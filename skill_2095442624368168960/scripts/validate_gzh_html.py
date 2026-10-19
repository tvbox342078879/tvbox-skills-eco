#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公众号 HTML 红线校验器 —— gzh-typeset

用途：在交付前机检 HTML 是否满足微信公众号编辑器的兼容红线，以及多屏自适应要求。
用法：
    python scripts/validate_gzh_html.py <file.html> [--quiet] [--no-warn-as-error]

退出码：
    0  —— 无 ERROR（WARNING 视情况可交付）
    1  —— 存在 ERROR，禁止交付
"""

import argparse
import re
import sys
from html.parser import HTMLParser

# ---------------------------------------------------------------- 规则表

VOID_TAGS = {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}

# 禁用标签：(等级, 说明)
FORBIDDEN_TAGS = {
    "style":     ("ERROR", "微信会整段剥离 <style>，样式必须全部内联"),
    "script":    ("ERROR", "<script> 会被剥离且可能触发安全拦截"),
    "div":       ("ERROR", "<div> 会被转换或丢失，统一改用 <section>"),
    "link":      ("ERROR", "<link> 外链资源会被剥离"),
    "iframe":    ("ERROR", "<iframe> 不支持"),
    "svg":       ("ERROR", "<svg> 不支持，改用图片"),
    "video":     ("ERROR", "<video> 不支持，视频走公众号素材插入"),
    "audio":     ("ERROR", "<audio> 不支持"),
    "canvas":    ("ERROR", "<canvas> 不支持"),
    "table":     ("ERROR", "<table> 在微信内宽度失控，改用 flex 仿表格（common-components G04）"),
    "thead":     ("ERROR", "表格系列标签不支持，改用 flex 仿表格"),
    "tbody":     ("ERROR", "表格系列标签不支持，改用 flex 仿表格"),
    "tr":        ("ERROR", "表格系列标签不支持，改用 flex 仿表格"),
    "td":        ("ERROR", "表格系列标签不支持，改用 flex 仿表格"),
    "th":        ("ERROR", "表格系列标签不支持，改用 flex 仿表格"),
    "form":      ("ERROR", "<form> 不支持"),
    "input":     ("ERROR", "表单控件不支持"),
    "button":    ("ERROR", "表单控件不支持"),
    "select":    ("ERROR", "表单控件不支持"),
    "textarea":  ("ERROR", "表单控件不支持"),
    "object":    ("ERROR", "<object> 不支持"),
    "embed":     ("ERROR", "<embed> 不支持"),
    "marquee":   ("ERROR", "<marquee> 不支持"),
    "nav":       ("WARN",  "语义标签在微信内支持不稳定，建议 <section>"),
    "header":    ("WARN",  "语义标签在微信内支持不稳定，建议 <section>"),
    "footer":    ("WARN",  "语义标签在微信内支持不稳定，建议 <section>"),
    "article":   ("WARN",  "语义标签在微信内支持不稳定，建议 <section>"),
    "aside":     ("WARN",  "语义标签在微信内支持不稳定，建议 <section>"),
    "main":      ("WARN",  "语义标签在微信内支持不稳定，建议 <section>"),
}

# 禁用属性
FORBIDDEN_ATTRS = {
    "class": ("ERROR", "class 会被剥离或与其他文章冲突，全部禁止"),
    "id":    ("ERROR", "id 会被剥离或与其他文章冲突，全部禁止"),
}

EVENT_ATTR_RE = re.compile(r"^on[a-z]+$", re.I)

# 禁用 CSS：(正则, 等级, 说明)
FORBIDDEN_CSS = [
    (re.compile(r"position\s*:\s*(fixed|absolute|sticky)", re.I),
     "ERROR", "position 定位在公众号内全部失效，改用 flex 顺序 + margin"),
    (re.compile(r"float\s*:\s*(left|right|none)", re.I),
     "ERROR", "float 会被剥离导致图文串行，改用 display:flex 横排"),
    (re.compile(r"@media", re.I),
     "ERROR", "@media 在内联 style 中不生效，改用百分比 + max-width + flex-wrap"),
    (re.compile(r"@keyframes|animation\s*:", re.I),
     "ERROR", "动画不支持，静态呈现"),
    (re.compile(r"@import", re.I),
     "ERROR", "@import 会被剥离"),
    (re.compile(r"display\s*:\s*grid", re.I),
     "ERROR", "display:grid 在微信内核支持不稳定，改用 display:flex"),
    (re.compile(r"var\s*\(\s*--", re.I),
     "ERROR", "CSS 变量不支持，直接写死色值"),
    (re.compile(r"!important", re.I),
     "ERROR", "!important 会被编辑器当异常清洗"),
    (re.compile(r"white-space\s*:\s*pre", re.I),
     "WARN",  "white-space:pre 在微信内不可靠，代码缩进改用逐行 <p> + &nbsp;"),
    (re.compile(r"linear-gradient|radial-gradient", re.I),
     "WARN",  "渐变在微信内渲染不稳定，改用纯色"),
    (re.compile(r"box-shadow", re.I),
     "INFO",  "阴影在部分机型上渲染不一致，仅 sand-magazine 允许极轻暖阴影，其余主题靠边框与色块分层"),
    (re.compile(r"width\s*:\s*100%", re.I),
     "WARN",  "width:100% 会让低分辨率屏图片被拉伸糊掉，改用 max-width:100%"),
    (re.compile(r"transition\s*:", re.I),
     "INFO",  "transition 会被剥离，无害但无效"),
]

CODE_STYLE = re.compile(
    r"monospace|menlo|consolas|courier|sf mono|white-space\s*:\s*pre|mono\b", re.I
)

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HALF_PUNCT_RE = re.compile(r"[,.;:?!()\"']")

FLEX_BASIS_RE = re.compile(r"flex\s*:\s*\d+(\.\d+)?\s+\d+(\.\d+)?\s+(\d+)px", re.I)
FLEX_ONE_RE = re.compile(r"flex\s*:\s*1(\.\d+)?\s*$", re.I)
MAX_WIDTH_RE = re.compile(r"max-width\s*:\s*(\d+)px", re.I)
FIXED_WIDTH_RE = re.compile(r"(?<!max-)(?<!min-)width\s*:\s*(\d+)px", re.I)
FONT_SIZE_RE = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", re.I)
RADIUS_RE = re.compile(r"border-radius\s*:\s*(\d+)px", re.I)

LEVEL_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}


# ---------------------------------------------------------------- 校验器

class Validator(HTMLParser):
    def __init__(self, path):
        super().__init__(convert_charrefs=True)
        self.path = path
        self.stack = []
        self.issues = []
        self.root = None

        self.img_count = 0
        self.leaf_count = 0
        self.para_count = 0
        self.section_count = 0
        self.code_block_count = 0
        self.mpstyle = None          # (line, parent_p_has_display_none)
        self.raw_depth = 0           # 处于 <style>/<script> 内的深度，跳过文本类检查

    # -- 记录 ----------------------------------------------------
    def _add(self, level, line, msg):
        self.issues.append((level, line, msg))

    # -- 标签开始 -------------------------------------------------
    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        attrs = dict(attrs)
        style = attrs.get("style", "") or ""

        # 1. 禁用标签
        if tag in FORBIDDEN_TAGS:
            lvl, msg = FORBIDDEN_TAGS[tag]
            self._add(lvl, line, "<%s> —— %s" % (tag, msg))

        # 2. 禁用属性
        for a in attrs:
            if a in FORBIDDEN_ATTRS:
                lvl, msg = FORBIDDEN_ATTRS[a]
                self._add(lvl, line, "属性 %s=\"%s\" —— %s" % (a, attrs[a], msg))
            elif EVENT_ATTR_RE.match(a):
                self._add("ERROR", line, "事件属性 %s 会被剥离且可能触发拦截" % a)
            elif a.startswith("data-") and tag != "mp-style-type":
                self._add("WARN", line,
                          "自定义 data-* 属性 %s 可能被剥离（仅 mp-style-type 的 data-value 例外）" % a)

        # 3. 禁用 CSS
        for rx, lvl, msg in FORBIDDEN_CSS:
            if rx.search(style):
                # img 的 width:100% 走专门的 ERROR，这里跳过重复的 WARN
                if tag == "img" and "width:100%" in msg:
                    continue
                self._add(lvl, line, "<%s> 的样式 —— %s" % (tag, msg))

        # 4. 图片专项
        if tag == "img":
            self.img_count += 1
            self._check_img(line, attrs, style)

        # 5. 隐藏标记
        if tag == "mp-style-type":
            parent = self.stack[-1] if self.stack else None
            ok = bool(parent and parent["tag"] == "p"
                      and "display:none" in (parent["style"] or "").replace(" ", ""))
            self.mpstyle = (line, ok)
            if attrs.get("data-value") != "3":
                self._add("WARN", line,
                          "mp-style-type 的 data-value 建议为 3（自动匹配公众号排版样式）")

        if tag in VOID_TAGS:
            return

        # 6. 建节点
        if tag in ("style", "script"):
            self.raw_depth += 1
        node = {
            "tag": tag, "style": style, "attrs": attrs,
            "line": line, "children": [], "direct_text": False, "has_text": False,
        }
        if self.stack:
            self.stack[-1]["children"].append(node)
        else:
            if self.root is None and tag == "section":
                self.root = node
                self._check_root(line, style)
        self.stack.append(node)

        if tag == "p":
            self.para_count += 1
        if tag == "section":
            self.section_count += 1
        if "leaf" in attrs:
            self.leaf_count += 1
        if CODE_STYLE.search(style):
            self.code_block_count += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    # -- 标签结束 -------------------------------------------------
    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                node = self.stack.pop(i)
                if tag in ("style", "script"):
                    self.raw_depth = max(0, self.raw_depth - 1)
                self._check_node_on_close(node)
                # 向上传递「子树里有没有文字」，供空块检测使用
                if self.stack and (node["has_text"] or node["direct_text"]):
                    self.stack[-1]["has_text"] = True
                # 被跳过的层级视为未闭合，忽略
                del self.stack[i + 1:]
                return

    # -- 文本 -----------------------------------------------------
    def handle_data(self, data):
        if self.raw_depth > 0:
            return                       # <style>/<script> 里的文本不做 leaf / 标点检查
        if not data.strip():
            return
        line = self.getpos()[0]
        parent = self.stack[-1] if self.stack else None

        if parent is None:
            self._add("ERROR", line, "文本“%s…”出现在根容器之外" % data.strip()[:12])
            return

        parent["direct_text"] = True

        code_ctx = any(CODE_STYLE.search(n["style"] or "") for n in self.stack)

        # leaf 包裹检查
        if not code_ctx:
            if not (parent["tag"] == "span" and "leaf" in parent["attrs"]):
                self._add(
                    "ERROR", line,
                    "文本“%s…”未包在 <span leaf=\"\"> 内（父元素 <%s>）—— 粘贴后样式会丢失"
                    % (data.strip()[:16], parent["tag"]),
                )

        # 半角标点检查
        if not code_ctx and CJK_RE.search(data):
            snippet = data.strip()
            for m in HALF_PUNCT_RE.finditer(snippet):
                i = m.start()
                prev = snippet[i - 1] if i > 0 else ""
                nxt = snippet[i + 1] if i + 1 < len(snippet) else ""
                if CJK_RE.match(prev) or CJK_RE.match(nxt):
                    self._add(
                        "WARN", line,
                        "中文语境出现半角标点“%s”：“…%s…” —— 请改为全角"
                        % (m.group(), snippet[max(0, i - 8):i + 8]),
                    )
                    break

    # -- 专项检查 -------------------------------------------------
    def _check_root(self, line, style):
        m = MAX_WIDTH_RE.search(style)
        if not m:
            self._add("WARN", line,
                      "根容器缺少 max-width —— 大屏会被拉宽，建议 max-width:677px;margin:0 auto")
        elif int(m.group(1)) > 700:
            self._add("WARN", line,
                      "根容器 max-width:%spx 超过 677px 安全宽度，微信会压缩导致字号变小" % m.group(1))
        if "box-sizing:border-box" not in style.replace(" ", ""):
            self._add("WARN", line, "根容器建议加 box-sizing:border-box，否则 padding 会撑破宽度")

    def _check_img(self, line, attrs, style):
        s = style.replace(" ", "")
        if "max-width:100%" not in s:
            self._add("ERROR", line, "<img> 缺少 max-width:100% —— 小屏会溢出")
        if "height:auto" not in s:
            self._add("WARN", line, "<img> 建议加 height:auto，否则图片比例可能失真")
        if re.search(r"(?<!max-)width\s*:\s*100%", s):
            self._add("ERROR", line,
                      "<img> 使用 width:100% 会拉伸糊掉，必须改为 max-width:100%;height:auto")
        src = (attrs.get("src") or "").strip()
        if not src:
            self._add("ERROR", line, "<img> 缺少 src")
        elif not (src.startswith("http://") or src.startswith("https://")
                  or src.startswith("data:") or src.startswith("//")):
            self._add("WARN", line,
                      "图片 src 是本地/相对路径“%s” —— 粘贴到公众号必定裂图，请先上传图床" % src[:40])

    def _check_node_on_close(self, node):
        style = node["style"] or ""
        s = style.replace(" ", "")
        line = node["line"]

        # 空容器会被编辑器折叠（装饰色条里要放 &nbsp; 占位）
        if node["tag"] == "section" and not node["children"] and not node["has_text"]:
            self._add("WARN", line,
                      "空 <section> 会被编辑器折叠消失 —— 装饰色块/分隔线请补 "
                      "<span leaf=\"\">&nbsp;</span> 占位")

        # 多屏自适应：flex 横排未设 flex-wrap
        if re.search(r"display\s*:\s*flex", s):
            big_children = []
            for c in node["children"]:
                cs = (c["style"] or "").replace(" ", "")
                m = FLEX_BASIS_RE.search(cs)
                if m and int(m.group(3)) >= 200:
                    big_children.append(c)
                elif FLEX_ONE_RE.search(cs) and len(node["children"]) >= 3:
                    big_children.append(c)
            if len(big_children) >= 2 and "flex-wrap:wrap" not in s:
                self._add("WARN", line,
                          "flex 容器有 %d 个宽子项但未设 flex-wrap:wrap —— 窄屏可能溢出，建议补上"
                          % len(big_children))
            # flex 子项缺 min-width:0
            for c in node["children"]:
                cs = (c["style"] or "").replace(" ", "")
                if ("flex:1" in cs or FLEX_BASIS_RE.search(cs)) and "min-width:0" not in cs:
                    if any(k in cs for k in ("word-break", "overflow")):
                        continue
                    self._add("INFO", c["line"],
                              "flex 子项 <%s> 建议加 min-width:0，防止长英文/URL 撑破布局" % c["tag"])
                    break

        # 固定宽度过宽
        m = FIXED_WIDTH_RE.search(style)
        if m and int(m.group(1)) > 360 and node is not self.root:
            self._add("WARN", line,
                      "<%s> 写死 width:%spx —— 窄屏会溢出，改用 max-width 或 flex 自适应"
                      % (node["tag"], m.group(1)))

        # 字号过小
        m = FONT_SIZE_RE.search(style)
        if m:
            try:
                size = float(m.group(1))
            except ValueError:
                size = 12.0
            if size < 10:
                self._add("INFO", line,
                          "字号 %.1fpx 过小，手机上难以辨认，建议 ≥11px" % size)

        # 圆角过大
        m = RADIUS_RE.search(style)
        if m and int(m.group(1)) > 10:
            self._add("INFO", line,
                      "圆角 %spx 超出主题规范（≤10px），三套主题均为小圆角" % m.group(1))

    # -- 收尾 -----------------------------------------------------
    def finalize(self):
        if not self.mpstyle:
            self._add("WARN", 0,
                      "缺少文末隐藏标记 <p style=\"display:none;\">"
                      "<mp-style-type data-value=\"3\"></mp-style-type></p>")
        elif not self.mpstyle[1]:
            self._add("ERROR", self.mpstyle[0],
                      "mp-style-type 的父级 <p> 缺少 display:none —— 会在正文里露出标记")
        if self.root is None:
            self._add("WARN", 0, "未找到根容器 <section>")
        for n in self.stack:
            self._add("WARN", n["line"], "标签 <%s> 未闭合" % n["tag"])


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="公众号 HTML 红线与多屏自适应校验")
    ap.add_argument("file", help="待校验的 HTML 文件")
    ap.add_argument("--quiet", action="store_true", help="只输出统计与结论")
    ap.add_argument("--strict", action="store_true", help="WARNING 也视为失败")
    args = ap.parse_args()

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        print("读取失败：%s" % e)
        return 2

    v = Validator(args.file)
    try:
        v.feed(html)
    except Exception as e:  # noqa: BLE001
        print("HTML 解析异常：%s" % e)
        return 2
    v.finalize()

    issues = sorted(v.issues, key=lambda x: (LEVEL_ORDER.get(x[0], 9), x[1]))
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for lvl, line, msg in issues:
        counts[lvl] = counts.get(lvl, 0) + 1
        if not args.quiet:
            loc = "%s:%s" % (args.file, line) if line else args.file
            print("[%-5s] %s  %s" % (lvl, loc, msg))

    print("")
    print("—— 统计 ——")
    print("  根容器        : %s" % ("已识别" if v.root else "缺失"))
    print("  <section>     : %d" % v.section_count)
    print("  <p> 段落      : %d" % v.para_count)
    print("  <span leaf>   : %d" % v.leaf_count)
    print("  图片          : %d" % v.img_count)
    print("  代码块区      : %d" % v.code_block_count)
    print("—— 结论 ——")
    print("  ERROR %d / WARN %d / INFO %d"
          % (counts["ERROR"], counts["WARN"], counts["INFO"]))

    if counts["ERROR"] > 0:
        print("\n❌ 存在 ERROR，禁止交付。请按行号修复后重跑。")
        return 1
    if args.strict and counts["WARN"] > 0:
        print("\n⚠️  strict 模式：存在 WARNING，视为未通过。")
        return 1
    if counts["WARN"] > 0:
        print("\n⚠️  无 ERROR，但有 WARNING —— 逐条确认后再交付。")
    else:
        print("\n✅ 校验通过，可以生成预览页并交付。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
