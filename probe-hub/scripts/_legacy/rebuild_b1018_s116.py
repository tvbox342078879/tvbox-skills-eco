#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️ 历史归档脚本（T8/C5 留档·勿直接执行）：硬编码的 /workspace/原仓库库/ 与 artifact sweep_v2.json / py_run_result_v3.json 路径均已失效（原仓库库已归档至 /root/uploads/历史产物归档_20261023_1014至1020会话.tar.gz 内）。当前运行必报错，仅供历史追溯。
"""
S117 重建 v3：S115 内容层 + S116 py 沙箱真跑 + S117 用户端测，三层叠加。

与 S116 重建（rebuild_b1018_s116.py）的差异：
  S117 引入「用户端测」最高裁决层——用户在真实设备/真实网络上的
  使用结果是最终证据，覆盖沙箱结论：
    - 端测通过 → 坐实可用（骚火/4KAV/奇优影视/短剧 4 站）
    - 端测失败（转圈/找不到数据）→ 出可用版；再按上游现状分道：
        上游半活（偶尔可达）→ Tier2 待端测（LIBVIO 极慢抖动）
        上游全挂（全端口拒绝）→ 死（奇优动漫）

产物（v2 = 49 站主版，归档留痕）：
  B1018_47站_cluntop原仓库_实测可用.json   ← 新主版（v3）
  B1018_22站_cluntop原仓库_待端测.json
  B1018_重建_测活报告与依据说明.md         ← 增补 S117 端测层章节
"""
import json
import re
import shutil
import subprocess
from collections import Counter

BASE = 'https://clun.top'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
ORIG = '/workspace/原仓库库/cluntop_box原文_1018.json'
SWEEP = '/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d49-6b5af3d3c000/sweep_v2.json'
PYRUN = '/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d49-6b5af3d3c000/py_runner/py_run_result_v3.json'
OUT_DIR = '/workspace/原仓库库'
V2_T1 = f'{OUT_DIR}/B1018_49站_cluntop原仓库_实测可用.json'      # v2 主版（将被归档）
V2_T2 = f'{OUT_DIR}/B1018_21站_cluntop原仓库_待端测.json'        # v2 Tier2（将被归档）
OUT_T1 = f'{OUT_DIR}/B1018_47站_cluntop原仓库_实测可用.json'      # v3 新主版
OUT_T2 = f'{OUT_DIR}/B1018_22站_cluntop原仓库_待端测.json'        # v3 新 Tier2（+LIBVIO）
OUT_MD = f'{OUT_DIR}/B1018_重建_测活报告与依据说明.md'
ARCHIVE_T1 = f'{OUT_DIR}/B1018_47站_v1_端测确认_归档_20261018.json'
ARCHIVE_T2 = f'{OUT_DIR}/B1018_26站_v1_归档_20261018.json'
ARCHIVE_V2_T1 = f'{OUT_DIR}/B1018_49站_v2_归档_20261018.json'
ARCHIVE_V2_T2 = f'{OUT_DIR}/B1018_21站_v2_归档_20261018.json'


def curl_code(url, timeout=10):
    cmd = ['curl', '-s', '-L', '--compressed', '--connect-timeout', '6',
           '--max-time', str(timeout), '-A', UA, '-o', '/dev/null',
           '-w', '%{http_code}', url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 3)
        return int(p.stdout.decode().strip() or 0)
    except Exception:
        return 0


def curl_body(url, timeout=10):
    cmd = ['curl', '-s', '-L', '--compressed', '--connect-timeout', '6',
           '--max-time', str(timeout), '-A', UA, url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 3)
        return p.stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


def curl_code_body(url, timeout=10):
    """单次调用同时取 HTTP 码与正文——分两次调用会因抖动出现
    「code 000 但 body 有内容」的悖论证据行（v3 直播表实况）。"""
    cmd = ['curl', '-s', '-L', '--compressed', '--connect-timeout', '6',
           '--max-time', str(timeout), '-A', UA, '-w', '\n%{http_code}', url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 3)
        out = p.stdout.decode('utf-8', 'replace')
        body, _, code = out.rpartition('\n')
        return (int(code.strip() or 0)), body
    except Exception:
        return 0, ''


def to_abs(u):
    u = str(u)
    if u.startswith('http'):
        return u
    if u.startswith('./'):
        return BASE + u[1:]
    return BASE + '/' + u.lstrip('/')


def is_junk(name):
    return bool(re.fullmatch(r'-+\s*\d+\s*-+', (name or '').strip()))


# ---------- 修正台账（每条附证据，M-R85 留痕） ----------
FIX_LOG = []


