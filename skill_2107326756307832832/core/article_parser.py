"""Article HTML parsing: extract title, author, date, content, images."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Lightweight HTML-to-text / Markdown converter
# ---------------------------------------------------------------------------

class ArticleParser(HTMLParser):
    """Parse a WeChat article HTML into structured data."""

    def __init__(self):
        super().__init__()
        self._title = ""
        self._author = ""
        self._account = ""
        self._summary = ""
        self._pub_date: Optional[datetime] = None
        self._content_parts: List[str] = []
        self._images: List[Dict[str, str]] = []
        self._link = ""

        # State tracking
        self._in_title = False
        self._in_content = False
        self._content_depth = 0
        self._in_meta = False
        self._current_tag = ""
        self._tag_stack: List[str] = []
        self._link_stack: List[str] = []
        self._text_buffer: List[str] = []

    # -- Public API --

    def parse(self, html: str, url: str = "") -> Dict[str, Any]:
        self._link = url
        self.feed(html)
        self.close()
        content_text = self._get_content_text()
        return {
            "title": self._title.strip(),
            "author": self._author.strip(),
            "account": self._account.strip(),
            "summary": self._summary.strip(),
            "pub_date": self._pub_date.isoformat() if self._pub_date else None,
            "content_text": content_text,
            "content_markdown": self._get_content_markdown(),
            "images": self._images,
            "link": url,
            "word_count": len(content_text),
        }

    # -- Parser callbacks --

    def handle_starttag(self, tag: str, attrs: list):
        self._current_tag = tag
        attr_dict = dict(attrs)

        # Extract title from meta or <title> tag
        if tag == "meta":
            prop = attr_dict.get("property", "") or attr_dict.get("name", "")
            content = attr_dict.get("content", "")
            if prop == "og:title" or prop == "twitter:title":
                self._title = content
            elif prop == "og:description" and content:
                self._summary = content
            elif prop == "author":
                self._author = content

        if tag == "title" and not self._title:
            self._in_title = True

        # Rich Media Content area
        if tag == "div":
            cls = attr_dict.get("class", "")
            id_val = attr_dict.get("id", "")
            if "rich_media_content" in cls or "js_content" in id_val:
                self._in_content = True
                self._content_depth = 1
            elif self._in_content:
                self._content_depth += 1
            if "rich_media_title" in cls:
                self._in_title = True
            if "rich_media_meta_text" in cls or "profile_nickname" in cls:
                self._in_meta = True
            if "publish_time" in cls or "date" in cls:
                self._in_meta = True

        if tag == "span":
            cls = attr_dict.get("class", "")
            if "rich_media_meta_nickname" in cls:
                self._in_meta = True
            if "publish_time" in cls:
                self._in_meta = True

        # Collect images
        if tag == "img" and self._in_content:
            src = attr_dict.get("data-src") or attr_dict.get("src", "")
            if src and not src.startswith("data:"):
                alt = attr_dict.get("alt", "")
                self._images.append({"src": src, "alt": alt})
                self._content_parts.append(f"\n\n![{alt}]({src})\n\n")

        # Track structure for markdown conversion
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            if self._in_content:
                self._content_parts.append(f"\n{'#' * level} ")
        elif tag == "p" and self._in_content:
            self._content_parts.append("\n\n")
        elif tag == "br" and self._in_content:
            self._content_parts.append("\n")
        elif tag == "blockquote" and self._in_content:
            self._content_parts.append("\n> ")
        elif tag == "li" and self._in_content:
            self._content_parts.append("\n- ")
        elif tag == "strong" or tag == "b":
            if self._in_content:
                self._content_parts.append("**")
        elif tag == "em" or tag == "i":
            if self._in_content:
                self._content_parts.append("*")
        elif tag == "a":
            href = attr_dict.get("href", "")
            if self._in_content and href:
                self._content_parts.append("[")
                self._link_stack.append(href)
            self._tag_stack.append(tag)
            return
        elif tag == "section":
            pass  # Skip section wrappers

        # Void elements never receive a close tag; don't stack them.
        if tag not in ("img", "br", "hr", "meta", "link", "input", "source"):
            self._tag_stack.append(tag)

    def handle_endtag(self, tag: str):
        if self._in_title and tag in ("title", "div", "h1"):
            self._in_title = False

        if tag == "div" and self._in_content:
            self._content_depth -= 1
            if self._content_depth <= 0:
                self._in_content = False
                self._content_depth = 0

        if tag == "strong" or tag == "b":
            if self._in_content:
                self._content_parts.append("**")
        elif tag == "em" or tag == "i":
            if self._in_content:
                self._content_parts.append("*")
        elif tag == "a" and self._in_content:
            href = self._link_stack.pop() if self._link_stack else ""
            self._content_parts.append(f"]({href})")

        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

    def handle_data(self, data: str):
        text = data.strip()
        if not text:
            return

        if self._in_title and not self._title:
            self._title += text

        if self._in_meta:
            if not self._account and not self._author:
                # First meta text is often the account name
                self._account = text
            elif not self._author:
                self._author = text
            # Try to parse date
            date_match = re.search(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})', text)
            if date_match and not self._pub_date:
                try:
                    y, m, d = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
                    self._pub_date = datetime(y, m, d)
                except ValueError:
                    pass
            self._in_meta = False

        if self._in_content:
            self._content_parts.append(text)

        self._text_buffer.append(text)

    # -- Output helpers --

    def _get_content_text(self) -> str:
        """Get plain text content (strip markdown syntax)."""
        md = self._get_content_markdown()
        # Strip markdown syntax for plain text
        text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', md)  # images carry no prose
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # links
        text = re.sub(r'[*_]{1,2}([^*_]+)[*_]{1,2}', r'\1', text)  # bold/italic
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # headings
        text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)  # blockquotes
        text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)  # lists
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _get_content_markdown(self) -> str:
        """Get markdown content."""
        md = "".join(self._content_parts)
        # Clean up excessive newlines
        md = re.sub(r'\n{3,}', '\n\n', md)
        return md.strip()


# ---------------------------------------------------------------------------
# Standalone functions
# ---------------------------------------------------------------------------

def parse_article_html(html: str, url: str = "") -> Dict[str, Any]:
    """Parse article HTML and return structured data."""
    parser = ArticleParser()
    return parser.parse(html, url)


def _meta_content(html: str, key: str) -> Optional[str]:
    """Read a meta tag's content regardless of attribute order or quote style."""
    pattern = (
        r'<meta[^>]*?(?:property|name)\s*=\s*["\']' + re.escape(key) +
        r'["\'][^>]*?content\s*=\s*["\']([^"\']*)["\']'
        r'|'
        r'<meta[^>]*?content\s*=\s*["\']([^"\']*)["\'][^>]*?(?:property|name)\s*=\s*["\']' +
        re.escape(key) + r'["\']'
    )
    for m in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
        value = m.group(1) or m.group(2)
        if value:
            return value.strip()
    return None


