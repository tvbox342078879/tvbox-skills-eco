#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S115 v2 内容层测活（升级版）。

v1 教训：py/js 源码 host 写法各异（多 host 列表/发布页/home_url/IP/混淆），
单正则漏抓 32 站；HTTP 0 未区分「域名消失 / 拒连 / 超时」——三者证据强度不同。

v2 策略：
  1. 候选提取：显式模式（self.hosts 列表/self.host/home_url/publish_url/HOST/baseUrl）
     → 失败则退首部 http 字面量泛匹配（噪音域名过滤）；
  2. 引擎+规则分离：api 若是引擎文件（drpy2.min.js/live2vod.js/mod.js…），
     真实源在 ext（.json 取 URL / .js 取 host）；
  3. curl 错误分级：DNS 失败→域名消失(失败)；拒连→失败；超时/重置→无法判定(网络层)；
     403→看响应体是否 WAF/CDN 质询（是则无法判定，非死）；
  4. 多 host 候选：任一可达即通过；
  5. 混淆识别：有 charMap/分支加密且无 URL 字面量 → 无法判定（需端测）。
"""
import json
import os
import re
import subprocess
import sys

BASE = 'https://clun.top'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from core import ORIG_DIR, http_get  # noqa: E402  # 共享核心(B3)：原仓库目录，env TVBOX_ORIG_DIR 可覆写
ORIG = '%s/cluntop_box原文_1018.json' % ORIG_DIR
HERE_CP = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE_CP, 'sweep_v2.json')  # 不再绑定会话 artifact 路径

sys.path.insert(0, '/root/.codebuddy/skills/probe-hub/scripts')
import livecheck  # noqa: E402

# ---------- M-R114 统一并发入口（S110） ----------
# 本文件原先就地写死 max_workers=N，数字无依据。改为按档位表取上限。
# 保留一个同名 shim，避免改动所有调用点（改动面越小越安全）。
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded as _run_bounded  # noqa: E402


ENGINES = ('drpy2.min.js', 'live2vod.js', 'mod.js', 'crypto-js.js', 'jinja.js',
           'gbk.js', 'sortName.js', 'alist.min.js', 'node-rsa.js', 'cheerio.min.js')
NOISE_HOST = ('doubanio', 'static', 'cdn', 'github', 'githubusercontent', 'google',
              'gstatic', 'jsdelivr', 'telegram', 't.me', 'okzy', 'pic', 'img',
              'huoshan', 'bytedance', '127.0.0.1', 'localhost', 'clun.top',
              'w3.org', 'schema.org')


def curl_full(url, timeout=10):
    """返回 (http_code, exit_code, body_head)。code=0 时看 exit 分级。"""
    cmd = ['curl', '-s', '-L', '--compressed', '--connect-timeout', '6',
           '--max-time', str(timeout), '-A', UA, '-w', '\n%{http_code}', url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 4)
        out = p.stdout.decode('utf-8', 'replace')
        body, _, tail = out.rpartition('\n')
        try:
            code = int(tail.strip())
        except Exception:
            code = 0
        return code, p.returncode, body[:600]
    except Exception:
        return 0, -1, ''


_cache = {}


def fetch(url, timeout=15):
    if url in _cache:
        return _cache[url]
    st, payload, _ = http_get(url, timeout=timeout, ua=UA, as_text=True)
    body = payload if (st == 'ok' and payload) else ''
    _cache[url] = body
    return body


def to_abs(u):
    u = str(u)
    if u.startswith('http'):
        return u
    if u.startswith('./'):
        return BASE + u[1:]
    return BASE + '/' + u.lstrip('/')


def is_junk(name):
    return bool(re.fullmatch(r'-+\s*\d+\s*-+', (name or '').strip()))


def classify_code(code, exit_code, body):
    """HTTP/curl 结果 → 三态。"""
    if code in (200, 301, 302, 303, 307, 308):
        return '通过'
    if code == 403:
        low = (body or '').lower()
        if any(k in low for k in ('cloudflare', 'cf-ray', 'challenge', 'captcha',
                                  'just a moment', 'security check', 'ray id')):
            return '无法判定'
        return '失败'
    if code in (404, 410, 451, 520, 521, 522, 523):
        return '失败'
    if code and code >= 500:
        return '无法判定'
    # code == 0：按 curl 退出码分级
    if exit_code == 6:
        return '失败'      # could not resolve host：域名消失
    if exit_code == 7:
        return '失败'      # connection refused：服务已停
    if exit_code in (28, 56, 35, 18):
        return '无法判定'  # timeout / reset / SSL / partial：网络层，非死
    return '无法判定'


def extract_candidates(src, kind):
    """从 py/js 源码提取候选上游 URL 列表（有序，显式模式优先）。

    实测覆盖的写法（v3 加固，S115）：
      self.hosts=[多host] / self.host=""/ self.home_url / publish_url / HOST(+PORT)
      DEFAULT_HOST / baseUrl / 发布页动态解析(gethost 里 fetch 的 URL) / 泛字面量。
    """
    cands = []
    # ① 列表型：hosts 列表
    for m in re.finditer(r'self\.hosts\s*=\s*\[(.*?)\]', src, re.S):
        cands += re.findall(r'["\']([^"\']+)["\']', m.group(1))
    # ② 单值型
    val_pats = [r'self\.host\s*=\s*["\']([^"\']+)["\']',
                r'self\.home_url\s*=\s*["\']([^"\']+)["\']',
                r'publish_url\s*=\s*["\']([^"\']+)["\']',
                r'DEFAULT_HOST\s*=\s*["\']([^"\']+)["\']',
                r'default_host\s*=\s*["\']([^"\']+)["\']',
                r'(?:^|\n)\s*HOST\s*=\s*["\']([^"\']+)["\']',
                r'(?:^|\n)\s*host\s*=\s*["\']([^"\']+)["\']',
                r'baseUrl\s*=\s*["\']([^"\']+)["\']',
                r'base_url\s*=\s*["\']([^"\']+)["\']']
    for pat in val_pats:
        for m in re.finditer(pat, src):
            cands.append(m.group(1))
    # ③ 泛匹配：全文 http(s) 字面量（host 级，过滤噪音）——兜底发布页/动态解析
    if not cands:
        for u in re.findall(r'https?://[A-Za-z0-9.-]+(?::\d+)?', src):
            host = u.lower()
            if not any(n in host for n in NOISE_HOST):
                cands.append(u)
    # 归一化 + 去重
    out, seen = [], set()
    ip_port = re.search(r'PORT\s*=\s*(\d+)', src)
    for c in cands:
        c = str(c).strip().rstrip('/')
        if not c:
            continue
        if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?', c):  # 裸 IP（可带端口）
            c = 'http://' + c
        elif re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', c) and ip_port:
            c = f'http://{c}:{ip_port.group(1)}'
        if not c.startswith('http'):
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def probe_spider_site(s):
    """py/js 站：下载源码 → 提取候选 → 逐个实测。"""
    name = s.get('name', '')
    api = str(s.get('api', '')).strip()
    ext = s.get('ext', '')
    kind = 'py' if api.endswith('.py') else 'js'

    file_url = to_abs(api)
    src = fetch(file_url)
    if not src or len(src) < 200:
        return (kind, file_url, '失败', f'spider 文件不可下载（{len(src)}B）')
    if src.lstrip()[:15].lower().startswith(('<!doctype', '<html')):
        return (kind, file_url, '失败', 'HTTP 200 但是 HTML 兜底页（SPA 假活，文件不存在）')

    # ext.host 覆盖优先（py 站点惯例）
    cands = []
    if isinstance(ext, dict) and ext.get('host'):
        cands.append(str(ext['host']).rstrip('/'))

    # 引擎+规则分离：api 是引擎文件时，真实源在 ext
    base_name = api.rsplit('/', 1)[-1]
    if base_name in ENGINES and ext:
        ext_url = to_abs(ext)
        ext_body = fetch(ext_url)
        if ext_body.lstrip()[:1] in '{[':
            for u in re.findall(r'https?://[^\s"\'\\}]+', ext_body):
                u = u.split('{')[0].split('#')[0].rstrip('/')
                if not any(n in u.lower() for n in NOISE_HOST):
                    cands.append(u)
        elif ext_body and not ext_body.lstrip()[:15].lower().startswith(('<!doctype', '<html')):
            cands += extract_candidates(ext_body, kind)
        else:
            return (kind, ext_url, '失败', f'引擎规则文件不存在（{ext_url} 返 HTML 兜底页）')

    cands += extract_candidates(src, kind)

    if not cands:
        if re.search(r'charMap|_BRANCH_.*B64|zlib\.decompress', src[:20000]):
            return (kind, file_url, '无法判定', '源码加密混淆，无 URL 字面量（需端测）')
        return (kind, file_url, '无法判定', '源码内未提取到任何上游候选')

    tried = []
    for c in cands[:6]:
        code, ec, body = curl_full(c if c.endswith('/') else c + '/')
        v = classify_code(code, ec, body)
        tried.append(f'{c}→{code or ec}')
        if v == '通过':
            return (kind, c, '通过', f'HTTP {code}·上游可达（源码 {len(src)}B）')
    # 全部未过：按最强证据定性（有明确死证据报失败，否则无法判定）
    codes = []
    for c in cands[:6]:
        code, ec, body = curl_full(c if c.endswith('/') else c + '/')
        codes.append(classify_code(code, ec, body))
    if '失败' in codes and '无法判定' not in codes:
        return (kind, cands[0], '失败', '；'.join(tried))
    if '失败' in codes:
        return (kind, cands[0], '无法判定', '部分候选死、部分网络层不通：' + '；'.join(tried))
    return (kind, cands[0], '无法判定', '；'.join(tried) + '（网络层，非死）')


def probe_csp_site(s):
    api = str(s.get('api', '')).strip()
    ext = s.get('ext', '')
    if not ext:
        return ('csp', None, '无法判定', 'csp 无 ext 配置')
    ext_url = to_abs(ext)
    body = fetch(ext_url)
    if not body or body.lstrip()[:1] not in '{[':
        return ('csp', ext_url, '失败', f'ext 配置不可下载或非 JSON（{len(body)}B）')
    urls = []
    for u in re.findall(r'https?://[^\s"\'\\}]+', body):
        u = u.split('{')[0].rstrip('/?&')
        if u and u not in urls:
            urls.append(u)
    if not urls:
        return ('csp', ext_url, '无法判定', 'ext 内未解析出采集 URL')
    # 取最有代表性的一条（优先含 show/list/vod 的路径，否则 origin）
    test = next((u for u in urls if any(k in u for k in ('show', 'list', 'vod', 'channel'))), urls[0])
    code, ec, tbody = curl_full(test if test.endswith('/') else test + '/')
    v = classify_code(code, ec, tbody)
    ev = {'通过': f'HTTP {code}·采集源可达（{test}）',
          '失败': f'HTTP {code or ec}·采集源明确不可达（{test}）',
          '无法判定': f'HTTP {code or ec}·网络层（{test}，非死）'}[v]
    return ('csp', test, v, ev)


def probe_direct_site(s):
    api = str(s.get('api', '')).strip()
    if not api:
        return ('direct', None, '跳过', '无 api')
    v, ev, _ = livecheck.check_auto(api, timeout=12)
    return ('direct', api, v, ev)


def probe_site(s):
    name = s.get('name', '')
    if is_junk(name):
        return ('junk', None, '跳过', '占位分隔条目，非站点')
    api = str(s.get('api', '')).strip()
    try:
        if api.startswith('csp_'):
            return probe_csp_site(s)
        if api.endswith('.py') or api.endswith('.js'):
            return probe_spider_site(s)
        return probe_direct_site(s)
    except Exception as e:
        return ('?', None, '无法判定', f'异常 {e}')


def main():
    if not os.path.exists(ORIG):
        print('⚠️ 原仓库文件缺失：%s（设置环境变量 TVBOX_ORIG_DIR 指向真实原仓库目录后重试）' % ORIG)
        return
    d = json.load(open(ORIG, encoding='utf-8'))
    sites = d.get('sites', [])
    rows = []
    # ★ S163：改走统一入口（原 `_run_bounded_pool` 仍自建线程池，绕过写集合登记/护栏/实测）
    rep = _run_bounded(sites, probe_site, tier='B', label='content-probe',
                       keep_order=True, on_error='record')
    for s, r in zip(sites, rep.results):
        if r is None:
            kind, up, v, ev = ('?', None, '无法判定', '探测线程异常（并发报告已记录）')
        else:
            kind, up, v, ev = r
        rows.append({'name': s.get('name', ''), 'key': s.get('key', ''),
                     'type': s.get('type'), 'kind': kind, 'api': s.get('api', ''),
                     'upstream': up, 'verdict': v, 'evidence': ev})

    rows.sort(key=lambda r: (r['kind'], r['verdict'], r['name']))
    json.dump(rows, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    from collections import Counter
    stat = Counter((r['kind'], r['verdict']) for r in rows)
    print(f'{"kind":10} {"verdict":8} count')
    for (k, v), n in sorted(stat.items()):
        print(f'{k:10} {v:8} {n}')
    print('\n--- 失败清单 ---')
    for r in rows:
        if r['verdict'] == '失败':
            print(f"  [{r['kind']}] {r['name']}  {r['evidence']}")
    print('\n--- 无法判定清单 ---')
    for r in rows:
        if r['verdict'] == '无法判定':
            print(f"  [{r['kind']}] {r['name']}  {r['evidence'][:100]}")
    print('\n--- 通过清单 ---')
    for r in rows:
        if r['verdict'] == '通过':
            print(f"  [{r['kind']}] {r['name']}  {r['upstream']}")
    print(f'\n明细 → {OUT}')


if __name__ == '__main__':
    main()
