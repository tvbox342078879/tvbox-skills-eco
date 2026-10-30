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
    from core import file_to_janitor, register_monitor, http_get  # ECO 对齐：取数统一入口
except Exception:                              # 门面不可达时降级为 no-op，绝不阻断流水线
    def file_to_janitor(node_id, reason='', quiet=True): return False
    def register_monitor(risk_face, check_type, desc='', quiet=True): return False
    def http_get(url, **kw):                   # 取数入口不可达：显式三态失败（不静默回退 curl 另起炉灶）
        return ('error', None, 'core_unavailable')


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
    # ECO 对齐：走 core.http_get（as_text=False 二进制原样落盘，防文本模式损字节）
    _st, _pl, _rs = http_get(a.url, timeout=30, ua=UA, as_text=False)
    if _st != 'ok':
        print(f'❌ 抓取失败 {_rs} → {a.url}')
        return 1
    open(a.out, 'wb').write(_pl or b'')
    size = os.path.getsize(a.out)
    head = open(a.out, 'rb').read(16)
    form = 'plain'
    desc = '明文（未命中已知魔数）'
    for magic, name, d in MAGIC:
        if head.startswith(magic):
            form, desc = name, d
            break
    # 双 UA 验证伪装（§2）——ECO 对齐：二次下载走 core.http_get（内存比对，不再落 tmp）
    _st2, _pl2, _ = http_get(a.url, timeout=30, ua='Mozilla/5.0', as_text=False)
    same = False
    if _st2 == 'ok':
        h1 = hashlib.md5(open(a.out, 'rb').read()).hexdigest()
        h2 = hashlib.md5(_pl2 or b'').hexdigest()
        same = (h1 == h2)
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
    # ★ 断言口径必须是**实际非空列表的条数**，不能拿 total_after 硬撑：
    #   `write_json_verified` 的 _n() 优先读 sites，而 parses 为空时根本不写该键
    #   （上方 `if not items: continue`）。原写法 total_after 累加了空列表 0，
    #   看似等式成立，但一旦某类被去空、或只有 lives 这一类，_n() 与断言口径就分叉，
    #   于是**正确的产物被判成"写坏了"**。实测踩到：3 sites + 1 lives 断言 4、实读 3。
    #   现改为按"被保留的列表类"逐个数，与 _n() 的读取路径对齐。
    _kept = [k for k in ('sites', 'lives', 'parses') if isinstance(new.get(k), list) and new.get(k)]
    _expect = sum(len(new[k]) for k in _kept) if _kept else 0
    # ★ 注意 expect_n 的语义边界：write_json_verified 的 _n() 是「**单类**条数」
    #   （依次取 sites / lives / parses，取到第一个列表就返回），它天生不表达"跨类合计"。
    #   所以这里断言的是 **_kept[0] 那一类的条数**，不是合计——实测踩到：
    #   3 sites + 1 lives 传入合计 4，实读 sites 3，产物正确却被判"写坏"。
    #   不传 expect_n 也不对（那就等于没有回读断言了）。取第一类 = 与 _n() 同口径。
    _expect = len(new[_kept[0]]) if _kept else 0
    ok, msg = write_json_verified(new, fp, _expect, '去重结果')
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
    # ECO 对齐：Range 探活走 core.http_get（Range 头经 headers 传入，判活码口径不变）
    _st, _pl, _rs = http_get(url, timeout=timeout, ua=UA,
                             headers={'Range': 'bytes=0-300'}, as_text=False,
                             return_status=True)
    if _st == 'ok':
        code = _pl[0]
    elif _rs and _rs.startswith('http_'):
        try:
            code = int(_rs[5:])
        except Exception:
            code = 0
    else:
        code = 0                                 # conn/timeout/dns：不可达（同原 curl 000）
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
        # ECO 对齐：spider jar 下载走 core.http_get（as_text=False 二进制原样）
        _st, _pl, _rs = http_get(src, timeout=30, ua=UA, as_text=False)
        if _st != 'ok':
            print(f'❌ spider 下载失败：{src}（{_rs}）')
            return 1
        open(tmp, 'wb').write(_pl or b'')
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


