#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 接口流水线可执行骨架（S068 建档：interface 从「纯文档 SOP」升为「文档+脚本」双轨）。

背景：本技能 SKILL.md §0-§10 是成熟 SOP，但此前 0 脚本——flow.json 的 11 步 cmd 几乎全 null，
每次执行都要模型手工重做「抓取/体系识别/体检/去重/测活」这些**机械且易错**的动作。
本脚本把这些步骤固化为可复跑命令，口径与 SKILL.md 严格一致（见各函数 docstring 的 § 引用）。

设计原则（呼应生态铁律）：
  · 单一事实源（M-R38）：体系判据/文件夹 id/测活判定码/去重键 都收敛为本文件顶部常量
  · 宁触发勿漏（§0.1）：触发判定宽松
  · 先拆分→再去重→后测活（§4.5.4 顺序铁律）
  · 全量测活不抽样（§5）——并发但全量
  · 失败返回结构化 None/[]，不抛（网络抖动不误报）

用法：
  python3 pipeline.py fetch   <URL> --out /tmp/raw.bin     # 抓取 + 伪装形态识别（含双UA md5）
  python3 pipeline.py health  <file.json>                   # 体检（type分布/lives/spider/相对路径）+ 体系识别
  python3 pipeline.py split   <file.json> --outdir /tmp/sp  # 分类拆分（lives/py/采集/直链/其他/parses）
  python3 pipeline.py dedup   <file.json>                   # 全量去重（四类各自 key）+ 落盘记录
  python3 pipeline.py probe   <file.json>                   # 全量测活（并发，判定码）
  python3 pipeline.py run     <URL> --workdir /tmp/tvbox_run # 一把梭：抓→体检→拆分→去重→测活
"""
import os, sys, json, re, time, subprocess, argparse, hashlib
from collections import Counter

# ---------- M-R114 统一并发入口（S110） ----------
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import resolve_workers as _resolve_workers  # noqa: E402


# ★ 生态联动（S106 补接门面）：整包坊产出的「抓取/拆分中间残留」「失败整包」AI 删不掉 → 投递清道夫；
#   出现新体系/新伪装形态（不在 A/B/C/D/🔞 内）→ 登记监察司风险面，纳入下轮扫描。
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
try:
    from core import file_to_janitor, register_monitor
except Exception:                              # 门面不可达时降级为 no-op，绝不阻断流水线
    def file_to_janitor(node_id, reason='', quiet=True): return False
    def register_monitor(risk_face, check_type, desc='', quiet=True): return False


def _eco_linked_probe():
    """自证已接门面：返回 (清道夫, 监察司) 可达性，供 inspect 联动覆盖判据与自检。"""
    return (callable(file_to_janitor), callable(register_monitor))

# ── 单一事实源（M-R38：会多处引用的枚举收敛于此，各使用点派生）─────────────
CURL = '/usr/bin/curl'                     # §2 必须绝对路径（shell 里 curl 偶发 not found）
UA = 'okhttp/3.12.0'
ALIVE_CODES = {200, 206, 301, 302, 303, 307, 308}          # §5 判定活
SPLIT_ROOT = '/root/.codebuddy/artifact'   # §2 抓取落盘目录

# §2.5.1 四体系 + 🔞 正交分类（文件夹 id 与 SKILL.md §2.5.1 表一一对应）
SYS_FOLDER = {
    'A': 'jxJHRZHyNzjY6fONPlFTe1',   # CatVod 标准系（裸类名）
    'B': 'AJJixTz2yohkUz4Pu1zAXB',   # moji Guard 系（加壳）
    'C': 'j7bD2jgogJxILWwKQv7ZMJ',   # 零 jar 纯直链系
    'D': 'pqSV3xwMuZsgBLmPQSKdGh',   # Amns 系（裸类名+Amns 后缀）
    '🔞': 'SbPKgzcnGAsQ3cKjbRIJT4',  # 成人直链系（与 A/B/C/D 正交）
}

# MAGIC：首字节真身判定（§2 伪装形态识别表）
MAGIC = [
    (b'\xff\xd8\xff', 'jpeg', 'JPEG（可能嵌套 base64，检查尾部）'),
    (b'PK\x03\x04', 'zip',  'ZIP/dex（Android jar 常见伪装）'),
    (b'\xef\xbb\xbf', 'bom', 'UTF-8 BOM 明文（用 utf-8-sig 读）'),
    (b'\x1f\x8b', 'gzip', 'GZIP'),
]


def _sh(cmd, timeout=60):
    """跑 shell 命令，失败返回 (rc, out) 不抛。"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except Exception as e:
        return -1, f'（执行失败：{e}）'


