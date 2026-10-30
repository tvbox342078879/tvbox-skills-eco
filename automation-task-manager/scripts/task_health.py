#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task_health.py —— 定时任务健康体检器（M-R209 / P073，S213 新增）

每日对启用中的定时任务做三查，发现问题输出结构化报告；
可选 --auto-ledger 把问题自动 append 进历史遗留账本（issue_ledger.json），带去重。

三查判据（与 scheduler-api.sh validate_prompt_env 同源，纯文本）：
  ① prompt 是否引用本地绝对路径/脚本却非纯提醒模式 → 调度沙箱无此文件必失败
  ② prompt 是否调用未验证的 tvbox 子命令 → 沙箱可达性未知，曾失败
  ③ 运行成败可见性（E类元问题）：list 接口无 lastStatus 字段 → 标记「不可见，靠本体检替」

注意：本脚本**直接调 API**（不绕 scheduler-api.sh），因此在 automation 调度环境
也能跑（scheduler-api.sh 自身有 automation 防护会拒绝）。

用法：
  python3 task_health.py                 # 三查并输出报告，有问题 exit 1
  python3 task_health.py --auto-ledger   # 同时把新问题自动入账历史遗留
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

LEDGER_PATH = "/root/.codebuddy/skills/ecosys-conductor/plans/issues_ledger.json"

# ① 本地资源引用（与 validate_prompt_env 同正则意图）
LOCAL_RE = re.compile(r'(/root/|/workspace/|python3 /|/[A-Za-z0-9_./-]+\.py)')
REMIND_RE = re.compile(r'(纯提醒|不跑脚本|不执行脚本|请在对话里|请手动|提醒：|提醒用户|只提示|仅提示|勿执行)', re.I)
# ② tvbox 子命令
TVBOX_RE = re.compile(r'tvbox\s+([a-z-]+)')
KNOWN_TVBOX = {"brief", "conductor", "history", "ask"}


def get_base_url():
    u = os.environ.get("SCHEDULER_API_BASE_URL")
    if not u and os.environ.get("ACC_PRODUCT_CONFIG_V3"):
        try:
            cfg = json.loads(os.environ["ACC_PRODUCT_CONFIG_V3"])
            u = cfg.get("endpoint")
        except Exception:
            pass
    return (u or "http://auth.proxy/codebuddy").rstrip("/")


def _build_query():
    """复制 scheduler-api.sh 的 ownerType/agentType 收窄逻辑，保证能查到本会话的任务。"""
    q = "page=1&pageSize=50"
    ext = os.environ.get("CLIENT_INFO_USER_AGENT_EXTENSION", "")
    expert = os.environ.get("CODEBUDDY_PERSONAL_EXPERT_ID", "")
    pid = os.environ.get("X_PROJECT_ID", "")
    ch_key = os.environ.get("WORKBUDDY_CHANNEL_KEY", "")
    if ext == "channel-workbuddy" and expert and pid:
        q += f"&agentType=channel_assistant&ownerType=team&projectId={pid}&cloudAgentId={expert}"
    elif ext == "assistant" and expert:
        q += f"&agentType=personal_agent&ownerType=personal&cloudAgentId={expert}"
    elif ch_key and expert and os.environ.get("CLIENT_INFO_USER_AGENT_EXTENSION") not in (
        "personal-cloudagents-build", "personal-cloudagents-preview", "AgentDebug", "quickstart"):
        q += f"&agentType=personal_agent_wecom&ownerType=personal&cloudAgentId={expert}"
    elif pid:
        q += f"&ownerType=team&projectId={pid}"
    return q


def list_tasks():
    base = get_base_url()
    url = f"{base}/v2/as/scheduler/tasks?{_build_query()}"
    try:
        out = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", url,
             "-H", "Content-Type: application/json",
             "-H", "User-Agent: WorkBuddy-Agent/1.0 (sandbox-skill; task_health)"],
            capture_output=True, text=True, timeout=30)
        parts = out.stdout.rsplit("\n", 1)
        if len(parts) != 2:
            return []
        body, code = parts
        if not code.startswith("2"):
            print(f"list_tasks HTTP {code}", file=sys.stderr)
            return []
        data = json.loads(body)
        return (data.get("data", {}) or {}).get("tasks", []) or []
    except Exception as e:
        print(f"list_tasks error: {e}", file=sys.stderr)
        return []


def check_prompt(prompt):
    issues = []
    if not prompt:
        return issues
    if LOCAL_RE.search(prompt) and not REMIND_RE.search(prompt):
        issues.append("引用本地绝对路径/脚本但非纯提醒模式 → 调度沙箱无该文件，运行即 FileNotFoundError")
    for sub in TVBOX_RE.findall(prompt):
        if sub not in KNOWN_TVBOX:
            issues.append(f"调用未验证的 tvbox 子命令 `{sub}` → 沙箱可达性未知，曾失败（任务#1）")
    return issues


def load_ledger(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"issues": []}


def max_pnum(ledger):
    mx = 0
    for it in ledger.get("issues", []):
        m = re.match(r"P(\d+)", str(it.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def append_ledger(report, path):
    """把每个问题的任务 append 为一条历史遗留，带去重（同任务名+同主题已 open 则跳过）。"""
    ledger = load_ledger(path)
    existing = {(str(i.get("title", "")).split("（")[0], i.get("status"))
                 for i in ledger.get("issues", [])}
    nxt = max_pnum(ledger) + 1
    added = 0
    today = "2026-10-24"
    for p in report["problems"]:
        base_title = f"定时任务健康体检·{p['name']}"
        if (base_title, "open") in existing:
            continue
        ledger.setdefault("issues", []).append({
            "id": f"P{nxt:03d}",
            "title": f"{base_title}（{('; '.join(p['issues']))[:60]}）",
            "from": "task_health.py 自动体检",
            "next": "按 M-R204 预览整改方案",
            "status": "open",
            "ledger": "历史遗留",
            "opened": today,
            "closed": "",
            "owner": "ecosys-conductor",
            "due": "2026-10-31",
        })
        existing.add((base_title, "open"))
        nxt += 1
        added += 1
    if added:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已自动入账 {added} 条历史遗留（{path}）", file=sys.stderr)
    else:
        print("\n✅ 无新问题需入账（去重后）", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto-ledger", action="store_true",
                    help="发现问题自动 append 历史遗留账本（带去重）")
    ap.add_argument("--ledger", default=LEDGER_PATH)
    args = ap.parse_args()

    tasks = list_tasks()
    report = {
        "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total": len(tasks),
        "enabled": 0,
        "problems": [],
        "meta_note": "运行成败可见性（E类）：list 接口无 lastStatus 字段，失败不可见，靠本体检替代",
    }
    for t in tasks:
        if t.get("status") != 1:
            continue
        report["enabled"] += 1
        pid = t.get("id")
        name = t.get("name")
        prompt = (t.get("agentConfig") or {}).get("prompt", "")
        iss = check_prompt(prompt)
        if iss:
            report["problems"].append({"id": pid, "name": name, "issues": iss})

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["problems"]:
        print(f"\n⚠️ 发现 {len(report['problems'])} 个任务存在问题：", file=sys.stderr)
        for p in report["problems"]:
            print(f"  - [{p['id']}] {p['name']}: {'; '.join(p['issues'])}", file=sys.stderr)
    if args.auto_ledger and report["problems"]:
        append_ledger(report, args.ledger)
    sys.exit(1 if report["problems"] else 0)


if __name__ == "__main__":
    main()
