"""Download article images locally and rewrite Markdown to point at them.

WeChat serves images from mmbiz.qpic.cn with Referer checks, so a saved
Markdown file usually shows broken images. Downloading them next to the note
makes an export readable offline.
"""

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import get_logger, sanitize_filename, ensure_dir, USER_AGENTS, resolve_export_dir

logger = get_logger("image-downloader")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
}


def _extension(url: str, content_type: str = "") -> str:
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type in _EXT_BY_CONTENT_TYPE:
        return _EXT_BY_CONTENT_TYPE[content_type]
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def image_filename(url: str, index: int, content_type: str = "") -> str:
    """Stable, human-ordered filename for one image URL."""
    digest = hashlib.md5(url.encode()).hexdigest()[:10]
    return f"{index:03d}_{digest}{_extension(url, content_type)}"


class ImageDownloader:
    """Fetch article images into a local folder."""

    def __init__(
        self,
        output_dir: Optional[str] = None,
        timeout: int = 20,
        max_images: int = 30,
        proxy: Optional[str] = None,
        min_bytes: int = 1024,
    ):
        self.output_dir = Path(output_dir) if output_dir else resolve_export_dir()
        self.timeout = timeout
        self.max_images = max_images
        self.proxy = proxy
        # WeChat serves a 1px placeholder for hotlink-blocked requests.
        self.min_bytes = min_bytes
        self.session = requests.Session() if HAS_REQUESTS else None
        if self.session is not None:
            self.session.headers.update({
                "User-Agent": USER_AGENTS[0],
                "Referer": "https://mp.weixin.qq.com/",
            })
            if proxy:
                self.session.proxies = {"http": proxy, "https": proxy}

    def download(self, url: str, target_dir: Path, index: int = 1) -> Optional[str]:
        """Download one image. Returns the local path, or None on failure."""
        if self.session is None:
            logger.warning("requests 未安装，跳过图片下载")
            return None
        if not url or url.startswith("data:"):
            return None
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.content
            if len(payload) < self.min_bytes:
                logger.info(f"跳过占位图({len(payload)}B): {url[:60]}")
                return None
            name = image_filename(url, index, resp.headers.get("Content-Type", ""))
            path = Path(target_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return str(path)
        except Exception as e:
            logger.warning(f"图片下载失败 {url[:60]}: {e}")
            return None

    def download_article_images(
        self,
        article: Dict[str, Any],
        folder_name: Optional[str] = None,
        rewrite_markdown: bool = True,
    ) -> Dict[str, Any]:
        """Download every image of one article into its own subfolder."""
        images = article.get("images") or []
        title = article.get("title") or folder_name or "article"
        target = ensure_dir(self.output_dir / sanitize_filename(folder_name or title, max_len=50))

        mapping: Dict[str, str] = {}
        failures = 0
        for i, image in enumerate(images[: self.max_images], 1):
            src = image.get("src", "") if isinstance(image, dict) else str(image)
            local = self.download(src, target, i)
            if local:
                mapping[src] = os.path.relpath(local, target)
            else:
                failures += 1

        result = {
            "title": title,
            "image_dir": str(target),
            "downloaded": len(mapping),
            "failed": failures,
            "total": len(images),
            "mapping": mapping,
        }

        if rewrite_markdown and mapping:
            result["content_markdown"] = rewrite_image_links(
                article.get("content_markdown", ""), mapping
            )
        return result

    def download_for_articles(
        self,
        articles: List[Dict[str, Any]],
        max_articles: int = 20,
    ) -> Dict[str, Any]:
        """Bulk download; each article gets its own image folder."""
        folders = {}
        downloaded = 0
        failed = 0
        for article in articles[:max_articles]:
            if not article.get("images"):
                continue
            res = self.download_article_images(article)
            downloaded += res["downloaded"]
            failed += res["failed"]
            folders[res["title"]] = res["image_dir"]
        return {
            "articles_processed": len(folders),
            "images_downloaded": downloaded,
            "images_failed": failed,
            "folders": folders,
        }


def rewrite_image_links(markdown: str, mapping: Dict[str, str]) -> str:
    """Swap remote image URLs for the local relative paths we just saved."""
    if not markdown:
        return markdown
    for src, local in mapping.items():
        markdown = markdown.replace(src, local)
    return markdown


def extract_image_urls(markdown_or_html: str) -> List[str]:
    """Pull image URLs out of Markdown/HTML text (data: URIs ignored)."""
    urls = re.findall(r'!?\[[^\]]*\]\((https?://[^)\s]+)\)', markdown_or_html)
    urls += re.findall(r'<img[^>]+(?:data-src|src)="(https?://[^"]+)"', markdown_or_html)
    result = []
    for url in urls:
        if url not in result:
            result.append(url)
    return result