def _read_json(fp):
    """读 json，自动处理 BOM（§2 形态表：带 BOM 用 utf-8-sig）。"""
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            return json.load(open(fp, encoding=enc))
        except Exception:
            continue
    return None


# ── §2 抓取 ────────────────────────────────────────────────────────────
def write_json_verified(obj, fp, expect_n=None, label=''):
    """★ S156 招式 T-I 落地：写后回读验证（写了 ≠ 写成了）。

    为什么必须做：`json.dump(obj, open(fp,'w'))` 之后 **没有任何证据**说明写成功了。
    实测过的三种失败都是静默的：
      · 磁盘/权限问题 → dump 抛异常还好，被 try 包住就无声；
      · 写到一半被打断 → 文件存在但截断，下游 `json.load` 报错却说不清是哪一步坏的；
      · 序列化降级（set/自定义对象）→ 写了个空壳，`{}`，下游当"没数据"而不是"写坏了"。
    这三种在流水线里表现为"下一步没数据"，排查方向会被带偏到上游。

    做法：写 → 立刻回读解析 → 断言条数（expect_n 给了才断言）→ 不一致就**明说写坏了**并返回 False。
    三态：回读解析失败 = 写坏（False），不是"空文件也凑合"（M-R60）。

    返回 (ok, msg)。
    """
    try:
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return False, f'写入失败（{type(e).__name__}: {str(e)[:80]}）'
    try:
        back = json.load(open(fp, encoding='utf-8'))
    except Exception as e:
        return False, f'写后回读**解析失败**——文件已写坏（{type(e).__name__}: {str(e)[:80]}）'
    if expect_n is not None:
        # 条数口径：优先取 obj 的主列表键（sites/lives/parses），否则按顶层列表
        def _n(x):
            if isinstance(x, list):
                return len(x)
            if isinstance(x, dict):
                for k in ('sites', 'lives', 'parses'):
                    if isinstance(x.get(k), list):
                        return len(x[k])
                return len(x)
            return 1
        got = _n(back)
        if got != expect_n:
            return False, f'写后回读条数不符：写入 {expect_n} 条，回读到 {got} 条——内容被截断/覆盖'
    return True, f'{label or fp} 回读确认'


def cmd_fetch(a):
    """抓取 + 伪装形态识别（§2）。双 UA 比对 md5 判「明文直出」。"""
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    rc, _ = _sh(f'{CURL} -s -L -m 30 -A "{UA}" "{a.url}" -o "{a.out}"', timeout=40)
    if rc != 0 or not os.path.exists(a.out):
        print(f'❌ 抓取失败 rc={rc} → {a.url}')
        return 1
    size = os.path.getsize(a.out)
    head = open(a.out, 'rb').read(16)
    form = 'plain'
    desc = '明文（未命中已知魔数）'
    for magic, name, d in MAGIC:
        if head.startswith(magic):
            form, desc = name, d
            break
    # 双 UA 验证伪装（§2）
    tmp = a.out + '.ua2'
    _sh(f'{CURL} -s -L -m 30 -A "Mozilla/5.0" "{a.url}" -o "{tmp}"', timeout=40)
    same = False
    if os.path.exists(tmp):
        h1 = hashlib.md5(open(a.out, 'rb').read()).hexdigest()
        h2 = hashlib.md5(open(tmp, 'rb').read()).hexdigest()
        same = (h1 == h2)
        os.remove(tmp)
    print(f'📥 抓取完成 {a.out}')
    print(f'   大小 {size}B ｜ 首字节 {head[:8].hex()} ｜ 形态={form}（{desc}）')
    print(f'   双UA md5 一致={same} → {"明文直出（无伪装）" if same else "疑似按 UA 伪装（需换 UA 复验）"}')
    return 0


