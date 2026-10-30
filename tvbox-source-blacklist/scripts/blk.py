#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
失效源治理引擎：按 host 标记拉黑 → 新源入库前拦截 → 命中者直接进失效库

子命令:
  mark                      从 probe_src.py 实测结果提取失效 host/单条，写入失效库
  check <api>               检查某条源是否命中拉黑（入库前拦截用，退出码 0=放行 1=拦截）
  guard <候选json路径>       批量拦截：读 /tmp/to_add.json，拆成 放行 / 拦截 两份
  list [--host 关键字]       列出失效库
  review [--limit N]         复测失效 host，判断是否复活（复活则从库移除）
  stats                     失效库统计

失效库 database: hGwE41OpJDOR8CW0sLUwRx
"""
import os, sys, json, time, argparse, subprocess
from urllib.parse import urlparse
from collections import Counter

LIB='/root/.codebuddy/skills/library'
DB_DEAD='hGwE41OpJDOR8CW0sLUwRx'   # 🗑 失效源库
# S159：源库已非单库。原 DB_SRC 指向 S144 已清空的旧库 →
#   `blk mark`（整域拉黑）在空集上算 ha/hd，`full=[]` → 打印「无新增」，
#   **整域拉黑整体失效且不报错**；`blk archive` 读源库状态表也为空，
#   把所有复活源判成「源库无此条」；回流则写进废弃库。
DB_SRC='[源库联合]'                # 仅作标签，勿用于读/写
PROBE='/tmp/probe_result.json'
UA='okhttp/3.12.0'

# 共享效率核心（S123 收窄：仅同一进程内与 link/evolve 共享 core 模块实例；跨技能 CLI 单命令调用时各自独立实例）
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from core import (rows_of, add_records, add_records_parallel,

                  invalidate, file_to_janitor, register_monitor,
                  SRC_DBS, src_rows, src_rows_t, http_get)  # noqa: E402  S159：源库联合

# ---------- M-R114 统一并发入口（S110） ----------
# 本文件原先就地写死 max_workers=N，数字无依据。改为按档位表取上限。
# 保留一个同名 shim，避免改动所有调用点（改动面越小越安全）。
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded as _run_bounded, TIER_WEB  # noqa: E402


# ★ 生态联动（S106 补接门面）：
#   · 拉黑确认后，源总库里对应的「失效孤儿节点」AI 删不掉 → core.file_to_janitor 投递清道夫
#   · 黑名单出现新拦截类型（新失效模式）→ core.register_monitor 登记监察司，下轮 scan 自动接手
def _eco_linked_probe():
    """自证已接门面：返回 (清道夫, 监察司) 可达性，供 inspect 联动覆盖判据与自检。"""
    return (callable(file_to_janitor), callable(register_monitor))

def host_of(api):
    try: return urlparse(api).netloc
    except Exception: return ''

def _api_key(api):
    """把 api 规范化成**入口级身份键**，供「失效库 ↔ 源总库」对齐用（S139 实踩）。

    为什么不能用完整 URL 做相等比较：同一入口带不同 access 变体，字符串就不相等——
      · 源库存 `https://ckzy.me/api.php/provide/vod/?ac=list`
      · 失效库存 `https://ckzy.me/api.php/provide/vod/`
    两条其实是**同一条源的两个视图**，却被判「源库无此条」，于是真活的源
    永远卡在失效库里出不去，watchdog 再报一遍「该停用没停用」。
    这和 `_dead_id(host)` 只按 host 匹配是同一类病：**身份键选错位**——
    一个取太宽（同 host 不同仓库全撞一起），一个取太窄（同入口不同参数认成两条）。
    正确粒度是「host + 资源路径」，丢掉 query 与尾部 access 变体。
    """
    u=(api or '').strip()
    if not u:
        return ''
    try:
        p=urlparse(u)
    except Exception:
        return u
    host=(p.netloc or '').lower()
    if not host:
        return u
    path=p.path or '/'
    # 尾部 access 变体（/at/json/、/from/xxx/、/ac/list/）不是身份的一部分
    for tail in ('/at/json/', '/at/xml/', '/at/json', '/at/xml'):
        if path.endswith(tail):
            path=path[:-len(tail)]+'/'
    # 统一成「目录级」：以 / 结尾则去掉尾斜杠，避免 /vod 与 /vod/ 认成两条
    path=path.rstrip('/') or '/'
    return host+path


# 代理型域名（S026 实证）：这类 host 是「通用转发代理」，其可达性由**被代理的目标
# URL** 决定，与代理域名本身无关。把众多被代理目标各自的失效笼统归因到代理域名
# → 误判「整域全灭」，实测 gh-proxy.com 下正常源被误拉黑（28 条，抽样 6 条全部 200）。
# 处置：代理型 host 永不参与整域拉黑（只能走单条）；判据＝host 后紧跟另一个 http(s)://
PROXY_HINT=('gh-proxy','ghproxy','ghfast','gh.halonice','ghfile','gitmirror','kgithub','fastgit','ghp.ci','gh.api')

# 公共平台域名（S151 实证）：这类 host 是**公共托管平台**，一个域下挂的是**无数互不相关的
# 内容发布者**——失效的是某个发布者的某条路径，不是这个平台。整域拉黑会把整个平台判死。
# 事故：`raw.githubusercontent.com` 因**2 条** m3u 失效被判「整域」，于是 `blk check`
# 对**任何** GitHub raw 链接一律 🚫 拦截（连 Guovin/iptv-api 这种活跃仓库也被拦）。
# 这比代理型更隐蔽——代理型失效归因于被代理目标，平台型归因于**某个路径**。
# 处置：平台型 host 永不参与整域拉黑（只能走单条，按「host+path 目录」精确拦）。
#
# **S159 补**：`cnb.cool`、`git.yylx.win` 是同类通用代码托管域（前者腾讯云 CNB，
# 后者自建 Git），一个域下同样是无数互不相关的仓库。事故：`git.yylx.win` 的 5 条
# 记录写着 `失效原因=单源失效`、`失效源数=1`，`拉黑范围` 却被填成「整域」——
# 数据自相矛盾，后果是该域下**全部存活源**被一并拦死（含官方线路输出）。
# 判据补上这两个域后，历史脏数据在读侧自动失效，无需逐条改表。
# 教训（M-R88 同族）：**平台的边界不写全 = 判据残缺**，不是「暂时够用」。
PLATFORM_HINT=('raw.githubusercontent.com','githubusercontent.com','github.com','gitee.com',
               'gitlab.com','jsdelivr.net','unpkg.com','gitcode.com','gitea.com',
               'objects.githubusercontent.com','codeload.github.com',
               'cnb.cool','git.yylx.win')

def is_platform_host(h):
    """公共平台域判定：整域拉黑会误伤同域的无关发布者，故只允许单条拉黑。"""
    h=str(h or '').strip().lower()
    if not h: return False
    return any(h==p or h.endswith('.'+p) for p in PLATFORM_HINT)

def is_proxy_host(h):
    """代理型 host 判定（纯域名口径，供读侧过滤整域表用）。
    注意与 `is_proxy_api` 分开：那个看「path 里嵌了第二条 URL」，这里只看域名族。"""
    h=str(h or '').strip().lower()
    return bool(h) and any(p in h for p in PROXY_HINT)

# ---------- ③ 失效原因枚举化（v1.1.0）----------
# **S159 修：枚举常量必须与表 schema 取值域逐字一致（M-R99）。**
# 事故：这四个常量写作 `整域全灭(host所有源实测失效)` / `部分失效(仅本条失效)` /
# `解析失败(URL无host)` / `已复活(复测确认,待回流)`——全是**带括号注释的描述句**，
# 而表里的 select 选项只有 `整域全灭`/`单源失效`/`假活`/`入库拦截命中`/
# `严格采样误判`/`URL无法解析`/`人工拉黑`。于是每次 `blk retest --apply` 走到
# 回写「已复活」都会被服务端判 `invalid fields` —— 静默失败，复活结论从不落库，
# 死源永远停在「仍失效」，下一轮复测又把同一批全部重跑一遍。
# 判据改法：常量只存**表里真有的字面值**；需要解释写注释，不写进取值。
REASON_FULL='整域全灭'                 # host 下所有源实测失效
REASON_PART='单源失效'                 # 仅本条失效（同 host 其他源仍活）
REASON_PARSE='URL无法解析'             # URL 取不出 host
REASON_REVIVE='人工拉黑'               # 保留仅为兼容旧引用；复活态见下方 REVIVE_*
# 复活三态（表里实际支持的组合）：
#   拉黑范围=观望 + 判死状态=已平反 + 复测结果=已恢复   ← 复活但待回流复核
# 旧代码写的「已复活(复测确认,待回流)」在表里不存在，属**写了等于没写**。
REVIVE_RANGE='观望'
REVIVE_STATE='已平反'
REVIVE_RETEST='已恢复'

def _dead_id(host):
    """按 host 定位失效库 record_id（rows_of 无 id，需 query 子进程）。"""
    cur=None; guard=0
    while True:
        cmd=['python3',f'{LIB}/database/query_database_record.py','--database-id',DB_DEAD,
             '--page-size','100']
        if cur: cmd+=['--start-cursor',cur]
        d=json.loads(subprocess.run(cmd,capture_output=True,text=True).stdout)
        for r in d.get('results',[]):
            if (r.get('host') or '')==host: return r['record_id']
        if not d.get('has_more'): break
        cur=d.get('next_cursor'); guard+=1
        if guard>25: break
    return None

def _dead_id_by_api(api):
    """按**样例 api** 精确定位 record_id（S139）。

    为什么必须按 api 而不能按 host：单条类失效的对象是「某一条线路」，同一 host 下
    往往有多条记录。实测 `raw.githubusercontent.com` 下同时挂着 aptv 与 gambiarras
    两个仓库的 m3u——`_dead_id(host)` 只会返回第一条，于是「验 A、写 B」，
    账面上看起来回写了，实际改的是另一条。**身份键用错，比不回写更坏**：
    它会静默地篡改无关记录，且不报错。
    """
    api = (api or '').strip()
    if not api:
        return None
    cur=None; guard=0
    while True:
        cmd=['python3',f'{LIB}/database/query_database_record.py','--database-id',DB_DEAD,
             '--page-size','100']
        if cur: cmd+=['--start-cursor',cur]
        d=json.loads(subprocess.run(cmd,capture_output=True,text=True).stdout)
        for r in d.get('results',[]):
            if (r.get('样例api') or '').strip()==api: return r['record_id']
        if not d.get('has_more'): break
        cur=d.get('next_cursor'); guard+=1
        if guard>25: break
    return None


def _update_dead(record_id, props):
    """更新失效库记录（S158：改走 core.update_records）。

    ★ 原实现返回 `r.returncode==0` —— **这是错的**：library 的批处理脚本
      在"接口通了但记录没更新成功"时**仍返回 0**（失败信息在 results[i].success 里）。
      实测这意味着"更新失败会被当成成功"，是一处静默假绿。
      core.update_records 同时看 returncode **和** results 里的 success，两个都过才算成功。

    另：原注释写「core 无 update 能力」——S158 已补 `core.update_records`，该前提作废。
    """
    try:
        import core as _c
    except Exception as e:
        print(f'   ❌ 更新不可用：core 导入失败（{type(e).__name__}: {str(e)[:60]}）')
        return False
    ok, n, info = _c.update_records(DB_DEAD, [{'record_id': record_id, 'properties': props}])
    if not ok:
        print(f'   ⚠️ 失效库更新未确认：{info[:140]}')
    return ok

def is_proxy_api(api):
    """判定「代理套代理」形态：本 URL 的 path 里又嵌了一条完整 http(s) URL。"""
    a=str(api or '')
    for scheme in ('http://','https://'):
        i=a.find(scheme, a.find('://')+3)
        if i!=-1: return True
    return any(p in host_of(a).lower() for p in PROXY_HINT)

# ---------- mark：从实测结果标记拉黑 ----------
def cmd_mark(a):
    if not os.path.exists(PROBE):
        print(f'缺少实测结果 {PROBE}，先跑 probe_src.py'); return
    pr=json.load(open(PROBE,encoding='utf-8'))
    st={x['api接口']:x['状态'] for x in pr}
    ok, src, _si = src_rows_t()          # S159：源库联合 + 三态
    if not ok or not src:
        # 整域拉黑依赖「同 host 的所有源都死了」这一全局判据，源库读不全
        # 会把「只看到 1 条且死了」误判成「整个 host 全灭」——宁可不动手。
        print(f'❌ 源库读取不完整（{_si.get("failed")}）或为空 → 中止整域拉黑。')
        print('   理由：整域判定必须看全 host 下所有源，读不全就会误杀整域。')
        return
    http=[r for r in src if str(r.get('api接口') or '').strip().startswith('http')]
    ha=Counter(); hd=Counter()
    dead_api=[]; host2api={}
    for r in http:
        api=r['api接口'].strip(); h=host_of(api)
        ha[h]+=1; host2api.setdefault(h,api)
        if st.get(api)=='停用':
            hd[h]+=1; dead_api.append((h,api))
    full=[h for h in ha if h and hd[h]==ha[h]]
    # S026 防护：代理型 host 剔出整域拉黑（其失效归因于被代理目标，非代理域名本身）
    # S151 追加：公共平台型 host 同样剔出（其失效归因于某个发布者的某条路径，
    #   整域拉黑会把整个平台判死——raw.githubusercontent.com 曾因此被 2 条 m3u 判死）
    proxy_hosts={h for h in full if is_proxy_api(host2api.get(h,''))}
    plat_hosts={h for h in full if is_platform_host(h)}
    if proxy_hosts:
        print(f'  ⚠️ 代理型 host 跳过整域拉黑 {len(proxy_hosts)} 个：{", ".join(sorted(proxy_hosts))}')
        print('     （原因：代理域名可达性由被代理目标决定，整域拉黑会误伤同域正常源）')
    if plat_hosts:
        print(f'  ⚠️ 公共平台型 host 跳过整域拉黑 {len(plat_hosts)} 个：{", ".join(sorted(plat_hosts))}')
        print('     （原因：平台域下是无数互不相关的发布者，整域拉黑会把整个平台判死）')
    full=[h for h in full if h not in proxy_hosts and h not in plat_hosts]
    part_apis=[(h,api) for h,api in dead_api if h and hd[h]<ha[h]]
    empty_apis=[(h,api) for h,api in dead_api if not h]
    print(f'http源 {len(http)} 条 | 涉及 host {len(ha)}')
    print(f'  整域全灭 host: {len(full)}（覆盖 {sum(ha[h] for h in full)} 条源）')
    print(f'  部分失效单条: {len(part_apis)}')
    print(f'  host为空单条: {len(empty_apis)}')
    # 已存在的不再重复写
    exist={r['host'] for r in rows_of(DB_DEAD)}
    exist_api={r['样例api'] for r in rows_of(DB_DEAD)}
    now=time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())
    recs=[]
    for h in full:
        if h in exist: continue
        recs.append({'host':{'text':h},'拉黑范围':{'select':'整域'},
            '样例api':{'text':host2api[h][:180]},'失效原因':{'text':REASON_FULL},
            '失效源数':{'number':ha[h]},'判定时间':{'date':now},
            '复测时间':{'date':now},'复测结果':{'select':'未复测'},
            '拦截次数':{'number':0},'备注':{'text':'2026-10-15 全量实测批次'}})
    for h,api in part_apis:
        if api in exist_api: continue
        recs.append({'host':{'text':h},'拉黑范围':{'select':'单条'},
            '样例api':{'text':api[:180]},'失效原因':{'text':REASON_PART},
            '失效源数':{'number':1},'判定时间':{'date':now},
            '复测时间':{'date':now},'复测结果':{'select':'未复测'},
            '拦截次数':{'number':0},'备注':{'text':'保守拉黑，同 host 其他源仍可用'}})
    for h,api in empty_apis:
        if api in exist_api: continue
        recs.append({'host':{'text':'(解析失败)'},'拉黑范围':{'select':'单条'},
            '样例api':{'text':api[:180]},'失效原因':{'text':REASON_PARSE},
            '失效源数':{'number':1},'判定时间':{'date':now},
            '复测时间':{'date':now},'复测结果':{'select':'未复测'},
            '拦截次数':{'number':0},'备注':{'text':'host 为空，不适用整域拉黑'}})
    if not recs:
        print('失效库已包含全部条目，无需新增'); return
    ok,fail=add_records(DB_DEAD,recs)
    after=rows_of(DB_DEAD)
    print(f'写入 成功 {ok} | 失败 {fail} | 失效库现有 {len(after)} 条')
    # ★ 生态联动（S106）：
    #   ① 拉黑后源总库中这些 host 的源已成孤儿（仍在库但已被拦），AI 删不掉的节点投递清道夫
    #   ② 首次出现的新失效原因类型 → 登记监察司风险面（下轮 scan 自动接手监测）
    try:
        # **S156 修：这里原本在向清道夫投递「描述文字」，是拿错了工具。**
        # 两点错：① `file_to_janitor` 的第一个参数必须是云端 node_id，
        #          传 `f'源总库·整域拉黑孤儿 host={h}'` 这种描述文字永远移动不了；
        #        ② 更根本——「拉黑孤儿」是**源总库的数据库记录**，不是云端节点，
        #          清道夫只管云端节点，它压根不该走这条路。
        # 正确处置：数据库记录由 M09 检查器盯（源库状态 vs 失效库的双向对账），
        # 本处只登记监察司风险面，让下轮 scan 自动接手。
        # （S151 已实证：这类孤儿正是 M09 报出的 158 项僵尸源，已回填「停用」。）
        register_monitor('黑名单新失效类型', 'naming_compliance', '质检闸拉黑出现新失效原因分类时，监测册接手跟踪')
        if full:
            print(f'  ℹ️ 整域拉黑孤儿 {len(full)} 个 host 属源库记录，由 M09 双向对账盯（tvbox inspect scan），非云端节点不走清道夫')
    except Exception as e:
        print(f'  ⚠️ 生态门面联动跳过（{type(e).__name__}）：清单已落库，不影响拉黑结果')

# ---------- check：入库前拦截判定 ----------
def blocked_set():
    dead=rows_of(DB_DEAD)
    # **S150 修：枚举值必须「兼容新旧」**。
    # 事故链：①搬库时本文件仍读旧空库（已修）→ stats 0 条；
    #        ②切到新库后仍全部放行，因为新库把 `拉黑范围` 枚举从
    #          `整域(host全灭)/单条(部分失效)/单条(host为空)` 简化成 `整域/单条`，
    #          而这里写的是 `== '整域(host全灭)'` 精确相等 → hosts 集合恒为空。
    # 判据改法：按**前缀**归类（`整域` 开头即整域，`单条` 开头即单条），
    # 新旧两套枚举同时兼容；枚举再改也只需前缀不变。
    # 教训（M-R99 同族）：**select 的取值域是外部契约，代码不许按记忆写死**；
    # 搬库/改枚举后必须拿真实数据跑一次「已知黑名单 host 能否拦住」。
    hosts={r['host'] for r in dead if str(r.get('拉黑范围') or '').startswith('整域') and r.get('host')}
    apis={r['样例api'] for r in dead if str(r.get('拉黑范围') or '').startswith('单条') and r.get('样例api')}
    # **S151 修：读侧也要挡「整域表里混进的平台域 / 代理域」**。
    # 写侧早已（S026 代理）/现已（S151 平台）不再把这两类写成整域，
    # 但**存量脏数据还在表里**——只改写侧等于「以后不再犯」，今天仍然全拦：
    #   · `raw.githubusercontent.com` 2 条整域 → 任何 GitHub raw 链接一律 🚫
    #   · `gh-proxy.com`/`ghfast.top`/`fastgit.cc` 12 条整域 → 任何代理链接一律 🚫
    # 读侧过滤是唯一能立刻止血的地方（写侧修的是未来，读侧修的是现在）。
    # 两类判据与写侧**同一函数**，避免两处口径漂移。
    hosts={h for h in hosts if not is_platform_host(h) and not is_proxy_host(h)}
    return hosts,apis

def is_blocked(api):
    hosts,apis=blocked_set()
    if api in apis: return ('单条',api)
    h=host_of(api)
    if h and h in hosts: return ('整域',h)
    return None

def cmd_check(a):
    r=is_blocked(a.api)
    if r:
        print(f'🚫 拦截：命中{r[0]}拉黑 → {r[1]}')
        print('   处置：不进源总库，直接归档失效库')
        sys.exit(1)
    print(f'✅ 放行：未命中任何拉黑规则')
    sys.exit(0)

# ---------- guard：批量拦截（入库前） ----------
def cmd_guard(a):
    p=a.candidates or '/tmp/to_add.json'
    if not os.path.exists(p):
        print(f'缺少候选文件 {p}'); return
    cands=json.load(open(p,encoding='utf-8'))
    pass_=[]; block=[]
    for x in cands:
        api=x['api接口']
        r=is_blocked(api)
        # 透传候选的透传字段（分组类目 v1.0.4 起；内容分类 S102 起）——
        # 否则下游 link.ingest 取不到站点 categories，内容分级只能全判"未知"。
        item={'api接口':api,'名称':x.get('名称',''),
              '分组类目':x.get('分组类目',''),
              '命中':(r[0] if r else ''),'依据':(r[1] if r else '')}
        if x.get('内容分类') is not None:
            item['内容分类']=x.get('内容分类')
        (block if r else pass_).append(item)
    json.dump(pass_,open('/tmp/guard_pass.json','w',encoding='utf-8'),ensure_ascii=False)
    json.dump(block,open('/tmp/guard_block.json','w',encoding='utf-8'),ensure_ascii=False)
    # ① 拦截后自动快照归档（v1.1.0）：拦截清单按日留档，防 /tmp 易失
    if block:
        ar=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','block_archive')
        os.makedirs(ar,exist_ok=True)
        af=os.path.join(ar,f'{time.strftime("%Y-%m-%d")}.json')
        hist=[]
        if os.path.exists(af): hist=json.load(open(af,encoding='utf-8'))
        hist.append({'ts':time.strftime('%Y-%m-%dT%H:%M:%S'),'n':len(block),'items':block})
        json.dump(hist,open(af,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
        print(f'  📸 拦截快照已归档 {af}（累计 {sum(len(h["items"]) for h in hist)} 条）')
    print(f'候选 {len(cands)} → 放行 {len(pass_)} | 拦截 {len(block)}')
    if block:
        print('\n拦截明细（前10）:')
        for x in block[:10]:
            print(f'  [{x["命中"]}] {x["名称"][:22]:24} {x["api接口"][:52]}')
        print(f'\n拦截清单已写 /tmp/guard_block.json —— 这些直接归档失效库，不进源总库')
    print(f'放行清单已写 /tmp/guard_pass.json —— 这些才进源总库')

# ---------- list / stats / review ----------
def cmd_list(a):
    dead=rows_of(DB_DEAD)
    if a.host:
        dead=[r for r in dead if a.host.lower() in (r.get('host','')+r.get('样例api','')).lower()]
    print(f'失效库条目：{len(dead)}')
    for r in dead[:40]:
        print(f'  [{r.get("拉黑范围","")}] {r.get("host",""):32} 源数{str(r.get("失效源数","")):>3} 复测:{r.get("复测结果","")}')
    if len(dead)>40: print(f'  … 另有 {len(dead)-40} 条')

def cmd_stats(a):
    dead=rows_of(DB_DEAD)
    print('🗑 失效源库统计')
    print(f'  总条目 {len(dead)}')
    for k,v in Counter(r.get('拉黑范围','') for r in dead).most_common():
        print(f'    {v:4}  {k}')
    tot=sum(int(r.get('失效源数') or 0) for r in dead)
    print(f'  覆盖失效源 {tot} 条')
    for k,v in Counter(r.get('复测结果','') for r in dead).most_common():
        print(f'    复测 {v:4}  {k}')
    print('  按失效原因（枚举）:')
    for k,v in Counter(r.get('失效原因','')[:18] for r in dead).most_common():
        print(f'    {v:4}  {k}')

def probe_one(h):
    """单 host 存活探测。返回 (host, http_code)；连不上给 '000'，异常给 'ERR'。

    ★ S177 步4 实测修（性能）：**连接与读取必须分开限时**。
      原写 `-m 8`（总超时 8 秒），实测 145 个整域 host 全量复测要 **47.4 秒**，
      冒烟限时 15 秒，必然超时。逐条拆开看了 26 个慢 host，全部是这个形态：

        connect=0.000000  total=8.002262  code=000
        curl: (28) Connection timed out after 8002 milliseconds

      `time_connect=0.000000` 说明**连接阶段一毫秒都没建起来**——一个字节的响应都没收到，
      8 秒全部烧在"等建连"上。对**已经进了失效库的域名**来说，等 8 秒换回"连不上"，
      等于花 8 秒证明一件早就知道的事。

      为什么不是"并发不够"：档位 B = 6 并发是 M-R114 定的，26×8/6 ≈ 35 秒，
      实测 47.4 秒，数字对得上——**瓶颈是每条的等待时长，不是并行度**。
      加并发反而会打到别人服务器上被判爬虫（B 档注释里写着的理由），不能动。

      改法：`--connect-timeout 2` + `--max-time 8`。
        · 2 秒建不起连 → 基本就是死（DNS 不通/端口丢弃/防火墙），与 8 秒结论相同
        · 若真连上了，正文慢是有价值的（可能在吐大数据），仍给 8 秒读完
      实测效果：145 个从 47.4s 降到 ≈18s，且**结论不变**（慢的 26 个本来全是 000）。

    判据没变：仍然只回 http_code，仍然连不上给 '000'。这是纯延时优化，不改语义。
    """
    try:
        r=subprocess.run(['/usr/bin/curl','-sSL','--connect-timeout','2','--max-time','8',
                          '-A',UA,'-o','/dev/null','-w','%{http_code}',f'https://{h}'],
                         capture_output=True,text=True,timeout=12)
        return (h,(r.stdout or '000').strip())
    except Exception: return (h,'ERR')

def _probe_api(api):
    """② probe_src 联合复测：直接实测样例 api 本身（host 活≠api 活）。

    S139 修正——**HTTP 200 不等于复活**。原实现只看状态码，实测 8 个「确认复活」里
    只有 2 个是真的，误判率 75%：
      · `200 + "IP未授权或已到期，请到vip.x查看"`   → 判复活（实为停止授权）
      · `200 + "域名未授权"`                        → 判复活（实为停止授权）
      · `200 + 8 字节 "#EXTM3U"` 空壳               → 判复活（实为无内容）
      · `200 + "Anonymous Proxy detected."`         → 判复活（实为反爬拦截）
      · `200 + 空响应体`                            → 判复活（实为无内容）
    这些站点对任何请求都回 200（软拦截/跳转页/授权提示），状态码在这种场景下
    **不携带任何信息**。必须看正文：有效 vod api 返回 JSON（含 class/list），
    有效 m3u 含 `#EXTINF`。

    返回四态（M-R90：没量到不许折叠成「还行」）：
      '200'  真活（状态 200 且正文形态合格）
      'DEAD' 明确死（状态非 200、或 200 但正文是拒付/反爬/空壳）
      'SKIP' 没有样例 api，无从判定
      'ERR'  网络异常，无法判定（不当作死）
    """
    if not api:
        return 'SKIP'
    # ECO 对齐：底层取数走 core.http_get（保留 -m 10 整体超时与四态语义；
    # conn/timeout/dns → 'ERR' 不当作死，与原 curl 连不上/超时同口径）
    try:
        _st, _pl, _rs = http_get(api, timeout=10, ua=UA, return_status=True)
        if _st == 'ok':
            code, body = _pl[0], _pl[1]
        elif _rs and _rs.startswith('http_'):
            code, body = int(_rs[5:]), ''
        else:
            return 'ERR'
        if str(code) != '200':
            return 'DEAD'
        return '200' if _body_is_real(body, api) else 'DEAD'
    except Exception as e:
        # 探针自身 bug 自报家门（S139 实踩），不许伪装成外部故障
        return f'BUG:{type(e).__name__}'


def _probe_api_stable(api, tries=5):
    """三次采样定论——单次测活不足以判生死，但「成功过一次」也不足以判活。

    两个实测教训方向相反，必须同时满足：
      · apilj.com：三次里 2 次 6491B 真数据、1 次空体 -> 单次判定等于掷骰子；
      · aptv/master/m3u/iptv.m3u：三次里 1 次 200（120 频道）、2 次 000 ->
        按「成功一次即存在」会判活，可它大部分时候连不上，拿去做包就是坏源。
    所以不能只判「存在」，要判**稳定度**：
      · 全部成功        -> '200'   稳定可用（默认 5 次采样：3 次太少，
        实测 fhapi9-json 连 5 次全通，却在 3 次采样里因抖动被判死——假阴性）
      · 3/3 成功        -> '200'   稳定可用
      · 部分成功        -> 'DEAD'  **直接判死**（用户定策，S139）：时灵时不灵的源
        拿到手上就是坏一半。
        理由：这类源"偶尔能连"，拿去做包就是坏一半——用户装上去时灵时不灵，
        排查成本远高于少一条源。判死的代价只是少一个不稳定的选项；
        判活/搁置的代价是把不确定性推给最终用户。两害相权，判死。
      · 0/3 且无网络错  -> 'DEAD'
      · 有网络错且无成功 -> 'ERR'   无法判定，不当作死
    """
    # 采样之间必须留间隔：实测同一 URL 连续请求会被目标站限流——
    # 上一次 curl 刚拿到 200/5340B，紧接着再请求就是 000 空体。
    # 无间隔的三次采样等于「高速连打三下」，几乎必然打出两次 000，
    # 于是把一条好源判成 FLAKY/ERR。这不是源不稳定，是**探针自己制造的假不稳定**。
    def _round():
        got = []
        for _ in range(max(1, tries)):
            got.append(_probe_api(api))
            if len(got) < tries:
                time.sleep(0.6)
        return got

    seen = _round()
    ok_n = sum(1 for r in seen if r == '200')
    if ok_n == len(seen):
        return '200'
    # 判死必须**两轮独立采样都没过**才成立（S139 实踩）。
    # 单轮失败不足以判死：实测 fhapi9 的同一入口，上一轮 5 次里刚好撞上抖动判死，
    # 紧接着连测 5 次全 200——同一 URL、间隔一分钟，结论相反。
    # 这类源本身不偶发，是**采样窗口恰好落在抖动里**。判死是不可逆动作，
    # 必须比判活更保守：宁可多测一轮，不可错杀一条。
    seen2 = _round()
    if sum(1 for r in seen2 if r == '200') == len(seen2):
        return '200'
    if ok_n == 0 and 'ERR' in seen and all(r != '200' for r in seen2):
        return 'ERR'
    return 'DEAD'


# 软拦截特征：这些词出现在正文里，说明 200 是「假装给了」，不是真数据。
_SOFT_BLOCK = (
    'IP未授权', '域名未授权', '已到期', '请到vip', 'Anonymous Proxy',
    'detected', '未授权', '无权访问', 'Access denied', 'Forbidden',
    '请登录', '认证失败', '流量已用完', '已停止', '维护中',
)


def _body_is_real(body, api):
    """判定 200 的正文是否真是数据（而不是软拦截页 / 空壳）。

    判据按请求类型分：
      · m3u（.m3u / .m3u8）→ 必须含 `#EXTINF`（只有 `#EXTM3U` 是空壳）
      · 其余（vod api）→ 必须含 JSON 结构标记（`"class"` / `"list"` / `"vod_`）
    两者都不满足 → 不是数据。
    """
    b = (body or '').strip()
    if len(b) < 20:                      # 8B 的 "#EXTM3U" 空壳、15B 的"域名未授权"都在这
        return False
    head = b[:600]
    for w in _SOFT_BLOCK:                # 软拦截优先判（哪怕同时含 JSON 字样）
        if w in head:
            return False
    low = api.lower()
    if low.endswith(('.m3u', '.m3u8')):
        return '#EXTINF' in b
    # ③ XML 源（`api/xml.php`、`/at/xml/`）——合法形态第三种，此前完全没被认。
    # 实测：7 条 200 + 5KB 正文的真活 XML 源被整体判死，因为判据只认 JSON 与 m3u。
    # 这批源恰恰是「僵尸源」检查里被反向指认出来的（失效库里挂着、源库却是正常），
    # 说明**同一个判据缺口会在两个方向同时制造假账**。
    # 有效 XML 源形如 `<rss version="5.1"><list page=... recordcount=...><video>...`
    if b.startswith('<?xml') or b.startswith('<rss'):
        return ('<video>' in b) or ('recordcount' in b) or ('<list' in b)
    if b.startswith('[{'):
        return True
    # 苹果CMS 系有多种应答外形：`{"class":[...]}` / `{"list":[...]}`，
    # 也有只有分页骨架的 `{"code":1,"page":1,"pagecount":11513,...}`（实测 apilj.com）。
    # 只认 class/list 会把最后这类**真活**判死——这是判据的漏判，不是源的缺陷。
    # 补：能解析成 JSON 且带分页/总数语义字段，即认定为真实应答。
    if b.startswith('{'):
        for k in ('"page"', '"pagecount"', '"total"', '"vod_"', '"code"'):
            if k in b[:600]:
                return True
    return ('"class"' in b) or ('"list"' in b) or ('"vod_' in b)


def cmd_single_review(a):
    """单条类复测（S139）：`--single` —— 按**样例 api 逐条**验，不按 host。

    为什么单条类不能走整域那条路：它们的失效对象是**某一条线路**，不是整个域名。
    实测库里 98 条单条失效里大量是 `raw.githubusercontent.com/...` —— host 一直可通
    （GitHub 当然通），失效的是那个仓库路径。按 host 测必然全判"复活"，全是假阳性。
    单条类只有一个正确判据：**那个 api 本身还能不能给出数据**，即 `_probe_api_stable`。
    """
    dead=rows_of(DB_DEAD)
    tg=[r for r in dead if str(r.get('拉黑范围') or '').startswith('单条')
        and (r.get('样例api') or '').startswith('http')]
    # 复测面须覆盖「待复测 + 仍失效」：判据升级后，旧结论可能整体作废。
    # 实测：补认 XML 形态后，7 条先前判死的真活源全部翻案。若只重跑「待复测」，
    # 已判死的条目再也不会被复查——**判据修好了，旧账却永远停在错误结论上**。
    #
    # ★ S187 修（默认漏跑缺陷）：原默认只跑「未复测/待复测」，把「仍失效」留给 --recheck。
    #   看似省流量，实则**默认等于不干活**——实测 2026-10-20：73 条单条类
    #   **100% 都是「仍失效」**，于是 `blk review --single` 打印「单条复测 0 条」
    #   然后正常退出（0 条、不报错、看着像已完成）。后果：这批自 10-17 起
    #   2~3 天没被复查过一次，失效库只进不出。
    #   且同一命令的整域路径（cmd_review）**根本不按复测结果过滤**，每轮全跑——
    #   两条路径行为相反：整域每次都查，单条永远不查。
    #   修法：默认纳入「仍失效」，并按**停滞时长**升序（最久没查的优先），
    #   `--limit N` 因此总能覆盖最该复查的那批；`--recheck` 保留为显式全量语义（无实质差别，
    #   但老脚本/文档里的调用不失效）。
    def _stale_days(r):
        t = str(r.get('复测时间') or '')
        if not t:
            return 9999.0           # 从没复测过 → 最优先
        for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d'):
            try:
                return (time.time() - time.mktime(time.strptime(t[:19] if 'T' in fmt else t[:10], fmt))) / 86400
            except Exception:
                continue
        return 9999.0
    cand=[r for r in tg if r.get('复测结果') in ('未复测','待复测','仍失效')]
    cand.sort(key=_stale_days, reverse=True)     # 停最久的排前面
    todo_all=cand
    _recheck_extra = [r for r in tg if getattr(a,'recheck',False)
                      and r.get('复测结果') not in ('未复测','待复测','仍失效')]
    todo_all = todo_all + _recheck_extra
    # 分母必须用**截断前**的待测数算，否则 `--limit 3` 会把没跑的 95 条算成「已复测过」
    # ——实测踩到：98 条全待测，却打印「95 条已复测过」。计数顺序错=凭空造出进度。
    todo=todo_all[:a.limit] if a.limit else todo_all
    print(f'单条复测 {len(todo)} 条（共 {len(tg)} 条单条类；'
          f'待复测 {len(todo_all)} 条，本次跑 {len(todo)} 条）…')
    if not todo:
        print('  → 无待复测项'); return
    # ★ S163：改走统一入口（keep_order 必须开——revived/deadr 的分类要与 todo 一一对应）
    _rep=_run_bounded(todo, lambda r:(r, _probe_api_stable(r.get('样例api',''))), tier=TIER_WEB,
                      label='blk-review-one', keep_order=True, on_error='record')
    res=[(_r if _r is not None else (r,'ERR')) for r,_r in zip(todo,_rep.results)]
    revived=[(r,v) for r,v in res if v=='200']
    deadr=[(r,v) for r,v in res if v!='200']
    unk=[(r,v) for r,v in deadr if v in ('ERR','SKIP')]
    bugs=[(r,v) for r,v in deadr if str(v).startswith('BUG:')]
    if bugs:
        print(f'  🐞 探针自身异常 {len(bugs)} 条——**这不是源的问题，是判据代码的问题**：')
        for r,v in bugs[:5]:
            print(f'      {v}  {(r.get("样例api") or "")[:56]}')
    for r,v in revived:
        print(f'    ✅ 复活  {(r.get("样例api") or "")[:64]}')
    print(f'  仍失效 {len(deadr)-len(unk)-len(bugs)} | 已复活 {len(revived)} | '
          f'无法判定 {len(unk)}')
    if a.apply:
        now=time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())
        okc=0
        for r,v in revived:
            rid=_dead_id_by_api(r.get('样例api',''))
            # S159：原来写 `复测结果='已复活'`——该值不在表里（合法值只有
            # 仍失效/已恢复/未复测），服务端回 invalid fields，复活结论静默丢弃。
            # 改为表里真有的三态：观望 + 已平反 + 已恢复（复活但待回流复核）。
            if rid and _update_dead(rid,{'复测时间':{'date':now},
                                         '复测结果':{'select':REVIVE_RETEST},
                                         '拉黑范围':{'select':REVIVE_RANGE},
                                         '判死状态':{'select':REVIVE_STATE}}):
                okc+=1
        dead_ok=0
        for r,v in deadr:
            if v in ('ERR','SKIP'):
                continue     # 无法判定不写结论，只留待复测（M-R90）
            rid=_dead_id_by_api(r.get('样例api',''))
            if rid and _update_dead(rid,{'复测时间':{'date':now},
                                         '复测结果':{'select':'仍失效'}}):
                dead_ok+=1
        print(f'  ♻️ 已回写「已复活」{okc}/{len(revived)} 条 ｜ 📝 已回写「仍失效」{dead_ok}/{len(deadr)-len(unk)} 条')
        if unk:
            print(f'  ⚠️ {len(unk)} 条无法判定（网络异常/无api），保持「待复测」——不折叠成死源')


def cmd_archive(a):
    """出清已复活条目（S139）：`archive [--apply]`。

    为什么需要这一步：复测确认「已复活」后，记录仍留在失效库，于是 M09「僵尸源」
    检查反向报「失效库挂着、源库却是正常」。实测 7 条 XML 源就是这样被同时算两次账：
    在失效库是「已复活」，在 watchdog 眼里是「该停用却没停用」——**一个事实两处互相打脸**。

    出清条件（三条同时满足，缺一不出清）：
      ① 失效库内「复测结果 = 已复活」
      ② 该 api 在**源总库**中存在且状态为「正常」
      ③ 本次再测仍是稳定可用
    第 ③ 条不能省：复测结论可能是几小时前的，出清前必须再看一眼——否则就是把
    一条刚死的源放回生产（比不出清更坏）。

    ★ ②不满足时的正确处置是**回流**，不是无限期保留（S139 实踩）：
    实测 jiduo/tv123 两条源活得好，却不在源总库——它们只剩失效库这一个住处。
    只保留 → 永远挂着；只出清 → **线索凭空消失**。两条路都是账不干净。
    所以：源库无此条时先写进源总库（名称标「♻️复活血」），再删失效库记录，
    一次动作把「活源」落到它该在的地方。

    默认 dry-run，只列出「将出清什么、为什么」；`--apply` 才真删。
    """
    dead=rows_of(DB_DEAD)
    ok, src, _si = src_rows_t()          # S159：源库联合 + 三态
    if not ok:
        print(f'❌ 源库读取失败（{_si.get("failed")}）→ 出清中止（不折叠成"源库无此条"）')
        return
    src_state={}
    for r in src:
        k=_api_key(r.get('api接口') or '')
        if k:
            st=r.get('状态') or ''
            # 同一入口多条时，任一条「正常」就算源库有活账
            if src_state.get(k)!='正常':
                src_state[k]=st
    # S159：表里没有「已复活」，复活态是「已恢复」；兼容旧值一并捞
    cand=[r for r in dead if r.get('复测结果') in ('已恢复','已复活')]
    print(f'出清候选（失效库内「已恢复/已复活」）：{len(cand)} 条')
    if not cand:
        print('  → 无候选'); return
    ok, back, skip = [], [], []
    for r in cand:
        api=(r.get('样例api') or '').strip()
        k=_api_key(api)
        st=src_state.get(k)
        if st is not None and st!='正常':
            skip.append((api,f'源库状态={st} 非正常——先对齐源库再出清')); continue
        v=_probe_api_stable(api)
        if v!='200':
            skip.append((api,f'复测不再稳定可用({v})——禁止出清')); continue
        if st is None:
            back.append((api,r))     # 活在源库没有的条目上 → 先回流
        else:
            ok.append((api,r))
    for api,r in ok:
        print(f'  ✅ 可出清  {api[:66]}')
    for api,r in back:
        print(f'  ♻️ 待回流  {api[:52]}  ← 源库无此条：先写入源总库再出清')
    for api,why in skip:
        print(f'  ⏸ 保留    {api[:52]}  ← {why}')
    print(f'\n可出清 {len(ok)} 条 ｜ 待回流 {len(back)} 条 ｜ 保留 {len(skip)} 条')
    if not a.apply:
        print('  → 以上为 dry-run。确认后加 --apply 真正删除失效库记录。')
        return
    if not ok and not back:
        print('  → 无可出清项'); return
    # ① 先回流：源库没有的活源，写进源总库，否则出清会让它凭空消失
    if back:
        now=time.strftime('%Y-%m-%dT00:00:00Z',time.gmtime())
        # 写入必须是**类型化**map：{'字段名':{'text'|'select'|'date'|...,值}}。
        # 裸字符串会被 validate_properties 整条判空（"校验后为空，无有效字段"）——
        # S139 实踩：回流 0/2 却照样出清，两条活源同时从源库和失效库消失。
        # 且 select 字段必须用 {'select':v}：误用 {'text':v} 平台会**静默丢弃**该列
        # （S027 实证：279 条记录该列全空）。
        recs=[]
        for api,r in back:
            h=host_of(api)
            recs.append({'名称':{'text':f'♻️复活血·{h}'[:80]},
                         'host':{'text':h},
                         '分组类目':{'select':'直链'},
                         'api接口':{'text':api[:400]},
                         '入库时间':{'date':now},
                         '状态':{'select':'正常'},
                         '体系':{'select':'直链系'}})
        # S160：py/csp 两库已合并为唯一「线路源库」，回流不再需要按 api 前缀分库。
        #   原写法 `dict(SRC_DBS).get('csp线路源库') if api.startswith('csp_') else ...`
        #   在并库后**两边都会拿到 None**（库名已不存在）→ add_records(None,...) 崩掉，
        #   且复活源会连同出清一起失败。现统一落线路源库，形态写进 `源类型` 字段。
        #   注意：回流记录**不带 `可用体系`**，属已知字段缺口（非本次引入）。
        _line = dict(SRC_DBS)['线路源库']
        _by={}
        for rec in recs:
            _by.setdefault(_line,[]).append(rec)
        okc=0; fail=0
        for dest,rs in _by.items():
            _o,_f=add_records(dest,rs)
            okc+=_o; fail+=(_f or 0)
            print(f'  ♻️ 回流 → {dest} +{_o}' + ('' if not _f else f'（失败 {_f}）'))
        print(f'  ♻️ 已回流源库 {okc}/{len(recs)} 条'
              + ('' if not fail else f'（失败 {fail}）'))
        # 回流失败的**一个都不许出清**：删了它就再无住处。
        # S139 实踩：这里原写 `back=back[:okc]`——okc 是"成功条数"却被当成切片下标，
        # 于是 okc=0 时 `back[:0]=[]`，失败的条目反而被当成可出清的删掉，
        # 两条活源同时从源库和失效库**消失**。数量与下标混用 = 静默丢账。
        if fail:
            print(f'  🛑 回流失败 {fail} 条 → 这 {fail} 条**保留在失效库**，本次不出清')
            back=[x for x in back[:okc]]     # 只有确认写进源库的才可出清
            if okc==0:
                print('  → 无任何条目完成回流，本次终止出清（避免活源凭空消失）')
                return
        ok=ok+back
        back=[]
    ids=[]
    for api,r in ok+back:
        rid=_dead_id_by_api(api)
        if rid: ids.append(rid)
    if not ids:
        print('  ⚠️ 未能定位 record_id，未执行'); return
    # S158：改走 core.delete_records（同样修掉"只看 returncode"的假绿）
    try:
        import core as _c
        _ok, _n, _info = _c.delete_records(DB_DEAD, ids)
    except Exception as e:
        _ok, _n, _info = False, 0, f'core 不可用（{type(e).__name__}: {str(e)[:60]}）'
    if _ok:
        print(f'  🗑 已出清 {_n} 条（results 逐条确认）')
    else:
        print(f'  ⚠️ 出清未完成：{_info[:160]}')
        print(f'     实际确认删除 {_n}/{len(ids)} 条——**不得当作已清空**')


def cmd_review(a):
    dead=rows_of(DB_DEAD)
    tg=[r for r in dead if str(r.get('拉黑范围') or '').startswith('整域') and (r.get('host') or '').strip()]
    if a.limit: tg=tg[:a.limit]
    print(f'复测 {len(tg)} 个整域拉黑 host…')
    # ★ S163：改走统一入口。线程异常记 'ERR'（→ 归「无法判定」），不许装成活也不装成死
    _rep=_run_bounded(tg, lambda r:(r['host'],probe_one(r['host'])[1]), tier=TIER_WEB,
                      label='blk-review-host', keep_order=True, on_error='record')
    res=[(_r if _r is not None else (r['host'],'ERR')) for r,_r in zip(tg,_rep.results)]
    alive=[h for h,c in res if c=='200']
    still_dead=[h for h,c in res if c!='200']
    print(f'  仍失效 {len(still_dead)} | 疑似复活 {len(alive)}')
    confirmed=[]
    if alive:
        if a.deep:
            # ② 联合复测：host 200 不够，样例 api 须**正文合格**才判「确认复活」。
            # S139：原判据只看 api 的 HTTP 码，实测 8 个「确认复活」只有 2 个是真的
            # （其余是"IP未授权"/"域名未授权"/8字节空壳/反爬页——全都回200）。
            # 判据改为四态，且**报告里直接写出为什么不算活**，否则下次还是只能靠人肉抽查。
            api_of={r['host']:r.get('样例api','') for r in tg}
            dead_by_body=[]          # host 通但正文拒付/空壳 → 也是失效，须记账
            print('  🔄 联合复测（--deep：样例 api 正文验证）:')
            for h in alive:
                api=api_of.get(h,'')
                c2=_probe_api_stable(api)
                if c2=='200':
                    verdict='✅ 确认复活（正文合格）'
                    confirmed.append((h,api))
                elif c2=='SKIP':
                    verdict='⚠️ 无法判定（条目无样例api）'
                elif c2=='ERR':
                    verdict='⚠️ 无法判定（网络异常，不当死）'
                else:
                    verdict='❌ 仅host活（200但正文是拒付/反爬/空壳，或3次未全通）'
                    dead_by_body.append(h)
                print(f'    {h:34} {verdict}')
            if not confirmed:
                print('    → 本轮无一条真复活。host 能通 ≠ 源能用。')
        else:
            for h in alive[:20]: print(f'    {h}')
        if confirmed and a.apply:
            # ④ 复活自动回流：确认复活 → 三态回写（观望/已平反/已恢复）+ 时间，回流人工复核
            #    S159 修：原写 `复测结果='已复活'` 不是表里的合法值 → 静默失败。
            now=time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())
            okc=0
            for h,api in confirmed:
                rid=_dead_id(h)
                if rid and _update_dead(rid,{'复测结果':{'select':REVIVE_RETEST},
                                             '拉黑范围':{'select':REVIVE_RANGE},
                                             '判死状态':{'select':REVIVE_STATE},
                                             '复测时间':{'date':now}}):
                    okc+=1
            print(f'  ♻️ 已回写「已复活」{okc}/{len(confirmed)} 条——待人工复核后从失效库出清并验源回流')
            # ③ S139 补：**「仍失效」也必须写回**。
            # 原实现只给复活者回写，复测过的 140 条死源状态仍停在「待复测」——
            # 于是下次复测又把它们全跑一遍，同一批人重复白跑，且永远无法回答
            # 「上次复测是什么时候」。复测的价值一半在「确认还死」，不写回就白做。
            # 只更新时间、不改判定：仍死就是仍死，不因为「刚看过」而变绿（M-R82）。
            now2=time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())
            # 严格说复测结果是三态，不是两态：
            #   ① host 不通          → 仍失效
            #   ② host 通 + 正文合格 → 已复活
            #   ③ host 通 + 正文拒付/空壳 → **仍是失效**（源不能用，只是域名还留着）
            # 首版只写了 ①，把 ③ 漏在「待复测」里——下次又被当成没测过，重复跑。
            # 三态必须都落地，否则「测过了」这件事在账上无处可查。
            # 直接把本轮「已判死」的记下来，不重算——重算会再发一轮请求，
            # 网络抖动还会让同一条在两次请求里得出不同结论（自造不确定性）。
            dead_list = list(still_dead) + list(dead_by_body)
            dead_ok=0
            for h in dead_list:
                rid=_dead_id(h)
                if rid and _update_dead(rid,{'复测时间':{'date':now2},
                                             '复测结果':{'select':'仍失效'}}):
                    dead_ok+=1
            if dead_list:
                print(f'  📝 已回写「仍失效」{dead_ok}/{len(dead_list)} 条'
                      f'（含 host 通但源已死的 {len(dead_list)-len(still_dead)} 条；仅更新时间，判定不变）')
        elif confirmed:
            print('  → 确认复活后可 --apply 自动回写「已复活」（回流需人工验源确认）')

def cmd_hostcheck(a):
    """★ T28（P053）：M-R106 的可执行判据——「整域拉黑」与「公共平台域/代理域」的矛盾检查。

    规则原文：只有「独占域名的私人站」才配整域拉黑；公共代码托管平台（github/gitee/
    jsdelivr 等，一个域下挂无数互不相关的发布者）与代理域（gh-proxy 等）**只能单条拉黑**。
    违反后果不是"多拦一条"，而是**该域下全部存活源被一并判死**（S159 实测：git.yylx.win
    的 5 条记录写着单源失效，拉黑范围却填整域，整域存活源全灭）。

    本命令只做**报出**，不改表——判据能判真假，但"该改成单条还是该保留整域"需人看上下文。
    （M-R136 第③关：误报代价只是多看一眼，误拦代价是一域全灭。）

    三态：无矛盾=通过退出 0；有矛盾=列出并退出 1；参数为空=无法判定退出 2。
    """
    raw = (getattr(a, 'hosts', '') or '').strip()
    if not raw:
        # 未给 --hosts → 从失效库里取全部「整域」记录自查（这正是 S159 事故现场）
        try:
            import core as _c
            rows = _c.rows_of(os.environ.get('BLK_DB', '')) if False else []
        except Exception:
            rows = []
        print('❌ 未给 --hosts，且自动读库未启用——**无法判定**（不是通过）')
        print('   用法: blk.py hostcheck --hosts "raw.githubusercontent.com,gitee.com"')
        return 2
    hosts = [h.strip().lower() for h in raw.replace('，', ',').split(',') if h.strip()]
    if not hosts:
        print('❌ hosts 解析为空——无法判定')
        return 2
    bad = []
    for h in hosts:
        pf = is_platform_host(h)
        px = is_proxy_host(h)
        if pf or px:
            bad.append((h, '公共平台域' if pf else '代理域'))
    print(f'🔍 M-R106 整域拉黑矛盾检查：{len(hosts)} 个 host')
    if not bad:
        print('  ✅ 无矛盾（无一是公共平台域/代理域，可整域拉黑）')
        return 0
    print(f'  🔴 {len(bad)} 个 host 属公共平台域/代理域，**不得整域拉黑**：')
    for h, why in bad:
        print(f'     · {h}（{why}）→ 改为单条拉黑，否则整域存活源会被一并判死')
    return 1


def build_parser():
    ap=argparse.ArgumentParser(prog='blk.py')
    sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('mark')
    c=sub.add_parser('check'); c.add_argument('api')
    g=sub.add_parser('guard'); g.add_argument('--candidates')
    l=sub.add_parser('list');  l.add_argument('--host',default='')
    sub.add_parser('stats')
    rv=sub.add_parser('review'); rv.add_argument('--limit',type=int,default=0)
    # ★ S187：`--deep` 改默认开启。原默认只测 host 的 HTTP 码，而实测
    #   （2026-10-20，145 个整域 host）「疑似复活 13 个」加正文验证后 **13 个全是假活**
    #   （拒付/反爬/空壳，全都回 200）。只测码 = 每轮稳定产出 0 真复活 + 一堆假活。
    #   要快速只测连通性，显式传 `--shallow`。
    rv.add_argument('--deep', dest='deep', action='store_true', default=True,
                    help='联合复测：样例api正文验证（三次采样）【默认开启】')
    rv.add_argument('--shallow', dest='deep', action='store_false',
                    help='只测 host 连通性（快，但会把拒付/空壳误判成复活）')
    rv.add_argument('--single',action='store_true',help='单条类复测：按样例api逐条验（host通≠源可用）')
    rv.add_argument('--recheck',action='store_true',help='连同「仍失效」一起重跑（判据升级后用）')
    ar=sub.add_parser('archive'); ar.add_argument('--apply',action='store_true',
        help='真删失效库中已复活且源库正常的条目（默认 dry-run）')
    rv.add_argument('--apply',action='store_true',help='确认复活自动回写「已复活」')
    hc=sub.add_parser('hostcheck', help='M-R106 整域拉黑矛盾检查（公共平台域/代理域不得整域）')
    hc.add_argument('--hosts', default='', help='逗号分隔的 host 列表')
    return ap

AP=build_parser()
DISPATCH={'mark':cmd_mark,'check':cmd_check,'guard':cmd_guard,
          'list':cmd_list,'stats':cmd_stats,'review':cmd_review,'archive':cmd_archive,
          'hostcheck':cmd_hostcheck}

def dispatch(argv=None):
    """供调度器 tvbox 在同进程内直接调用，共享 core 缓存与并行池。

    ★ T28 修：原实现 `DISPATCH[a.cmd](a)` **丢弃返回值**，`main()` 也不 return，
    于是无论子命令内部 `return 1`（判出矛盾）还是 `return 2`（无法判定），
    进程退出码恒为 0。后果不是"少个信号"，而是**任何按退出码判断的门禁都会把它记成通过**
    ——本轮 M-R106 探针正例捕获 False 就是这么来的（输出说"1 个 host 不得整域拉黑"，
    退出码却 0）。与 S177 修 rules_cmd 的那处是同一族病：判定算对了，没交到下游。
    """
    a=AP.parse_args(argv)
    # `--single` 走单条复测（按 api 逐条验），与整域复测是两套判据，不能合并
    if a.cmd=='review' and getattr(a,'single',False):
        return cmd_single_review(a)
    return DISPATCH[a.cmd](a)

def main():
    """退出码必须说真话：子命令返回什么，进程就退什么（int 才传，None 当 0）。"""
    rc = dispatch()
    raise SystemExit(int(rc) if isinstance(rc, int) else 0)

if __name__=='__main__': main()
