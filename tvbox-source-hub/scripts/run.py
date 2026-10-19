#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tvbox-source-hub 一体化入口：扫描 → 去重 → 判体系 → 批量入库 → 读回验证

用法:
  python3 run.py --export-only          # 只导出当前库内已有数据到 /tmp/exist_api.txt
  python3 run.py --dry-run              # 全流程但不写入
  python3 run.py                        # 全流程并写入主库
  python3 run.py --db <database_id>     # 指定库(默认主库)
  python3 run.py --dirs /a /b           # 额外扫描目录

依赖环境: /root/.codebuddy/skills/library/space_api.py (库 API)
"""
import os,sys,json,glob,time,argparse,subprocess,csv,io
from urllib.parse import urlparse
from collections import Counter

LIB='/root/.codebuddy/skills/library'
# S159：库坐标改从 core 取（单一事实源）。原 DB_MAIN 指向 S144 已清空的旧源总库，
#   于是 `--db` 默认值 = 空库：导出 0 条、判重恒不命中、**写入全部落到废弃库**。
# S160：py/csp 两库合并为唯一「线路源库」，本脚本产出的线路记录（含 `可用体系` 多选）
#   全部落它；`源类型` 字段区分 py线路/csp线路，不再按库区分。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import SRC_DBS as _SRC_DBS  # noqa: E402
_D = dict(_SRC_DBS)
DB_MAIN=_D['线路源库']
DEF_DIRS=['/root/.codebuddy/artifact/baseline','/workspace','/root/.codebuddy/artifact/hzm']

A='A·CatVod标准系'; B='B·moji Guard系'; C='C·零jar纯直链系'; D='D·Amns系'; X='🔞成人直链系'
CATS=['配置','本地','网盘','DIY','直链','动漫','音乐','4k','教育','B站','磁力','直播',
      '体育','短剧','听书','搜索','功夫','影视','APP','其他']

BLACKLIST=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'blacklist.txt')

def load_blacklist():
    bl=set()
    if os.path.exists(BLACKLIST):
        for ln in open(BLACKLIST,encoding='utf-8'):
            ln=ln.strip()
            if not ln or ln.startswith('#'): continue
            bl.add(ln.split('\t')[0].strip())
    return bl

def api_call(script,*args):
    """S158：改走 core._run_lib —— 统一三态，失败不再静默返回空串。

    返回 (ok, stdout, err)。旧版只返回 stdout，调用方 `json.loads('')` 抛异常后
    `return set()`，**把"库读不到"折叠成"库里啥都没有"**——这会导致后续全量重写。
    """
    try:
        import core as _c
        return _c._run_lib(script, *args)
    except Exception as e:
        return False, '', f'core 不可用（{type(e).__name__}: {str(e)[:60]}）'

# ---------- 1. 导出库内已有 ----------
def export_exist(db):
    ok, out, err = api_call('database/get_database_content.py', '--database-id', db)
    if not ok:
        # 三态：读不到 ≠ 空库。此处**必须显式失败**，否则调用方会把整库当空、全量重灌。
        print(f'❌ 导出失败（库 {db} 读不到）：{err[:150]}')
        print('   ⚠️ 这**不是"库是空的"**——禁止按空库继续，否则会全量重写造成重复。')
        return None
    try:
        d = json.loads(out)
        rows = list(csv.DictReader(io.StringIO(d['content'])))
    except Exception as e:
        print(f'❌ 导出解析失败：{type(e).__name__}（返回前 150 字：{out[:150]}）')
        return None
    return rows, set(r['api接口'].strip() for r in rows)

# ---------- 2. 扫描 ----------
def load_json(path):
    try:
        raw=open(path,encoding='utf-8',errors='replace').read().strip().lstrip('\ufeff')
        if not raw: return None
        lines=[l for l in raw.split('\n') if not l.strip().startswith('//')]
        return json.loads('\n'.join(lines))
    except Exception: return None

def scan(dirs):
    packs=[]
    for dd in dirs:
        packs+= [(os.path.basename(p).replace('.json',''),p)
                 for p in sorted(glob.glob(os.path.join(dd,'*.json')))]
    rows=[]; stats={}; failed=[]
    for name,path in packs:
        d=load_json(path)
        if d is None: failed.append(name); continue
        if isinstance(d,list):
            d={'sites':[x for x in d if isinstance(x,dict) and x.get('type')!=0],
               'lives':[x for x in d if isinstance(x,dict) and x.get('type')==0]}
        n=(0,0,0)
        for s in d.get('sites') or []:
            a=str(s.get('api') or '').strip()
            if a: rows.append({'名称':s.get('name') or '','api接口':a,'来源包':name,'kind':'site',
                               '内容分类':s.get('categories')}); n=(n[0]+1,n[1],n[2])
        for l in d.get('lives') or []:
            u=str(l.get('url') or '').strip()
            if u: rows.append({'名称':l.get('name') or '','api接口':u,'来源包':name,'kind':'live'}); n=(n[0],n[1]+1,n[2])
        for p in d.get('parses') or []:
            u=str(p.get('url') or '').strip()
            if u: rows.append({'名称':p.get('name') or '','api接口':u,'来源包':name,'kind':'parse'}); n=(n[0],n[1],n[2]+1)
        if sum(n): stats[name]=n
        else: failed.append(name+'(空)')
    return rows,stats,failed

# ---------- 3. 体系判定 ----------
def systems(api):
    if api.startswith('csp_'):
        cls=api[4:]
        if cls.endswith('Guard'): return [B]
        if cls.endswith('Amns'):  return [D]
        return [A]
    return [A,B,C,D]

def old_system(api):
    if not api.startswith('csp_'): return '直链系'
    cls=api[4:]
    if cls.endswith('Guard'): return 'guard系'
    if cls.endswith('Amns'):  return '其他系'
    return 'lytvs系'

CAT_PAIRS=[('网盘','网盘'),('Pan','网盘'),('阿里','网盘'),('夸克','网盘'),('百度','网盘'),('115','网盘'),
           ('磁力','磁力'),('种子','磁力'),('荐片','磁力'),('动漫','动漫'),('番剧','动漫'),
           ('音乐','音乐'),('MV','音乐'),('电视','直播'),('央视','直播'),('体育','体育'),('足球','体育'),
           ('NBA','体育'),('看球','体育'),('赛程','体育'),('短剧','短剧'),('听书','听书'),('有声','听书'),
           ('搜索','搜索'),('4K','4k'),('4k','4k'),('B站','B站'),('哔哩','B站'),('bili','B站'),
           ('APP','APP'),('应用','APP'),('教育','教育'),('课堂','教育'),('学习','教育'),
           ('小学','教育'),('初中','教育'),('高中','教育'),('影视','影视'),('电影','影视'),
           ('功夫','功夫'),('Local','本地'),('本地','本地'),('Alist','DIY'),('Emby','DIY'),
           ('DIY','DIY'),('配置','配置')]
def cat_of(name,kind,api):
    if kind=='live': return '直播'
    for k,v in CAT_PAIRS:
        if k in (name or ''): return v if v in CATS else '其他'
    if kind=='parse': return '配置'
    if api.startswith('http'): return '直链'
    return '其他'

def host_of(a):
    if a.startswith('http'):
        try: return urlparse(a).netloc
        except: return ''
    return a.lower()

# ---------- 4. 主流程 ----------
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default=DB_MAIN)
    ap.add_argument('--dirs',nargs='*',default=DEF_DIRS)
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--export-only',action='store_true')
    a=ap.parse_args()

    print('[1/5] 导出库内已有…')
    _ex = export_exist(a.db)
    if _ex is None:
        # 三态：读不到就停。继续跑会把整库当空、全量重灌 → 重复行。
        print('⏹ 中止：库读不到，无法确定已有内容（不是"库是空的"）。')
        raise SystemExit(3)
    rows_exist,exist=_ex
    print(f'      库内已有 {len(rows_exist)} 行')
    if a.export_only:
        open('/tmp/exist_api.txt','w',encoding='utf-8').write('\n'.join(sorted(exist)))
        print('      已写 /tmp/exist_api.txt'); return

    print('[2/5] 扫描 packages…')
    rows,stats,failed=scan(a.dirs)
    print(f'      包 {len(stats)} 个成功 / {len(failed)} 失败(空) | 原始 {len(rows)} 条')

    print('[3/5] 去重 + 判体系…')
    bl=load_blacklist()
    kept=[]; d_rel=d_dup=d_bl=0; seen=set()
    for r in rows:
        api=(r['api接口'] or '').strip()
        if not api: continue
        if api.startswith(('./','../')): d_rel+=1; continue
        k=api.split('\n')[0].strip()
        if not k.startswith(('http','csp_')): d_rel+=1; continue
        if k in bl: d_bl+=1; continue
        if k in exist or k in seen: d_dup+=1; continue
        seen.add(k)
        kept.append({'名称':(r['名称'] or '').strip() or '(未命名)','api接口':k,
                     'kind':r['kind'],'可用体系':systems(k),
                     '内容分类':r.get('内容分类')})
    print(f'      剔相对路径 {d_rel} | 剔黑名单 {d_bl} | 剔重复 {d_dup} | → 待入库 {len(kept)}')
    for k,v in Counter(tuple(x['可用体系']) for x in kept).most_common():
        print(f'        {v:4}  {"+".join(x.split("·")[0] for x in k)}')

    recs=[]
    # 内容分级（S102·规则56）：复用 link.py 的单一事实源判据（M-R38）
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from link import classify_adult, ADULT_WORDS
    except Exception:
        ADULT_WORDS=['伦理','三级','激情','成人','18禁','里番','无码','H漫','情色','禁片']
        def classify_adult(cats):
            if not cats: return '未知'
            cats=[str(c).strip() for c in cats if str(c).strip()]
            if not cats: return '未知'
            hit=[c for c in cats if any(w in c for w in ADULT_WORDS)]
            if not hit: return '纯净'
            return '18' if len(hit)==len(cats) else '全量'
    for x in kept:
        api=x['api接口']
        cats=x.get('内容分类')
        recs.append({'名称':{'text':x['名称']},'host':{'text':host_of(api)},
                     '分组类目':{'select':cat_of(x['名称'],x['kind'],api)},
                     'api接口':{'text':api},
                     '入库时间':{'date':time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())},
                     '状态':{'select':'待查'},'体系':{'select':old_system(api)},
                     '可用体系':{'multi_select':x['可用体系']},
                     '内容分级':{'select':classify_adult(cats)},
                     '内容分类':{'text':json.dumps(cats,ensure_ascii=False)[:2000] if cats else ''}})
    if a.dry_run:
        print('[DRY-RUN] 未写入。样例:',json.dumps(recs[0],ensure_ascii=False)[:220] if recs else '(无待入库数据)')
        return
    if not recs:
        print('       库已收录全部有效数据源，无需写入。✅')
        print(f'       最终库内 {len(rows_exist)} 行'); return

    print('[4/5] 批量写入…')
    # S158 收敛：改走 core.add_records（统一三态 + properties 结构检测）。
    # 原实现在解析失败 / 无 results 时都 `fail+=len(b)`，与原注释自相矛盾——
    # 它明明打印了「ERR」却把「不知道有没有写进去」记成「这批失败」。
    try:
        import core as _c
        ok, fail = _c.add_records(a.db, recs)
        if ok + fail < len(recs):
            print(f'   ⚠️ 有 {len(recs) - ok - fail} 条未取得结果（不是"被拒"，是通路没回报）')
    except Exception as _e:
        ok, fail = 0, 0
        print(f'   ❌ 写入通路不可用（{type(_e).__name__}: {str(_e)[:60]}）'
              f'——{len(recs)} 条全部「未判定」，下方回读会给真实结论')
    print(f'      成功 {ok} | 失败 {fail}')

    print('[5/5] 读回验证…')
    _ex2 = export_exist(a.db)
    if _ex2 is None:
        print('⚠️ 回读失败——写入结果**未确认**（不是"没写进去"，是无法判定）。请人工核对。')
        raise SystemExit(3)
    rows2,exist2=_ex2
    empty=sum(1 for r in rows2 if not r['可用体系'])
    print(f'      库内现在 {len(rows2)} 行 | 可用体系空值 {empty}')
    print(f'      {"✅ 验证通过" if len(rows2)==len(rows_exist)+ok and empty==0 else "⚠️ 需人工核对"}')

if __name__=='__main__':
    main()
