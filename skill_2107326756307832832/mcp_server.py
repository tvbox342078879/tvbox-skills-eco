"""MCP Server for WeChat Article Scraper.

Exposes tools for searching, fetching, filtering, summarizing, and exporting
WeChat Official Account articles.
"""

import sys

# stdio JSON-RPC breaks if stray output is written to stdout, and .pyc files
# dropped next to the sources get the skill package rejected on upload.
sys.dont_write_bytecode = True

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.utils import (
    load_config, get_logger, parse_date_range, extract_wechat_urls,
    is_wechat_url, canonicalize_url, resolve_export_dir, resolve_state_dir,
)
from core.filters import filter_by_date, filter_by_keywords, rank_by_relevance, deduplicate
from core.dedup import Deduplicator
from core.state_store import StateStore
from core.summarizer import build_digest, summarize_article, article_stats
from core.image_downloader import ImageDownloader
from core.article_parser import parse_article_html, extract_article_meta_from_html
from scrapers.sogou_search import SogouWeChatSearcher, SogouBlocked
from scrapers.article_fetcher import ArticleFetcher
from scrapers.direct_url import DirectUrlScraper
from exporters.zip_exporter import ZipExporter
from exporters.markdown_exporter import MarkdownExporter
from exporters.ima_exporter import ImaExporter
from exporters.dify_exporter import DifyExporter
from exporters.obsidian_exporter import ObsidianExporter

logger = get_logger("mcp-server")

SERVER_NAME = "wechat-article-scraper"
SERVER_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# Global instances (lazy-initialized)
# ---------------------------------------------------------------------------

_config = None
_searcher = None
_fetcher = None
_state = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_searcher():
    global _searcher
    if _searcher is None:
        cfg = _get_config()
        sogou_cfg = cfg.get("sogou", {})
        _searcher = SogouWeChatSearcher(
            rate_limit_min=sogou_cfg.get("rate_limit_min", 3.0),
            rate_limit_max=sogou_cfg.get("rate_limit_max", 8.0),
            max_pages=sogou_cfg.get("max_pages", 5),
            proxy=sogou_cfg.get("proxy"),
            raise_on_block=sogou_cfg.get("raise_on_block", False),
        )
    return _searcher


def _get_fetcher():
    global _fetcher
    if _fetcher is None:
        cfg = _get_config()
        fetch_cfg = cfg.get("fetch", {})
        _fetcher = ArticleFetcher(
            rate_limit_min=fetch_cfg.get("rate_limit_min", 2.0),
            rate_limit_max=fetch_cfg.get("rate_limit_max", 5.0),
            proxy=fetch_cfg.get("proxy"),
            save_raw_html=fetch_cfg.get("save_raw_html", False),
            cache_max_age_days=fetch_cfg.get("cache_max_age_days"),
        )
    return _fetcher


def _get_state():
    global _state
    if _state is None:
        _state = StateStore()
    return _state


