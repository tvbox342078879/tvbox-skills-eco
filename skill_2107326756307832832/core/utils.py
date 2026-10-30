"""Core utilities for WeChat article scraper."""

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# Query params WeChat/Sogou append for tracking. They don't identify the
# article, so leaving them in breaks cache hits and link-level dedup.
TRACKING_QUERY_PARAMS = {
    "chksm", "scene", "tanh", "from", "frommsgid", "fromappmsgstyle", "ascene",
    "devicetype", "mpshare", "shareid", "srcid", "sharer_shareinfo",
    "sharer_shareinfo4", "sharer_sharetime", "sharer_userid", "nettype",
    "isappinstalled", "wxshare", "clicktime", "sessionid", "sub_type",
    "click_mids", "appuin", "tempkey", "sign", "timestamp", "srcid",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load config.json from the skill root."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_config_value(key: str, default: Any = None, config: Optional[Dict] = None) -> Any:
    """Get a config value with dot-notation support (e.g. 'sogou.rate_limit')."""
    cfg = config or load_config()
    keys = key.split(".")
    for k in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(k, default)
        else:
            return default
    return cfg


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

_RELATIVE_DATE_RE = re.compile(
    r"(刚刚|半分钟前|(\d+)\s*分钟前|(\d+)\s*小时前|(\d+)\s*天前|今天|昨天|前天|(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日)"
)


def parse_relative_date(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse Sogou/WeChat relative date strings ('昨天', '3小时前', '12天前').

    Returns None when the text has no recognizable date.
    """
    if not text:
        return None
    now = now or datetime.now()
    m = _RELATIVE_DATE_RE.search(text)
    if not m:
        return None
    token = m.group(1)
    if token == "刚刚" or token == "半分钟前":
        return now
    if m.group(2):
        return now - timedelta(minutes=int(m.group(2)))
    if m.group(3):
        return now - timedelta(hours=int(m.group(3)))
    if m.group(4):
        return now - timedelta(days=int(m.group(4)))
    if token == "今天":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if token == "昨天":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if token == "前天":
        return (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return datetime(int(m.group(5)), int(m.group(6)), int(m.group(7)))
    except (ValueError, TypeError):
        return None


def parse_date(date_str: str) -> datetime:
    """Parse various date formats into datetime.

    Handles absolute formats (YYYY-MM-DD, YYYY年MM月DD日, ...) plus the
    relative strings Sogou prints ('昨天', '3小时前'). Raises ValueError
    when nothing recognizable is present.
    """
    raw = date_str.strip()
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M",
        "%Y年%m月%d日", "%m月%d日", "%Y-%m-%d %H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:  # format without year
                dt = dt.replace(year=datetime.now().year)
            return dt
        except ValueError:
            continue
    # ISO 8601 with timezone, e.g. 2025-01-15T08:00:00+08:00
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except ValueError:
        pass
    relative = parse_relative_date(raw)
    if relative:
        return relative
    raise ValueError(f"无法解析日期: {date_str}")


def parse_pub_date(value: Any) -> Optional[datetime]:
    """Best-effort parse of an article's pub_date field.

    Accepts datetime, ISO/date strings, unix timestamps and Sogou relative
    text. Returns None instead of raising so batch filtering stays quiet.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit() and 9 <= len(text) <= 12:
        # Sogou sometimes hands out bare unix timestamps as strings.
        try:
            return datetime.fromtimestamp(int(text))
        except (ValueError, OSError, OverflowError):
            return None
    try:
        return parse_date(text)
    except ValueError:
        return None



def parse_date_range(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
) -> Tuple[datetime, datetime]:
    """Parse date range from various inputs.

    Returns (start_datetime, end_datetime). end_date defaults to now.
    If `days` is given, start = now - days.
    """
    now = datetime.now()
    if end_date:
        end_dt = parse_date(end_date)
    else:
        end_dt = now

    if start_date:
        start_dt = parse_date(start_date)
    elif days:
        start_dt = now - timedelta(days=days)
    else:
        start_dt = now - timedelta(days=30)  # default 30 days

    return start_dt, end_dt


def format_date(dt: datetime, fmt: str = "%Y-%m-%d") -> str:
    return dt.strftime(fmt)


def is_in_range(pub_date: datetime, start: datetime, end: datetime) -> bool:
    return start <= pub_date <= end


# ---------------------------------------------------------------------------
# Text / content helpers
# ---------------------------------------------------------------------------

def extract_urls(text: str) -> List[str]:
    """Extract all URLs from text, tolerating CJK punctuation glued to the tail.

    The match stops at any non-ASCII character: Chinese text often follows a bare
    URL with no whitespace ('.../s/xyz。还有百度'), and CJK is never URL-valid.
    """
    pattern = r'https?://[^\s<>"\')\]\x80-\U0010ffff]+'
    urls = []
    for raw in re.findall(pattern, text):
        cleaned = raw.rstrip('.,;:!?、。，；：！？）)】》"\'')
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return urls


def extract_wechat_urls(text: str) -> List[str]:
    """Extract only WeChat article URLs from mixed text."""
    return [u for u in extract_urls(text) if is_wechat_url(u)]


# ---------------------------------------------------------------------------
# Anti-bot detection
# ---------------------------------------------------------------------------
# Sogou and WeChat serve a captcha page instead of content once throttled.
# Such a page parses into "zero articles", which is indistinguishable from a
# genuinely empty result unless we look for these markers.

BLOCK_MARKERS = (
    "antispider", "输入验证码", "验证码错误", "去验证", "wappass",
    "您的访问出错了", "访问过于频繁", "Result_captcha", "环境异常",
)


def detect_blocked(html: Optional[str]) -> bool:
    """True when a response body looks like an anti-bot / captcha page."""
    if not html:
        return True
    sample = html[:6000]
    return any(marker in sample for marker in BLOCK_MARKERS)


def is_wechat_url(url: str) -> bool:
    """Check if URL is a WeChat article URL.

    Matches on DNS labels so 'evilmp.weixin.qq.com.attacker.tld' is rejected.
    """
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return False
    netloc = netloc.split("@")[-1].split(":")[0]
    if not netloc:
        return False
    for domain in ("mp.weixin.qq.com", "weixin.qq.com", "weixin110.qq.com"):
        if netloc == domain or netloc.endswith("." + domain):
            return True
    return False


def is_wechat_article_link(url: str) -> bool:
    """Whether a link can be resolved to WeChat article content.

    Covers both direct mp.weixin.qq.com links and the Sogou `/link?url=`
    redirect that search results actually return.
    """
    if is_wechat_url(url):
        return True
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    netloc = parsed.netloc.lower()
    return netloc.endswith("sogou.com") and "/link" in parsed.path


def is_sogou_link_url(url: str) -> bool:
    """True for Sogou's intermediate `/link?url=` page."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.netloc.lower().endswith("sogou.com") and parsed.path.startswith("/link")


_SOGOU_CHUNK = re.compile(r"""url\s*\+=\s*'([^']*)'""")
_SOGOU_STRIP = re.compile(
    r"""url\s*=\s*url\.replace\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]*)['"]\s*\)"""
)


