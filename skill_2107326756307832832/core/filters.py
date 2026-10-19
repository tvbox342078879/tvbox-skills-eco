"""Date range and keyword filtering for articles."""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .utils import (
    parse_date, parse_pub_date, is_in_range, match_keywords,
    count_keywords, keyword_frequency,
)


def _has_time_component(date_str: str) -> bool:
    return bool(re.search(r"\d{1,2}:\d{2}", date_str or ""))


def filter_by_date(
    articles: List[Dict[str, Any]],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    keep_undated: bool = False,
) -> List[Dict[str, Any]]:
    """Filter articles by publication date range.

    Args:
        articles: List of article dicts with 'pub_date' field (ISO string or datetime).
        start_date: Start date string (inclusive).
        end_date: End date string (inclusive through the end of that day).
        days: If start_date not given, look back this many days.
        keep_undated: Keep articles whose pub_date is missing/unparseable.

    Returns:
        Filtered list of articles.
    """
    if not start_date and not end_date and not days:
        return articles

    now = datetime.now()
    if end_date:
        end_dt = parse_date(end_date) if isinstance(end_date, str) else end_date
        # A date-only bound still includes articles published later that day.
        if isinstance(end_date, str) and not _has_time_component(end_date):
            end_dt = end_dt + timedelta(days=1) - timedelta(microseconds=1)
    else:
        end_dt = now

    if start_date:
        start_dt = parse_date(start_date) if isinstance(start_date, str) else start_date
    elif days:
        start_dt = now - timedelta(days=days)
    else:
        start_dt = now - timedelta(days=30)

    result = []
    for article in articles:
        pub_dt = parse_pub_date(article.get("pub_date"))
        if pub_dt is None:
            if keep_undated:
                result.append(article)
            continue
        if is_in_range(pub_dt, start_dt, end_dt):
            result.append(article)

    return result



def filter_by_keywords(
    articles: List[Dict[str, Any]],
    keywords: List[str],
    match_all: bool = False,
    search_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Filter articles by keyword presence.

    Args:
        articles: List of article dicts.
        keywords: Keywords to match.
        match_all: If True, all keywords must match (AND). Otherwise ANY (OR).
        search_fields: Which fields to search in. Defaults to title + content.

    Returns:
        Filtered list of articles matching keywords.
    """
    if not keywords:
        return articles

    fields = search_fields or ["title", "content_text", "content_markdown"]

    result = []
    for article in articles:
        # Combine text from selected fields
        combined = " ".join(str(article.get(f, "")) for f in fields)
        if match_keywords(combined, keywords, match_all):
            result.append(article)

    return result


def rank_by_relevance(
    articles: List[Dict[str, Any]],
    keywords: List[str],
    search_fields: Optional[List[str]] = None,
    title_weight: int = 3,
) -> List[Dict[str, Any]]:
    """Rank articles by keyword relevance.

    Score = keyword hits in title * title_weight + total occurrences in body.
    Adds '_relevance' (and '_relevance_detail') to each article dict.
    """
    if not keywords:
        return articles

    for article in articles:
        title = str(article.get("title", ""))
        body_fields = search_fields or ["content_text", "content_markdown"]
        body = " ".join(str(article.get(f, "")) for f in body_fields)
        hits = count_keywords(title, keywords) * title_weight + keyword_frequency(body, keywords)
        article["_relevance"] = hits
        article["_relevance_detail"] = {
            "title_hits": count_keywords(title, keywords),
            "body_frequency": keyword_frequency(body, keywords),
        }

    return sorted(articles, key=lambda a: a.get("_relevance", 0), reverse=True)


def normalize_link(link: str) -> str:
    """Canonical form of an article link for dedup comparisons."""
    from .utils import canonicalize_url
    return canonicalize_url(link or "")


def deduplicate(
    articles: List[Dict[str, Any]],
    by_link: bool = True,
    by_title: bool = True,
) -> List[Dict[str, Any]]:
    """Remove duplicate articles by link or title.

    Links are compared in canonical form so tracking-param variants of the
    same article collapse to one entry.
    """
    seen_links = set()
    seen_titles = set()
    result = []
    for article in articles:
        link = normalize_link(article.get("link", ""))
        title = article.get("title", "").strip()
        if by_link and link and link in seen_links:
            continue
        if by_title and title and title in seen_titles:
            continue
        if link:
            seen_links.add(link)
        if title:
            seen_titles.add(title)
        result.append(article)
    return result