# ---------------------------------------------------------------------------
# MCP Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_wechat_articles",
        "description": "通过搜狗微信搜索查找公众号文章。支持按公众号名称、关键词、时间范围搜索。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_name": {
                    "type": "string",
                    "description": "微信公众号名称（如'人民日报'、'36氪'）",
                },
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期，格式: YYYY-MM-DD",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期，格式: YYYY-MM-DD",
                },
                "max_pages": {
                    "type": "integer",
                    "description": "最大搜索页数（默认5，每页约10条）",
                    "default": 5,
                },
                "fetch_content": {
                    "type": "boolean",
                    "description": "是否获取全文内容（默认true）",
                    "default": True,
                },
                "keywords": {
                    "type": "array",
                    "description": "结果二次过滤的关键词列表",
                    "items": {"type": "string"},
                },
                "match_all": {
                    "type": "boolean",
                    "description": "关键词 AND 匹配",
                    "default": False,
                },
                "max_articles": {
                    "type": "integer",
                    "description": "最多保留条数（0=不限）",
                    "default": 0,
                },
            },
            "required": [],
        },
    },
    {
        "name": "fetch_wechat_article",
        "description": "通过URL获取微信公众号文章全文。支持单个URL、URL列表或包含URL的文本。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {
                    "description": "文章URL（字符串、字符串数组或包含URL的文本）",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
            },
            "required": ["urls"],
        },
    },
    {
        "name": "export_articles_zip",
        "description": "将文章导出为ZIP压缩包，支持Markdown/Text/HTML/JSON多种格式。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "文章列表（来自search或fetch的结果）",
                    "items": {"type": "object"},
                },
                "output_dir": {
                    "type": "string",
                    "description": "输出目录路径",
                },
                "filename": {
                    "type": "string",
                    "description": "ZIP文件名（不含扩展名）",
                },
                "formats": {
                    "type": "array",
                    "description": "输出格式: markdown, text, html, json",
                    "items": {"type": "string"},
                    "default": ["markdown", "text", "json"],
                },
            },
            "required": ["articles"],
        },
    },
    {
        "name": "export_to_ima",
        "description": "将文章导出到IMA知识库。需要配置IMA API Key。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "文章列表",
                    "items": {"type": "object"},
                },
                "kb_id": {
                    "type": "string",
                    "description": "IMA知识库ID（留空则自动创建）",
                },
                "collection_name": {
                    "type": "string",
                    "description": "知识库集合名称",
                    "default": "微信公众号文章",
                },
            },
            "required": ["articles"],
        },
    },
    {
        "name": "export_to_dify",
        "description": "将文章导出到Dify知识库。需要配置Dify API Key。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "文章列表",
                    "items": {"type": "object"},
                },
                "dataset_id": {
                    "type": "string",
                    "description": "Dify Dataset ID（留空则自动创建）",
                },
                "dataset_name": {
                    "type": "string",
                    "description": "Dataset 名称",
                    "default": "微信公众号文章",
                },
            },
            "required": ["articles"],
        },
    },
    {
        "name": "export_to_obsidian",
        "description": "将文章导出为Obsidian格式的Markdown笔记。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "文章列表",
                    "items": {"type": "object"},
                },
                "vault_path": {
                    "type": "string",
                    "description": "Obsidian vault路径（留空则导出到data目录）",
                },
                "folder": {
                    "type": "string",
                    "description": "vault内子文件夹",
                    "default": "微信公众号",
                },
                "create_moc": {
                    "type": "boolean",
                    "description": "是否创建Map of Content索引",
                    "default": True,
                },
                "group_by_account": {
                    "type": "boolean",
                    "description": "按公众号名称再分一层子文件夹",
                    "default": False,
                },
            },
            "required": ["articles"],
        },
    },
    {
        "name": "filter_articles",
        "description": "按日期范围和关键词过滤文章列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "文章列表",
                    "items": {"type": "object"},
                },
                "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "keywords": {
                    "type": "array",
                    "description": "关键词列表",
                    "items": {"type": "string"},
                },
                "match_all": {
                    "type": "boolean",
                    "description": "关键词匹配模式：true=AND, false=OR",
                    "default": False,
                },
                "days": {
                    "type": "integer",
                    "description": "未给 start_date 时回溯天数",
                },
                "keep_undated": {
                    "type": "boolean",
                    "description": "保留没有发布日期的文章（默认剔除）",
                    "default": False,
                },
                "rank": {
                    "type": "boolean",
                    "description": "按关键词相关性排序",
                    "default": True,
                },
                "dedup": {
                    "type": "boolean",
                    "description": "过滤后再去重",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数（0=不限）",
                    "default": 0,
                },
                "search_fields": {
                    "type": "array",
                    "description": "关键词匹配的字段，默认 title+正文",
                    "items": {"type": "string"},
                },
            },
            "required": ["articles"],
        },
    },
    {
        "name": "batch_scrape",
        "description": "一体化批量爬取：搜索+过滤+去重+获取全文+导出。一次完成从搜索到导出的完整流程，支持增量爬取（跳过历史里已有的文章）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_name": {
                    "type": "string",
                    "description": "微信公众号名称",
                },
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "start_date": {"type": "string", "description": "起始日期"},
                "end_date": {"type": "string", "description": "结束日期"},
                "days": {
                    "type": "integer",
                    "description": "回溯天数（与start_date二选一）",
                },
                "keywords": {
                    "type": "array",
                    "description": "正文关键词过滤列表",
                    "items": {"type": "string"},
                },
                "max_pages": {
                    "type": "integer",
                    "description": "最大搜索页数",
                    "default": 5,
                },
                "max_articles": {
                    "type": "integer",
                    "description": "本次最多获取全文的篇数（0=不限制）",
                    "default": 30,
                },
                "dedup_threshold": {
                    "type": "number",
                    "description": "标题相似度去重阈值（0.8~1.0）",
                    "default": 0.92,
                },
                "incremental": {
                    "type": "boolean",
                    "description": "增量模式：跳过历史记录中已爬取过的文章",
                    "default": False,
                },
                "export_format": {
                    "type": "string",
                    "description": "导出格式: zip, markdown, ima, dify, obsidian, none",
                    "default": "zip",
                },
                "output_dir": {
                    "type": "string",
                    "description": "输出目录（留空使用系统临时目录）",
                },
                "with_digest": {
                    "type": "boolean",
                    "description": "是否同时生成本地摘要（关键词+逐篇摘要）",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_wechat_accounts",
        "description": "搜索微信公众号账号本身（名称、微信号、主页），用于确认或纠正公众号名称。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_name": {"type": "string", "description": "公众号名称（模糊）"},
                "max_pages": {"type": "integer", "default": 2},
            },
            "required": ["account_name"],
        },
    },
    {
        "name": "parse_wechat_html",
        "description": "解析已有的公众号文章 HTML（如用户粘贴的源码或本地文件），返回标题/作者/日期/正文/图片。无需联网。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "文章 HTML 源码"},
                "file_path": {"type": "string", "description": "或本地 HTML 文件路径"},
                "url": {"type": "string", "description": "原文链接（写入结果）"},
            },
            "required": [],
        },
    },
    {
        "name": "extract_wechat_urls",
        "description": "从任意文本（聊天记录、推文、文档）中提取微信公众号链接，返回去重后的链接列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "包含链接的文本"},
                "only_wechat": {"type": "boolean", "default": True},
            },
            "required": ["text"],
        },
    },
    {
        "name": "scrape_from_file",
        "description": "从文本文件读取链接列表（每行一个或混杂文本）并批量爬取全文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "包含 URL 的文本文件路径"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "deduplicate_articles",
        "description": "对文章列表执行智能去重（链接规范化 + 内容哈希 + 标题相似度），返回去重结果和被剔除的条目。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {"type": "array", "items": {"type": "object"}},
                "similarity_threshold": {
                    "type": "number",
                    "description": "标题相似度阈值，越大越严格",
                    "default": 0.92,
                },
                "use_content_hash": {"type": "boolean", "default": True},
                "use_title_match": {"type": "boolean", "default": True},
            },
            "required": ["articles"],
        },
    },
    {
        "name": "summarize_articles",
        "description": "离线生成文章摘要：整体统计 + 高频关键词 + 逐篇抽取式摘要（纯本地，无需 API Key）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {"type": "array", "items": {"type": "object"}},
                "max_sentences": {"type": "integer", "default": 2},
                "top_keywords": {"type": "integer", "default": 12},
                "output_md": {
                    "type": "string",
                    "description": "可选：把摘要 Markdown 写到该路径",
                },
            },
            "required": ["articles"],
        },
    },
    {
        "name": "download_article_images",
        "description": "下载文章图片到本地目录，并把 Markdown 里的图片链接改写为本地路径（解决离线看图）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {"type": "array", "items": {"type": "object"}},
                "output_dir": {"type": "string", "description": "图片根目录，留空用临时目录"},
                "max_images": {"type": "integer", "default": 30},
            },
            "required": ["articles"],
        },
    },
    {
        "name": "export_articles_markdown",
        "description": "把文章导出为纯文本 Markdown 目录（含 index.csv 与摘要.md），无二进制文件，适合直接上传/入库。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {"type": "array", "items": {"type": "object"}},
                "output_dir": {"type": "string"},
                "folder": {"type": "string", "description": "输出子目录名"},
                "group_by_account": {"type": "boolean", "default": True},
            },
            "required": ["articles"],
        },
    },
    {
        "name": "get_cache_status",
        "description": "查看抓取缓存与历史统计（条目数、占用空间、成功/失败计数）。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "clear_article_cache",
        "description": "清除已缓存的文章全文（下次抓取将重新请求）。不影响历史记录。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "also_history": {"type": "boolean", "description": "是否同时清空爬取历史", "default": False},
            },
            "required": [],
        },
    },
    {
        "name": "get_scrape_history",
        "description": "读取历史爬取记录（按公众号筛选、按时间倒序），用于判断哪些文章已经采集过。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "按公众号筛选"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "filter_new_articles",
        "description": "用历史记录区分“新文章 / 已爬过”，实现增量爬取；可选同时写入历史。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles": {"type": "array", "items": {"type": "object"}},
                "record": {"type": "boolean", "description": "把新文章写入历史", "default": True},
            },
            "required": ["articles"],
        },
    },
    {
        "name": "get_skill_stats",
        "description": "返回技能自检信息：版本、工具数量、依赖与配置状态、目录配置。用于确认环境是否就绪。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle search_wechat_articles tool call."""
    searcher = _get_searcher()
    account = args.get("account_name", "")
    keyword = args.get("keyword", "")
    max_pages = args.get("max_pages", 5)

    searcher.blocked_pages = 0
    try:
        if account and keyword:
            articles = searcher.search_account_articles(account, keyword=keyword, max_pages=max_pages)
        elif account:
            articles = searcher.search_account_articles(account, max_pages=max_pages)
        elif keyword:
            articles = searcher.search_by_keyword(keyword, max_pages=max_pages)
        else:
            return {"error": "请至少提供 account_name 或 keyword 之一", "articles": []}
    except SogouBlocked as e:
        return {
            "error": str(e),
            "blocked": True,
            "articles": [],
            "hint": "搜狗要求验证码：降低 max_pages、增大 config.sogou.rate_limit_*，或配置 proxy 后重试",
        }

    # Date filter
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    if start_date or end_date:
        articles = filter_by_date(articles, start_date=start_date, end_date=end_date)

    extra_keywords = args.get("keywords") or []
    if extra_keywords:
        articles = filter_by_keywords(
            articles, extra_keywords, match_all=args.get("match_all", False)
        )
        articles = rank_by_relevance(articles, extra_keywords)

    articles = deduplicate(articles)

    max_articles = args.get("max_articles", 0)
    if max_articles:
        articles = articles[:max_articles]

    # Fetch content if requested
    if args.get("fetch_content", True) and articles:
        fetcher = _get_fetcher()
        # Reuse the search session: Sogou /link redirects 403 the antispider
        # page unless the request carries the cookies from the results page.
        articles = fetcher.enrich_articles(
            articles, session=getattr(searcher, "session", None)
        )

    result = {
        "articles": articles,
        "total": len(articles),
        "query": {"account": account, "keyword": keyword, "start_date": start_date, "end_date": end_date},
    }
    if searcher.blocked:
        result["warning"] = f"搜索过程中命中 {searcher.blocked_pages} 次验证/限流页，结果可能不完整"
        result["blocked"] = True
    return result


def handle_fetch(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle fetch_wechat_article tool call."""
    urls_input = args.get("urls", "")
    scraper = DirectUrlScraper(_get_fetcher())

    if isinstance(urls_input, list):
        articles = scraper.scrape_urls(urls_input)
    elif isinstance(urls_input, str):
        articles = scraper.scrape_from_text(urls_input)
    else:
        return {"error": "urls 参数无效", "articles": []}

    failed = [a for a in articles if a.get("error")]
    result = {
        "articles": articles,
        "total": len(articles),
        "skipped_non_wechat": scraper.last_skipped,
    }
    if failed:
        result["failed"] = [{"link": a.get("link", ""), "error": a["error"]} for a in failed]
        result["warning"] = f"{len(failed)} 篇获取失败，可降速或配置代理后重试"
    return result


def handle_export_zip(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle export_articles_zip tool call."""
    articles = args.get("articles", [])
    if not articles:
        return {"error": "没有文章可导出"}

    exporter = ZipExporter(output_dir=args.get("output_dir"), config=_get_config())
    zip_path = exporter.export(
        articles,
        filename=args.get("filename"),
        formats=args.get("formats", ["markdown", "text", "json"]),
    )
    return {
        "zip_path": zip_path,
        "article_count": len(articles),
        "note": "产物为二进制，默认写入临时目录；如需入库请改用 export_articles_markdown",
    }


def handle_export_ima(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle export_to_ima tool call."""
    articles = args.get("articles", [])
    if not articles:
        return {"error": "没有文章可导出"}

    cfg = _get_config()
    api_key = cfg.get("ima", {}).get("api_key")
    exporter = ImaExporter(api_key=api_key)
    return exporter.export_articles(
        articles,
        kb_id=args.get("kb_id"),
        collection_name=args.get("collection_name", "微信公众号文章"),
    )


def handle_export_dify(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle export_to_dify tool call."""
    articles = args.get("articles", [])
    if not articles:
        return {"error": "没有文章可导出"}

    cfg = _get_config()
    api_key = cfg.get("dify", {}).get("api_key")
    base_url = cfg.get("dify", {}).get("base_url", "https://api.dify.ai/v1")
    exporter = DifyExporter(api_key=api_key, base_url=base_url)
    return exporter.export_articles(
        articles,
        dataset_id=args.get("dataset_id"),
        dataset_name=args.get("dataset_name", "微信公众号文章"),
    )


def handle_export_obsidian(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle export_to_obsidian tool call."""
    articles = args.get("articles", [])
    if not articles:
        return {"error": "没有文章可导出"}

    cfg = _get_config()
    vault_path = args.get("vault_path") or cfg.get("obsidian", {}).get("vault_path")
    exporter = ObsidianExporter(vault_path=vault_path)
    return exporter.export_articles(
        articles,
        folder=args.get("folder", "微信公众号"),
        create_moc=args.get("create_moc", True),
        group_by_account=args.get("group_by_account", False),
    )


def handle_filter(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle filter_articles tool call."""
    articles = args.get("articles", [])
    keywords = args.get("keywords", [])
    match_all = args.get("match_all", False)
    start_date = args.get("start_date")
    end_date = args.get("end_date")

    result = filter_by_date(
        articles,
        start_date=start_date,
        end_date=end_date,
        days=args.get("days"),
        keep_undated=args.get("keep_undated", False),
    )
    if keywords:
        result = filter_by_keywords(
            result, keywords, match_all=match_all,
            search_fields=args.get("search_fields"),
        )
        if args.get("rank", True):
            result = rank_by_relevance(result, keywords)
    if args.get("dedup", False):
        result = deduplicate(result)

    limit = args.get("limit", 0)
    if limit:
        result = result[:limit]

    return {"articles": result, "total": len(result), "input_total": len(articles)}


def handle_batch_scrape(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle batch_scrape tool call — one-stop scraping with incremental support."""
    searcher = _get_searcher()
    fetcher = _get_fetcher()
    account = args.get("account_name", "")
    keyword = args.get("keyword", "")
    max_pages = args.get("max_pages", 5)

    # Step 1: Search
    searcher.blocked_pages = 0
    try:
        if account and keyword:
            articles = searcher.search_account_articles(account, keyword=keyword, max_pages=max_pages)
        elif account:
            articles = searcher.search_account_articles(account, max_pages=max_pages)
        elif keyword:
            articles = searcher.search_by_keyword(keyword, max_pages=max_pages)
        else:
            return {"error": "请至少提供 account_name 或 keyword 之一"}
    except SogouBlocked as e:
        return {
            "error": str(e),
            "blocked": True,
            "articles": [],
            "hint": "请降低 max_pages / 增大限速间隔 / 配置代理后重试",
        }

    searched_total = len(articles)

    # Step 2: Date filter
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    days = args.get("days")
    if start_date or end_date or days:
        articles = filter_by_date(
            articles,
            start_date=start_date,
            end_date=end_date,
            days=days,
            keep_undated=args.get("keep_undated", False),
        )

    # Step 3: Keyword filter + relevance ranking
    keywords = args.get("keywords") or []
    if keywords:
        articles = filter_by_keywords(
            articles, keywords, match_all=args.get("match_all", False)
        )
        articles = rank_by_relevance(articles, keywords)

    # Step 4: Deduplicate (title similarity is configurable)
    dedup = Deduplicator(
        similarity_threshold=args.get("dedup_threshold", 0.92),
        use_content_hash=True,
        use_title_match=True,
    )
    before = len(articles)
    articles = dedup.deduplicate(articles)
    removed_duplicates = before - len(articles)

    # Step 5: Incremental — drop anything already in history
    skipped_known = 0
    if args.get("incremental"):
        new_articles, known = _get_state().filter_new(articles)
        skipped_known = len(known)
        articles = new_articles

    # Step 6: Fetch full text (bounded, ranked first)
    max_articles = args.get("max_articles", 30)
    to_fetch = articles[:max_articles] if max_articles else articles
    articles = fetcher.enrich_articles(
        to_fetch, session=getattr(searcher, "session", None)
    )

    # Step 7: Export
    export_format = args.get("export_format", "zip")
    output_dir = args.get("output_dir")
    export_result = {}
    cfg = _get_config()

    if articles and export_format != "none":
        if export_format == "zip":
            exporter = ZipExporter(output_dir=output_dir, config=cfg)
            export_result = {"zip_path": exporter.export(articles)}
        elif export_format == "markdown":
            exporter = MarkdownExporter(output_dir=output_dir)
            export_result = exporter.export(
                articles,
                folder=args.get("folder") or f"wechat_{account or keyword or 'articles'}",
            )
        elif export_format == "ima":
            exporter = ImaExporter(api_key=cfg.get("ima", {}).get("api_key"))
            export_result = exporter.export_articles(articles)
        elif export_format == "dify":
            exporter = DifyExporter(
                api_key=cfg.get("dify", {}).get("api_key"),
                base_url=cfg.get("dify", {}).get("base_url", "https://api.dify.ai/v1"),
            )
            export_result = exporter.export_articles(articles)
        elif export_format == "obsidian":
            vault_path = output_dir or cfg.get("obsidian", {}).get("vault_path")
            exporter = ObsidianExporter(vault_path=vault_path)
            export_result = exporter.export_articles(articles)
        else:
            export_result = {"error": f"未知导出格式: {export_format}"}

    # Step 8: Record history
    new_recorded = 0
    if args.get("record_history", True) and articles:
        new_recorded = _get_state().record(articles)

    result = {
        "articles": articles,
        "total": len(articles),
        "export": export_result,
        "pipeline": {
            "searched": searched_total,
            "after_filtering": before,
            "duplicates_removed": removed_duplicates,
            "already_known_skipped": skipped_known,
            "full_text_fetched": sum(1 for a in articles if a.get("word_count")),
            "new_in_history": new_recorded,
        },
        "query": {
            "account": account,
            "keyword": keyword,
            "start_date": start_date,
            "end_date": end_date,
            "days": days,
        },
    }
    if args.get("with_digest"):
        result["digest"] = build_digest(articles)["digest_markdown"]
    if searcher.blocked:
        result["warning"] = f"命中 {searcher.blocked_pages} 次验证/限流页，结果可能不完整"
        result["blocked"] = True
    return result


# ---------------------------------------------------------------------------
# v1.1 tool handlers
# ---------------------------------------------------------------------------

def handle_search_accounts(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle search_wechat_accounts tool call."""
    searcher = _get_searcher()
    name = args.get("account_name", "")
    if not name:
        return {"error": "请提供 account_name"}
    searcher.blocked_pages = 0
    accounts = searcher.search_accounts(name, max_pages=args.get("max_pages", 2))
    result = {"accounts": accounts, "total": len(accounts)}
    if not accounts and searcher.blocked:
        result["blocked"] = True
        result["warning"] = "命中验证/限流页，未获取到账号结果"
    return result


def handle_parse_html(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle parse_wechat_html tool call (offline, no network)."""
    html = args.get("html", "")
    file_path = args.get("file_path")
    if not html and file_path:
        path = Path(file_path)
        if not path.exists():
            return {"error": f"文件不存在: {path}"}
        html = path.read_text(encoding="utf-8", errors="replace")
    if not html:
        return {"error": "请提供 html 源码或 file_path"}

    article = parse_article_html(html, args.get("url", ""))
    meta = extract_article_meta_from_html(html)
    for field in ("title", "author", "account", "pub_date", "summary"):
        if not article.get(field) and meta.get(field):
            article[field] = meta[field]
    if not article.get("summary") and meta.get("description"):
        article["summary"] = meta["description"]
    if meta.get("canonical_url") and is_wechat_url(meta["canonical_url"]):
        article["link"] = meta["canonical_url"]
    return {"article": article}


def handle_extract_urls(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle extract_wechat_urls tool call."""
    text = args.get("text", "")
    from core.utils import extract_urls
    urls = extract_wechat_urls(text) if args.get("only_wechat", True) else extract_urls(text)
    return {
        "urls": urls,
        "total": len(urls),
        "normalized": [canonicalize_url(u) for u in urls],
    }


def handle_scrape_from_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle scrape_from_file tool call."""
    file_path = args.get("file_path", "")
    if not Path(file_path).exists():
        return {"error": f"文件不存在: {file_path}"}
    scraper = DirectUrlScraper(_get_fetcher())
    articles = scraper.scrape_from_file(file_path)
    return {
        "articles": articles,
        "total": len(articles),
        "skipped_non_wechat": scraper.last_skipped,
    }


def handle_deduplicate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle deduplicate_articles tool call."""
    articles = args.get("articles", [])
    dedup = Deduplicator(
        similarity_threshold=args.get("similarity_threshold", 0.92),
        use_content_hash=args.get("use_content_hash", True),
        use_title_match=args.get("use_title_match", True),
    )
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for article in articles:
        reason = dedup.duplicate_reason(article)
        if reason:
            removed.append({
                "title": article.get("title", ""),
                "link": article.get("link", ""),
                "reason": reason,
            })
        else:
            dedup.mark_seen(article)
            kept.append(article)
    return {
        "articles": kept,
        "total": len(kept),
        "removed": len(removed),
        "removed_items": removed,
        "stats": dedup.stats,
    }


def handle_summarize(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle summarize_articles tool call."""
    articles = args.get("articles", [])
    if not articles:
        return {"error": "没有文章可摘要"}
    digest = build_digest(
        articles,
        top_keywords=args.get("top_keywords", 12),
        max_sentences=args.get("max_sentences", 2),
    )
    output_md = args.get("output_md")
    if output_md:
        path = Path(output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest["digest_markdown"], encoding="utf-8")
        digest["output_md"] = str(path)
    return digest


def handle_download_images(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle download_article_images tool call."""
    articles = args.get("articles", [])
    if not articles:
        return {"error": "没有文章可处理"}
    downloader = ImageDownloader(
        output_dir=args.get("output_dir"),
        max_images=args.get("max_images", 30),
        proxy=_get_config().get("fetch", {}).get("proxy"),
    )
    return downloader.download_for_articles(
        articles, max_articles=args.get("max_articles", 20)
    )


def handle_export_markdown(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle export_articles_markdown tool call."""
    articles = args.get("articles", [])
    if not articles:
        return {"error": "没有文章可导出"}
    exporter = MarkdownExporter(output_dir=args.get("output_dir"))
    return exporter.export(
        articles,
        folder=args.get("folder"),
        group_by_account=args.get("group_by_account", True),
    )


def handle_cache_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle get_cache_status tool call."""
    fetcher = _get_fetcher()
    state = _get_state()
    return {
        "cache": fetcher.cache_status(),
        "history": state.stats(),
    }


def handle_clear_cache(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle clear_article_cache tool call."""
    removed = _get_fetcher().clear_cache()
    result = {"cache_entries_removed": removed}
    if args.get("also_history"):
        result["history_entries_removed"] = _get_state().clear()
    return result


def handle_history(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle get_scrape_history tool call."""
    state = _get_state()
    return {
        "history": state.history(account=args.get("account"), limit=args.get("limit", 50)),
        "stats": state.stats(),
    }


def handle_filter_new(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle filter_new_articles tool call."""
    articles = args.get("articles", [])
    state = _get_state()
    new, known = state.filter_new(articles)
    recorded = state.record(new) if (args.get("record", True) and new) else 0
    return {
        "new_articles": new,
        "new_count": len(new),
        "known_count": len(known),
        "known_preview": [a.get("title", "") for a in known[:10]],
        "recorded": recorded,
    }


def handle_skill_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle get_skill_stats tool call — environment self-check."""
    cfg = _get_config()
    import importlib.util
    state_dir = resolve_state_dir()
    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "tool_count": len(TOOLS),
        # HTML parsing is stdlib-only (html.parser); requests is the one hard dep.
        "dependencies": {
            "requests": importlib.util.find_spec("requests") is not None,
        },
        "directories": {
            "project_root": str(PROJECT_ROOT),
            "export_dir": str(resolve_export_dir(config=cfg)),
            "state_dir": str(state_dir),
            "state_dir_writable": os.access(str(state_dir), os.W_OK),
        },
        "knowledge_base": {
            "ima": bool(cfg.get("ima", {}).get("api_key")),
            "dify": bool(cfg.get("dify", {}).get("api_key")),
            "obsidian_vault": cfg.get("obsidian", {}).get("vault_path"),
        },
        "cache": _get_fetcher().cache_status(),
        "history": _get_state().stats(),
        "checked_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

TOOL_HANDLERS = {
    "search_wechat_articles": handle_search,
    "fetch_wechat_article": handle_fetch,
    "export_articles_zip": handle_export_zip,
    "export_to_ima": handle_export_ima,
    "export_to_dify": handle_export_dify,
    "export_to_obsidian": handle_export_obsidian,
    "filter_articles": handle_filter,
    "batch_scrape": handle_batch_scrape,
    "search_wechat_accounts": handle_search_accounts,
    "parse_wechat_html": handle_parse_html,
    "extract_wechat_urls": handle_extract_urls,
    "scrape_from_file": handle_scrape_from_file,
    "deduplicate_articles": handle_deduplicate,
    "summarize_articles": handle_summarize,
    "download_article_images": handle_download_images,
    "export_articles_markdown": handle_export_markdown,
    "get_cache_status": handle_cache_status,
    "clear_article_cache": handle_clear_cache,
    "get_scrape_history": handle_history,
    "filter_new_articles": handle_filter_new,
    "get_skill_stats": handle_skill_stats,
}


def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Dispatch a tool call."""
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"error": f"未知工具: {name}"}
    try:
        return handler(arguments or {})
    except Exception as e:
        logger.error(f"工具执行失败 [{name}]: {e}")
        return {"error": str(e), "tool": name}


# ---------------------------------------------------------------------------
# MCP stdio server (minimal)
# ---------------------------------------------------------------------------

def _envelope(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _tool_response(req_id: Any, payload: Any, is_error: bool = False) -> Dict[str, Any]:
    return _envelope(req_id, {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    })


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC request and return a response (None for notifications)."""
    if not isinstance(request, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: 需要 JSON 对象"},
        }
    method = request.get("method", "")
    params = request.get("params", {}) or {}
    req_id = request.get("id")

    if method == "initialize":
        return _envelope(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _envelope(req_id, {"serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
    if method == "tools/list":
        return _envelope(req_id, {"tools": TOOLS})
    if method == "tools/call":
        tool_name = params.get("name", "")
        payload = call_tool(tool_name, params.get("arguments", {}))
        return _tool_response(req_id, payload, isinstance(payload, dict) and bool(payload.get("error")))
    if method == "resources/list":
        return _envelope(req_id, {"resources": []})
    if method == "prompts/list":
        return _envelope(req_id, {"prompts": []})
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if req_id is None:  # unknown notification: stay silent per JSON-RPC
        return None
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def run_mcp_server():
    """Run a minimal MCP stdio server: newline-delimited JSON-RPC 2.0 on stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"[mcp-server] 非法 JSON 行: {line[:120]}\n")
            continue

        try:
            response = handle_request(request)
        except Exception as e:  # a broken handler must never kill the loop
            logger.error(f"请求处理异常: {e}")
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32603, "message": str(e)},
            }
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run_mcp_server()