# ── §2.5 体系识别 + §3 体检 ────────────────────────────────────────────
def _detect_system(d, spider_form=None):
    """体系识别（§2.5.2 判定表·S114 改锚）。返回 (体系, 判据说明)。

    S114 锚点修正（用户洞察驱动）：体系回答的是「**线路能不能平移**」，这取决于
    **jar 挂了什么**，不取决于站点清单用没用 csp——
      - 有 spider（jar 挂了）→ 体系随 jar：标准 dex=A / 加壳=B / Amns=D / 非标准=E?
      - 无 spider 且无 csp → C（无 jar 可用，内容不依赖 jar）
      - 无 spider 但有 csp → A?（类名风格像 A，但 jar 挂载不明——配置没带 spider，
        可能采集丢失，需查仓库实录；S113b 教训：不能据配置空字段推断"作者没挂"）
    旧判据的坑：「有 jar 但配置没用 csp」的包会在 A/C 间摇摆（cluntop 同 jar 跨系实锤）；
    新判据只看 jar 在不在——**站点清单用什么那是 L3 魔改层的事，由内容标签承载**。
    spider 真身是唯一权威，但需下载解 zip 才能验；此处只做**静态可判**部分，
    未解包时由调用方按 §2.5.2 铁律标注「静态推断」并提示下钻。"""
    apis = [s.get('api', '') for s in (d.get('sites') or []) if isinstance(s.get('api'), str)]
    csp = [x for x in apis if x.startswith('csp_')]
    amns = [x for x in csp if x.endswith('Amns')]
    guard = [x for x in csp if 'Guard' in x]
    spider = (d.get('spider') or '').strip()
    if not spider:
        if csp:
            return 'A?', (f'csp 裸类名 {len(csp)} 条但配置无 spider 字段——jar 挂载不明，'
                          f'需查仓库实录（§2.5.2 铁律·S113b 教训）')
        return 'C', f'无 jar 可用（spider 空，api {len(apis)} 条全不依赖 jar）'
    if amns:
        return 'D', f'csp 带 Amns 后缀 {len(amns)} 条（裸类名+Amns）'
    if spider_form == 'zip':
        # 静态证据不足，标注待下钻（§2.5.2：只看 api 名字会判错，如 XPB 反例）
        note = 'spider 为 zip/dex 形态，需解包看 assets/*guard* 才能定 A/B'
        if guard:
            return 'B?', f'csp 带 Guard 后缀 {len(guard)} 条 + {note}'
        return 'A?', f'csp 裸类名 {len(csp)} 条 + {note}'
    if spider_form is not None:
        return 'E?', (f'spider 解包为非 zip/dex 形态（{spider_form}）——既无 guard 也无 dex 的'
                      f'非标准 jar，待人工确认（§2.5.4 新体系）')
    # spider 在但未解包（URL/相对路径）：体系随 jar，静态推断由调用方提示下钻
    if csp:
        return 'A', (f'spider 已挂 + csp 裸类名 {len(csp)} 条 → 体系随 jar'
                     f'（未解包=静态推断）')
    return 'A', (f'spider 已挂（jar 在）但 csp 0 条 → 直链主导包，体系仍随 jar'
                 f'（S114：不因配置没用 csp 就掉 C；直链主导是内容标签的事）')


