#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_convert.py — 把任意 HTML / Markdown 转成「公众号可粘贴」的兼容版本。

微信公众号编辑器的粘贴白名单会丢弃大量 CSS：
  - linear-gradient / radial-gradient 背景 → 直接变透明（最常见崩坏）
  - box-shadow / text-shadow / transform / transition / animation
  - position / float 限制 / display:flex|grid
  - background-image / background-size 等
本脚本做四件事：
  1) 内联 CSS（自研确定性内联器：把「主题 + 文章自带 <style>」的全部 class 拍平为内联样式，绝不静默丢弃自定义样式）
  2)  sanitize：把不支持的写法转成兼容写法（渐变→实色，阴影/变换→丢弃）
  3)  图片本地化：本地图自动转 base64 内嵌，外链图保留并加 max-width
  4)  套主题 + 包裹容器，产出两份文件：
        *_wechat.html  —— 可直接在浏览器打开后 Ctrl+A 复制、粘进公众号
        *_preview.html —— 375px 移动端预览（所见即公众号所见）

用法：
  python wechat_convert.py 输入.html [--theme fresh] [--title "标题"] [--outdir 目录] [--name 前缀]
  python wechat_convert.py 输入.md   # 自动把 Markdown 转 HTML 再处理
依赖：beautifulsoup4, lxml, markdown(可选)。CSS 内联为自研确定性实现，无需 premailer。
"""
import os
import re
import sys
import base64
import argparse

# Windows 控制台默认 gbk，强制 stdout/stderr 为 utf-8 以免打印 emoji 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("缺少 beautifulsoup4，请先 pip install beautifulsoup4 lxml")

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "..", "assets")


# ---------------------------------------------------------------------------
# 1) 公众号支持的 CSS 白名单
# ---------------------------------------------------------------------------
ALLOWED_PROPS = {
    "color", "font-size", "font-weight", "font-style", "font-family",
    "font-variant", "text-align", "text-decoration", "text-indent",
    "text-transform", "line-height", "letter-spacing", "word-spacing",
    "white-space", "vertical-align", "word-break", "word-wrap",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-width", "border-style", "border-color", "border-radius",
    "border-collapse", "border-spacing", "background-color",
    "width", "height", "max-width", "min-width", "min-height",
    "display", "float", "clear", "opacity", "list-style-type",
    "list-style-position", "table-layout",
}

# 这些属性公众号基本不支持，直接丢弃
DROP_PROPS = {
    "background", "background-image", "background-size", "background-position",
    "background-repeat", "background-attachment", "background-clip",
    "background-origin", "box-shadow", "text-shadow", "outline", "outline-color",
    "outline-style", "outline-width", "transform", "transform-origin",
    "transform-style", "transition", "transition-property", "transition-duration",
    "transition-timing-function", "transition-delay", "animation",
    "animation-name", "animation-duration", "animation-timing-function",
    "animation-delay", "animation-iteration-count", "animation-direction",
    "position", "top", "right", "bottom", "left", "z-index", "filter",
    "backdrop-filter", "pointer-events", "cursor", "user-select",
    "visibility", "overflow", "overflow-x", "overflow-y", "object-fit",
    "object-position", "gap", "flex", "flex-grow", "flex-shrink", "flex-basis",
    "flex-direction", "flex-wrap", "grid", "grid-template", "grid-template-columns",
    "grid-template-rows", "align-items", "align-content", "justify-content",
    "justify-items", "align-self", "justify-self", "clip", "clip-path",
    "will-change", "box-sizing", "content", "caption-side", "list-style",
    "list-style-image", "-webkit-box-shadow", "-moz-box-shadow",
}

# display 只允许这些值，其余（flex/grid 等）丢弃
DISPLAY_OK = {"block", "inline-block", "none", "table", "table-cell",
              "table-row", "list-item", "inline", "run-in"}

# 容器默认样式（仅作用于没有自带样式的裸文本/元素）
WRAP_STYLE = (
    "font-size:15px;color:#3f3f3f;line-height:1.85;letter-spacing:.3px;"
    "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',"
    "'Hiragino Sans GB','Microsoft YaHei',sans-serif;word-break:break-word;"
)


def parse_declarations(style_str):
    """返回 [(prop, value), ...]，保留原始顺序。"""
    out = []
    if not style_str:
        return out
    for chunk in style_str.split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        prop, val = chunk.split(":", 1)
        out.append((prop.strip().lower(), val.strip()))
    return out


def serialize_declarations(decls):
    return ";".join(f"{p}:{v}" for p, v in decls if v != "")


def first_color(value):
    """从一个 CSS 值里抓第一个颜色（hex / rgb / 已知色名）。"""
    m = re.search(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))", value)
    if m:
        return m.group(1)
    for name in ("white", "black", "red", "green", "blue", "yellow",
                 "orange", "purple", "gray", "grey", "silver", "transparent"):
        if re.search(r"\b" + name + r"\b", value, re.I):
            return name
    return None


def sanitize_style(tag, warnings, stats):
    """就地清理单个标签的 style 属性，返回是否改动。"""
    if not tag.has_attr("style"):
        return False
    decls = parse_declarations(tag["style"])
    new = []
    for prop, val in decls:
        low = val.lower()
        # background 单独处理：渐变/图片 → 实色，纯实色 shorthand 直接保留
        if prop == "background":
            if "gradient" in low:
                c = first_color(val)
                if c:
                    new.append(("background-color", c))
                    warnings.append(
                        f"渐变背景已退化为实色 {c}（公众号不支持渐变）")
                else:
                    warnings.append("渐变背景被移除（未解析到颜色）")
                    stats["dropped"] += 1
            elif "url(" in low:
                c = first_color(val)
                if c:
                    new.append(("background-color", c))
                warnings.append("背景图片被移除（公众号粘贴易丢图，建议用实色）")
                stats["dropped"] += 1
            else:
                c = first_color(val)
                if c:
                    new.append(("background-color", c))
                else:
                    stats["dropped"] += 1
            continue
        # 丢弃整类不支持属性
        if prop in DROP_PROPS:
            stats["dropped"] += 1
            continue
        if prop not in ALLOWED_PROPS:
            stats["dropped"] += 1
            continue
        # display 限制
        if prop == "display":
            base = low.replace(" ", "")
            if base not in DISPLAY_OK:
                stats["dropped"] += 1
                continue
        # width 超过 100% 会被公众号压扁，限制一下
        if prop == "width" and re.match(r"\d", val) and "%" not in val \
                and "px" in val:
            try:
                if int(re.sub(r"\D", "", val)) > 1080:
                    val = "100%"
            except ValueError:
                pass
        new.append((prop, val))
    tag["style"] = serialize_declarations(new)
    if not tag["style"]:
        del tag["style"]
    return True


def handle_images(soup, base_dir, img_mode, warnings, stats):
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src:
            continue
        if src.startswith("data:image"):
            # 已是内嵌
            style = img.get("style", "")
            if "max-width" not in style:
                img["style"] = (style + ";max-width:100%;").lstrip(";")
            size = len(src) * 3 // 4
            if size > 1_000_000:
                warnings.append(
                    f"内嵌图片约 {size//1000}KB，过大可能被公众号丢弃，建议压缩")
            continue
        if src.startswith(("http://", "https://")):
            style = img.get("style", "")
            if "max-width" not in style:
                img["style"] = (style + ";max-width:100%;").lstrip(";")
            continue
        # 本地/相对路径
        if img_mode == "keep":
            continue
        local = src
        if not os.path.isabs(local):
            local = os.path.join(base_dir, local)
        if os.path.exists(local):
            ext = os.path.splitext(local)[1].lstrip(".").lower() or "png"
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                    "gif": "gif", "webp": "webp", "bmp": "bmp"}.get(ext, "png")
            with open(local, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            img["src"] = f"data:image/{mime};base64,{b64}"
            style = img.get("style", "")
            if "max-width" not in style:
                img["style"] = (style + ";max-width:100%;").lstrip(";")
            stats["embedded"] += 1
            warnings.append(f"本地图片已转 base64 内嵌：{os.path.basename(local)}")
        else:
            warnings.append(f"本地图片不存在，无法内嵌（将显示为破图）：{src}")


def unwrap_nested_docs(soup):
    """递归拆除嵌套的 <html>/<head>/<body> 标签（保留子元素）。

    常见触发场景：
    - 输入文件本身是完整的 HTML 文档（带 <html><head><body>）
    - 输入文件是旧版 _preview.html / _wechat.html（已被包装过一次）
    - AI 生成的 HTML 嵌套了多层文档结构
    不拆的话，产物里会出现 <section><!DOCTYPE><html><body>...</body></html></section>，
    公众号 ProseMirror 遇到内部 html/body 会异常解析，导致开头元素样式丢失。
    """
    # 需要重复处理直到稳定（处理 body 时可能暴露出内层的 html）
    for _ in range(4):  # 最多 4 层嵌套，防止无限循环
        found = False
        for tag_name in ("html", "head", "body"):
            for tag in soup.find_all(tag_name):
                found = True
                # 将 tag 替换为其所有子节点（unwrap 操作）
                tag.unwrap()
        if not found:
            break


def strip_ui_chrome(soup):
    """移除预览模板的 UI 铃层元素（工具栏/按钮/提示/手机模拟框）。
    输入可能是旧版 _preview.html，其 <body> 里嵌了 .bar / #phone / .tip / button 等。
    不剥的话这些会当正文内容进入公众号。
    注意：必须在 strip_unwanted() 之前调用（此时 class/id 属性还在，可精确匹配）。"""
    # 按class匹配：工具栏、底部提示 → 整体删除（里面没有正文）
    for cls in ("bar", "tip"):
        for el in soup.find_all(class_=cls):
            el.decompose()
    # 按id匹配：复制状态提示 → 整体删除
    for id_ in ("copied",):
        for el in soup.find_all(id=id_):
            el.decompose()
    # 按标签匹配：按钮 → 整体删除
    for el in soup.find_all("button"):
        el.decompose()
    # 手机模拟框 #phone → 拆标签保留子元素（正文在它里面！）
    for el in soup.find_all(id="phone"):
        el.unwrap()


def strip_unwanted(soup):
    for sel in ("style", "script", "link", "meta", "title", "base"):
        for t in soup.find_all(sel):
            t.decompose()
    # 剥离 HTML 注释（注释不渲染，但会污染诊断扫描，且不应进入发布产物）
    try:
        from bs4 import Comment
        for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
            c.extract()
    except Exception:
        pass
    # 去掉 class/id（样式已内联，保留只会让源码臃肿）
    # 例外：callout / note 这类"组件类"保留，供发布前诊断器识别视觉锚点
    KEEP_CLASS = {"callout", "note"}
    for t in soup.find_all(True):
        if t.has_attr("class"):
            kept = [c for c in t["class"] if c in KEEP_CLASS]
            if kept:
                t["class"] = kept
            else:
                del t["class"]
        if t.has_attr("id"):
            del t["id"]


def load_theme(name):
    if not name or name == "none":
        return None
    # 兼容两种命名：发布到开放平台时 .css 不在扩展名白名单，模板改名为 .css.txt
    candidates = [f"{name}.css", f"{name}.css.txt"]
    path = None
    for cand in candidates:
        p = os.path.join(TEMPLATE_DIR, cand)
        if os.path.exists(p):
            path = p
            break
    if path is None:
        for cand in ("fresh.css", "fresh.css.txt"):
            p = os.path.join(TEMPLATE_DIR, cand)
            if os.path.exists(p):
                path = p
                break
    if path is None:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1b) 确定性 CSS 内联器（替代 premailer）
#     设计动机：premailer 对自定义 class / 复杂选择器会「静默跳过」，导致用户文章里
#     自己写的 .fact-box / .golden 等 class 内联失败、粘贴后样式全丢。
#     本内联器仅依赖 bs4 + 正则，行为透明、可预测：
#       - 同时内联「主题 CSS」+「用户文章 <style> 里的全部规则」
#       - 支持 .class / tag / #id / 复合 / 后代选择器；跳过伪类::/属性/兄弟组合符
#       - 内联样式优先于 class 规则（符合 CSS 优先级）；多个规则命中后者覆盖前者
#       - 兼容化（渐变→实色等）在 sanitize 阶段统一处理
# ---------------------------------------------------------------------------

def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _strip_at_rules(css):
    """删除 @media / @keyframes / @import / @font-face 等 @规则块。
    公众号编辑器不认 @规则，且 @ 块里的 { } 会干扰后面的规则解析，必须整段剔除。"""
    out = []
    i, n = 0, len(css)
    while i < n:
        c = css[i]
        if c == '@':
            depth = 0
            j = i
            while j < n:
                if css[j] == '{':
                    depth += 1
                elif css[j] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_compound(comp):
    """解析单个复合选择器（tag + #id + .class）。不支持的字符（* 等）返回 None。"""
    comp = comp.strip()
    if not comp:
        return None
    tag = None
    ids = []
    classes = []
    m = re.match(r"^[a-zA-Z][\w-]*", comp)
    if m:
        tag = m.group(0).lower()
    i = m.end() if m else 0
    while i < len(comp):
        ch = comp[i]
        if ch == '.':
            j = i + 1
            while j < len(comp) and (comp[j].isalnum() or comp[j] in "-_"):
                j += 1
            if j > i + 1:
                classes.append(comp[i + 1:j])
            i = j
        elif ch == '#':
            j = i + 1
            while j < len(comp) and (comp[j].isalnum() or comp[j] in "-_"):
                j += 1
            if j > i + 1:
                ids.append(comp[i + 1:j])
            i = j
        else:
            return None
    return {"tag": tag, "ids": ids, "classes": classes}


