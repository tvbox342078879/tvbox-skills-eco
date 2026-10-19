#!/usr/bin/env python3
"""轻量自检：SKILL.md 存在性 + frontmatter 完整性（95 配额填装器生成）"""
import re
from pathlib import Path


def check() -> int:
    md = Path(__file__).parent / 'SKILL.md'
    assert md.exists(), 'SKILL.md missing'
    c = md.read_text(encoding='utf-8', errors='replace')
    for key in ('slug', 'displayName', 'description'):
        assert re.search(rf'^{key}:', c, re.M), f'frontmatter {key} missing'
    print('selftest OK')
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
