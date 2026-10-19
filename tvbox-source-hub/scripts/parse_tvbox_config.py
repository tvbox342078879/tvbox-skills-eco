#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_tvbox_config.py —— 健壮的 TVBox 配置接口解析器（可复用能力，固化本轮踩坑）。

背景坑：jar 型/FongMi 配置接口常混用 `//` 行注释、`#`/`####` 行注释、尾随逗号，
且把巨量 sites 压在超长行（可达 2601 字符）里；直接 json.loads / json5.loads 常因
注释里的引号、括号把「字符串边界」搞错位而整篇失败（实测手写状态机、json5 均会在
`"token":"http://127.0.0.1..."` 处把 `//` 误判为注释而截断）。

策略（外科式，稳）：
  1. 括号匹配定位 `sites` / `lives` 数组（匹配器尊重字符串 + 跳过 // 与 # 注释）；
  2. 数组内逐顶层 `{...}` 对象切分；
  3. 每个对象单独 json5.loads（json5 原生吃 // 注释与尾随逗号，单对象坏不影响整体）；
  4. 顶层标量字段（spider/wallpaper/logo...）用正则抽取，`#` 注释行只做「整行」安全剥离。

用法：
  python3 parse_tvbox_config.py <config.json> [-o out.clean.json] [--hosts hosts.json]
  # 也可 import: from parse_tvbox_config import parse_config; d = parse_config(text)
"""
import sys, json, re

try:
    import json5
except Exception:
    json5 = None


def _find_array(text, key):
    """括号匹配定位 "key":[ ... ] 的数组内部文本（尊重字符串 + 跳过 // 与 # 注释）。"""
    m = re.search(r'"%s"\s*:\s*\[' % re.escape(key), text)
    if not m:
        return None
    start = m.end() - 1  # 指向 '['
    depth = 0; in_str = False; q = ''; i = start; n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == '\\':
                i += 2; continue
            if c == q:
                in_str = False
            i += 1; continue
        if c == '"' or c == "'":
            in_str = True; q = c; i += 1; continue
        if (c == '/' and i + 1 < n and text[i + 1] == '/') or c == '#':
            while i < n and text[i] != '\n':
                i += 1
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return None


def _split_objects(t):
    """把数组内部文本切成一串顶层 {...} 对象（同样尊重字符串 + 跳注释）。"""
    objs = []; depth = 0; in_str = False; q = ''; start = None; i = 0; n = len(t)
    while i < n:
        c = t[i]
        if in_str:
            if c == '\\':
                i += 2; continue
            if c == q:
                in_str = False
            i += 1; continue
        if c == '"' or c == "'":
            in_str = True; q = c; i += 1; continue
        if (c == '/' and i + 1 < n and t[i + 1] == '/') or c == '#':
            while i < n and t[i] != '\n':
                i += 1
            continue
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start is not None:
                objs.append(t[start:i + 1]); start = None
        i += 1
    return objs


def _nohash(s):
    return '\n'.join(l for l in s.splitlines() if not l.lstrip().startswith('#'))


def _parse_list(text, key):
    txt = _find_array(text, key) or ''
    good = []; bad = 0
    for o in _split_objects(txt):
        obj = None
        if json5 is not None:
            try:
                obj = json5.loads(_nohash(o))
            except Exception:
                obj = None
        if obj is None:
            try:
                obj = json.loads(_nohash(o))
            except Exception:
                bad += 1
                continue
        if isinstance(obj, dict):
            good.append(obj)
    return good, bad


def _top_scalar(text, key):
    """抽取顶层标量 "key":"value" 或 "key": value；返回最后一个非注释行的值（生效值）。"""
    vals = re.findall(r'"%s"\s*:\s*"([^"]*)"' % re.escape(key), text)
    return vals[-1] if vals else None


def parse_config(text):
    """返回 dict: {spider, wallpaper, logo, warningText, sites, lives, _bad}。"""
    sites, bad_s = _parse_list(text, 'sites')
    lives, bad_l = _parse_list(text, 'lives')
    return {
        'spider': _top_scalar(text, 'spider'),
        'wallpaper': _top_scalar(text, 'wallpaper'),
        'logo': _top_scalar(text, 'logo'),
        'warningText': _top_scalar(text, 'warningText'),
        'sites': sites,
        'lives': lives,
        '_bad': {'sites': bad_s, 'lives': bad_l},
    }


def extract_hosts(obj):
    hosts = set()
    def host(u):
        m = re.match(r'https?://([^/]+)', u)
        return m.group(1) if m else None
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            for u in re.findall(r'https?://[^\s"\'\\,}\[\]]+', o):
                h = host(u)
                if h:
                    hosts.add(h)
    walk(obj)
    return sorted(hosts)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]
    text = open(src, encoding='utf-8').read()
    d = parse_config(text)
    print(f"sites: {len(d['sites'])} (坏 {d['_bad']['sites']}) | lives: {len(d['lives'])} (坏 {d['_bad']['lives']})")
    print(f"spider: {d['spider']}")
    if '-o' in sys.argv:
        out = sys.argv[sys.argv.index('-o') + 1]
        json.dump({k: v for k, v in d.items() if k != '_bad'}, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
        print(f"已写 {out}")
    if '--hosts' in sys.argv:
        hp = sys.argv[sys.argv.index('--hosts') + 1]
        json.dump(extract_hosts(d), open(hp, 'w'), ensure_ascii=False, indent=2)
        print(f"已写 {hp} ({len(extract_hosts(d))} hosts)")
