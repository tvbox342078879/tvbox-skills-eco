#!/usr/bin/env python3
"""
微信公众号封面图生成器 v2.0
- 支持底图叠加（优先使用用户提供的底图）
- 支持纯渐变背景（兼容旧版）
- 900×383 px（2.35:1 标准比例）
- 主标题单行，副标题单行，绝不换行，字号自适应
"""

from PIL import Image, ImageDraw, ImageFont
import os


def create_gradient_background(width, height, color1, color2):
    """创建垂直渐变背景"""
    gradient = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(gradient)
    for y in range(height):
        ratio = y / height
        r = int(color1[0] + (color2[0] - color1[0]) * ratio)
        g = int(color1[1] + (color2[1] - color1[1]) * ratio)
        b = int(color1[2] + (color2[2] - color1[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return gradient


def draw_tech_grid(draw, width, height, opacity=0.08):
    """科技感网格"""
    gc = (30, 60, 100, int(255 * opacity))
    for x in range(0, width, 50):
        draw.line([(x, 0), (x, height)], fill=gc, width=1)
    for y in range(0, height, 50):
        draw.line([(0, y), (width, y)], fill=gc, width=1)


def draw_dots_pattern(draw, width, height, opacity=0.10):
    """点阵图案"""
    dc = (40, 80, 140, int(255 * opacity))
    for x in range(0, width, 30):
        for y in range(0, height, 30):
            offset = 15 if (y // 30) % 2 else 0
            draw.ellipse([x + offset - 2, y - 2, x + offset + 2, y + 2], fill=dc)


def draw_text_with_stroke(draw, xy, text, font, fill, stroke_color, stroke_width=2):
    """带描边的文字（白色描边提升在任意底图上的可读性）"""
    # 描边
    for dx in range(-stroke_width, stroke_width + 1, stroke_width):
        for dy in range(-stroke_width, stroke_width + 1, stroke_width):
            if dx == 0 and dy == 0:
                continue
            draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=stroke_color, anchor="mm")
    # 实心
    draw.text(xy, text, font=font, fill=fill, anchor="mm")


def text_size(text, font):
    """计算文本宽高（兼容新版 PIL getbbox）"""
    bb = font.getbbox(text)
    return bb[2] - bb[0], bb[3] - bb[1]


def find_single_line_font(text, font_path, max_w, max_s=80, min_s=22):
    """
    强制单行：找到能满足 max_w 约束的最大字号。
    返回 (size, font_obj)。
    """
    for s in range(max_s, min_s - 1, -1):
        font = ImageFont.truetype(font_path, s)
        if text_size(text, font)[0] <= max_w:
            return s, font
    # 最小字号兜底
    font = ImageFont.truetype(font_path, min_s)
    return min_s, font


def find_font_windows():
    """Windows 字体搜索"""
    font_dirs = [
        "C:/Windows/Fonts/",
        os.path.expanduser("~") + "/AppData/Local/Microsoft/Windows/Fonts/",
    ]
    font_names = [
        "NotoSansCJK-Medium.ttc",
        "NotoSansCJK-Bold.ttc",
        "MicrosoftYaHei.ttf",
        "msyh.ttc",
        "SimHei.ttf",
        "STHeiti-Medium.ttc",
    ]
    for d in font_dirs:
        if os.path.exists(d):
            for fn in font_names:
                fp = os.path.join(d, fn)
                if os.path.exists(fp):
                    return fp
    return None


def find_font_mac():
    """macOS 字体搜索"""
    font_dirs = [
        "/System/Library/Fonts/",
        "/Library/Fonts/",
        os.path.expanduser("~") + "/Library/Fonts/",
    ]
    font_names = [
        "NotoSansCJK-Medium.ttc",
        "NotoSansCJK-Bold.ttc",
        "Hiragino Sans GB.ttc",
        "STHeiti Light.ttc",
    ]
    for d in font_dirs:
        if os.path.exists(d):
            for fn in font_names:
                fp = os.path.join(d, fn)
                if os.path.exists(fp):
                    return fp
    return None


def find_font_linux():
    """Linux 字体搜索"""
    font_dirs = [
        "/usr/share/fonts/",
        "/usr/local/share/fonts/",
    ]
    font_names = [
        "google-noto-cjk/NotoSansCJK-Medium.ttc",
        "google-noto-cjk/NotoSansCJK-Bold.ttc",
        "google-droid/DroidSansFallback.ttf",
        "noto-cjk/NotoSansCJK-Medium.ttc",
    ]
    for d in font_dirs:
        if os.path.exists(d):
            for fn in font_names:
                fp = os.path.join(d, fn)
                if os.path.exists(fp):
                    return fp
    return None


def find_font():
    """跨平台字体搜索"""
    if os.name == 'nt':
        return find_font_windows()
    elif os.name == 'posix' and os.uname().sysname == 'Darwin':
        return find_font_mac()
    else:
        return find_font_linux()


def generate_wechat_cover(main_title, sub_title, output_path,
                           bg_color=None,
                           bg_image=None,
                           font_path=None):
    """
    生成微信公众号封面图

    参数:
        main_title  : 主标题文本（强制单行，超宽自动缩字号）
        sub_title   : 副标题文本（强制单行，可为 None）
        output_path : 输出文件路径（.png）
        bg_color    : 背景色（hex），当 bg_image=None 时生效
        bg_image    : 底图路径（.png/.jpg），优先使用
        font_path   : 字体路径，None → 自动找系统 CJK 字体
    """
    # ── 画布常量 ─────────────────────────────────────────
    W, H = 900, 383
    SAFE_W, SAFE_H = 600, 337
    SAFE_X = (W - SAFE_W) // 2
    SAFE_Y = (H - SAFE_H) // 2

    # ── 字体路径 ─────────────────────────────────────────
    if font_path is None:
        font_path = find_font()

    # ── 字号计算（强制单行）────────────────────────────────
    main_max_w = int(SAFE_W * 0.95)
    sub_max_w  = int(SAFE_W * 0.85)

    if font_path and os.path.exists(font_path):
        main_size, main_font = find_single_line_font(
            main_title, font_path, main_max_w, max_s=80, min_s=24)
        sub_size  = int(main_size * 0.82)
        sub_size  = max(22, min(sub_size, main_size - 4))
        sub_size, sub_font = find_single_line_font(
            sub_title, font_path, main_max_w, max_s=sub_size, min_s=22) if sub_title else (0, None)
    else:
        print("⚠️ 未找到中文字体，使用默认字体")
        main_font = sub_font = ImageFont.load_default()
        main_size = sub_size = 14

    # ── 构建背景 ─────────────────────────────────────────
    if bg_image and os.path.exists(bg_image):
        image = Image.open(bg_image).convert('RGB')
        if image.size != (W, H):
            image = image.resize((W, H), Image.LANCZOS)
    elif bg_color:
        bg_rgb  = tuple(int(bg_color[i:i+2], 16) for i in (1, 3, 5))
        color2  = tuple(min(255, c + 30) for c in bg_rgb)
        image   = create_gradient_background(W, H, bg_rgb, color2)
        # 科技图层
        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        draw_tech_grid(od, W, H, opacity=0.08)
        draw_dots_pattern(od, W, H, opacity=0.10)
        image = Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')
    else:
        image = create_gradient_background(W, H, (13, 27, 42), (43, 67, 72))

    # ── 垂直布局计算 ──────────────────────────────────────
    main_h = text_size(main_title, main_font)[1]
    sub_h  = text_size(sub_title, sub_font)[1] if sub_title and sub_font else 0
    gap    = 22
    total_h = main_h + gap + sub_h

    text_top = SAFE_Y + (SAFE_H - total_h) // 2

    print(f"✅ 字体：{font_path}")
    print(f"   主标题：{main_size}px，宽度={text_size(main_title, main_font)[0]}")
    print(f"   副标题：{sub_size}px，宽度={text_size(sub_title, sub_font)[0] if sub_title and sub_font else 0}")
    print(f"   总高={total_h}px，安全区Y=[{SAFE_Y}, {SAFE_Y+SAFE_H}]")

    draw = ImageDraw.Draw(image)

    # ── 文字叠图 ─────────────────────────────────────────
    # 主标题（白色 + 黑色描边）
    draw.text((W // 2, text_top + main_h // 2),
              main_title, font=main_font, fill=(255, 255, 255), anchor="mm",
              stroke_width=3, stroke_fill=(0, 0, 0))

    # 副标题（浅蓝白 + 黑色描边）
    if sub_title and sub_font:
        cur_y = text_top + main_h + gap
        draw.text((W // 2, cur_y + sub_h // 2),
                  sub_title, font=sub_font, fill=(200, 220, 255), anchor="mm",
                  stroke_width=2, stroke_fill=(0, 0, 0))

    # ── 保存 PNG ──────────────────────────────────────────
    from pathlib import Path
    parent = Path(output_path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    image = image.convert('RGB')
    image.save(output_path, 'PNG')
    print(f"✅ 已保存：{output_path}")
    return output_path


# ── 测试 ─────────────────────────────────────────────
if __name__ == "__main__":
    output = "./output/test_cover.png"
    generate_wechat_cover(
        main_title  = "我用Google的时间序列模型做了个测试",
        sub_title   = "结果出乎意料",
        output_path = output
    )
    print(f"输出：{output}")