def resolve_sogou_redirect(html: str) -> Optional[str]:
    """Rebuild the article URL that Sogou's /link page hides in JavaScript.

    That page is not an HTTP redirect: it concatenates the mp.weixin URL from
    chunks like 'https://mp.' + 'weixin.qq.c' + 'om/s?...' and calls
    window.location.replace(), so an HTTP client stops on the wrapper page.
    """
    if not html:
        return None
    chunks = _SOGOU_CHUNK.findall(html)
    if not chunks:
        return None
    target = "".join(chunks).strip()
    strip = _SOGOU_STRIP.search(html)
    if strip:
        target = target.replace(strip.group(1), strip.group(2))
    return target if is_wechat_url(target) else None


def canonicalize_url(url: str) -> str:
    """Strip tracking noise from a URL so the same article maps to one ID.

    Drops the fragment and WeChat/Sogou share params, lowercases the host and
    removes a trailing slash on bare paths.
    """
    if not url:
        return ""
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.netloc:
        return url

    keep = [
        (k, v) for k, vs in parse_qs(parsed.query, keep_blank_values=True).items()
        if k.lower() not in TRACKING_QUERY_PARAMS
        for v in vs
    ]
    keep.sort(key=lambda kv: kv[0])
    path = parsed.path
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower().split("@")[-1],
        path,
        parsed.params,
        urlencode(keep, doseq=False),
        "",  # drop fragment
    ))


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = name.strip('_. ')
    if len(name) > max_len:
        name = name[:max_len]
    return name or "untitled"


