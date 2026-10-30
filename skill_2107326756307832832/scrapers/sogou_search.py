"""Sogou WeChat Search — primary source for discovering WeChat articles.

Uses the Sogou WeChat search engine (weixin.sogou.com) which indexes
WeChat Official Account articles with Tencent's cooperation.
"""

import html as html_module
import re
import time
import random
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import RateLimiter, get_logger, USER_AGENTS, parse_relative_date, detect_blocked

logger = get_logger("sogou-search")


def _clean_text(fragment: str) -> str:
    """Drop Sogou's markup and decode entities.

    Sogou escapes everything it returns: titles carry `&ldquo;`, and the
    `/link?url=` redirects carry `&amp;`, which would corrupt the query string.
    Inline `document.write(...)` date scripts are removed with their body, so
    they never leak into titles or account names.
    """
    fragment = re.sub(
        r"<script\b[^>]*>.*?</script>", " ", fragment,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return html_module.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


class SogouBlocked(RuntimeError):
    """Raised when Sogou serves a captcha / rate-limit page."""


class SogouWeChatSearcher:
    """Search for WeChat articles via Sogou WeChat search."""

    BASE_URL = "https://weixin.sogou.com"
    SEARCH_URL = f"{BASE_URL}/weixin"
    ACCOUNT_SEARCH_URL = f"{BASE_URL}/weixin?type=1"  # type=1 for account search
    ARTICLE_SEARCH_URL = f"{BASE_URL}/weixin?type=2"  # type=2 for article search

    def __init__(
        self,
        rate_limit_min: float = 3.0,
        rate_limit_max: float = 8.0,
        max_pages: int = 5,
        proxy: Optional[str] = None,
        raise_on_block: bool = False,
    ):
        if not HAS_REQUESTS:
            raise ImportError("需要安装 requests: pip install requests")

        self.limiter = RateLimiter(rate_limit_min, rate_limit_max)
        self.max_pages = max_pages
        self.proxy = proxy
        self.raise_on_block = raise_on_block
        self.blocked_pages = 0
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        """Initialize session with headers."""
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Referer": self.BASE_URL,
        })
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

    def _rotate_ua(self):
        self.session.headers["User-Agent"] = random.choice(USER_AGENTS)

    def search_articles(
        self,
        query: str,
        page: int = 1,
        article_type: str = "article",
    ) -> List[Dict[str, Any]]:
        """Search for articles by keyword.

        Args:
            query: Search query string.
            page: Page number (1-indexed).
            article_type: 'article' for article search, 'account' for account search.

        Returns:
            List of article/account dicts with at least: title, link, account, snippet.
        """
        self.limiter.wait()
        self._rotate_ua()

        search_type = "2" if article_type == "article" else "1"
        params = {
            "type": search_type,
            "query": query,
            "page": page,
            "ie": "utf8",
        }
        url = f"{self.SEARCH_URL}?{urlencode(params)}"

        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            html = resp.text
            if detect_blocked(html):
                self.blocked_pages += 1
                message = f"搜狗返回验证/限流页 (page={page})，请降低频率或配置代理"
                logger.warning(message)
                if self.raise_on_block:
                    raise SogouBlocked(message)
                return []
            return self._parse_search_results(html, article_type)
        except SogouBlocked:
            raise
        except Exception as e:
            logger.warning(f"搜索失败 (page={page}): {e}")
            return []

    @property
    def blocked(self) -> bool:
        """True if any page in this session hit Sogou's anti-bot wall."""
        return self.blocked_pages > 0

    def search_account_articles(
        self,
        account_name: str,
        keyword: Optional[str] = None,
        max_pages: Optional[int] = None,
        strict_account: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search for articles from a specific account.

        Strategy:
        1. Search with the account name as a qualifier
        2. Keep only results whose account/title actually mentions the account
        3. Optionally filter by keyword

        Args:
            strict_account: When True, unrelated results are dropped. When False,
                a page with no account match falls back to all its results.
        """
        pages = max_pages or self.max_pages
        all_articles = []

        query = f"{account_name}"
        if keyword:
            query = f"{keyword} {account_name}"

        for page in range(1, pages + 1):
            results = self.search_articles(query, page=page, article_type="article")
            if not results:
                break

            filtered = [
                r for r in results
                if account_name.lower() in r.get("account", "").lower()
                or account_name.lower() in r.get("title", "").lower()
            ]
            if filtered or strict_account:
                all_articles.extend(filtered)
            else:
                all_articles.extend(results)
            logger.info(f"第 {page} 页: 获取 {len(results)} 条, 匹配 {len(filtered)} 条")

            if page < pages:
                time.sleep(random.uniform(2, 5))

        return all_articles

    def search_accounts(
        self,
        account_name: str,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Search for Official Account profiles (name, wechat_id, profile link)."""
        pages = max_pages or self.max_pages
        accounts: List[Dict[str, Any]] = []
        for page in range(1, pages + 1):
            results = self.search_articles(account_name, page=page, article_type="account")
            if not results:
                break
            accounts.extend(results)
            if page < pages:
                time.sleep(random.uniform(2, 5))
        return accounts

    def search_by_keyword(
        self,
        keyword: str,
        max_pages: Optional[int] = None,
        account_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search articles by keyword across all accounts.

        Args:
            keyword: Search keyword.
            max_pages: Max pages to fetch.
            account_filter: Optional account name to filter results.
        """
        pages = max_pages or self.max_pages
        all_articles = []

        for page in range(1, pages + 1):
            results = self.search_articles(keyword, page=page)
            if not results:
                break

            if account_filter:
                results = [
                    r for r in results
                    if account_filter.lower() in r.get("account", "").lower()
                ]

            all_articles.extend(results)
            logger.info(f"第 {page} 页: 获取 {len(results)} 条")

            if page < pages:
                time.sleep(random.uniform(2, 5))

        return all_articles

    def _parse_search_results(self, html: str, article_type: str) -> List[Dict[str, Any]]:
        """Parse Sogou search result HTML into structured data."""
        results = []

        if article_type == "article":
            # Parse article search results
            # Each result is in a <div class="txt-box"> inside <li>
            blocks = re.findall(
                r'<div class="txt-box">(.*?)</div>\s*</div>',
                html, re.DOTALL
            )
            if not blocks:
                # Alternative pattern
                blocks = re.findall(
                    r'<div class="txt-box">(.*?)</li>',
                    html, re.DOTALL
                )

            for block in blocks:
                article = self._parse_article_block(block)
                if article:
                    results.append(article)
        else:
            # Parse account search results
            blocks = re.findall(
                r'<div class="txt-box">(.*?)</div>\s*</li>',
                html, re.DOTALL
            )
            for block in blocks:
                account = self._parse_account_block(block)
                if account:
                    results.append(account)

        # Also try a generic fallback pattern
        if not results:
            results = self._parse_generic_results(html)

        return results

    def _parse_article_block(self, block: str) -> Optional[Dict[str, Any]]:
        """Parse a single article result block."""
        # Title and link
        title_match = re.search(
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            block, re.DOTALL
        )
        if not title_match:
            return None

        link = _clean_text(title_match.group(1))
        title = _clean_text(title_match.group(2))

        # Fix relative URLs
        if link.startswith("/"):
            link = self.BASE_URL + link

        # Account name — current Sogou markup uses a plain span, older used
        # `<a class="account">`; the s-p block is the last resort.
        account = ""
        for pattern in (
            r'<span[^>]*class="[^"]*all-time-y2[^"]*"[^>]*>(.*?)</span>',
            r'<a[^>]*class="account"[^>]*>(.*?)</a>',
            r'<div[^>]*class="s-p"[^>]*>(.*)',
        ):
            match = re.search(pattern, block, re.DOTALL)
            if match:
                account = _clean_text(match.group(1))
                if account:
                    break

        # Snippet / description
        snippet = ""
        snippet_match = re.search(
            r'<p[^>]*class="[^"]*txt-info[^"]*"[^>]*>(.*?)</p>',
            block, re.DOTALL
        )
        if not snippet_match:
            snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        if snippet_match:
            snippet = _clean_text(snippet_match.group(1))

        # Date — Sogou prints either an ISO date, a timeConvert('unix') script
        # call, or a relative label like '3小时前'.
        pub_date = self._extract_date(block)

        return {
            "title": title,
            "link": link,
            "account": account,
            "snippet": snippet,
            "pub_date": pub_date,
            "source": "sogou",
            "content_text": "",  # Will be filled by ArticleFetcher
            "content_markdown": "",
            "images": [],
            "author": account,
            "word_count": 0,
        }

    @staticmethod
    def _extract_date(block: str) -> Optional[str]:
        """Pull a publication date out of one Sogou result block."""
        m = re.search(r'timeConvert\(\'(\d{10})\'\)', block)
        if m:
            try:
                return datetime.fromtimestamp(int(m.group(1))).isoformat()
            except (ValueError, OSError):
                pass
        text = re.sub(r'<[^>]+>', ' ', block)
        m = re.search(r'\d{4}-\d{2}-\d{2}', text)
        if m:
            try:
                return datetime.strptime(m.group(0), "%Y-%m-%d").isoformat()
            except ValueError:
                pass
        m = re.search(r'\d{4}年\d{1,2}月\d{1,2}日', text)
        if m:
            try:
                return datetime.strptime(m.group(0), "%Y年%m月%d日").isoformat()
            except ValueError:
                pass
        relative = parse_relative_date(text)
        return relative.isoformat() if relative else None

    def _parse_account_block(self, block: str) -> Optional[Dict[str, Any]]:
        """Parse a single account result block."""
        name_match = re.search(r'<a[^>]*>(.*?)</a>', block, re.DOTALL)
        if not name_match:
            return None
        name = _clean_text(name_match.group(1))

        link_match = re.search(r'<a[^>]*href="([^"]*)"', block)
        link = _clean_text(link_match.group(1)) if link_match else ""
        if link.startswith("/"):
            link = self.BASE_URL + link

        # WeChat ID
        wechat_id = ""
        id_match = re.search(r'微信号[:：]\s*(\S+)', block)
        if id_match:
            wechat_id = id_match.group(1)

        return {
            "name": name,
            "wechat_id": wechat_id,
            "link": link,
            "source": "sogou",
        }

    def _parse_generic_results(self, html: str) -> List[Dict[str, Any]]:
        """Fallback: extract any links that look like WeChat article URLs."""
        results = []
        # Find mp.weixin.qq.com links
        links = [_clean_text(l) for l in re.findall(
            r'href="(https?://mp\.weixin\.qq\.com/s[^"]*)"',
            html
        )]
        titles = re.findall(
            r'<a[^>]*href="https?://mp\.weixin\.qq\.com/s[^"]*"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        if not links:
            # Sogou wraps articles in its own /link?url= redirect; keep those
            # too — ArticleFetcher can resolve them.
            links = [self.BASE_URL + _clean_text(l) for l in re.findall(
                r'href="(/link\?url=[^"]*)"', html
            )]
            titles = re.findall(
                r'<a[^>]*href="/link\?url=[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL
            )
        for i, link in enumerate(links):
            title = _clean_text(titles[i]) if i < len(titles) else ""
            results.append({
                "title": title,
                "link": link,
                "account": "",
                "snippet": "",
                "pub_date": None,
                "source": "sogou",
                "content_text": "",
                "content_markdown": "",
                "images": [],
                "word_count": 0,
            })
        return results