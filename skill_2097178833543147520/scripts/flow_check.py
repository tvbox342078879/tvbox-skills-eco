# -*- coding: utf-8 -*-
"""剪映/CapCut 标准流程清单 — 交付前逐项勾选"""
CHECKS = [
  "成片用途/横竖屏/时长确认",
  "字幕准确无错别字",
  "人声清晰背景不过响",
  "节奏/转场不生硬",
  "导出分辨率码率匹配平台"
]
def main():
    print(f"{'='*46}\n  剪映/CapCut 交付前自检清单\n{'='*46}")
    ok = True
    for i, c in enumerate(CHECKS, 1):
        print(f"  [ ] {i}. {c}")
    print("\n逐项完成后，把成功案例写回经验库 experience-base.md。")
if __name__ == "__main__":
    main()
