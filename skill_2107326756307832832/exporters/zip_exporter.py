"""Export articles to ZIP archive with multiple format support."""

import html as html_module
import json
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import sanitize_filename, get_logger, file_size_str, resolve_export_dir


logger = get_logger("zip-exporter")


def _esc(value: Any) -> str:
    """Escape untrusted page text before embedding it in generated HTML."""
    return html_module.escape(str(value or ""), quote=True)


class ZipExporter:
    """Export articles to a ZIP archive."""

    def __init__(self, output_dir: Optional[str] = None, config: Optional[Dict] = None):
        # ZIP is binary; default to the temp dir so it never sits in the skill
        # folder (WECHAT_SCRAPER_EXPORT_DIR or config export.default_output_dir
        # override).
        self.output_dir = resolve_export_dir(output_dir, config)

    def export(
        self,
        articles: List[Dict[str, Any]],
        filename: Optional[str] = None,
        formats: Optional[List[str]] = None,
        include_images: bool = False,
        include_index: bool = True,
        include_metadata: bool = True,
    ) -> str:
        """Export articles to a ZIP file.

        Args:
            articles: List of article dicts.
            filename: Output ZIP filename (without extension).
            formats: Output formats: 'markdown', 'html', 'text', 'json'. Default: all.
            include_images: Whether to include images (requires image download).
            include_index: Whether to include an index/TOC file.
            include_metadata: Whether to include metadata JSON.

        Returns:
            Path to the created ZIP file.
        """
        if not articles:
            raise ValueError("没有文章可导出")

        formats = formats or ["markdown", "text", "json"]

        # Generate filename
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            first_account = articles[0].get("account", "unknown")
            filename = sanitize_filename(f"wechat_{first_account}_{timestamp}")

        zip_path = self.output_dir / f"{filename}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add articles in each format
            for i, article in enumerate(articles, 1):
                title = sanitize_filename(article.get("title", f"article_{i}"))
                prefix = f"{i:03d}_{title}"

                if "markdown" in formats:
                    md = self._to_markdown(article)
                    zf.writestr(f"markdown/{prefix}.md", md)

                if "text" in formats:
                    txt = self._to_text(article)
                    zf.writestr(f"text/{prefix}.txt", txt)

                if "html" in formats:
                    html = self._to_html(article)
                    zf.writestr(f"html/{prefix}.html", html)

                if "json" in formats:
                    data = self._to_json(article)
                    zf.writestr(f"json/{prefix}.json", data)

            # Index / TOC
            if include_index:
                index_content = self._generate_index(articles)
                zf.writestr("INDEX.md", index_content)

            # Metadata
            if include_metadata:
                meta = self._generate_metadata(articles)
                zf.writestr("metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))

            # Summary
            summary = self._generate_summary(articles)
            zf.writestr("SUMMARY.md", summary)

        size = file_size_str(zip_path.stat().st_size)
        logger.info(f"已导出 {len(articles)} 篇文章到 {zip_path.name} ({size})")
        return str(zip_path)

    def _to_markdown(self, article: Dict[str, Any]) -> str:
        """Convert article to Markdown."""
        lines = []
        title = article.get("title", "未知标题")
        lines.append(f"# {title}\n")

        # Metadata header
        meta = []
        if article.get("account"):
            meta.append(f"**公众号**: {article['account']}")
        if article.get("author"):
            meta.append(f"**作者**: {article['author']}")
        if article.get("pub_date"):
            meta.append(f"**发布时间**: {article['pub_date']}")
        if article.get("link"):
            meta.append(f"**原文链接**: {article['link']}")
        if article.get("word_count"):
            meta.append(f"**字数**: {article['word_count']}")
        if meta:
            lines.append("\n".join(meta))
            lines.append("\n---\n")

        # Content
        content = article.get("content_markdown") or article.get("content_text", "")
        if content:
            lines.append(content)
        else:
            snippet = article.get("snippet", "")
            if snippet:
                lines.append(f"> {snippet}\n\n*(仅获取到摘要，完整内容请访问原文链接)*")

        # Images
        images = article.get("images", [])
        if images:
            lines.append("\n\n---\n## 文章图片\n")
            for i, img in enumerate(images, 1):
                alt = img.get("alt", f"图片 {i}")
                src = img.get("src", "")
                lines.append(f"![{alt}]({src})\n")

        return "\n".join(lines)

    def _to_text(self, article: Dict[str, Any]) -> str:
        """Convert article to plain text."""
        lines = []
        title = article.get("title", "未知标题")
        lines.append(title)
        lines.append("=" * len(title.encode("gbk", errors="replace")) + "\n")

        if article.get("account"):
            lines.append(f"公众号: {article['account']}")
        if article.get("author"):
            lines.append(f"作者: {article['author']}")
        if article.get("pub_date"):
            lines.append(f"发布时间: {article['pub_date']}")
        if article.get("link"):
            lines.append(f"原文链接: {article['link']}")
        lines.append("")

        content = article.get("content_text", "")
        if content:
            lines.append(content)
        else:
            snippet = article.get("snippet", "")
            if snippet:
                lines.append(f"[摘要] {snippet}")
                lines.append("\n(仅获取到摘要，完整内容请访问原文链接)")

        return "\n".join(lines)

    def _to_html(self, article: Dict[str, Any]) -> str:
        """Convert article to standalone HTML."""
        title = _esc(article.get("title", "未知标题"))
        account = _esc(article.get("account", ""))
        author = _esc(article.get("author", ""))
        pub_date = _esc(article.get("pub_date", ""))
        link = _esc(article.get("link", ""))
        content = article.get("content_markdown") or article.get("content_text", "")

        # Simple markdown to HTML (basic conversion)
        import re
        content_html = content
        content_html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', content_html, flags=re.MULTILINE)
        content_html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', content_html, flags=re.MULTILINE)
        content_html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', content_html, flags=re.MULTILINE)
        content_html = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)',
                              lambda m: f'<img alt="{_esc(m.group(1))}" src="{_esc(m.group(2))}">',
                              content_html)
        content_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content_html)
        content_html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', content_html)
        content_html = re.sub(r'\[(.+?)\]\((.+?)\)',
                              lambda m: f'<a href="{_esc(m.group(2))}">{m.group(1)}</a>',
                              content_html)
        content_html = content_html.replace("\n\n", "</p><p>")
        content_html = f"<p>{content_html}</p>"

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.8; color: #333; }}
h1 {{ font-size: 1.8em; border-bottom: 2px solid #1a73e8; padding-bottom: 10px; }}
.meta {{ color: #666; font-size: 0.9em; margin-bottom: 20px; }}
.meta span {{ margin-right: 15px; }}
blockquote {{ border-left: 3px solid #ddd; margin: 10px 0; padding: 5px 15px; color: #666; }}
img {{ max-width: 100%; height: auto; }}
a {{ color: #1a73e8; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">
  {f'<span>公众号: {account}</span>' if account else ''}
  {f'<span>作者: {author}</span>' if author else ''}
  {f'<span>发布时间: {pub_date}</span>' if pub_date else ''}
  {f'<span><a href="{link}">原文链接</a></span>' if link else ''}
</div>
<hr>
<div class="article-content">
{content_html}
</div>
</body>
</html>"""

    def _to_json(self, article: Dict[str, Any]) -> str:
        """Convert article to JSON."""
        # Remove internal fields
        clean = {k: v for k, v in article.items() if not k.startswith("_")}
        return json.dumps(clean, ensure_ascii=False, indent=2)

    def _generate_index(self, articles: List[Dict[str, Any]]) -> str:
        """Generate a Markdown index/TOC."""
        lines = ["# 微信公众号文章索引\n"]
        lines.append(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append(f"文章总数: {len(articles)} 篇\n")
        lines.append("\n## 文章列表\n")

        accounts = {}
        for i, article in enumerate(articles, 1):
            title = article.get("title", "未知标题")
            account = article.get("account", "未知公众号")
            pub_date = article.get("pub_date", "未知日期")
            words = article.get("word_count", 0)

            lines.append(f"{i}. **{title}** — {account} | {pub_date} | {words}字")

            if account not in accounts:
                accounts[account] = 0
            accounts[account] += 1

        if len(accounts) > 1:
            lines.append("\n## 公众号统计\n")
            for account, count in sorted(accounts.items(), key=lambda x: -x[1]):
                lines.append(f"- {account}: {count} 篇")

        return "\n".join(lines)

    def _generate_summary(self, articles: List[Dict[str, Any]]) -> str:
        """Generate a summary document."""
        total_words = sum(a.get("word_count", 0) for a in articles)
        accounts = set(a.get("account", "") for a in articles if a.get("account"))
        dates = [a.get("pub_date", "") for a in articles if a.get("pub_date")]

        lines = [
            "# 导出摘要\n",
            f"- **文章总数**: {len(articles)} 篇",
            f"- **总字数**: {total_words:,} 字",
            f"- **公众号数量**: {len(accounts)} 个",
        ]

        if dates:
            dates_sorted = sorted(dates)
            lines.append(f"- **时间范围**: {dates_sorted[0]} ~ {dates_sorted[-1]}")

        return "\n".join(lines)

    def _generate_metadata(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate export metadata."""
        return {
            "export_time": datetime.now().isoformat(),
            "article_count": len(articles),
            "total_words": sum(a.get("word_count", 0) for a in articles),
            "accounts": list(set(a.get("account", "") for a in articles if a.get("account"))),
            "date_range": {
                "earliest": min((a.get("pub_date", "") for a in articles if a.get("pub_date")), default=""),
                "latest": max((a.get("pub_date", "") for a in articles if a.get("pub_date")), default=""),
            },
        }