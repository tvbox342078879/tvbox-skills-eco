"""Offline article analysis: extractive summaries, keyword extraction, digests.

Deliberately stdlib-only — a batch of scraped articles gets a usable overview
without an API key or network call. Sentence scores come from term frequency,
so long articles still yield short summaries.
"""

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_SENTENCE_SPLIT = re.compile(r"[。！？；;\n!?]+")
_CJK_SEQ = re.compile(r"[\u4e00-\u9fff]{2,}")
# Two-letter tech abbreviations (AI, PC, 5G) are exactly the terms worth ranking.
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,}")

# High-frequency function words that would otherwise top every list.
_STOPWORDS = {
    "我们", "你们", "他们", "这个", "那个", "什么", "怎么", "可以", "已经", "因为",
    "所以", "但是", "如果", "不是", "没有", "一个", "一些", "以及", "对于", "通过",
    "进行", "认为", "表示", "指出", "记者", "报道", "了解", "同时", "目前", "今年",
    "去年", "明年", "月", "日", "年", "的", "了", "是", "在", "和", "与", "也",
    "the", "and", "for", "with", "that", "this", "from", "have", "will", "your",
}


def _tokenize(text: str) -> List[str]:
    """Split text into candidate terms: CJK bigrams + ASCII words."""
    terms: List[str] = []
    for run in _CJK_SEQ.findall(text):
        # Bigrams approximate word segmentation without a dictionary.
        terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    terms.extend(w.lower() for w in _ASCII_WORD.findall(text))
    return [t for t in terms if t not in _STOPWORDS]


def extract_keywords(articles: Any, top_n: int = 15) -> List[Tuple[str, int]]:
    """Top terms across one article dict or a list of them.

    Terms appearing in a title count double — the author chose them.
    """
    items = articles if isinstance(articles, list) else [articles]
    counter: Counter = Counter()
    for article in items:
        body = article.get("content_text") or article.get("content_markdown", "")
        counter.update(_tokenize(body))
        # Terms appearing in a title count double — the author chose them.
        for term in _tokenize(article.get("title", "")):
            counter[term] += 2
    return counter.most_common(top_n)


def summarize_article(
    article: Dict[str, Any],
    max_sentences: int = 3,
) -> Dict[str, Any]:
    """Extractive summary: highest-scoring sentences, kept in original order."""
    body = (article.get("content_text") or article.get("content_markdown")
            or article.get("snippet", ""))
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(body) if len(s.strip()) >= 8]
    if not sentences:
        return {
            "summary": article.get("summary", "") or "",
            "sentences": [],
            "method": "fallback_meta" if article.get("summary") else "no_content",
        }

    token_sets = [_tokenize(s) for s in sentences]
    freq = Counter(t for terms in token_sets for t in terms)
    scored = []
    for idx, terms in enumerate(token_sets):
        # A sentence is informative when it carries vocabulary the rest of the
        # article also cares about, normalized by its own length.
        score = sum(freq[t] for t in set(terms)) / max(len(terms), 1)
        scored.append((idx, score, len(set(terms))))

    top = sorted(scored, key=lambda x: (x[1], x[2]), reverse=True)[:max_sentences]
    picked = [sentences[i] for i, _, _ in sorted(top, key=lambda x: x[0])]
    return {
        "summary": "。".join(picked) + "。",
        "sentences": picked,
        "method": "extractive",
    }


def article_stats(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate statistics for a batch of articles."""
    if not articles:
        return {
            "count": 0, "total_words": 0, "avg_words": 0, "accounts": {},
            "date_range": {"earliest": None, "latest": None},
            "with_full_text": 0, "failed": 0, "images": 0,
        }

    words = [int(a.get("word_count") or 0) for a in articles]
    accounts = Counter(
        a.get("account") or "未知公众号" for a in articles
    )
    dates = sorted(
        str(a["pub_date"]) for a in articles if a.get("pub_date")
    )
    return {
        "count": len(articles),
        "total_words": sum(words),
        "avg_words": round(sum(words) / len(words), 1),
        "max_words": max(words),
        "with_full_text": sum(1 for w in words if w > 0),
        "failed": sum(1 for a in articles if a.get("error")),
        "images": sum(len(a.get("images") or []) for a in articles),
        "accounts": dict(accounts.most_common()),
        "account_count": len([k for k in accounts if k != "未知公众号"]),
        "date_range": {
            "earliest": dates[0] if dates else None,
            "latest": dates[-1] if dates else None,
        },
    }


def build_digest(
    articles: List[Dict[str, Any]],
    top_keywords: int = 12,
    max_sentences: int = 2,
    top_articles: int = 5,
) -> Dict[str, Any]:
    """Full batch digest: stats + keywords + per-article summaries + markdown."""
    stats = article_stats(articles)
    keywords = extract_keywords(articles, top_n=top_keywords)

    detailed = []
    for article in articles:
        result = summarize_article(article, max_sentences=max_sentences)
        detailed.append({
            "title": article.get("title", ""),
            "account": article.get("account", ""),
            "pub_date": article.get("pub_date"),
            "word_count": article.get("word_count", 0),
            "link": article.get("link", ""),
            **result,
        })

    ranked = sorted(
        detailed,
        key=lambda a: (a["word_count"], len(a.get("summary", ""))),
        reverse=True,
    )

    digest_md = render_digest_md(detailed, stats, keywords)
    return {
        "generated_at": datetime.now().isoformat(),
        "stats": stats,
        "keywords": [{"term": t, "count": c} for t, c in keywords],
        "articles": detailed,
        "top_articles": ranked[:top_articles],
        "digest_markdown": digest_md,
    }


def render_digest_md(
    articles: List[Dict[str, Any]],
    stats: Dict[str, Any],
    keywords: List[Tuple[str, int]],
) -> str:
    """Render a digest as shareable Markdown (pure text, safe to upload)."""
    lines = ["# 微信公众号文章摘要\n"]
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("## 一、整体情况\n")
    lines.append(f"- 文章数量：{stats['count']} 篇")
    lines.append(f"- 全文获取成功：{stats['with_full_text']} 篇，失败 {stats['failed']} 篇")
    lines.append(f"- 总字数：{stats['total_words']:,} 字，平均 {stats['avg_words']} 字")
    lines.append(f"- 涉及公众号：{stats['account_count']} 个")
    span = stats["date_range"]
    if span["earliest"]:
        lines.append(f"- 时间跨度：{span['earliest']} ~ {span['latest']}")

    if keywords:
        lines.append("\n## 二、高频关键词\n")
        lines.append("、".join(f"{t}({c})" for t, c in keywords))

    lines.append("\n## 三、公众号分布\n")
    for account, count in list(stats["accounts"].items())[:10]:
        lines.append(f"- {account}：{count} 篇")

    lines.append("\n## 四、逐篇摘要\n")
    for i, article in enumerate(articles, 1):
        date = (article.get("pub_date") or "")[:10]
        lines.append(
            f"\n### {i}. {article.get('title') or '（无标题）'}"
            f" — {article.get('account', '')} {date}"
        )
        summary = article.get("summary") or "（无正文，仅有搜索摘要）"
        lines.append(f"> {summary}")
        if article.get("link"):
            lines.append(f"链接：{article['link']}")

    return "\n".join(lines)


def reading_time_minutes(word_count: int, cpm: int = 400) -> int:
    """Estimated reading time in whole minutes."""
    return max(1, math.ceil((word_count or 0) / cpm)) if word_count else 0
