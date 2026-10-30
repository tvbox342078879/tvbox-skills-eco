#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
py_runner/worker.py —— 单个 py spider 的沙箱真跑 worker（S116）。

由 run_all.py 以子进程调用（隔离崩溃/挂起/端口占用）：
  python3 worker.py <py文件绝对路径> <ext JSON字符串> <结果输出路径>

流程：装 stub → exec 源码 → 实例化 Spider → init(extend) →
      homeVideoContent() / categoryContent(首个分类) / searchContent 三路探口 →
      任一返回非空片单 = 内容层真通过。

三态（M-R60）：
  通过   —— 真拉到非空片单（含 vod_id/vod_name 的条目）
  失败   —— 代码跑通但片单全空 / 代码级异常（站点改版致解析崩）
  无法判定 —— 网络层异常（连接/超时/SSL）或 worker 超时
"""
import importlib.util
import json
import sys

# ── C7（T9）exec 白名单：AST 静态审查，exec 前拦高危能力 ─────────────
# 目标：worker 只服务 py 源测活（真拉片单），源码不需要这些能力：
#   禁模块：os/socket/subprocess/sys/shutil/ctypes/signal/resource/importlib
#           （requests/json/re/time/urllib/xml/lxml 等正常源常用库全部放行）
#   禁调用：__import__ / eval / exec / compile / open / input
# 诚实边界：静态 AST 审计是纵深防御的一层，不是完整沙箱——getattr 链、
#   内建属性逃逸等动态手法无法全防；隔离主要靠独立子进程 + run_all 超时。
_BANNED_MODULES = {'os', 'socket', 'subprocess', 'sys', 'shutil', 'ctypes',
                   'signal', 'resource', 'importlib', 'multiprocessing'}
_BANNED_CALLS = {'__import__', 'eval', 'exec', 'compile', 'open', 'input'}


def _audit_src(src):
    """审 py 源码，命中白名单禁项返回拦截原因字符串，放行返回 None。"""
    import ast
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return None  # 语法问题交由后续 compile 自然暴露，不算安全拦截
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                if al.name.split('.')[0] in _BANNED_MODULES:
                    return f'import {al.name}'
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '').split('.')[0] in _BANNED_MODULES:
                return f'from {node.module} import ...'
        elif isinstance(node, ast.Call):
            f = node.func
            name = (f.id if isinstance(f, ast.Name)
                    else f.attr if isinstance(f, ast.Attribute) else None)
            if name in _BANNED_CALLS:
                return f'调用 {name}(...)'
    return None


def _set_rlimits():
    """C7 资源上限：CPU 时间封顶（网络等待不算 CPU，不影响真拉片单）。"""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (60, 65))
    except Exception:
        pass  # 非 POSIX / 限额失败不阻断测活主流程


def install_stubs():
    """把 stub base.spider / com.github.catvod 注册进 sys.modules（相对本文件定位）。"""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    stub_path = os.path.join(here, 'base_stub.py')
    spec = importlib.util.spec_from_file_location('base.spider', stub_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['base.spider'] = mod
    spec.loader.exec_module(mod)
    base_pkg = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location('base', stub_path))
    base_pkg.spider = mod
    sys.modules['base'] = base_pkg
    # com.github.catvod 惰性导入顶替（真机里来自 jar）
    for name in ('com', 'com.github', 'com.github.catvod'):
        m = type(sys)('x')
        m.__path__ = []
        sys.modules[name] = m
    cg = sys.modules['com.github.catvod']

    class _Proxy:
        @staticmethod
        def getUrl(*a, **k):
            return (a[0] if a else '')

        @staticmethod
        def Proxy(*a, **k):
            return ''
    cg.Proxy = _Proxy
    return mod


def _list_of(result):
    if not isinstance(result, dict):
        return []
    return result.get('list') or []


def _valid(items):
    """非空且至少一条像样条目（有 id 或 name）。"""
    if not items:
        return 0
    n = 0
    for it in items:
        if isinstance(it, dict) and (it.get('vod_id') or it.get('vod_name')
                                     or it.get('id') or it.get('name')):
            n += 1
    return n


def _read_src(py_file):
    """读源码：剥 BOM（utf-8-sig），GBK 兜底（部分老站源码非 UTF-8）。

    S116 修复：LIBVIO 源码带 U+FEFF，直接 compile 炸 SyntaxError
    「invalid non-printable character」，被误记成代码级失败。
    """
    raw = open(py_file, 'rb').read()
    for enc in ('utf-8-sig', 'gbk', 'utf-16'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode('utf-8', 'replace')


def main():
    py_file, ext_json, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    result = {'verdict': '无法判定', 'evidence': '', 'route': '', 'count': 0}
    try:
        stub = install_stubs()
    except Exception as e:
        result.update(verdict='无法判定', evidence=f'stub 安装失败: {type(e).__name__}')
        json.dump(result, open(out_path, 'w'))
        return
    try:
        _set_rlimits()
        src = _read_src(py_file)
        # C7（T9）：exec 前静态审计——命中禁项即拒绝执行，明确记因（不静默）
        _ban = _audit_src(src)
        if _ban:
            result.update(verdict='失败',
                          evidence=f'安全白名单拦截（T9）：{_ban}')
            json.dump(result, open(out_path, 'w'))
            return
        ns = {'__name__': 'spider_under_test', '__file__': py_file}
        exec(compile(src, py_file, 'exec'), ns)

        cls = ns.get('Spider')
        if cls is None:
            # 退化：找任何继承自 stub Spider 的类
            for v in ns.values():
                if isinstance(v, type) and issubclass(v, stub.Spider) and v is not stub.Spider:
                    cls = v
                    break
        if cls is None:
            result.update(verdict='失败', evidence='源码中未找到 Spider 类')
            json.dump(result, open(out_path, 'w'))
            return

        s = cls()
        s.init(extend=ext_json or '')

        # ① 首页推荐
        try:
            hv = s.homeVideoContent()
            n = _valid(_list_of(hv))
            if n:
                result.update(verdict='通过', route='homeVideoContent',
                              count=n, evidence=f'首页片单 {n} 条')
                json.dump(result, open(out_path, 'w'))
                return
        except Exception as e:
            if _is_net(e):
                result.update(evidence=f'homeVideoContent 网络层: {_e(e)}')
            else:
                result.update(evidence=f'homeVideoContent 异常: {_e(e)}')

        # ② 分类页（取 homeContent 首个分类）
        try:
            hc = s.homeContent(True) or {}
            classes = hc.get('class') or []
            tids = [c.get('type_id') for c in classes if isinstance(c, dict) and c.get('type_id')]
        except Exception as e:
            tids = []
            result['evidence'] += f' | homeContent 异常: {_e(e)}'
        for tid in tids[:3]:
            try:
                cc = s.categoryContent(tid, 1, False, '')
                n = _valid(_list_of(cc))
                if n:
                    result.update(verdict='通过', route=f'categoryContent({tid})',
                                  count=n, evidence=f'分类 {tid} 片单 {n} 条')
                    json.dump(result, open(out_path, 'w'))
                    return
            except Exception as e:
                if not _is_net(e):
                    result.update(verdict='失败', evidence=f'categoryContent({tid}) 代码异常: {_e(e)}')
                    json.dump(result, open(out_path, 'w'))
                    return
                result['evidence'] += f' | categoryContent({tid}) 网络层: {_e(e)}'
                break

        # ③ 搜索兜底
        try:
            sc = s.searchContent('电视剧', False)
            n = _valid(_list_of(sc))
            if n:
                result.update(verdict='通过', route='searchContent',
                              count=n, evidence=f'搜索结果 {n} 条')
                json.dump(result, open(out_path, 'w'))
                return
        except Exception as e:
            if not _is_net(e):
                result.update(verdict='失败', evidence=f'searchContent 代码异常: {_e(e)}')
                json.dump(result, open(out_path, 'w'))
                return
            result['evidence'] += f' | searchContent 网络层: {_e(e)}'

        # 三路全空
        if not result['evidence']:
            # S116 修复：代码全程跑通、无任何异常，但三路片单全空——
            # 这不是"无法判定"（没有任何网络层歧义），是明确的失败：
            # 站点改版/空源，app 里同样看不到数据。旧代码在此分支漏改
            # verdict，停在初始值"无法判定"，把 5 个改版站误送进待端测。
            result.update(verdict='失败', evidence='代码跑通但三路片单全空（站点改版/空源）')
        elif '网络层' in result['evidence']:
            result.update(verdict='无法判定', evidence=result['evidence'] + '（网络层，非死）')
        else:
            result.update(verdict='失败', evidence=result['evidence'])
    except Exception as e:
        if _is_net(e):
            result.update(verdict='无法判定', evidence=f'网络层: {_e(e)}（非死）')
        else:
            result.update(verdict='失败', evidence=f'顶层异常: {_e(e)}')
    json.dump(result, open(out_path, 'w'))


def _e(exc):
    return f'{type(exc).__name__}: {str(exc)[:120]}'


def _is_net(exc):
    """仅「连接层」异常 → 无法判定（M-R60，沙箱网络 ≠ 用户端网络）。

    S116 修复（误判实证）：旧版把 OSError 整类当网络层，而
    requests 2.x 的 RequestException 继承 IOError（=OSError）——导致
    JSONDecodeError（上游返 HTML，站点改版）/ InvalidSchema / MissingSchema
    这些「代码/改版级」异常全被吞成无法判定。现收窄为：
      - requests.ConnectionError（含 SSLError/ConnectTimeout/ProxyError）
      - requests.Timeout（含 ReadTimeout）
      - 内置 ConnectionError / TimeoutError / socket.gaierror(DNS)
      - urllib3 连接族（裸用 urllib3 的 spider 会漏出来）
    其余（JSONDecodeError/InvalidSchema/AttributeError/ValueError…）→ 失败。
    """
    import socket
    net = [ConnectionError, TimeoutError, socket.gaierror, socket.timeout]
    try:
        import requests
        net += [requests.ConnectionError, requests.Timeout]
    except ImportError:
        pass
    try:
        import urllib3.exceptions as ue
        net += [ue.NewConnectionError, ue.ConnectTimeoutError,
                ue.ReadTimeoutError, ue.ProtocolError]
    except ImportError:
        pass
    return isinstance(exc, tuple(net))


if __name__ == '__main__':
    main()
