"""Core modules for WeChat article scraper."""

from .utils import (
    load_config,
    parse_date_range,
    parse_date,
    parse_pub_date,
    parse_relative_date,
    sanitize_filename,
    is_wechat_url,
    is_wechat_article_link,
    canonicalize_url,
    article_id_from_url,
    extract_urls,
    extract_wechat_urls,
    detect_blocked,
    merge_article,
    resolve_export_dir,
    resolve_state_dir,
    RateLimiter,
    get_logger,
)
from .article_parser import parse_article_html, extract_article_meta_from_html
from .filters import filter_by_date, filter_by_keywords, rank_by_relevance, deduplicate
from .dedup import Deduplicator, content_hash, title_similarity
from .state_store import StateStore
from .summarizer import (
    summarize_article, extract_keywords, article_stats, build_digest,
)
from .image_downloader import ImageDownloader, extract_image_urls

__all__ = [
    "load_config",
    "parse_date_range",
    "parse_date",
    "parse_pub_date",
    "parse_relative_date",
    "sanitize_filename",
    "is_wechat_url",
    "is_wechat_article_link",
    "canonicalize_url",
    "article_id_from_url",
    "extract_urls",
    "extract_wechat_urls",
    "detect_blocked",
    "merge_article",
    "resolve_export_dir",
    "resolve_state_dir",
    "RateLimiter",
    "get_logger",
    "parse_article_html",
    "extract_article_meta_from_html",
    "filter_by_date",
    "filter_by_keywords",
    "rank_by_relevance",
    "deduplicate",
    "Deduplicator",
    "content_hash",
    "title_similarity",
    "StateStore",
    "summarize_article",
    "extract_keywords",
    "article_stats",
    "build_digest",
    "ImageDownloader",
    "extract_image_urls",
]