def extract_article_meta_from_html(html: str) -> Dict[str, str]:
    """Extract just metadata from HTML (lightweight, no full parse)."""
    meta = {}
    for field, key in (
        ("title", "og:title"),
        ("description", "og:description"),
        ("author", "author"),
    ):
        value = _meta_content(html, key)
        if value:
            meta[field] = value
    if "title" not in meta:
        value = _meta_content(html, "twitter:title")
        if value:
            meta["title"] = value

    # nickname
    m = re.search(r'var\s+nickname\s*=\s*"([^"]*)"', html)
    if m:
        meta["account"] = m.group(1)
    # canonical article URL — Sogou hands us a /link?url= redirect instead
    canonical = _meta_content(html, "og:url") or _meta_content(html, "twitter:url")
    if canonical:
        meta["canonical_url"] = canonical
    m = re.search(r'var\s+msg_link\s*=\s*"([^"]*)"', html)
    if m:
        meta["canonical_url"] = m.group(1).replace("&amp;", "&")
    # publish time (Unix timestamp pattern in WeChat pages)
    m = re.search(r'var\s+ct\s*=\s*"?(\d{10})"?', html)
    if m:
        meta["pub_timestamp"] = m.group(1)
        try:
            meta["pub_date"] = datetime.fromtimestamp(int(m.group(1))).isoformat()
        except (ValueError, OSError):
            pass
    m = re.search(r'var\s+publish_time\s*=\s*"([^"]*)"', html)
    if m:
        meta["publish_time_raw"] = m.group(1)
    # og:article:published_time is more reliable than the JS vars
    published = _meta_content(html, "article:published_time")
    if published:
        try:
            meta["pub_date"] = datetime.fromisoformat(
                published.replace("Z", "+00:00")
            ).replace(tzinfo=None).isoformat()
        except ValueError:
            pass
    return meta


def html_to_markdown(html: str) -> str:
    """Convert article HTML content area to markdown."""
    result = parse_article_html(html)
    return result.get("content_markdown", "")


def html_to_text(html: str) -> str:
    """Convert article HTML content area to plain text."""
    result = parse_article_html(html)
    return result.get("content_text", "")