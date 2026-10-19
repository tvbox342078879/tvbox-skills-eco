"""Persistent scrape history, so repeat runs can pick up where they stopped.

The store is a small JSON file (no database, no binary artifacts). It maps an
article's canonical link to the metadata seen on the first scrape, which lets
`filter_new()` skip everything already exported.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .utils import canonicalize_url, resolve_state_dir, get_logger

logger = get_logger("state-store")

SCHEMA_VERSION = 1


class StateStore:
    """JSON-backed registry of previously scraped articles."""

    def __init__(self, path: Optional[str] = None):
        if path:
            self.path = Path(path)
        else:
            self.path = resolve_state_dir() / "scrape_history.json"
        self._data: Dict[str, Any] = self._load()

    # -- persistence --

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("articles"), dict):
                    return data
                logger.warning(f"历史文件格式不符，重新初始化: {self.path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"历史文件读取失败({e})，重新初始化")
        return {
            "schema": SCHEMA_VERSION,
            "created_at": datetime.now().isoformat(),
            "updated_at": None,
            "batches": 0,
            "articles": {},
        }

    def save(self) -> Path:
        """Write atomically so a crash never leaves a half-written history."""
        self._data["updated_at"] = datetime.now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".history_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return self.path

    # -- keying --

    @staticmethod
    def _key(article_or_link: Any) -> str:
        if isinstance(article_or_link, dict):
            link = article_or_link.get("link", "") or article_or_link.get("url", "")
        else:
            link = str(article_or_link)
        return canonicalize_url(link)

    # -- api --

    def record(self, articles: List[Dict[str, Any]], auto_save: bool = True) -> int:
        """Add articles to history. Returns how many were previously unseen."""
        now = datetime.now().isoformat()
        registry = self._data["articles"]
        new_count = 0
        for article in articles:
            key = self._key(article)
            if not key:
                continue
            entry = registry.get(key)
            if entry:
                entry["times_seen"] = entry.get("times_seen", 1) + 1
                entry["last_seen"] = now
                if not entry.get("title") and article.get("title"):
                    entry["title"] = article["title"]
                continue
            registry[key] = {
                "title": article.get("title", ""),
                "account": article.get("account", ""),
                "pub_date": article.get("pub_date"),
                "first_seen": now,
                "last_seen": now,
                "times_seen": 1,
            }
            new_count += 1
        self._data["batches"] = self._data.get("batches", 0) + 1
        if auto_save:
            self.save()
        return new_count

    def is_known(self, article_or_link: Any) -> bool:
        key = self._key(article_or_link)
        return bool(key) and key in self._data["articles"]

    def filter_new(
        self, articles: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split articles into (never seen, already in history)."""
        new, known = [], []
        for article in articles:
            (known if self.is_known(article) else new).append(article)
        return new, known

    def history(
        self,
        account: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Recent history entries, newest first, optionally one account only."""
        rows = []
        for link, entry in self._data["articles"].items():
            if account and account.lower() not in entry.get("account", "").lower():
                continue
            rows.append({"link": link, **entry})
        rows.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
        return rows[:limit]

    def stats(self) -> Dict[str, Any]:
        entries = list(self._data["articles"].values())
        accounts = sorted({e.get("account", "") for e in entries if e.get("account")})
        dates = [e.get("pub_date") for e in entries if e.get("pub_date")]
        return {
            "path": str(self.path),
            "total_articles": len(entries),
            "batches": self._data.get("batches", 0),
            "accounts": accounts,
            "account_count": len(accounts),
            "created_at": self._data.get("created_at"),
            "updated_at": self._data.get("updated_at"),
            "date_range": {
                "earliest": min(dates) if dates else None,
                "latest": max(dates) if dates else None,
            },
        }

    def clear(self) -> int:
        """Drop all history. Returns the number of entries removed."""
        removed = len(self._data["articles"])
        self._data["articles"] = {}
        self._data["batches"] = 0
        self.save()
        return removed