def _parse_selector_chain(sel):
    """解析选择器链：按空白切分为后代选择器列表（末位为目标元素）。
    含伪类:: / 属性[] / 子>/兄弟+/~ 组合符则返回 None（跳过，公众号不认）。"""
    sel = sel.strip()
    if not sel:
        return None
    if any(tok in sel for tok in (':', '[', '>', '+', '~')):
        return None
    parts = [p for p in sel.split() if p]
    if not parts:
        return None
    compounds = []
    for p in parts:
        c = _parse_compound(p)
        if c is None:
            return None
        compounds.append(c)
    return compounds


def _matches_compound(el, comp):
    if comp["tag"] and (el.name or "").lower() != comp["tag"]:
        return False
    if comp["ids"]:
        eid = el.get("id", "")
        if not eid or eid not in comp["ids"]:
            return False
    if comp["classes"]:
        ecls = el.get("class", []) or []
        if not isinstance(ecls, list):
            ecls = [ecls]
        for c in comp["classes"]:
            if c not in ecls:
                return False
    return True


def _matches_selector(el, compounds):
    """el 是否匹配该选择器链（末位复合匹配 el，前驱复合按序匹配其祖先）。"""
    if not _matches_compound(el, compounds[-1]):
        return False
    ci = len(compounds) - 2
    parent = el.parent
    while ci >= 0 and parent is not None:
        if _matches_compound(parent, compounds[ci]):
            ci -= 1
        parent = parent.parent
    return ci < 0


