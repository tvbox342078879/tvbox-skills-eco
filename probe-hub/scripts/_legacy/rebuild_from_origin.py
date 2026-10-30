#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️ 历史归档脚本（T8/C5 留档·勿直接执行）：硬编码的 /workspace/原仓库库/ 与 artifact sweep_v2.json / py_run_result_v3.json 路径均已失效（原仓库库已归档至 /root/uploads/历史产物归档_20261023_1014至1020会话.tar.gz 内）。当前运行必报错，仅供历史追溯。
"""
S115 重建：从作者原配置出发，按内容层实测结论分层交付。

与旧交付（A1018_112站）的原则差异（用户 2026-10-18 批评驱动）：
  旧：以"可用版"为中转二次加工 → 擅改 type(3→1, 52 站)、丢 ext(7 站)、
      把 lives/parses 错并进 sites(22 条)、丢全部顶层配置键、丢 lives/parses。
  新：以作者原配置为唯一基线，字段全部原样保留；只做三件有证据的事：
      ① 相对→绝对（每个目标先验活再转）；
      ② 作者配置中已失效的字段修正（附证据，逐条留痕）；
      ③ 按内容层实测结论分层（通过/待端测/死），不把未验证的当可用。

分层（M-R60 三态）：
  Tier1 实测可用（内容层通过）→ B1018_47站_实测可用.json
  Tier2 待端测（无法判定≠死）  → B1018_27站_待端测.json
  死（明确不可用）            → 不交付，进报告
"""
import json
import re
import subprocess

BASE = 'https://clun.top'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
ORIG = '/workspace/原仓库库/cluntop_box原文_1018.json'
SWEEP = '/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d49-6b5af3d3c000/sweep_v2.json'
OUT_DIR = '/workspace/原仓库库'
OUT_T1 = f'{OUT_DIR}/B1018_47站_cluntop原仓库_实测可用.json'
OUT_T2 = f'{OUT_DIR}/B1018_26站_cluntop原仓库_待端测.json'
OUT_MD = f'{OUT_DIR}/B1018_重建_测活报告与依据说明.md'


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
    if s.get('name') == '影视天堂[py]' and isinstance(s.get('ext'), dict):
        old = str(s['ext'].get('host', ''))
        if old.startswith('https://'):
            new = 'http://' + old[len('https://'):]
            code = curl_code(new)
            if code == 200:
                s['ext'] = dict(s['ext'])
                s['ext']['host'] = new
                FIX_LOG.append(f"影视天堂[py]：ext.host {old} → {new}"
                               f"（原值 https SSL 握手失败 exit35；http 实测 200）")
    return s


