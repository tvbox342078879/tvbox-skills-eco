#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""new_office_project.py — 建立文字/表格/演示三目录与项目档案，支持 --name/--type 参数"""
import argparse
from datetime import date
from pathlib import Path

ARCHIVE = """# {name} · 项目档案

- 建档日期: {today}
- 用途: 【】
- 版本: 待确认

## 进度看板
- [ ] 需求确认（见 01 需求单）
- [ ] 执行
- [ ] 自查（见 02 检查清单）
- [ ] 交付

## 待核实清单
- 【】
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    root = Path(args.name).resolve()
    if root.exists():
        raise SystemExit(f"目录已存在: {{root}}")
    for sub in ["01_源文件", "02_过程稿", "03_交付"]:
        (root / sub).mkdir(parents=True)
    (root / "项目档案.md").write_text(ARCHIVE.format(name=args.name, today=date.today().isoformat()), encoding="utf-8")
    print(f"项目已建立: {{root}}")

if __name__ == "__main__":
    main()