def parse_css_rules(css_text):
    """解析 <style> 文本 → [(compounds, decls, source_index), ...]。"""
    css_text = _strip_comments(css_text)
    css_text = _strip_at_rules(css_text)
    rules = []
    idx = 0
    for block in css_text.split('}'):
        if '{' not in block:
            continue
        sel_part, decl_part = block.split('{', 1)
        decls = parse_declarations(decl_part)
        if not decls:
            continue
        for sel in (s.strip() for s in sel_part.split(',') if s.strip()):
            compounds = _parse_selector_chain(sel)
            if compounds is None:
                continue
            rules.append((compounds, decls, idx))
            idx += 1
    return rules


def inline_rules(soup, rules):
    """把规则内联到 soup 各元素。
    优先级（高→低）：元素已有 inline style  >  后出现的 class 规则  >  先出现的 class 规则。
    - 元素手写的 inline style 受保护，class 规则绝不覆盖它（符合 CSS 优先级）。
    - 但 class 规则之间按源码顺序级联：后定义覆盖先定义（修复旧版「后者不覆盖前者」）。"""
    if not rules:
        return
    for el in soup.find_all(True):
        matched = [r for r in rules if _matches_selector(el, r[0])]
        if not matched:
            continue
        matched.sort(key=lambda r: r[2])  # 按源码顺序，后者覆盖前者
        base_map = {p: v for p, v in parse_declarations(el.get("style", ""))}
        inline_props = set(base_map)       # 这些属性来自 inline，受保护不可被 class 覆盖
        for _compounds, decls, _idx in matched:
            for p, v in decls:
                v = re.sub(r"\s*!important\s*", "", v, flags=re.I).strip()
                if p in inline_props:       # inline 优先：跳过
                    continue
                base_map[p] = v             # class 规则级联：后者覆盖前者
        el["style"] = serialize_declarations(list(base_map.items()))
        if not el["style"]:
            del el["style"]


