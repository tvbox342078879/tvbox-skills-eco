#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules_cmd.py — 管家「规则纳管」命令（S109 P3 · 规则↔技能联动）

单一路由：`tvbox conductor rules ...`
读唯一事实源 rules_registry.json，回答：
  - 这条规则归谁守（正本/共守）
  - 落地了没（技能 SKILL.md 是否已含）
  - 探针是什么

子命令：
  rules                       总览（73 条 × 状态 + 孤岛 + 漂移）
  rules <编号> [<编号>...]     单条/多条详情
  rules --orphan              孤岛清单
  rules --owner <技能>         按技能查分卷
  rules --dup                 去重候选清单
  rules check                 三方一致性校验（注册表 vs 实况）+ 留痕

设计对齐：M-R38 单一事实源 / M-R40 副本对账 / M-R57 三态输出 /
          M-R60 读失败不折叠 / M-R65 真实对象端到端
"""
import json, os, re, sys, datetime, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))          # /root/.codebuddy/skills
REG = os.path.join(HERE, "rules_registry.json")
MASTER = "/workspace/生态1015_TVBox生态全局规则.md"
PLANS = os.path.join(os.path.dirname(HERE), "plans")


def _load():
    """三态读取（M-R60）：('ok',reg) / ('corrupt',None) / ('none',None)"""
    if not os.path.exists(REG):
        return ('none', None)
    try:
        d = json.load(open(REG, encoding="utf-8"))
        if not isinstance(d.get("rules"), list):
            return ('corrupt', None)
        return ('ok', d)
    except Exception:
        return ('corrupt', None)


def _skill_has(skill, rid):
    """该技能是否"有这条规则"——**查两处：SKILL.md 与 references/ 下的外置明细**。

    ★ S158 修（真缺陷，曾被误当成 49 条违规）：
      原先只看 `SKILL.md`。但 S108 已经把明细表外置到
      `references/rules_铁律坐标明细.md`，SKILL.md 只留一句指路。
      于是 check 报出 49 条「注册表标已固化但 SKILL.md 无此编号（副本过期）」——
      **全是假阳性**：规则明明在，只是检测器不知道去哪找。
      这正是本轮 S158 归纳的同一类缺陷：**检测器的扫描面与实际存储位置不一致**，
      它不报错，它报"过期"，看起来像真问题，于是没人敢动。
    """
    base = os.path.join(SKILLS, skill)
    cands = [os.path.join(base, "SKILL.md")]
    refdir = os.path.join(base, "references")
    if os.path.isdir(refdir):
        for fn in sorted(os.listdir(refdir)):
            if fn.endswith('.md'):
                cands.append(os.path.join(refdir, fn))
    hit_any = False
    read_ok = False
    for p in cands:
        if not os.path.exists(p):
            continue
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception:
            continue                                  # 读失败不算"没有"，但也不就此收工
        read_ok = True
        if re.search(r'\b' + re.escape(rid) + r'\b', txt):
            hit_any = True
            break
    if not read_ok:
        return None                                   # 一处都读不到 → 无法判定（M-R60）
    return hit_any


def _live_stats(reg):
    """实时统计（S135）：`stats.total` 是手写缓存，加规则时忘同步就永久错一个数——
    实测加 M-R78 后 stats.total 仍写 84 而 rules 实际 85，check 打印「注册表 84 条」自欺。
    一律以 `rules` 实际内容为准，缓存字段只作备份不再读取（M-R74 单一真源的同款问题）。"""
    rs = reg.get("rules") or []
    return {
        "total": len(rs),
        "solidified": sum(1 for r in rs if r.get("status") == "solidified"),
        "orphan": sum(1 for r in rs if r.get("status") == "orphan"),
        # 实际取值（S135 核对）：keep_in_master 71 / deduped_to_skill 7 / None 5 / '' 2
        "dup_candidates": sum(1 for r in rs if r.get("dedup_decision") == "keep_in_master"),
        "deduped": sum(1 for r in rs if r.get("dedup_decision") == "deduped_to_skill"),
    }


def cmd_overview():
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）——无法判定（勿当'无规则'，M-R60）")
        return 3     # S177：读不出=无法判定，不是成功也不是「没有规则」
    s = _live_stats(reg)
    print(f"📋 规则纳管总览  v{reg['version']} · {reg['updated']}")
    print(f"   总数 {s['total']} ｜ 已固化 {s['solidified']} ｜ 孤岛 {s['orphan']} ｜ 去重候选 {s['dup_candidates']}")
    orph = [r["id"] for r in reg["rules"] if r["status"] == "orphan"]
    if orph:
        print(f"   ⚠️ 孤岛规则（无技能认领）：{', '.join(orph)}")
    else:
        print("   ✅ 无孤岛——全部规则有技能正本")
    dups = [r["id"] for r in reg["rules"] if r.get("dedup_decision") == "keep_in_skill"]
    if dups:
        print(f"   📦 去重候选（技能已固化，总册可精简）：{', '.join(dups)}")


def cmd_detail(ids):
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）——无法判定"); return 3   # S177：读不出=无法判定(M-R60)，不是成功也不是「没有」
    idx = {r["id"]: r for r in reg["rules"]}
    for rid in ids:
        r = idx.get(rid)
        if not r:
            print(f"❓ {rid}：注册表中无此编号（无法判定）"); continue
        print(f"\n[{rid}] {r['summary']}")
        print(f"  正本: {r['primary_owner']}  {r['chapter']}")
        # `owner` 在 registry 里是**字符串**（单守），`for x in 'unfinished-audit'`
        # 会按字符拆成 u, n, f, i, n, i, s, h...  ——这条 bug 一直存在，
        # 只是「共守」多数为空所以没显形；单守技能（如 unfinished-audit）一查就露。
        # 统一归一成列表，同时兼容将来改成 list 的情形（M-R86 同族：显示层不得失真）。
        _ow = r.get("owner")
        _ow = [x for x in _ow if x] if isinstance(_ow, list) else \
              ([t.strip() for t in str(_ow or '').replace('，', ',').split(',') if t.strip()])
        co = [x for x in _ow if x != r["primary_owner"]]
        if co:
            print(f"  共守: {', '.join(co)}")
        # 实况核对（M-R40）
        live = _skill_has(r["primary_owner"], rid) if r["primary_owner"] else None
        lt = {True: "已落地 ✅", False: "未落地 ❌", None: "无法判定 ⚠️"}[live]
        print(f"  落地: {lt}（{r['primary_owner']}/SKILL.md）")
        # 缺字段不得让整条命令崩掉（S136 实测：13 条无 dedup_decision，查一条就 KeyError，
        # 整个 rules 详情功能全废）。缺 = 显式说"未登记"，不静默补默认值（M-R57 三态）。
        _dd = r.get("dedup_decision")
        print(f"  状态: {r['status']} ｜ 去重决策: {_dd or '未登记（本条缺 dedup_decision 字段）'}")
        if r.get("probe", {}).get("command"):
            print(f"  探针: {r['probe']['command']}")


def cmd_orphan():
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）"); return 3   # S177：读不出=无法判定(M-R60)，不是成功也不是「没有」
    orph = [r for r in reg["rules"] if r["status"] == "orphan"]
    if not orph:
        print("✅ 无孤岛规则"); return
    print(f"⚠️ 孤岛规则 {len(orph)} 条：")
    for r in orph:
        print(f"  {r['id']}  {r['summary'][:60]}")


def cmd_owner(skill):
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）"); return 3   # S177：读不出=无法判定(M-R60)，不是成功也不是「没有」
    rs = [r for r in reg["rules"] if r["primary_owner"] == skill]
    if not rs:
        print(f"「{skill}」名下无规则（或技能名拼写有误）"); return
    print(f"📚 {skill} 规则分卷（{len(rs)} 条）：")
    for r in rs:
        print(f"  {r['id']}  {r['summary'][:62]}")


def cmd_dup():
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）"); return 3   # S177：读不出=无法判定(M-R60)，不是成功也不是「没有」
    ds = [r for r in reg["rules"] if r.get("dedup_decision") == "keep_in_skill"]
    print(f"📦 去重候选 {len(ds)} 条（技能已逐字固化 → 总册可精简）：")
    for r in ds:
        print(f"  {r['id']}  正本={r['primary_owner']}  {r['summary'][:50]}")


def cmd_scene():
    """--scene：按使用场景编组（S128 P004——消除"69 条挤一章"假分类，按"什么时候用"取规则）。"""
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 注册表不可用（{st}）"); return 3   # S177：读不出=无法判定(M-R60)，不是成功也不是「没有」
    scenes = {
        '开工前':      lambda r: any(k in r['summary'] for k in ('预载','经验','开工','术语','路由','触发','意图','黑话','需求')),
        '写库/留痕':   lambda r: any(k in r['summary'] for k in ('写库','留痕','append','账本','回读','merge','PropertyValue','批次','写入')),
        '匹配/去重':   lambda r: any(k in r['summary'] for k in ('bigram','匹配','去重','正则','解析','措辞','子串','阈值')),
        '检测/探针':   lambda r: any(k in r['summary'] for k in ('探针','信号源','检测','巡检','盲区','假阳性','捕获','自检','报警','监测')),
        '联动/备份':   lambda r: any(k in r['summary'] for k in ('联动','备份','三层','同步','契约','版本','漂移','生态库','资料库','云端')),
        '复盘/清账':   lambda r: any(k in r['summary'] for k in ('复盘','问题','遗留','清零','白做','超期','总账')),
        '评估/对账':   lambda r: any(k in r['summary'] for k in ('评估','评分','对账','指标','量化','采纳率','体检','覆盖率')),
        '技能治理':    lambda r: any(k in r['summary'] for k in ('技能','SKILL','manifest','命令表','设计文档','增长','瘦身','收敛')),
        '云端/容错':   lambda r: any(k in r['summary'] for k in ('云端','三态','折叠','失败','降级','缓存','限流','分页','超时')),
        '解析/抓取':   lambda r: any(k in r['summary'] for k in ('图种','UA','代理','拉黑','线路源','HTTP 可达')),
        '性能/反模式': lambda r: any(k in r['summary'] for k in ('误报','重算','必填','eager','单点','瓶颈','耗时','单测','端到端','一刀切')),
    }
    assigned = {}
    # S130 P001：关键词覆盖不到的 30 条显式分桶（三选一分类，0 条留"其他"）
    explicit = {
        'L023':'匹配/去重','L024':'检测/探针','L025':'解析/抓取','L026':'解析/抓取','L027':'解析/抓取',
        'L029':'写库/留痕','M-R5':'性能/反模式','M-R6':'技能治理','M-R7':'性能/反模式','M-R9':'评估/对账',
        'M-R11':'评估/对账','M-R15':'技能治理','M-R17':'技能治理','M-R18':'评估/对账','M-R19':'检测/探针',
        'M-R21':'评估/对账','M-R22':'复盘/清账','M-R41':'性能/反模式','M-R44':'性能/反模式','M-R46':'性能/反模式',
        'M-R48':'性能/反模式','M-R50':'性能/反模式','M-R51':'性能/反模式','M-R55':'检测/探针','M-R58':'检测/探针',
        'M-R59':'检测/探针','M-R65':'评估/对账','M-R66':'技能治理','P-R1':'解析/抓取','U-R1':'技能治理',
        'M-R74':'技能治理','M-R75':'检测/探针','M-R76':'检测/探针',
        'M-R77':'技能治理','M-R78':'技能治理',
    }
    for r in reg['rules']:
        if r['id'] in explicit:
            assigned.setdefault(explicit[r['id']], []).append(r['id'])
    for r in reg['rules']:
        if r['id'] in explicit:
            continue
        for sc, pred in scenes.items():
            if pred(r):
                assigned.setdefault(sc, []).append(r['id'])
                break
    other = [r['id'] for r in reg['rules'] if r['id'] not in {i for v in assigned.values() for i in v}]
    fr = reg.get('freeze') or {}
    print(f"🧭 规则场景编组（v{reg['version']}{'｜🔒冻结 '+fr.get('since','?') if fr else ''}）——按'什么时候用'取，替代章节假分类：")
    for sc, ids in assigned.items():
        print(f"   【{sc}】{len(ids)} 条：{', '.join(ids)}")
    if other:
        print(f"   【其他】{len(other)} 条：{', '.join(other)}")



def cmd_check():
    """三方一致性：注册表 vs 总册 vs 技能实况。"""
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）"); return 3   # S177：读不出=无法判定(M-R60)，不是成功也不是「没有」
    issues = []
    # 1) 总册编号集 vs 注册表
    #    **S148 修**：本册自 S109 起已降级为**索引**——§七之二 明确写了「逐条索引不再手抄」，
    #    正文住在各技能 SKILL.md。故「总册逐条列出每个编号」**不再是要求**，
    #    只有这样才把编号单向列为漂移；若总册是索引形态（声明了工具入口），
    #    判据应改为「注册表的编号是否能在总册某处被点到」——但索引形态下这本来就成立，
    #    故**只在缺失时告警**，不再报"幽灵条目"。否则 99 条通篇红，把真问题（ask 缺失）淹没。
    if os.path.exists(MASTER):
        _mtext = open(MASTER, encoding="utf-8").read()
        mid = set(re.findall(r'^\|\s*(M-R\d+|U-R\d+|P-R\d+|L0\d+)\s*\|', _mtext, re.M))
        rid = {r["id"] for r in reg["rules"]}
        # 判断总册是否为「索引形态」：声明了指向注册表的权威入口即视为索引
        _is_index = bool(re.search(r'rules_registry\.json', _mtext)) and \
                    bool(re.search(r'conductor rules', _mtext))
        for x in sorted(mid - rid):
            issues.append(f"总册有 {x} 但注册表无（漏登记）")
        if not _is_index:
            for x in sorted(rid - mid):
                issues.append(f"注册表有 {x} 但总册无（幽灵条目）")
    else:
        issues.append("总册文件不存在——无法判定")
    # 2) 注册表 vs 技能实况（M-R40）
    for r in reg["rules"]:
        po = r["primary_owner"]
        if not po:
            issues.append(f"{r['id']} 无归属技能")
            continue
        live = _skill_has(po, r["id"])
        if live is False and r["status"] == "solidified":
            issues.append(f"{r['id']} 注册表标已固化但 {po}/SKILL.md 无此编号（副本过期）")
        if live is None:
            issues.append(f"{r['id']} 归属技能 {po} 的 SKILL.md 不可读（无法判定）")
    # 3) contracts 一致性——总册§五 声明「contracts.json 为正本」时，比【契约 id 集合】而非出现次数
    #    （S109 P5：§五 改为指向 contracts.json 的清单表，计数法失效；改为双向集合比对，M-R38/M-R13）
    cj = os.path.join(SKILLS, "tvbox-source-hub/scripts/contracts.json")
    if os.path.exists(cj):
        try:
            cids = {c.get("id") for c in json.load(open(cj, encoding="utf-8")).get("contracts", []) if c.get("id")}
            m = open(MASTER, encoding="utf-8").read()
            sec5 = re.search(r'## 五、[^\n]*\n(.*?)## 六', m, re.S)
            if sec5:
                body = sec5.group(1)
                if "contracts.json" in body and ("正本" in body or "为准" in body):
                    # 声明了正本 → 比 id 集合（双向）
                    listed = set(re.findall(r'`([a-z][a-z-]+)`', body))
                    miss = cids - listed                      # 契约有·总册无（漏登记）
                    # 幽灵：只认「像契约 id 的词」（含连字符，如 candidate-list），排除说明词（host/principle）
                    ghost = {w for w in (listed - cids) if "-" in w}
                    if miss:
                        issues.append(f"contracts 漏登记：{sorted(miss)}（contracts.json 有·总册§五 未列）")
                    elif ghost:
                        issues.append(f"contracts 幽灵条目：总册§五 列了 contracts.json 没有的 {sorted(ghost)}")
                else:
                    # 未声明正本 → 退回计数法
                    mc = len(re.findall(r'`[a-z-]+`', body))
                    if mc and mc != len(cids):
                        issues.append(f"contracts 漂移：contracts.json {len(cids)} 条 vs 总册§五 约 {mc} 处引用")
        except Exception as e:
            issues.append(f"contracts.json 读取失败（{type(e).__name__}）")
    # 4) 废止语义完整性（M-R98 · S144）——三方一致只管「编号在不在」，
    #    管不到「废止有没有交代」。缺 retracted_by 时 index 全绿而机制已断，属假绿。
    for r in reg["rules"]:
        if r.get("status") == "retracted":
            miss = [k for k in ("retracted_by", "retracted_session", "retracted_reason")
                    if not str(r.get(k) or "").strip()]
            if miss:
                issues.append(f"{r['id']} 已废止但缺 {miss}（无痕废止，M-R98）")
            elif str(r["retracted_by"]) not in {x["id"] for x in reg["rules"]}:
                issues.append(f"{r['id']} 声称被 {r['retracted_by']} 取代但该编号不存在（假废止，M-R98）")
    # 5) ask 字段完整性（S148 · M-R79 同族）——「编号在不在」之外还有「规则能不能被问中」。
    #    只有 summary 时规则是维护者视角的结论句，用户不会这么说话，规则就永远靠人记得。
    #    判据与 `rules --ask-coverage` **同一口径**（函数复用，不另写一套，M-R38）。
    issues.extend(_ask_issues(reg))
    # 输出 + 留痕
    print(f"🔍 三方一致性校验（注册表 {_live_stats(reg)['total']} 条）")
    if not issues:
        print("  ✅ 三方一致，无漂移")
    else:
        print(f"  ⚠️ 发现 {len(issues)} 处问题：")
        for i in issues[:30]:
            print(f"    - {i}")
    try:
        os.makedirs(PLANS, exist_ok=True)
        lf = os.path.join(PLANS, "rules_check_log.json")
        logs = json.load(open(lf, encoding="utf-8")) if os.path.exists(lf) else []
        logs = [x for x in logs if x.get("date") != datetime.date.today().isoformat()]
        logs.append({"date": datetime.date.today().isoformat(),
                     "total": _live_stats(reg)["total"], "issues": issues})
        json.dump(logs, open(lf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  💾 已留痕 plans/rules_check_log.json")
    except Exception as e:
        print(f"  （留痕失败：{e}）")

    # ★ S177：本函数原来**结尾没有 return** —— 全绿时靠"隐式 None 被入口归一成 0"偶然正确，
    #   但查出 issues 时同样落到函数末尾，也是 0。等于**校验说有问题，退出码说没问题**。
    #   这正是 verify 门禁最怕的那种：门上的红灯不接到电闸上。
    # 口径：0 全过 / 3 跑完了但查出问题（与 signal-check 一致，刻意区别于 1=脚本自身崩）
    return 3 if issues else 0


def cmd_scope():
    """`rules --scope`：规则**作用面**审计（S139）。

    为什么加这一项：S130–S138 连立 19 条新法，每条都对、每条都有探针、每条都验证通过，
    但规则库总条数从 82 涨到 102，而业务面**一条新规则都没进去**——19 条全部是关于
    「怎么管规则／怎么管判定／怎么管度量」的自指规则。系统的自检能力越来越强，
    管辖的业务反而更少。这才是「治理占比 90%」的真身：不是治理动作多，
    是**只有治理类规则还在被写**。

    本项不加任何新规则，只把已有的这个事实量出来：每条规则的落点究竟是
    「可执行代码」还是「只有文档」，以及 M-R77 前后两段的分部对照。
    看得见，才谈得上愿不愿意继续这样写。
    """
    st, d = _load()
    if st == 'none':
        print("⚠️ 规则注册表不存在——无法判定，勿当作无问题"); return 1
    if st == 'corrupt':
        print("⚠️ 规则注册表格式损坏——无法判定，勿当作无问题"); return 1
    rs = d["rules"]

    def _num(rid):
        m = re.search(r"(\d+)$", rid or "")
        return int(m.group(1)) if m else 0

    def _flat(x):
        if isinstance(x, str):
            return x
        if isinstance(x, list):
            return " ".join(_flat(i) for i in x)
        if isinstance(x, dict):
            return " ".join(_flat(v) for v in x.values())
        return str(x)

    # 落点分类：landing 里若点到 .py 文件 = 有代码载体；否则只有技能文档锚点
    code, doc_only = [], []
    for r in rs:
        t = _flat(r.get("landing", ""))
        (code if re.search(r"[a-zA-Z_0-9]+\.py", t) else doc_only).append(r["id"])

    def _seg(lo, hi):
        seg = [r for r in rs if r["id"].startswith("M-R") and lo <= _num(r["id"]) <= hi]
        hp = [r for r in seg if (r.get("probe") or {}).get("command")]
        return len(seg), len(hp)

    n_old, p_old = _seg(0, 76)
    n_new, p_new = _seg(77, 999)

    print("🧭 规则作用面审计（S139·不加规则，只把已有事实量出来）")
    print()
    print(f"  规则总数 {len(rs)}")
    print(f"  落点=可执行代码：{len(code)} 条 ｜ 落点=仅技能文档：{len(doc_only)} 条"
          f"（{len(doc_only) * 100 // max(len(rs), 1)}%）")
    print(f"  ├ M-R1..76  ：{n_old} 条，其中 {p_old} 条有可执行探针"
          f"（{p_old * 100 // max(n_old, 1)}%）")
    print(f"  └ M-R77..95 ：{n_new} 条，其中 {p_new} 条有可执行探针"
          f"（{p_new * 100 // max(n_new, 1)}%）")
    print()
    print("  读法：新规则条条可验证 ≠ 规则库在变好。若新增的每一条都是")
    print("        「怎么管规则/怎么管判定/怎么管度量」，那是系统在照镜子，不是在干活。")
    if doc_only:
        print(f"\n  仅文档载体（前 8 条）：{'、'.join(doc_only[:8])}")
    return 0


def _score_fn():
    """取 conductor 的路由打分器（**唯一口径**，不另写一套）。

    S148：`rules --ask` 必须在语义上与 `conductor ask` 的技能路由同口径——
    否则会出现"用户问同一句话，找技能命中、找规则不命中"的分裂，
    而两套打分的阈值/分词/特异性加权只要有一处不同，就会长期静默漂移（M-R38）。
    故延迟导入（rules_cmd 被 conductor 反向调用，模块级导入会循环）。
    """
    try:
        import conductor as _c
        return _c._route_score, getattr(_c, 'ROUTE_HIT_MIN', 2)
    except Exception:
        return None, 2


def _score_rule(q, r, score):
    """规则专用打分（S148）——在通用路由分之上补**具体性加权**。

    **为什么不能直接用 _route_score**：通用分只看 bigram 交集，
    而方法论类规则（M-R3x–M-R7x）的 summary 长达数百字，bigram 池天然比
    具体坑（L025「配置藏在图片里」，8 个字）大一个数量级。
    实测后果：用户说「怎么从图上读出接口」，本该是 L025（具体坑），
    却因为 L023 的长 summary 撞上「接口/怎么/读到」而排到前面——
    **长文本吸走匹配，具体规则被泛化规则淹没**。

    修法（只加权、不改分词）：
      ① ask 命中 ×1.5 —— ask 是**用户口吻**，与提问同域，命中它才是真撞上；
      ② summary 命中 ×1.0 —— 维护者口吻，只能算辅助证据；
      ③ 短规则（具体坑）+1 —— L0xx / M-R9x 这类"一句话描述一个具体现象"的，
         比"一段话描述一个方法论"更可能正是用户要的那条。
    这样既保留通用口径，又能让具体压过泛化。加权口径写在这里，**不得散落别处**。
    """
    a = (r.get('ask') or '')
    sm = (r.get('summary') or '')
    s_ask = score(q, a)
    s_sum = score(q, sm)
    base = s_ask * 1.5 + s_sum
    # 具体坑加分：id 命名即分类（L0xx = 具体坑，M-R9x = 实现层）
    if r['id'].startswith('L') or re.match(r'M-R\d{2,3}$', r['id']) and int(r['id'].split('R')[1]) >= 96:
        base += 1
    return base


def cmd_ask(query):
    """--ask：**按用户的话**找规则（S148）。

    **为什么必须有这个入口**：原有 `--scene` 是按 summary 关键词猜"场景"，
    那是**维护者分类**，不是用户提问。规则库长期只有 summary（该守什么）、
    没有 ask（用户会怎么问），结果就是：规则写得再准，全靠在场的人**恰好想起来**。

    判据：用户说「同一条源换个写法就认不出来」，应该直接撞出 M-R96/M-R97，
    而不是要他自己知道"这属于解析/抓取场景"。

    匹配口径：conductor._route_score（bigram 交集 + 整词通路），阈值同 ROUTE_HIT_MIN。
    三态（M-R60）：注册表读失败 → 显式报无法判定，**不当成"没有这条规则"**。
    """
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）——**无法判定**，不代表没有相关规则")
        return 3     # S177：与其它「注册表不可用」统一（无法判定≠不通过）
    q = (query or '').strip()
    if not q:
        print("用法: rules --ask \"<你的问题>\"")
        return 1
    score, MIN = _score_fn()
    if score is None:
        print("❌ 取不到路由打分器（conductor 导入失败）——无法判定")
        return 1
    rows = []
    no_ask = 0
    for r in reg['rules']:
        a = (r.get('ask') or '').strip()
        if not a:
            no_ask += 1
            continue
        s = _score_rule(q, r, score)
        if s >= MIN:
            rows.append((s, r))
    rows.sort(key=lambda x: (-x[0], x[1]['id']))
    print(f"🔎 按用户话找规则｜你说：{q}")
    print('─' * 56)
    if not rows:
        print("   未命中（打分均低于阈值）——可能超出规则覆盖范围，或换个说法再试")
        if no_ask:
            print(f"   ⚠️ 另有 {no_ask} 条规则无 ask 字段（本次无法参与匹配）")
        return 0
    for s, r in rows[:6]:
        print(f"   [{r['id']}] {r['summary'][:64]}")
        print(f"      用法：{(r.get('ask') or '')[:80]}")
        print(f"      正本：{r['primary_owner']}  {r.get('chapter', '')}  ｜ 命中 {s}")
    if len(rows) > 6:
        print(f"   …另有 {len(rows) - 6} 条较低分结果")
    if no_ask:
        print(f"\n   ⚠️ {no_ask} 条规则无 ask 字段，未参与匹配——这是**覆盖缺口**，不是「没有相关规则」")
    return 0


def _ask_issues(reg, limit=8):
    """ask 字段的检查项（S148）——**check 与 --ask-coverage 的唯一口径**。

    两条判据（都是"退化"而不是"少写一行字"）：
      ① 缺失 ask —— 规则回到"只有维护者结论句"的状态，用户问不中；
      ② ask 与 summary 过于相似（bigram 包含率 >0.8）——字段有了但**价值没有**，
         属于"假补"：看起来覆盖了，实际是把 summary 换个标点填进去。

    **为什么相似度能抓住假补**：ask 必须是用户口吻（"导了一大批进去还是有重复"），
    summary 是维护者口吻（"批量入库判重只用库内快照"）。二者共享 bigram 应极少；
    一旦 >0.8，说明 ask 基本就是 summary 复写，用户不会这么问。
    阈值 0.8 是实测取值（S148 全部 108 条里最高真实值约 0.42），留了充足余量。
    """
    def _norm(s):
        return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s or '')

    rs = reg.get('rules') or []
    miss, fake = [], []
    for r in rs:
        a = (r.get('ask') or '').strip()
        if not a:
            miss.append(r['id'])
            continue
        an, sn = _norm(a), _norm(r.get('summary'))
        if not sn:
            continue                      # summary 空是另一类问题，不在此折叠
        ga = {an[i:i + 2] for i in range(len(an) - 1)}
        gs = {sn[i:i + 2] for i in range(len(sn) - 1)}
        if gs and len(ga & gs) / len(gs) > 0.8:
            fake.append(r['id'])

    out = []
    if miss:
        out.append(f"ask 缺失 {len(miss)} 条（规则只剩维护者视角，用户问不中）："
                   f"{', '.join(miss[:limit])}{' …' if len(miss) > limit else ''}")
    if fake:
        out.append(f"ask 与 summary 雷同 {len(fake)} 条（字段有了但价值没有，属假补）："
                   f"{', '.join(fake[:limit])}{' …' if len(fake) > limit else ''}")
    return out


def cmd_ask_coverage():
    """--ask-coverage：ask 字段覆盖率（S148·M-R79 同族：缺口必须可见）。

    只验"有字段"不够——ask 若照抄 summary，字段有了但用户照样问不中。
    故双判据：①覆盖率 100% ②**不许与 summary 雷同**（相似度>0.8 视为假补）。
    判据实现统一在 `_ask_issues()`——**此处只负责打印**，不另算一遍（M-R38）。
    """
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）"); return 3   # S177：无法判定统一为 3
    rs = reg['rules']
    tot = len(rs)
    miss = [r['id'] for r in rs if not (r.get('ask') or '').strip()]
    issues = _ask_issues(reg)
    print(f"📋 ask 覆盖率 {tot - len(miss)}/{tot}")
    for it in issues:
        print(f"   ❌ {it}")
    if not issues:
        print("   ✅ 全覆盖，且无照抄迹象")
    return 0 if not issues else 1


# ── 规则话术黄金用例（S148）─────────────────────────────────────────────
# **必须独立于 ask 书写**：若用例直接抄 ask 原文，就是「用自己写的词考自己」的自测自绿
# （上一轮 ROUTE_GOLDEN 已经栽过一次）。故这里刻意用**不同措辞**表达同一件事——
# 有人问现象、有人问原因、有人只丢一个词。判据：这些都不含 ask 原句。
# 覆盖口径：全部具体坑（L0xx）+ 高频实现层规则（M-R9x）。方法论类不抽查——
# 它们本来就该由 summary 承担检索，ask 只为"用户撞得上"服务。
RULES_GOLDEN = {
    'L023': ['导了一大批进去，结果还是有重复的',
             '同一批里两个站一模一样，没去重'],
    'L024': ['拉黑没生效，还在跑',
             '名单里加了怎么照样能用'],
    'L025': ['站是个图片，配置藏在图里',
             '怎么从图上读出接口'],
    'L026': ['封了一个域名，好多好源也不能用了',
             '拉黑一大片，正常的也跟着挂'],
    'L027': ['手机能放的电脑放不了，同样的源',
             '换个播放器就不行了'],
    'L028': ['写的时候没报错，读出来是空',
             '存进去了但查不到值'],
    'L029': ['参数加了还是不能写',
             '照帮助填的却提示不对'],
    'M-R96': ['地址带不带 www 算不算同一个',
              '换个写法就认不出来了',
              '同一条线路好几种地址，拉黑不全'],
    'M-R97': ['库里同一个源好几条，就是写法不同',
              '地址没统一过，重复的越来越多'],
    'M-R98': ['上次修过了这次又来',
              '记了笔记但没真改，所以老犯'],
    'M-R99': ['提示成功但其实是空的',
              '保存成功了，打开没有'],
    'M-R100': ['翻页老是同一批',
               '一直翻都是那些，翻不到新的'],
    'M-R101': ['改完有些字段没了',
               '结构一改，原来的东西丢了'],
    'M-R102': ['全都说网络失败，其实是字段没对上',
               '一批下来全是失败，感觉不是网的问题'],
    'M-R103': ['一条线路在两个分组都有',
               '同样的源重复出现好几次'],
    'P-R1': ['能打开但放不出来，算能用吗',
             '网页打得开，播放器读不了'],
    'M-R77': ['文件太大了会不会读不全',
              '写太长了，关键的东西会不会被挤掉'],
    'M-R94': ['是不是一直在改规则没干正事',
              '修了一堆，问题还是那些'],
    'M-R76': ['写了触发词但我说的它接不住',
              '名字登记了，实际用不上'],
    'M-R60': ['返回空到底是真没有还是没读到',
              '拿不到结果和结果为空怎么分'],
}


def cmd_ask_golden():
    """用**独立书写的用户话术**考 ask（S148·M-R77 验证须独立于被验对象）。

    为什么不能拿 ask 原文自测：那是自测自绿，100% 通过毫无信息量
    （实测：拿 ask 考 ask 是 108/108，但换句话就可能掉）。
    本命令用 RULES_GOLDEN（人工另写、措辞不同）当外部标尺。
    """
    st, reg = _load()
    if st != 'ok':
        print(f"❌ 规则注册表不可用（状态={st}）"); return 3   # S177：无法判定统一为 3
    idx = {r['id']: r for r in reg['rules']}
    score, MIN = _score_fn()
    if score is None:
        print("❌ 取不到路由打分器——无法判定"); return 1
    tot = ok = 0
    wrong = []
    for rid, cases in RULES_GOLDEN.items():
        if rid not in idx:
            print(f"   ⚠️ {rid} 黄金用例指向的规则不存在")
            continue
        for q in cases:
            tot += 1
            best, bid = 0, None
            for o in reg['rules']:
                s = _score_rule(q, o, score)
                if s > best:
                    best, bid = s, o['id']
            if bid == rid:
                ok += 1
            else:
                wrong.append((q, rid, bid, best))
    print(f"🎯 规则话术黄金用例 {ok}/{tot}")
    for q, exp, got, s in wrong:
        print(f"   ✗ 「{q}」→ 实际 {got}（应 {exp}，{s} 分）")
    if not wrong:
        print("   ✅ 全部命中——用户这么说，能撞出对的规则")
    return 0 if ok == tot else 1


def cmd_relapse():
    """`rules relapse`：复发检测（M-R98 的可执行落地）。

    背景：L026 早在 S? 就记录了「代理域名被整域拉黑，误伤 28 条正常源」，
    但当时只留档、没改机制；S144 同一问题以「122 host 中 4 个整域判死，3 个是活源」
    的形态复发。**留档不产生免疫力，只有改实现才产生。**

    本命令不加新规则，只把「一个坑被踩了第二次」这件事从人的记忆里搬进代码：
      ① retracted 条目必须交代「被谁取代、哪一轮、为什么」——否则废止是无痕的，
         下一个人（或下一轮的我）会照抄旧口径；
      ② retracted 的取代者必须真实存在、且状态不是 retracted——否则是「假废止」，
         旧规则撤了、新规则没立，行为退回到无约束；
      ③ 同一条 retracted 不许被重复取代（新法互相打架到死锁，M-R83 同型）；
      ④ 每一轮（session）的净增规则数必须 ≤ 净废止数 + 1，否则是「只增不减」，
         等同于把规则库当日志写。这条直接对应 M-R94 的治理刹车。

    四态输出（M-R90）：green / red×N / unknown，不允许「没量到」被算成 0 问题。
    """
    st, reg = _load()
    if st == "none":
        print("⚠️ 规则注册表不存在——无法判定，勿当作无问题"); return 1
    if st == "corrupt":
        print("⚠️ 规则注册表格式损坏——无法判定，勿当作无问题"); return 1
    rs = reg.get("rules") or []
    ids = {r.get("id") for r in rs}
    by = {r.get("id"): r for r in rs}

    reds, unk = [], []
    retracted = [r for r in rs if r.get("status") == "retracted"]

    # ① 废止必须带三要素
    for r in retracted:
        miss = [k for k in ("retracted_by", "retracted_session", "retracted_reason")
                if not str(r.get(k) or "").strip()]
        if miss:
            reds.append(f"{r['id']} 已废止但缺 {miss}（无痕废止：后人会照抄旧口径）")

    # ② 取代者必须真实存在且未一并废止（防「假废止」）
    replace_of = {}
    for r in retracted:
        rb = str(r.get("retracted_by") or "").strip()
        if not rb:
            continue
        if rb not in ids:
            reds.append(f"{r['id']} 声称被 {rb} 取代，但注册表无此编号（假废止：旧撤了新没立）")
            continue
        if (by[rb].get("status") == "retracted"):
            reds.append(f"{r['id']} 被 {rb} 取代，而 {rb} 自己也被废止（取代链断头）")
        replace_of.setdefault(rb, []).append(r["id"])

    # ③ 取代者不得一对多，除非显式声明 supersedes_mode='merge'（合并须讲清为什么是同一根因）
    for rb, olds in replace_of.items():
        if len(olds) > 1 and str(by[rb].get("supersedes_mode") or "").strip() != "merge":
            unk.append(f"{rb} 同时取代 {olds}，未标 supersedes_mode=merge（无法判定是合并还是误挂）")

    # ④ 每轮净增 ≤ 净废止 + 1（只增不减刹车·M-R94）
    #    S144 自测暴露两次口径错，都属 M-R90「归属不得摊派」：
    #    (a) 原实现只统计「显式 session 字段」，而历史条目的轮次写在 note 里 → 恒数不到 → 恒通过；
    #    (b) 改为扫 note 里所有 `S\d+` 后，**被修订的旧规则**（note 写「S109 新增…S137 修正」）
    #        会被算进 S137 的新增里 → 造出 6 处假红。修订 ≠ 新增。
    #    收敛：**归属只认「立法轮」**——显式 `session` 字段优先，其次 note/summary 里**首个** S 标记
    #    （文的叙述总从立法那一刻起笔）；后续出现的 S 一律视为修订。认不出立法的（无任何 S 标记）
    #    单列 unk，**不摊派给任何轮次**，也不许算作 0。
    def _law_session(r):
        s = str(r.get("session") or "").strip()
        if s:
            return s
        for k in ("note", "summary"):
            m = re.search(r"\bS\d{2,3}\b", str(r.get(k) or ""))
            if m:
                return m.group(0)
        return ""

    sess, unassigned = {}, []
    for r in rs:
        s = _law_session(r)
        if not s:
            unassigned.append(r["id"])
            continue
        d = sess.setdefault(s, {"new": 0, "cut": 0, "ids": []})
        if r.get("status") == "retracted":
            d["cut"] += 1
        else:
            d["new"] += 1
            d["ids"].append(r["id"])
    if unassigned:
        unk.append(f"{len(unassigned)} 条规则归属不到立法轮（不摊派、也不当 0）：{unassigned[:8]}")
    over = [(s, d) for s, d in sess.items() if d["new"] > d["cut"] + 1]
    TOLERATE = set(reg.get("relapse_tolerate_sessions") or [])
    for s, d in over:
        if s in TOLERATE:
            unk.append(f"{s} 净增 {d['new']} 条 vs 废止 {d['cut']}（历史欠账·已登记豁免，新增条目不再追认）")
            continue
        reds.append(f"{s}：计得新增 {d['new']} 条 vs 废止 {d['cut']} 条"
                    f"（净增 >1，按 M-R94 应找取代对象或拆条）新增={d['ids'][:4]}")

    solid = sum(1 for r in rs if r.get("status") == "solidified")
    print("🔁 规则复发/进化检测（M-R98）")
    print(f"   在册 {len(rs)} 条 ｜ 生效 {solid} ｜ 已废止 {len(retracted)}")
    if retracted:
        print("   废止台账：")
        for r in retracted:
            print(f"     ✗ {r['id']} → 被 {r.get('retracted_by') or '?'} 取代"
                  f"（{r.get('retracted_session') or '?'}）")
    if unk:
        print(f"   🟡 无法判定 {len(unk)} 处：")
        for x in unk:
            print(f"     - {x}")
    if reds:
        print(f"   🔴 发现 {len(reds)} 处问题：")
        for x in reds:
            print(f"     - {x}")
        return 1
    if unk:
        print("   ➡️ 无硬错，但有无法判定项——不要当成「已验通过」")
        return 0
    print("   ✅ 废止均有取代、取代均有效、本轮无只增不减")
    return 0


def main(argv):
    """规则纳管命令入口。

    ★ S177 批量修（同一模式扫出）：**原实现绝大多数分支是裸调用 `cmd_xxx()`，
    返回值全被丢弃** → 无论命令内部怎么 `return 3`，出口一律 rc=0。
    只有 relapse/scope 两个分支恰好写了 return，于是同一个文件里
    「有的命令退出码可信、有的不可信」，比全都没修更难排查。

    这与 `evolve.dispatch`、`link.dispatch` 是同一处病：**判定算对了，没交到下游**。
    本文件内 6 处「规则注册表不可用」尤其危险——注册表坏了，命令却说"成功"，
    任何按 rc 判断的门禁都会把它记成通过。
    """
    if not argv:
        return cmd_overview()
    a = argv[0]
    if a == "check":
        return cmd_check()
    elif a == "relapse":
        return cmd_relapse()
    elif a == "--scope":
        return cmd_scope()
    elif a == "--orphan":
        return cmd_orphan()
    elif a == "--scene":
        return cmd_scene()
    elif a == "--ask":
        if len(argv) < 2:
            print("用法: rules --ask \"<你的问题>\"（按用户的话找规则）"); return 1
        return cmd_ask(' '.join(argv[1:]))
    elif a == "--ask-coverage":
        return cmd_ask_coverage()
    elif a == "--ask-golden":
        return cmd_ask_golden()
    elif a == "--dup":
        return cmd_dup()
    elif a == "--owner":
        if len(argv) < 2:
            print("用法: rules --owner <技能名>"); return 1
        return cmd_owner(argv[1])
    elif a.startswith("-"):
        print(f"未知选项 {a}｜可用: --ask \"<问题>\" / --ask-coverage / --orphan / "
              f"--dup / --owner <skill> / --scope / check / relapse")
        return 2      # 用法错，与 conductor 未知命令同口径
    else:
        return cmd_detail(argv)          # 其余当规则编号


if __name__ == "__main__":
    # 原写 `main(sys.argv[1:])`，返回值同样被丢 → 直接跑本文件恒 rc=0
    sys.exit(main(sys.argv[1:]) or 0)
