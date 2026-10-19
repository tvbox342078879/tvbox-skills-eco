#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_inline.py — wechat_convert 内联器的回归测试。

守护的就是「用户文章自带 <style> 里的自定义 class 必须被内联」这一根因：
历史上 premailer 路径 + 只内联主题 CSS，导致 .fact-box / .golden 等 class
粘贴后样式全丢。本测试用含自定义 class 的最小 fixture 断言它们确实被拍平为
inline style，CI / 手动 `python scripts/test_inline.py` 即可防退化。

运行：
  python scripts/test_inline.py
依赖：beautifulsoup4, lxml（与 wechat_convert.py 相同）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bs4 import BeautifulSoup  # noqa: E402
from wechat_convert import parse_css_rules, inline_rules  # noqa: E402


def _soup(html):
    return BeautifulSoup(html, "lxml")


def _run(css, body):
    soup = _soup(f"<html><head><style>{css}</style></head><body>{body}</body></html>")
    root = soup.body
    rules = parse_css_rules(css)
    inline_rules(root, rules)
    return root


def _check(name, cond):
    if cond:
        print(f"  ✅ {name}")
        return True
    print(f"  ❌ {name}")
    return False


def main():
    ok = True
    print("内联器回归测试：")

    # 1) 简单 class 必须内联
    r = _run(".box{background:#abc;border:1px solid #999;padding:10px;}",
             '<div class="box">hi</div>')
    box = r.find("div")
    st = box.get("style", "")
    ok &= _check("简单 class .box 被内联",
                 "background:#abc" in st and "border:1px solid #999" in st)

    # 2) 后代选择器 .box .hi 必须命中内层
    r = _run(".box{color:#111;} .box .hi{color:red;}",
             '<div class="box">x <span class="hi">y</span></div>')
    hi = r.find("span", class_="hi")
    ok &= _check("后代选择器 .box .hi 命中内层",
                 "color:red" in hi.get("style", ""))

    # 3) 复合选择器 .a.b 必须命中同时带两类的元素
    r = _run(".a.b{font-weight:700;}",
             '<p class="a b">x</p><p class="a">y</p>')
    ps = r.find_all("p")
    both = next(p for p in ps if set(p.get("class", [])) == {"a", "b"})
    only_a = next(p for p in ps if set(p.get("class", [])) == {"a"})
    ok &= _check("复合选择器 .a.b 仅命中双类元素",
                 "font-weight:700" in (both.get("style") or "") and
                 "font-weight:700" not in (only_a.get("style") or ""))

    # 4) #id 选择器
    r = _run("#main{margin:0 auto;}", '<section id="main">x</section>')
    ok &= _check("#id 选择器命中", "margin:0 auto" in r.find(id="main").get("style", ""))

    # 5) tag 选择器
    r = _run("h2{color:#2b6cb0;}", '<h2>t</h2>')
    ok &= _check("tag 选择器 h2 命中", "color:#2b6cb0" in r.find("h2").get("style", ""))

    # 6) 内联样式优先于 class（不被覆盖）
    r = _run(".x{color:red;}", '<p class="x" style="color:blue;">t</p>')
    ok &= _check("内联 style 不被 class 覆盖",
                 r.find("p").get("style", "").strip().rstrip(";") == "color:blue")

    # 7) 后者覆盖前者（同属性，后定义胜）
    r = _run(".y{color:red;} .y{color:green;}", '<p class="y">t</p>')
    ok &= _check("后定义规则覆盖先定义", "color:green" in r.find("p").get("style", ""))

    # 8) @media 块被剔除（不内联其中规则，避免污染）
    r = _run("@media(max-width:600px){.z{color:red;}} .z{color:blue;}",
             '<p class="z">t</p>')
    ok &= _check("@media 内规则被剔除，仅留普通规则",
                 r.find("p").get("style", "").strip().rstrip(";") == "color:blue")

    # 9) 伪类/伪元素被跳过（公众号不认）
    r = _run(".q:hover{color:red;} .q{color:green;}", '<p class="q">t</p>')
    ok &= _check("伪类 :hover 被跳过",
                 r.find("p").get("style", "").strip().rstrip(";") == "color:green")

    # 10) 真实场景：文章自定义卡片 class 必须内联（核心回归点）
    css = """
      .fact-box{background:#f0f4ff;border:1px solid #93c5fd;padding:14px 16px;margin:18px 0;}
      .fact-box b{color:#1d4ed8;}
      .limit-box h3{font-size:16px;color:#a16200;}
      .golden{background:linear-gradient(transparent 60%, #fde047 60%);padding:0 3px;font-weight:700;}
    """
    body = ('<div class="fact-box"><b>冷知识</b></div>'
            '<div class="limit-box"><h3>局限</h3></div>'
            '<p><span class="golden">重点</span></p>')
    r = _run(css, body)
    fb = r.find("div", class_="fact-box")
    ok &= _check(".fact-box 卡片背景/边框被内联",
                 "background:#f0f4ff" in fb.get("style", "") and
                 "border:1px solid #93c5fd" in fb.get("style", ""))
    fb_b = fb.find("b")
    ok &= _check(".fact-box b 后代选择器命中", "color:#1d4ed8" in fb_b.get("style", ""))
    lb_h3 = r.find("div", class_="limit-box").find("h3")
    ok &= _check(".limit-box h3 后代选择器命中", "color:#a16200" in lb_h3.get("style", ""))
    g = r.find("span", class_="golden")
    # 渐变在 sanitize 阶段才退化，这里只验证 padding/font-weight 已内联（内联器本职）
    ok &= _check(".golden 内联器拍平 padding/font-weight",
                 "padding:0 3px" in g.get("style", "") and "font-weight:700" in g.get("style", ""))

    print()
    if ok:
        print("🎉 全部通过：自定义 class 已确定性内联，旧版「粘贴后样式全丢」根因已修复。")
        sys.exit(0)
    else:
        print("⚠️ 存在失败用例，内联器可能退化，请检查 wechat_convert.py。")
        sys.exit(1)


if __name__ == "__main__":
    main()
