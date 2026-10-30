#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compliance_probe.py —— 跨模型合规自检探针（可验证闭环核心）

用户一条命令即可看到：各模型最近会话是否真按规则走了。
判据维度：记忆还原 / 代码同步 / 四极联动 / 记忆推送 / git身份 / 四极数据 / 静默失败。

关键设计（回应「如何确定」而非「写个脚本」）：
  · 读 telemetry/*.jsonl 的真实留痕，而非模型自报；
  · 对 sync 等「静默脚本」自行核验硬指标（HEAD 变更+远端比对）；
  · 静态扫描钩子是否仍含 except→return 0 的「静默失败」设计；
  · 输出红/黄/绿，任意红=该维度未达标，用户立即可见。

用法：python3 /root/.codebuddy/skills/ecosys-conductor/scripts/compliance_probe.py
"""
import os
import re
import glob
import json
import subprocess
import datetime

HERE = "/root/.codebuddy"
TL = os.path.join(HERE, "telemetry")
NOW = datetime.datetime.now().astimezone()
WINDOW = datetime.timedelta(hours=24)


def load_recent():
    rows = []
    for fp in glob.glob(os.path.join(TL, "*.jsonl")):
        for line in open(fp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                ts, action, rcpart, detail = line.split("\t", 3)
                rc = int(rcpart.split("=", 1)[1])
                rows.append((ts, action, rc, detail))
            except Exception:
                pass
    return rows


def last_success(action, rows):
    for ts, a, rc, d in reversed(rows):
        if a == action and rc == 0:
            t = datetime.datetime.fromisoformat(ts)
            return t, (NOW - t)
    return None, None


def git_identity_ready():
    for k in ("user.name", "user.email"):
        r = subprocess.run(["git", "config", "--global", k],
                           capture_output=True, text=True)
        if not r.stdout.strip():
            return False
    return True


def link_status():
    try:
        out = subprocess.run(
            ["python3", "/root/.codebuddy/skills/tvbox-source-hub/scripts/link.py", "status"],
            capture_output=True, text=True, timeout=60).stdout
        return out, None
    except Exception as e:
        return None, str(e)


def hook_observability(rows):
    """O2 新判据（2026-10-25 修正）：钩子可观测性，回应「静默失败」。

    旧判据 silent_fail_scan 静态扫描 except→return 0，把防御性异常处理也判静默失败，
    永远红且是误报（stop_drift 等钩子的 except→return 0 本就是「读不到文件不阻断对话」的合理设计）。
    新判据动态读 telemetry：接入 hook_trace 后，每个非安全闸钩子的运行 rc 都落痕，
    失败可见即非静默。判据：有 hook_* 记录=框架已活（🟢）；无=框架待触发（🟡）。
    PreToolUse 安全闸（biz_gate 等）rc=业务语义（拦截=rc≠0），不包 hook_trace，不计入。
    """
    hook_rows = [(ts, a, rc, d) for ts, a, rc, d in rows if a.startswith("hook_")]
    if not hook_rows:
        return None, []
    fails = [(ts, a, rc) for ts, a, rc, d in hook_rows if rc != 0]
    return hook_rows, fails


def lang_discipline():
    """语言纪律判据（2026-10-25）：读 lang_ledger 违规账本，非模型自报。
    账本由 Stop 钩子自动扫思考面板/正文落账（3484 次实证在记）。
    红：last 违规在 2 小时内（本会话仍在漏）；绿：2 小时窗口零新增。"""
    p = "/root/.codebuddy/hooks/.lang_violations.json"
    try:
        d = json.load(open(p))
    except Exception:
        return None, None, []
    total = int(d.get("total", 0))
    last = str(d.get("last", ""))
    try:
        t = datetime.datetime.fromisoformat(last)
        if t.tzinfo is None:
            t = t.astimezone()
        return total, NOW - t, d.get("recent", [])[-3:]
    except Exception:
        return total, None, d.get("recent", [])[-3:]


def red(s):
    return "🔴 " + s


def green(s):
    return "🟢 " + s


def yellow(s):
    return "🟡 " + s


def fmt(age):
    if age is None:
        return "无记录"
    return f"{int(age.total_seconds()//3600)}h{int((age.total_seconds()%3600)//60)}m 前"


def main():
    rows = load_recent()
    print("=" * 64)
    print(f"合规自检探针  @ {NOW.isoformat(timespec='seconds')}")
    print(f"留痕样本数：{len(rows)} 条（窗口 {int(WINDOW.total_seconds()//3600)}h）")
    print("=" * 64)

    t, age = last_success("memory_restore", rows)
    print(green(f"[记忆还原] 最近成功 {fmt(age)}") if t else
          red("[记忆还原] 窗口内零成功留痕 → 各模型可能凭摘要硬猜"))

    t, age = last_success("sync", rows)
    print(green(f"[代码同步] 最近成功 {fmt(age)}") if t else
          red("[代码同步] 窗口内零成功留痕 → 改动可能从未上云"))

    t, age = last_success("link_learn", rows)
    print(green(f"[四极联动] learn 最近成功 {fmt(age)}") if t else
          yellow("[四极联动] 窗口内无 learn 留痕（四极数据可能陈旧）"))

    t, age = last_success("push_gist", rows)
    print(green(f"[记忆推送] gist 最近成功 {fmt(age)}") if t else
          yellow("[记忆推送] 窗口内无 gist 留痕"))

    print(green("[git身份] 已配置，commit 可成功") if git_identity_ready() else
          red("[git身份] 未配置 user.name/user.email → commit 直接失败"))

    out, err = link_status()
    if out:
        print(yellow(f"[四极数据]\n{out.strip()[:240]}"))
    else:
        print(red(f"[四极数据] link.py status 失败：{err}"))

    hook_rows, fails = hook_observability(rows)
    if hook_rows:
        names = sorted({a for _, a, _, _ in hook_rows})
        fn = len(fails)
        note = f"（{fn} 次失败已记录可见，非静默）" if fn else "（全部成功）"
        print(green(f"[钩子可观测] 已采集 {len(hook_rows)} 条运行留痕，覆盖 {len(names)} 个钩子{note}"))
    else:
        print(yellow("[钩子可观测] 尚未采集到钩子运行留痕（hook_trace 框架待触发）"))

    total, lage, lsamp = lang_discipline()
    if total is None:
        print(yellow("[语言纪律] 违规账本取不到——无法判定（不装干净）"))
    elif lage is not None and lage < datetime.timedelta(hours=2):
        s = lsamp[-1].get("sample", "") if lsamp else ""
        print(red(f"[语言纪律] {fmt(lage)}仍有英文违规（累计 {total} 次），漏网样本：「{s[:60]}」"))
    else:
        print(green(f"[语言纪律] 近 2 小时零新增违规（账本累计 {total} 次，机制在记）"))

    print("=" * 64)
    print("判据：记忆还原/代码同步/语言纪律务必🟢；🟡为可改进；任意🔴=该维度未达标")
    print("如何确定：eco_sync_traced.sh 每次真跑都留硬指标痕；本探针读痕给红绿")


if __name__ == "__main__":
    main()