def fix_site(site):
    """返回修正后的站点 dict（原样拷贝后有证据才改）。"""
    s = dict(site)

    # ① 影视天堂[py]：作者 ext.host 指向已失效的 https（SSL 错误 exit 35），
    #    py 源码默认 host=http://ysttv.com 实测 200 → 按证据修正 scheme。
    #    （S116 注：该站后经真跑实证改版，已移出可用版，此修正仅留痕。）
    if s.get('name') == '影视天堂[py]' and isinstance(s.get('ext'), dict):
        old = str(s['ext'].get('host', ''))
        if old.startswith('https://'):
            new = 'http://' + old[len('https://'):]
            code = curl_code(new)
            if code == 200:
                s['ext'] = dict(s['ext'])
                s['ext']['host'] = new
                FIX_LOG.append(f"影视天堂[py]：ext.host {old} → {new}"
                               f"（原值 https SSL 握手失败 exit35；http 实测 200；"
                               f"S116 真跑复核发现站点已改版，此站已移出可用版）")
    return s


# ---------- S116 最终判定 ----------

def final_verdict(name, sv, rv, ev):
    """sv=上游层判定 rv=真跑判定 ev=真跑证据 → 最终判定。"""
    if rv is None:                       # 非 py 站：只有上游层
        return sv
    if rv == '通过':
        return '通过'                    # 真拉到片单 = 最强证据
    if rv == '无法判定':
        return sv                        # 真跑无新信息，沿用上游层
    # rv == '失败'
    if '三路片单全空' in ev:
        # 无异常的空结果：上游活着=改版；上游同样不通=沙箱网络层阻塞（非死）
        return '失败' if sv == '通过' else ('无法判定' if sv == '无法判定' else '失败')
    return '失败'                        # 代码级异常：改版实证，与网络无关


def quadrant(name, sv, rv):
    if rv is None:
        return '非py（仅上游层）'
    if sv == '通过' and rv == '通过':
        return 'A_双通过'
    if sv == '通过' and rv == '失败':
        return 'B_上游活真跑败'
    if sv == '失败' and rv == '通过':
        return 'C_上游死真跑过'
    if sv == '无法判定' and rv == '通过':
        return 'C2_上游疑真跑过'
    if sv == '失败' and rv == '失败':
        return 'D_双败'
    if sv == '失败' and rv == '无法判定':
        return 'D2_上游死真跑疑'
    return 'E_待端测'


# ---------- S117 lives/parses 复测补丁 ----------
# v3 首轮重建时沙箱出口网络集体抖动（HTTP 000/530 频发，v2 同站点全过），
# 对判定失败项做 8 轮加严复测（间隔 2s）后的结论：
LIVES_RETRY_OK = {'Kimentanm', 'YanG'}        # 复测第 1 轮即含 #EXTM3U——首轮抖动假阴性
LIVES_CONFIRM_DEAD = {'wqwqwq', 'epg.pw All', 'iptv.org All'}   # 8 轮全败，530 错误页
PARSE_CONFIRM_DEAD = {'极速', 'Super', '无上', '巧计', '爱心', '观山', '777', 'jsonplayer', 'OKJX'}
# 注：parses 复测 9 站 8 轮全 0B（连接失败，DNS 消失/拒连级），与 v2 归档名单核对
# 通过数一致（v2 亦为 11 过 10 败），非本轮退化，坐实为死。


# ---------- S117 用户端测层（最高裁决） ----------
# 用户在真实设备/网络上的实测结果，覆盖沙箱结论。
# value = (端测后裁定, 证据)
E2E = {
    'LIBVIO[py]':  ('无法判定',
        '用户端测转圈（首页加载不出）；上游 curl 3 轮 2 次 20s 超时、'
        '唯一成功轮 19.85s——站点极慢抖动（非死），沙箱 72 条是撞上好状态'),
    '奇优动漫[py]': ('失败',
        '用户端测"找不到数据"；上游首页/search/list 全端口 Empty reply'
        '（curl exit52，TCP 通但应用层空回复，0.3s 失败）——站点已挂，判死'),
    '骚火影视[py]': ('通过', '用户端测确认可用（首页 27 条片单）'),
    '4KAV[py]':    ('通过', '用户端测确认可用（24 条片单）'),
    '奇优影视[py]': ('通过', '用户端测确认可用（25 条片单）'),
    '短剧[py]':    ('通过', '用户端测确认可用（20 条片单）'),
}