def cmd_health(a):
    """体检（§3）：type 分布 / lives / spider / ext 本地助手依赖 + 体系识别（§2.5）。"""
    d = _read_json(a.file)
    if d is None:
        print(f'❌ 无法解析 json：{a.file}（非 json / 编码异常）')
        return 1
    sites = d.get('sites') or []
    lives = d.get('lives') or []
    parses = d.get('parses') or []
    spider = (d.get('spider') or '').strip()
    tdist = dict(Counter(s.get('type') for s in sites))
    # spider 真身（本地文件才测首字节；URL 需先下载）
    form = None
    if spider and not spider.startswith(('http', './', '../')):
        if os.path.exists(spider):
            form = {m[1] for m in MAGIC if open(spider, 'rb').read(8).startswith(m[0])}
            form = next(iter(form), 'plain')
    print('🔬 体检（§3）')
    print(f'   TOP KEYS: {list(d.keys())}')
    print(f'   sites {len(sites)} 条 ｜ type 分布 {tdist}')
    print(f'   lives {len(lives)} 条 ｜ parses {len(parses)} 条')
    print(f'   spider: {spider or "（无）"}')
    # §3 关键判据：type=3 占比高 → csp 包，站价值低、lives 价值高
    t3 = tdist.get(3, 0)
    if sites and t3 / len(sites) > 0.5:
        print(f'   ⚠️ type=3(csp) 占比 {t3}/{len(sites)} > 50% → csp 包，站点价值低、lives 价值高')
    # 相对路径 spider → 必探（§3）
    rel = [s.get('api', '') for s in sites if isinstance(s.get('api'), str)
           and (s['api'].startswith('./') or s['api'].startswith('../'))]
    if spider.startswith(('./', '../')) or rel:
        print(f'   ⚠️ 相对路径：spider={spider.startswith(("./", "../"))} ｜ sites 相对 {len(rel)} 条 → 必探 base 主机 200（§3）')
    # ext 本地助手依赖（§3 统计时扣除）
    local = [s.get('name', '') for s in sites
             if '127.0.0.1' in str(s.get('ext', '')) or '9978' in str(s.get('ext', ''))]
    if local:
        print(f'   ⚠️ 依赖本地助手 {len(local)} 条（127.0.0.1:9978）→ 统计时扣除（§3）')
    # §3.1 分类参数写死检查（S101·父分类问题的姊妹坑）
    # 病根：api 里写死 `?ac=list`（或 `ac=videolist` 等固定查询），TVBox 点分类时会再拼
    # `&t=<type_id>`，写死的 ac 与之并存 → 部分壳解析异常 / 分类参数失效 → 细分点开为空。
    # 检出：api 里出现 `ac=` 查询参数（非 ac=detail 的固定写法）。
    hardcoded = []
    for s in sites:
        api = s.get('api', '')
        if not isinstance(api, str):
            continue
        m = re.search(r'[?&]ac=(list|videolist|detail)\b', api)
        if m:
            hardcoded.append((s.get('name', ''), m.group(1), api))
    if hardcoded:
        print(f'   ⚠️ 分类参数写死 {len(hardcoded)} 条（§3.1）→ 点分类可能空：')
        for nm, ac, api in hardcoded[:5]:
            print(f'        · [{ac}] {nm[:16]}  {api[:64]}')
        if len(hardcoded) > 5:
            print(f'        … 另有 {len(hardcoded) - 5} 条')
        print(f'      → 建议去掉 `?ac=xxx`（让壳自行拼分类参数），或改用 `?ac=detail` 仅在拉详情时用')
    sysname, why = _detect_system(d, spider_form=form)
    print(f'   🏷  体系识别：{sysname} ｜ 判据：{why}')
    # '?' 后缀 = 待下钻/待确认，归位到基系文件夹（A?→A 系），不当新体系
    print(f'      目标文件夹 id：{SYS_FOLDER.get(sysname.rstrip("?"), "（新体系，自动建系 §2.5.4）")}')
    # §2.5.2 铁律：spider 真身是唯一权威。若未解包（本地无 jar / URL 未下载），体系结论只是「静态推断」，
    # 必须显式提示需下钻，防把 A? / A 当定论（XPB 反例：只看 api 名字会判错）
    if spider and form is None:
        print(f'      ⚠️ spider 未解包 → 体系为**静态推断**，须下载解 zip 看 assets/*guard* 才能定 A/B（§2.5.2）')
    return 0


# ── §4 拆分（四类 + lives + parses）────────────────────────────────────
# 四类判据 = 生态单一事实源（M-R38）。任何需要"站点线分类"的消费方
# （本技能 cmd_split、源枢库曹、探源司 harvest）一律复用本函数，禁各自重写。
SITE_CLASSES = ('py', '采集', '直链', '其他')


