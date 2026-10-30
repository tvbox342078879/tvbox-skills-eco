#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mount_judge.py — 挂载决定论判定器（探源司升级·S113 / S113b）

正本依据：
  《做json决策依据_挂载决定论与相对转绝对.md》（原 /workspace/ 位置已归档：
    /root/uploads/历史产物归档_20261023_1014至1020会话.tar.gz 内 依据_1018_改名）
  tvbox-interface/docs/manual_4_2c_相对地址转绝对.md
  《标准1015_底包细分类体系.md》§2 可平移性矩阵

回答的问题：**作者原仓库的线路「能不能用、能用哪些」**——
  ① 相对地址(./py/*.py, ./js/*.js)：逐条 base 活体探测 → 200 则转绝对保留（§4.2c）
  ② csp_类名（S113b 完整判定）：
       ext 相对(./js/s/xxx.json) → base 探测 200 → 转绝对（配置源活着）
       spider jar（site 级或包级 ./jar/xs.jar）→ 下载 → 解 dex → 列类名
         → 含 csp_ 后缀同名类（com.github.catvod.spider.XBPQ）= 作者挂了这个类 → 可用
         → 不含 = 作者没挂这个类 → 剔除
         → 下载失败/无 spider 地址 = 无法判定 ≠ 死（M-R60）
  ③ 绝对 http：标记「待测活」（与作者挂载无关，看第三方存活）

dex 解析口径（纯 stdlib，无外部工具依赖）：
  - dex 头：string_ids_size@56 / string_ids_off@60 / type_ids_size@64 / type_ids_off@68
  - string_data：uleb128(utf16长度) + MUTF-8 字节 + \0（按 \0 截断，兼容非 ASCII）
  - type_ids → string_ids 索引 → 描述符 Lcom/xxx/Yyy; → 类名 com.xxx.Yyy
  - jar/apk = zip，取全部 *.dex 条目（含 classes2.dex 等多 dex）类名并集

设计纪律（规则 49/52）：
  - 纯函数、可 import，不依赖 probe.py 主程序（避免耦合 30 万字节文件）
  - M-R60 三态：通过 / 失败 / **无法判定≠死**（jar 下载失败即"无法判定"，不硬杀）
  - §4.2c 铁律：base 未验证/不通 → **不硬编**，如实标记

兼容探源司格式({api接口,名称,...})与标准 TVBox 格式({api,name,ext})。
"""
import io
import struct
import subprocess
import threading
import zipfile
from urllib.parse import quote

# ---------- M-R114 统一并发入口（S110） ----------
# 下载 jar 解 dex，打公网
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded as _run_bounded, TIER_WEB  # noqa: E402
from core import http_get  # noqa: E402  # 共享核心(C1)：统一取数入口


CURL = '/usr/bin/curl'
UA = 'okhttp/3.12.0'
ALIVE_CODES = ('200', '206')


# ── 基础探测 ──────────────────────────────────────────────────────────────

def _http_code(url, timeout=8):
    """探测 URL 可达性，返回 HTTP 码字符串（'0'=失败）。非 ASCII 自动百分号编码。"""
    try:
        probe = quote(url, safe=':/?&=%#')
        r = subprocess.run(
            [CURL, '-s', '-o', '/dev/null', '-w', '%{http_code}',
             '-m', str(timeout), '-A', UA, '-r', '0-300', probe],
            capture_output=True, text=True, timeout=timeout + 3)
        return (r.stdout or '').strip() or '0'
    except Exception:
        return '0'


def _http_alive(url, timeout=8):
    """活体判定（S113b 加固）：状态码 200/206 **且响应体非 HTML 兜底页**。

    防 SPA catch-all 假活：clun.top 类 GitHub Pages 站对不存在路径也返 200+主页
    HTML，单看状态码会把「作者没挂的资源」误判成挂了。取前 512 字节验内容。
    返回 (code, alive_bool)。
    """
    marker = '\n__HTTP_CODE__'
    try:
        probe = quote(url, safe=':/?&=%#')
        r = subprocess.run(
            [CURL, '-s', '-L', '-m', str(timeout), '-A', UA, '-r', '0-512',
             '-w', marker + '%{http_code}', probe],
            capture_output=True, timeout=timeout + 3)
        out = (r.stdout or b'').decode('utf-8', 'replace')
        i = out.rfind(marker)
        code = out[i + len(marker):].strip() if i >= 0 else '0'
        body = out[:i] if i >= 0 else ''
        if code not in ALIVE_CODES:
            return code, False
        head = body.lstrip()[:64].lower()
        if head.startswith('<!doctype') or head.startswith('<html'):
            return code, False   # SPA 兜底页 = 资源实际不存在
        return code, True
    except Exception:
        return '0', False


def api_kind(api):
    """形态分诊（与 probe.py `_lj_api_kind` 同口径，保持一致）。"""
    a = (api or '').strip()
    al = a.lower()
    if a.startswith('csp_'):
        return 'csp'
    if al.endswith('.py') or '/py/' in al or 'pyplugin' in al:
        return 'py'
    if al.endswith('.js') or 'drpy' in al or '/js/' in al:
        return 'js'
    if al.startswith(('http://', 'https://')):
        return 'http'
    if a.startswith(('./', '../', '/')):
        return 'local'
    return 'other'


def to_abs(api, base):
    """相对地址 → 绝对（§4.2c：base 已活着才调这个）。"""
    if not base:
        return api
    return base.rstrip('/') + '/' + (api or '').lstrip('./').lstrip('../').lstrip('/')


def _ext_is_relative(ext):
    """ext 是否为相对路径字符串（远程壳加载不到 = 死壳）。dict/list 内联 → False。"""
    if isinstance(ext, (dict, list)):
        return False
    if not isinstance(ext, str) or not ext.strip():
        return False
    e = ext.strip()
    if e.startswith(('http://', 'https://')):
        return False
    return e.startswith(('./', '../', '/'))


# ── S113b：dex/jar 类名解析（挂载决定论的 jar 判定层）─────────────────────

def _uleb128(data, off):
    """读 uleb128 变长整数，返回 (值, 下一偏移)。dex string_data 长度前缀。"""
    result = 0
    for i in range(5):
        if off + i >= len(data):
            return result, off + i
        b = data[off + i]
        result |= (b & 0x7f) << (i * 7)
        if not (b & 0x80):
            return result, off + i + 1
    return result, off + 5


def dex_class_names(dex_bytes):
    """解析 dex 二进制 → 类名集合（com.github.catvod.spider.XBPQ 形态，含 $ 内层类）。

    纯 stdlib 实现（无 dexdump/baksmali 依赖）。解析失败/非 dex → 空集合
    （空集合 = 无法判定，由调用方按 M-R60 处理，不在此处硬杀）。
    """
    try:
        if not dex_bytes or not dex_bytes.startswith(b'dex\n'):
            return set()
        # dex 头定长字段（全部小端 uint32）
        sids_size, sids_off, tids_size, tids_off = struct.unpack_from('<IIII', dex_bytes, 56)
        if sids_off + sids_size * 4 > len(dex_bytes) or tids_off + tids_size * 4 > len(dex_bytes):
            return set()
        # ① string_ids → 字符串表（按 \0 截断，兼容 MUTF-8 非 ASCII）
        strs = []
        for i in range(sids_size):
            (so,) = struct.unpack_from('<I', dex_bytes, sids_off + i * 4)
            _, p = _uleb128(dex_bytes, so)
            end = dex_bytes.find(b'\x00', p)
            if end < 0:
                end = len(dex_bytes)
            strs.append(dex_bytes[p:end].decode('utf-8', 'replace'))
        # ② type_ids → 描述符表 → 类名
        names = set()
        for i in range(tids_size):
            (idx,) = struct.unpack_from('<I', dex_bytes, tids_off + i * 4)
            if 0 <= idx < len(strs):
                d = strs[idx]
                if d.startswith('L') and d.endswith(';') and len(d) > 2:
                    names.add(d[1:-1].replace('/', '.'))
        return names
    except Exception:
        return set()


def jar_class_names(jar_bytes):
    """jar/zip/apk → 全部 *.dex 条目类名并集；裸 dex 也支持；失败空集合。

    多 dex（classes.dex + classes2.dex…）取并集；zip 内嵌套 jar 不递归
    （TVBox spider jar 的类都在顶层 dex，嵌套场景未见于生态）。
    """
    names = set()
    if not jar_bytes:
        return names
    try:
        if jar_bytes[:2] == b'PK':
            zf = zipfile.ZipFile(io.BytesIO(jar_bytes))
            for n in zf.namelist():
                if n.endswith('.dex'):
                    names |= dex_class_names(zf.read(n))
            return names
        if jar_bytes.startswith(b'dex\n'):
            return dex_class_names(jar_bytes)
    except Exception:
        return set()
    return names


_JAR_CACHE = {}
_JAR_LOCK = threading.Lock()


def fetch_jar(url, timeout=30):
    """下载 spider jar（进程内缓存，同 URL 只下一次）。返回 bytes 或 None。

    自动剥离 TVBox 的 `;md5;` 后缀（pipeline.py cmd_spider 同口径）。
    底层统一走 core.http_get（C1：收敛取数入口，as_text=False 保二进制）。
    """
    if not url:
        return None
    url = url.split(';md5;')[0].strip()
    if not url:
        return None
    with _JAR_LOCK:
        if url in _JAR_CACHE:
            return _JAR_CACHE[url]
    st, payload, _ = http_get(url, timeout=timeout, ua=UA, as_text=False)
    data = payload if (st == 'ok' and payload) else b''
    # HTML 兜底页 ≠ jar（SPA catch-all 会把不存在路径也返 200+主页）
    out = None if (not data or data.lstrip()[:1] == b'<') else data
    with _JAR_LOCK:
        _JAR_CACHE[url] = out
    return out


def _simple_name(csp_key):
    """csp_XBPQ → XBPQ（判「同名类」用的简单类名，标准1015§2 口径）。"""
    return csp_key[4:] if csp_key.startswith('csp_') else csp_key


def spider_url_of(site, pkg_spider=None, base=None):
    """解析站点生效的 spider jar 地址：site 级优先，否则包级；相对→拼 base。"""
    sp = (site.get('spider') or '').strip() or (pkg_spider or '').strip()
    if not sp:
        return None
    if sp.startswith(('http://', 'https://')):
        return sp.split(';md5;')[0].strip()
    if sp.startswith(('./', '../', '/')):
        return to_abs(sp, base) if base else sp
    return sp


# ── 单条判定 ──────────────────────────────────────────────────────────────

def judge_site(site, base=None, spider=None):
    """单条判定（挂载决定论）。

    spider：包级 spider 地址（整包 dict 的 spider 字段，可为相对路径）。
    返回 dict：{api, name, kind, verdict, new_api, new_ext, reason, code, jar_classes}

    verdict 取值：
      keep_http      绝对 http → 待测活保留
      converted      本地相对地址(py/js/local)，base 探测 200 → 转绝对保留
      csp_ok         csp 且 jar 含同名类 且 ext 状态允许 → 保留（作者挂了且类在）
      csp_jar_unknown csp 但 jar 下载失败/无 spider 地址，或 ext 相对无 base
                     → 无法判定 ≠ 死（M-R60）
      csp_dead       csp 且 ext 相对转绝对后探测失败，或 jar 明确无同名类 → 剔除
      drop_relative  本地相对地址，base 未提供/探测非 200 → 剔除（§4.2c 不硬编）
      drop_other     形态未知
    """
    api = str(site.get('api') or site.get('api接口') or '').strip()
    name = str(site.get('name') or site.get('名称') or '').strip()
    ext = site.get('ext')
    k = api_kind(api)
    d = {'api': api, 'name': name, 'kind': k, 'verdict': '', 'new_api': api,
         'new_ext': None, 'reason': '', 'code': '', 'jar_classes': 0}

    if k == 'http':
        d['verdict'] = 'keep_http'
        d['reason'] = '绝对 http，待测活（与作者挂载无关，看第三方存活）'
    elif k == 'csp':
        _judge_csp(d, site, api, name, ext, base, spider)
    elif k in ('py', 'js', 'local'):
        if api.startswith(('http://', 'https://')):
            d['verdict'] = 'keep_http'
            d['reason'] = f'远程 {k}，待测活'
        elif base:
            abs_url = to_abs(api, base)
            code, alive = _http_alive(abs_url)
            d['code'] = code
            if alive:
                d['verdict'] = 'converted'
                d['new_api'] = abs_url
                d['reason'] = f'相对→绝对，base 活体探测 {code}（作者挂了该资源）'
            elif code in ALIVE_CODES:
                d['verdict'] = 'drop_relative'
                d['reason'] = (f'base 探测 {code} 但响应为 HTML 兜底页'
                               f'（SPA catch-all，资源实际不存在），不采用')
            else:
                d['verdict'] = 'drop_relative'
                d['reason'] = f'base 探测 {code}（作者未挂/不通），§4.2c 不硬编'
        else:
            d['verdict'] = 'drop_relative'
            d['reason'] = '本地相对地址且未提供 base（§4.2c 需先活体验证），不硬编'
    else:
        d['verdict'] = 'drop_other'
        d['reason'] = f'形态未知({api[:30]})'
    return d


def _judge_csp(d, site, api, name, ext, base, spider):
    """csp 挂载判定（S113b）：ext 配置源 + spider jar 同名类，两层都过才可用。"""
    # ① ext 判定（§4.2c 统一口径：相对→base 探测→转绝对；内联/绝对原样；缺失=部分 csp 不需要）
    ext_state, new_ext = 'none', None
    if 'ext' in site and ext not in (None, ''):
        if _ext_is_relative(ext):
            if base:
                abs_ext = to_abs(ext, base)
                code, alive = _http_alive(abs_ext)
                d['code'] = code
                if alive:
                    ext_state, new_ext = 'converted', abs_ext
                else:
                    d['verdict'] = 'csp_dead'
                    d['reason'] = (f'csp 配置源死：ext 相对({str(ext)[:40]}) 转绝对后探测 '
                                   f'{code}{"（HTML兜底页，资源不存在）" if code in ALIVE_CODES else ""}'
                                   f'（作者没挂这个配置）')
                    return
            else:
                d['verdict'] = 'csp_jar_unknown'
                d['reason'] = (f'csp ext 相对({str(ext)[:40]}) 但未提供 base，'
                               f'无法转绝对验证（无法判定≠死）')
                return
        else:
            ext_state = 'inline'   # dict 内联配置 或 http URL
    # ② jar 判定（挂载决定论核心：作者挂的 jar 里有没有这个类）
    sp_url = spider_url_of(site, spider, base)
    if not sp_url:
        d['verdict'] = 'csp_jar_unknown'
        d['reason'] = 'csp 无 spider 地址（site 级/包级都没有），jar 挂载无法判定≠死'
        return
    jar = fetch_jar(sp_url)
    if jar is None:
        d['verdict'] = 'csp_jar_unknown'
        d['reason'] = f'csp spider jar 下载失败（{sp_url[:60]}），无法判定≠死'
        return
    classes = jar_class_names(jar)
    d['jar_classes'] = len(classes)
    if not classes:
        d['verdict'] = 'csp_jar_unknown'
        d['reason'] = (f'csp spider jar 解析不出类名（{sp_url[:50]}，可能加壳/非标准 dex），'
                       f'无法判定≠死')
        return
    simple = _simple_name(api)
    hit = any(c.rsplit('.', 1)[-1].split('$', 1)[0] == simple for c in classes)
    if not hit:
        d['verdict'] = 'csp_dead'
        d['reason'] = (f'csp 死壳：jar（{sp_url[:50]}，{len(classes)} 类）不含同名类 '
                       f'{simple}，作者没挂这个')
        return
    # ③ 两层都过 → 可用
    d['verdict'] = 'csp_ok'
    if new_ext:
        d['new_ext'] = new_ext
    d['reason'] = (f'jar 含同名类 {simple}（{len(classes)} 类）+ ext {ext_state}'
                   f'{"→已转绝对" if new_ext else ""}')


# ── 整批判定 ──────────────────────────────────────────────────────────────

def classify_mount(sites, base=None, spider=None, workers=12):
    """整批判定（挂载决定论）。

    返回 {keep, converted, csp, drop, stats}
      keep       绝对 http（待测活）
      converted  本地相对 → 已转绝对保留（base 探测 200）
      csp        csp 可用（jar 同名类 + ext 通过）
      drop       剔除（死壳 / base 不通 / jar 无同类 / 未知）
      stats      csp_ok / csp_pending（无法判定）/ csp_dead 分列
    """
    out = {'keep': [], 'converted': [], 'csp': [], 'drop': [], 'stats': {}}

    def _one(s):
        return judge_site(s, base, spider)

    # ★ S163：改走统一入口。keep_order 必须开——下面按 results 顺序回填 out 各分类
    rep = _run_bounded(sites, _one, tier=TIER_WEB, label='mount-judge',
                       keep_order=True, on_error='record')
    results = [(_r if _r is not None else {'verdict': '无法判定',
               'why': '判定线程异常（并发报告已记录）'}) for _r in rep.results]

    for d in results:
        v = d['verdict']
        if v == 'keep_http':
            out['keep'].append(d)
        elif v == 'converted':
            out['converted'].append(d)
        elif v == 'csp_ok':
            out['csp'].append(d)
        else:
            out['drop'].append(d)

    st = out['stats']
    st['total'] = len(sites)
    st['keep_http'] = len(out['keep'])
    st['converted'] = len(out['converted'])
    st['csp_ok'] = len(out['csp'])
    st['csp_pending'] = sum(1 for d in out['drop'] if d['verdict'] == 'csp_jar_unknown')
    st['csp_dead'] = sum(1 for d in out['drop'] if d['verdict'] == 'csp_dead')
    st['dropped'] = len(out['drop'])
    st['usable'] = st['keep_http'] + st['converted'] + st['csp_ok']
    return out


if __name__ == '__main__':
    import json
    import sys
    # 独立跑：python3 mount_judge.py <sites文件.json> [base] [spider]
    if len(sys.argv) < 2:
        print('用法: python3 mount_judge.py <sites文件.json|探源司list.json> [base] [spider]')
        print('  base   例: https://clun.top （相对地址转绝对的候选主机）')
        print('  spider 例: ./jar/xs.jar 或 https://.../xs.jar （包级 spider，csp 判jar用）')
        sys.exit(1)
    raw = json.load(open(sys.argv[1], encoding='utf-8'))
    sites = raw if isinstance(raw, list) else raw.get('sites', [])
    base = sys.argv[2] if len(sys.argv) > 2 else None
    spider = sys.argv[3] if len(sys.argv) > 3 else None
    r = classify_mount(sites, base=base, spider=spider)
    st = r['stats']
    print(f"═══ 挂载决定论判定 ═══ 总 {st['total']}")
    print(f"  ✅ 可用 {st['usable']}（绝对http {st['keep_http']} + 相对转绝对 {st['converted']}"
          f" + csp {st['csp_ok']}）")
    print(f"  ⚠️ csp 无法判定 {st['csp_pending']}（jar下载失败/无spider，无法判定≠死）")
    print(f"  💀 csp 死壳 {st['csp_dead']}（jar无同名类/ext配置源不通）")
    print(f"  ❌ 其余剔除 {st['dropped'] - st['csp_pending'] - st['csp_dead']}")
    print("—— 转绝对明细（前5）——")
    for d in r['converted'][:5]:
        print(f"  [{d['code']}] {d['name']}: {d['api']} → {d['new_api']}")
    print("—— csp 判定明细 ——")
    for d in r['csp']:
        print(f"  ✅ {d['name']}: {d['api']} ｜ {d['reason']}")
        if d.get('new_ext'):
            print(f"      ext → {d['new_ext']}")
    print("—— 剔除明细（前8）——")
    for d in r['drop'][:8]:
        print(f"  {d['name']}: {d['reason']}")