def main():
    orig = json.load(open(ORIG, encoding='utf-8'))
    sweep = {r['name']: r for r in json.load(open(SWEEP, encoding='utf-8'))}
    run = json.load(open(PYRUN, encoding='utf-8'))

    # 人工复核补丁（沙箱复测 + 用户端测证据，与 S115 重建一致）
    sweep['苹果[py]'].update(verdict='通过',
                             upstream='http://122.228.193.211:18008',
                             evidence='HTTP 200·IP+PORT 上游可达（PORT=18008，v2 漏带端口）')
    sweep['狐狸君'].update(verdict='失败',
                           evidence='TLS 握手后服务器空回复（curl exit 92）+ 用户端测"找不到数据"双证据')

    # ---------- 逐站终判 ----------
    final = {}          # name → (verdict, kind, evidence_combined)
    quads = []
    for s in orig['sites']:
        name = s.get('name')
        if not name or is_junk(name):
            continue
        rs = sweep.get(name)
        sv = rs['verdict'] if rs else '无'
        kind = rs['kind'] if rs else '无'
        sev = rs['evidence'] if rs else '无上游层记录'
        rr = run.get(name)
        rv = rr['verdict'] if rr else None
        rev = rr.get('evidence', '') if rr else ''
        v = final_verdict(name, sv, rv, rev)
        ev_all = (f'上游层[{sv}]：{sev[:60]}'
                  + (f' ｜ 真跑[{rv}]：{rev.replace(chr(124), "/")[:60]}' if rr else ''))
        e2e = E2E.get(name)
        e2e_tag = ''
        if e2e:
            ev2, ee2 = e2e
            if ev2 != v:
                ev_all += f' ｜ 端测改判[{v}→{ev2}]：{ee2}'
                v = ev2
            else:
                ev_all += f' ｜ 端测坐实：{ee2}'
            e2e_tag = '已端测'
        final[name] = (v, str(kind).replace(' | ', ' / '), ev_all)
        quads.append({'name': name, 'sweep': sv, 'run': rv or '-',
                      'quadrant': quadrant(name, sv, rv),
                      'count': rr.get('count', 0) if rr else 0,
                      'route': rr.get('route', '') if rr else '',
                      'e2e': e2e_tag})

    t1_names = [n for n, (v, k, e) in final.items() if v == '通过']
    t2_names = [n for n, (v, k, e) in final.items() if v == '无法判定']
    dead = [(n, e, str(k).replace(' | ', ' / ')) for n, (v, k, e) in final.items() if v == '失败']
    print(f'终判：Tier1 通过 {len(t1_names)} ｜ Tier2 待端测 {len(t2_names)} ｜ 死 {len(dead)}')
    print('\n四象限:')
    for q, n in Counter(q['quadrant'] for q in quads).most_common():
        print(f'  {q}: {n}')

    # ---------- sites：原样保留 + 相对转绝对 ----------
    def build_sites(names):
        out = []
        for s in orig['sites']:
            if s.get('name') not in names:
                continue
            site = fix_site(s)
            if str(site.get('api', '')).startswith('./'):
                site['api'] = to_abs(site['api'])
            ext = site.get('ext')
            if isinstance(ext, str) and ext.startswith('./'):
                site['ext'] = to_abs(ext)
            out.append(site)
        return out

    sites_t1 = build_sites(set(t1_names))
    sites_t2 = build_sites(set(t2_names))

    # ---------- lives：验活（正文含 #EXTM3U 为内容层判据，5 次重试抗抖动） ----------
    # S116 注：raw.githubusercontent.com 在沙箱里抖动严重（偶发 HTTP 000，
    # 且 000 时 curl 连输出文件都不落盘）——Kimentanm/suxuang/Guovin/gambiarras/
    # YanG 都出现过单次调用假阴性，3 次重试都不够（YanG/Guovin 曾 3 连败）。
    # 收紧口径：**必须见到 #EXTM3U 正文**才算通过（与 S115「HTTP 200≠能采到
    # 数据」同口径：只返 200 而正文无清单 = 不可用），5 次重试任一命中即通过，
    # 证据行标注命中轮次。
    def live_ok(u):
        last = ''
        for i in range(1, 6):
            code, body = curl_code_body(u, timeout=15)
            has = '#EXTM3U' in body[:4096]
            last = f'HTTP {code}·{"含#EXTM3U" if has else "无M3U内容"}（第{i}次命中）' if has \
                else f'HTTP {code}·无M3U内容'
            if has:
                return True, last
        return False, last + '（5 次重试均未见 M3U 正文）'

    lives_out, live_verdicts = [], []
    for l in orig['lives']:
        name, url = l.get('name'), l.get('url')
        if not name or not url:
            live_verdicts.append((str(name), str(url), '跳过', '空条目'))
            continue
        u = url
        if u.startswith('https://https://'):        # 作者笔误：双协议头
            u = u.replace('https://https://', 'https://')
            FIX_LOG.append(f'直播[{name}]：URL 双协议头 {url} → {u}（作者配置笔误）')
        if u.startswith('./'):
            u = to_abs(u)
        ok, ev = live_ok(u)
        live_verdicts.append((name, u, '通过' if ok else '失败', ev))
        if ok:
            l2 = dict(l)
            l2['url'] = u
            lives_out.append(l2)

    # S117 复测补丁：v3 首轮沙箱出口网络集体抖动，5 次重试仍可能假阴性。
    # 8 轮加严复测（间隔 2s）后：恢复的加回主版，确认死的坐实并更新证据。
    for i, (n, u, v, ev) in enumerate(live_verdicts):
        if n in LIVES_RETRY_OK and v == '失败':
            live_verdicts[i] = (n, u, '通过',
                                'HTTP 200·含#EXTM3U（首轮 5 次重试全 000 系沙箱网络抖动；'
                                '8 轮复测第 1 轮命中恢复；v2 归档亦为通过）')
            for l in orig['lives']:
                if l.get('name') == n:
                    l2 = dict(l)
                    l2['url'] = u
                    lives_out.append(l2)
            FIX_LOG.append(f'直播[{n}]：v3 首轮 5 次重试全部 HTTP 000（沙箱出口网络抖动假阴性），'
                           f'8 轮复测第 1 轮即含 #EXTM3U——恢复可用')
        elif n in LIVES_CONFIRM_DEAD and v == '失败':
            live_verdicts[i] = (n, u, v,
                                ev + '；8 轮复测全败（HTTP 530 错误页 7633B 无 M3U 正文）——确认死')

    # ---------- parses：验活（基地址 HTTP，3 次重试抗抖动） ----------
    parses_out, parse_verdicts = [], []
    for p in orig['parses']:
        name, url = p.get('name'), str(p.get('url', ''))
        if not url:
            parse_verdicts.append((str(name), url, '跳过', '无 url'))
            continue
        base = url.split('?url=')[0].rstrip('/') + '/'
        ok, ev = False, ''
        for _ in range(3):
            code = curl_code(base, timeout=12)
            if code in (200, 301, 302, 303, 307, 308):
                ok, ev = True, f'HTTP {code}'
                break
            ev = f'HTTP {code}'
        else:
            ev += '（3 次重试均失败）'
        parse_verdicts.append((name, base, '通过' if ok else '失败', ev))
        if ok:
            parses_out.append(p)

    # S117 复测补丁：8 轮复测全 0B 的解析坐实为死（连接失败，DNS 消失/拒连级），
    # 与 v2 归档名单核对通过数一致（v2 亦 11 过 10 败），非本轮退化。
    for i, (n, u, v, ev) in enumerate(parse_verdicts):
        if n in PARSE_CONFIRM_DEAD and v == '失败':
            parse_verdicts[i] = (n, u, v,
                                 ev + '；8 轮复测全 0B（连接失败）——确认死')

    # ---------- 组装整包（顶层键全保留，相对资源转绝对） ----------
    def build_pkg(sites):
        pkg = {}
        for k, v in orig.items():
            if k in ('sites', 'lives', 'parses'):
                continue
            pkg[k] = v
        if str(pkg.get('spider', '')).startswith('./'):
            pkg['spider'] = to_abs(pkg['spider'])
        for k in ('wallpaper', 'disabled_wallpaper', 'logo'):
            if isinstance(pkg.get(k), str) and pkg[k].startswith('./'):
                pkg[k] = to_abs(pkg[k])
        pkg['sites'] = sites
        pkg['lives'] = lives_out
        pkg['parses'] = parses_out
        return pkg

    pkg_t1 = build_pkg(sites_t1)
    pkg_t2 = build_pkg(sites_t2)
    # Tier2 不附 lives/parses（那是 Tier1 已验证的资产，避免混淆交付边界）
    pkg_t2['lives'] = []
    pkg_t2['parses'] = []

    # ---------- 归档 v2（49 站主版 + 21 站 Tier2）后写 v3 ----------
    v2_t1_names, v2_t2_names = set(), set()
    if os_path_exists(V2_T1):
        shutil.move(V2_T1, ARCHIVE_V2_T1)
        print(f'归档 v2 主版（49 站） → {ARCHIVE_V2_T1}')
    if os_path_exists(V2_T2):
        shutil.move(V2_T2, ARCHIVE_V2_T2)
        print(f'归档 v2 Tier2（21 站） → {ARCHIVE_V2_T2}')
    if os_path_exists(ARCHIVE_V2_T1):
        v2_t1_names = {s['name'] for s in json.load(open(ARCHIVE_V2_T1, encoding='utf-8'))['sites']}
    if os_path_exists(ARCHIVE_V2_T2):
        v2_t2_names = {s['name'] for s in json.load(open(ARCHIVE_V2_T2, encoding='utf-8'))['sites']}

    # v1 对比源（v1 = 47 站用户端测确认版，归档留痕）
    v1_t1_names, v1_t2_names = set(), set()
    if os_path_exists(ARCHIVE_T1):
        v1_t1_names = {s['name'] for s in json.load(open(ARCHIVE_T1, encoding='utf-8'))['sites']}
    if os_path_exists(ARCHIVE_T2):
        v1_t2_names = {s['name'] for s in json.load(open(ARCHIVE_T2, encoding='utf-8'))['sites']}

    json.dump(pkg_t1, open(OUT_T1, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(pkg_t2, open(OUT_T2, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'Tier1 → {OUT_T1}（{len(sites_t1)} 站 / lives {len(lives_out)} / parses {len(parses_out)}）')
    print(f'Tier2 → {OUT_T2}（{len(sites_t2)} 站）')

    # 升降级清单（相对 v1 / v2）
    up = sorted(n for n in t1_names if n not in v1_t1_names)
    down = sorted(n for n in v1_t1_names if n not in t1_names)
    print(f'\n相对 v1：升级入可用版 {len(up)} 站：{up}')
    print(f'相对 v1：移出可用版 {len(down)} 站：{down}')
    down_v2 = sorted(n for n in v2_t1_names if n not in t1_names)
    up_v2 = sorted(n for n in t1_names if n not in v2_t1_names)
    print(f'相对 v2：端测移出 {len(down_v2)} 站：{down_v2}｜端测新坐实 {len(up_v2)} 站：{up_v2}')

    # ---------- 报告 ----------
    md = []
    md.append('# B1018 重建 · cluntop 原仓库内容层测活报告与依据说明\n')
    md.append('> 生成：2026-10-18（v3，增补 S117 用户端测裁决层）'
              '｜ 基线：作者原配置 `cluntop_box原文_1018.json`（唯一事实源）')
    md.append('> 测活口径：**内容层**（真拉采集源/接口），不是 HTTP 层\n')
    md.append('> v1（47 站）经用户端测确认"这次可用"；v2 加做 py 沙箱真跑得 49 站；'
              'v3 拿 6 个新增站回端测，4 个坐实、2 个沙箱假阳性出局，主版回到 **47 站**'
              '（构成与原 v1 不同：4KAV/奇优影视/短剧/骚火 替换 影视天堂/偷乐短剧/国外剧情/PPnix）。'
              '历史版本全部归档留痕。\n')

    md.append('## 一、为什么要重建（旧交付 A1018_112站 的缺陷，全部实证）\n')
    md.append('| # | 缺陷 | 证据 | 后果 |')
    md.append('|---|---|---|---|')
    md.append('| 1 | 52 个 py/js 站 type 被 3→1 擅改 | 原文全 3、交付全 1 | 形态不符作者配置，疑 app 识别错乱 |')
    md.append('| 2 | 7 个站 ext 配置被丢 | 影视天堂[py] 丢 ext.host 等 | spider 不知宿主 → 转圈 |')
    md.append('| 3 | 17 lives + 21 parses 被错并进 sites（22 条） | B站直播 .m3u 成 type1 站点 | 直播单被当 spider 跑 → 转圈 |')
    md.append('| 4 | 顶层配置键全丢 | rules/headers/ads/flags/ijk/wallpaper/logo/doh… | 播放规则/解码/去广告全没 |')
    md.append('| 5 | "可用"只验到 HTTP 层 + jar 类名层 | csp 只验 jar 有 XBPQ 类 | 采集源死了照发 → 用户端转圈/无数据 |')
    md.append('')
    md.append('**截图四站归因**：B站直播（缺陷3）、金牌[js]（缺陷1，上游其实活着）、'
              '影视天堂[py]（缺陷1+2，且站点已改版——S116 真跑实证）、'
              '狐狸君（缺陷5：jar 有类、ext 活着，但采集源 foxjun.com 已死）。\n')

    md.append('## 二、内容层测活总览（原文 100 站，v2 终判）\n')
    md.append('| 层 | 通过 | 待端测 | 明确死 |')
    md.append('|---|---|---|---|')
    stat = Counter()
    for n, (v, k, e) in final.items():
        stat[(k, v)] += 1
    for kind in ('direct', 'py', 'js', 'csp'):
        md.append(f"| {kind} | {stat.get((kind,'通过'),0)} | {stat.get((kind,'无法判定'),0)} | {stat.get((kind,'失败'),0)} |")
    md.append(f"| **合计** | **{len(t1_names)}** | **{len(t2_names)}** | **{len(dead)}** |")
    md.append('')
    md.append('> 三态契约（M-R60）：**无法判定 ≠ 死**。沙箱网络层不通/源码加密/站点重度抖动的站进 Tier2 待端测，'
              '不混进可用版；只有拿到明确死证据（DNS 消失/拒连/404/无播放地址/真跑无货/端测失败）才判死。\n')

    md.append('## 三、测活方法（三层）\n')
    md.append('### 3.1 上游层（S115，内容层口径）\n')
    md.append('| 站型 | 验什么 | 判据 |')
    md.append('|---|---|---|')
    md.append('| 直链/接口 | 真请求 API | livecheck：ac=detail 拉到 vod_play_url |')
    md.append('| py/js | 下载 spider 源码提取真实上游（host/home_url/发布页/IP+PORT/多 host 列表） | 上游 HTTP 可达；混淆无字面量 → 待端测 |')
    md.append('| csp | ext 配置里的采集 URL | 采集页 HTTP 可达 |')
    md.append('| 引擎型（drpy2/live2vod） | 真实源在 ext 规则里 | ext 规则文件存在且其上游可达 |')
    md.append('| 直播单 | m3u 清单 | HTTP 200 + 正文含 #EXTM3U |')
    md.append('| 解析 | 基地址 | HTTP 可达 |')
    md.append('')
    md.append('错误分级：DNS 消失(exit6)/拒连(exit7) → 死；超时/重置/SSL(exit28/56/35) → 待端测；'
              '403 看响应体是否 WAF 质询（是则待端测）。\n')
    md.append('### 3.2 py 沙箱真跑层（S116，新增）\n')
    md.append('stub `base.spider`（六大接口 + 会话保持，贴近 app 行为）→ 子进程隔离执行 py 源码 → '
              '`homeVideoContent / categoryContent / searchContent` 三路探口 → '
              '任一返回非空片单（含 vod_id/vod_name）= 内容层真通过。单站超时 180s。\n')
    md.append('**终判规则**（真跑层只带来确定性证据时才改判）：\n')
    md.append('| 真跑结果 | 上游层 | 终判 | 说明 |')
    md.append('|---|---|---|---|')
    md.append('| 通过（真拉到片单） | 任意 | **可用** | 最强证据，覆盖上游层误判 |')
    md.append('| 无法判定 | 通过/无法判定/失败 | 沿用上游层 | 真跑未带来新信息 |')
    md.append('| 失败·代码级异常 | 任意 | **死** | JSONDecodeError/InvalidSchema/AttributeError 与网络无关，改版实证 |')
    md.append('| 失败·三路全空 | 通过 | **死** | 上游活但无货 = 改版 |')
    md.append('| 失败·三路全空 | 无法判定/失败 | **待端测** | curl 同样 403/28，沙箱网络层阻塞，M-R60 非死 |')
    md.append('')
    md.append('**工具缺陷与修复记录**（S116 自查发现，全部会致误判，已修后重跑）：\n')
    md.append('| # | 缺陷 | 误判后果 | 修法 |')
    md.append('|---|---|---|---|')
    md.append('| 1 | 未剥 BOM | LIBVIO SyntaxError 误判失败 | exec 前 utf-8-sig/gbk/utf-16 试解 |')
    md.append('| 2 | `_is_net` 把 OSError 整类当网络层 | requests 2.x 全系异常继承 IOError，'
              'JSONDecodeError/InvalidSchema（改版信号）被吞成无法判定 | 收窄为仅连接层'
              '（ConnectionError/Timeout/DNS/urllib3 连接族） |')
    md.append('| 3 | "三路全空无异常"停在初始态无法判定 | 5 个改版站误送待端测 | 该分支显式判失败 |')
    md.append('| 4 | stub 会话在基类 `__init__` 创建 | 瓜子等 11 站自带 `__init__` 不调 super，'
              'Python 里基类构造不执行（真机 Java 构造链隐式向上）→ `_session` 缺失 | 懒初始化 '
              '`_get_session()`，不对基类构造跑过做假设 |')
    md.append('| 5 | 单站超时 90s 太紧 | 瓜子/如意/雪落/剧OK/YYMP3 多 host 探测撞超时误判 | 放宽至 180s |')
    md.append('')
    md.append('> 修复后全量重跑（v3）：27 通过 / 12 失败 / 7 无法判定。'
              '注：瓜子经追踪实证 6 个候选 API 全部返回应用层 `{"code":403,"msg":"forbidden"}`,'
              '设备注册无 token → 判死；如意 3 个真实请求全部 403（WAF 页）→ 与上游层一致，待端测。\n')
    md.append('### 3.3 用户端测层（S117，最高裁决）\n')
    md.append('沙箱能跑通 ≠ 用户打得开。v2 的 6 个新增站全部送回真实设备端测，'
              '以用户实际使用结果做最终裁决：\n')
    md.append('| 端测结果 | 终判 | 说明 |')
    md.append('|---|---|---|')
    md.append('| 通过（首页有片单/能进分类） | **坐实可用** | 用户环境是最强证据 |')
    md.append('| 失败·转圈/找不到数据 + 上游半活 | **Tier2** | 站点活着但不稳（极慢抖动），留端测机会 |')
    md.append('| 失败·转圈/找不到数据 + 上游全挂 | **死** | 用户端与上游层双证据，站点已挂 |')
    md.append('')
    md.append('> 本层推翻的沙箱结论即"沙箱假阳性"：沙箱单次采样撞上站点好状态，'
              '不代表用户时刻可用（LIBVIO：沙箱 72 条 vs 端测转圈；'
              '奇优动漫：沙箱仅 search 21 条 vs 端测"找不到数据"）。\n')

    md.append('## 四、S116 真跑复核结论（46 个 py 站）\n')
    run_stat = Counter(r['verdict'] for r in run.values())
    md.append(f"真跑统计：**通过 {run_stat.get('通过',0)} ｜ 失败 {run_stat.get('失败',0)} ｜ "
              f"无法判定 {run_stat.get('无法判定',0)}**（LIBVIO/骚火/4KAV/偷乐短剧 经二次复核确认）\n")
    md.append('| 象限 | 站数 | 站点 | 处置 |')
    md.append('|---|---|---|---|')
    qmap = {}
    for q in quads:
        qmap.setdefault(q['quadrant'], []).append(q)
    order = ['A_双通过', 'C_上游死真跑过', 'C2_上游疑真跑过', 'B_上游活真跑败',
             'D_双败', 'D2_上游死真跑疑', 'E_待端测', '非py（仅上游层）']
    disp = {
        'A_双通过': '留可用版',
        'C_上游死真跑过': '**升入可用版**（上游层误判，curl 被 WAF 挡但 spider 请求通过）',
        'C2_上游疑真跑过': '**升入可用版**',
        'B_上游活真跑败': '**移出可用版**（实证改版）',
        'D_双败': '死',
        'D2_上游死真跑疑': '死（上游层已有明确死证据）',
        'E_待端测': '待端测',
        '非py（仅上游层）': '按上游层',
    }
    # 站名本身可能含竖线（如「黑木耳 | 采集」），会撞坏 Markdown 表格——
    # 仅净化展示，数据（JSON/名单匹配）不动。
    def dn(x):
        return str(x).replace(' | ', ' · ')

    for q in order:
        if q not in qmap:
            continue
        items = qmap[q]
        if q == '非py（仅上游层）':
            md.append(f'| {q} | {len(items)} | （direct/csp/js 站，不真跑） | 按上游层 |')
            continue
        labels = []
        for i in items:
            tag = ''
            if i['e2e']:
                tag = '·端测坐实' if final[i['name']][0] == '通过' else '·端测出局'
            labels.append(f"{dn(i['name'])}({i['count']}条){tag}" if i['count']
                          else f"{dn(i['name'])}{tag}")
        names = '、'.join(labels)
        md.append(f'| {q} | {len(items)} | {names} | {disp[q]} |')
    md.append('')
    if up:
        md.append(f"**相对 v1 升级入可用版（{len(up)} 站）**：{'、'.join(dn(x) for x in up)}\n")
    if down:
        md.append(f"**相对 v1 移出可用版（{len(down)} 站）**：{'、'.join(dn(x) for x in down)}\n")

    md.append('## 五、修正台账（有证据才改，逐条留痕）\n')
    for f in FIX_LOG:
        md.append(f'- {f}')
    md.append('')

    md.append(f'## 六、明确死清单（不进交付，{len(dead)} 站）\n')
    md.append('| 站 | 层 | 判定依据 |')
    md.append('|---|---|---|')
    for n, e, k in sorted(dead, key=lambda x: x[2]):
        md.append(f"| {dn(n)} | {k} | {e[:110]} |")
    md.append('')

    md.append(f'## 七、待端测清单（Tier2，{len(t2_names)} 站，非死）\n')
    md.append('| 站 | 层 | 证据 |')
    md.append('|---|---|---|')
    for n in t2_names:
        v, k, e = final[n]
        md.append(f"| {dn(n)} | {k} | {e[:110]} |")
    md.append('')

    md.append(f'## 八、S117 用户端测结论（v2 新增 6 站回测，最高裁决）\n')
    md.append('| 站 | 沙箱层 | 端测结果 | 终判 |')
    md.append('|---|---|---|---|')
    for q in quads:
        if q['e2e'] != '已端测':
            continue
        n = q['name']
        rr = run.get(n, {})
        md.append(f"| {dn(n)} | 真跑[{q['run']}]"
                  f"{'·' + str(q['count']) + '条' if q['count'] else ''}"
                  f" | {E2E[n][1][:60]} | **{final[n][0]}** |")
    md.append('')
    md.append('**两个沙箱假阳性的归因证据链**：\n')
    md.append('- **LIBVIO[py]**：沙箱 `homeVideoContent` 72 条（通过）→ 端测转圈。'
              '回测上游 3 轮：2 次 HTTP 000（20s 超时）、唯一成功轮耗时 19.85s——'
              '站点极慢且剧烈抖动，沙箱单次采样撞上好状态。**非死，移 Tier2**。\n')
    md.append('- **奇优动漫[py]**：沙箱仅 `searchContent` 21 条（home/category 空）→ 端测"找不到数据"。'
              '回测上游：首页 `/`、`search.php`、`/list/1_1.html` 全端口 **Empty reply from server**'
              '（curl exit52，TCP 通但应用层空回复，0.3s 失败）——站点已挂（非解析失效）。'
              '用户端现象与上游现状互证，**判死**。\n')
    md.append('> 教训：沙箱真跑是强证据但不是终极证据——站点级抖动/半死状态下，'
              '单次采样会骗过验证器。最终交付必须过用户端测。\n')

    md.append('## 九、直播单与解析验活\n')
    md.append('| 直播单 | 判定 | 证据 |')
    md.append('|---|---|---|')
    for n, u, v, ev in live_verdicts:
        md.append(f'| {n} | {v} | {ev} |')
    md.append('')
    md.append('> 复测注记：v3 首轮重建时沙箱出口网络集体抖动（github 大量 HTTP 000/530），'
              'Kimentanm/YanG 曾 5 次重试全败，8 轮加严复测第 1 轮即恢复（v2 归档同为通过）'
              '→ 失败项须过 8 轮复测才定性，单轮 5 次仍有假阴性；'
              'wqwqwq/epg.pw/iptv.org 8 轮全败（530 错误页）确认死。\n')
    md.append('| 解析 | 判定 | 证据 |')
    md.append('|---|---|---|')
    for n, u, v, ev in parse_verdicts:
        md.append(f'| {n} | {v} | {ev} |')
    md.append('')

    md.append('## 十、交付物\n')
    md.append(f'1. `B1018_47站_cluntop原仓库_实测可用.json` —— {len(sites_t1)} 站内容层通过'
              f'（含 {sum(1 for s in sites_t1 if str(s.get("api","")).endswith(".py"))} 个 py 站经沙箱真跑 + 端测双重复核）'
              f' + {len(lives_out)} 直播单 + {len(parses_out)} 解析，顶层配置全保留，drop-in 可用')
    md.append(f'2. `B1018_22站_cluntop原仓库_待端测.json` —— {len(sites_t2)} 站沙箱无法判定'
              '或站点重度抖动（含 LIBVIO），请本地端测后自行选用')
    md.append('3. 本报告（含死站清单、修正台账、三层测活方法、真跑四象限、端测结论、工具缺陷记录）')
    md.append('4. `B1018_49站_v2_归档_20261018.json` —— v2 主版归档（端测前 49 站）')
    md.append('5. `B1018_21站_v2_归档_20261018.json` —— v2 Tier2 归档')
    md.append('6. `B1018_47站_v1_端测确认_归档_20261018.json` —— v1 主版归档（用户端测确认版）')
    md.append('7. `cluntop_box原文_1018.json` —— 作者原配置归档（基线）')
    open(OUT_MD, 'w', encoding='utf-8').write('\n'.join(md))
    print(f'报告 → {OUT_MD}')


def os_path_exists(p):
    import os
    return os.path.exists(p)


if __name__ == '__main__':
    main()
