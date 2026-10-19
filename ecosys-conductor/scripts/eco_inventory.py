#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eco_inventory.py —— 全技能清单正本（S174）

## 为什么必须新建这个文件

用户原话（本模块唯一依据）：
   「你做的是全生态检测，覆盖大生态，小生态和各技能了没，我就说为何看不到技能」

查下来，根因不是某个技能写坏了，而是**整个生态只有"成员/非成员"二分**：
  · `eco_skills.json` 是一维名单，只登记 12 个
  · `conductor._SKILL_INTENTS` 是手写字面量，也只 12 个
  · 而所有触发词/路由/孤儿词检查都在 `for sk in _SKILL_INTENTS` 里循环
  → 磁盘上另外 54 个技能，**整套检测机制根本遍历不到它们**。
  不是"检查了没通过"，是"压根没进检查名单"——这种漏法最安静，因为它不报错。

## 三层模型（依据可查，不靠猜）

  big-eco     大生态：`eco_skills.json` 明确登记的成员（当前 12 个）
              我们建的、要为它守三件套/探针/路由的全责技能
  platform    平台托管：frontmatter 有 `display_name` / `category`（当前 43 个）
              商店下发的技能。**不归我们改**（改了下次更新被覆盖），
              但要能被生态感知到——否则用户说"做个PPT"我们一脸茫然
  orphan      无归属：既非生态成员，也没有平台元数据（当前 9 个）
              工具/连接器/路由层。这类最容易变成"看不见"：既没有 owner 认领，
              也没有平台帮它加载

  no_skill    ！连 SKILL.md 都没有的目录（当前 2 个）——不是技能，是残留，该清

## 判据为什么这样定

  · `display_name` / `category` 是平台 schema 的**客观标记**，不是我们的推测。
    用它区分"平台托管"与"无归属"，比按名字猜（wechat-* 算谁的）可靠。
  · 目录名常是 `skill_<哈希>`，与真实技能名不符（45/66 如此）。
    所以**一律以 frontmatter 的 `name` 为准，目录名只当路径**。
  · 读失败必须报，不得折叠成"没有"（M-R60 三态）。

## 用法

    python3 eco_inventory.py                 # 打印分层清单
    python3 eco_inventory.py --json          # 机器可读
    python3 eco_inventory.py --layer big-eco # 只看某层

