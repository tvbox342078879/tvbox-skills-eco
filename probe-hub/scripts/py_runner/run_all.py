#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
py_runner/run_all.py —— 全量 py spider 沙箱真跑编排器（S116）。

对作者原配置的 46 个 py 站逐个真跑（子进程隔离 + 超时兜底），
产出「真拉到片单」深度判定，与 S115 上游层结论交叉验证。

用法：python3 run_all.py [--sites <配置json>] [--out <结果json>]
"""
import json
import os
import subprocess
import sys
import tempfile

# ---------- M-R114 统一并发入口（S110） ----------
# 本文件原先就地写死 max_workers=N，数字无依据。改为按档位表取上限。
# 保留一个同名 shim，避免改动所有调用点（改动面越小越安全）。
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded as _run_bounded  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, 'worker.py')
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from core import ORIG_DIR  # noqa: E402  # 共享核心(B3)：原仓库目录，env TVBOX_ORIG_DIR 可覆写
BASE = 'https://clun.top'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0'
CACHE = os.path.join(HERE, '_pycache')
ORIG = '%s/cluntop_box原文_1018.json' % ORIG_DIR
OUT = os.path.join(HERE, 'py_run_result.json')


def fetch(url, timeout=20):
    import urllib.request
    from urllib.parse import quote, urlsplit, urlunsplit
    # URL 路径含中文（./py/农民影视.py）需编码，否则 urllib 炸 ascii
    parts = urlsplit(url)
    url = urlunsplit((parts.scheme, parts.netloc,
                      quote(parts.path), parts.query, parts.fragment))
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def to_abs(u):
    u = str(u)
    if u.startswith('http'):
        return u
    return BASE + u[1:] if u.startswith('./') else BASE + '/' + u.lstrip('/')


def ext_string(ext):
    """配置 ext → init(extend) 实参。dict→JSON串；相对路径串→取其文件内容；其余原样。"""
    if ext is None or ext == '':
        return ''
    if isinstance(ext, dict):
        return json.dumps(ext, ensure_ascii=False)
    s = str(ext)
    if s.startswith('./'):
        try:
            return fetch(to_abs(s), timeout=15)
        except Exception:
            return ''
    return s


def run_one(site):
    name = site['name']
    api = str(site.get('api', ''))
    fname = api.rsplit('/', 1)[-1]
    local = os.path.join(CACHE, fname)
    if not os.path.exists(local):
        try:
            src = fetch(to_abs(api))
            if not src or len(src) < 200:
                return name, {'verdict': '失败', 'evidence': f'源码不可下载({len(src)}B)'}
            open(local, 'w', encoding='utf-8').write(src)
        except Exception as e:
            return name, {'verdict': '无法判定', 'evidence': f'源码下载失败: {type(e).__name__}（非死）'}
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as tf:
        out_path = tf.name
    try:
        cmd = [sys.executable, WORKER, local, ext_string(site.get('ext')), out_path]
        try:
            # S116 修复：90s 对多 host 探测站点太紧（瓜子/如意/雪落/剧OK/YYMP3
            # 全撞超时误判无法判定），放宽到 180s；仍超时的才记无法判定
            subprocess.run(cmd, capture_output=True, timeout=180)
            r = json.load(open(out_path))
        except subprocess.TimeoutExpired:
            r = {'verdict': '无法判定', 'evidence': 'worker 超时 180s（挂起/端口占用，非死）'}
        except Exception as e:
            r = {'verdict': '无法判定', 'evidence': f'worker 异常: {type(e).__name__}'}
        return name, r
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def main():
    os.makedirs(CACHE, exist_ok=True)
    cfg = sys.argv[sys.argv.index('--sites') + 1] if '--sites' in sys.argv else ORIG
    out = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else OUT
    sites = [s for s in json.load(open(cfg, encoding='utf-8'))['sites']
             if str(s.get('api', '')).endswith('.py')]
    print(f'待真跑 py 站：{len(sites)}')
    results = {}
    # ★ S163：改走统一入口（run_one 会起子进程跑 worker.py，属本地计算——D 档更贴切，
    #   但子进程本身要拉远端 py 源，按原意仍留 B 档；keep_order 保证打印顺序=输入顺序）
    rep = _run_bounded(sites, run_one, tier='B', label='py-run-all',
                       keep_order=True, on_error='record')
    for r in rep.results:
        if r is None:
            # 线程异常 → 该站记无法判定，不装成「跑过了」（M-R60）
            print('  ⚠️ 某站线程异常（并发报告已记录）——记无法判定')
            continue
        name, rr = r
        results[name] = rr
        mark = {'通过': '✅', '失败': '❌', '无法判定': '⚠️'}.get(rr['verdict'], '?')
        print(f"  {mark} {name}: {rr.get('evidence', '')[:75]}（{rr.get('route', '-')}）")
    json.dump(results, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    from collections import Counter
    print('\n统计:', dict(Counter(r['verdict'] for r in results.values())))
    print(f'明细 → {out}')


if __name__ == '__main__':
    main()
