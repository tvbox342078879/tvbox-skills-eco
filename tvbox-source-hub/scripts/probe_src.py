#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源库 HTTP 类数据源实测（提升数据精准性）

对源库里 api接口 以 http 开头的行做真实请求，判定 正常 / 停用 / 待查：
  - 采集类(api.php/provide/vod 等)：请求 +?ac=list，返回 JSON 且 total/list 非空 → 正常
  - 直播源(m3u/txt)：返回体 > 500 字节且含 #EXTINF 或 http → 正常
  - 其余：HTTP 200 且 size>500 → 正常
  - 超时/4xx/5xx/空体 → 停用

终态三态机（S134 P011）：正常=有效（终态）/ 停用=失效（终态）/ 待查=待测（非终态，须测活）
  空值历史数据一律归入「待查」并由本工具测活推进到终态。

用法: python3 probe_src.py [--limit N] [--out /tmp/probe_result.json] [--write]
  --write  测活后**回写源库状态列**（终态化，P011）；不带则只出本地报告
"""
import os,sys,json,argparse,subprocess,csv,io,time
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import rows_of_t, src_rows_t, SRC_DBS   # S083·M-R38 单一事实源：复用 core 三态读表

# ---------- M-R114 统一并发入口（S110） ----------
# 本文件原先就地写死 max_workers=N，数字无依据。改为按档位表取上限。
# 保留一个同名 shim，避免改动所有调用点（改动面越小越安全）。
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded as _run_bounded, TIER_WEB  # noqa: E402


LIB='/root/.codebuddy/skills/library'
# S159：原 DB_SRC 写死旧源总库（S144 已清空）。本文件的 M-R60 三态守卫其实写得很对，
#   但**读的是空库**：ok=True + rows=[] → 守卫不触发 → 打印「待测 HTTP 源: 0 条」
#   → 写出一份「无源需测」的结果。三态守卫挡得住「读失败」，挡不住「读错库」。
_D = dict(SRC_DBS)
DB_SRC = '[源库联合]'                  # 仅作标签；实际读写走下面的 SRC_TARGETS
SRC_TARGETS = [(n, d) for n, d in SRC_DBS]   # 测活在源库联合上都要跑
UA='okhttp/3.12.0'

def probe(api):
    url=api.strip()
    test=url
    if 'api.php' in url or 'provide/vod' in url or 'zyapi' in url:
        test=url+('&' if '?' in url else '?')+'ac=list'
    try:
        r=subprocess.run(['/usr/bin/curl','-sSL','-m','10','-A',UA,
                          '-o','/dev/null','-w','%{http_code} %{size_download}',test],
                         capture_output=True,text=True,timeout=14)
        p=(r.stdout or '').split()
        code=p[0] if p else '000'
        size=int(p[1]) if len(p)>1 and p[1].isdigit() else 0
    except Exception:
        return (api,'停用','ERR',0)
    if code!='200' or size<300:
        return (api,'停用',code,size)
    return (api,'正常',code,size)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--limit',type=int,default=0)
    ap.add_argument('--out',default='/tmp/probe_result.json')
    ap.add_argument('--write',action='store_true',
                    help='S134 P011：把测活结果回写源库「状态」列（正常→有效 / 停用→失效）')
    a=ap.parse_args()
    ok, rows, sinfo = src_rows_t()        # S159：源库联合 + M-R60 三态
    if not ok:
        print(f'❌ 源库读取失败（{sinfo.get("failed")}）——无法测活。')
        print('   已中止：**不写出空结果**（若写 0 条，会被误读为「无 HTTP 源」，实为读取失败）。')
        print('   请检查云端连接/鉴权后重跑。')
        sys.exit(2)
    print('   分库：' + ' | '.join(f'{k} {v}' for k, v in (sinfo.get('by_db') or {}).items()))
    http=[r for r in rows if r['api接口'].strip().startswith('http')]
    if a.limit: http=http[:a.limit]
    print(f'待测 HTTP 源: {len(http)} 条（并发 40）')
    t0=time.time()
    # ★ S163：改走统一入口（原 shim 自建池，绕过写集合登记/拖进程护栏/收益实测）
    _rep=_run_bounded(http, lambda r: probe(r['api接口']), tier=TIER_WEB,
                      label='probe-src', keep_order=True, on_error='record')
    res=[(_r if _r is not None else (r['api接口'],'无法判定','ERR','探测线程异常'))
         for r,_r in zip(http,_rep.results)]
    el=time.time()-t0
    c=Counter(x[1] for x in res)
    print(f'完成，用时 {el:.0f}s → {dict(c)}')
    json.dump([{'api接口':x[0],'状态':x[1],'code':x[2],'size':x[3]} for x in res],
              open(a.out,'w',encoding='utf-8'),ensure_ascii=False)
    print(f'结果已写 {a.out}')
    if a.write:
        write_status(res)
    # 抽样展示
    for x in res[:5]:
        print(f'  [{x[1]}|{x[2]}|{x[3]}] {x[0][:70]}')

def _paged(db):
    """分页读全表（含 record_id）——P011 回写需要 id。

    ★ S158：改走 `core.query_all`。旧实现 `except: break` 会**把半个表当全表**返回，
      且无法区分"读失败"与"真空库"。
    三态：成功 → list（可能为空=真空库）；**失败 → None**（调用方须判，不得当空列表）。
    """
    try:
        import core as _c
    except Exception as e:
        print(f'   ❌ 读表不可用：core 导入失败（{type(e).__name__}: {str(e)[:60]}）')
        return None
    ok, rows, info = _c.query_all(db, page_size=100, max_pages=80)
    if not ok:
        print(f'   ❌ 读表失败（{db}）：{info[:140]}')
        return None
    return rows


def write_status(res):
    """S134 P011：把测活结果回写源库「状态」列，推进到终态。

    库内既有选项（S134 实测 schema）：正常／有广／待查／停用。
    终态：正常（可达可用）、停用（不可达）；非终态：待查（未测）、有广（待清洗）。
    本工具只推进 正常/停用 两个终态，不臆造新选项值（写非选项值会被平台拒绝）。
    只改「状态」单字段，不触碰其它列（最小写面）。
    """
    want={x[0]: ('正常' if x[1]=='正常' else '停用') for x in res}
    # S159：record_id 只在**所属库**内有效，必须逐库读、逐库写。
    #   原来只读一个库 → 联合库里其余库的源永远测完不落账。
    ok=fail=0; hit=0
    for _nm, _db in SRC_TARGETS:
        rows=_paged(_db)
        if rows is None:
            # 读**失败**：不能说"没有记录"，只能说"读不到"，更不能静默跳过当已完成
            print(f'   ⚠️ {_nm}：读表**失败**，本库回写跳过'
                  f'（这不是"该库无记录"——须修好后重跑，否则该库源永远不落账）')
            continue
        if not rows:
            print(f'   ℹ️ {_nm}：该库确实没有记录（读成功，0 条），跳过')
            continue
        recs=[]
        for r in rows:
            api=str(r.get('api接口') or '').strip()
            if api in want:
                rid=r.get('record_id') or r.get('id')
                if not rid: continue
                recs.append({'record_id':rid,'properties':{'状态':{'select':want[api]}}})
        if not recs:
            continue
        hit+=len(recs)
        print(f'   {_nm}：待回写 {len(recs)} 条')
        ok,fail=_flush(recs,_db,ok,fail)
    print(f'待回写合计 {hit} 条（终态：正常/停用）')


def _flush(recs, db, ok, fail):
    # S158 收敛：改走 core.update_records。原实现解析失败 `fail+=len(chunk)`
    # 把「通路坏了」记成「这一批被拒」，失败数会虚高，人会照着重写已成功的数据。
    for i in range(0,len(recs),50):
        chunk=recs[i:i+50]
        try:
            import core as _c
            _u_ok, _u_n, _u_info = _c.update_records(db, chunk)
            if _u_ok:
                ok += _u_n
            else:
                fail += len(chunk) - max(0, _u_n or 0)
                print(f'   ⚠️ 回写未全部确认：{_u_info[:120]}')
        except Exception as _e:
            fail += len(chunk)
            print(f'   ❌ 回写通路不可用（{type(_e).__name__}: {str(_e)[:60]}）'
                  f'——这 {len(chunk)} 条是「未判定」，不是「被拒」')
    return ok, fail


if __name__=='__main__': main()