被 conductor / inspect_engine 共同 import（M-R38：一份实现，多处派生）。
"""
from __future__ import annotations

import json
import os
import re
import sys

SKILL_ROOT = '/root/.codebuddy/skills'
ECO_FILE = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'eco_skills.json')

BIG_ECO = 'big-eco'
PLATFORM = 'platform'
ORPHAN = 'orphan'
NO_SKILL = 'no_skill'

LAYER_LABEL = {
    BIG_ECO: '大生态（我们全责）',
    PLATFORM: '平台托管（不改它，但要感知到）',
    ORPHAN: '无归属（无 owner，最易变孤岛）',
    NO_SKILL: '无 SKILL.md（残留，该清）',
}


def _read_eco_members():
    """读生态成员正本。读失败 → 返回 (None, 原因)，调用方**不得**当空名单。"""
    try:
        with open(ECO_FILE, encoding='utf-8') as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, f'eco_skills.json 不存在（{ECO_FILE}）'
    except Exception as e:
        return None, f'eco_skills.json 读取失败（{type(e).__name__}: {e}）'
    names = d.get('eco_skills') if isinstance(d, dict) else d
    if not isinstance(names, list) or not names:
        return None, 'eco_skills.json 无 eco_skills 列表或为空'
    return set(names), ''


def _frontmatter(path):
    """取 SKILL.md 的 frontmatter 文本（不含 --- 行）。没有 frontmatter 返回 ''。"""
    try:
        s = open(path, encoding='utf-8', errors='replace').read()
    except Exception:
        return ''
    m = re.search(r'^---\s*\n(.*?)\n---', s, re.S | re.M)
    return m.group(1) if m else ''


def _field(fm, key):
    m = re.search(rf'^{key}:\s*(.+?)$', fm, re.M)
    return m.group(1).strip().strip('"\'') if m else ''


def scan(root=None):
    """扫全量技能，返回 (items, warnings)。

    root: 可覆盖技能根目录。**探针注入必需**——S174 自造缺陷实录：
      首版把根目录写死 SKILL_ROOT，而信号源28 的探针是在**临时目录**造一个未纳管技能
      再看它会不会被报。写死之后 scan 永远扫真目录，探针造的东西压根看不见，
      探针当场从"有效"变成"捕获=否"。
      → 这类"改功能顺手弄坏探针"的回归，只有跑 signal-check 才现形。

    items: [{dir, name, layer, has_skill_md, n_triggers, display_name, category}]
    warnings: ['读失败/无名单' 之类的如实说明]，不吞
    """
    root = root or SKILL_ROOT
    members, warn = _read_eco_members()
    warnings = [warn] if warn else []
    items = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)      # ★ 必须跟 root 走，不能写死 SKILL_ROOT（探针注入用）
        if not os.path.isdir(p):
            continue
        md = os.path.join(p, 'SKILL.md')
        if not os.path.isfile(md):
            items.append({'dir': d, 'name': '', 'layer': NO_SKILL, 'has_skill_md': False,
                          'n_triggers': 0, 'display_name': '', 'category': ''})
            continue
        fm = _frontmatter(md)
        name = _field(fm, 'name') or d
        disp = _field(fm, 'display_name')
        cat = _field(fm, 'category')
        # 触发词计数：沿用 conductor._skill_desc_terms 的口径（显式句式）
        ntrg = 0
        dm = re.search(r'description:\s*["\']?(.*?)(?:\n[A-Za-z_-]+:|\Z)', fm, re.S)
        if dm:
            desc = dm.group(1).replace('\n', ' ')
            parts = []
            for mm in re.finditer(r'触发词[：:]\s*([^。；\n]+)', desc):
                parts.extend(re.split(r'\s*[/、，,]\s*', mm.group(1)))
            ntrg = len([x for x in parts if 2 <= len(x.strip()) <= 14])
        if members is not None and d in members:
            layer = BIG_ECO
        elif disp or cat:
            layer = PLATFORM
        else:
            layer = ORPHAN
        items.append({'dir': d, 'name': name, 'layer': layer, 'has_skill_md': True,
                      'n_triggers': ntrg, 'display_name': disp, 'category': cat})
    return items, warnings


def by_layer(items):
    out = {}
    for it in items:
        out.setdefault(it['layer'], []).append(it)
    return out


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    items, warnings = scan()
    for w in warnings:
        print(f'⚠️ {w}', file=sys.stderr)
    if '--json' in argv:
        print(json.dumps(items, ensure_ascii=False, indent=1))
        return 0
    only = None
    if '--layer' in argv:
        i = argv.index('--layer')
        only = argv[i + 1] if i + 1 < len(argv) else None
    groups = by_layer(items)
    print(f'全量 {len(items)} 个目录'
          + (f'（⚠️ 生态名单读失败：{warnings[0]}）' if warnings else ''))
    for lay in (BIG_ECO, PLATFORM, ORPHAN, NO_SKILL):
        sub = groups.get(lay, [])
        if only and lay != only:
            continue
        print(f'\n【{LAYER_LABEL[lay]}】{len(sub)} 个')
        for it in sub:
            mark = f"触发词{it['n_triggers']}" if it['has_skill_md'] else '—'
            print(f'   {it["name"] or it["dir"]:36s} {it["dir"][:34]:36s} {mark}')
    return 0


def dispatch(argv=None):
    """入口契约：tvbox conductor inventory"""
    return main(argv)


if __name__ == '__main__':
    sys.exit(main())
