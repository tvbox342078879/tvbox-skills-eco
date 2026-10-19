#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csdn_convert.py — 把任意 HTML / Markdown 文章转成「CSDN 可发布」的 Markdown 文件。

设计目标（与 wechat_convert.py 互补）：
  公众号靠「内联样式粘贴」；CSDN / 技术社区靠「Markdown 原生」。
  本脚本把文章归一为干净的 Markdown：
    1) 标题 / 小标题 / 段落 / 列表 / 表格 / 代码块 / 引用 全部转为标准 MD 语法；
    2) ⚠️ 图片策略：默认「纯文本、不嵌图」。
       CSDN 编辑器不认 data-URI 内嵌图（报「外链图片转存失败」防盗链），外链图被转存拦截，
       本地导出文件也需手动上传有摩擦。所以默认直接剥离所有 `![...](...)` 图片引用，
       只在显式传 `--keep-img` 时才把图导出为独立文件并保留引用（供愿意手动上传的用户用）。
    3) 自动剥离公众号预览 UI 铬层（.bar / #phone / button 等），避免把「复制全文」泄漏进技术文；
    4) 可选轻度中性化：去掉关注/在看/转发等硬广 CTA 词，降低被 CSDN 判「推广营销」的概率。

用法：
  python csdn_convert.py 输入.html [--title "标题"] [--outdir 目录] [--name 前缀]
  python csdn_convert.py 输入.md   # 自动把 Markdown 转 HTML 再处理
  --keep-img                      # 可选：保留图片（导出为独立文件，需手动上传）
依赖：beautifulsoup4, lxml（与 wechat_convert.py 同环境）。
复用：图片内嵌与 UI 剥离直接复用 wechat_convert.py 的 handle_images / strip_ui_chrome，
     行为一致、久经验证。
