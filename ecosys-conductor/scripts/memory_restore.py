# -*- coding: utf-8 -*-
"""memory_restore.py —— 换模型 / 失忆兜底：一键恢复本用户全部真实输入

用途：
  当新模型只拿到压缩摘要、跑偏不知道用户要干啥时，跑本脚本即可从原始会话记录
  恢复用户 100% 真实输入（剥掉系统注入包装），按时间序落盘，供回溯完整意图。

用法：
  python3 memory_restore.py                  # 自动找最新会话 jsonl，提取到 /tmp/user_msgs_all.txt
  python3 memory_restore.py <会话jsonl路径>   # 指定文件
  python3 memory_restore.py --grep "关键词"   # 提取后顺便定位含关键词的历史发言

依据：M-R206 模型联动·全量记忆兜底（ecosys-conductor）。
"""
import json
import re
import os
import sys
import glob
from datetime import datetime

PROJ_DIR = "/root/.codebuddy/projects/workspace"
OUT = "/tmp/user_msgs_all.txt"


def find_latest():
    files = glob.glob(os.path.join(PROJ_DIR, "*.jsonl"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def clean(text):
    """剥掉系统注入包装，只留用户真实输入。"""
    m = re.search(r"<user_query>(.*?)</user_query>", text, re.S)
    if m:
        return m.group(1).strip()
    t = re.sub(r"<system-reminder.*?</system-reminder>", "", text, flags=re.S)
    t = re.sub(r"<previous_user_message>.*?</previous_user_message>", "", t, flags=re.S)
    return t.strip()


def extract(src, out):
    msgs = []
    with open(src, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "message" or obj.get("role") != "user":
                continue
            content = obj.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if isinstance(c, dict) and c.get("type") == "input_text":
                    t = clean(c.get("text", ""))
                    if not t or len(t) < 2:
                        continue
                    ts = obj.get("timestamp", 0)
                    try:
                        dt = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
                    except Exception:
                        dt = "?"
                    msgs.append((dt, t))
    with open(out, "w", encoding="utf-8") as f:
        for i, (dt, t) in enumerate(msgs, 1):
            f.write(f"===== 第{i}条 [{dt}] =====\n{t}\n\n")
    return len(msgs)


if __name__ == "__main__":
    args = sys.argv[1:]
    src = None
    grep_kw = None
    if args and not args[0].startswith("--"):
        src = args[0]
    if "--grep" in args:
        try:
            grep_kw = args[args.index("--grep") + 1]
        except Exception:
            grep_kw = None
    if not src:
        src = find_latest()
    if not src or not os.path.exists(src):
        print("❌ 找不到会话记录文件")
        sys.exit(1)
    n = extract(src, OUT)
    print(f"✅ 已从 {src}")
    print(f"   提取 {n} 条用户输入 → {OUT}（{os.path.getsize(OUT)} 字节）")
    print("   下一步：直接读该文件回溯完整意图；或加 --grep 定位关键词")
    if grep_kw:
        txt = open(OUT, encoding="utf-8").read()
        hits = 0
        for blk in txt.split("====="):
            if grep_kw in blk:
                print("----", blk.strip()[:400])
                hits += 1
        print(f"关键词「{grep_kw}」匹配 {hits} 块")