def inner(el):
    """取元素内部 HTML（不含元素自身标签），用于剥离 <body>。"""
    if el is None:
        return ""
    return "".join(str(c) for c in el.children)


def md_to_html(text):
    try:
        import markdown
        return markdown.markdown(
            text, extensions=["tables", "fenced_code", "nl2br"])
    except ImportError:
        # 退化：把换行变成 <br>，标题简单处理
        lines = []
        for ln in text.splitlines():
            if ln.startswith("# "):
                lines.append(f"<h1>{ln[2:]}</h1>")
            elif ln.startswith("## "):
                lines.append(f"<h2>{ln[3:]}</h2>")
            elif ln.startswith("### "):
                lines.append(f"<h3>{ln[4:]}</h3>")
            elif ln.strip() == "":
                continue
            else:
                lines.append(f"<p>{ln}</p>")
        return "\n".join(lines)


def build_preview(body_html, title):
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>公众号预览 - {title or '未命名'}</title>
<style>
  body{{margin:0;background:#ededed;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;}}
  .bar{{position:sticky;top:0;background:#07c160;color:#fff;padding:10px 16px;
        font-size:14px;z-index:9;display:flex;align-items:center;justify-content:space-between;}}
  .bar button{{background:#fff;color:#07c160;border:none;border-radius:14px;
        padding:6px 14px;font-size:13px;font-weight:bold;cursor:pointer;}}
  .phone{{width:375px;margin:16px auto 40px;background:#fff;min-height:667px;
          padding:16px;box-sizing:border-box;border-radius:4px;}}
  .phone *{{max-width:100%!important;box-sizing:border-box;}}
  .tip{{max-width:375px;margin:0 auto;color:#999;font-size:12px;padding:0 4px 30px;}}
  #copied{{color:#ffd;font-size:12px;margin-left:8px;}}
</style>
</head>
<body>
  <div class="bar">
    <span>📱 公众号移动端预览（375px）· 所见即所得</span>
    <span><button onclick="copyContent()">📋 复制全文</button><span id="copied"></span></span>
  </div>
  <div class="phone" id="phone">
  <div id="copy-target">
{body_html}
  </div>
  </div>
  <div class="tip">说明：渐变/阴影等公众号不支持的样式已退化为兼容写法。点击「复制全文」后，到公众号编辑器 Ctrl+V 即可。</div>
<script>
/* 复制即所得：#copy-target 内的 HTML 已由 Python 管道完全清洗
   （div→section、渐变→实色、剥 px 宽度、拆 UI 外壳、style 内联、零 script/style），
   这里只把已干净的 HTML 写剪贴板，不做任何浏览器内转换——
   因此不存在 JS 转义坑、也没有会崩的转换逻辑。 */
function copyContent(){{
  var node = document.getElementById("copy-target");
  if(!node){{ var c=document.getElementById("copied"); if(c)c.textContent="复制失败"; return; }}
  var html = node.innerHTML;
  var plain = node.innerText;
  var tip = document.getElementById("copied");
  function done(msg){{ if(tip)tip.textContent=msg; }}
  try{{
    if(navigator.clipboard && window.ClipboardItem){{
      var htmlBlob=new Blob([html],{{type:"text/html"}});
      var textBlob=new Blob([plain],{{type:"text/plain"}});
      navigator.clipboard.write([new ClipboardItem({{"text/html":htmlBlob,"text/plain":textBlob}})])
        .then(function(){{done("已复制 ✅");}}).catch(function(){{fallback();}});
      return;
    }}
  }}catch(e){{}}
  fallback();
  function fallback(){{
    var tmp=document.createElement("div");
    tmp.style.position="fixed";tmp.style.left="-9999px";
    tmp.innerHTML=html;
    document.body.appendChild(tmp);
    try{{
      var r=document.createRange();r.selectNodeContents(tmp);
      var s=window.getSelection();s.removeAllRanges();s.addRange(r);
      document.execCommand("copy");done("已复制 ✅");
    }}catch(e){{done("复制失败，请手动 Ctrl+A");}}
    s.removeAllRanges();
    document.body.removeChild(tmp);
  }}
}}</script>
</body>
</html>"""




def validate_wechat(html):
    """出厂质检：检查转换产物是否真的公众号兼容。
    返回问题列表（空=通过）。任一不过都意味着粘贴后可能崩/变形。"""
    problems = []
    if re.search(r"<div[\s>]", html, re.I):
        n = len(re.findall(r"<div[\s>]", html, re.I))
        problems.append(f"仍含 {n} 个 <div>（公众号不认 div 容器，必须全转 section）")
    if re.search(r"(linear|radial)-gradient", html, re.I):
        problems.append("仍含渐变背景（公众号不支持，应已退化为实色）")
    m = re.search(r"max-width\s*:\s*(\d+)px", html, re.I)
    if m and m.group(1) != "100":
        problems.append(f"仍含固定像素 max-width 约束：{m.group(0).strip()}")
    for pat in (r"<button[\s>]", r'class\s*=\s*["\']bar', r'class\s*=\s*["\']tip',
                r'id\s*=\s*["\']phone', r'id\s*=\s*["\']copied',
                r'class\s*=\s*["\']phone'):
        if re.search(pat, html, re.I):
            problems.append("仍含预览 UI 铬层残留（.bar/.tip/#phone/#copied/button）")
            break
    if re.search(r"<script[\s>]", html, re.I):
        problems.append("仍含 <script>（不应进入发布产物）")
    if re.search(r"<style[\s>]", html, re.I):
        problems.append("仍含 <style>（样式应已内联，不应残留）")
    if re.search(r"<!DOCTYPE", html, re.I):
        problems.append("仍含 <!DOCTYPE> 声明")
    if re.search(r"<(html|head|body)[\s>]", html, re.I):
        problems.append("仍含 <html>/<head>/<body> 标签（应已 unwrap）")
    return problems

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="输入 .html 或 .md 文件")
    ap.add_argument("--theme", default="fresh",
                    help="主题：fresh / minimal / news / warm / none")
    ap.add_argument("--title", default="", help="文章标题（仅用于预览标题栏）")
    ap.add_argument("--outdir", default="", help="输出目录，默认与输入同目录")
    ap.add_argument("--name", default="",
                    help="输出文件前缀，默认取输入文件名（不含扩展名）。"
                         "用于区分不同推文避免互相覆盖，如 --name epub-to-txt")
    ap.add_argument("--img-mode", default="embed",
                    choices=["embed", "keep"],
                    help="本地图片处理方式：embed 转 base64 / keep 保留原路径")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"输入文件不存在：{args.input}")
    base_dir = os.path.dirname(os.path.abspath(args.input))
    raw = open(args.input, "r", encoding="utf-8", errors="ignore").read()

    warnings = []
    # Stage 1: 读取并（按需）把 Markdown 转 HTML
    ext = os.path.splitext(args.input)[1].lower()
    if ext == ".md":
        soup = BeautifulSoup(md_to_html(raw), "lxml")
        warnings.append("输入为 Markdown，已转为 HTML")
    else:
        # 健壮解析：片段（无 <html>/<body>）若以 <style> 开头，lxml 会把
        # 第一个紧随其后的块级元素（如首图 <figure>）误删。统一包成完整文档再解析。
        if not re.search(r"<\s*(html|body)[\s>]", raw, re.I):
            raw = "<html><head></head><body>" + raw + "</body></html>"
        soup = BeautifulSoup(raw, "lxml")

    # Stage 2（关键修复）：抽取用户文章自带的 <style>
    #   旧版只内联主题 CSS，把用户 <head> 里的 <style> 整段丢弃 → 自定义 class 样式全丢。
    #   新版把「主题 + 用户自带 <style>」合并后再统一内联。
    user_css = "\n".join(s.get_text() for s in soup.find_all("style"))

    # 取 body（无 body 时退化为整篇）
    root = soup.body if soup.body else soup

    # Stage 3: 合并 主题 + 用户CSS，确定性内联
    #   顺序：主题在前、用户在后 → 用户显式样式优先于主题同选择器。
    theme_css = load_theme(args.theme)
    combined = (theme_css or "") + "\n" + user_css
    rules = parse_css_rules(combined)
    inline_rules(root, rules)

    stats = {"dropped": 0, "embedded": 0}
    # Stage 4: sanitize 兼容化 + 图片
    for tag in root.find_all(True):
        sanitize_style(tag, warnings, stats)
    handle_images(root, base_dir, args.img_mode, warnings, stats)

    # 关键：公众号 ProseMirror 编辑器**不认 <div>**，会连背景/边框/圆角一起吞掉！
    # 必须把所有 <div> 换成 <section>（秀米/135 导出全是 section）。
    for d in root.find_all("div"):
        d.name = "section"

    # Stage 4.5: 剥离 UI 铬层（输入可能是旧版 _preview.html，其 body 里嵌了
    #   .bar 工具栏 / #phone 手机框 / .tip 提示 / button 按钮）
    #   必须在 strip_unwanted() 之前调用（此时 class/id 还在，可精确匹配）
    strip_ui_chrome(soup)

    # Stage 5: 清理 <style>/注释/class（样式已内联，class 仅留组件类供诊断）
    strip_unwanted(soup)

    # Stage 5.5: 拆除内容区内的嵌套 <html>/<head>/<body>
    #   输入可能是完整 HTML 文档或已被包装过的 _preview.html，
    #   不拆的话产物里会出现 <section><!DOCTYPE><html><body>...</body></html></section>
    unwrap_nested_docs(root)

    clean = inner(root)
    # 清除残留的 <!DOCTYPE ...> 声明（unwrap 只处理标签，不处理 DOCTYPE 节点）
    clean = re.sub(r'<!DOCTYPE[^>]*>\s*', '', clean, flags=re.I).strip()
    # 包裹容器：必须用 <section>，绝不能用 <div>（公众号会吞掉 div）
    wrapped = f'<section id="content" style="{WRAP_STYLE}">{clean}</section>'

    # Stage 6: 写两份文件


    # Stage 5.8: 出厂质检（硬门禁）
    problems = validate_wechat(wrapped)
    if problems:
        print("❌ 出厂质检未通过，请检查（产物仍已写出，但粘贴公众号可能崩）：")
        for p in problems:
            print(f"   - {p}")
    else:
        print("✅ 出厂质检通过（0 div / 0 渐变 / 0 固定max-width / 0 UI铬层 / 0 script-style / 0 doctype）")
    outdir = args.outdir or base_dir
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.input))[0]
    prefix = args.name or stem
    wechat_path = os.path.join(outdir, f"{prefix}_wechat.html")
    preview_path = os.path.join(outdir, f"{prefix}_preview.html")
    open(wechat_path, "w", encoding="utf-8").write(wrapped)
    open(preview_path, "w", encoding="utf-8").write(
        build_preview(wrapped, args.title))

    # 报告
    print("✅ 转换完成（已内联 主题 + 文章自带样式）")
    print(f"  · 可直接复制版 : {wechat_path}")
    print(f"  · 移动端预览   : {preview_path}")
    print(f"  · 内联规则数   : {len(rules)}")
    print(f"  · 丢弃属性数   : {stats['dropped']}")
    print(f"  · 内嵌图片数   : {stats['embedded']}")
    if warnings:
        print("  · 提示：")
        for w in warnings:
            print(f"      - {w}")

    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
