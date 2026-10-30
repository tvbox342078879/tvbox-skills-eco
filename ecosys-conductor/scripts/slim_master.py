#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slim_master.py —— SKILL/总册 瘦身工具（M-R77⑥：三法落地）

背景：M-R69 一直说"精简优先"，audit-growth 也一直提示 `slim_master.py`，
但**这个文件从未存在**（S135 全盘搜索确认）——规则写着、工具缺失，等于没落地。

本工具只做 M-R77⑥ 允许的三法，**不做第四种**（不改写内容、不压缩语义）：
  法① 同源抄本改索引 —— 同一内容别处有正本，SKILL 里只留「见 XXX」
  法② 重复表合并 —— 同一张表出现两次，删一处留指针
  法③ 论证型长节外置 docs/ —— 整节搬到 docs/manual_*.md，SKILL 留索引行

用法：
  python3 slim_master.py <技能名|总册> --diagnose
      只诊断：列出各节字节数、标出「可外置候选」（论证型/历史实证型）与「不可动」（铁律/命令表）
  python3 slim_master.py <技能名> --apply <节标题关键字> [--apply ...]
      执行法③外置：抽节 → 写 docs/manual_slim_<日期>.md → SKILL 留索引行 → 校验字节下降
  python3 slim_master.py <技能名> --verify
      校验：外置后索引行在位、外置文档非空、字节已降

设计约束（M-R77⑥ 后半句）：
  · 外置**必须有索引行**——否则模型不知道内容去哪了，等于删除
  · 校验**关键内容不丢**——按节内锚点词（会话号/规则号/命令名）核对是否出现在外置文档里
  · 永不自动执行 —— --diagnose 是默认，--apply 必须显式点名要外置的节
