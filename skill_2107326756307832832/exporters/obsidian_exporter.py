"""Export articles to Obsidian vault."""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import sanitize_filename, get_logger, resolve_state_dir


logger = get_logger("obsidian-exporter")


class ObsidianExporter:
    """Export articles as Obsidian-compatible Markdown notes."""

    def __init__(self, vault_path: Optional[str] = None):
        self.vault_path = vault_path

    def export_articles(
        self,
        articles: List[Dict[str, Any]],
        folder: str = "微信公众号",
        use_frontmatter: bool = True,
        create_moc: bool = True,
        moc_name: str = "微信文章索引",
        tag_prefix: str = "wechat",
        group_by_account: bool = False,
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        """Export articles as Obsidian Markdown notes.

        Args:
            articles: List of article dicts.
            folder: Subfolder within vault (or output dir).
            use_frontmatter: Whether to add YAML frontmatter.
            create_moc: Whether to create a Map of Content (MOC) note.
            moc_name: Name of the MOC note.
            tag_prefix: Prefix for auto-generated tags.
            group_by_account: Nest each account in its own subfolder.
            overwrite: Rewrite an existing note for the same article instead of
                piling up `title_1.md`, `title_2.md` duplicates.

        Returns:
            Result dict with paths and stats.
        """
        output_dir = self.vault_path or str(resolve_state_dir() / "obsidian_export")
        target_dir = os.path.join(output_dir, folder)
        os.makedirs(target_dir, exist_ok=True)

        created_files = []
        note_names: Dict[str, str] = {}
        for article in articles:
            dir_for_article = target_dir
            if group_by_account and article.get("account"):
                dir_for_article = os.path.join(
                    target_dir, sanitize_filename(article["account"], max_len=40)
                )
                os.makedirs(dir_for_article, exist_ok=True)
            path = self._write_note(
                article, dir_for_article, use_frontmatter, tag_prefix, overwrite
            )
            if path:
                created_files.append(path)
                note_names[article.get("title", "")] = os.path.splitext(
                    os.path.basename(path)
                )[0]

        # Create MOC
        if create_moc and created_files:
            moc_path = self._write_moc(articles, target_dir, moc_name, note_names)
            if moc_path:
                created_files.append(moc_path)

        logger.info(f"已导出 {len(created_files)} 个文件到 {target_dir}")
        return {
            "success": len(articles),
            "files_created": len(created_files),
            "output_dir": target_dir,
            "files": created_files,
        }

    def _write_note(
        self,
        article: Dict[str, Any],
        target_dir: str,
        use_frontmatter: bool,
        tag_prefix: str,
        overwrite: bool = True,
    ) -> Optional[str]:
        """Write a single article as an Obsidian note."""
        title = article.get("title", "未知标题")
        safe_title = sanitize_filename(title, max_len=60)
        date_prefix = (str(article.get("pub_date") or "")[:10]).replace("-", "")
        name = f"{date_prefix}_{safe_title}.md" if date_prefix else f"{safe_title}.md"
        path = os.path.join(target_dir, name)

        # Re-exporting the same article rewrites its note; a *different* article
        # that hashes to the same name gets a numeric suffix instead.
        if os.path.exists(path) and not self._same_article(path, article.get("link", "")):
            counter = 1
            base = path[:-3]
            while os.path.exists(path) and not self._same_article(path, article.get("link", "")):
                path = f"{base}_{counter}.md"
                counter += 1
        elif os.path.exists(path) and not overwrite:
            return path

        lines = []

        # Frontmatter
        if use_frontmatter:
            lines.append("---")
            escaped_title = title.replace('"', '\\"')
            lines.append(f'title: "{escaped_title}"')
            if article.get("account"):
                lines.append(f"account: \"{article['account']}\"")
            if article.get("author"):
                lines.append(f"author: \"{article['author']}\"")
            if article.get("pub_date"):
                lines.append(f"date: {article['pub_date']}")
            if article.get("link"):
                lines.append(f"source: \"{article['link']}\"")
            if article.get("word_count"):
                lines.append(f"word_count: {article['word_count']}")

            # Auto tags
            tags = [f"{tag_prefix}/article"]
            account = article.get("account", "")
            if account:
                account_tag = re.sub(r'[^\w\u4e00-\u9fff]', '', account)
                tags.append(f"{tag_prefix}/{account_tag}")
            lines.append(f"tags: {json.dumps(tags, ensure_ascii=False)}")
            lines.append("---")
            lines.append("")

        # Title
        lines.append(f"# {title}\n")

        # Metadata block
        meta_parts = []
        if article.get("account"):
            meta_parts.append(f"**公众号**: [[{article['account']}]]")
        if article.get("author"):
            meta_parts.append(f"**作者**: {article['author']}")
        if article.get("pub_date"):
            meta_parts.append(f"**日期**: {article['pub_date']}")
        if article.get("link"):
            meta_parts.append(f"**链接**: [{article['link']}]({article['link']})")
        if meta_parts:
            lines.append(" | ".join(meta_parts))
            lines.append("\n---\n")

        # Content
        content = article.get("content_markdown") or article.get("content_text", "")
        if content:
            lines.append(content)
        else:
            snippet = article.get("snippet", "")
            if snippet:
                lines.append(f"> {snippet}\n\n*(仅获取到摘要)*")

        # Images as Obsidian embeds
        images = article.get("images", [])
        if images:
            lines.append("\n\n## 图片\n")
            for i, img in enumerate(images, 1):
                alt = img.get("alt", f"图片{i}")
                src = img.get("src", "")
                lines.append(f"![{alt}]({src})\n")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return path

    @staticmethod
    def _same_article(path: str, link: str) -> bool:
        """True when an existing note's `source:` frontmatter is this article.

        Lets a repeated export update notes in place instead of piling up
        `xxx_1.md`, `xxx_2.md` copies.
        """
        if not link:
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                head = f.read(2000)
        except OSError:
            return False
        return link in head

    def _write_moc(
        self,
        articles: List[Dict[str, Any]],
        target_dir: str,
        moc_name: str,
        note_names: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Write a Map of Content (MOC) note linking all articles."""
        path = os.path.join(target_dir, f"{sanitize_filename(moc_name)}.md")
        note_names = note_names or {}

        lines = [
            "---",
            f"title: {moc_name}",
            "type: moc",
            f"created: {datetime.now().isoformat()}",
            "---",
            "",
            f"# {moc_name}\n",
            f"共 {len(articles)} 篇文章\n",
        ]

        # Group by account
        by_account: Dict[str, List] = {}
        for article in articles:
            account = article.get("account", "未知公众号")
            if account not in by_account:
                by_account[account] = []
            by_account[account].append(article)

        for account, ac_articles in sorted(by_account.items()):
            lines.append(f"\n## {account}\n")
            for article in ac_articles:
                title = article.get("title", "未知标题")
                # Link the file we actually wrote, not a re-derived name.
                note = note_names.get(title) or sanitize_filename(title, max_len=60)
                date = article.get("pub_date", "")
                words = article.get("word_count", 0)
                lines.append(f"- [[{note}]] — {date} | {words}字")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return path