#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三方联动引擎（规则 45）：源数据库 ↔ 底包库 ↔ 会话台账

子命令:
  status                     三方体检：各有多少条/多少包/多少会话, 联动是否闭环
  learn <关键字>              【开工必做】查台账同类历史: 踩坑/解法/可复用点
  log   --summary .. --pit .. --fix .. --reuse .. [--type ..] [--src N] [--pkg ..]
                             【收工必做】写一条会话台账(含联动状态)
  sync                       L1: 底包→源库自动提源(调 tvbox-source-hub/run.py)
  brief                      开工速览: 一次打印 三方状态 + 最近踩坑 TOP

原则: 一个命令完成一件事, 不逐步确认, 减少中间环节。
"""
import os, sys, json, time, argparse, subprocess, re
from collections import Counter

LIB='/root/.codebuddy/skills/library'
HUB='/root/.codebuddy/skills/tvbox-source-hub/scripts'
BLK='/root/.codebuddy/skills/tvbox-source-blacklist/scripts/blk.py'  # L7 第一道闸
DB_SESS='TO4LwsjVVdR8JRAUlkejk3'     # ③ 会话台账（业务经验）
DB_LIB='XTuuj8mKtqnZSolDdeDxkq'      # ④ 资料库操作经验库（平台经验）
DB_DEAD='hGwE41OpJDOR8CW0sLUwRx'     # 🗑 失效源库（黑名单命中者归档处）
DB_ECO='T4kODtANe7rSrtZTu4sIND'      # 🧩 TVBox技能生态登记库
DOC_SNAP='j2Ygm8nsglPdJ7DPM1A0we'    # 📊 四极现状快照（唯一权威状态源）
DOC_CONV='bZz0mBZj3svZjKQXLZtVAd'    # 协作约定（含 8.14 数据源总库段）
FOLDERS={'体系A':'jxJHRZHyNzjY6fONPlFTe1','体系B':'AJJixTz2yohkUz4Pu1zAXB',
         '体系C':'j7bD2jgogJxILWwKQv7ZMJ','体系D':'pqSV3xwMuZsgBLmPQSKdGh',
         '成人':'SbPKgzcnGAsQ3cKjbRIJT4'}
SNAP_ID_FILE=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'snapshot_id.txt')
# —— 台账「触发类型」唯一真源（S158）——
# brake.py 的 BIZ_KINDS/GOV_KINDS 是**判定侧**定义，本处是**写入侧**可选值。
# 两边必须同源：否则判定侧在算一个写不进数据库的枚举值（实测踩过）。
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)))), 'ecosys-conductor', 'scripts'))
    import brake as _brake
    _LEDGER_TYPES = tuple(_brake.BIZ_KINDS) + tuple(_brake.GOV_KINDS)
except Exception:
    # 读不到 brake 时**不许静默退回旧六类**（那正是本缺陷的成因）——
    # 用显式的最全并集兜底，并在首次使用时告警。
    _LEDGER_TYPES = ('新接口处理', '整包入库', '扫描归源库', '出库交付', '补源', '失效复测',
                     '规则维护', '纠错返工', '查询答疑', '生态治理')
    print('⚠️ link.py 未能从 brake.py 派生台账类型，已退回内置并集——请检查跨技能路径', file=_sys.stderr)


def read_snap_id():
    try: return open(SNAP_ID_FILE).read().strip()
    except Exception: return DOC_SNAP
def write_snap_id(i):
    open(SNAP_ID_FILE,'w').write(i)
SPACE='8w5cnqCqQJ0wNoF2Hb6efH'
ROOT='GCLJWbFGS1BmYbj6JCX9it'        # ② 底包库根
# ★ S158：快照父目录提为常量（原先在 create-doc 调用里裸写），
#   因为下面失败提示也要引用它——**同一个坐标写两处，改一处必漏另一处**（M-R85）。
SNAP_PARENT='GHqCfbGoCOAOV4g4NVxsCE' # 📁 数据·数据源总库（快照落点）
DOC_RULES='isuY4U01Me0kUggpMN5RfX'   # 规则总览文档（资料库侧的规则沉淀）

# ── 共享效率核心：缓存 + 并行读 + 限流写（技能↔脚本联动的运行时落点）──
# 仅同一进程内（如 tvbox 把多命令放进同一进程时）三个脚本共享同一个 core 模块实例，
# 缓存与并行池复用；跨技能 CLI 单命令调用时各自独立实例，无跨进程复用（S123 措辞收窄）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import (rows_of, api, add_records, fanout,
                  add_records_parallel, invalidate, rows_of_failed,
                  file_to_janitor,
                  # S159：源库坐标与联合读的**唯一事实源**（详见 core.py 顶部注释）
                  SRC_DBS, DB_SRC_LEGACY, DB_BAK_LEGACY,
                  src_rows, src_rows_t)  # noqa: E402

# S159：源库坐标一律取自 core（单一事实源），本文件不再写死库 id。
#   · 读：`src_rows()` / `src_rows_t()`（源库联合）
#   · 写：`dest_db(api)` 单一判据分派（成品源库不由线路入库写入）
# ★ 定义顺序铁律：这些必须放在 `from core import ...` **之后**。实跑踩到两次
#   NameError（SRC_DBS / DB_BAK_LEGACY）——`ast.parse` 只验语法，验不出顺序依赖。
# ★S160：py/csp 两库合并为「线路源库」，故 `SRC_PY`/`SRC_CSP` 不再是两个不同 id。
#   **保留这两个名字**（值相同）是为了让外部既有调用不至于 KeyError 崩掉；
#   但它们已无"分库"含义，需要区分技术形态请读记录的 `源类型` 字段。
_SRC_BY_NAME = dict(SRC_DBS)
SRC_LINE = _SRC_BY_NAME['线路源库']     # ★S160：唯一线路落点
SRC_PROD_DB = _SRC_BY_NAME['成品源库']  # ★S160：成品源库（沿用旧名），只由 promote 写
SRC_PY   = SRC_LINE                     # 兼容别名（S160 起无独立 py 库）
SRC_CSP  = SRC_LINE                     # 兼容别名（S160 起无独立 csp 库）
SRC_PROD = SRC_PROD_DB                  # 兼容别名（指向成品源库）
_DEST_NAME = {SRC_LINE: '线路源库', SRC_PROD_DB: '成品源库'}
DB_SRC = '[联合:线路源库+成品源库]'      # 仅作标签展示，**勿用于读/写**
DB_BAK = DB_BAK_LEGACY               # 旧备用库：S144 已清空废弃，仅保留兼容

# ★S110 体系重构：旧 A/B/C/D 四槽位体系（壳子家族）已废止，改按「靠什么活」分两值。
#   用户 2026-10-18 原话："以后做的 json 叫 jar系和直链系"——判据只看 api 开头，无灰色地带：
#     · csp_*        → jar系    （jar 里的类，挂了 jar 就能跑；不管 jar 里是 Java/py/js）
#     · http(s)://   → 直链系   （零依赖，联网即用；含远程 .py / .txt / .m3u8）
#     · ./xxx        → jar系/直链系 + 随身文件标记（要跟包一起带文件）
#   废止 `可用体系`（A·CatVod标准系 等四槽位）：用户包自带 jar，壳子家族区分无意义。
SYS = {}  # 保留名字避免旧调用点 ImportError；新代码不要再引用
SYSTEM_JAR = 'jar系'
SYSTEM_LINK = '直链系'


def classify(api):
    """体系判据（S110 重构，S111 修正本地 py/js）。返回 (体系select, 可用体系list)。

    可用体系一律返回 []（字段已废止，保留空值占位以兼容旧签名）。

    ★ S111 修正：本地 `./py/*.py` 与 `./lib/*.js` 原判 jar系，理由是"要有引擎 jar 才跑"。
      这条推理是错的，且错得有价值，记在这里防止重犯：
        · `jar系` 的语义是**该线路依赖某个外部业务 jar**（mojiGuard/amns/各家自定类），
          出包时必须把那个 jar 一起带上，否则源直接跑不起来。
        · 本地 py 靠 **py 引擎**（drpy2/TVB-py）执行，本地 js 靠 **drpy2 引擎**执行，
          两者都不是"外部业务 jar 依赖"。实测这 7 条 `py引擎` 字段全部有值。
        · 反向验证：`jar_file()` 能认出 jar 家族的 241 条，恰好等于库里有 jar 的那 241 条，
          一条不差 —— 说明**认不出 jar 家族 == 不依赖 jar**，这 7 条本来就该在直链系。
      注意区分「csp_ 内置类」：`csp_PanUC`/`csp_Config` 看着像标准类，但测活证据写明
      「csp类线：依赖 jar，仅可随底包端测」—— **csp 一律依赖 jar（含 CatVod 标准 jar）**，
      所以这里不动 csp 分支。标准 jar 也是 jar，出包同样要带。
    """
    api = (api or '').strip()
    if api.startswith('http'):
        return SYSTEM_LINK, []
    if api.startswith('csp_'):
        return SYSTEM_JAR, []
    if api.startswith('./'):
        # 本地随身文件：纯文本清单 + py/js 源码都不依赖外部业务 jar
        # （py/js 依赖的是**引擎**，与 jar 依赖是两个维度，见 docstring）
        return SYSTEM_LINK, []
    return SYSTEM_LINK, []


def side_file(api):
    """随身文件判据（S110 新增）：返回需跟包一起带的文件类型，空串=不需要。

    为什么要单列：`./` 开头的源不是"只要有 json 就行"——用户拿到包必须同时拿到这些文件，
    否则源直接跑不起来。这是出包环节最容易漏的一处（S110 用户追问暴露）。
    """
    api = (api or '').strip()
    if not api.startswith('./'):
        return ''
    low = api.lower()
    if low.endswith(('.txt', '.m3u', '.m3u8')):
        return 'txt'
    if low.endswith('.py'):
        return 'py'
    if low.endswith(('.js', '.jsm')) or 'drpy' in low:
        return 'js'
    return '其他'


def jar_file(api):
    """jar 家族预判（S110）：按 csp 类名后缀猜是哪家的 jar，认不出返回空串。

    只做"最确定的"两类——`*Guard` 与 `*Amns` 是社区公认包名，其余 267 条类名各家混用，
    猜错比空着更糟（空着=待验，猜错=误导）。真值要靠解 dex 列类名（mount_judge）。
    """
    api = (api or '').strip()
    if not api.startswith('csp_'):
        return ''
    cls = api[4:]
    if cls.endswith('Guard'):
        return 'mojiGuard.jar'
    if cls.endswith('Amns'):
        return 'amns.jar'
    return ''

def src_type(api):
    """技术形态判据（S160）：返回写进 `源类型` 字段的值。与 `dest_db` 同一把尺子。"""
    return 'csp线路' if (api or '').strip().startswith('csp_') else 'py线路'

def dest_db(api):
    """**落库判据·唯一事实源**（S159 建立，S160 简化）：一条线路该写进哪个源库。

    S160 起 py/csp 合并为**同一库**，故这里只剩两个答案：
      · 线路（http 直链 / `./py/*` / `csp_*`）→ 线路源库，形态差异写进 `源类型` 字段
      · **成品源库不在此分派范围内**——它由 `probe harvest promote` 从实测过的源提升而来，
        不是线路入库的落点。
    两个历史坑一并记着（都是"分派判据写错"这一族）：
      · S159 前写成 `SRC_PROD if startswith('csp_')` → 把 csp_ 源全灌进成品库；
      · S160 前依赖"按库名区分形态"，并库后 `dict(SRC_DBS)[库名]` 全部 KeyError。

    `api` 参数保留（本判据现在是常量，但调用点都传它，且将来若再分库可就地改回）。
    """
    return SRC_LINE

# ---------- 内容分级（S102·规则56）：站点内部内容分类三档判定 ----------
# 用户 2026-10-16 要求：站点分类含"伦理片"类的，按三档入库——
#   全量 = 保留伦理片（分类全留）；纯净 = 伦理片隐藏（剔除18分类）；18 = 只保留伦理分类。
# 判据源 = 站点 json 的 `sites[].categories`（用户确认："站点内部的内容分类"）。
ADULT_WORDS = ['伦理','三级','激情','成人','18禁','里番','无码','H漫','情色','禁片']
# ⚠️ 严禁收地域/泛词（欧美/日韩/港台/韩国/日本/动漫/福利）——实证：正常站有
#    `日韩动漫` 分类，收地域词会把干净站误判成18。用精确短词，子串匹配。

def classify_adult(cats):
    """站点内容分级：cats(站点 categories 数组) → '纯净'|'全量'|'18'|'未知'。

    四态（M-R60 三态契约的派生）：
      '未知'  无 categories / 空数组 → **未确认**，不等于"干净"（不进纯净版！）
      '纯净'  有分类且无任一命中 → 无18内容
      '全量'  有命中 且 仍有非18分类 → 混编站（如西瓜：30分类含1个伦理片）
      '18'    有命中 且 全是18分类 → 纯18站（如 🔞 那批）
    """
    if not cats:
        return '未知'
    cats = [str(c).strip() for c in cats if str(c).strip()]
    if not cats:
        return '未知'
    hit = [c for c in cats if any(w in c for w in ADULT_WORDS)]
    if not hit:
        return '纯净'
    return '18' if len(hit) == len(cats) else '全量'


def classify_adult_from_sites(site_list):
    """对一批站点(sites 数组)取**最保守**聚合分级。

    同一 api 被多站名引用时（S025 实证 gh-proxy x8），categories 可能不同 →
    取最保守：任一站是18 → 整 api 判18；有未知 → 至少"未知"；全纯净 → 纯净。
    返回 (分级, 全部categories并集)。
    """
    lv = {'未知':0, '纯净':1, '全量':2, '18':3}
    worst = '纯净' if site_list else '未知'
    allcats = []
    for s in site_list:
        cats = s.get('categories') if isinstance(s, dict) else None
        allcats.extend(cats or [])
        v = classify_adult(cats)
        if lv[v] > lv[worst]:
            worst = v
    return worst, allcats

# ---------- ④ 资料库经验侧 ----------
def show_lib(title='④ 资料库经验库', lib=None):
    if lib is None: lib=rows_of(DB_LIB)
    print(f'\n{title} ({DB_LIB})')
    print(f'   经验条目 {len(lib)}')
    if lib:
        for k,v in Counter(r.get('操作类型','') for r in lib).most_common():
            print(f'     {v:2}  {k}')
        for k,v in Counter(r.get('严重度','') for r in lib).most_common():
            print(f'     严重度 {v:2}  {k}')
    return lib

def search_lib(kw):
    lib=rows_of(DB_LIB)
    return [r for r in lib if kw.lower() in ' '.join(
        str(r.get(f) or '') for f in ['场景','坑','解法','可复用点','涉及API']).lower()]

def cmd_liblearn(a):
    hit=search_lib(a.keyword)
    print(f'🔍 资料库经验检索「{a.keyword}」 → 命中 {len(hit)} 条')
    if not hit:
        print('   无同类经验。按标准流程走，遇到坑用 lib-log 沉淀。'); return
    for r in hit:
        print(f'\n── {r.get("编号","")} | {r.get("操作类型","")} | 严重度:{r.get("严重度","")}')
        print(f'   场景: {r.get("场景","")}')
        if r.get('坑'):   print(f'   ⚠️坑: {r["坑"]}')
        if r.get('解法'): print(f'   ✅解法: {r["解法"]}')
        if r.get('可复用点'): print(f'   ♻️可复用: {r["可复用点"]}')
        if r.get('涉及API'): print(f'   🔧API: {r["涉及API"]}')

def cmd_liblog(a):
    lib=rows_of(DB_LIB)
    nxt=_next_seq(lib,'编号','L')
    rec={'编号':{'text':nxt},
         '时间':{'date':time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())},
         '操作类型':{'select':a.type},
         '场景':{'text':a.scene},
         '坑':{'text':a.pit or ''},'解法':{'text':a.fix or ''},
         '可复用点':{'text':a.reuse or ''},'涉及API':{'text':a.api or ''},
         '关联空间':{'text':a.space or ''},'严重度':{'select':a.sev}}
    # ★ T29 / P052：结构门禁——类型、场景、严重度必须非空，否则拒写
    if not _guard_required_struct(
            {'时间': rec['时间']['date'], '操作类型': a.type,
             '场景': a.scene, '严重度': a.sev},
            '经验库', ('时间', '操作类型', '场景', '严重度')):
        return 1
    # S158 收敛：改走 core.add_records_diag —— 统一三态 + properties 结构检测 + 失败原因回传。
    # 原实现（自造 subprocess + `except: pass` 留 ok=False）把「解析不出」折叠成
    # 「写入被拒」，两者都表现为 ok=False，下游分不清是数据问题还是通路问题。
    # ★ 改这段时踩到的坑（勿回归）：改成走 core 后，下面 else 分支仍在读 `out`（已不存在），
    #   一旦走到失败分支就 NameError——**改写调用方式却没清掉旧变量**是这类重构的典型漏点。
    #   现在失败原因统一由 _why 承载，else 分支不再引用任何旧局部变量。
    ok=False
    _why=''
    try:
        import core as _c
        _n_ok, _n_fail, _why = _c.add_records_diag(DB_LIB, [rec])
        ok = (_n_fail == 0 and _n_ok > 0)
    except Exception as _e:
        _why = f'通路不可用（{type(_e).__name__}: {str(_e)[:60]}）'
        print(f'   ⚠️ 经验写入通路不可用——这不是"写入被拒"，下方读回验证会给出真实结论')
    invalidate(DB_LIB)    # 写后失效缓存（铁律），否则下方读回验证命中脏缓存
    if ok:
        # 读回验证（伪成功防御）—— 未查到时短重试2次防服务端延迟误报（S020增强）
        after, v = [], False
        for attempt in range(3):
            after = rows_of(DB_LIB)
            v = any(r.get('编号') == nxt for r in after)
            if v:
                break
            time.sleep(0.6)
        print(f'✅ 资料库经验已写入 {nxt}（{a.scene[:36]}）| 读回验证 {"通过" if v else "⚠️未查到(3次重试后仍无，请独立读回复核)"} | 累计 {len(after)} 条')
        # ★ S196 补（P019 缺口④）：经验池写入同笔投待过账——此前只有③台账投（LOG-），
        #   ④经验池每次都靠 reconcile 报差额再人工 writeoff，同一件事两套待遇（M-R38）。
        _enqueue_post(nxt, len(after), v, account='经验池', prefix='L')
    else:
        print(f'❌ 写入失败：{_why or "（未取得原因）"}')

# ---------- status ----------
def cmd_status(a):
    print('═══ 三方联动体检（规则 45）═══')
    # 并行读：源库 + 底包根节点 + 台账 + 经验库（一轮并发）
    # S085：根节点改用 _root_nodes() 三态原语（M-R60 ⑩ 单一事实源），
    #       云端读失败 → ('fail', None)，不得折叠成 [] 冒充"底包库为空"。
    src_pack, root_r, sess, lib_rows = fanout(
        src_rows_t,                      # (ok, rows, info) 三元组一次带回
        _root_nodes,
        lambda: rows_of(DB_SESS),
        lambda: rows_of(DB_LIB))
    src, src_info = src_pack[1], src_pack[2]
    _root_st, nodes = root_r
    if _root_st != 'ok':
        print('   ⚠️ 底包库根节点读取失败（云端 API 未授权/网络异常）——')
        print('      「顶层节点/体系文件夹」统计不可用，请勿据此判定"底包库为空"')
        nodes = []
    # ① 源数据库
    print(f'\n① 源数据库  源库联合 ({DB_SRC})')
    # S159：分库列条数——只看总数会掩盖「某一个库读失败/为空」
    _bd = src_info.get('by_db') or {}
    print('   分库：' + ' | '.join(f'{k} {v if v is not None else "❌读失败"}' for k, v in _bd.items())
          + f' → 合计 {len(src)}')
    if not src_info.get('ok'):
        print(f'   ⚠️ 有库读取失败（{src_info.get("failed")}）——合计条数偏低，勿当"源库变小了"')
    if src:
        for k,v in Counter(r.get('可用体系','') for r in src).most_common():
            print(f'     {v:4}  {k or "(空)"}')
    # ② 底包库
    folders=[n for n in nodes if n.get('kind')=='folder']
    files=[n for n in nodes if n.get('kind') in ('drive','doc')]
    print(f'\n② 底包库  📁底包·底包总库 ({ROOT})')
    print(f'   顶层节点 {len(nodes)} 个 | 体系文件夹 {len(folders)} | 文件/文档 {len(files)}')
    for n in folders: print(f'     📁 {n.get("title")}')
    # ③ 会话台账
    print(f'\n③ 会话台账  ({DB_SESS})')
    print(f'   会话记录 {len(sess)} 条')
    if sess:
        for k,v in Counter(r.get('触发类型','') for r in sess).most_common():
            print(f'     {v:2}  {k}')
        for k,v in Counter(r.get('反哺状态','') for r in sess).most_common():
            print(f'     反哺:{v:2}  {k or "(空)"}')
    # ④ 资料库经验库
    lib=show_lib(lib=lib_rows)
    print('\n─── 闭环检查（四方）───')
    print(f'   L1 底包→源库   : {"✅ 已通" if src else "⚠️ 源库空"}')
    print(f'   L4 两库→台账   : {"✅ 已通" if sess else "⚠️ 台账空, 收工记得 log"}')
    print(f'   L3 台账→开工   : {"✅ 有经验可学" if sess else "⚠️ 无历史经验"}')
    print(f'   L5 资料库→三方 : {"✅ 有平台经验" if lib else "⚠️ 经验库空, 遇坑用 lib-log"}')
    print(f'   L6 三方→资料库 : {"✅ 规则已沉淀" if lib or sess else "⚠️ 待沉淀"}')

# ---------- learn（开工必做） ----------
def cmd_learn(a):
    kw=a.keyword
    sess=rows_of(DB_SESS)
    hit=[]
    for r in sess:
        blob=' '.join(str(r.get(f) or '') for f in
                      ['任务摘要','涉及接口','成果产出','踩坑','解法','可复用点','联动包'])
        if kw.lower() in blob.lower(): hit.append(r)
    print(f'🔍 台账检索「{kw}」 → 命中 {len(hit)} 条')
    if not hit:
        print('   无同类历史。建议：按标准流程走，收工时用 log 沉淀本次经验。')
        return
    for r in hit:
        print(f'\n── {r.get("会话编号","")} | {r.get("时间","")} | {r.get("触发类型","")}')
        print(f'   摘要: {r.get("任务摘要","")}')
        if r.get('踩坑'):  print(f'   ⚠️踩坑: {r["踩坑"]}')
        if r.get('解法'):  print(f'   ✅解法: {r["解法"]}')
        if r.get('可复用点'): print(f'   ♻️可复用: {r["可复用点"]}')
    # 汇总可复用点
    reuse=[r['可复用点'] for r in hit if r.get('可复用点')]
    if reuse:
        print(f'\n♻️ 可复用点汇总（{len(reuse)} 条）—— 本次直接套用:')
        for x in reuse: print(f'   • {x}')

# ---------- log（收工必做） ----------
def _next_seq(rows, field, prefix):
    """续号：取**已有最大编号 + 1**，不是 `len(rows)+1`（S148 修）。

    为什么必须改：`len(rows)+1` 在**删过记录**之后会撞号。
    实测台账 89 条时最大号是 S089、经验库 133 条时是 L133，恰好相等所以看不出问题；
    但只要中间删一条（例如清掉一条测试记录），`len+1` 就会生成一个**已存在的编号**。
    撞号不会报错——服务端照收，于是库里出现两条同号记录，此后一切"按编号反查"
    都会拿到歧义结果（回读验证、对账、引用全部受影响）。

    这也是 M-R38 的老教训：**编号的唯一依据是库里的事实，不是本地计数**。
    """
    mx = 0
    for r in rows or []:
        v = str(r.get(field) or '').strip()
        if not v.startswith(prefix):
            continue
        tail = v[len(prefix):]
        if tail.isdigit():
            mx = max(mx, int(tail))
    return f'{prefix}{mx + 1:03d}'


def _guard_required_struct(rec, label, fields):
    """写前结构门禁（T29 / P052）：关键字段不得为空。

    ★ 为什么加这一步：T29 实测台账里有 15 条「类型空 + 时间空 + 严重度空」的记录，
      而 cmd_log/cmd_liblog 的 argparse 明明有 default 和 choices，命令行产生不了空值。
      查证结果是那些条目**不是走这两个命令写的**——是手工批量补录，内容抄对了、
      结构字段全漏。所以缺陷不在"参数缺省"，在"**写入路径可绕过**"。

      本门禁拦的是"结构性空白"：只要走到 cmd_log/cmd_liblog，这三项必须非空。
      真出现空，宁可不写也不落一条半成品——半成品进了库，事后靠人筛查不出来
      （15 条里 2 条连编号都没有，只能靠逐条读内容猜）。
    """
    bad = [k for k in fields if not str(rec.get(k) or '').strip()]
    if bad:
        print(f'❌ 拒绝写入{label}：字段为空 {bad}——结构不完整，宁可不写也不落半成品')
        return False
    return True


def cmd_log(a):
    sess=rows_of(DB_SESS)
    nxt=_next_seq(sess,'会话编号','S')
    rec={'会话编号':{'text':nxt},
         '时间':{'date':time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())},
         '触发类型':{'select':a.type},
         # ★ S163 修：摘要里**不许再自报会话号**。
         #   台账号由 _next_seq 分配（S113），而 CHANGELOG 里的会话号（S163）是另一套编号——
         #   两条规则同时喂给一个字段，结果台账出现 `S113 | S163 ...` 这种自相矛盾的行，
         #   且靠摘要首词 grep 历史的人会拿到错号。编号的唯一来源是「会话编号」字段。
         '任务摘要':{'text':re.sub(r'^S\d{3}[：: ]+', '', a.summary).strip()},
         '涉及接口':{'text':a.src or ''},
         '成果产出':{'text':a.output or ''},
         '踩坑':{'text':a.pit or ''},
         '解法':{'text':a.fix or ''},
         '可复用点':{'text':a.reuse or ''},
         '联动包':{'text':a.pkg or ''},
         '新增源数':{'number':a.nsrc},
         '反哺状态':{'select':a.status}}
    # ★ T29 / P052：结构门禁——类型与摘要必须非空，否则拒写
    if not _guard_required_struct(
            {'时间': rec['时间']['date'], '触发类型': a.type, '任务摘要': a.summary},
            '会话台账', ('时间', '触发类型', '任务摘要')):
        return 1
    # S158 收敛：同上，走 core.add_records_diag（统一三态 + 失败原因回传）。
    # ★ 同 cmd_liblog：改成走 core 后必须清掉旧局部变量 `out`，否则 else 分支 NameError。
    ok=False
    _why=''
    try:
        import core as _c
        _n_ok, _n_fail, _why = _c.add_records_diag(DB_SESS, [rec])
        ok = (_n_fail == 0 and _n_ok > 0)
    except Exception as _e:
        _why = f'通路不可用（{type(_e).__name__}: {str(_e)[:60]}）'
        print(f'   ⚠️ 会话台账写入通路不可用——这不是"写入被拒"，下方读回验证会给出真实结论')
    invalidate(DB_SESS)   # 写后失效缓存（铁律），否则下方读回验证命中脏缓存
    if ok:
        # 读回验证（伪成功防御）—— 未查到时短重试2次防服务端延迟误报（S020增强）
        after, verified = [], False
        for attempt in range(3):
            after = rows_of(DB_SESS)
            verified = any(r.get('会话编号') == nxt for r in after)
            if verified:
                break
            time.sleep(0.6)
        print(f'✅ 台账已写入 {nxt}（{a.summary[:40]}）| 读回验证 {"通过" if verified else "⚠️未查到(3次重试后仍无，请独立读回复核)"} | 累计 {len(after)} 条')
        # ★ S175 修 P019 缺口③：**台账写了就投一条待过账**，不许再靠人补 ledger post。
        _enqueue_post(nxt, len(after), verified)
    else:
        # 服务端 error 原文由 core.add_records_diag 带回（_why 形如「服务端拒绝：...」）。
        # 保留这条区分很重要：`ok=False` 有两种成因（服务端拒绝 / 返回里没有 results），
        # 不说清就会误判成网络问题（S148 教训）。
        print(f'❌ 写入失败：{_why or "（未取得原因）"}')


def _enqueue_post(sid, total, verified, account='会话台账', prefix='LOG'):
    """把「台账/经验池 +1」这件事投进账房的待过账队列（P019 缺口③的正解）。

    ## 为什么是投递而不是直调
    本命令（源枢库）与账房（eco-ledger）是两个技能。直接 `import ledger` 会让
    源枢库**硬依赖**账房——账房一旦不可用，连台账都记不进去，把「记账失败」放大成
    「留痕失败」。投递则只依赖一个 JSON 文件：账房当时挂掉，台账照样写成功，
    待过账队列留着，账房下次起来自己消费。

    ## 为什么不在这里直接记账
    `make_entry` 的币种/守恒律/科目表校验只有账房知道。在这里记账等于抄一份判据（M-R38）。
    投递只负责说清**发生了什么**（台账多了一条），怎么记由账房决定。

    ## 投递失败怎么办
    **不影响台账写入的结果**。台账已经落库且回读通过，那是既成事实；队列写失败只意味着
    「这笔还没人过账」，账房 `reconcile` 仍会报账实不符——即降级回修复前的行为，
    不会比现在更坏。故此处只告警不抛错。

    ★ S196 补：参数化 account/prefix——此前只给③台账投（LOG-），④经验库（lib-log）
      没投，每次都要 reconcile 报差额再人工 writeoff。同一机制两库共用（M-R38）。
    """
    try:
        _q = '/root/.codebuddy/skills/eco-ledger/scripts/pending_post.json'
        _d = {'version': '1.0', 'items': []}
        try:
            with open(_q, encoding='utf-8') as _f:
                _d = json.load(_f)
        except FileNotFoundError:
            pass                      # 首次投递，用空队列
        except Exception as _e:
            # 队列损坏时**不覆盖**——覆盖会吞掉别人的待办。宁可这次不投，也不能毁队列。
            print(f'   ⚠️ 待过账队列读取失败（{type(_e).__name__}）——本次未投递，账房将报账实不符（可人工 ledger post 补）')
            return
        _items = _d.setdefault('items', [])
        _vid = f'{prefix}-{sid}'
        if any(str(it.get('voucher')) == _vid for it in _items):
            return                    # 幂等：同一编号不重复投
        _items.append({
            'voucher': _vid,
            'debit': account,
            'credit': '暂记·无主变动',
            'amount': 1,
            'currency': '记录数',
            'action': '入库',
            'agent': 'tvbox log' if prefix == 'LOG' else 'tvbox lib-log',
            'at': time.strftime('%Y-%m-%dT%H:%M:00Z', time.gmtime()),
            'note': f'{account} +1 = {sid}（读回验证{"通过" if verified else "未确认"}，累计{total}）；'
                    f'来源 P019 缺口③+S196 补④：写入器落库时同笔自动投递过账',
        })
        tmp = _q + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as _f:
            json.dump(_d, _f, ensure_ascii=False, indent=2)
        os.replace(tmp, _q)           # 原子替换，防写一半
        print(f'   📮 已投待过账 {_vid}（账房 reconcile 时自动消费，无需人工 ledger post）')
    except Exception as _e:
        print(f'   ⚠️ 待过账投递失败（{type(_e).__name__}: {str(_e)[:50]}）——账房将报账实不符，'
              f'可人工 ledger post 补；**台账本身已写成功，不受影响**')

# ---------- sync（L1 自动提源） ----------
def cmd_sync(a):
    print('🔗 L1 联动：底包 → 源数据库 自动提源')
    r=subprocess.run(['python3',f'{HUB}/run.py'],capture_output=True,text=True)
    print(r.stdout.strip() or r.stderr[:300])

# ---------- brief（开工速览） ----------
def cmd_sizes(a):
    """只读四极规模单行输出（S073）：供 conductor ③活跃度等**评估/只读**场景使用。
    与 snapshot 的区别：**绝不写云端**——snapshot 会 create-doc + rename-node（有副作用），
    评估命令若调它=每次评估都污染云端；本命令仅 collect() 计数后打印。"""
    # S159：源库改联合读。原读旧库 DB_SRC（S144 已清空）→ 永远打印「源库 0」，
    #   conductor ③活跃度据此判 0 分，而 assess 总分仍是 100（各维度独立算）——
    #   「分数说满分、明细说零条」并列展示，谁都没报错。三态：读失败显式标 ❌。
    src_t, sess, lib = fanout(
        src_rows_t, lambda: rows_of(DB_SESS), lambda: rows_of(DB_LIB))
    _ok, _rows, _info = src_t
    _bd = _info.get('by_db') or {}
    _bad = [k for k, v in _bd.items() if v is None]
    _sizes = '＋'.join(f'{v}' for k, v in _bd.items() if v is not None)
    _tail = f'（线路{_bd.get("线路源库")}+成品{_bd.get("成品源库")}）' if _sizes else ''
    _flag = f' ⚠️{len(_bad)}库读失败' if _bad else ''
    print(f'源库 {len(_rows)}{_tail}{_flag} | 台账 {len(sess)} | 经验库 {len(lib)}')


def cmd_brief(a):
    # S085：根节点改用 _root_nodes() 三态原语（M-R60 ⑩），失败不折叠成 []。
    src, root_r, sess, lib = fanout(
        src_rows,                       # S159：源库联合
        _root_nodes,
        lambda: rows_of(DB_SESS),
        lambda: rows_of(DB_LIB))
    _root_st, nodes = root_r
    if _root_st != 'ok':
        nodes = []
        _brief_cloud_fail = True
    else:
        _brief_cloud_fail = False
    print('⚡ 开工速览（四方联动）')
    _nodes_disp = f'{len(nodes)} 顶层节点' if not _brief_cloud_fail else '❌读取失败'
    print(f'   源库 {len(src)} 线路 | 底包库 {_nodes_disp} | 台账 {len(sess)} 会话 | 资料库经验 {len(lib)} 条')
    if _brief_cloud_fail:
        print('   ⚠️ 底包库根节点读取失败（云端 API 异常）——"顶层节点"不可用，勿当"底包库为空"')
    if src:
        # ★ S164 修：原统计 `可用体系` —— 该字段 S110 已废止（classify 设计上恒返回 []，
        #   见上方 SYS 注释："用户包自带 jar，壳子家族区分无意义"，空值仅为兼容旧签名占位）。
        #   于是 brief 每天都报「源库体系: 空:732」，定时任务每天把这个死数挂给用户。
        #   改统计活字段 `体系`（直链系/jar系，classify 真正在写的那个）；
        #   `可用体系` 若哪天要复活，应由 classify 恢复返回值，而不是让 brief 继续数空。
        c=Counter(r.get('体系','') for r in src)
        print(f'   源库体系: '+' | '.join(f'{k.split("·")[0] if k else "空"}:{v}' for k,v in c.most_common(4)))
    # 业务踩坑
    pits=[(r.get('会话编号'),r.get('踩坑')) for r in sess if r.get('踩坑')]
    if pits:
        print(f'\n⚠️ 业务踩坑 {len(pits)} 条（避雷）:')
        for n,p in pits[-4:]: print(f'   [{n}] {str(p)[:66]}')
    # 平台（资料库）踩坑
    lp=[(r.get('编号'),r.get('坑'),r.get('严重度')) for r in lib if r.get('坑')]
    if lp:
        print(f'\n🔧 资料库操作坑 {len(lp)} 条（平台层避雷）:')
        for n,p,s in lp[-4:]: print(f'   [{n}|{s}] {str(p)[:62]}')
    reuses=[r.get('可复用点') for r in sess if r.get('可复用点')]
    lre=[r.get('可复用点') for r in lib if r.get('可复用点')]
    if reuses or lre:
        print(f'\n♻️ 可复用经验 {len(reuses)+len(lre)} 条（直接套用）:')
        for x in (reuses+lre)[-6:]: print(f'   • {str(x)[:66]}')

    # **S152 加：开工提速三件套**。上面给了「避什么坑」，这里给「用哪条命令更省事」。
    # 事故：端到端实测——同一件事（顺序列全表）手写 subprocess 分页循环 9.907s，
    # 走 `core.rows_of`（进程内缓存）0.629s，**15.7 倍差距**；而历史早已固化的招式
    # （如 T-I「写后必回读」）因开工没检索被现场重新推导一遍——机制在，入口没走，
    # 等于空转（M-R105 的同族形态：**有机制 ≠ 在用机制**）。
    print('\n💡 提速三件套（动手前先跑，胜过事后返工）:')
    print('   tvbox conductor ask "<需求>"  术语翻译+技能路由+**自动预载历史经验**（M-R68 同坑不二踩）')
    print('   tvbox learn <关键字>          查业务经验 ③台账  ｜ tvbox lib-learn <关键字> 查平台经验 ④')
    print('   tvbox p "<a>" "<b>" 并发跑 ｜ tvbox run a b 同进程共享缓存（重复读库实测快 15.7×）')

# ---------- snapshot：采集四极真值并刷新权威快照 ----------
# S082·M-R60 修复：云端读取必须三态——(ok, nodes) / ('fail', None)。
# 旧版把云端读失败折叠成 []（空节点列表），鉴权失效/网络不可达时 collect() 会
# 静默产出「0 节点」的**假权威快照**，用户误以为库真空（M-R58/M-R60 同类隐患）。
_CLOUD_FAIL = '☁️读取失败'
def _root_nodes():
    out=api('space_api.py','space.workspace.list-nodes','--space-id',SPACE,
            '--parent-node-id',ROOT,'--limit','60')
    try:
        nodes=json.loads(out)['data'].get('nodes')
    except Exception:
        return ('fail', None)   # 不得折叠成 []（写盘会造假象）
    if nodes is None:
        # S086·M-R60：有 data 但无 nodes 字段 = **结构失配**（接口改版/字段改名），
        # 与「真·空库（nodes 显式为空数组）」语义相反，绝不可折叠成同一 ('ok', [])。
        return ('fail', None)
    return ('ok', nodes)

def _fold_nodes(pid):
    o=api('space_api.py','space.workspace.list-nodes','--space-id',SPACE,
          '--parent-node-id',pid,'--limit','40')
    try:
        nodes=json.loads(o)['data'].get('nodes')
    except Exception:
        return ('fail', None)
    if nodes is None:
        return ('fail', None)   # S086·M-R60：结构失配 ≠ 空库
    return ('ok', nodes)

def collect():
    # 并行读：四极库 + 根节点 + 5 个体系文件夹（原串行 7 次云端读 → 并发 1 轮）
    fold_tasks=[lambda p=pid: _fold_nodes(p) for pid in FOLDERS.values()]
    src,sess,lib,root_r,*folds_r = fanout(
        src_rows,                       # S159：源库联合（不再读已清空的旧库）
        lambda: rows_of(DB_SESS), lambda: rows_of(DB_LIB),
        _root_nodes, *fold_tasks)
    bak=[]   # S144 起旧备用库已清空废弃，四极「备用」极实际退场
    # 三态解包：云端失败 → root/folds 记为 None（非 []），并汇总失败清单（M-R60）
    cloud_fail=[]
    root_st, root = root_r
    if root_st == 'fail':
        cloud_fail.append('底包库顶层'); root = None
    folds={}
    for i,nm in enumerate(FOLDERS):
        st, nd = folds_r[i]
        if st == 'fail':
            cloud_fail.append(nm); nd = None
        folds[nm]=nd
    # S083·M-R60：数据库读失败同样登记（rows_of 失败→_FAILED），凡失败的四极库记账
    # S159：源库已联合，逐个查失败态；旧备用库已废弃不再计入
    db_fail=[lbl for lbl, dbid in
             [(f'源库·{n}', d) for n, d in SRC_DBS] + [('台账',DB_SESS),('经验库',DB_LIB)]
             if rows_of_failed(dbid)]
    sysc=Counter(r.get('可用体系','') or '(空)' for r in src)
    statc=Counter(r.get('状态','') or '(空)' for r in src)
    return dict(src=src,bak=bak,sess=sess,lib=lib,root=root,folds=folds,
                sysc=sysc,statc=statc,cloud_fail=cloud_fail,db_fail=db_fail)

def build_snapshot_md(c):
    ts=time.strftime('%Y-%m-%d %H:%M',time.localtime())
    cols=len(c['src'][0]) if c['src'] else 0
    cf=set(c.get('cloud_fail') or [])   # 云端读取失败的项（M-R60：不得写假 0）
    def _cloud_n(nodes):
        """云端失败 → 显示「—（云端读取失败）」而非 0——禁止把『读失败』当『空库』。"""
        return '—（☁️读取失败）' if nodes is None else f'{len(nodes)}'
    df=set(c.get('db_fail') or [])   # S083：数据库读取失败的库（同样不得写假 0）
    def _db_n(lbl, rows):
        """库读失败 → 显示「—（读取失败）」而非 0。"""
        return '—（❌读取失败）' if lbl in df else f'{len(rows)}'
    L=[]
    L.append('# 📊 状态·四极现状快照（唯一权威）')
    L.append('')
    L.append('> **本页是四极数据的唯一权威状态源。** 其他文档（协作约定 / RULES / 索引）一律**不记绝对数字**，只引用本页。')
    L.append('> 由 `link.py snapshot` **自动生成与刷新**，不要手写。')
    L.append('')
    L.append(f'最后更新：{ts}')
    L.append('')
    if cf or df:
        _fails = sorted(set(cf) | set(df))
        L.append(f'> ⚠️ **本次采集中 {len(_fails)} 项读取失败**（{", ".join(_fails)}）——')
        L.append('> 相关数量显示为「—」而非 0，**本页不完整**，请修复云端连接后重跑 `tvbox snapshot`。')
        L.append('')
    L.append('## 一、四极总量')
    L.append('')
    L.append('| 极 | 载体 | ID | 数量 | 备注 |')
    L.append('| --- | --- | --- | --- | --- |')
    L.append(f'| ① 源数据库 | 数据源总库 | {DB_SRC} | {_db_n("源库",c["src"])} 条 | {cols} 列（含 体系 + 可用体系） |')
    L.append(f'| ② 底包库 | 📁 底包·底包总库 | {ROOT} | {_cloud_n(c["root"])} 个顶层节点 | 5 个体系文件夹 |')
    L.append(f'| ③ 会话台账 | 🔗 会话台账 | {DB_SESS} | {_db_n("台账",c["sess"])} 条 | 业务经验 |')
    L.append(f'| ④ 资料库经验库 | 📚 资料库操作经验库 | {DB_LIB} | {_db_n("经验库",c["lib"])} 条 | 平台经验 |')
    L.append('')
    L.append('## 二、配套库')
    L.append('')
    L.append('| 库 | ID | 数量 | 备注 |')
    L.append('| --- | --- | --- | --- | --- |')
    L.append(f'| 数据源总库备用库 | {DB_BAK} | {_db_n("备用库",c["bak"])} 条 | {cols} 列；⚠️ 同步未接线（停更风险，P010 待 bak-sync），非实时备份 |')
    L.append('')
    L.append('## 三、底包库体系分布')
    L.append('')
    L.append('| 体系文件夹 | ID | 节点数 |')
    L.append('| --- | --- | --- | --- |')
    for nm in ['体系A','体系B','体系C','体系D','成人']:
        pid=FOLDERS[nm]
        L.append(f'| {nm} | {pid} | {_cloud_n(c["folds"].get(nm))} |')
    L.append('')
    L.append('## 四、源库「可用体系」分布')
    L.append('')
    L.append('| 可用体系 | 条数 | 说明 |')
    L.append('| --- | --- | --- | --- |')
    note={'A·CatVod标准系':'csp 裸类名','B·moji Guard系':'csp XxxGuard 后缀',
          'D·Amns系':'csp XxxAmns 后缀'}
    for k,v in c['sysc'].most_common():
        if k.startswith('A·CatVod标准系,B'):
            L.append(f'| A+B+C+D（全系） | {v} | http 零 jar 依赖，跨体系通用 |')
        else:
            L.append(f'| {k} | {v} | {note.get(k,"")} |')
    L.append('')
    L.append('判定铁律（后缀即体系）：`csp_XxxGuard`→B，`csp_XxxAmns`→D，`csp_裸类名`→A，`http://`→全系 A/B/C/D。')
    L.append('实测依据：guard系 95 条全部带 Guard 后缀、lytvs系 76 条全部不带，交叉违规 0。')
    L.append('')
    L.append('## 五、源库「状态」分布（实测）')
    L.append('')
    L.append('| 状态 | 条数 | 说明 |')
    L.append('| --- | --- | --- | --- |')
    smap={'正常':'实测可用（HTTP 200 且有实质内容）','停用':'实测失效（超时/4xx/5xx/空体）',
          '待查':'csp 类，依赖 spider jar，需端测','有广':'可用但含广告'}
    for k,v in c['statc'].most_common():
        L.append(f'| {k} | {v} | {smap.get(k,"")} |')
    L.append('')
    L.append('状态零空值：http 类全部实测标注；csp 类因依赖 spider 无法 curl 测，统一标「待查」。')
    L.append('')
    L.append('## 六、历史沿革（数字修订留痕）')
    L.append('')
    L.append('| 时间 | 变更 |')
    L.append('| --- | --- | --- |')
    L.append('| 2026-10-13 | 三表终态：总库 339 / 备用库 537 / 功夫库 27 |')
    L.append(f'| 2026-10-15 | 全包扫描归库：总库 339 → **{len(c["src"])}**（净增 624）；新增「可用体系」列，6 列 → {cols} 列 |')
    nz=c['statc'].get('正常',0); dz=c['statc'].get('停用',0)
    L.append(f'| 2026-10-15 | 全量实测：http 类 {nz+dz} 条 → 正常 {nz} / 停用 {dz}；状态列零空值 |')
    L.append('')
    L.append('> 协作约定 8.14 中「192 条 / 104 条 / 6 列」为更早历史值，已过时，**以本页为准**。')
    return '\n'.join(L)

def cmd_snapshot(a):
    print('📊 采集四极真值…')
    c=collect()
    cf=c.get('cloud_fail') or []
    df=c.get('db_fail') or []
    def _n(x): return '—' if x is None else str(len(x))
    print(f'   源库 {_n(c["src"]) if "源库" not in df else "—"} | 备用 {_n(c["bak"]) if "备用库" not in df else "—"} | '
          f'台账 {_n(c["sess"]) if "台账" not in df else "—"} | 经验库 {_n(c["lib"]) if "经验库" not in df else "—"}')
    print(f'   底包库 {_n(c["root"])} 顶层 | ' +
          ' '.join(f'{k}:{_n(c["folds"].get(k))}' for k in ['体系A','体系B','体系C','体系D','成人']))
    _all_fail = sorted(set(cf) | set(df))
    if cf:
        print(f'   ⚠️ 云端读取失败 {len(cf)} 项（{", ".join(cf)}）')
    if df:
        print(f'   ⚠️ 数据库读取失败 {len(df)} 项（{", ".join(df)}）')
    if _all_fail:
        print(f'   → 本次**拒绝刷新云端权威快照**（M-R60：不得把『读失败』写成 0 的假象）')
    md=build_snapshot_md(c)
    if a.dry:
        open('/tmp/snapshot_preview.md','w',encoding='utf-8').write(md)
        print(f'   [DRY] 已生成预览 /tmp/snapshot_preview.md（{len(md)} 字符），未写入云端')
        print(md[:400]); return
    if _all_fail:
        print('   ❌ 已中止云端刷新：请先修复读取失败（鉴权/网络）后重跑 tvbox snapshot。')
        return
    # 云端刷新：整页重建（快照页为脚本生成，重建最稳），随后把旧页标废弃（规则 35）
    # ★ S158：改走 core._run_lib（统一三态）。旧写法把 stdout 塞进 json.loads，
    #   失败时只剩一句 "刷新失败: <前250字>"，看不出是鉴权问题还是网络问题。
    import core as _c
    _ok, r, _err = _c._run_lib('space_api.py', 'space.importer.create-doc',
                               '--space-id', SPACE, '--parent-id', SNAP_PARENT,
                               '--title', '📊 状态·四极现状快照（唯一权威）',
                               '--markdown', md)
    if not _ok:
        print(f'❌ 刷新失败：{_err[:200]}')
        print('   ⚠️ 快照**未刷新**——不要当"已更新"，下次读到的仍是旧快照。')
        return
    try:
        d = json.loads(r)['data']; nid = d.get('nodeBlockId')
    except Exception:
        print(f'❌ 刷新返回解析失败（前 250 字）：{r[:250]}')
        return
    print(f'✅ 快照已刷新 → {nid}')
    print(f'   {d.get("url")}')
    # 旧页标废弃 + 记录当前有效 id
    cur=read_snap_id()
    if cur and cur!=nid:
        stamp=time.strftime('%m%d_%H%M',time.localtime())
        _r_ok, _, _r_err = _c._run_lib('space_api.py', 'space.workspace.rename-node',
                                       '--node-id', cur,
                                       '--title', f'❌已废弃_快照_{stamp}')
        if _r_ok:
            print(f'   旧快照 {cur} 已改名为「❌已废弃_快照_{stamp}」')
        else:
            print(f'   ⚠️ 旧快照改名失败（{_r_err[:100]}）——旧页仍是原名，可能被误当有效快照')
        # ★ 生态联动（S104）：自动投递清道夫收件箱 → 用户一处按单删
        # ★ S158：旧页若投递成功，**不必停在原地**——先试"整包目录一起走"，再试单页。
        #   根因复盘（G4）：本函数每轮都 rename 一个成功 + 投递一个失败，而 rename 的
        #   成功给了"已处理"的假信号 —— 于是 `📁 数据·数据源总库` 里堆了 100+ 个
        #   `❌已废弃_快照_*`，越标越多、一个都没少。**"改名"不是处理，"搬走"才是。**
        #   投递失败必须说出原因，不能只用一句"未投递"带过：这正是堆积长达十几个
        #   会话却无人察觉的原因（谁也不知道它其实一次都没投成过）。
        _filed = file_to_janitor(cur, f'快照刷新后的旧版（{stamp}），已被新快照取代')
        if _filed:
            print(f'   → 已自动移入「🗑 待删收件箱」（清道夫），无需手动找')
        else:
            print(f'   ⚠️ 旧快照**未投递成功**（仍在 {SNAP_PARENT} 原地）——'
                  f'这类残留会越积越多，请人工清一次或查清道夫收件箱是否可用')
    elif cur and cur == nid:
        print(f'   · 快照 id 未变（{nid}），无旧页需处理')
    write_snap_id(nid)
    print(f'   当前有效快照 id 已记录 → {SNAP_ID_FILE}')
    # 检查协作约定是否写死了 id（应改为「取最新一份」的稳健指引）
    if cur and cur!=nid:
        s=api('doc/get_doc_reviews.py','--page-id',DOC_CONV)
        hard=cur in s or nid in s
        if hard:
            print(f'   ⚠️ 协作约定中写死了快照 id，需改为「取标题为…的最新一份」的稳健指引')
        else:
            print(f'   ✅ 协作约定用的是「最新一份」稳健指引，无需改 id')

# ---------- M-R114 统一并发入口（S110） ----------
# 拉自家文档（doc API）
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded as _run_bounded, TIER_API  # noqa: E402


# ---------- audit：一致性对账（文档记载 vs 真值） ----------
def cmd_audit(a):
    print('🔍 一致性对账（资料库记载 vs 四极真值）')
    c=collect()
    # S086·M-R60⑬：c['root'] 在云端失败时为 None（非 []）——裸 len(None) 会崩溃，
    # 属「装失败的哑巴」镜像（读失败本该显示"—"却抛异常）。统一用 _nn 保护。
    def _nn(x): return '—' if x is None else len(x)
    truth={'源库':len(c['src']),'备用库':len(c['bak']),'台账':len(c['sess']),
           '经验库':len(c['lib']),'底包库顶层':_nn(c['root'])}
    if c.get('cloud_fail') or c.get('db_fail'):
        _f=sorted(set(c.get('cloud_fail') or [])|set(c.get('db_fail') or []))
        print(f'   ⚠️ 本次采集中 {len(_f)} 项读取失败（{", ".join(_f)}）——显示"—"者不可作对账基准')
    print('\n【真值】')
    for k,v in truth.items(): print(f'   {k}: {v}')
    # 检查协作约定里的陈旧数字
    print('\n【文档记载扫描】')
    issues=[]
    # S072：3 篇文档独立，网络拉取并行（M-R41 独立即并行）——原串行 1.15s→并行 ~0.4s
    # 每次强制重新拉取（api 缓存按参数区分，3 篇 page-id 各异本就不命中）；
    # 仍显式 _cache=False 以杜绝「同参数二次调用读旧值」破坏一致性要求。

    _PAGES={'协作约定':DOC_CONV,'索引总库':'7x1ooSOUVOnqRqMYszd3Qm',
            '可用清单':'RErMC5CkyuzcFx53Dg8Po9'}
    def _fetch(kv):
        f,pid=kv
        try:
            return f, api('doc/get_doc_reviews.py','--page-id',pid,_cache=False)
        except Exception as e:
            return f, ''
    # ★ S163：改走统一入口（A 档：打自家 API）
    _rep=_run_bounded(list(_PAGES.items()), _fetch, tier=TIER_API,
                      label='link-docs', keep_order=True, on_error='record')
    _docs=dict((_r if _r is not None else (k,'')) for k,_r in zip(list(_PAGES.keys()),_rep.results))
    for f,o in _docs.items():
        p=f'/tmp/audit/{f}.xml'
        open(p,'w',encoding='utf-8').write(o)
    for f in _PAGES:
        txt=re.sub(r'<[^>]+>','',_docs.get(f,''))
        for m in re.finditer(r'数据源总库[^\n]{0,40}?(\d{2,5})\s*条', txt):
            n=int(m.group(1))
            if n!=len(c['src']):
                issues.append((f,'数据源总库',n,len(c['src'])))
        for m in re.finditer(r'备用库[^\n]{0,40}?(\d{2,5})\s*条', txt):
            n=int(m.group(1))
            if n!=len(c['bak']):
                issues.append((f,'备用库',n,len(c['bak'])))
        for m in re.finditer(r'（(\d{1,2})\s*列', txt):
            n=int(m.group(1)); cur=len(c['src'][0]) if c['src'] else 0
            if n<cur: issues.append((f,'列数',n,cur))
    # 检测各文档是否已含「更正/以快照为准」标注 → 区分 未处置 vs 已处置(历史留痕)
    annotated={}
    for f in ['协作约定','索引总库','可用清单']:
        p=f'/tmp/audit/{f}.xml'
        try: s=open(p,encoding='utf-8').read()
        except Exception: continue
        txt=re.sub(r'<[^>]+>','',s)
        annotated[f]=bool(re.search(r'(以.{0,6}快照.{0,6}为准|已过时|不再记绝对数字|现状更正)', txt))
    if not issues:
        print('   ✅ 未发现数量级不一致')
    else:
        seen=set()
        uniq=[]
        for it in issues:
            key=(it[0],it[1],it[2],it[3])
            if key in seen: continue
            seen.add(key); uniq.append(it)
        fresh=[x for x in uniq if not annotated.get(x[0])]
        done =[x for x in uniq if annotated.get(x[0])]
        print(f'   共 {len(uniq)} 处数值差异 | 未处置 {len(fresh)} | 已标注更正 {len(done)}')
        if fresh:
            print('   ⚠️ 未处置（需修正）:')
            for doc,fld,old,new in fresh: print(f'     [{doc}] {fld}: {old} → {new}')
        if done:
            print('   ✅ 已标注更正（历史留痕，以快照页为准，无需删除）:')
            for doc,fld,old,new in done: print(f'     [{doc}] {fld}: {old} → {new}')
        print('\n【快照页状态】')
        cur=read_snap_id()
        print(f'   当前有效快照 id: {cur}（记录于 snapshot_id.txt）')
    print('\n【处置建议】')
    if any(not annotated.get(x[0]) for x in uniq) if issues else False:
        print('   1) 跑 link.py snapshot 刷新权威快照页')
        print('   2) 在未处置文档中补「以快照页为准」更正块')
    else:
        print('   ✅ 数值差异均为历史留痕且已标注更正，无需动作')
        print('      数据变动后跑 link.py snapshot 即可保持同步')


# ---------- L7：统一入库管线（黑名单拦截 → 分类 → 实测 → 入源库 → 快照）----------
def cmd_ingest(a):
    p=a.candidates or '/tmp/to_add.json'
    if not os.path.exists(p):
        # ★ S165：原来 `print(...); return` —— None 被入口归一成 exit=0。
        #   实测 `tvbox ledger call ingest` 一个字没入，判定却是 ✅ok。
        #   入库是**写库动作**，"没做"和"做成了"在退出码上必须分开，
        #   否则上游 relay 会把下一步（snapshot/收口）当成有依据地继续跑。
        print(f'❌ 缺少候选文件 {p}')
        print(f'   用法: tvbox ingest --candidates <文件> [--dry]')
        print(f'   缺省路径 /tmp/to_add.json 是旧约定；沙箱重启后它会丢，别依赖它。')
        return 2
    cands=json.load(open(p,encoding='utf-8'))
    if isinstance(cands,dict): cands=cands.get('records') or cands.get('items') or []
    print(f'═══ L7 统一入库管线 ═══\n候选源 {len(cands)} 条')
    # 第一道闸：黑名单拦截（调用 blk.py guard）
    json.dump(cands,open('/tmp/_ingest_cands.json','w',encoding='utf-8'),ensure_ascii=False)
    out=subprocess.run([sys.executable,BLK,'guard','--candidates','/tmp/_ingest_cands.json'],
                      capture_output=True,text=True).stdout
    print(out.strip())
    try:
        block=json.load(open('/tmp/guard_block.json',encoding='utf-8'))
        pass_=json.load(open('/tmp/guard_pass.json',encoding='utf-8'))
    except Exception:
        print('⚠️ guard 输出读取失败'); return
    now=time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())

    # 拦截部分 → 直接归档失效库（不污染源总库）
    # 幂等双层（S025 实证复发修复）：exist_api=库内去重；seen=批内去重。
    # 坑：exist_api 是读时快照，循环内追加不回写——同批次同 api（如 gh-proxy
    # 代理同一 drpy2.min.js 被 8 个站名各拦一次）会全部通过判重写入，失效库
    # 出现重复行（历史 yylx.win/ghfast.top 各有 x2，本次 gh-proxy x8 复发）。
    exist_api={r.get('样例api') for r in rows_of(DB_DEAD)}
    seen_api=set()
    dead_recs=[]
    for x in block:
        api=x['api接口']; h=x.get('依据') or ''
        if api[:180] in exist_api: continue   # 幂等：库内已归档则跳过
        if api[:180] in seen_api: continue    # 幂等：本批已归档则跳过
        seen_api.add(api[:180])
        dead_recs.append({'host':{'text':h or '(空)'},
            '拉黑范围':{'select':'单条'},
            '样例api':{'text':api[:180]},'失效原因':{'text':'ingest 入库拦截命中'},
            '失效源数':{'number':1},'判定时间':{'date':now},'复测时间':{'date':now},
            '复测结果':{'select':'待复测'},'拦截次数':{'number':1},
            '备注':{'text':'L7 管线自动拦截'}})
    # 放行部分 → 分类 → 入源总库
    src_recs=[]
    # 幂等双层（S025 实证修复，与失效库侧同构）：exist_src=库内去重；
    # seen_src=批内去重。坑：源总库侧此前完全无判重——四包配置共用同一条
    # 线（jihulab drpy.min.js 被 32 站名引用）时同批全写，源库出现重复行。
    exist_src={str(r.get('api接口',''))[:400] for r in src_rows()}   # S159：源库联合去重
    seen_src=set()
    dup_src=0
    for x in pass_:
        api=x['api接口']; name=x.get('名称') or api
        if api[:400] in exist_src or api[:400] in seen_src:
            dup_src+=1; continue
        seen_src.add(api[:400])
        sys_sel,usys=classify(api)
        # 内容分级（S102·规则56）：候选记录可带 '内容分类'=站点 categories 数组；
        # 无则判"未知"（不进纯净版）。词表见 ADULT_WORDS。
        cats=x.get('内容分类') or x.get('categories')
        grade=classify_adult(cats)
        cats_txt=json.dumps(cats, ensure_ascii=False)[:2000] if cats else ''
        # 分组类目字段类型=select，必须用 {'select':值}；用 {'text':值} 会被平台**静默丢弃**
        # （S027 实证：10-14 起 279 条入库记录该列全空，因误用 text 键）
        src_recs.append({'名称':{'text':name[:80]},'host':{'text':api},
            '分组类目':{'select':x.get('分组类目') or '配置'},
            'api接口':{'text':api[:400]},'入库时间':{'date':now},
            '状态':{'select':'待查'},'体系':{'select':sys_sel},
            '可用体系':{'multi_select':usys},
            '内容分级':{'select':grade},'内容分类':{'text':cats_txt}})

    if a.dry:
        print(f'\n[--dry 演示] 将拦截归档 {len(dead_recs)} 条 → 失效库；放行 {len(src_recs)} 条 → 源总库'
              + (f'（去重跳过 {dup_src} 条同 api 重复）' if dup_src else ''))
        for x in src_recs[:8]:
            print(f'   放行: {x["名称"]["text"][:24]:26} 体系={x["体系"]["select"]} '
                  f'可用={",".join(x["可用体系"]["multi_select"])} 分级={x["内容分级"]["select"]}')
        gc=Counter(x['内容分级']['select'] for x in src_recs)
        if gc:
            print('   内容分级分布：'+' ｜ '.join(f'{k} {v}' for k,v in gc.most_common()))
        if block:
            print(f'   拦截前3: '+' | '.join(b["api接口"][:30] for b in block[:3]))
        print('   （演示模式未写入，去掉 --dry 执行真实入库）')
        return

    d_ok=d_fail=0; s_ok=s_fail=0
    if dead_recs:
        d_ok,d_fail=add_records(DB_DEAD,dead_recs)
    if src_recs:
        # S159·M-R103：一条线路只许住一个库（实测三库 api 零重合，S160 并库后依旧成立）。
        # 落点统一走 `dest_db()`（本文件唯一判据，M-R38）。S160 起 py/csp 合并，
        # 技术形态不再由"库"区分，而是写进 `源类型` 字段（`src_type()` 同一把尺子）。
        by_db={}
        for rec in src_recs:
            api=rec['api接口']['text']
            # ★ S159 修正：原写 `SRC_PROD if api.startswith('csp_') else SRC_PY`
            #   ——会把所有 csp_ 源灌进成品库。改走 `dest_db()`，不再就地写第二份。
            dest=dest_db(api)
            by_db.setdefault(dest,[]).append(rec)
        for dest,recs in by_db.items():
            _o,_f=add_records(dest,recs)
            s_ok+=_o; s_fail+=_f
            print(f'   → {_DEST_NAME[dest]} +{_o}(失败{_f})')
    print(f'\n✅ 入库完成：源库(线路+JSON) +{s_ok}(失败{s_fail}) ｜ 失效库归档 +{d_ok}(失败{d_fail})'
          + (f' ｜ 去重跳过 {dup_src}' if dup_src else ''))
    if (s_ok+d_ok)>0:
        print('   按规则 47 主动触达：刷新权威快照…')
        subprocess.run([sys.executable,__file__,'snapshot'],capture_output=True,text=True)
        cmd_baksync(None)   # S129 P010：入库后自动补同步备用库（联动闭环）

# ---------- S129：bak-sync（备用库与总库实时联动，P010 收尾） ----------
def cmd_baksync(a):
    """bak-sync：SRC→BAK 按 host 差集补同步（写后失效缓存+回读验证）。入库流程末尾自动触发。"""
    import time as _t
    # S159：**旧备用库 G9B1GzfR… 已随 S144 清空并改名废弃**。本命令原会向它写入，
    #   即把数据灌进一个已宣布废弃的库——写完谁也读不到，还占着「备份」的名分。
    #   M-R103 下不存在「同构备份库」这一角色，直接停用并说明。
    print('   ⏹ bak-sync 已停用：旧备用库 G9B1GzfR… 自 S144 起清空废弃；')
    print('      当前源库为「线路源库＋成品源库」，无「同构备份库」角色。')
    print('      备份请走 snapshot（四极现状快照）。')
    return
    # ↓ 以下为 S159 前的旧实现，已不可达（上方 return）。保留仅为对照，
    #   删不删都不影响行为；**不要复活它**——它会往废弃库写数据。
    src = rows_of(DB_SRC)
    bak_hosts = {str(r.get('host') or '').strip() for r in rows_of(DB_BAK)}   # noqa: F821
    todo = [r for r in src if str(r.get('host') or '').strip() not in bak_hosts]
    if not todo:
        print('   ✓ bak-sync：备用库已同步（无缺口）')
        return
    def _w(v, kind):
        v = str(v or '').strip()
        if not v: return None
        if kind == 'date':
            try: _t.strptime(v[:10], '%Y-%m-%d'); return {'date': v[:10]}
            except Exception: return None
        return {kind: v}
    recs = []
    for r in todo:
        p = {}
        for col, kind in [('名称','text'),('host','text'),('api接口','text'),('入库时间','date'),
                          ('状态','select'),('体系','select'),('内容分级','text'),('内容分类','text'),('分组类目','select')]:
            w = _w(r.get(col), kind)
            if w: p[col] = w
        ms = [x.strip() for x in str(r.get('可用体系') or '').replace('，',',').split(',') if x.strip()]
        if ms: p['可用体系'] = {'multi_select': ms}
        recs.append(p)
    ok = fail = 0
    import json as _j, subprocess as _sp
    for i in range(0, len(recs), 100):
        out = _sp.run([sys.executable, f'{LIB}/database/batch_add_database_records.py',
                       '--database-id', DB_BAK, '--records', _j.dumps(recs[i:i+100], ensure_ascii=False)],
                      capture_output=True, text=True).stdout
        for x in _j.loads(out).get('results', []):
            ok += 1 if x.get('success') else 0
            fail += 0 if x.get('success') else 1
    invalidate(DB_BAK)
    print(f'   ✓ bak-sync：+{ok}(失败{fail})，备用库 {len(rows_of(DB_BAK))} 行')
    if fail:
        print('   ⚠️ 有失败件——重跑 tvbox bak-sync 补齐')




# ---------- 生态视图：🧩 技能生态登记库 ----------
def cmd_eco(a):
    eco=rows_of(DB_ECO)
    print(f'🧩 TVBox 技能生态（登记库 {DB_ECO}）共 {len(eco)} 个：\n')
    for r in sorted(eco,key=lambda x:x.get('技能名','')):
        v=r.get('version') or '-'; st=r.get('状态') or ''
        up='🔼可升级' if r.get('可升级')=='是' else '—'
        print(f'  {r.get("技能名","") or "":26} v{v:7} [{st}] {up}')
        print(f'     触发: {r.get("触发词","")}')
        print(f'     依赖: {r.get("依赖","")}')

# ---------- 内容分级三命令（S102·规则56）----------
def cmd_grade(a):
    """打标预览：读站点 json，逐站算分级，打印四态计数（**不改库**）。"""
    d=_read_json_any(a.candidates)
    if d is None:
        print(f'读取失败：{a.candidates}'); return
    sites=d.get('sites') if isinstance(d,dict) else d
    if not isinstance(sites,list):
        print('未找到 sites 数组'); return
    c=Counter(); rows=[]
    for s in sites:
        if not isinstance(s,dict): continue
        cats=s.get('categories')
        g=classify_adult(cats)
        c[g]+=1
        hit=[x for x in (cats or []) if any(w in str(x) for w in ADULT_WORDS)]
        rows.append((s.get('name') or s.get('api') or '?', g, len(cats or []), hit[:4]))
    print(f'═══ 内容分级预览：{len(sites)} 站 ═══')
    print(f'  纯净 {c["纯净"]} ｜ 全量 {c["全量"]} ｜ 18 {c["18"]} ｜ 未知 {c["未知"]}')
    for want in ('18','全量','未知','纯净'):
        sub=[r for r in rows if r[1]==want]
        if not sub: continue
        print(f'\n  ── {want}（{len(sub)}）──')
        for nm,g,n,hit in sub[:a.limit]:
            extra=f' 命中:{hit}' if hit else ('' if n else ' (无categories)')
            print(f'     [{g}] {str(nm)[:34]:36} 分类{n}个{extra}')
        if len(sub)>a.limit:
            print(f'     … 另有 {len(sub)-a.limit} 条')
    return 0


def cmd_tag_backfill(a):
    """回填老数据：对库内缺"内容分级"的记录补标。

    源库未存 categories → 无 categories 的只能标"未知"（**绝不瞎猜**）。
    可选 --map 提供 api→categories 映射（先从站点包导入可提高回填率）。
    注：rows_of 不含 record_id，故本命令用分页查询拿 id。
    """
    # S159：逐个源库分页读（原来只读已清空的旧库 → 永远「0 条，无需回填」）
    # ★ S158：任一源库读失败即中止。旧写法 `allrows.extend(_paged_records(_db))` 在两库
    #   时最危险——其中一个读失败返回空，基线就只用另一半算，
    #   回填统计「缺分级 X 条」是**错的**，而没人看得出来。
    allrows=[]
    for _nm,_db in SRC_DBS:
        _r = _paged_records(_db)
        if _r is None:
            print(f'⏹ 中止：源库「{_nm}」读取失败，无法建立完整基线'
                  f'（这不是"该库无记录"）。请先修复鉴权/网络后重跑。')
            return 3
        allrows.extend(_r)
    rows=allrows
    todo=[r for r in rows if not r.get('内容分级')]
    print(f'源库 {len(rows)} 条，其中缺"内容分级" {len(todo)} 条')
    amap={}
    if a.map and os.path.exists(a.map):
        amap=json.load(open(a.map,encoding='utf-8'))
        print(f'  载入 api→categories 映射 {len(amap)} 条')
    recs=[]
    for r in todo:
        api=str(r.get('api接口',''))
        cats=amap.get(api)
        g=classify_adult(cats)
        recs.append({'record_id':r['record_id'],
                     '_api':api,
                     'properties':{'内容分级':{'select':g},
                                   '内容分类':{'text':json.dumps(cats,ensure_ascii=False)[:2000] if cats else ''}}})
    c=Counter(x['properties']['内容分级']['select'] for x in recs)
    print(f'  待回填分级分布：'+' ｜ '.join(f'{k} {v}' for k,v in c.most_common()))
    if a.dry:
        print('  [--dry] 未写入。去掉 --dry 执行回填。')
        return 0
    ok=fail=0
    for i in range(0,len(recs),50):
        chunk=recs[i:i+50]
        # ★S160 修：原写 `--database-id DB_SRC`，而 DB_SRC 是**标签字符串**（'[联合:...]'），
        #   不是库 id → 每次回填都是「写一个不存在的库」，failed 静默计入。
        #   已改为按 api 逐条分派到真实库（并库后只剩线路源库/成品源库两个落点）。
        by_db={}
        for rec in chunk:
            api=rec.pop('_api', '')
            by_db.setdefault(dest_db(api),[]).append(rec)
        for _db,rs in by_db.items():
            # S158 收敛：改走 core.update_records（统一三态 + properties 结构检测）。
            # 原实现对解析失败 `fail+=len(rs)`，把「通路坏了」记成「这 N 条被拒」，
            # 人会照着失败数去重写已经成功的数据。
            try:
                import core as _c
                _u_ok, _u_n, _u_info = _c.update_records(_db, rs)
                if _u_ok:
                    ok += _u_n
                else:
                    fail += len(rs) - max(0, _u_n or 0)
                    print(f'   ⚠️ 库 {_db} 更新未全部确认：{_u_info[:120]}')
            except Exception as _e:
                fail += len(rs)
                print(f'   ❌ 库 {_db} 更新通路不可用（{type(_e).__name__}: {str(_e)[:60]}）'
                      f'——这 {len(rs)} 条是「未判定」，不是「被拒」')
    for _nm,_db in SRC_DBS:
        invalidate(_db)
    print(f'  ✅ 回填完成：成功 {ok} / 失败 {fail}')
    return 0


def _paged_records(db):
    """分页读全表（含 record_id），供需 id 的写操作使用。

    ★ S158 收敛：改走 `core.query_all`——**这一处原本是"半张表当全表"的典型**：
      旧实现里 `except: break` 直接跳出，返回**已经读到的那几页**，
      调用方拿它当"全部记录"，于是按它算出的增删就会漏掉后面几页。
      更隐蔽的是参数写错时服务端不报错、每次返回首页，循环会把同一页追加 N 次
      （probe-hub S110 踩过，`core.query_all` 里已有"游标不前进即退"的闸门）。

    三态：成功返回 list（可能为空表=真空库）；**失败返回 None**——
      调用方必须判断，不得当空列表用。
    """
    try:
        import core as _c
    except Exception as e:
        print(f'❌ 读表不可用：core 导入失败（{type(e).__name__}: {str(e)[:60]}）')
        return None
    ok, rows, info = _c.query_all(db, page_size=100, max_pages=60)
    if not ok:
        print(f'❌ 读表失败（{db}）：{info[:150]}')
        print('   ⚠️ 这**不是"表是空的"**——禁止按空表继续（会漏算后续增删）。')
        return None
    return rows


def cmd_export(a):
    """★ 出库：按内容分级筛选，组装「全量版 / 纯净版 / 18版」json。

    --level all|pure|adult → 库内标签 全量|纯净|18
    **纯净版铁律**：`未知` 与 `全量` 都不进纯净版——只有"纯净"才进（防漏网18源）。
    """
    LV={'all':'全量','pure':'纯净','adult':'18'}
    if a.level not in LV:
        print(f'--level 必须是 all|pure|adult（收到 {a.level}）'); return 1
    want=LV[a.level]
    rows=src_rows()      # S159：源库联合
    keep=[r for r in rows if r.get('内容分级')==want]
    sites=[]
    for r in keep:
        api=str(r.get('api接口',''))
        raw=r.get('内容分类') or ''
        if not raw:
            cats=[]
        else:
            try:
                cats=json.loads(raw)
            except Exception as e:
                # M-R60：读失败不静默折叠成空态——显式告警并标空（供人工复核）
                print(f'   ⚠️ 内容分类解析失败（按空处理）：{api[:60]} — {e}')
                cats=[]
        # 纯净版：剔除所有18分类（双保险，即便标注有误）
        if a.level=='pure':
            cats=[c for c in cats if not any(w in str(c) for w in ADULT_WORDS)]
        # 18版：只留18分类
        elif a.level=='adult':
            cats=[c for c in cats if any(w in str(c) for w in ADULT_WORDS)]
        sites.append({'name':r.get('名称') or api,'api':api,'categories':cats})
    out={'sites':sites}
    op=a.out or f'{a.level}_版.json'
    json.dump(out,open(op,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
    print(f'═══ 出库「{want}版」═══')
    print(f'  筛得 {len(sites)} 站 → {op}')
    cc=Counter(r.get('内容分级') for r in rows)
    print(f'  源库分级分布：'+' ｜ '.join(f'{k or "(空)"} {v}' for k,v in cc.most_common()))
    return 0


def _read_json_any(p):
    if not p or not os.path.exists(p): return None
    try:
        return json.load(open(p,encoding='utf-8-sig'))
    except Exception:
        try: return json.load(open(p,encoding='utf-8'))
        except Exception: return None


def build_parser():
    ap=argparse.ArgumentParser(prog='link.py')
    sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('status'); sub.add_parser('sync'); sub.add_parser('brief')
    sub.add_parser('audit'); sub.add_parser('sizes')
    sp=sub.add_parser('snapshot'); sp.add_argument('--dry',action='store_true')
    p=sub.add_parser('learn'); p.add_argument('keyword')
    p2=sub.add_parser('lib-learn'); p2.add_argument('keyword')
    l2=sub.add_parser('lib-log')
    l2.add_argument('--scene',required=True)
    l2.add_argument('--type',default='记录读写',choices=[
        '上传入库','表格编辑','字段管理','记录读写','分页查询','节点移动改名','建库建夹','链接分享'])
    l2.add_argument('--pit',default=''); l2.add_argument('--fix',default='')
    l2.add_argument('--reuse',default=''); l2.add_argument('--api',default='')
    l2.add_argument('--space',default='')
    l2.add_argument('--sev',default='耗时',choices=['阻断','耗时','轻微'])
    l=sub.add_parser('log')
    # ★ S158 修（M-R85 单一事实源 / M-R60 空值折叠的混合体）：
    #   原 choices 只有 6 类，而 brake.py 的 GOV_KINDS 含 `生态治理`、BIZ_KINDS 含
    #   `出库交付/补源/失效复测`——**这四个值根本写不进去**。
    #   实测台账 109 条里这四个类型出现 0 次，于是：
    #     · 治理刹车在统计一个永远不会出现的枚举值；
    #     · 真正的"出库交付/补源"业务活只能被记成别的类型 → 业务占比被系统性低估；
    #     · 熔断次数因此虚高（本轮实测 11，其中含 2 条空值 + 分类错配）。
    #   修法：两处枚举合为一个真源，`--type` 直接引用 brake 的定义（同源派生，禁手抄）。
    l.add_argument('--summary',required=True); l.add_argument('--type',default='查询答疑',
        choices=_LEDGER_TYPES)
    l.add_argument('--src',default=''); l.add_argument('--output',default='')
    l.add_argument('--pit',default=''); l.add_argument('--fix',default='')
    l.add_argument('--reuse',default=''); l.add_argument('--pkg',default='')
    l.add_argument('--nsrc',type=int,default=0)
    l.add_argument('--status',default='已闭环',
        choices=['已闭环','已同步源库','已同步底包','待同步'])
    ing=sub.add_parser('ingest'); ing.add_argument('--candidates',default='/tmp/to_add.json')
    ing.add_argument('--dry',action='store_true')
    # 内容分级三命令（S102·规则56）
    gr=sub.add_parser('grade'); gr.add_argument('--candidates',required=True)
    gr.add_argument('--limit',type=int,default=20)
    bf=sub.add_parser('tag-backfill'); bf.add_argument('--dry',action='store_true')
    bf.add_argument('--map',default='')
    ex=sub.add_parser('export'); ex.add_argument('--level',required=True,choices=['all','pure','adult'])
    ex.add_argument('--out',default='')
    sub.add_parser('eco')
    sub.add_parser('baksync')  # S129 P010：备用库补同步独立入口
    return ap

AP=build_parser()
DISPATCH={'status':cmd_status,'learn':cmd_learn,'log':cmd_log,'sync':cmd_sync,'brief':cmd_brief,
          'lib-learn':cmd_liblearn,'lib-log':cmd_liblog,
          'snapshot':cmd_snapshot,'audit':cmd_audit,'ingest':cmd_ingest,'eco':cmd_eco,
          'grade':cmd_grade,'tag-backfill':cmd_tag_backfill,'export':cmd_export,
          'sizes':cmd_sizes,'baksync':cmd_baksync}

def dispatch(argv=None):
    """供调度器 tvbox 在同进程内直接调用，共享 core 缓存与并行池。"""
    a=AP.parse_args(argv)
    # ★ S165 修：原来 `DISPATCH[a.cmd](a)` 不接返回值 —— link 系**所有**命令的
    #   `return 2/1` 全被吞掉，入口恒判 exit=0。
    #   实测：`tvbox ingest`（缺候选文件）打印报错后 `$?` 仍是 0；
    #   而 ingest 在 `ledger call` 里靠退出码判成败，于是"一个字都没入库"被判 ✅ok。
    #   与账房 `main()`、conductor `cmd_whoami` 是同族错误（判定算对了，没交到下游）。
    #   一句 `return` 就够，别在别处再加一层归一（入口 `_as_exit_code` 已经处理 bool/int）。
    return DISPATCH[a.cmd](a)

def main():
    # ★ S177：`dispatch()` 的返回值原来被丢弃 → 直接 `python3 xxx.py` 跑时恒 rc=0。
    #   同一处病在 dispatch 里修过，但**出口这一层没修，等于白修**——
    #   经 `tvbox` 入口调用时值能传出去，直接跑脚本时不能，两条路径行为不一致。
    #   M-R63 的变体：修了上游、漏了下游，检测时只看上游就会以为已经好了。
    return dispatch()

if __name__=='__main__': sys.exit(main() or 0)
