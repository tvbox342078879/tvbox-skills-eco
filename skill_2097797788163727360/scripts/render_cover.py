#!/usr/bin/env python3
"""
render_cover.py — CSDN 封面图程序化渲染器（Pillow + 中文字体）
用法：
  python render_cover.py --title "主标题" --subtitle "副标题" [--style tech-flat] [--accent "#6366f1"] --output cover.png

特点：
  - 中文标题 100% 清晰准确（Pillow + 系统中文字体）
  - 颜色精确控制（不漂移）
  - 零积分消耗（纯本地渲染）
  - 可选叠加 AI 生成的背景图（--bg-image）
"""

import argparse
import math
import os
import sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter


# ── 样式配置 ──────────────────────────────────────────────
STYLES = {
    "tech-flat": {
        "bg_top": "#667eea",
        "bg_bottom": "#764ba2",
        "text_color": "#ffffff",
        "sub_color": "#e0e7ff",
        "tag_color": "#c7d2fe",
        "deco_color": "rgba(255,255,255,0.08)",
        "glow": True,
    },
    "gradient-burst": {
        "bg_top": "#f97316",
        "bg_bottom": "#dc2626",
        "text_color": "#ffffff",
        "sub_color": "#fef3c7",
        "tag_color": "#fde68a",
        "deco_color": "rgba(255,255,255,0.10)",
        "glow": True,
    },
    "cyan-tech": {
        "bg_top": "#06b6d4",
        "bg_bottom": "#0891b2",
        "text_color": "#ffffff",
        "sub_color": "#cffafe",
        "tag_color": "#a5f3fc",
        "deco_color": "rgba(255,255,255,0.08)",
        "glow": True,
    },
    "emerald-clean": {
        "bg_top": "#10b981",
        "bg_bottom": "#059669",
        "text_color": "#ffffff",
        "sub_color": "#d1fae5",
        "tag_color": "#a7f3d0",
        "deco_color": "rgba(255,255,255,0.08)",
        "glow": True,
    },
}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """#rrggbb → (r,g,b)"""
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def find_chinese_font(size: int = 48) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    查找可用的中文字体。
    优先级：微软雅黑 > 思源黑体 > 黑体 > SimHei > 系统默认
    """
    candidates = [
        # Windows
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/msyhbd.ttc",      # 微软雅黑粗体
        "C:/Windows/Fonts/simhei.ttf",      # 黑体
        "C:/Windows/Fonts/simsun.ttc",      # 宋体
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        # Linux
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # fallback
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def draw_gradient(draw: ImageDraw.ImageDraw, w: int, h: int, top: str, bottom: str):
    """垂直渐变背景"""
    r1, g1, b1 = hex_to_rgb(top)
    r2, g2, b2 = hex_to_rgb(bottom)
    for y in range(h):
        ratio = y / max(h - 1, 1)
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def draw_deco_lines(draw: ImageDraw.ImageDraw, w: int, h: int, color_hex: str):
    """装饰性几何线条/网格/光点"""
    # 解析颜色：支持 "#rrggbb" 或 "rgba(r,g,b,a)"
    if color_hex.startswith("rgba"):
        inner = color_hex.replace("rgba(", "").replace(")", "")
        parts = [p.strip() for p in inner.split(",")]
        base = (int(parts[0]), int(parts[1]), int(parts[2]))
    else:
        base = hex_to_rgb(color_hex)
    alpha = 30  # 固定低透明度
    c = (*base, alpha) if len(base) == 3 else base

    # 右上角放射线
    cx, cy = int(w * 0.85), int(h * 0.15)
    import random
    rng = random.Random(42)  # 固定种子保证风格一致
    for _ in range(12):
        angle = rng.uniform(0, math.pi * 2)
        length = rng.randint(80, 300)
        x2 = cx + int(math.cos(angle) * length)
        y2 = cy + int(math.sin(angle) * length)
        draw.line([(cx, cy), (x2, y2)], fill=(*base[:3],), width=1)

    # 散落光点
    for _ in range(25):
        px = rng.randint(0, w)
        py = rng.randint(0, h)
        r = rng.randint(2, 5)
        draw.ellipse([px - r, py - r, px + r, py + r], fill=(*base[:3], 120))

    # 底部波浪线（用多段直线近似）
    wave_y = int(h * 0.88)
    points = []
    for x in range(0, w + 20, 20):
        wy = wave_y + int(8 * math.sin(x * 0.03))
        points.append((x, wy))
    if len(points) >= 2:
        draw.line(points, fill=(*base[:3], 60), width=2)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """按像素宽度自动换行（中文友好）"""
    lines: list[str] = []
    line = ""
    for ch in text:
        test = line + ch
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = ch
    if line:
        lines.append(line)
    return lines


def render_cover(
    title: str,
    subtitle: str = "",
    style: str = "tech-flat",
    accent: str = "#6366f1",
    output: str = "cover.png",
    bg_image: str | None = None,
    tag: str = "CSDN · 技术深度",
    font_path: str | None = None,
) -> str:
    """生成封面图，返回输出文件路径"""

    W, H = 1280, 720
    cfg = STYLES.get(style, STYLES["tech-flat"])

    # 如果用户传了 accent，覆盖样式的默认色
    if accent and accent.startswith("#"):
        # 用 accent 构造渐变：accent → 加深版
        ar, ag, ab = hex_to_rgb(accent)
        darker = (max(ar - 40, 0), max(ag - 40, 0), max(ab - 40, 0))
        cfg["bg_top"] = accent
        cfg["bg_bottom"] = "#%02x%02x%02x" % darker

    # 创建画布
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # 背景
    if bg_image and os.path.exists(bg_image):
        # 叠加 AI 背景图（缩放+裁剪到 1280×720）
        bg = Image.open(bg_image).convert("RGB")
        bg = bg.resize((W, H), Image.LANCZOS)
        # 半透明叠一层渐变以保证文字可读性
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 60))
        img.paste(bg, (0, 0))
        img = img.convert("RGB")
        draw = ImageDraw.Draw(img)
    else:
        # 纯渐变背景
        draw_gradient(draw, W, H, cfg["bg_top"], cfg["bg_bottom"])

    # 装饰线条
    draw_deco_lines(draw, W, H, cfg["deco_color"])

    # ── 文字层 ──
    title_font_size = 72 if len(title) <= 12 else 58
    sub_font_size = 32
    tag_font_size = 22

    if font_path and os.path.exists(font_path):
        title_font = ImageFont.truetype(font_path, title_font_size)
        sub_font = ImageFont.truetype(font_path, sub_font_size)
        tag_font = ImageFont.truetype(font_path, tag_font_size)
    else:
        title_font = find_chinese_font(title_font_size)
        sub_font = find_chinese_font(sub_font_size)
        tag_font = find_chinese_font(tag_font_size)

    text_color = hex_to_rgb(cfg["text_color"])
    sub_color = hex_to_rgb(cfg["sub_color"])
    tag_color = hex_to_rgb(cfg["tag_color"])

    # 主标题（居中偏下，视觉重心稳定）
    title_lines = wrap_text(title, title_font, int(W * 0.82))
    line_height = title_font_size + 14
    total_title_h = len(title_lines) * line_height
    start_y = (H - total_title_h) // 2 + 30  # 稍偏下

    for i, line in enumerate(title_lines):
        bbox = title_font.getbbox(line)
        tw = bbox[2] - bbox[0]
        tx = (W - tw) // 2
        ty = start_y + i * line_height
        # 描边效果（让文字在复杂背景上更清晰）
        for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2)]:
            draw.text((tx + dx, ty + dy), line, fill=(20, 20, 30), font=title_font)
        draw.text((tx, ty), line, fill=text_color, font=title_font)

    # 副标题
    if subtitle:
        sub_lines = wrap_text(subtitle, sub_font, int(W * 0.80))
        sub_y = start_y + total_title_h + 24
        for i, line in enumerate(sub_lines):
            bbox = sub_font.getbbox(line)
            sw = bbox[2] - bbox[0]
            sx = (W - sw) // 2
            draw.text((sx, sub_y + i * 44), line, fill=sub_color, font=sub_font)

    # 右下角标签
    if tag:
        tbbox = tag_font.getbbox(tag)
        tw = tbbox[2] - tbbox[0]
        th = tbbox[3] - tbbox[1]
        pad_x, pad_y = 16, 8
        tag_x = W - tw - pad_x - 30
        tag_y = H - th - pad_y - 20
        # 圆角矩形背景
        rx, ry = tag_x - pad_x, tag_y - pad_y
        rw, rh = tw + pad_x * 2, th + pad_y * 2
        draw.rounded_rectangle([rx, ry, rx + rw, ry + rh], radius=12, fill=(*tag_color,))
        draw.text((tag_x, tag_y), tag, fill=text_color, font=tag_font)

    # 保存
    img.save(output, "PNG", optimize=True)
    size_kb = os.path.getsize(output) / 1024
    print(f"OK: {output} ({W}x{H}, {size_kb:.0f}KB)")
    return output


def main():
    parser = argparse.ArgumentParser(description="CSDN 封面图程序化渲染器")
    parser.add_argument("--title", required=True, help="文章标题（必填，会显示在封面大字）")
    parser.add_argument("--subtitle", default="", help="副标题/关键词（小字，显示在标题下方）")
    parser.add_argument("--style", default="tech-flat", choices=list(STYLES.keys()), help="视觉风格")
    parser.add_argument("--accent", default="#6366f1", help="主色调（#rrggbb 格式，精确生效）")
    parser.add_argument("--output", default="cover.png", help="输出文件路径")
    parser.add_argument("--bg-image", default=None, help="可选：AI 生成的背景图路径（文字会叠在上面）")
    parser.add_argument("--tag", default="CSDN · 技术深度", help="右下角标签文字")
    parser.add_argument("--font", default=None, help="中文字体文件路径（默认自动查找系统字体）")
    args = parser.parse_args()

    render_cover(
        title=args.title,
        subtitle=args.subtitle,
        style=args.style,
        accent=args.accent,
        output=args.output,
        bg_image=args.bg_image,
        tag=args.tag,
        font_path=args.font,
    )


if __name__ == "__main__":
    main()