def classify_site(s):
    """站点线分类（§4.2.0 判据表）：py / 采集 / 直链 / 其他。

    判据（顺序即优先级，先命中先返回）：
      - py   ：`type==4` 或 api 以 `.py` 结尾或含 `drpy`/`hipy`
      - 采集 ：api 以 `/provide/vod` 结尾或含 `ac=videolist`/`provide/vod`（苹果CMS）
      - 直链 ：`type==0`
      - 其他 ：其余

    **单一事实源（M-R38）**：探源司 harvest 与整包坊 split 必须同一口径——
    否则同一源在探源司算「配置」、在整包坊算「采集」，下游 ingest 类目分叉。
    """
    t = s.get('type')
    api = str(s.get('api', ''))
    if t == 4 or api.endswith('.py') or 'drpy' in api or 'hipy' in api:
        return 'py'
    if api.endswith('/provide/vod') or 'ac=videolist' in api or 'provide/vod' in api:
        return '采集'
    if t == 0:
        return '直链'
    return '其他'


# 兼容旧名（本文件内既有调用点无需改）
_classify_site = classify_site


def classify_lives(item):
    """直播线分类（§1.0）：lives 下的 `type==0` 归「直播」，不做 py/采集细分。"""
    return '直播'


def classify_parses(item):
    """解析线分类（§4.3）：parses 提取，统一归「其他」。"""
    return '其他'


def classify_line(kind, item):
    """统一分类入口（跨模块唯一调用点，M-R38）。

    kind ∈ {'sites','lives','parses'} → 返回类目字符串。
    未知 kind 返回 '其他'（不抛——采集场景下未知桶应可继续而非中断）。
    """
    if kind == 'sites':
        return classify_site(item)
    if kind == 'lives':
        return classify_lives(item)
    if kind == 'parses':
        return classify_parses(item)
    return '其他'



def cmd_split(a):
    """分类拆分（§4.2/§4.1/§4.3）：sites 四类分开拆、lives 按组拆、parses 提取。
    注：sites 与 lives 都有 type=0，看归属决定（§1.0：在 lives→直播，在 sites→直链）。"""
    d = _read_json(a.file)
    if d is None:
        print(f'❌ 无法解析 json：{a.file}')
        return 1
    os.makedirs(a.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(a.file))[0]
    out = {}
    # sites 四类
    buckets = {'py': [], '采集': [], '直链': [], '其他': []}
    for s in (d.get('sites') or []):
        buckets[_classify_site(s)].append(s)
    for kind, items in buckets.items():
        if not items:
            continue
        fp = os.path.join(a.outdir, f'{kind}_{stem}_{len(items)}条.json')
        ok, msg = write_json_verified({'sites': items}, fp, len(items), f'拆分 {kind}')
        if not ok:
            print(f'   ❌ 拆分 {kind} 写坏：{msg}'); return 1
        out[kind] = fp
        print(f'   拆分 {kind:4} {len(items):4} 条 → {fp}（回读 {len(items)} 条 ✅）')
    # lives 按组拆（§4.1）
    lives = d.get('lives') or []
    if lives:
        fp = os.path.join(a.outdir, f'lives_{stem}_合集{len(lives)}条.json')
        ok, msg = write_json_verified({'lives': lives}, fp, len(lives), '拆分 lives')
        if not ok:
            print(f'   ❌ 拆分 lives 写坏：{msg}'); return 1
        out['lives'] = fp
        print(f'   拆分 lives  {len(lives):4} 条 → {fp}（回读 {len(lives)} 条 ✅）')
    # parses 提取（§4.3）
    parses = d.get('parses') or []
    if parses:
        fp = os.path.join(a.outdir, f'parses_{stem}_{len(parses)}条.json')
        ok, msg = write_json_verified({'parses': parses}, fp, len(parses), '提取 parses')
        if not ok:
            print(f'   ❌ 提取 parses 写坏：{msg}'); return 1
        out['parses'] = fp
        print(f'   提取 parses {len(parses):4} 条 → {fp}（回读 {len(parses)} 条 ✅）')
    print(f'✅ 拆分完成 → {a.outdir}（{len(out)} 类产物）')
    return 0


