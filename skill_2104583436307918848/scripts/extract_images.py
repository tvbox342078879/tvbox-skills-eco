#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取网页 / 公众号文章正文图片，转存为 JPG。

用法:
    python extract_images.py --url "https://example.com/article" [--output ./extracted_images]

特性:
    - 自动定位正文区域（支持微信公众号 js_content、article 等常见正文容器）
    - 自动去除广告图、Logo、头像、二维码、图标、背景图、占位图等无关图片
    - 所有图片统一转换为 JPG 格式（GIF 取首帧、PNG 透明底铺白）
    - 下载文件自动保存到指定文件夹
"""
import argparse
import os
import re
import sys
import time
import warnings
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image

warnings.filterwarnings("ignore")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 正文容器选择器，按优先级依次尝试（微信公众号正文在最前）
CONTENT_SELECTORS = [
    "div#js_content",            # 微信公众号正文
    "div.rich_media_content",    # 微信公众号正文（兜底）
    "article",
    "div.article-content",
    "div.article_content",
    "div.post-content",
    "div.entry-content",
    "div#content",
    "div.content",
    "div#main-content",
    "div.main-content",
    "main",
]

# 无关图片的特征关键词：命中 class/id/父容器即过滤
BAD_KEYWORDS = [
    "ad", "ads", "advert", "banner", "logo", "qr", "qrcode",
    "footer", "header", "nav", "menu", "sidebar", "comment", "share",
    "recommend", "related", "hot", "subscribe", "follow", "reward",
    "promo", "sponsor", "tip", "copyright", "avatar", "icon", "emoji",
    "decor", "decoration", "background", "bkg", "watermark", "weixin",
    "wechat", "official", "profile",
]

# 无关图片 URL 特征
BAD_URL_PATTERNS = [
    r"^data:", r"1x1\.gif", r"blank\.gif", r"pixel", r"spacer",
    r"loading", r"\.svg", r"beacon", r"track", r"placeholder",
    r"emoji",
]

IMG_ATTRS = ["data-src", "data-original", "data-lazyload", "data-actualsrc", "data-s", "src"]


def fetch_html(url, timeout=25):
    """抓取网页 HTML，带重试与微信 UA。"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=timeout, verify=False)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5)
    raise RuntimeError(f"网页抓取失败: {last_err}")


def find_content_node(soup):
    """定位正文节点：优先命中已知正文容器，其次取 img 密度最高的容器。"""
    for sel in CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node is not None:
            return node
    # 兜底：在 body 内按 img 数量排序取候选块
    body = soup.body or soup
    best = None
    best_count = -1
    for candidate in body.find_all(["div", "section", "article"]):
        count = len(candidate.find_all("img"))
        if count > best_count:
            best_count = count
            best = candidate
    if best is not None and best_count > 0:
        return best
    return body


def node_has_bad_feature(node):
    """检查节点及其祖先是否带无关特征。"""
    for cur in [node] + list(node.parents):
        if cur is None:
            break
        ids = (cur.get("id") or "") + " " + (cur.get("class") and " ".join(cur.get("class")) or "")
        for kw in BAD_KEYWORDS:
            if kw in ids.lower():
                return True
    return False


def is_bad_url(src):
    """按 URL 特征过滤无关图片。"""
    low = src.lower()
    for pat in BAD_URL_PATTERNS:
        if re.search(pat, low):
            return True
    return False


def collect_image_urls(url, html):
    """从正文中提取符合条件的图片绝对地址列表。"""
    soup = BeautifulSoup(html, "html.parser")
    content = find_content_node(soup)
    urls = []
    seen = set()
    for img in content.find_all("img"):
        src = None
        for attr in IMG_ATTRS:
            v = img.get(attr)
            if v:
                src = v.strip()
                break
        if not src or is_bad_url(src):
            continue
        if node_has_bad_feature(img):
            continue
        abs_url = urljoin(url, src)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        urls.append(abs_url)
    return urls


def download_and_convert(url, save_path, timeout=25):
    """下载图片字节并转为 JPG 保存，失败返回 None。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": url,
    })
    resp = session.get(url, timeout=timeout, verify=False, stream=True)
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "").lower()
    if "text" in ctype or "html" in ctype:
        return None
    img = Image.open(resp.raw)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    img.save(save_path, "JPEG", quality=90)
    return save_path


def main():
    parser = argparse.ArgumentParser(description="提取网页/公众号正文图片并保存为 JPG")
    parser.add_argument("--url", required=True, help="网页或公众号文章链接")
    parser.add_argument("--output", "-o", default="extracted_images", help="保存文件夹（默认 extracted_images）")
    args = parser.parse_args()

    print(f"[1/3] 抓取页面: {args.url}")
    html = fetch_html(args.url)

    print("[2/3] 定位正文并筛选图片...")
    urls = collect_image_urls(args.url, html)
    if not urls:
        print("未在页面正文中找到可用图片。")
        return 1

    os.makedirs(args.output, exist_ok=True)
    saved = 0
    failed = []
    print(f"[3/3] 共发现 {len(urls)} 张图片，开始下载转换...")
    for i, img_url in enumerate(urls, 1):
        save_path = os.path.join(args.output, f"{i:03d}.jpg")
        try:
            download_and_convert(img_url, save_path)
            saved += 1
            print(f"  已保存: {save_path}")
        except Exception as e:  # noqa: BLE001
            failed.append(img_url)
            print(f"  失败: {img_url} ({e})", file=sys.stderr)

    print(f"\n完成: 成功 {saved} 张，失败 {len(failed)} 张。")
    print(f"保存目录: {os.path.abspath(args.output)}")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
