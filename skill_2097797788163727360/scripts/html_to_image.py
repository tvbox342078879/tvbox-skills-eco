#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
html_to_image.py —— 把排版好的 HTML 渲染成图片，供腾讯新闻/小红书/知乎等
"只能一键复制、会过滤 CSS 或只能手机发"的平台使用。

图片是所有平台的"最大公约数"：无论编辑器多简陋，图片 100% 保真。

用法:
  python html_to_image.py <输入.html> [--outdir 目录] [--width 750]
                          [--split 1200] [--title 文件名]

产出:
  <名>_长图.png          整篇一张长图（存手机相册，直接上传）
  <名>_分段_01.png ...   按高度切成多张（更适合手机逐张上传/九宫格）

依赖: playwright（chromium）
"""
import argparse
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("缺少 playwright，请先安装：pip install playwright && python -m playwright install chromium")
    sys.exit(1)

# 手机阅读容器：白底、居中、留白，字号在窄屏下更清晰
WRAP_TPL = """<!doctype html><html><head><meta charset="utf-8">
<style>
  html,body{{margin:0;padding:0;background:#ffffff;}}
  .__page{{width:{w}px;margin:0 auto;background:#ffffff;
           box-sizing:border-box;padding:{pad}px;
           font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;}}
  .__page *{{max-width:100%!important;box-sizing:border-box;}}
  .__page img{{height:auto;}}
</style></head>
<body><div class="__page">{body}</div></body></html>"""


def extract_body(html):
    """取 <body> 内部；没有就整段用。"""
    low = html.lower()
    i = low.find("<body")
    if i >= 0:
        i = low.find(">", i) + 1
        j = low.rfind("</body>")
        return html[i:j] if j > i else html[i:]
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="排版好的 HTML（建议用 *_wechat.html）")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--width", type=int, default=750,
                    help="渲染宽度(px)，750=手机2倍图，清晰")
    ap.add_argument("--pad", type=int, default=24, help="页面内边距")
    ap.add_argument("--split", type=int, default=0,
                    help="分段高度(px)，>0 时额外切成多张；0 只出长图")
    ap.add_argument("--scale", type=float, default=2.0, help="设备像素比(清晰度)")
    ap.add_argument("--title", default=None, help="输出文件名(不含扩展名)")
    ap.add_argument("--chrome", default=None,
                    help="用系统 Chrome/Chromium 可执行文件路径，跳过下载(如 C:/Program Files/Google/Chrome/Application/chrome.exe)")
    args = ap.parse_args()

    src = os.path.abspath(args.input)
    raw = open(src, encoding="utf-8", errors="ignore").read()
    body = extract_body(raw)
    doc = WRAP_TPL.format(w=args.width, pad=args.pad, body=body)

    os.makedirs(args.outdir, exist_ok=True)
    stem = args.title or os.path.splitext(os.path.basename(src))[0]
    stem = stem.replace("_wechat", "")
    long_path = os.path.join(args.outdir, f"{stem}_长图.png")

    with sync_playwright() as p:
        launch_kwargs = {"executable_path": args.chrome} if args.chrome else {}
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(
            viewport={"width": args.width, "height": 1000},
            device_scale_factor=args.scale,
        )
        # 用本地文件基准，图片(相对路径/base64)都能加载
        page.goto("file:///" + src.replace("\\", "/"), wait_until="load")
        # 直接注入包裹后的文档，保证宽度与留白统一
        page.set_content(doc, wait_until="load")
        page.wait_for_timeout(300)

        total_h = page.evaluate(
            "() => document.querySelector('.__page').scrollHeight")
        page.set_viewport_size({"width": args.width, "height": int(total_h) + 20})
        page.wait_for_timeout(200)

        page.screenshot(path=long_path, full_page=True)
        print("长图:", long_path, f"({args.width}x{int(total_h)} @{args.scale}x)")

        seg_paths = []
        if args.split and total_h > args.split:
            n = int((total_h + args.split - 1) // args.split)
            for k in range(n):
                y = k * args.split
                h = min(args.split, total_h - y)
                sp = os.path.join(args.outdir, f"{stem}_分段_{k+1:02d}.png")
                page.screenshot(path=sp, clip={
                    "x": 0, "y": y, "width": args.width, "height": h})
                seg_paths.append(sp)
            print(f"分段: {n} 张 -> {stem}_分段_01.png ...")

        browser.close()

    print("完成。图片可存手机相册，在腾讯新闻/小红书等 App 里直接当图片上传。")
    return long_path, seg_paths


if __name__ == "__main__":
    main()
