"""T31 修 p044：段号口径统一 —— 加输出「文件行号」，不再只给「调用序号」

## 为什么改
T31 排查 P044 那 1 段真违规时发现口径混乱：
  · 探针报「第 32445~32447 条连续单调用」——这是**助手工具消息序号**
  · 我在别的脚本里按 **function_call 记录序号** 编号，同样位置得到不同号
  · 两者都不等于**文件行号**，而文件行号才是能直接定位的

结果：想核对「这段到底是什么」时，三个口径互相错位，花了 3 次脚本才对齐。

## 改法
段记录从 `(起始调用序号, calls)` 扩展为 `(起始调用序号, calls, 起始文件行号, 结束文件行号)`。
输出时同时给两种号，并在头部说明口径。**行号必须能直接定位**，这是排查的前提。

★ M-R134 教训复用：一个「说得出位置但定位不到」的报告，等于没报。
"""
import glob
import json
import os
import re
import sys

DEFAULT_GLOB = '/root/.codebuddy/projects/workspace/*.jsonl'
WINDOW = 200
MIN_LEN = 3

READ_TOOLS = {'Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'}
WRITE_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}
STATEFUL_TOOLS = {'Skill', 'present_files', 'TaskCreate', 'TaskUpdate', 'Agent',
                  'SendMessage', 'ExitPlanMode', 'EnterPlanMode'}
SIDE_EFFECT = re.compile(
    r'(>>?\s*[^|&\s])|(\brm\b)|(\bmv\b)|(\bcp\b)|(\bmkdir\b)|(\btouch\b)|'
    r'(git\s+(commit|add|push|checkout|reset))|(\bsed\s+-i)|(\btee\b)|'
    r'(>/)', re.I)


def scan_transcript(path):
    """返回 (总调用消息数, 单调用消息数, 段列表[(起始序号, 调用列表, 起始行, 结束行)])"""
    calls_by_msg = {}
    order = []
    first_line = {}
    last_line = {}
    ln_no = 0
    with open(path, encoding='utf-8') as f:
        for ln_no, line in enumerate(f, 1):
            if '"function_call"' not in line and '"role"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get('type') == 'function_call':
                mid = ((rec.get('providerData') or {}).get('messageId')
                       or rec.get('parentId'))
                if mid not in calls_by_msg:
                    calls_by_msg[mid] = []
                    order.append(mid)
                    first_line[mid] = ln_no
                last_line[mid] = ln_no
                name = (rec.get('name')
                        or (rec.get('function') or {}).get('name') or '?')
                args = rec.get('arguments') or rec.get('input') or ''
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                calls_by_msg[mid].append((name, args))
            elif rec.get('type') == 'message' and rec.get('role') == 'assistant':
                order.append('text:' + str(rec.get('id')))

    streaks = []
    run = []
    run_first = None
    run_last = None
    total = single = 0
    for mid in order:
        if str(mid).startswith('text:'):
            if len(run) >= MIN_LEN:
                streaks.append((total - len(run), run, run_first, run_last))
            run = []
            run_first = run_last = None
            continue
        cs = calls_by_msg.get(mid, [])
        total += 1
        if len(cs) == 1:
            single += 1
            run.append(cs[0])
            if run_first is None:
                run_first = first_line.get(mid)
            run_last = last_line.get(mid)
        else:
            if len(run) >= MIN_LEN:
                streaks.append((total - len(run), run, run_first, run_last))
            run = []
            run_first = run_last = None
    if len(run) >= MIN_LEN:
        streaks.append((total - len(run), run, run_first, run_last))
    return total, single, streaks


def classify(streak_calls):
    tools = [t for t, _a in streak_calls]
    if any(t in WRITE_TOOLS for t in tools):
        return '黄'
    if any(t in STATEFUL_TOOLS for t in tools):
        return '灰'
    for t, a in streak_calls:
        if t == 'Bash':
            if SIDE_EFFECT.search(a or ''):
                return '黄'
        elif t not in READ_TOOLS:
            return '灰'
    return '红'


def main(argv):
    paths = [a for a in argv[1:] if not a.startswith('-')]
    full = '--full' in argv
    if not paths:
        paths = sorted(glob.glob(DEFAULT_GLOB), key=os.path.getmtime)[-1:]
    if not paths:
        print('⚠️ 未找到会话转录文件')
        return 0

    red_any = False
    for p in paths:
        total, single, streaks = scan_transcript(p)
        recent = [(s, l, f, t) for s, l, f, t in streaks if s > total - WINDOW]
        graded = [(s, l, classify(l), f, t) for s, l, f, t in recent]
        n_red = sum(1 for _s, _l, g, _f, _t in graded if g == '红')
        n_yel = sum(1 for _s, _l, g, _f, _t in graded if g == '黄')
        n_gray = sum(1 for _s, _l, g, _f, _t in graded if g == '灰')
        print(f'📄 {os.path.basename(p)}：助手工具消息 {total} 条，'
              f'单调用 {single} 条；全量串行段 {len(streaks)} 段，'
              f'近窗 {WINDOW} 条内 {len(recent)} 段'
              f'（🔴 真违规 {n_red} ／ 🟡 含写待核 {n_yel} ／ ⚪ 有状态 {n_gray}）')
        show = graded if full else [g for g in graded if g[2] == '红']
        for s, l, g, f, t in show[:10]:
            icon = {'红': '🔴', '黄': '🟡', '灰': '⚪'}[g]
            tools = {}
            for tname, _a in l:
                tools[tname] = tools.get(tname, 0) + 1
            print(f'   {icon} 调用#{s}~{s+len(l)-1}（文件行 {f}~{t}）：{tools}')
        if n_red:
            red_any = True
            print(f'   ↑ 这 {n_red} 段**全是只读操作**，本可一次性并发（M-R145）')
        if n_yel and not full:
            print(f'   （另有 {n_yel} 段含写操作，按依赖关系属合法串行，不报红；--full 查看）')

    if red_any:
        print('❌ 存在「全只读却串行」段：独立读操作须同消息并发发出（M-R145）')
        return 1
    print('✅ 未见「全只读却串行」——含写操作的串行属合法依赖，不计违规')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
