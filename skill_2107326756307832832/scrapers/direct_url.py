"""Direct URL scraper: process user-provided WeChat article URLs."""

import re
from typing import Any, Dict, List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import is_wechat_url, extract_urls, get_logger
from .article_fetcher import ArticleFetcher


logger = get_logger("direct-url")


class DirectUrlScraper:
    """Scrape articles from user-provided URLs (single, list, or mixed text)."""

    def __init__(self, fetcher: Optional[ArticleFetcher] = None):
        self.fetcher = fetcher or ArticleFetcher()
        self.last_skipped: List[str] = []

    def scrape_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Scrape articles from a list of URLs.

        Non-WeChat links are recorded in `last_skipped` instead of failing
        silently, so the caller can tell the user what was dropped.
        """
        wechat_urls = [u for u in urls if is_wechat_url(u)]
        self.last_skipped = [u for u in urls if not is_wechat_url(u)]

        if self.last_skipped:
            logger.warning(f"跳过 {len(self.last_skipped)} 个非微信链接")

        if not wechat_urls:
            logger.error("没有找到有效的微信公众号文章链接")
            return []

        return self.fetcher.fetch_articles(wechat_urls)

    def scrape_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Extract and scrape WeChat article URLs from mixed text."""
        urls = extract_urls(text)
        if not urls:
            logger.warning("文本中未找到 URL")
            return []
        return self.scrape_urls(urls)

    def scrape_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """Read URLs from a file (one per line or mixed text) and scrape."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="gbk") as f:
                text = f.read()

        return self.scrape_from_text(text)