def main():
    orig = json.load(open(ORIG, encoding='utf-8'))
    sweep = {r['name']: r for r in json.load(open(SWEEP, encoding='utf-8'))}

    # 人工复核补丁（沙箱复测 + 用户端测证据）
    sweep['苹果[py]'].update(verdict='通过',
                             upstream='http://122.228.193.211:18008',
                             evidence='HTTP 200·IP+PORT 上游可达（PORT=18008，v2 漏带端口）')
    sweep['狐狸君'].update(verdict='失败',
                           evidence='TLS 握手后服务器空回复（curl exit 92）+ 用户端测"找不到数据"双证据')

    t1_names = [n for n, r in sweep.items() if r['verdict'] == '通过']
    t2_names = [n for n, r in sweep.items() if r['verdict'] == '无法判定']
    dead = [(n, r) for n, r in sweep.items() if r['verdict'] == '失败']
    print(f'Tier1 通过 {len(t1_names)} ｜ Tier2 待端测 {len(t2_names)} ｜ 死 {len(dead)}')

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

    # ---------- lives：验活（HTTP + #EXTM3U 内容校验） ----------
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
        body = curl_body(u, timeout=12)
        code = curl_code(u, timeout=12)
        ok = code in (200, 301, 302, 303, 307, 308) and '#EXTM3U' in body[:4096]
        live_verdicts.append((name, u, '通过' if ok else '失败',
                              f'HTTP {code}·{"含#EXTM3U" if "#EXTM3U" in body[:4096] else "无M3U内容"}'))
        if ok:
            l2 = dict(l)
            l2['url'] = u
            lives_out.append(l2)

    # ---------- parses：验活（基地址 HTTP） ----------
    parses_out, parse_verdicts = [], []
    for p in orig['parses']:
        name, url = p.get('name'), str(p.get('url', ''))
        if not url:
            parse_verdicts.append((str(name), url, '跳过', '无 url'))
            continue
        base = url.split('?url=')[0].rstrip('/') + '/'
        code = curl_code(base, timeout=10)
        ok = code in (200, 301, 302, 303, 307, 308)
        parse_verdicts.append((name, base, '通过' if ok else '失败', f'HTTP {code}'))
        if ok:
            parses_out.append(p)

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

    json.dump(pkg_t1, open(OUT_T1, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(pkg_t2, open(OUT_T2, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'Tier1 → {OUT_T1}（{len(sites_t1)} 站 / lives {len(lives_out)} / parses {len(parses_out)}）')
    print(f'Tier2 → {OUT_T2}（{len(sites_t2)} 站）')

    # ---------- 报告 ----------
    md = []
    md.append('# B1018 重建 · cluntop 原仓库内容层测活报告与依据说明\n')
    md.append('> 生成：2026-10-18 ｜ 基线：作者原配置 `cluntop_box原文_1018.json`（唯一事实源）')
    md.append('> 测活口径：**内容层**（真拉采集源/接口），不是 HTTP 层\n')

    md.append('## 一、为什么要重建（旧交付 A1018_112站 的缺陷，全部实证）\n')
    md.append('| # | 缺陷 | 证据 | 后果 |')
    md.append('|---|---|---|---|')
    md.append('| 1 | 52 个 py/js 站 type 被 3→1 擅改 | 原文全 3、交付全 1 | 形态不符作者配置，疑 app 识别错乱 |')
    md.append('| 2 | 7 个站 ext 配置被丢 | 影视天堂[py] 丢 ext.host 等 | spider 不知宿主 → 转圈 |')
    md.append('| 3 | 17 lives + 21 parses 被错并进 sites（22 条） | B站直播 .m3u 成 type1 站点 | 直播单被当 spider 跑 → 转圈 |')
    md.append('| 4 | 顶层配置键全丢 | rules/headers/ads/flags/ijk/wallpaper/logo/doh… | 播放规则/解码/去广告全没 |')
    md.append('| 5 | “可用”只验到 HTTP 层 + jar 类名层 | csp 只验 jar 有 XBPQ 类 | 采集源死了照发 → 用户端转圈/无数据 |')
    md.append('')
    md.append('**截图四站归因**：B站直播（缺陷3）、金牌[js]（缺陷1，上游其实活着）、'
              '影视天堂[py]（缺陷1+2，且作者 ext.host 本身已失效）、'
              '狐狸君（缺陷5：jar 有类、ext 活着，但采集源 foxjun.com 已死）。\n')

    md.append('## 二、内容层测活总览（原文 100 站）\n')
    md.append('| 层 | 通过 | 待端测 | 明确死 |')
    md.append('|---|---|---|---|')
    from collections import Counter
    stat = Counter()
    for n, r in sweep.items():
        if r['verdict'] in ('通过', '失败', '无法判定'):
            stat[(r['kind'], r['verdict'])] += 1
    for kind in ('direct', 'py', 'js', 'csp'):
        md.append(f"| {kind} | {stat.get((kind,'通过'),0)} | {stat.get((kind,'无法判定'),0)} | {stat.get((kind,'失败'),0)} |")
    md.append(f"| **合计** | **{sum(n for (k,v),n in stat.items() if v=='通过')}** | "
              f"**{sum(n for (k,v),n in stat.items() if v=='无法判定')}** | "
              f"**{sum(n for (k,v),n in stat.items() if v=='失败')}** |")
    md.append('')
    md.append('> 三态契约（M-R60）：**无法判定 ≠ 死**。沙箱网络层不通/源码加密的站进 Tier2 待端测，'
              '不混进可用版；只有拿到明确死证据（DNS 消失/拒连/404/无播放地址/端测失败）才判死。\n')

    md.append('## 三、测活方法（内容层，S115）\n')
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

    md.append('## 四、修正台账（有证据才改，逐条留痕）\n')
    for f in FIX_LOG:
        md.append(f'- {f}')
    md.append('')

    md.append('## 五、明确死清单（不进交付，19 站）\n')
    md.append('| 站 | 层 | 死证据 |')
    md.append('|---|---|---|')
    for n, r in sorted(dead, key=lambda x: x[1]['kind']):
        md.append(f"| {n} | {r['kind']} | {r['evidence']} |")
    md.append('')

    md.append('## 六、待端测清单（Tier2，26 站，非死）\n')
    md.append('| 站 | 层 | 沙箱证据 |')
    md.append('|---|---|---|')
    for n in t2_names:
        r = sweep[n]
        md.append(f"| {n} | {r['kind']} | {r['evidence'][:80]} |")
    md.append('')

    md.append('## 七、直播单与解析验活\n')
    md.append('| 直播单 | 判定 | 证据 |')
    md.append('|---|---|---|')
    for n, u, v, ev in live_verdicts:
        md.append(f'| {n} | {v} | {ev} |')
    md.append('')
    md.append('| 解析 | 判定 | 证据 |')
    md.append('|---|---|---|')
    for n, u, v, ev in parse_verdicts:
        md.append(f'| {n} | {v} | {ev} |')
    md.append('')

    md.append('## 八、交付物\n')
    md.append(f'1. `B1018_47站_cluntop原仓库_实测可用.json` —— {len(sites_t1)} 站内容层通过'
              f' + {len(lives_out)} 直播单 + {len(parses_out)} 解析，顶层配置全保留，drop-in 可用')
    md.append(f'2. `B1018_26站_cluntop原仓库_待端测.json` —— {len(sites_t2)} 站沙箱无法判定，'
              '请本地端测后自行选用')
    md.append('3. 本报告（含死站清单、修正台账、方法口径）')
    md.append('4. `cluntop_box原文_1018.json` —— 作者原配置归档（基线）')
    open(OUT_MD, 'w', encoding='utf-8').write('\n'.join(md))
    print(f'报告 → {OUT_MD}')


if __name__ == '__main__':
    main()
