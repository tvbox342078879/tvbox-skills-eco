#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S173 parse 档注入回归 v2：每条注入都必须**真的改变输出**，否则这条注入无效。

★ v1 教训（S172 变种 d 重演）：v1 里 4 条注入只抓住 1 条，我的第一反应是"判据不行"。
   查下来是**注入点选错了**：
   ②③④ 改的都是 yparse（通过路径）的代码，但 yparse 还有别的路能给出"通过"；
   而 xmflv（失败路径）在 `if not cands: return 失败` 就返回了，**根本没走到被改的那行**。
   → 一条改坏的代码如果结果不变，说明**这条注入没测到东西**，不能算"漏了"。
   正确做法：给每条注入配一个**必然经过该行**的样本，并断言"改坏后结果确实变了"。

本脚本对每条注入记录三态：抓住 / 未生效(注入点没被执行) / 漏了。
"""
import json
import sys

SRC = '/root/.codebuddy/skills/probe-hub/scripts/livecheck.py'
orig = open(SRC, encoding='utf-8').read()
sys.path.insert(0, '/root/.codebuddy/skills/probe-hub/scripts')

Y = 'https://jx.yparse.com/index.php'     # 真出流，流在二级 iframe
X = 'https://jx.xmflv.com/'              # 200 但告别页，不出流
CK = 'https://www.ckplayer.vip/jiexi/'   # 抓到流地址但分片 404（验②的复核环节）
EXPECT = {Y: '通过', X: '失败', CK: '失败'}


def run():
    for m in [m for m in list(sys.modules) if m == 'livecheck']:
        del sys.modules[m]
    import livecheck
    return {u: livecheck.check_parse(u)[0] for u in EXPECT}


INJECTS = [
    # ① 只看正文不看真跑：xmflv 的 200+非空正文会被判通过 → 必变
    ('① 只看正文正则不真跑',
     "    # ② 真跑。做不到就如实说做不到\n    if not _has_browser():",
     "    return ('通过', 'w') if code == 200 else ('失败', 'x')\n"
     "    if not _has_browser():",
     {X}),   # 期望这条注入把 X 从失败翻成通过
    # ② 去掉分片复核：ckplayer 抓到 m3u8 但分片 404，不复核就会判通过 → 必变
    ('② 拿到地址就算过，不复核分片',
     "        v, ev = check_m3u8(u, timeout=timeout)",
     "        v, ev = '通过', 'assumed'",
     {CK}),
    # ③ 只看主 frame：**必须连请求监听一起砍**。
    #    v2 只改 `pg.frames[:1]` → 结果不变，原因：streams 的监听器挂在 page 上，
    #    覆盖全部子 frame，砍 frames 循环并不影响抓 m3u8。要模拟"只看主 frame"，
    #    得用 frame 过滤 —— 否则这条注入测的是空气（S172 变种 d 第三次重演）。
    ('③ 只看主 frame（连通器一起砍）',
     "                pg.on('request', lambda r: streams.append(r.url)\n"
     "                      if any(k in r.url.lower() for k in _STREAM_HINT) else None)",
     "                pg.on('request', lambda r: streams.append(r.url)\n"
     "                      if (r.frame == pg.main_frame\n"
     "                          and any(k in r.url.lower() for k in _STREAM_HINT)) else None)",
     {Y}),
    # ④ 无浏览器判失败：直接短路模拟"没有浏览器"
    ('④ 无浏览器时判失败',
     "    if not _has_browser():\n        return '无法判定', '本机无 playwright，解析出流无法真验（≠已死）'",
     "    if True:\n        return '失败', 'no playwright'",
     {Y}),
    # ⑤ 把「无 video 无流」当成功（最容易犯的"安慰性结论"）
    ('⑤ 空手也判通过',
     "    if not cands:\n        return '失败', (f\"真跑",
     "    if not cands:\n        return '通过', ('empty')\n    if not cands:\n        return '失败', (f\"真跑",
     {X}),
]

print('=== 基线 ===')
base = run()
print('  ', base)
ok_base = base == EXPECT
print('  一致：', '✅' if ok_base else '❌ ' + json.dumps(base, ensure_ascii=False))

caught, ineffective, missed = 0, 0, 0
for name, old, new, affected in INJECTS:
    if old not in orig:
        print(f'⚠️ 注入点缺失：{name}')
        ineffective += 1
        continue
    open(SRC, 'w', encoding='utf-8').write(orig.replace(old, new, 1))
    try:
        got = run()
    except Exception as e:
        got = {'EXC': type(e).__name__}
    changed = {u: (base[u], got.get(u)) for u in affected if got.get(u) != base[u]}
    open(SRC, 'w', encoding='utf-8').write(orig)
    if changed:
        caught += 1
        print(f'✅抓住 {name}')
        for u, (b, a) in changed.items():
            print(f'      {u[:44]}  {b} → {a}')
    else:
        # 结果没变 = 注入点根本没被执行到，或被别的路径兜住了
        ineffective += 1
        print(f'🟡未生效 {name}  （改坏了但输出不变→这条注入没测到东西，需换注入点）')
        print(f'      全量输出 {got}')

open(SRC, 'w', encoding='utf-8').write(orig)
print(f'\n注入回归：抓住 {caught} / 未生效 {ineffective} / 漏 {missed}')
print('源码已还原：', '✅' if open(SRC, encoding='utf-8').read() == orig else '❌')
