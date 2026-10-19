"""Fetch full article content from WeChat article URLs."""

import re
import time
import random
from typing import Any, Dict, List, Optional
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import (
    RateLimiter, get_logger, USER_AGENTS,
    is_wechat_url, is_wechat_article_link, article_id_from_url,
    is_sogou_link_url, resolve_sogou_redirect,
    sanitize_filename, save_json, merge_article, parse_pub_date, detect_blocked,
    file_size_str,
    DEFAULT_DATA_DIR, resolve_state_dir,
)
from core.article_parser import parse_article_html, extract_article_meta_from_html
from core.dedup import Deduplicator


logger = get_logger("article-fetcher")


class ArticleFetcher:
    """Fetch and parse full WeChat article content from URLs."""

    def __init__(
        self,
        rate_limit_min: float = 2.0,
        rate_limit_max: float = 5.0,
        proxy: Optional[str] = None,
        save_raw_html: bool = False,
        cache_dir: Optional[Path] = None,
        cache_max_age_days: Optional[float] = None,
    ):
        if not HAS_REQUESTS:
            raise ImportError("需要安装 requests: pip install requests")

        self.limiter = RateLimiter(rate_limit_min, rate_limit_max)
        self.proxy = proxy
        self.save_raw_html = save_raw_html
        # 缓存是纯文本 JSON，放在 data 目录；导出产物才走 tempdir。
        self.cache_dir = Path(cache_dir) if cache_dir else (resolve_state_dir() / "cache")
        self.cache_max_age_days = cache_max_age_days
        self.session = requests.Session()
        self._setup_session()
        self.dedup = Deduplicator()
        self.stats = {"requested": 0, "cache_hits": 0, "fetched": 0, "failed": 0}

    def _setup_session(self):
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

    def fetch_article(self, url: str, session=None) -> Dict[str, Any]:
        """Fetch and parse a single article.

        Args:
            url: WeChat article URL.
            session: optional requests.Session to use instead of this
                fetcher's own. Sogou only releases a `/link?url=` redirect to a
                client carrying the cookies from the search page, so pass the
                searcher's session or it answers with the antispider wall.

        Returns:
            Article dict with title, content, images, etc.
        """
        self.stats["requested"] += 1
        self.limiter.wait()

        # Check cache
        cached = self._load_cache(url)
        if cached:
            self.stats["cache_hits"] += 1
            logger.info(f"缓存命中: {cached.get('title', url[:50])}")
            return cached

        sess = session or self.session
        try:
            resp = sess.get(url, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            html = resp.text

            # Sogou /link is a JS wrapper page, not an HTTP redirect.
            if is_sogou_link_url(resp.url or url):
                target = resolve_sogou_redirect(html)
                if target:
                    logger.info(f"搜狗跳转目标: {target[:60]}")
                    resp = sess.get(
                        target, timeout=20, allow_redirects=True,
                        headers={"Referer": url},
                    )
                    resp.raise_for_status()
                    resp.encoding = "utf-8"
                    html = resp.text
                else:
                    logger.warning(f"未能从搜狗跳转页解析出目标: {url[:60]}")
        except Exception as e:
            self.stats["failed"] += 1
            logger.error(f"获取失败 {url[:60]}: {e}")
            return self._error_article(url, str(e))

        if detect_blocked(html):
            self.stats["failed"] += 1
            logger.warning(f"目标站点返回验证/限流页: {url[:60]}")
            return self._error_article(url, "blocked: 返回验证页，请降速或配置代理")

        self.stats["fetched"] += 1

        # Parse
        article = parse_article_html(html, url)

        # Enhance with meta extraction
        meta = extract_article_meta_from_html(html)
        for field in ("title", "author", "account", "pub_date", "summary"):
            if not article.get(field) and meta.get(field):
                article[field] = meta[field]
        if not article.get("summary") and meta.get("description"):
            article["summary"] = meta["description"]
        # Sogou links are redirects; prefer the page's canonical mp.weixin URL.
        canonical = meta.get("canonical_url") or resp.url
        if canonical and is_wechat_url(canonical):
            article["link"] = canonical
        pub = parse_pub_date(article.get("pub_date"))
        if pub:
            article["pub_date"] = pub.isoformat()
        article["source"] = "direct"
        article["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # Save to cache
        self._save_cache(url, article)
        # Sogou tokens change every search, so also cache under the stable
        # article URL or the next run re-fetches the same page.
        resolved = article.get("link", "")
        if resolved and resolved != url and is_wechat_url(resolved):
            self._save_cache(resolved, article)

        # Save raw HTML if configured
        if self.save_raw_html:
            raw_path = self.cache_dir / "raw" / f"{article_id_from_url(url)}.html"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(html, encoding="utf-8")

        logger.info(f"已获取: {article.get('title', url[:50])} ({article.get('word_count', 0)} 字)")
        return article

    def fetch_articles(
        self,
        urls: List[str],
        deduplicate: bool = True,
        session=None,
    ) -> List[Dict[str, Any]]:
        """Fetch multiple articles from URLs.

        Args:
            urls: List of WeChat article URLs.
            deduplicate: Whether to remove duplicates.
            session: optional shared requests.Session (see fetch_article).

        Returns:
            List of article dicts.
        """
        articles = []
        for i, url in enumerate(urls):
            logger.info(f"[{i + 1}/{len(urls)}] 正在获取: {url[:60]}")
            article = self.fetch_article(url, session=session)

            if deduplicate and self.dedup.is_duplicate(article):
                logger.info(f"跳过重复: {article.get('title', url[:50])}")
                continue

            if deduplicate:
                self.dedup.mark_seen(article)

            articles.append(article)

        return articles

    def enrich_articles(
        self,
        articles: List[Dict[str, Any]],
        keep_search_meta: bool = True,
        session=None,
    ) -> List[Dict[str, Any]]:
        """Enrich articles that have URLs but no content.

        A failed fetch keeps the original search metadata (title, snippet,
        account, pub_date) instead of overwriting it with an empty article.
        Sogou `/link?url=` redirects are followed too — they are what search
        results actually return.
        """
        result = []
        for article in articles:
            if article.get("content_text"):
                result.append(article)
                continue
            link = article.get("link", "")
            if not (link and is_wechat_article_link(link)):
                result.append(article)
                continue

            fetched = self.fetch_article(link, session=session)
            if fetched.get("error"):
                logger.warning(
                    f"全文获取失败，保留搜索结果: {article.get('title', link[:50])}"
                )
                result.append(article)
                continue

            merged = merge_article(article, fetched) if keep_search_meta else dict(fetched)
            merged["link"] = fetched.get("link") or link
            merged["search_link"] = link
            result.append(merged)
        return result

    # -- Cache --

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{article_id_from_url(url)}.json"

    def _load_cache(self, url: str) -> Optional[Dict[str, Any]]:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if self.cache_max_age_days is not None:
            try:
                import datetime as _dt
                mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime)
                age_days = (_dt.datetime.now() - mtime).total_seconds() / 86400
                if age_days > self.cache_max_age_days:
                    logger.info(f"缓存过期({age_days:.1f}天): {url[:50]}")
                    return None
            except OSError:
                return None
        if data.get("error"):
            return None  # never serve a cached failure
        return data

    def _save_cache(self, url: str, article: Dict[str, Any]):
        try:
            save_json(article, self._cache_path(url))
        except Exception as e:
            logger.warning(f"缓存保存失败: {e}")

    def clear_cache(self) -> int:
        """Delete all cached articles. Returns the number of entries removed."""
        import shutil
        removed = 0
        if self.cache_dir.exists():
            removed = len(list(self.cache_dir.glob("*.json")))
            shutil.rmtree(self.cache_dir, ignore_errors=True)
            logger.info(f"缓存已清除（{removed} 条）")
        return removed

    def cache_status(self) -> Dict[str, Any]:
        """Report cache size and fetch counters — no network access."""
        files = list(self.cache_dir.glob("*.json")) if self.cache_dir.exists() else []
        total_bytes = sum(f.stat().st_size for f in files)
        return {
            "cache_dir": str(self.cache_dir),
            "entries": len(files),
            "size_bytes": total_bytes,
            "size_human": file_size_str(total_bytes),
            "max_age_days": self.cache_max_age_days,
            "counters": dict(self.stats),
        }

    def _error_article(self, url: str, error: str) -> Dict[str, Any]:
        return {
            "title": "",
            "link": url,
            "account": "",
            "author": "",
            "pub_date": None,
            "content_text": "",
            "content_markdown": "",
            "images": [],
            "word_count": 0,
            "source": "direct",
            "error": error,
        }