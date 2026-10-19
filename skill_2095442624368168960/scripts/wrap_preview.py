#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预览页生成器 —— gzh-typeset

把排版好的 HTML 片段套进 templates/preview-template.html，
产出带「手机 / 平板 / 桌面」三档切换 + 一键复制到公众号的本地预览页。

用法：
    python scripts/wrap_preview.py <file.html> [标题]
    python scripts/wrap_preview.py <file.html> -t "标题" -o "自定义输出路径"

输出：默认在源文件同目录生成 <原名>_预览.html
"""

import argparse
import html as html_mod
import os
import re
import sys

TEMPLATE_NAME = "preview-template.html"
PLACEHOLDER_TITLE = "{{TITLE}}"
PLACEHOLDER_CONTENT = "<!--GZH_CONTENT-->"


def resolve_template(explicit=None):
    """按 显式参数 → 脚本同级 templates/ → skill 根目录 templates/ 顺序找模板。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, TEMPLATE_NAME))
    candidates.append(os.path.join(os.path.dirname(here), "templates", TEMPLATE_NAME))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def guess_title(article_html, fallback):
    """从正文里猜一个标题：优先取第一个较大字号(<p>/<section>)里的文字。"""
    m = re.search(r'font-size:\s*(2[0-9]|3[0-9])px[^>]*>\s*<span leaf="">([^<]{4,60})</span>',
                  article_html)
    if m:
        return m.group(2).strip()
    m = re.search(r'<span leaf="">([^<]{6,60})</span>', article_html)
    if m:
        return m.group(1).strip()
    return fallback


def main():
    ap = argparse.ArgumentParser(description="生成公众号排版预览页")
    ap.add_argument("file", help="排版好的 HTML 片段文件")
    ap.add_argument("title_pos", nargs="?", default=None, help="文章标题（可选，位置参数）")
    ap.add_argument("-t", "--title", default=None, help="文章标题")
    ap.add_argument("-o", "--output", default=None, help="输出路径")
    ap.add_argument("--template", default=None, help="自定义模板路径")
    args = ap.parse_args()

    src = args.file
    if not os.path.isfile(src):
        print("找不到源文件：%s" % src)
        return 2

    tpl_path = resolve_template(args.template)
    if not tpl_path:
        print("找不到模板 %s，请确认 templates/ 目录完整" % TEMPLATE_NAME)
        return 2

    with open(src, "r", encoding="utf-8") as f:
        article = f.read()
    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    base = os.path.splitext(os.path.basename(src))[0]
    title = args.title or args.title_pos or guess_title(article, base)

    if PLACEHOLDER_CONTENT not in tpl:
        print("模板缺少占位符 %s" % PLACEHOLDER_CONTENT)
        return 2

    out = tpl.replace(PLACEHOLDER_TITLE, html_mod.escape(title))
    out = out.replace(PLACEHOLDER_CONTENT, article)

    if args.output:
        dst = args.output
    else:
        dst = os.path.join(os.path.dirname(os.path.abspath(src)), base + "_预览.html")

    with open(dst, "w", encoding="utf-8") as f:
        f.write(out)

    size_kb = os.path.getsize(dst) / 1024.0
    print("✅ 预览页已生成：%s" % dst)
    print("   标题：%s" % title)
    print("   大小：%.1f KB" % size_kb)
    print("")
    print("下一步：在浏览器打开该页面 → 切换「手机 / 平板 / 桌面」确认多屏效果")
    print("        → 点右上角「一键复制到公众号」→ 到公众号编辑器 Ctrl+V")
    return 0


if __name__ == "__main__":
    sys.exit(main())
