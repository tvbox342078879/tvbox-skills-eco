"""Deduplication for articles using content hash and title similarity."""

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from .utils import canonicalize_url


def content_hash(text: str) -> str:
    """Generate a hash from normalized article content."""
    # Normalize: remove whitespace, lowercase
    normalized = re.sub(r'\s+', '', text.lower())
    return hashlib.md5(normalized.encode()).hexdigest()


def title_similarity(a: str, b: str) -> float:
    """Compute title similarity ratio (0-1)."""
    # Normalize
    a_clean = re.sub(r'\s+', '', a.lower())
    b_clean = re.sub(r'\s+', '', b.lower())
    return SequenceMatcher(None, a_clean, b_clean).ratio()


class Deduplicator:
    """Article deduplicator with configurable strategies."""

    def __init__(
        self,
        similarity_threshold: float = 0.92,
        use_content_hash: bool = True,
        use_title_match: bool = True,
    ):
        self.similarity_threshold = similarity_threshold
        self.use_content_hash = use_content_hash
        self.use_title_match = use_title_match
        self._seen_hashes: Set[str] = set()
        self._seen_links: Set[str] = set()
        self._seen_titles: List[str] = []
        self._checked = 0
        self._duplicates = 0

    def duplicate_reason(self, article: Dict[str, Any]) -> Optional[str]:
        """Return why an article is a duplicate, or None if it is new."""
        link = canonicalize_url(article.get("link", ""))
        title = article.get("title", "").strip()
        content = article.get("content_text", "")

        if link and link in self._seen_links:
            return "link"
        if self.use_content_hash and content:
            if content_hash(content) in self._seen_hashes:
                return "content_hash"
        if self.use_title_match and title:
            for seen_title in self._seen_titles:
                if title_similarity(title, seen_title) >= self.similarity_threshold:
                    return "title_similarity"
        return None

    def is_duplicate(self, article: Dict[str, Any]) -> bool:
        """Check if article is a duplicate of something already seen."""
        self._checked += 1
        reason = self.duplicate_reason(article)
        if reason:
            self._duplicates += 1
            return True
        return False

    def mark_seen(self, article: Dict[str, Any]):
        """Record an article as seen."""
        link = canonicalize_url(article.get("link", ""))
        title = article.get("title", "").strip()
        content = article.get("content_text", "")

        if link:
            self._seen_links.add(link)
        if content and self.use_content_hash:
            self._seen_hashes.add(content_hash(content))
        if title and self.use_title_match:
            self._seen_titles.append(title)

    def deduplicate(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicates from a list of articles."""
        result = []
        for article in articles:
            if not self.is_duplicate(article):
                self.mark_seen(article)
                result.append(article)
        return result

    def find_duplicate_groups(self, articles: List[Dict[str, Any]]) -> List[List[int]]:
        """Group article indices that are near-duplicates of each other.

        Unlike deduplicate(), nothing is discarded — useful for reporting.
        """
        groups: List[List[int]] = []
        keys: List[Tuple[str, str]] = []
        for idx, article in enumerate(articles):
            link = canonicalize_url(article.get("link", ""))
            title = article.get("title", "").strip()
            placed = False
            for g_idx, group in enumerate(groups):
                for other in group:
                    other_link, other_title = keys[other]
                    if link and link == other_link:
                        group.append(idx)
                        placed = True
                        break
                    if title and other_title and title_similarity(title, other_title) >= self.similarity_threshold:
                        group.append(idx)
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                groups.append([idx])
            keys.append((link, title))
        return [g for g in groups if len(g) > 1]

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "seen_links": len(self._seen_links),
            "seen_hashes": len(self._seen_hashes),
            "seen_titles": len(self._seen_titles),
            "checked": self._checked,
            "duplicates": self._duplicates,
        }