def article_id_from_url(url: str) -> str:
    """Generate a stable short ID from a WeChat article URL.

    Prefers the __biz + mid + idx triple; otherwise hashes the canonicalized
    URL so share-param variants collapse to the same ID.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    biz = qs.get("__biz", [""])[0]
    mid = qs.get("mid", [""])[0]
    idx = qs.get("idx", [""])[0]
    if biz and mid:
        return f"{biz[:12]}_{mid}_{idx}"
    sn = qs.get("sn", [""])[0]
    if sn:
        return f"sn_{sn}"
    return hashlib.md5(canonicalize_url(url).encode()).hexdigest()[:16]


def truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def count_keywords(text: str, keywords: List[str]) -> int:
    """Count how many keywords appear in text."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def keyword_frequency(text: str, keywords: List[str]) -> int:
    """Total occurrences of all keywords — weights repeated mentions."""
    text_lower = text.lower()
    return sum(text_lower.count(kw.lower()) for kw in keywords)


def merge_article(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """Merge fetched article onto search metadata without losing either.

    Empty values in `update` (typical of a failed fetch) never overwrite a
    populated field in `base`.
    """
    merged = dict(base)
    for key, value in update.items():
        if value in ("", None, [], 0) and merged.get(key) not in ("", None, [], 0):
            continue
        merged[key] = value
    return merged



def match_keywords(text: str, keywords: List[str], match_all: bool = False) -> bool:
    """Check if text matches keywords.

    If match_all=True, ALL keywords must appear (AND logic).
    Otherwise ANY keyword matches (OR logic).
    """
    text_lower = text.lower()
    hits = [kw.lower() in text_lower for kw in keywords]
    if match_all:
        return all(hits)
    return any(hits)


# ---------------------------------------------------------------------------
# File / IO helpers
# ---------------------------------------------------------------------------

def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Runtime directories
# ---------------------------------------------------------------------------
# ZIP/图片是二进制产物，留在技能目录里会导致 SkillHub 拒绝上传，
# 因此默认落在系统临时目录，可用环境变量或 config 覆盖。

ENV_EXPORT_DIR = "WECHAT_SCRAPER_EXPORT_DIR"
ENV_DATA_DIR = "WECHAT_SCRAPER_DATA_DIR"


def temp_base_dir() -> Path:
    return Path(tempfile.gettempdir()) / "wechat-article-scraper"


def resolve_export_dir(
    export_dir: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Path:
    """Resolve where binary/markdown artifacts are written.

    Precedence: explicit arg > WECHAT_SCRAPER_EXPORT_DIR > config
    export.default_output_dir > system temp dir.
    """
    target = (
        export_dir
        or os.environ.get(ENV_EXPORT_DIR)
        or (config or {}).get("export", {}).get("default_output_dir")
    )
    path = Path(target) if target else temp_base_dir() / "exports"
    return ensure_dir(path)


def resolve_state_dir(config: Optional[Dict[str, Any]] = None) -> Path:
    """Resolve the directory for small JSON state (history/cache)."""
    target = os.environ.get(ENV_DATA_DIR) or str(DEFAULT_DATA_DIR)
    return ensure_dir(Path(target))



def save_json(data: Any, path: Path, indent: int = 2) -> Path:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent, default=str)
    return path


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def file_size_str(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ---------------------------------------------------------------------------
# Rate limiting / retry
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple rate limiter with per-request delay."""

    def __init__(self, min_delay: float = 2.0, max_delay: float = 5.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_request = 0.0

    def wait(self):
        import random
        elapsed = time.time() - self._last_request
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.time()


def retry(fn, max_retries: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """Retry a function with exponential backoff."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(delay * (backoff ** attempt))
    raise last_error


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name: str = "wechat-scraper"):
    """Get a simple logger."""
    import logging
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger