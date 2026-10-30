#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe-hub/scripts/livecheck.py —— 真实可用测活引擎（S097）

病根（用户 2026-10-16 批评）：原测活仅判 HTTP 码（ALIVE_CODES 200/206/3xx），
"HTTP 可达 ≠ 能真正播放"——用户拿来放进 json 后一直转圈。

本引擎把"合格"定义为**真实可用**：
  · 点播源（站点 API / 苹果CMS csp）：真请求 `?ac=detail`（或 ac=videolist），
    校验返回 JSON 含 `vod_play_url`（有播放地址），才算通过。
  · 直播源（m3u8）：真拉 m3u8 清单 → 真拉其中一个分片，拿到 >0 字节流，才算通过。
  · py/spider 源：真请求 api，返回体非空且非错误页，且能解析出下一层可达。

三态契约（M-R60）：无法判定 ≠ 死。
  '通过'      真实可用（拉到了播放地址/分片字节）
  '失败'      明确不可用（404/无分片/无vod_play_url/错误页）
  '无法判定'  网络抖动/超时/限流（**非死**，不当失败）

单一事实源（M-R38）：本模块是生态"真实可用"判据唯一实现，
  探源司 harvest、整包坊 probe、源枢库曹 ingest 一律复用，禁各自重写。

用法：
  python3 livecheck.py <url> [--kind vod|m3u8|auto] [--json]
  python3 livecheck.py --batch <file.json> [--json]     # 批量[{name,api/url,type}]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded, TIER_WEB  # noqa: E402  M-R114 统一入口

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
TIMEOUT = 12
# ★S169：最近一次 _curl 失败的类别（'' 表示成功）。供上层区分"证书坏了"与"站点死了"。
_last_err_kind = ''


def _curl(url, *, timeout=TIMEOUT, headers=None, want_body=True, rng=None):
    """底层 curl：返回 (code, body)。失败 code=0（调用方判'无法判定'，非死）。

    S112 修复：加 `--compressed`——此前不带该参数，服务器返回 gzip 压缩体时
    body 是二进制压缩流（`\\x1f\\x8b` 开头），`_looks_like_py` 检查明文特征必然失败，
    导致**真 py 源码被误判"非 Python 源码"**（实测 pan.szfx.top 6 条）。
    `--compressed` 让 curl 自动解压 gzip/deflate/br。

    ★S169：**失败原因要分开记**。原来任何失败都塌成 code=0，上层只能判"无法判定"，
    黑名单那边再按"失败2次+间隔1天"升级成 L1「确认死」——于是
    「证书过期」和「站点真死」走同一条路，证书一问题的源被判死。
    实测：ryzy.tv / lz.118318.xyz 证书 2026-09-17 过期，但接口返回的是
    87594 / 302199 条真实数据；加 -k 立刻能拉到。
    故这里把 stderr 的关键词解析成 `_last_err_kind`（模块级，供同线程的调用方读），
    上层可据此把证书类单独标出来，而不是混进"不可达"。
    """
    global _last_err_kind
    cmd = ['curl', '-sS', '-L', '--compressed', '--max-time', str(timeout), '-A', UA,
           '-w', '\n%{http_code}']
    if rng:
        cmd += ['-r', rng]
    if headers:
        for k, v in headers.items():
            cmd += ['-H', f'{k}: {v}']
    cmd.append(url)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 4)
        out = p.stdout.decode('utf-8', 'replace')
        err = (p.stderr or b'').decode('utf-8', 'replace').lower()
        if '\n' in out:
            body, _, tail = out.rpartition('\n')
        else:
            body, tail = '', out
        try:
            code = int(tail.strip())
        except Exception:
            code = 0
        # ★S193：把 curl 退出码一并交给分类器（主判据；err 仅兜底）
        _last_err_kind = '' if code else _classify_err(err, p.returncode)
        return code, (body if want_body else '')
    except Exception:
        _last_err_kind = 'other'
        return 0, ''


def _classify_err(err, rc=0):
    """curl 失败 → 类别。**只分类，不改判定**（判死与否仍由上层定）。

    ★S193：主判据从「stderr 关键词」改为「**curl 退出码优先**」。

      实测（本机 curl）：连不上时统一吐
        `curl: (7) Failed to connect to 10.255.255.1 port 80: Couldn't connect to server`
      ——**既没有 refused 也没有 timeout**，关键词判据把"端口关了"和"超时"
      两类全塌成 other，分类等于白做：看的人还是分不清该重试还是该判死。
      而 curl 退出码是 libcurl 的稳定错误码，比人话文案可靠：
        6  = 域名解析不了   7 = 连不上（端口关/被拒/不可路由）
        28 = 超时          35 = SSL/TLS 握手失败
        51/58 = 证书本身有问题    60 = 证书校验不过（过期通常是这个）
      退出码优先；stderr 关键词保留作**老版本 curl 的兜底**（措辞会随版本变，
      退出码不会），两者都没有时才落 other。
    """
    if rc == 6:
        return 'dns'
    if rc == 7:
        return 'refused'
    if rc == 28:
        return 'timeout'
    if rc in (51, 58):
        return 'cert_invalid'
    if rc == 60:
        return 'cert_expired' if ('expired' in err or 'has expired' in err) else 'cert_invalid'
    if rc == 35:
        return 'tls'
    # —— 以下为老版 curl 兜底 ——
    if 'certificate' in err and 'expired' in err:
        return 'cert_expired'
    if 'certificate' in err:
        return 'cert_invalid'
    if 'could not resolve' in err or 'name or service' in err:
        return 'dns'
    if 'refused' in err:
        return 'refused'
    if 'timed out' in err or 'timeout' in err:
        return 'timeout'
    if 'ssl' in err or 'tls' in err:
        return 'tls'
    return 'other'


CERT_KINDS = ('cert_expired', 'cert_invalid', 'tls')

# ★S193：失败成因的说明 + 处置提示，提到模块级供**所有** check_* 复用。
#
#   原来这份字典内联在 check_vod 里，m3u8 / 分片 / 分类接口三处拿不到，
#   于是它们一律硬写"超时/不可达"——把「DNS 没解析出 A 记录」「端口关了」
#   「证书过期」全说成超时，看的人分不清**该重试**还是**该判死**。
#   这正是 S169 的根因形态：失败原因塌成一个，上层只能按次数判死。
#   M-R38 单一事实源：同一份口径只写一处，其余函数引用，不各写各的。
ERR_HINT = {
    'cert_expired': '证书已过期（**续期即恢复，非站点失效**；建议跳过校验复验内容）',
    'cert_invalid': '证书校验失败（可能是自签/CDN 错配；建议跳过校验复验内容）',
    'tls':          'TLS 握手失败（同上，先排除证书再谈失效）',
    'dns':          'DNS 无 A 记录（域名真没了）',
    'timeout':      '连接超时（可能在墙/在挂，不一定是死）',
    # curl 退出码 7 = CURLE_COULDNT_CONNECT，涵盖"端口关/被拒/路由不通"三种，
    # 旧提示只写"端口关了"——实测不可路由 IP 也是 rc=7，会被误导成端口问题。
    'refused':      '连不上：端口关闭／被拒／路由不通（三者 curl 都是退出码 7）',
}

# 按成因给出**准确的动词**：dns 就是解析失败，不该统统说成"超时"
ERR_LEAD = {
    'dns':          '域名解析失败',
    'refused':      '连接被拒',
    'timeout':      '连接超时',
    'cert_expired': '证书过期',
    'cert_invalid': '证书校验失败',
    'tls':          'TLS 握手失败',
}


def err_tail():
    """最近一次连接失败的成因片段（无成因返回空串）。

    ★ 只在 code==0 时有值：HTTP 返回了响应码就不算连接失败，
      `_curl` 会把 `_last_err_kind` 清成 ''，这里自然返回空串——
      那种情况不该硬加成因，否则"HTTP 404"也会被标成"超时"，反而误导。
    """
    k = _last_err_kind
    if not k:
        return ''
    hint = ERR_HINT.get(k)
    return f'·【{k}】{hint}' if hint else f'·【{k}】'


def err_lead(default='不可达'):
    """按成因给出准确说法；认不出时用调用方自己的兜底措辞。"""
    return ERR_LEAD.get(_last_err_kind, default)


def check_auto_ignore_cert(api, *, timeout=TIMEOUT):
    """★S169：**仅用于「证书类失败」的复验**，不是日常测活入口。

    为什么需要它：证书过期会让 curl 在校验阶段就失败（code=0），
    真实内容根本没被看到。实测 ryzy.tv 证书 2026-09-17 过期，但接口
    返回的是实时数据、播放地址真拉得动（130KB）——这样的源被判死，
    是把「运维忘续证书」当成「站点没了」。

    用法边界（写死在实现里，防止被滥用）：
      · 只在 `check_*` 返回「无法判定」**且** `_last_err_kind in CERT_KINDS` 时调用；
      · 结果里强制带 `【跳过证书校验】` 前缀，看的人一眼知道这份证据的分量；
      · 结论**不会**因此变成 `通过` 进正常库——它只用于 `pardon` 之类的复验动作。
    """
    global _last_err_kind
    _saved = _curl
    _real_kinds = []

    def _k(url, **kw):
        # 在命令里插 -k：复用原实现，只改 argv
        import subprocess as _sp
        cmd = ['curl', '-k', '-sS', '-L', '--compressed', '--max-time', str(kw.get('timeout', timeout)),
               '-A', UA, '-w', '\n%{http_code}']
        if kw.get('rng'):
            cmd += ['-r', kw['rng']]
        for _k2, _v in (kw.get('headers') or {}).items():
            cmd += ['-H', f'{_k2}: {_v}']
        cmd.append(url)
        try:
            p = _sp.run(cmd, capture_output=True, timeout=kw.get('timeout', timeout) + 4)
            out = p.stdout.decode('utf-8', 'replace')
            body, _, tail = out.rpartition('\n') if '\n' in out else ('', '', out)
            try:
                code = int(tail.strip())
            except Exception:
                code = 0
            return code, (body if kw.get('want_body', True) else '')
        except Exception:
            return 0, ''

    try:
        globals()['_curl'] = _k
        v, ev, kind = check_auto(api, timeout=timeout)
    finally:
        globals()['_curl'] = _saved
    if v == '通过':
        return v, f'【跳过证书校验】{ev}', kind
    return v, f'【跳过证书校验·仍未通过】{ev}', kind


def _is_m3u8(url, body=''):
    u = (url or '').lower()
    return '.m3u8' in u or '#EXTM3U' in (body or '')


# ═══════════ 源形态判类（S144·P1「源类型」单一事实源） ═══════════
# 为什么单独抽一个函数：`源类型` 这一列此前是「测活时顺手填」的副产品，
# 于是成品库 189 条里 170 条被标成「采集」、12 条 js 被标成「点播」、m3u8 恒 0 ——
# 因为判类逻辑散落在 check_auto 内联的 if-elif 里，谁也复述不出来。
# 现在**判类只看 api 字符串形态**，不看响应体（不看响应体才可离线批量回填）。
SOURCE_KINDS = ('采集', '直链', 'js', 'py', 'php', 'csp', '其它')


def classify_source(api):
    """按 api 形态判「源类型」，返回 SOURCE_KINDS 之一。

    判据顺序（先特殊后一般，互斥）：
      · csp_ / jar 类名            → csp      （加壳类，不是 URL）
      · .m3u8 / .m3u / /live      → 直链(m3u8) → 归「直链」
      · .js / jsm / drpy / hipy / .json  → js   （js 源或 js 配置）
      · .py / spider / /py/       → py
      · .php / /provide/vod / api.php → 采集      （苹果CMS 采集接口）
      · 其它 http(s) 直链          → 直链
      · 空/非法                    → 其它
    """
    a = (api or '').strip()
    if not a:
        return '其它'
    low = a.lower()
    # ① 加壳类名（csp_Config / csp_PanConfigGuard / xxx.jar）
    if low.startswith('csp_') or low.endswith('.jar') or low.startswith('jar_'):
        return 'csp'
    if not low.startswith(('http://', 'https://')):
        return '其它'
    path = low.split('?')[0]
    # ② 直播清单
    if '.m3u8' in path or path.endswith('.m3u') or '/live' in path:
        return '直链'
    # ③ js 源（jsm/drpy/hipy/json 配置都走 js 通道）
    if path.endswith(('.js', '.json')) or 'jsm' in low or 'drpy' in low or 'hipy' in low:
        return 'js'
    # ④ py 源
    if path.endswith('.py') or 'spider' in low or '/py/' in path:
        return 'py'
    # ⑤ 苹果CMS 采集接口（php 后缀或 provide/vod 形态）
    if path.endswith('.php') or '/provide/vod' in path or 'ac=videolist' in low:
        return '采集'
    # ⑥ 其余 http 直链
    return '直链'


def can_enter_pack(kind, verdict='通过'):
    """「可进包」判据：这一条能不能直接放进用户的 json 包。

    用户的包只有两类吃得下：采集接口（sites）与直链/直播（lives）。
    js / py / csp 是**类包依赖**，须配类包才能跑——不是「不好」，
    而是「不能单独进包」，混进去就是运行时报错。
    """
    if verdict != '通过':
        return '否'
    if kind in ('采集', '直链'):
        return '是'
    if kind in ('js', 'py', 'csp'):
        return '需类包'
    return '待判'


def _looks_like_error_page(body):
    """错误页判定：HTML 且含常见错误词，或极短正文。"""
    b = (body or '').strip()
    if not b:
        return True
    if b[:1] in '<{':  # HTML/JSON 开头不一定错，需看内容
        low = b[:2000].lower()
        for w in ('404', 'not found', '页面不存在', '非法请求', 'error', 'denied',
                  'forbidden', 'bad request', 'no such'):
            if w in low and 'vod' not in low and 'play' not in low:
                return True
    return False


def _has_playable(val):
    """判 vod_play_url 取值是否含真实播放地址（非空串、含 http，或含 m3u8 分片串）。

    合格样例：第01集$https://xx.com/a.m3u8#第02集$https://...
    不合格：'' / ' ' / 纯标题无 http / 仅 '#'

    S099：JSON 响应里 URL 的斜杠常被转义为 `https:\\/\\/`，故须先归一化转义再判，
    否则活跃源会被误判"假活"（实测 cj.lziapi.com 栽在此处）。
    """
    if not val:
        return False
    v = val.strip().replace('\\/', '/')   # 还原 JSON 转义的斜杠
    if len(v) < 12:
        return False
    return ('http://' in v) or ('https://' in v) or ('.m3u8' in v)


def _xml_play_urls(body):
    """从苹果CMS **XML 版**响应里取播放地址（S142）。

    病根（2026-10-17 溯源查出·重大）：`check_vod` 原本只 `re.search('vod_play_url')`，
    而 XML 版的地址在 `<dl><dd flag="ckplayer"><![CDATA[https://…m3u8]]></dd></dl>` 里，
    **全文根本没有 vod_play_url 这个词** → 所有 type=0 的 XML 直链被一律判"失败"。
    而 type=0 恰是 D 包（DmaxPro908 纯直链底座）唯一能吃的形态 → 原料被自家闸门拦死。
    实测三条活源（xrbsp/gdlsp/pgxdy）全被误杀，真拉分片却有 13~30 万字节。

    返回播放地址列表（已去重、仅保留像 URL 的）。非 XML / 无地址 → []。
    """
    if not body or '<video' not in body:
        return []
    if not re.search(r'<rss\b|<list\b', body[:600]):
        return []
    out, seen = [], set()

    def _absorb(txt):
        """从一段文本里吸出播放地址（S146 修）。

        病根：旧实现要求整段 `startswith('http')`，但苹果CMS XML 的 `<dd>` 里
        标准写法是 **`正片$https://…m3u8`**（集名$地址），整段以「正片」开头 →
        一条都吸不出来 → 所有 XML 形态的源被判「无法判定」，继而被旧库判死。
        实测：`lbapi9.com/api.php/provide/vod/at/xml` 返回 20 条 `<dd>`，
        形如 `正片$https://lbpl26.com/.../index.m3u8`，真拉分片 HTTP 200。
        现在改为：按 `$` 与空白切段，任何一段里出现 http(s) 就认。
        """
        for piece in re.split(r'[\$\s#]+', txt or ''):
            p = piece.strip().strip('"\'<>[]')
            if p.startswith(('http://', 'https://')) and p not in seen:
                seen.add(p)
                out.append(p)

    # 主要形态：<dd flag="xx">集名$url</dd> 或 <dd><![CDATA[url]]></dd>
    for m in re.finditer(r'<dd\b[^>]*>\s*(?:<!\[CDATA\[)?([^<]{4,600})', body):
        _absorb(m.group(1))
    # 兜底：<dd flag="xx$url$yy"> 这类属性里带地址的写法
    for m in re.finditer(r'<dd\b[^>]*flag="([^"]{4,600})"', body):
        _absorb(m.group(1))
    return out


def _probe_playable(url, *, timeout=TIMEOUT):
    """对播放地址做真实可达校验（S142）：m3u8 真拉一个分片拿字节，其它判 HTTP。

    返回 (ok, evidence)。ok=False 时证据说明原因，但**不据此直接判死**——
    由调用方结合整体结果判三态（单条播放地址坏不代表整个接口坏）。
    """
    if _is_m3u8(url):
        v, ev = check_m3u8(url, timeout=timeout)
        if v == '通过':
            return True, ev
        return False, f'm3u8 {v}：{ev}'
    code, body = _curl(url, timeout=timeout, rng='0-2048')
    if code in (200, 206) and len(body or '') > 0:
        return True, f'HTTP {code}·{len(body)}B'
    return False, f'HTTP {code}'


def check_vod(api, *, timeout=TIMEOUT):
    """点播源真实测活：真请求 ?ac=detail，校验含播放地址。三态。

    S142：**双形态**支持——JSON 版找 `vod_play_url`，XML 版找 `<dd><![CDATA[..]]></dd>`。
    通过判据统一为"拿到播放地址 + 该地址真拉得动"（m3u8 拉分片 / 其它拉前 2KB）。
    """
    base = api.split('?')[0].rstrip('/')
    # 苹果CMS 标准：先列分类（ac=list 或 ac=videolist），再取详情
    tried = []
    fake_seen = False
    xml_pages = 0        # S142：见过的 XML 形态响应的最大 pagecount
    xml_urls = []        # S142：XML 形态里取到的播放地址
    # S099 修正：sep 顺序必须 '?' 在前——无查询参数的地址用 '&' 拼接会 404（实测
    # cj.lziapi.com 等大量活跃源因此被误杀）；'&' 仅对已有 '?' 的地址有意义。
    for sep in ('?', '&'):
        for ac in ('ac=detail', 'ac=videolist'):
            probe = f'{base}{sep}{ac}'
            code, body = _curl(probe, timeout=timeout)
            tried.append((probe, code, len(body or '')))
            if code == 0:
                continue
            if code in (404, 410, 403, 451):
                # S099：404 不立即判失败——可能只是这一路拼法不对，继续试其它 sep/ac 组合
                continue
            if code in (200, 301, 302, 303, 307, 308) and body:
                # ① JSON 形态（P-R1 强化）：vod_play_url 的**取值必须非空且含真实播放地址**。
                # 只出现字段名（如 vod_play_url":"" 空串）不算通过——那是"列表可达"假活。
                m = re.search(r'vod_play_url["\']?\s*[:=]\s*["\']([^"\']{0,400})', body)
                if m and _has_playable(m.group(1)):
                    ev = m.group(1)[:80]
                    return '通过', f'HTTP {code}·JSON·有真实播放地址·{ev}'
                # 字段存在但取值为空/无地址 → 记下"假活"证据，继续尝试其它路
                if 'vod_play_url' in body:
                    fake_seen = True
                # ② XML 形态（S142 补）：地址在 <dd><![CDATA[..]]></dd>，无 vod_play_url 字段
                pc = re.search(r'pagecount="(\d+)"', body)
                xurls = _xml_play_urls(body)
                if pc:
                    xml_pages = max(xml_pages, int(pc.group(1)))
                if xurls:
                    for u in xurls:
                        xml_urls.append(u)
                    # 真拉第一条播放地址：m3u8 拉分片 / 其它拉前 2KB
                    ok, pev = _probe_playable(xurls[0], timeout=timeout)
                    if ok:
                        return '通过', (f'HTTP {code}·XML·{len(xurls)}条播放地址·'
                                        f'{pev}·{xurls[0][:70]}')
                    # 该条播放地址拉不动 → 继续试同响应的其它地址（防只取到广告/坏镜头）
                    for u in xurls[1:6]:
                        ok2, pev2 = _probe_playable(u, timeout=timeout)
                        if ok2:
                            return '通过', (f'HTTP {code}·XML·{len(xurls)}条播放地址·'
                                            f'{pev2}·{u[:70]}')
                    # 全部拉不动：记证据，继续试其它 sep/ac 组合（可能是这一路返了坏片）
                    tried[-1] = (probe, code, len(body))
                    continue
                # 分类列表返回（ac=list）也说明接口活，但没有播放地址 → 无法判定
                if 'class' in body[:500] or 'list' in body[:500]:
                    tried[-1] = (probe, code, len(body))
                    continue
    # 所有尝试都拿不到可用播放地址：区分"接口活但无播放地址"与"根本连不上"
    if any(c == 0 for _, c, _ in tried):
        # ★S169：把失败类别带出来。原来只说"均超时/不可达"，黑名单无从分辨
        #   "证书过期"（续期即活）和"域名没了"（真死），一律按失败次数判死。
        _k = _last_err_kind
        _tail = err_tail()
        if _k in CERT_KINDS:
            return '无法判定', f'均不可达（尝试{len(tried)}路）·【证书类】{_tail}'
        # ★S193：非证书类也说清是哪种不可达，别一律塌成"超时/不可达"
        return '无法判定', f'均{err_lead("超时/不可达")}（尝试{len(tried)}路）{_tail}'
    # S142：XML 形态取到地址但分片拉不动 → 明确失败，别退化成"无vod_play_url"
    if xml_urls:
        return '失败', (f'XML 有{len(xml_urls)}条播放地址但均拉不动（尝试{len(tried)}路）'
                        f'·{xml_urls[0][:70]}')
    if fake_seen:
        return '失败', (f'假活：vod_play_url 取值为空/无http地址，列表可达≠真实可用'
                        f'（尝试{len(tried)}路）')
    codes = [c for _, c, _ in tried]
    if codes and all(c in (404, 410, 403, 451) for c in codes):
        return '失败', f'HTTP {codes[0]}（{len(tried)}路均 4xx）'
    if xml_pages > 0:
        # S142：是真 XML 接口（有 pagecount）但没返回 <dd> 地址 → 分不清"接口薄"与"取参不对"
        return '无法判定', (f'XML 接口可达（pagecount={xml_pages}）但未取到播放地址'
                            f'（尝试{len(tried)}路）')
    return '失败', f'无vod_play_url/无XML播放地址（尝试{len(tried)}路，末路HTTP{tried[-1][1] if tried else 0}）'


def check_m3u8(url, *, timeout=TIMEOUT):
    """直播源真实测活：真拉 m3u8 → 真拉一个分片字节流。三态。"""
    code, body = _curl(url, timeout=timeout)
    if code == 0:
        # ★S193：别一律说"超时"——DNS 没了和超时，处置完全不同
        return '无法判定', f'm3u8 拉取{err_lead("超时/不可达")}{err_tail()}'
    if code in (404, 410, 403, 451):
        return '失败', f'm3u8 HTTP {code}'
    if code not in (200, 301, 302, 303, 307, 308) or not body:
        return '无法判定', f'm3u8 HTTP {code} 无正文'
    if '#EXTM3U' not in body:
        return '失败', f'非 m3u8（HTTP {code}，正文开头 {body[:40]!r}）'
    # 取第一个分片或子清单
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith('#')]
    if not lines:
        return '失败', 'm3u8 无任何地址行（空清单）'
    first = lines[0]
    if not first.startswith('http'):
        sp = urlsplit(url)
        origin = f'{sp.scheme}://{sp.netloc}'
        if first.startswith('/'):
            # S146 修：以 `/` 开头是**站内绝对路径**，只能拼 origin。
            # 旧代码 `base + '/' + first.lstrip('/')` 会拼成
            # `.../20261017/PUpmURkm/20261017/PUpmURkm/1500kb/...`（路径重复）→ 404 → 活源被判死。
            first = origin + first
        else:
            first = url.rsplit('/', 1)[0] + '/' + first
    # 真拉分片字节流（子清单则再下钻一层，最多 2 层）
    for _depth in range(2):
        scode, sbody = _curl(first, timeout=timeout, rng=None if _depth == 0 else '0-2048')
        if scode in (200, 206) and sbody and '#EXTM3U' in sbody and 'EXTINF' not in sbody:
            nxt = [l.strip() for l in sbody.splitlines()
                   if l.strip() and not l.startswith('#')]
            if nxt:
                n = nxt[0]
                if not n.startswith('http'):
                    sp = urlsplit(first)
                    n = (f'{sp.scheme}://{sp.netloc}{n}' if n.startswith('/')
                         else first.rsplit('/', 1)[0] + '/' + n)
                first = n
                continue
        break
    if scode == 0:
        return '无法判定', f'分片拉取{err_lead("超时")}（{first[:60]}）{err_tail()}'
    if scode in (200, 206) and len(sbody or '') > 0:
        return '通过', f'分片 {len(sbody)}B（{first[:60]}）'
    if scode in (404, 410, 403, 451):
        return '失败', f'分片 HTTP {scode}（{first[:60]}）'
    return '无法判定', f'分片 HTTP {scode}'


def _page_aggregation_probe(base, parents, *, timeout=TIMEOUT):
    """仅父级时补一刀：真拉一页父分类详情，看 pagecount/total 是否同步聚合。

    ★ 移植自道长 spider-craft 实战判据：站点只暴露父分类时，客户端常把子分类
      数据合并进父级展示；若 pagecount/total 没按合并后总量重算，就出现
      「1 条数据配 697 页」（total=1、pagecount=697）——前端分页器显示 697 页，
      翻过去全是空的/重复的。
    返回说明串；拉不到返 ''（不把网络问题说成"聚合没问题"——M-R60）。
    """
    pid = None
    for p in parents:
        cand = None
        if isinstance(p, dict):
            cand = p.get('type_id') or p.get('id') or p.get('tid')
        else:
            cand = p                     # XML 分支解析出来的是 int
        if cand is not None:
            pid = cand
            break
    if pid is None:
        return ''
    for sep in ('?', '&'):
        probe = f'{base}{sep}ac=detail&t={pid}&pg=1'
        code, body = _curl(probe, timeout=timeout)
        if code == 0 or code in (404, 410, 403, 451) or not body:
            continue
        m_pc = re.search(r'"?pagecount"?\s*[:=]\s*"?(\d+)', body)
        m_to = re.search(r'"?total"?\s*[:=]\s*"?(\d+)', body)
        if not (m_pc and m_to):
            continue
        pc, to = int(m_pc.group(1)), int(m_to.group(1))
        m_li = re.search(r'"?limit"?\s*[:=]\s*"?(\d+)', body)
        li = int(m_li.group(1)) if m_li else 0
        if to <= 1 and pc > 5:
            return (f'分页聚合异常：total={to} 却配 pagecount={pc}'
                    f'——子分类合并后未同步聚合（前端「1 条数据配 {pc} 页」）')
        if li:
            expect = -(-to // li)        # ceil(total/limit)
            if pc > max(expect * 3, expect + 10):
                return (f'分页聚合存疑：total={to}/limit={li} 应收录约 {expect} 页，'
                        f'实测 pagecount={pc}')
        return f'分页聚合正常（total={to}·pagecount={pc}）'
    return ''


def check_class(api, *, timeout=TIMEOUT):
    """点播源分类完整性检测（S101·父分类问题）。

    病根（用户 2026-10-16）：某条线路打开后分类栏只有几个大项、点细分是空的。
    根因：苹果CMS 分类树里 `type_pid=0` 是**父分类**、非 0 是**子分类**；只取父分类
    → 分类栏缺细分，数据若挂在子分类上则点开为空。

    本函数真请求 `?ac=class`（或 `at/xml` 风格的 class 接口），解析分类树，返回四态：
      '完整'    父子俱全，子分类挂对了爹（可取数）
      '仅父级'  只有 type_pid=0 的父分类，子分类全丢 → 会缺细分/点空
      '仅子级'  只有子分类无父分类 → 分类栏会乱/无归类
      '无分类'  接口不返回分类树（非苹果CMS 或分类接口缺失）
      外加 '无法判定'（超时/限流）与 '失败'（4xx/非 JSON）。
    返回 (verdict, evidence, detail)；detail 含 parent/child 计数，供入库证据。
    """
    base = api.split('?')[0].rstrip('/')
    tried = []
    for sep in ('?', '&'):
        for ac in ('ac=class', 'ac=list'):
            probe = f'{base}{sep}{ac}'
            code, body = _curl(probe, timeout=timeout)
            tried.append((probe, code, len(body or '')))
            if code == 0:
                continue
            if code in (404, 410, 403, 451):
                continue
            if not body:
                continue
            parsed = _parse_class_tree(body)
            if parsed is None:
                continue  # 不是分类树（可能是详情/列表）→ 继续试
            parents, children = parsed
            detail = {'parent': len(parents), 'child': len(children),
                      'probe': probe, 'http': code}
            if parents and children:
                return '完整', f'父{len(parents)}/子{len(children)}（{probe}）', detail
            if parents and not children:
                # ★ S194（移植道长判据）：仅父级时客户端惯常补救是合并子分类到父级，
                #   但 pagecount/total 必须同步聚合，否则「1 条数据配 697 页」。
                agg = _page_aggregation_probe(base, parents, timeout=timeout)
                if agg:
                    detail['page_agg'] = agg
                return '仅父级', (f'仅父分类{len(parents)}个·无子分类（{probe}）→ 细分点开为空'
                                  + (f'；{agg}' if agg else '')), detail
            if children and not parents:
                return '仅子级', f'仅子分类{len(children)}个·无父分类（{probe}）→ 分类栏无归类', detail
            return '无分类', f'分类树为空（{probe}）', detail
    if any(c == 0 for _, c, _ in tried):
        return '无法判定', f'分类接口均{err_lead("超时/不可达")}（尝试{len(tried)}路）{err_tail()}', {}
    codes = [c for _, c, _ in tried]
    if codes and all(c in (404, 410, 403, 451) for c in codes):
        return '失败', f'分类接口 HTTP {codes[0]}（{len(tried)}路均 4xx）', {}
    return '无分类', f'未返回可解析的分类树（尝试{len(tried)}路）', {}


def _parse_class_tree(body):
    """从响应体解析苹果CMS 分类树。返回 (parents, children) 或 None（非分类树）。

    容错三种形态：
      ① JSON：{"class":[{"type_id":1,"type_name":"电影","type_pid":0}, ...]}
      ② XML：<class><ty id="1" pid="0">电影</ty>...</class>
      ③ 分类在 list 里带 type_pid 字段
    判据：至少有一条记录**显式带 type_pid/type_pid 语义**才认（防把详情当分类）。
    """
    if not body:
        return None
    b = body.strip()
    # ① JSON 形态
    if b.startswith('{') or b.startswith('['):
        try:
            obj = json.loads(b)
        except Exception:
            return None
        arr = None
        if isinstance(obj, dict):
            arr = obj.get('class') or obj.get('classes') or obj.get('data') or obj.get('list')
        elif isinstance(obj, list):
            arr = obj
        if not isinstance(arr, list):
            return None
        parents, children = [], []
        for it in arr:
            if not isinstance(it, dict):
                continue
            pid = it.get('type_pid', it.get('pid'))
            if pid is None:
                continue  # 无 pid 语义 → 不是分类树
            try:
                pid = int(pid)
            except Exception:
                continue
            (parents if pid == 0 else children).append(it)
        if parents or children:
            return parents, children
        return None
    # ② XML 形态
    if '<class' in b[:200] or '<ty ' in b[:200]:
        parents, children = [], []
        for m in re.finditer(r'<ty\b[^>]*?(?:pid|type_pid)\s*=\s*["\']?(\d+)', b, re.I):
            pid = int(m.group(1))
            (parents if pid == 0 else children).append(pid)
        if parents or children:
            return parents, children
    return None


def _looks_like_py(body):
    """判正文是否像 Python/spider 源码（S109·py 测活强化）。

    用户要求：py 线要"测活"——不只看 HTTP 码，还要确认**真是可加载的 py/spider**。
    合格特征（命中任一即算）：
      · python 语法：def / class / import / from 语句
      · spider 约定：init(self / homeContent / categoryContent / searchContent
      · TVBox py 源常见：`class Spider` / `ext` / `self.extend`
    反例：HTML 错误页 / 跳转到登录页 / 空体 → 不算通过。
    """
    b = (body or '').strip()
    if len(b) < 40:
        return False
    low = b[:6000]
    if low[:1] == '<':          # HTML 开头 → 极可能是错误页/跳转页
        return False
    marks = ('def ', 'class ', 'import ', 'from ', 'init(self', 'homeContent',
             'categoryContent', 'searchContent', 'self.extend', 'class Spider')
    return any(m in low for m in marks)


def check_py(api, *, timeout=TIMEOUT):
    """py/spider 源真实测活（S109 强化）：真请求 → HTTP 通 且 正文是 Python/spider 源码。三态。

    与旧版差异：旧版只看"非空非错误页"；新版加 `_looks_like_py` 源码特征，
    防"HTTP 200 但其实是 HTML 跳转页/网关错误页"被误判通过。
    """
    code, body = _curl(api, timeout=timeout)
    if code == 0:
        return '无法判定', 'py 接口超时/不可达'
    if code in (404, 410, 403, 451):
        return '失败', f'HTTP {code}'
    if code in (200, 301, 302, 303, 307, 308):
        if _looks_like_error_page(body):
            return '失败', f'HTTP {code} 但为错误页/空正文'
        if not _looks_like_py(body):
            return '失败', f'HTTP {code} 但正文非 Python/spider 源码（{len(body)}B，疑跳转/错误页）'
        return '通过', f'HTTP {code}·Python源码 {len(body)}B'
    return '无法判定', f'HTTP {code}'


def check_js(api, *, timeout=TIMEOUT):
    """js / js 配置源真实测活（S144·P3）：真请求 → 正文像 js 源码或合法 json。三态。

    为什么必须有独立通道：成品源库 12 条 js 此前走 `vod` 分支，判据是
    「响应里找 vod_play_url」——**js 文件里当然没有**，于是 12 条全被判失败。
    这和 S141 的 XML 版苹果CMS 是**同一个错**：判据写的是「有没有某个词」，
    而不是「里面有没有货」。M-R96 的对照组（同一 host 一条通过一条不可达）
    也有它的贡献——那条「通过」的恰好是 vod，死的都是 js。
    """
    code, body = _curl(api, timeout=timeout)
    if code == 0:
        return '无法判定', 'js 接口超时/不可达'
    if code in (404, 410, 403, 451):
        return '失败', f'HTTP {code}'
    if code not in (200, 301, 302, 303, 307, 308):
        return '无法判定', f'HTTP {code}'
    b = (body or '').strip()
    if not b:
        return '失败', f'HTTP {code} 但正文为空'
    low = b[:4000].lower()
    # JSON 配置形态（{"sites":[...]} / {"lives":[...]}）
    if b[:1] in '{[':
        for k in ('"sites"', '"lives"', '"parses"', '"spider"', '"wallpaper"'):
            if k in low:
                return '通过', f'HTTP {code}·JSON配置 {len(b)}B（含 {k}）'
        return '失败', f'HTTP {code}·JSON 但无 sites/lives/parses 键（{len(b)}B）'
    # js 源码形态：找 drpy/hipy 的典型符号，而不是找 vod_play_url
    marks = [m for m in ('var rule', 'rule =', 'module.exports', 'export default',
                         'function ', 'const ', '=> ', 'drpy', 'hipy', 'let rule')
             if m in low]
    if marks:
        return '通过', f'HTTP {code}·js源码 {len(b)}B（命中 {marks[0]}）'
    if _looks_like_error_page(b):
        return '失败', f'HTTP {code} 但为错误页'
    return '失败', f'HTTP {code} 但正文既非 js 源码也非 JSON 配置（{len(b)}B）'


# ═══════════ 解析接口（parses）测活（S173） ═══════════
# 病根：探源司把 `parses` 采进了 rows（kind='parse'），**之后没有任何判定**就搁置了。
# 而解析接口恰恰是「HTTP 200 最不可信」的一类 —— 实测 7 个解析站（2026-10-19）：
#   jx.xmflv.com    200 3105B  正文是"江湖路远，后会无期"告别页，0 个流请求
#   jx.aidouer.net  200 7475B  同上，告别页
#   jx.yparse.com   200 26976B 正文**一个 m3u8 都没有**，真跑后二级 iframe 出 2 条 m3u8
#   jx.m3u8.tv      404 / jx.nnxv.cn 403 / playerjy·parwix·okjx 均超时(0)
# ∴ 两条判据铁律：
#   ① **只看正文正则必错**：yparse 正文没流、实际有流（漏判）；xmflv 正文像页面、实际全空（误判）。
#   ② **只到顶层必错**：真解析器普遍把活干在 iframe 里（yparse → jx.nmjsjs.com 才是播放器）。
# 故本档的"通过"只能由**真跑**拿到：DOM 里出现 video(src/currentSrc) 或捕获到 m3u8 请求，
# 且该地址再经 check_m3u8 真拉到分片，才算通过。做不到真跑的环境一律判「无法判定」。

PARSE_TEST_VIDEO = 'https://v.qq.com/x/cover/mzc00200mp8vo9b.html'
# 封面/追踪类 CDN 极易被误当流；只认这几种真能喂播放器的
_STREAM_HINT = ('.m3u8', '.mp4', '.flv')


def _parse_url(api, video=PARSE_TEST_VIDEO):
    """拼解析地址：api 若已含 url= 则直接接，否则按 `?url=` 追加。

    真实 parses 配置里两种都有：`https://jx.x/?url=` 与 `https://jx/y.php`。
    另有少数要求 `url` 后跟**未编码**原地址——故编码失败时退回明文（并如实记录）。
    """
    from urllib.parse import quote as _q
    a = (api or '').strip()
    if not a:
        return ''
    try:
        q = _q(video, safe='')
    except Exception:
        q = video
    if 'url=' in a:
        return a + q if a.rstrip().endswith(('=', '/')) else a + '&url=' + q
    return a + ('&' if '?' in a else '?') + 'url=' + q


def _has_browser():
    """能不能真跑浏览器。不能就别装作能——返回 False 时上层判「无法判定」。"""
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def _run_parse_page(url, *, wait_s=12, timeout=25000):
    """真开一个 headless 页 → 等 JS 跑完 → 收集所有 frame 的 video 地址 + 流请求。

    返回 dict：{'title','video':[..],'streams':[..],'frames':n,'err':str}
    ★ 必须遍历 **所有 frame**：实测 yparse 顶层 title='云解析'、video 空，
      真正的播放器在 iframe[1]（title='正在播放：开端 第01集'）。只看主 frame = 判死活站。
    """
    import asyncio

    async def _go():
        from playwright.async_api import async_playwright
        out = {'title': '', 'video': [], 'streams': [], 'frames': 0, 'err': ''}
        streams = []

        async with async_playwright() as p:
            b = await p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                pg = await b.new_page(user_agent=UA)
                pg.on('request', lambda r: streams.append(r.url)
                      if any(k in r.url.lower() for k in _STREAM_HINT) else None)
                try:
                    await pg.goto(url, timeout=timeout, wait_until='domcontentloaded')
                except Exception as e:
                    out['err'] = f'goto {type(e).__name__}'
                # 等 JS 出流。解析器普遍先请求影片详情再解密，5s 常不够（实测 9s 才出 blob）
                await pg.wait_for_timeout(wait_s * 1000)
                try:
                    out['title'] = await pg.title()
                except Exception:
                    pass
                for fr in pg.frames:
                    try:
                        v = await fr.evaluate(
                            "()=>{const v=document.querySelector('video');"
                            "return v?(v.currentSrc||v.src||''):''}")
                        if v:
                            out['video'].append(v)
                    except Exception:
                        continue
                out['frames'] = len(pg.frames)
            finally:
                try:
                    await b.close()
                except Exception:
                    pass
        # 去重保序
        seen, uniq = set(), []
        for u in streams:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        out['streams'] = uniq
        return out

    try:
        return asyncio.run(_go())
    except Exception as e:
        return {'title': '', 'video': [], 'streams': [], 'frames': 0,
                'err': f'{type(e).__name__}: {e}'}


def check_parse(api, *, video=PARSE_TEST_VIDEO, wait_s=12, timeout=TIMEOUT):
    """解析接口真实测活（S173）。三态。

    '通过'     真跑到流：DOM 有 video 或捕获到 m3u8，**且** 该地址真拉到分片字节
    '失败'     明确不可用：4xx/播放器明确报错/真跑后既无 video 也无流请求
    '无法判定' 本机跑不了浏览器 / 超时 / 页面在跑但流请求失败（不确定是我方还是它的锅）

    ★ 为什么不返回「解析壳」这个第四态：三态契约（M-R60）只允许三个。
      「页面能开、正文无流」到底属于哪态，取决于**能不能真跑**：
        · 能真跑且跑完无流 → '失败'（这是确定的结论，不是猜）
        · 跑不了（无浏览器）→ '无法判定'（我方能力不足，不许算它死）
      把"壳"单列成态会诱导调用方把两种完全不同的情形折叠到一起。
    """
    if not api or not str(api).strip().startswith(('http://', 'https://')):
        return '失败', '解析地址非 http(s)，无法请求'
    url = _parse_url(api, video)

    # ① 先做一次纯 HTTP 探底：4xx 直接判死，省一次 20s 浏览器启动
    code, body = _curl(url, timeout=timeout, rng='0-4096')
    if code in (404, 410, 451):
        return '失败', f'HTTP {code}（解析地址不存在/已下线）'
    if code == 403:
        # 403 可能是「拒绝机房 IP」而非站点死——与其他模块同一口径，判无法判定
        return '无法判定', 'HTTP 403（可能为 IP/风控拦截，非确证已死）'
    if code == 0:
        return '无法判定', '解析地址不可达（超时/DNS/证书），非确证已死'

    # ② 真跑。做不到就如实说做不到
    if not _has_browser():
        return '无法判定', '本机无 playwright，解析出流无法真验（≠已死）'
    r = _run_parse_page(url, wait_s=wait_s)
    if r['err'] and not r['streams'] and not r['video']:
        return '无法判定', f"浏览器运行异常 {r['err']}（≠已死）"

    cands = []
    for v in r['video']:
        # blob: 是 MSE 中间态，本身拉不动；真地址在同时捕获的 m3u8 里
        if v.startswith('blob:'):
            continue
        cands.append(v)
    cands += r['streams']

    if not cands:
        return '失败', (f"真跑 {r['frames']} 个 frame、等 {wait_s}s：无 video、无 m3u8 请求"
                        f"（标题「{(r['title'] or '')[:20]}」=页面能开但不出流）")

    # ③ 拿到了地址不算数 —— 真拉一次分片才算（与 check_m3u8 同一判据，不另写）
    tried = []
    for u in cands[:3]:
        if not _is_m3u8(u):
            continue
        v, ev = check_m3u8(u, timeout=timeout)
        tried.append((u, v, ev))
        if v == '通过':
            return '通过', f'真出流·{ev}·{u[:90]}'
    if tried:
        u, v, ev = tried[0]
        if v == '无法判定':
            return '无法判定', f'抓到流地址但拉不动（{ev}）·{u[:80]}'
        return '失败', f'抓到流地址但不可播（{ev}）·{u[:80]}'
    # 只有非 m3u8 的直链（mp4/flv）：拉前 2KB 验字节
    for u in cands[:3]:
        c2, b2 = _curl(u, timeout=timeout, rng='0-2048')
        if c2 in (200, 206) and len(b2 or '') > 0:
            return '通过', f'真出流·直链 HTTP {c2}·{len(b2)}B·{u[:80]}'
    return '无法判定', f'捕获 {len(cands)} 条疑似地址但均拉不动（非确证已死）'


def check_parse_stable(api, *, rounds=3, video=PARSE_TEST_VIDEO, wait_s=12, timeout=TIMEOUT):
    """解析接口**稳定性**复测（S173）。返回 (verdict, evidence, detail)。

    ★ 为什么必须多测几轮：实测「闪电」（https://bfq.txnp.cn/excessive）连测 4 次
      → 通过 / 通过 / 通过 / 无法判定。它是**抖动型**：m3u8 清单时好时坏。
      单测一次就入库，等于把"碰巧那一刻活着"当成"这个源可用"——
      用户拿到手第一次点开就转圈，而库里写着「真实出流=通过」。
      这与 S110「首片耗时/下载速率」是同一类问题：**可用 ≠ 稳定可用**。

    判据（不折叠三态，M-R60）：
      · 全轮通过                  → '通过'
      · 有过通过、也有过失败      → '通过' 但证据标「抖动 x/y」+ 降级建议（可用但不稳）
      · 全轮失败                  → '失败'
      · 其余（含无法判定占多）    → '无法判定'
    detail 里带逐轮结果，供写库时填 `出流证据`——**证据必须是逐轮原文，不是一句"通过"**。
    """
    seq = []
    for _ in range(max(1, rounds)):
        v, ev = check_parse(api, video=video, wait_s=wait_s, timeout=timeout)
        seq.append((v, ev))
    marks = [v for v, _ in seq]
    n_ok = marks.count('通过')
    n_ng = marks.count('失败')
    n_uk = marks.count('无法判定')
    detail = ' / '.join(f'{v}' for v in marks)
    ev0 = seq[0][1] if seq else ''
    if n_ok == len(marks):
        return '通过', ev0, detail
    # ★ 抖动判据是「有通过 + 有不通过」，**不是**「有通过 + 有失败」。
    #   实测闪电是「通过/无法判定/通过」——一个"失败"都没有，
    #   按旧判据会掉进末尾兜底，证据里连「抖动」二字都不剩，读的人根本不知道它其实能出流。
    #   这类"半活"源入库后最坑：用户点十次成两次，而库里只写「无法判定」。
    if n_ok and (n_ng or n_uk):
        return '通过', f'抖动 {n_ok}/{len(marks)}（{detail}）·可用但不稳·{ev0[:60]}', detail
    if marks and n_ng == len(marks):
        return '失败', ev0, detail
    return '无法判定', f'全轮未出流（{detail}）·{ev0[:70]}', detail


def check_auto(url, *, kind=None, timeout=TIMEOUT):
    """自动判类 + 真实测活。返回 (verdict, evidence, kind)。"""
    k = kind
    if not k or k == 'auto':
        # S144：判类统一走 classify_source（单一事实源），不再各处内联 if-elif。
        # 但 check_auto 的 kind 取值域是 vod/m3u8/py/js/class，需做一次映射。
        shape = classify_source(url)
        k = {'采集': 'vod', '直链': 'm3u8' if _is_m3u8(url) else 'vod',
             'js': 'js', 'py': 'py', 'csp': 'py', '其它': 'vod'}.get(shape, 'vod')
    if k == 'class':                       # S101：分类完整性检测
        v, ev, _ = check_class(url, timeout=timeout)
        return v, ev, 'class'
    if k == 'parse':                       # S173：解析接口
        v, ev = check_parse(url, timeout=timeout)
        return v, ev, 'parse'
    fn = {'vod': check_vod, 'm3u8': check_m3u8, 'py': check_py, 'js': check_js}[k]
    v, ev = fn(url, timeout=timeout)
    return v, ev, k


def cmd_one(a):
    # S173：parse 档的两个实测参数要能穿过 check_auto —— 否则 --video/--wait 是死参数
    if a.kind == 'parse':
        v, ev = check_parse(a.url, video=a.video, wait_s=a.wait)
        k = 'parse'
    else:
        v, ev, k = check_auto(a.url, kind=a.kind)
    if a.json:
        print(json.dumps({'url': a.url, 'kind': k, 'verdict': v, 'evidence': ev},
                         ensure_ascii=False))
    else:
        mark = {'通过': '✅', '完整': '✅', '失败': '❌', '无法判定': '⚠️',
                '仅父级': '⚠️', '仅子级': '⚠️', '无分类': '⬜'}.get(v, '·')
        print(f'{mark} [{k}] {v} — {ev}\n   {a.url}')
    return 0


def cmd_batch(a):
    items = json.load(open(a.batch, encoding='utf-8'))
    if isinstance(items, dict):
        items = items.get('items') or items.get('sites') or items.get('lives') or []
    # S144 修：字段名兼容。探源司全线（候选/剔失清单/repack 产物）用的是 **`api接口`**，
    # 而本函数旧版只认 `api`/`url` → 取到空串 → 全部拿空 URL 去测活 →
    # 一律「无法判定（均超时/不可达）」。**这不是网络问题，是字段名读丢了**，
    # 且表现与"源真失效"完全一样，会和 prune 的"待复测"混成一堆，极难分辨。
    _KEYS = ('api接口', 'api', 'url', 'link', '地址')
    def _pick(it):
        for k in _KEYS:
            v = str(it.get(k) or '').strip()
            if v:
                return v
        return ''

    def _one(it):
        url = _pick(it)
        # 空 URL 不许"照样测活"——直接标成配置错误，别让它伪装成网络失败
        if not url:
            return {'name': it.get('name') or it.get('名称', ''), 'url': '',
                    'kind': '-', 'verdict': '失败',
                    'evidence': f'条目无地址字段（已有键：{list(it)[:6]}）——测活输入为空，非网络问题'}
        kind = it.get('kind') or ('m3u8' if '.m3u8' in url.lower() else None)
        if (it.get('kind') or '').lower() == 'parse':
            v, ev = check_parse(url, video=a.video, wait_s=a.wait)
            k = 'parse'
        else:
            v, ev, k = check_auto(url, kind=kind)
        return {'name': it.get('name') or it.get('名称', ''), 'url': url, 'kind': k,
                'verdict': v, 'evidence': ev}
    # ★ M-R114：走统一入口。测活打的是公网站点，归 B 档（上限 6）。
    #   单条异常不拖垮全批（on_error='record'），失败条目会单独列出。
    rep = run_bounded(items, _one, tier=TIER_WEB, workers=a.workers, label='真实测活')
    res = rep.results
    if rep.errors:
        print(f'  ⚠ 执行异常 {rep.fail_count} 条（已跳过，不拖垮全批）')
    print(f'  {rep.summary()}')
    ok = [r for r in res if r['verdict'] == '通过']
    ng = [r for r in res if r['verdict'] == '失败']
    un = [r for r in res if r['verdict'] == '无法判定']
    print(f'🔎 真实测活 {len(res)} 条：✅通过 {len(ok)} / ❌失败 {len(ng)} / ⚠️无法判定 {len(un)}')
    for r in ok:
        print(f'   ✅ [{r["kind"]}] {r["name"][:30]:30} {r["evidence"]}')
    for r in ng[:20]:
        print(f'   ❌ [{r["kind"]}] {r["name"][:30]:30} {r["evidence"]}')
    for r in un[:20]:
        print(f'   ⚠️ [{r["kind"]}] {r["name"][:30]:30} {r["evidence"]}')
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    out = os.path.join(os.path.dirname(os.path.abspath(a.batch)),
                       'livecheck_' + os.path.basename(a.batch))
    json.dump(res, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'💾 结果落盘：{out}')
    return 0


def main():
    ap = argparse.ArgumentParser(description='真实可用测活引擎（点播拉片/直播拉流）')
    ap.add_argument('url', nargs='?', help='单条 URL')
    ap.add_argument('--kind', default='auto',
                    choices=['auto', 'vod', 'm3u8', 'py', 'js', 'class', 'parse'])
    ap.add_argument('--batch', help='批量 JSON 文件')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--video', default=PARSE_TEST_VIDEO,
                    help='kind=parse 时的测试影片页（默认开端 S01）')
    ap.add_argument('--wait', type=int, default=12,
                    help='kind=parse 时等 JS 出流的秒数（实测 9s 才出 blob，少于 8 会误判死）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    if a.batch:
        return cmd_batch(a)
    if a.url:
        return cmd_one(a)
    ap.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