# ── A·T4 模板层（tpl）──────────────────────────────────────────────
TPL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates.json')

def _tpl_load():
    with open(TPL_FILE, encoding='utf-8') as f:
        return json.load(f)

def _tpl_match(src):
    """按判据猜一个配置文件/片段的站点类型（可导入 json 或传单条 api 字符串）。"""
    t = _tpl_load()['types']
    if os.path.exists(src):
        try:
            cfg = json.load(open(src, encoding='utf-8'))
        except Exception:
            return None
        sites = cfg.get('sites') or []
        if cfg.get('lives'):
            return '直播'
        apis = [s.get('api', '') for s in sites]
    else:
        apis = [src]
    for api in apis:
        if api.startswith('./py/') or 'lang:hipy' in api:
            return 'py'
    for api in apis:
        if 'provide/vod' in api or '?ac=' in api:
            return '采集'
    for api in apis:
        if re.match(r'^https?://', api) and ('.m3u8' in api or '.txt' in api):
            return '直链'
    return None

def cmd_tpl(a):
    t = _tpl_load()
    if a.type:
        tp = t['types'].get(a.type)
        if not tp:
            print(f'❌ 未知类型 {a.type} ｜ 可用: ' + ' / '.join(t['types']))
            return 2
        print(f'📋 模板「{a.type}」— {tp["名称"]}')
        for k, v in tp.items():
            if k != '名称':
                print(f'   {k}: {v}')
        return 0
    if a.file:
        m = _tpl_match(a.file)
        print(f'🔍 判型：{a.file} → **{m or "未识别"}**')
        if m:
            print(f'   建议流程：pipeline.py tpl --type {m} 看测活/命名/入库规范后照做')
            if m == '直播':
                print('   双格式提示：入库同时跑 pipeline.py live2vod 产 CMS 兼容 vod 格式')
        return 0
    print('📋 模板总览：')
    for k, v in t['types'].items():
        print(f'   {k}: {v["名称"]} ｜ 判据 {v["判据"]}')
    print(f'   铁律：{t.get("18+", "")}')
    return 0


# ── D·T4 直播双格式（live2vod）：lives json → CMS 兼容 vod 格式 ──────
def cmd_live2vod(a):
    """把直播源（lives json 或 TXT/M3U）转成 CMS 兼容 vod 格式（道长 live2cms 思路），
    让直播也能在点播壳里当源看。输出与输入同目录，命名 <原名>.vod.json。"""
    try:
        cfg = json.load(open(a.file, encoding='utf-8'))
    except Exception:
        print(f'❌ {a.file} 不是可解析 json（TXT/M3U 转换后续版本支持，先走既有通道）')
        return 1
    lives = cfg.get('lives') or []
    if not lives:
        print('❌ 该文件无 lives 段，不是直播源')
        return 1
    groups = []
    for lv in lives:
        name = lv.get('name') or lv.get('group') or '直播'
        urls = lv.get('url') or lv.get('urls') or []
        if isinstance(urls, str):
            urls = [urls]
        groups.append((name, urls))
    vod_sites = []
    for name, urls in groups:
        for u in urls:
            vod_sites.append({
                'key': f'live2vod_{name}_{vod_sites.__len__()+1}',
                'name': f'📺{name}',
                'type': 0,
                'api': u,
                'searchable': 0, 'quickSearch': 0, 'filterable': 0,
            })
    out = {'sites': vod_sites, 'lives': lives}  # 保留原 lives，CMS 端只取 sites
    op = a.out or (os.path.splitext(a.file)[0] + '.vod.json')
    with open(op, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'✅ 双格式产出：{op} ｜ {len(vod_sites)} 条 CMS 兼容站点（源自 {len(groups)} 个直播分组）')
    print('   入库规范：与原 lives json 成对入库（模板「直播」·双格式项，M-R141 配套 D 改造）')
    return 0