# ── §4.5 去重（四类各自 key）──────────────────────────────────────────
def _dedup_key(kind, item):
    """去重键（§4.5.1 判据表）。"""
    if kind == 'lives':
        return (item.get('name'), item.get('url'))
    if kind == 'parses':
        return (item.get('url'),)
    return (item.get('api'), str(item.get('ext', '')))   # 站线：api+ext（勿只用 api）


def _dedup_list(kind, items):
    seen, out = set(), []
    for it in items:
        k = _dedup_key(kind, it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def cmd_dedup(a):
    """全量去重（§4.5）：四类各自 key；落盘 dedup 记录；先拆分→再去重→后测活（§4.5.4）。"""
    d = _read_json(a.file)
    if d is None:
        print(f'❌ 无法解析 json：{a.file}')
        return 1
    stem = os.path.splitext(os.path.basename(a.file))[0]
    total_before = total_after = 0
    rec = []
    new = dict(d)
    for kind, key in (('sites', '站线'), ('lives', 'lives'), ('parses', 'parses')):
        items = d.get(kind) or []
        if not items:
            continue
        ded = _dedup_list(kind, items)
        new[kind] = ded
        total_before += len(items)
        total_after += len(ded)
        rec.append(f'{key}: {len(items)} → {len(ded)}（删 {len(items)-len(ded)}）')
        print(f'   去重 {key:6} {len(items):4} → {len(ded):4}（删 {len(items)-len(ded)}）')
    if not rec:
        print('（无可去重内容）')
        return 0
    fp = os.path.join(os.path.dirname(os.path.abspath(a.file)) or '.', f'dedup_{stem}.json')
    ok, msg = write_json_verified(new, fp, total_after, '去重结果')
    if not ok:
        print(f'❌ 去重结果写坏：{msg}'); return 1
    try:
        open(fp.replace('.json', '.txt'), 'w', encoding='utf-8').write('\n'.join(rec))
    except Exception as e:
        print(f'❌ 去重记录写失败（{type(e).__name__}: {str(e)[:80]}）'); return 1
    print(f'✅ 去重完成 合计 {total_before} → {total_after}（删 {total_before-total_after}）')
    print(f'   去重后文件：{fp}  记录：{fp.replace(".json", ".txt")}')
    return 0


# ── §5 测活（全量，不抽样）────────────────────────────────────────────
def _alive(url, timeout=6):
    rc, out = _sh(f'{CURL} -s -o /dev/null -w "%{{http_code}}" -m {timeout} -A "{UA}" -r 0-300 "{url}"',
                  timeout=timeout + 2)
    try:
        code = int(out.strip().splitlines()[-1])
    except Exception:
        code = 0
    return code in ALIVE_CODES, code


def cmd_probe(a):
    """全量测活（§5）：除站点外全量、不抽样；并发但全量。判定码 200|206|301|302|303|307|308。"""
    d = _read_json(a.file)
    if d is None:
        print(f'❌ 无法解析 json：{a.file}')
        return 1
    prods = []
    for s in (d.get('sites') or []):
        prods.append(('site', s.get('name', ''), str(s.get('api', ''))))
    for l in (d.get('lives') or []):
        prods.append(('live', l.get('name', ''), str(l.get('url', ''))))
    for p in (d.get('parses') or []):
        prods.append(('parse', p.get('name', ''), str(p.get('url', ''))))
    prods = [x for x in prods if x[2].startswith(('http://', 'https://'))]
    if not prods:
        print('（无可测活的 http(s) 条目）')
        return 0
    print(f'🔎 全量测活 {len(prods)} 条（§5 并发全量）...')
    alive, dead = [], []
    # ★ M-R114：B 档上限 6。--workers 若给了就按档位校验（超限 raise，不静默忽略）
    from concurrency import resolve_workers as _rw
    _w = _rw('B', getattr(a, 'workers', None))
    print(f'   并发 {_w}（B 档·对公网站点上限 6）')
    # ★ S163：改走统一入口（B 档已由上面 _rw('B', --workers) 校验过上限，此处交回统一入口执行）
    from concurrency import run_bounded, TIER_WEB
    _rep = run_bounded(prods, lambda x: (x, _alive(x[2])), tier=TIER_WEB, workers=_w,
                       label='pipeline-alive-probe', keep_order=True, on_error='record')
    res = [(_r if _r is not None else (x, (False, '无法判定：探测线程异常')))
           for x, _r in zip(prods, _rep.results)]
    for (kind, name, url), (ok, code) in res:
        (alive if ok else dead).append((kind, name, url, code))
    stem = os.path.splitext(os.path.basename(a.file))[0]
    fp = os.path.join(os.path.dirname(os.path.abspath(a.file)) or '.', f'alive_{stem}.txt')
    with open(fp, 'w', encoding='utf-8') as f:
        for kind, name, url, code in alive + dead:
            f.write(f'{"✅" if (kind,name,url,code) in alive else "❌"} [{kind}] {name}\t{code}\t{url}\n')
    print(f'   存活 {len(alive)} / {len(prods)}（{len(alive)*100//max(len(prods),1)}%）')
    print(f'   逐条结果：{fp}')
    return 0


# ── §2.5.2 spider 真身解包（体系识别的唯一权威）────────────────────────
def cmd_spider(a):
    """下载 spider 并解包看内部文件，定 A/B（§2.5.2）。
    口径：zip 内含 assets/*guard*.so 或 *.guard → 加壳 → B；只有 classes.dex → 标准 → A。
    输入可为 URL、本地路径，或形如 './jar/pg.jar;md5;xxx' 的配置内 spider 串（自动去 ;md5; 后缀）。"""
    src = a.src
    # 去 TVBox 的 ;md5; 后缀
    src = re.split(r';md5;', src)[0]
    # 相对路径：若给了 base，拼 base
    if src.startswith(('./', '../')) and a.base:
        src = a.base.rstrip('/') + '/' + src.lstrip('./').lstrip('../')
    tmp = os.path.join(a.workdir, 'spider.bin')
    os.makedirs(a.workdir, exist_ok=True)
    if src.startswith(('http://', 'https://')):
        rc, _ = _sh(f'{CURL} -s -L -m 30 -A "{UA}" "{src}" -o "{tmp}"', timeout=40)
        if rc != 0 or not os.path.exists(tmp):
            print(f'❌ spider 下载失败：{src}')
            return 1
    elif os.path.exists(src):
        tmp = src
    else:
        print(f'❌ spider 不存在或无法下载：{src}（相对路径需给 --base）')
        return 1
    head = open(tmp, 'rb').read(8)
    is_zip = head.startswith(b'PK\x03\x04')
    print(f'🕷  spider 真身：{tmp}  首字节 {head[:4].hex()}  {"(ZIP)" if is_zip else "(非zip)"}')
    if not is_zip:
        print('   非 zip（可能明文/其它形态）→ 无法按 dex/guard 判系，请人工确认')
        return 0
    import zipfile
    try:
        names = zipfile.ZipFile(tmp).namelist()
    except Exception as e:
        print(f'   ❌ 解 zip 失败：{e}')
        return 1
    guard = [n for n in names if (n.endswith('.so') and 'guard' in n.lower())
             or n.endswith('.guard') or 'guard' in n.lower()]
    dex = [n for n in names if n.endswith('.dex')]
    print(f'   内部文件 {len(names)} 个 ｜ classes.dex {len(dex)} ｜ guard 资源 {len(guard)}')
    if guard:
        print(f'   🏷  判定：**B（moji Guard 加壳系）** ← 证据：{guard[:3]}（§2.5.2）')
    elif dex:
        print(f'   🏷  判定：**A（CatVod 标准系）** ← 仅 classes.dex，无 guard 资源（§2.5.2）')
    else:
        print('   ⚠️ 既无 guard 也无 dex → 非标准 Android jar，人工确认（可能新体系 E，§2.5.4）')
    return 0


# ── 一把梭（§1 触发即开工）────────────────────────────────────────────
def cmd_run(a):
    """一把梭：抓→体检→拆分→去重→测活（§1 固定顺序）。
    不替代体系识别的 spider 下钻（那步需人工/后续工具），只跑机械段。"""
    os.makedirs(a.workdir, exist_ok=True)
    raw = os.path.join(a.workdir, 'raw.bin')
    print(f'=== ① 抓取 {a.url} ===')
    if cmd_fetch(argparse.Namespace(url=a.url, out=raw)) != 0:
        return 1
    print(f'\n=== ② 体检 + 体系识别 ===')
    cmd_health(argparse.Namespace(file=raw))
    print(f'\n=== ③ 分类拆分 ===')
    sp = os.path.join(a.workdir, 'split')
    cmd_split(argparse.Namespace(file=raw, outdir=sp))
    print(f'\n=== ④ 全量去重 ===')
    cmd_dedup(argparse.Namespace(file=raw))
    print(f'\n=== ⑤ 全量测活 ===')
    cmd_probe(argparse.Namespace(file=raw, workers=a.workers))
    print(f'\n✅ 一把梭完成 → {a.workdir}')
    print('   下一步（需人工/下钻）：spider 真身解包定 A/B → 命名 <体系><MMDD>_<站数>站 → 入库 → 登记 → 复盘(§8)')
    # ★ 生态联动（S106）：整包坊跑完留下 raw.bin/split 中间产物，后续入库若不再需要即为
    #   「AI 删不掉的残留」→ 投递清道夫收件箱；同时登记「新体系/新伪装形态」观测风险面。
    try:
        # S156 修：原在这里投递「整包坊中间产物目录（raw.bin + split/）」——那是**本地目录**，
        #   不是云端节点，清道夫只收云端节点、移动必然失败，只会产出一条移不动的假账。
        #   本地中间产物由本地清理负责（或保留供排查），此处不投递。
        register_monitor('整包坊新体系形态', 'naming_compliance',
                         '出现 A/B/C/D/🔞 之外的 json 体系或未知伪装壳时，监测册接手跟踪')
    except Exception as e:
        print(f'  ⚠️ 生态门面联动跳过（{type(e).__name__}）：流水线结果不受影响')
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog='pipeline.py', description='TVBox 接口流水线骨架（S068）')
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch'); f.add_argument('url'); f.add_argument('--out', default=f'{SPLIT_ROOT}/raw.bin')
    h = sub.add_parser('health'); h.add_argument('file')
    s = sub.add_parser('split'); s.add_argument('file'); s.add_argument('--outdir', default=f'{SPLIT_ROOT}/split')
    d = sub.add_parser('dedup'); d.add_argument('file')
    p = sub.add_parser('probe'); p.add_argument('file'); p.add_argument('--workers', type=int, default=6)
    sp = sub.add_parser('spider'); sp.add_argument('src')
    sp.add_argument('--base', default=''); sp.add_argument('--workdir', default=f'{SPLIT_ROOT}/tvbox_run')
    r = sub.add_parser('run'); r.add_argument('url'); r.add_argument('--workdir', default=f'{SPLIT_ROOT}/tvbox_run')
    r.add_argument('--workers', type=int, default=6)
    return ap


DISPATCH = {'fetch': cmd_fetch, 'health': cmd_health, 'split': cmd_split,
            'dedup': cmd_dedup, 'probe': cmd_probe, 'spider': cmd_spider, 'run': cmd_run}


def dispatch(argv):
    """统一入口（与生态其他技能一致，供 tvbox CLI 的 ROUTE 调用）。
    argv = [子命令, ...]；子命令缺省或 --help 时打印帮助。"""
    argv = list(argv)
    if not argv or argv[0] in ('-h', '--help'):
        build_parser().print_help()
        return 0
    if argv[0] not in DISPATCH:
        print(f'未知命令: {argv[0]} ｜ 可用: ' + ' / '.join(DISPATCH))
        return 2
    args = build_parser().parse_args(argv)
    return DISPATCH[args.cmd](args)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'dispatch':
        sys.exit(dispatch(sys.argv[2:]))
    args = build_parser().parse_args()
    sys.exit(DISPATCH[args.cmd](args))