"""
import os
import re
import sys
import base64
import argparse

# Windows 控制台默认 gbk，强制 utf-8 以免打印 emoji 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from bs4 import BeautifulSoup, Comment, NavigableString
except ImportError:
    sys.exit("缺少 beautifulsoup4，请先 pip install beautifulsoup4 lxml")

# 复用同目录 wechat_convert 的成熟逻辑（图片内嵌 / UI 剥离 / md 转换）
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from wechat_convert import handle_images, strip_ui_chrome, md_to_html  # noqa: E402


# ---------------------------------------------------------------------------
# CSDN 中性化：去掉硬广 CTA 词（默认开启，降低被判「推广营销」的概率）
# ---------------------------------------------------------------------------
CTA_TOKENS = [
    "欢迎关注", "关注我们", "点个在看", "在看", "转发", "点赞", "收藏",
    "订阅", "私信", "扫码", "加微信", "限时免费", "免费送", "不容错过",
    "点击下方", "快来用", "速来", "获取技能",
]


def strip_cta_tokens(md):
    for t in CTA_TOKENS:
        md = md.replace(t, "")
    # 清理因删除产生的多余空白与标点
    md = re.sub(r"[，,]\s*[，,]", "，", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md


# ---------------------------------------------------------------------------
# HTML -> Markdown 序列化
# ---------------------------------------------------------------------------
BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote",
    "pre", "table", "hr", "figure", "div", "section", "img",
}

# 只有「容器类」块元素才从 inline_md 委派给 block_md；
# p / h1-h6 / li 等「叶块」应直接渲染子节点内联内容。
# 否则 block_md(p) 调 inline_md(p) 又委派回 block_md(p) 会无限递归（RecursionError）。
CONTAINER_BLOCKS = {"div", "section", "figure", "table", "ul", "ol", "pre", "blockquote"}


def _text_clean(s):
    return re.sub(r"[ \t]+", " ", s)


def render_img(node, ctx):
    src = node.get("src", "") or ""
    alt = node.get("alt", "") or node.get("title", "") or ""
    if not src:
        return ""
    return f"![{alt}]({src})"


# ---------------------------------------------------------------------------
# CSDN mermaid「炸弹」规避（CSDN v11.6.0 对节点/边标签里的特殊字符会 Parse error → 💣）
# 危险字符：() " ' + : /  ；唯独 <br/> 是安全换行，必须保留。
# ---------------------------------------------------------------------------
_MERMAID_DANGEROUS_REPL = [
    ("(", ""), (")", ""),
    ('"', ""), ("'", ""),
    ("+", "与"),
    (":", " "),
    ("/", "、"),
]


def _clean_mermaid_text(text):
    """清掉单个 mermaid 标签文本里的危险字符，但保留 <br/>。"""
    sentinel = "\x00BR\x00"
    text = text.replace("<br/>", sentinel)
    for ch, rep in _MERMAID_DANGEROUS_REPL:
        text = text.replace(ch, rep)
    text = text.replace(sentinel, "<br/>")
    text = re.sub(r" {2,}", " ", text).strip()  # 折叠替换产生的多余空格
    return text


def sanitize_mermaid(md):
    """扫描 ```mermaid 围栏块，清理其中节点/边标签的危险字符。非 mermaid 内容一律不动。"""
    def _fix_block(block):
        out = []
        for line in block.split("\n"):
            # 节点标签 [ ... ]，含圆柱 [( ... )]
            def repl_node(m):
                full = m.group(0)
                inner = m.group(1)
                if full.startswith("[(") and full.endswith(")]"):
                    inner = inner[1:-1] if (len(inner) >= 2 and inner[0] == "(" and inner[-1] == ")") else inner
                return "[" + _clean_mermaid_text(inner) + "]"
            line = re.sub(r"\[([^\]]*)\]", repl_node, line)
            # 决策节点 {}（菱形）标签同样危险，一并清理
            line = re.sub(r"\{([^{}]*)\}", lambda m: "{" + _clean_mermaid_text(m.group(1)) + "}", line)
            # 边标签 | ... |
            line = re.sub(r"\|([^|]*)\|", lambda m: "|" + _clean_mermaid_text(m.group(1)) + "|", line)
            out.append(line)
        return "\n".join(out)
    return re.sub(r"```mermaid\n(.*?)```", lambda m: "```mermaid\n" + _fix_block(m.group(1)) + "```", md, flags=re.S)


def inline_md(node, ctx):
    """内联级渲染：bold / italic / code / link / img / 换行。块级元素委派给 block_md。"""
    if node is None:
        return ""
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return _text_clean(str(node))
    name = node.name
    if name is None:
        return _text_clean(str(node))
    if name == "br":
        return "  \n"
    if name == "img":
        return render_img(node, ctx)
    if name in CONTAINER_BLOCKS:
        return block_md(node, ctx)
    inner = "".join(inline_md(c, ctx) for c in node.children)
    if name in ("strong", "b"):
        inner = inner.strip()
        return f"**{inner}**" if inner else ""
    if name in ("em", "i"):
        inner = inner.strip()
        return f"*{inner}*" if inner else ""
    if name == "code":
        return f"`{inner}`"
    if name == "a":
        href = node.get("href", "") or ""
        return f"[{inner}]({href})"
    # span / sub / sup / mark / u / small / font 等：保留内部文本
    return inner


def render_figure(node, ctx):
    img = node.find("img")
    cap = node.find(class_="cap") or node.find("figcaption")
    img_md = render_img(img, ctx) if img else ""
    cap_md = inline_md(cap, ctx).strip() if cap else ""
    res = "\n" + img_md
    if cap_md:
        res += "\n\n*" + cap_md + "*"
    return res + "\n"


def render_table(node, ctx):
    rows = node.find_all("tr")
    if not rows:
        return ""
    cells = []
    for tr in rows:
        tds = tr.find_all(["td", "th"])
        cells.append([inline_md(td, ctx).strip().replace("\n", " ") for td in tds])
    if not cells:
        return ""
    ncol = max(len(r) for r in cells)
    lines = []
    lines.append("| " + " | ".join(cells[0]) + " |")
    lines.append("| " + " | ".join("---" for _ in range(ncol)) + " |")
    for r in cells[1:]:
        while len(r) < ncol:
            r.append("")
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _block_inner(node, ctx):
    """把容器内部子节点按块/内联混合渲染。"""
    out = []
    for c in node.children:
        if isinstance(c, Comment):
            continue
        if isinstance(c, NavigableString):
            out.append(_text_clean(str(c)))
        elif c.name in BLOCK_TAGS:
            out.append(block_md(c, ctx))
        else:
            out.append(inline_md(c, ctx))
    return "".join(out)


def block_md(node, ctx):
    """块级渲染。"""
    name = node.name
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(name[1])
        text = inline_md(node, ctx).strip()
        return "\n" + ("#" * level) + " " + text + "\n"
    if name == "p":
        text = inline_md(node, ctx).strip()
        return ("\n" + text + "\n") if text else ""
    if name in ("ul", "ol"):
        items = node.find_all("li", recursive=False)
        lines = []
        for idx, li in enumerate(items, 1):
            it = inline_md(li, ctx).strip()
            bullet = "- " if name == "ul" else f"{idx}. "
            lines.append(bullet + it)
        return "\n" + "\n".join(lines) + "\n"
    if name == "blockquote":
        inner = _block_inner(node, ctx).strip()
        return "\n" + "\n".join("> " + l for l in inner.split("\n")) + "\n"
    if name == "hr":
        return "\n---\n"
    if name in ("div", "section"):
        cls = node.get("class", []) or []
        cls = cls if isinstance(cls, list) else [cls]
        inner = _block_inner(node, ctx).strip()
        if "callout" in cls:
            labeled = "\n".join(
                ("**💡 提示：** " + l) if i == 0 else l
                for i, l in enumerate(inner.split("\n"))
            )
            return "\n" + "\n".join("> " + l for l in labeled.split("\n")) + "\n"
        if "limit-box" in cls:
            labeled = "\n".join(
                ("**⚠️ 局限：** " + l) if i == 0 else l
                for i, l in enumerate(inner.split("\n"))
            )
            return "\n" + "\n".join("> " + l for l in labeled.split("\n")) + "\n"
        if "cta" in cls:
            # 中性化：直接丢弃纯 CTA 块
            return "" if ctx.get("strip_cta") else ("\n" + inner + "\n")
        return ("\n" + inner + "\n") if inner else ""
    if name == "figure":
        return render_figure(node, ctx)
    if name == "img":
        return "\n" + render_img(node, ctx) + "\n"
    if name == "pre":
        code = node.get_text()
        lang = ""
        c = node.find("code")
        if c and c.get("class"):
            for cl in (c["class"] if isinstance(c["class"], list) else [c["class"]]):
                if isinstance(cl, str) and cl.startswith("language-"):
                    lang = cl[len("language-"):]
        return "\n```" + lang + "\n" + code.rstrip() + "\n```\n"
    if name == "table":
        return "\n" + render_table(node, ctx) + "\n"
    if name == "br":
        return "  \n"
    # 未知块容器：递归渲染子节点
    return ("\n" + _block_inner(node, ctx).strip() + "\n")


def serialize(root, ctx):
    body = root.body if root.body else root
    parts = []
    for c in body.children:
        if isinstance(c, Comment):
            continue
        if isinstance(c, NavigableString):
            continue
        if c.name in BLOCK_TAGS:
            parts.append(block_md(c, ctx))
        else:
            parts.append(inline_md(c, ctx))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 将 MD 中的 data-URI 内嵌图导出为独立文件（CSDN 不认 base64 内嵌）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 将 MD 中的 data-URI 内嵌图导出为独立文件（CSDN 不认 base64 内嵌）
# ---------------------------------------------------------------------------
def export_data_uri_images(md, outdir, prefix="img"):
    """扫描 MD 中 ![alt](data:image/...;base64,...) 引用，导出为独立图片文件。

    返回 (new_md, exported_paths)：
      new_md        —— data-URI 已替换为本地文件路径的 Markdown 文本
      exported_paths —— 导出的文件绝对路径列表（供调用方打印提示）
    """
    pattern = re.compile(
        r'!\[([^\]]*)\]\((data:image/([^;]+);base64,([^)]+))\)'
    )
    exported = []
    idx = 0

    def _replace(m):
        nonlocal idx
        idx += 1
        alt = m.group(1)
        mime_full = m.group(2)   # data:image/jpeg;base64,...
        ext = m.group(3)          # jpeg / png / webp / gif ...
        b64 = m.group(4)
        # 规范扩展名
        ext_map = {"jpeg": "jpg", "svg": "png"}
        file_ext = ext_map.get(ext, ext)
        fname = f"{prefix}-{idx:02d}.{file_ext}"
        fpath = os.path.join(outdir, fname)
        # 解码写入文件
        try:
            img_bytes = base64.b64decode(b64)
            with open(fpath, "wb") as f:
                f.write(img_bytes)
            exported.append(os.path.abspath(fpath))
        except Exception:
            # 解码失败时保留原 data-URI（不丢图）
            return m.group(0)
        return f"![{alt}]({fname})"

    new_md = pattern.sub(_replace, md)
    return new_md, exported


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="输入 .html 或 .md 文件")
    ap.add_argument("--title", default="", help="文章标题（无 h1 时前置到 MD 顶部）")
    ap.add_argument("--outdir", default="", help="输出目录，默认与输入同目录")
    ap.add_argument("--name", default="",
                    help="输出文件前缀，默认取输入文件名（不含扩展名）。"
                         "用于区分不同推文避免互相覆盖，如 --name epub-to-txt")
    ap.add_argument("--keep-img", action="store_true",
                    help="保留图片（默认会剥离所有图片做纯文本）。开启后会把 data-URI 图"
                         "导出为独立 jpg/png 文件并保留 MD 引用。"
                         "注意：CSDN 编辑器不认内嵌图，保留的图需你手动上传。")
    ap.add_argument("--keep-cta", action="store_true",
                    help="保留关注/在看/转发等 CTA 词（默认会轻度去除以降低 CSDN 判推广风险）")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"输入文件不存在：{args.input}")
    base_dir = os.path.dirname(os.path.abspath(args.input))
    raw = open(args.input, "r", encoding="utf-8", errors="ignore").read()

    warnings = []
    stats = {"dropped": 0, "embedded": 0}
    ext = os.path.splitext(args.input)[1].lower()
    if ext == ".md":
        soup = BeautifulSoup(md_to_html(raw), "lxml")
        warnings.append("输入为 Markdown，已转为 HTML")
    else:
        if not re.search(r"<\s*(html|body)[\s>]", raw, re.I):
            raw = "<html><head></head><body>" + raw + "</body></html>"
        soup = BeautifulSoup(raw, "lxml")

    # 剥离公众号预览 UI 铬层（输入可能是旧版 _preview.html）
    strip_ui_chrome(soup)
    root = soup.body if soup.body else soup

    # 图片本地化（复用 wechat_convert 的成熟逻辑）
    # keep-img 时 embed（转 data-URI 以便后续导出文件）；默认 keep 原 src 即可（反正要剥离）。
    img_mode = "embed" if args.keep_img else "keep"
    handle_images(root, base_dir, img_mode, warnings, stats)

    ctx = {"strip_cta": not args.keep_cta}
    md = serialize(root, ctx)

    # 标题：若正文无 h1 且指定了 --title，前置
    if args.title and not re.match(r"^\s*#\s", md):
        md = f"# {args.title}\n\n" + md

    if ctx["strip_cta"]:
        md = strip_cta_tokens(md)

    outdir = args.outdir or base_dir
    os.makedirs(outdir, exist_ok=True)

    stem = os.path.splitext(os.path.basename(args.input))[0]
    prefix = args.name or stem

    # ★ CSDN 默认「纯文本、不嵌图」策略（用户铁律：CSDN 不认内嵌图、外链被拦、导出需手动上传）。
    #   仅 --keep-img 才保留图片（导出文件）；否则直接剥离所有图片引用。
    #   ⚠️ 任何情况都不追加文末「运行截图」占位区（用户明确不要）。
    exported_imgs = []
    if args.keep_img:
        if "data:image" in md:
            md, exported_imgs = export_data_uri_images(md, outdir, prefix + "-img")
    else:
        md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)

    # ★ CSDN mermaid「炸弹」规避：清掉节点/边标签里的 () " ' + : /（保留 <br/>）。
    #   手写的脏 mermaid 经此一步自动净化，粘进 CSDN 不再显示💣。
    md = sanitize_mermaid(md)

    # 后处理：折叠多余空行、去首尾空白
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"

    out_path = os.path.join(outdir, f"{prefix}_csdn.md")
    open(out_path, "w", encoding="utf-8").write(md)

    print("✅ CSDN Markdown 生成完成")
    print(f"  · 输出文件     : {out_path}")
    if args.keep_img:
        print(f"  · 导出图片数   : {len(exported_imgs)}")
        for p in exported_imgs:
            print(f"      - {p}")
    else:
        print("  · 图片         : 已剥离（CSDN 纯文本策略，不内嵌/不上传图）")
    print(f"  · CTA 中性化   : {'否' if args.keep_cta else '是（已去除硬广词）'}")
    if warnings:
        print("  · 提示：")
        for w in warnings:
            print(f"      - {w}")


if __name__ == "__main__":
    main()