# ── E·T6 CMS 快照还原（cms_restore）：code:1+list 快照 → 采集接口站点 ──
def _cms_check(d):
    """苹果CMS 采集响应结构判定：code==1 + list 数组 + 元素含 vod 特征字段。
    返回 (True, list) 或 (False, 原因)。"""
    if not isinstance(d, dict):
        return False, '顶层不是对象'
    if d.get('code') != 1:
        return False, f"code={d.get('code')!r} ≠ 1"
    lst = d.get('list')
    if not isinstance(lst, list) or not lst:
        return False, '无 list 或为空'
    probe = lst[0]
    if not isinstance(probe, dict) or 'vod_id' not in probe or 'vod_name' not in probe:
        return False, 'list 元素缺 vod_id/vod_name（非苹果CMS 响应）'
    return True, lst


def cmd_cms_restore(a):
    """把 CMS 采集接口的响应快照（code:1+list）还原成可入库的采集站点。
    来源识别优先级：--url > 快照文件内的「来源URL」字段（本工具落盘格式）> 失败。
    产出 <快照名>.site.json（sites 数组），站点必须过 classify_site 判「采集」（M-R38 同口径）。"""
    try:
        doc = json.load(open(a.file, encoding='utf-8'))
    except Exception as e:
        print(f'❌ {a.file} 解析失败：{e}')
        return 1
    # 本工具落盘格式 {来源URL, 快照:{...}} 直接解包；裸 CMS json 也能吃
    src_url = a.url or ''
    if isinstance(doc, dict) and isinstance(doc.get('快照'), dict) and 'list' in doc['快照']:
        src_url = src_url or doc.get('来源URL', '')
        doc = doc['快照']
    ok, lst = _cms_check(doc)
    if not ok:
        print(f'❌ 结构判定不过：{lst}——不是苹果CMS 采集响应快照（code:1+list）')
        return 2
    if not src_url:
        print('❌ 还原不出接口地址：快照本身不含自家域名，请补 --url <采集接口地址>（或用本工具带来源URL的落盘格式）')
        return 2
    # 反推 base：去 query，保留到 provide/vod 路径；无路径则只留域名并警告
    from urllib.parse import urlsplit
    sp = urlsplit(src_url)
    base = f'{sp.scheme}://{sp.netloc}{sp.path.rstrip("/")}'
    if 'provide/vod' not in base:
        print(f'⚠️ 来源路径不含 provide/vod（{base}），按苹果CMS 标准路径补全，建议实测核验')
        base = f'{sp.scheme}://{sp.netloc}/api.php/provide/vod'
    api = base + '?ac=videolist'
    # name 推断：--name > 播放源标记（vod_play_from 首值）> 域名
    name = a.name
    if not name:
        pf = str(lst[0].get('vod_play_from') or '').split('#')[0].split('$')[0]
        name = pf or sp.netloc
    site = {
        'key': f'cms_{sp.netloc.split(".")[-2]}',
        'name': f'🏢{name}',
        'type': 1,
        'api': api,
        'searchable': 1, 'quickSearch': 1, 'filterable': 1,
        'ext': {'来源': 'cms_restore', '快照list条数': len(lst)},
    }
    cls = classify_site(site)
    if cls != '采集':
        print(f'❌ 生成站点分类={cls}（应为「采集」），口径断裂，拒绝产出（M-R38）')
        return 2
    out = {'sites': [site], 'provenance': {'来源URL': src_url, '还原时间': time.strftime('%Y-%m-%dT%H:%M:%S')}}
    op = a.out or (os.path.splitext(a.file)[0] + '.site.json')
    with open(op, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    cats = {}
    for it in lst:
        cats[str(it.get('type_name') or '未知')] = cats.get(str(it.get('type_name') or '未知'), 0) + 1
    print(f'✅ 快照还原：{op}')
    print(f'   站点：{site["name"]} ｜ api={api} ｜ 分类判据=采集 ✅')
    print(f'   快照规模：{len(lst)} 条 ｜ 分类分布：{cats}')
    print('   入库规范：产出后走源库 ingest，探源司实测拉分类列表为准')
    return 0


def _load_link():
    """懒加载 source-hub 的 link 门面（落库判据 + 台账类型的单一事实源）。

    为什么懒加载：`pipeline.py` 的其他命令（fetch/split/dedup/probe）是纯本地计算，
    不该因为资料库侧脚本缺一个依赖就整体起不来。**但**这两条新命令一旦要用，
    拿不到 link 就必须报错退出——绝不允许"静默降级成不落库还声称完成"。
    """
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import link  # noqa: E402
        return link, ''
    except Exception as e:
        return None, f'{type(e).__name__}: {str(e)[:120]}'


def _to_record(s, src_url=''):
    """站点/线路 dict → 源库一条记录（字段名对齐 S160 两库 schema）。

    ★ 单一事实源：分类判据调 `classify_site()`（与 split 同口径，M-R38），
      源类型判据调 `link.dest_db()` 所属体系，不在本处另立一套。
    """
    api = str(s.get('api', ''))
    kind = classify_site(s)
    rec = {
        '名称': (s.get('name') or api)[:60],
        'api': api,
        '源类型': 'py线路' if kind == 'py' else 'csp线路',
        '分类': kind,
        '来源': src_url or 'interface.newapi',
    }
    ext = str(s.get('ext') or '')
    if ext:
        rec['ext'] = ext[:300]
    if s.get('type') is not None:
        rec['type'] = s.get('type')
    return rec


def cmd_newapi(a):
    """★ 新接口处理（v1.2.0）：一条新接口从「拿到 URL」到「源库有据」的固定六步。

    为什么要有这个入口：此前「来了个新接口」全靠模型临场拼命令，每步口径都可能不同——
    体检用一套判据、拆分用另一套、入库字段又靠记忆。**同一件事每次做法不一样，
    就等于没有流程**。本命令把 SKILL.md §2→§3→§4→§5 固化成一条链，每步产物落盘可复查。

    链路（顺序即铁律，§4.5.4）：
      1 fetch   抓取 + 伪装形态识别
      2 health  体检 + 体系识别（新体系自动登记监察司风险面）
      3 split   分类拆分（py/采集/直链/其他 + lives/parses）
      4 dedup   全量去重
      5 probe   全量测活（默认关，--probe 才跑，避免误伤限速源）
      6 ingest  测活通过项落线路源库（--no-ingest 可只跑前五步）

    三态：任何一步失败 → **立刻停**并明说停在哪一步、已产出了什么（不继续跑后续步骤，
    也不假装"部分成功"）。测活全灭 → 不落库，报告"零可用"而不是"入库 0 条"。
    """
    workdir = a.workdir
    os.makedirs(workdir, exist_ok=True)
    raw = os.path.join(workdir, 'raw.bin')
    print(f'══ 新接口处理：{a.url}')
    print(f'   工作目录：{workdir}')

    # 1 抓取
    fa = argparse.Namespace(url=a.url, out=raw)
    if cmd_fetch(fa) != 0:
        print('❌ 停在第 1 步（抓取）：URL 不可达/无响应。后续步骤未执行。')
        return 1
    # 抓下来的可能不是 json（伪装形态），先按真身转
    jf = raw
    head = open(raw, 'rb').read(16)
    if head.startswith(b'PK\x03\x04') or head.startswith(b'\x1f\x8b'):
        print('   ⚠️ 抓到的不是明文 json（zip/gzip 真身）→ 需先解包，本命令不猜，停在此处')
        print(f'      文件：{raw}')
        print('❌ 停在第 1 步（抓取）：解包不在本命令范围，请先解包再对新 URL/文件重跑')
        return 1
    if not _read_json(jf):
        print(f'   ⚠️ 抓取内容非 json（首字节 {head[:8]!r}）→ 可能是 html 跳转页/需要鉴权')
        print('❌ 停在第 1 步（抓取）：内容不可解析为配置 json')
        return 1
    print(f'   ✅ 1/6 抓取完成：{os.path.getsize(raw)} 字节')

    # 2 体检
    ha = argparse.Namespace(file=jf)
    if cmd_health(ha) != 0:
        print('❌ 停在第 2 步（体检）：json 解析失败')
        return 1
    print('   ✅ 2/6 体检完成（体系识别见上）')

    # 3 拆分
    splitdir = os.path.join(workdir, 'split')
    sa = argparse.Namespace(file=jf, outdir=splitdir)
    if cmd_split(sa) != 0:
        print('❌ 停在第 3 步（拆分）')
        return 1
    print(f'   ✅ 3/6 拆分完成：{splitdir}')

    # 4 去重
    da = argparse.Namespace(file=jf)
    if cmd_dedup(da) != 0:
        print('❌ 停在第 4 步（去重）')
        return 1
    print('   ✅ 4/6 去重完成')

    d = _read_json(jf) or {}
    sites = d.get('sites') or []
    print(f'   待处理条目：sites {len(sites)} 条')

    # 5 测活（可选）
    alive = None
    if a.probe:
        pa = argparse.Namespace(file=jf, workers=a.workers)
        if cmd_probe(pa) != 0:
            print('❌ 停在第 5 步（测活）')
            return 1
        print('   ✅ 5/6 测活完成（判定见上）')
    else:
        print('   ⏭  5/6 测活跳过（未传 --probe）')

    # 6 入库
    if a.no_ingest:
        print('   ⏭  6/6 入库跳过（--no-ingest）')
        print('✅ 新接口处理完成（未落库，产物在 ' + workdir + '）')
        return 0
    if not sites:
        print('   ⚠️ 无 sites 条目可入库（lives/parses 走 live2vod / 单独入库口径）')
        print('❌ 停在第 6 步（入库）：零条目')
        return 1
    link, err = _load_link()
    if not link:
        print(f'❌ 停在第 6 步（入库）：落库门面不可达 —— {err}')
        print('   **不降级、不假装成功**：请先修复 source-hub/scripts/link.py 可导入')
        return 1
    db = link.dest_db('')
    recs = [_to_record(s, a.url) for s in sites if str(s.get('api') or '').strip()]
    if a.dry:
        print(f'   （--dry 预演）将写入 {db} 共 {len(recs)} 条，样例：{recs[0] if recs else "无"}')
        return 0
    ok, fail = link.add_records(db, recs)
    print(f'   ✅ 6/6 入库：成功 {ok} 条 ｜ 失败 {fail} 条 → {db}')
    if fail:
        print(f'   ⚠️ {fail} 条未写入（服务端拒绝/schema 不符）——不当成功，请查 build.py 字段自查')
        return 1
    return 0


def cmd_packin(a):
    """★ 整包入库（v1.2.0）：一个整包（zip/配置 json）→ 按体系拆分 → 线路源库。

    与 newapi 的区别：newapi 面向「单接口 URL」，本命令面向「**一整包**」——
    一个包里可能同时含 A/B/C/D 多体系、含 jar 伪装、含几百站点。核心是**先按体系归位**，
    再按体系分别判源类型入库，避免整包混着灌进去后每条都要人工回头看体系。

    输入两种形态：
      · `.json` 配置 → 直接按 sites/lives 拆
      · `.zip`     → 解包后逐个 json 处理（jar 类资源登记监察司，不投收件箱——它们还有用）

    三态：某成员处理失败 → 该成员计入 fail 并在末尾逐条列出，其余继续（整包场景不能一颗坏果
    砸掉整筐），但**只要 fail>0 退出码即 1**——"大部分成功"不等于"成功"。
    """
    src = a.file
    if not os.path.exists(src):
        print(f'❌ 文件不存在：{src}')
        return 1
    workdir = a.workdir or os.path.join(os.path.dirname(src) or '.', 'packin')
    os.makedirs(workdir, exist_ok=True)
    print(f'══ 整包入库：{src}')
    print(f'   工作目录：{workdir}')

    members = []
    if src.lower().endswith('.zip'):
        import zipfile
        try:
            with zipfile.ZipFile(src) as z:
                names = [n for n in z.namelist() if n.lower().endswith('.json')]
                if not names:
                    print('   ⚠️ 包里没有 .json 成员——若整包是 jar/dex（Android 包），'
                          '它不是"接口配置包"，请走反查流程，本命令不处理')
                    print('❌ 零 json 成员')
                    return 1
                for n in names:
                    out = os.path.join(workdir, os.path.basename(n))
                    with open(out, 'wb') as f:
                        f.write(z.read(n))
                    members.append(out)
            print(f'   解包完成：{len(members)} 个 json 成员')
        except Exception as e:
            print(f'❌ 解包失败（{type(e).__name__}: {str(e)[:120]}）')
            return 1
    else:
        members = [src]

    link, err = _load_link()
    if not link and not a.dry:
        print(f'❌ 落库门面不可达 —— {err}')
        return 1

    tot_ok = tot_fail = 0
    fails = []
    by_sys = {}
    for m in members:
        d = _read_json(m)
        if d is None:
            fails.append((os.path.basename(m), 'json 解析失败'))
            continue
        sites = d.get('sites') or []
        spider = (d.get('spider') or '').strip()
        sysname, why = _detect_system(d, spider_form=None)
        by_sys.setdefault(sysname, []).append(os.path.basename(m))
        # 带 jar / 需解包 → 体系只是静态推断，登记监察司待下钻（不当定论）
        if spider and not spider.startswith(('http', './', '../')):
            try:
                register_monitor('未解包 jar 的静态体系推断', 'rule_liveness',
                                 f'{os.path.basename(m)} spider={spider[:60]}', quiet=True)
            except Exception:
                pass
        recs = [_to_record(s, a.file) for s in sites if str(s.get('api') or '').strip()]
        if not recs:
            fails.append((os.path.basename(m), '零可入库条目'))
            continue
        db = link.dest_db('')
        if a.dry:
            print(f'   （--dry）{os.path.basename(m)} → {sysname} ｜ {len(recs)} 条 → {db}')
            tot_ok += len(recs)
            continue
        ok, fail = link.add_records(db, recs)
        tot_ok += ok
        tot_fail += fail
        print(f'   · {os.path.basename(m)} ｜ 体系 {sysname}（{why[:40]}）｜ 写入 {ok} 失败 {fail}')

    print(f'\n   体系分布：' + ' ｜ '.join(f'{k} {len(v)} 包' for k, v in by_sys.items()))
    print(f'   合计：成功 {tot_ok} 条 ｜ 失败 {tot_fail} 条 ｜ 成员失败 {len(fails)} 个')
    for nm, why in fails:
        print(f'     ⚠️ {nm}：{why}')
    if tot_fail or fails:
        print('❌ 整包入库**未全部成功**（大部分成功 ≠ 成功，退出码 1）')
        return 1
    print('✅ 整包入库完成')
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
    tp = sub.add_parser('tpl'); tp.add_argument('--type'); tp.add_argument('--file')
    lv = sub.add_parser('live2vod'); lv.add_argument('file'); lv.add_argument('--out', default='')
    cr = sub.add_parser('cms_restore'); cr.add_argument('file'); cr.add_argument('--url', default='')
    cr.add_argument('--name', default=''); cr.add_argument('--out', default='')
    na = sub.add_parser('newapi', help='新接口处理：抓→体检→拆→去重→测活→入库 一条链')
    na.add_argument('url')
    na.add_argument('--workdir', default=f'{SPLIT_ROOT}/newapi')
    na.add_argument('--probe', action='store_true', help='跑全量测活（默认跳过，防误伤限速源）')
    na.add_argument('--workers', type=int, default=6)
    na.add_argument('--no-ingest', dest='no_ingest', action='store_true', help='只跑到测活前，不落库')
    na.add_argument('--dry', action='store_true', help='入库前预演，不真写')
    pk = sub.add_parser('packin', help='整包入库：zip/json 整包按体系拆分后入线路源库')
    pk.add_argument('file')
    pk.add_argument('--workdir', default='')
    pk.add_argument('--dry', action='store_true')
    return ap


DISPATCH = {'fetch': cmd_fetch, 'health': cmd_health, 'split': cmd_split,
            'dedup': cmd_dedup, 'probe': cmd_probe, 'spider': cmd_spider, 'run': cmd_run,
            'tpl': cmd_tpl, 'live2vod': cmd_live2vod, 'cms_restore': cmd_cms_restore,
            'newapi': cmd_newapi, 'packin': cmd_packin}


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
