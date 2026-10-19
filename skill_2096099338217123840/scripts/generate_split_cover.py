#!/usr/bin/env python3
"""Generate a 900x383 WeChat cover with text on the left and a diagram on the right."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from generate_cover import find_font, text_size


def _font(font_path, size):
    return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()


def _fit_font(text, font_path, max_width, start, minimum):
    for size in range(start, minimum - 1, -1):
        font = _font(font_path, size)
        if text_size(text, font)[0] <= max_width:
            return font
    return _font(font_path, minimum)


def _draw_icon(draw, kind, box, color):
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    if kind == "chart":
        for offset, bar_height in ((-18, 14), (0, 25), (18, 36)):
            draw.rounded_rectangle((cx + offset - 6, cy + 17 - bar_height, cx + offset + 6, cy + 17), radius=3, fill=color)
    elif kind == "layers":
        for offset in (-8, 0, 8):
            draw.polygon((cx, cy - 18 + offset, cx + 25, cy - 5 + offset, cx, cy + 8 + offset, cx - 25, cy - 5 + offset), outline=color)
    elif kind == "memory":
        draw.rounded_rectangle((cx - 21, cy - 19, cx + 21, cy + 19), radius=5, outline=color, width=2)
        draw.rounded_rectangle((cx - 10, cy - 9, cx + 10, cy + 9), radius=2, outline=color, width=2)
        for offset in (-12, 0, 12):
            draw.line((cx + offset, cy - 25, cx + offset, cy - 19), fill=color, width=2)
            draw.line((cx + offset, cy + 19, cx + offset, cy + 25), fill=color, width=2)
    else:
        for offset in (-12, 0, 12):
            draw.rounded_rectangle((cx - 24, cy + offset - 4, cx + 24, cy + offset + 4), radius=4, outline=color, width=2)
            draw.ellipse((cx + 14, cy + offset - 2, cx + 18, cy + offset + 2), fill=color)


def generate_split_infographic_cover(
    model_name,
    headline,
    subtitle,
    output_path,
    center_label="27B",
    diagram_labels=("视觉", "代码", "长文", "服务"),
    font_path=None,
):
    """Generate a reusable left-copy/right-diagram technology cover."""
    width, height = 900, 383
    font_path = font_path or find_font()
    image = Image.new("RGBA", (width, height), (244, 239, 230, 255))
    draw = ImageDraw.Draw(image, "RGBA")

    # Warm editorial palette: cream paper, charcoal ink, terracotta focus, ochre detail.
    charcoal = (36, 34, 31, 255)
    terracotta = (217, 119, 87, 255)
    ochre = (200, 154, 69, 255)
    paper_line = (92, 82, 68, 80)

    eyebrow_font = _font(font_path, 16)
    title_font = _fit_font(model_name, font_path, 470, 55, 38)
    headline_font = _fit_font(headline, font_path, 470, 38, 28)
    subtitle_font = _fit_font(subtitle, font_path, 470, 20, 16)
    label_font = _font(font_path, 15)
    center_font = _fit_font(center_label, font_path, 105, 34, 24)

    draw.text((54, 58), "MODEL FIELD NOTE", font=eyebrow_font, fill=terracotta, anchor="la")
    draw.rectangle((54, 101, 59, 223), fill=terracotta)
    draw.text((78, 108), model_name, font=title_font, fill=charcoal, anchor="la")
    draw.text((78, 179), headline, font=headline_font, fill=charcoal, anchor="la")
    draw.text((78, 246), subtitle, font=subtitle_font, fill=(92, 82, 68, 255), anchor="la")
    draw.line((78, 294, 470, 294), fill=paper_line, width=1)
    draw.text((78, 315), "LOCAL DEPLOYMENT · 2026", font=eyebrow_font, fill=ochre, anchor="la")

    center = (704, 191)
    nodes = ((704, 75), (820, 191), (704, 307), (588, 191))
    kinds = ("chart", "layers", "memory", "service")
    for node in nodes:
        draw.line((center[0], center[1], node[0], node[1]), fill=(36, 34, 31, 120), width=2)
    for (cx, cy), kind, label in zip(nodes, kinds, diagram_labels):
        draw.rounded_rectangle((cx - 44, cy - 42, cx + 44, cy + 42), radius=18, fill=(244, 239, 230, 245), outline=(36, 34, 31, 160), width=2)
        _draw_icon(draw, kind, (cx - 32, cy - 29, cx + 32, cy + 13), charcoal)
        draw.text((cx, cy + 25), label, font=label_font, fill=(36, 34, 31, 220), anchor="mm")

    draw.ellipse((center[0] - 61, center[1] - 61, center[0] + 61, center[1] + 61), fill=terracotta, outline=charcoal, width=3)
    draw.ellipse((center[0] - 48, center[1] - 48, center[0] + 48, center[1] + 48), outline=(244, 239, 230, 150), width=1)
    draw.text(center, center_label, font=center_font, fill=(244, 239, 230, 255), anchor="mm")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, "PNG")
    print(f"已生成左文右图封面：{output}")
    return str(output)


if __name__ == "__main__":
    generate_split_infographic_cover(
        model_name="Qwen3.8-27B 发布",
        headline="性能调研及部署建议",
        subtitle="评分对比 · 版本差距 · 显存预算",
        center_label="27B",
        diagram_labels=("性能", "量化", "显存", "部署"),
        output_path="qwen3.8-27b-cover.png",
    )
