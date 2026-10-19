"""Export articles as a plain folder of Markdown notes plus a CSV index.

ZIP is convenient but binary; this exporter produces text only, so results can
be committed, reviewed, or uploaded to SkillHub without tripping the
binary-file rule.
"""

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import sanitize_filename, ensure_dir, get_logger, resolve_export_dir

logger = get_logger("markdown-exporter")


class MarkdownExporter:
    """Write articles to a directory tree of .md files + index.csv."""

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir) if output_dir else resolve_export_dir()

    def export(
        self,
        articles: List[Dict[str, Any]],
        folder: Optional[str] = None,
        group_by_account: bool = True,
        include_index: bool = True,
        include_summary: bool = True,
    ) -> Dict[str, Any]:
        """Write articles; returns the created paths and counts.

        Args:
            articles: Article dicts from search/fetch.
            folder: Output subfolder name. Defaults to a timestamped name.
            group_by_account: Put each account's articles in its own subfolder.
            include_index: Write index.csv listing every article.
            include_summary: Write 摘要.md with batch statistics.
        """
        if not articles:
            raise ValueError("没有文章可导出")

        if not folder:
            folder = "wechat_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        root = ensure_dir(self.output_dir / sanitize_filename(folder, max_len=60))

        files: List[str] = []
        used_names: set = set()
        for i, article in enumerate(articles, 1):
            target = root
            if group_by_account and article.get("account"):
                target = ensure_dir(
                    root / sanitize_filename(article["account"], max_len=40)
                )
            path = self._unique_path(target, article, i, used_names)
            path.write_text(self._to_markdown(article), encoding="utf-8")
            files.append(str(path))

        index_path = None
        if include_index:
            index_path = self._write_index(root, articles, files)

        summary_path = None
        if include_summary:
            summary_path = self._write_summary(root, articles)

        logger.info(f"已导出 {len(files)} 篇文章到 {root}")
        return {
            "output_dir": str(root),
            "article_count": len(files),
            "files": files,
            "index_csv": index_path,
            "summary_md": summary_path,
        }

    # -- helpers --

    @staticmethod
    def _unique_path(
        target: Path,
        article: Dict[str, Any],
        index: int,
        used: set,
    ) -> Path:
        title = sanitize_filename(article.get("title") or f"article_{index}", max_len=60)
        name = f"{index:03d}_{title}.md"
        suffix = 1
        while name in used:
            name = f"{index:03d}_{title}_{suffix}.md"
            suffix += 1
        used.add(name)
        return target / name

    @staticmethod
    def _to_markdown(article: Dict[str, Any]) -> str:
        title = article.get("title", "未知标题")
        lines = [f"# {title}", ""]

        meta = []
        if article.get("account"):
            meta.append(f"公众号：{article['account']}")
        if article.get("author"):
            meta.append(f"作者：{article['author']}")
        if article.get("pub_date"):
            meta.append(f"发布时间：{article['pub_date']}")
        if article.get("word_count"):
            meta.append(f"字数：{article['word_count']}")
        if meta:
            lines.append(" · ".join(meta))
            lines.append("")
        if article.get("link"):
            lines.append(f"原文链接：{article['link']}")
            lines.append("")
        if article.get("summary"):
            lines.append(f"> {article['summary']}")
            lines.append("")

        lines.append("---")
        lines.append("")

        content = article.get("content_markdown") or article.get("content_text", "")
        if content:
            lines.append(content)
        elif article.get("snippet"):
            lines.append(f"> {article['snippet']}")
            lines.append("")
            lines.append("*（仅获取到搜索摘要，完整内容请访问原文链接）*")
        else:
            lines.append("*（未获取到正文内容）*")

        images = article.get("images") or []
        if images:
            lines.append("\n\n## 图片\n")
            for i, image in enumerate(images, 1):
                src = image.get("src", "") if isinstance(image, dict) else str(image)
                alt = image.get("alt", f"图片{i}") if isinstance(image, dict) else f"图片{i}"
                lines.append(f"![{alt}]({src})")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _write_index(root: Path, articles: List[Dict[str, Any]], files: List[str]) -> str:
        path = root / "index.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["序号", "标题", "公众号", "作者", "发布时间", "字数", "原文链接", "本地文件"])
            for i, (article, file_name) in enumerate(zip(articles, files), 1):
                writer.writerow([
                    i,
                    article.get("title", ""),
                    article.get("account", ""),
                    article.get("author", ""),
                    article.get("pub_date", ""),
                    article.get("word_count", 0),
                    article.get("link", ""),
                    os.path.relpath(file_name, root),
                ])
        return str(path)

    @staticmethod
    def _write_summary(root: Path, articles: List[Dict[str, Any]]) -> str:
        from core.summarizer import article_stats, extract_keywords

        stats = article_stats(articles)
        keywords = extract_keywords(articles, top_n=10)
        lines = [
            "# 导出摘要",
            "",
            f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 文章数量：{stats['count']} 篇",
            f"- 全文成功：{stats['with_full_text']} 篇 · 失败 {stats['failed']} 篇",
            f"- 总字数：{stats['total_words']:,} 字",
            f"- 公众号：{stats['account_count']} 个",
        ]
        if stats["date_range"]["earliest"]:
            lines.append(
                f"- 时间跨度：{stats['date_range']['earliest']} ~ {stats['date_range']['latest']}"
            )
        if keywords:
            lines.append("")
            lines.append("## 高频关键词")
            lines.append("、".join(f"{t}({c})" for t, c in keywords))
        lines.append("")
        lines.append("## 公众号分布")
        for account, count in list(stats["accounts"].items())[:15]:
            lines.append(f"- {account}：{count} 篇")

        path = root / "摘要.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)
