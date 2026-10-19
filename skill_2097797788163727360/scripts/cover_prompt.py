#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cover_prompt.py — 为 CSDN 技术文章生成 ImageGen 封面提示词。

用法:
  python scripts/cover_prompt.py --title "标题" --keywords "LoRA,蒸馏,低算力" \
      [--style tech-flat|dark-tech|gradient-bright] [--accent "#6366f1"]

输出:
  1) 可直接丢给 ImageGen 的中文提示词
  2) 推荐尺寸 1280x720 (16:9)
  3) 设计要点

设计原则（已查证 CSDN 社区共识）:
  - 封面是上传到 CSDN「封面图」字段的独立图片，不属于正文，不违反 csdn_convert 纯文本铁律。
  - 尺寸 1280x720 (16:9)，文件 <= 2MB；顶部 40px 预留安全区（APP 列表可能遮挡）。
  - 不写文字：AI 出图文字不可靠（中文尤其糊），CSDN 标题另显示在封面下方，图上留白即可。
"""
import argparse

STYLES = {
    "tech-flat": "扁平化科技插画风格，简洁几何图形与发光线条，留白充足、清爽专业",
    "dark-tech": "深色科技风，深蓝渐变背景叠加网格装饰，霓虹高光与数据流光效",
    "gradient-bright": "明亮渐变背景，轻量玻璃拟态卡片，现代通透、积极向上",
}


def build_prompt(title, keywords, style, accent):
    kw = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
    metaphor = "、".join(kw) if kw else title
    sty = STYLES.get(style, STYLES["tech-flat"])
    accent_txt = f"主色调 {accent}，" if accent else "科技蓝紫主色调，"
    prompt = (
        f"一张 CSDN 技术博客封面图，1280x720，16:9 横幅比例。"
        f"{sty}。{accent_txt}画面中央用简洁图形与图标隐喻「{metaphor}」这一主题，"
        f"点缀发光粒子、网格线与流动的数据线，整体专业、通透、高点击率；"
        f"顶部 40px 预留安全区不放置关键元素，缩略图小图也能一眼识别主题。"
        f"no text, no watermark, no letters, no words, clean composition."
    )
    return prompt


def main():
    ap = argparse.ArgumentParser(description="生成 CSDN 封面 ImageGen 提示词")
    ap.add_argument("--title", required=True, help="文章标题")
    ap.add_argument("--keywords", default="", help="主题关键词，逗号分隔，如 LoRA,蒸馏,低算力")
    ap.add_argument("--style", default="tech-flat",
                    choices=list(STYLES), help="视觉风格预设")
    ap.add_argument("--accent", default="", help="主色，如 #6366f1")
    args = ap.parse_args()

    prompt = build_prompt(args.title, args.keywords, args.style, args.accent)
    print("=== CSDN 封面提示词 ===")
    print(prompt)
    print()
    print("推荐尺寸: 1280x720 (16:9) | 文件 <= 2MB | 顶部 40px 安全区")
    print("注意: 不嵌文字（AI 出图文字不可靠）；CSDN 标题另显示在封面下方，无需图上写字。")


if __name__ == "__main__":
    main()