"""
import os, re, sys, json, datetime, shutil

SKILL_ROOT = '/root/.codebuddy/skills'
# S201 同口径：/workspace 下的旧总册已归档，人读总册现由铁律坐标明细承担
MASTER = '/root/.codebuddy/skills/ecosys-conductor/references/rules_铁律坐标明细.md'
REG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', 'rules_registry.json')

# 可外置的节特征（论证型 / 历史实证型 / 一次性读）：只看**标题**，不看正文。
# 教训（首版）：连正文一起匹配，`1.2 交付前自检` 因正文出现"复盘"被判为可外置——
# 它是每次必跑的执行面，外置=把闸门搬走。**判定必须只看标题**。
OFFLOAD_HINT = re.compile(
    r'实证|溯源|对比分析|历史|复盘四问|三次返工|踩坑细节|自举|双保险|坑谱系|方法论|六步|五步法')
# 绝不可外置（模型每次都需要的执行面）——优先级高于 OFFLOAD_HINT
KEEP_HINT = re.compile(
    r'铁律|命令|速查|坐标|流程|判据|触发词|入口|索引|自检|闸|必做|必过|格式|字段|表$|清单')


def _limits():
    try:
        th = json.load(open(REG, encoding='utf-8')).get('thresholds', {})
        return th.get('skill_max_bytes', 40960), th.get('skill_buffer_bytes', 35840)
    except Exception:
        return 40960, 35840


def _path_of(target):
    if target in ('总册', 'master', 'rules'):
        return MASTER
    p = os.path.join(SKILL_ROOT, target, 'SKILL.md')
    return p if os.path.exists(p) else None


def _sections(text, level='##'):
    """按指定级标题切节，返回 [(标题行, 正文, 起始偏移, 结束偏移)]。

    **S135 修复**：原实现 `^### ` 只匹配同级标题，遇到**更高级**标题（`## `）不截断，
    导致节体一路吞到下一个同级标题，甚至吞到文件末尾——`tvbox-inspect` 的「联动三层」
    实际 443 字节被算成 5.36KB（虚高 12 倍），并给出错误外置建议。
    正确语义：任何 **层级 ≤ 本级** 的标题都算本节结束（含文件尾）。
    """
    lv = level.strip().count('#')
    # 边界：任何 层级 ≤ lv 的标题（含更高级）都会终止当前节
    pat_b = re.compile(r'(?m)^(#{1,' + str(lv) + r'}\s.*)$')
    # 起点：只有 **恰好本级** 的标题才作为一个独立节的开始
    pat_s = re.compile(r'(?m)^(#{' + str(lv) + r'}\s.*)$')
    bounds = [m.start() for m in pat_b.finditer(text)]
    out = []
    for m in pat_s.finditer(text):
        st = m.start()
        en = next((b for b in bounds if b > st), len(text))
        out.append((m.group(1), text[st:en], st, en))
    return out


def diagnose(target):
    p = _path_of(target)
    if not p:
        print(f'❌ 找不到 {target}/SKILL.md（或 总册）')
        return 1
    hard, buf = _limits()
    raw = open(p, encoding='utf-8').read()
    size = os.path.getsize(p)
    print(f'📐 {target} ｜ {size/1024:.1f}KB（{size} 字节）｜ 硬上限 {hard/1024:.0f}KB ｜ 缓冲线 {buf/1024:.0f}KB')
    if size <= buf:
        print(f'   ✅ 已在缓冲线内（余量 {(buf-size)/1024:.1f}KB 到缓冲线）—— 无需瘦身')
    elif size <= hard:
        print(f'   ⚠️ 超缓冲线 {(size-buf)/1024:.1f}KB（未超硬上限，verify 会报 ⑦ 不过）')
    else:
        print(f'   ❌ 超硬上限 {(size-hard)/1024:.1f}KB —— audit-growth exit=1、自动建单')
    print()

    # 子节（###）优先；不足 3 条则回落到大节（##）——否则小技能会「清单为空」看不出结构
    subs = _sections(raw, '###')
    if len(subs) < 3:
        big = _sections(raw, '##')
        if big:
            subs = big
    OFFLOADED = re.compile(r'正本已外置|正本在|本节仅索引|见总册|见 `docs/')
    rows = []
    for title, body, st, en in subs:
        b = len(body.encode())
        done = bool(OFFLOADED.search(body))
        # 已外置的节不再列为候选——否则会误导人重复外置（S135 实测：8.16/8.14/8.3b 已外置仍被推荐）
        cand = bool(OFFLOAD_HINT.search(title)) and not done
        keep = bool(KEEP_HINT.search(title))
        rows.append((b, title.strip(), cand and not keep, keep, done))
    rows.sort(reverse=True)
    print('   【节清单】按字节降序（📦=可外置 ｜ 🔒=执行面锁定 ｜ ✅=已外置）')
    tot_cand = 0
    for b, t, cand, keep, done in rows:
        tag = '✅ 已外置' if done else ('🔒 锁定' if keep else ('📦 可外置候选' if cand else '   ·'))
        if cand and not keep:
            tot_cand += b
        print(f'   {b/1024:6.2f}KB  {tag:12s} {t[:56]}')
    print()
    print(f'   可外置候选合计 {tot_cand/1024:.1f}KB —— 全外置后约 {(size-tot_cand)/1024:.1f}KB')
    if tot_cand == 0:
        # 无候选时不得再给出「可降到缓冲线内」的结论——那是自相矛盾（S135 实测出现）
        if size <= buf:
            print('   ✅ 已达标且无可安全外置的节——这本就该这么厚，不要再动它')
        else:
            print('   ⚠️ 无外置候选但已超线：只能用法①（同源抄本改索引）或法②（重复表合并），'
                  '或考虑拆技能')
    elif size - tot_cand > buf:
        print(f'   ⚠️ 只靠法③不够（仍需 {(size-tot_cand)/1024:.1f}KB > 缓冲线 {buf/1024:.0f}KB），'
              f'还须用法①（同源抄本改索引）或法②（重复表合并）')
    else:
        print('   ✅ 外置候选足够，可降到缓冲线内')
    # ── 节内条目级诊断（S136 新增·收口 32ec6b4325b4）──────────────────────
    # 缺的是什么：索引制总册里最大的节常是「铁律/规则」类，被 KEEP_HINT 正确锁定
    #   （模型每次都要看），于是**节级**永远判"无可外置"。但这类节内部往往是
    #   几十条**互相独立**的条目（M-R1…M-R66 那种），条目级完全可以按需查阅。
    #   工具只看到「节」这一层，就永远说"这本就该这么厚"——判定没错，但**没给它
    #   第二种可能**：条目级外置（留索引行 + 条目正文进 docs/）。
    # 本条诊断不自动动手（改规则总册是高危操作），只回答「如果哪天必须再瘦，
    #   这座山的哪一面可以凿」——把"人工法"变成"有据可依的人工法"。
    try:
        rows2 = _sections(raw, '##')
        for title, body, _st, _en in rows2:
            b = len(body.encode())
            if b < 4096:            # 只看 >4KB 的大节，小节的条目级外置没意义
                continue
            items = re.findall(r'(?m)^\|?\s*(?:[-*]\s+)?\**((?:M-R|U-R|P-R|L)\d+)\**', body)
            # 表格行与列表项都算；去重保序
            seen, uniq = set(), []
            for it in items:
                if it not in seen:
                    seen.add(it); uniq.append(it)
            if len(uniq) >= 8:
                print(f'   🔍 节内条目级：{title.strip()[:40]} {b/1024:.2f}KB ｜ '
                      f'{len(uniq)} 条独立条目（{uniq[0]}…{uniq[-1]}）')
                print(f'      → 节级锁定是**正确**的（铁律须常驻），但条目可分批外置：'
                      f'留 `{uniq[0]}~{uniq[-1]} 正本见 docs/...` 索引行，正文进 docs/')
    except Exception as e:
        print(f'   ⚠️ 节内条目级诊断失败（{type(e).__name__}: {e}）——不影响上述节级结论')
    print()
    print('   下一步：python3 slim_master.py %s --apply "<节标题关键字>"' % target)
    return 0


def apply_offload(target, keywords):
    p = _path_of(target)
    if not p:
        print(f'❌ 找不到 {target}/SKILL.md'); return 1
    if target in ('总册', 'master', 'rules'):
        print('❌ 总册外置请人工处理（总册是索引制，外置目标不在 docs/）'); return 1
    hard, buf = _limits()
    raw = open(p, encoding='utf-8').read()
    before = os.path.getsize(p)
    skill_dir = os.path.dirname(p)
    docs = os.path.join(skill_dir, 'docs')
    os.makedirs(docs, exist_ok=True)
    doc_name = f'manual_slim_{datetime.date.today().strftime("%m%d")}.md'
    doc_fp = os.path.join(docs, doc_name)

    subs = _sections(raw, '###') or _sections(raw, '##')
    picked, anchors = [], {}
    for kw in keywords:
        hit = [(t, b) for t, b, _, _ in subs if kw in t]
        if not hit:
            print(f'   ⚠️ 未找到含「{kw}」的节，跳过'); continue
        t, b = hit[0]
        picked.append((t, b))
        # 锚点词：节内出现的会话号/规则号/命令名，用于校验外置后内容没丢
        anchors[t] = sorted(set(re.findall(r'S\d{3}|M-R\d+|U-R\d+|P-R\d+|[a-z_]+\.py|tvbox \w+', b)))[:8]

    if not picked:
        print('❌ 无可外置节'); return 1

    # 写外置文档：**追加**而非覆盖（S135 踩坑：第二次 --apply 覆盖了第一次外置的 4 节，
    # SKILL 里 5 条索引行指向同一文件但文件里只剩 1 节 = 内容静默丢失）。
    # 若文件已存在，只在文件尾追加新节；文件头只在新建时写。
    if os.path.exists(doc_fp):
        prev = open(doc_fp, encoding='utf-8').read()
        head = prev.rstrip() + '\n\n---\n\n'
        head += f'> 以下为 {datetime.datetime.now().strftime("%H:%M")} 追加（同一轮 --apply）\n\n'
    else:
        head = (f'# {target} 外置细则（{datetime.date.today().isoformat()} · M-R77⑥ 法③）\n\n'
                '> 由 `slim_master.py --apply` 自动外置。**权威性不变**：正文以本文件为准，\n'
                '> SKILL.md 对应位置只保留索引行。修改内容请直接改本文件。\n\n---\n\n')
    body_all = '\n'.join(b.rstrip() + '\n' for _, b in picked)
    open(doc_fp, 'w', encoding='utf-8').write(head + body_all)

    # SKILL 替换为索引行
    new = raw
    for t, b in picked:
        lvl = '###' if t.startswith('###') else '##'
        title = t.lstrip('#').strip()
        anchor = title.split('（')[0].split('★')[-1].strip()[:20]
        idx = (f'{lvl} {title}\n\n'
               f'> **正本已外置**：`docs/{doc_name}#{anchor}`（M-R77⑥ 法③，'
               f'{len(b.encode())/1024:.1f}KB 移出上下文）。\n'
               f'> 需要时读该文件；本节不放正文，防止两处并存各自漂移。\n\n')
        new = new.replace(b, idx)

    # 保护：必须真有下降，否则不落盘
    tmp = p + '.tmp'
    open(tmp, 'w', encoding='utf-8').write(new)
    after = os.path.getsize(tmp)
    if after >= before:
        os.remove(tmp)
        print(f'❌ 未减少（{before}→{after}），已放弃落盘'); return 1

    # 校验锚点词仍在外置文档里
    doc_txt = open(doc_fp, encoding='utf-8').read()
    lost = []
    for t, _ in picked:
        for a in anchors[t]:
            if a not in doc_txt:
                lost.append(a)
    if lost:
        os.remove(tmp)
        print(f'❌ 外置丢内容：{set(lost)} 未出现在文档中，已放弃落盘'); return 1

    shutil.copy2(p, p + f'.bak_{datetime.datetime.now().strftime("%H%M%S")}')
    os.replace(tmp, p)
    print(f'✅ 外置 {len(picked)} 节 → docs/{doc_name}')
    print(f'   {target}：{before/1024:.1f}KB → {after/1024:.1f}KB（省 {(before-after)/1024:.1f}KB）')
    print(f'   缓冲线 {buf/1024:.0f}KB：{"✅ 达标" if after <= buf else f"⚠️ 仍差 {(after-buf)/1024:.1f}KB"}')
    for t, _ in picked:
        print(f'   · {t.lstrip("#").strip()[:50]}')
    return 0


def verify(target):
    p = _path_of(target)
    if not p:
        print(f'❌ 找不到 {target}/SKILL.md'); return 1
    hard, buf = _limits()
    size = os.path.getsize(p)
    raw = open(p, encoding='utf-8').read()
    ok = True
    print(f'🔍 {target} 瘦身校验 ｜ {size/1024:.1f}KB（缓冲线 {buf/1024:.0f}KB）')
    if size > buf:
        print(f'   ❌ 仍超缓冲线 {(size-buf)/1024:.1f}KB'); ok = False
    else:
        print(f'   ✅ 体积达标（余量 {(buf-size)/1024:.1f}KB）')
    # 索引行是否在位
    idxs = re.findall(r'正本已外置\**\s*[：:]\s*`(docs/[^`#]+)', raw)
    for f in sorted(set(idxs)):
        fp = os.path.join(os.path.dirname(p), f)
        if not os.path.exists(fp):
            print(f'   ❌ 索引指向不存在的文件：{f}'); ok = False
        else:
            print(f'   ✅ 索引有效：{f}（{os.path.getsize(fp)/1024:.1f}KB）')
    if not idxs:
        print('   · 无外置索引行（可能未用法③）')
    return 0 if ok else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 1
    target, mode = argv[0], argv[1]
    if mode == '--diagnose':
        return diagnose(target)
    if mode == '--apply':
        kws = argv[2:]
        if not kws:
            print('用法: slim_master.py <技能> --apply "<节标题关键字>" [更多关键字...]'); return 1
        return apply_offload(target, kws)
    if mode == '--verify':
        return verify(target)
    print(f'未知模式 {mode}\n{__doc__}'); return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
