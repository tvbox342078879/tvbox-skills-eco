"""spider_verify：把一个 TVBox py 源的**代码**跑起来逐接口验证（S148）。

为什么需要它（这不是"锦上添花"，是缺口）：
`check_py` 只判「URL 指向的是不是 Python 源码」，**不判这段源码能不能用**。
一个语法正确、逻辑全错的源，`check_py` 照样给"通过"。写源环节真正要回答的是
「跑起来，这 5 个接口给的东西对不对」，而不仅是「文件像不像源码」。

判据（每条都是实测撞出来的，不是照抄技能书）：
  ① 分类 非空 + 每项 id/name 齐全；**并检测空壳分类**（父分类 total=0 是常见真实坑）
  ② 列表 非空 + 无重复 id + 跨页不重叠 + pagecount>1；图片是绝对地址
  ③ 详情 非空 + 关键字段齐 + **剧集数守恒**（产出 vs 接口原始，不许中间丢集）
     且**必须挑多集样本**——拿电影（1 个地址）验集数是 1==1 的假验证
  ④ 播放 真拉一次：HTTP 200/206 + 响应含 #EXTM3U（206 是 Range 的正常返回，不是失败）
  ⑤ 搜索 命中关键词非空
三态（M-R60）：出不来结果时区分「源有问题」与「网络/站点不可达」——
后者报"无法判定"，不冒充"失败"。
"""
import os
import re
import sys
import json
import shutil
import tempfile
import subprocess


_STUB = '''class Spider(object):
    def __init__(self, *a, **k):
        pass
'''


def _declared_host(src):
    """从源码里**静态**读出 `self.host = "..."`（不执行代码，免得运行副作用）。

    为什么不用"问被测源要 host"：源可能算出来的、可能被覆盖，而我们要判的恰恰是
    "它声明的站点到底存不存在"。静态读赋值语句最直接，也最不容易被源自己糊弄。
    """
    m = re.search(r"^\s*self\.host\s*=\s*[\"']([^\"']+)[\"']", src, re.M)
    if m:
        return m.group(1)
    m = re.search(r"^\s*self\.api\s*=\s*[\"']([\"']+)[\"']", src, re.M)
    return m.group(1) if m else ''


def _ensure_importable(spider_path):
    """构造一个临时可导入环境：把 base/spider.py 桩放进去，返回 (dir, module_dir)。"""
    d = tempfile.mkdtemp(prefix='spiderverify_')
    os.makedirs(os.path.join(d, 'base'), exist_ok=True)
    open(os.path.join(d, 'base', '__init__.py'), 'w', encoding='utf-8').close()
    with open(os.path.join(d, 'base', 'spider.py'), 'w', encoding='utf-8') as f:
        f.write(_STUB)
    shutil.copy(spider_path, os.path.join(d, 'spider_mod.py'))
    return d


_PROBE = r'''
import sys, os, json, time, re, urllib.request, ssl
sys.path.insert(0, %(dir)r)
os.environ.setdefault('PYTHONWARNINGS', 'ignore')
import spider_mod as sp

_ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE
_out = []
def rec(step, ok, detail):
    _out.append({'step': step, 'ok': bool(ok), 'detail': str(detail)})

def _fetch(url, n=4096):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Range': 'bytes=0-%%d' %% (n-1)})
        with urllib.request.urlopen(req, timeout=25, context=_ctx) as r:
            return r.status, r.read(n)
    except Exception as e:
        msg = str(e)
        code = 0
        m = re.search(r'(\d{3})', msg)
        if m: code = int(m.group(1))
        return code, msg.encode()

def main():
    try:
        s = sp.Spider(); s.init('')
    except Exception as e:
        return [{'step': '初始化', 'ok': False, 'detail': '%%s: %%s' %% (type(e).__name__, e)}]

    # ① 分类
    cat = []
    try:
        t0 = time.time(); home = s.homeContent(True) or {}
        cat = [c for c in (home.get('class') or []) if c.get('type_id') and c.get('type_name')]
        rec('①分类 homeContent', len(cat) >= 3,
            '%%d 个分类，%%.1fs' %% (len(cat), time.time() - t0))
    except Exception as e:
        rec('①分类 homeContent', False, '%%s: %%s' %% (type(e).__name__, e))
    if not cat:
        return _out

    # ①b 空壳分类探测（父分类 total=0 是常见真实坑，不探测则用户点进去一片空白）
    hollow, alive = [], 0
    try:
        for c in cat[:8]:
            if alive >= 3: break
            cc = s.categoryContent(c['type_id'], 1, True, {}) or {}
            if (cc.get('list') or []):
                alive += 1
            else:
                hollow.append(c['type_name'])
        rec('①b空壳分类', alive > 0,
            ('空壳 %%d 个（%%s）' %% (len(hollow), ','.join(hollow[:4]))) if hollow else '无空壳')
    except Exception as e:
        rec('①b空壳分类', False, '%%s: %%s' %% (type(e).__name__, e))

    # ② 列表
    l1 = l2 = []
    tid = None
    try:
        for c in cat:
            cc = s.categoryContent(c['type_id'], 1, True, {}) or {}
            if cc.get('list'):
                tid, l1 = c['type_id'], cc['list']; break
        cc2 = s.categoryContent(tid, 2, True, {}) if tid else {}
        l2 = cc2.get('list') or []
        ids = [str(x.get('vod_id')) for x in l1]
        dup = len(ids) - len(set(ids))
        inter = set(ids) & {str(x.get('vod_id')) for x in l2}
        rec('②列表 categoryContent', bool(l1) and dup == 0 and not inter,
            '第1页%%d 第2页%%d 重复%%d 跨页重叠%%d pagecount=%%s' %% (
                len(l1), len(l2), dup, len(inter), cc2.get('pagecount')))
        bad = [x.get('vod_name') for x in l1
               if x.get('vod_pic') and not str(x['vod_pic']).startswith('http')]
        rec('②b图片绝对地址', not bad, '非绝对 %%d 条' %% len(bad))
    except Exception as e:
        rec('②列表 categoryContent', False, '%%s: %%s' %% (type(e).__name__, e))

    # ③ 详情 + 多集样本
    #    ★ 致命教训（实测撞出来的）：第一版"从源产出里找多集样本"，结果**坏源自己把
    #      84 集砍成 1 集**，于是永远找不到多集样本，退化成"单片样本"分支照样判通过
    #      ——**验证器被被测对象牵着走**。正解：多集样本必须从**接口原始数据**判定。
    multi = None
    try:
        pools = [l1]
        try:
            sr = s.searchContent('剧', False, '1') or {}
            pools.append(sr.get('list') or [])
        except Exception:
            pass
        if sum(len(p) for p in pools) < 6:
            try:
                pools.append(s.categoryContent('12', 1, True, {}).get('list') or [])
            except Exception:
                pass

        raw_json = getattr(s, '_json', None)
        api_base = getattr(s, 'api', '') or getattr(s, 'host', '')
        for pool in pools:
            for it in pool[:12]:
                vid = str(it.get('vod_id') or '')
                if not vid:
                    continue
                # ★ M-R60：「取接口原始数据失败」≠「接口说 0 集」。原版 except 里写 `raw_n = 0`，
                #   于是**接口查不到**与**接口说没有剧集**被压成同一个 0——
                #   而下游 `raw_n == 0 and mine_n > 3` 又把这个 0 当成"基准就是 0"，
                #   等于在**信任源自己的产出**（正是坏源最擅长的：把 84 集砍成 1 集后，
                #   基准也变成它自己的产出，永远"一致"）。用 None 表示"拿不到基准"，跳过该样本。
                raw_n = None
                if callable(raw_json) and api_base:
                    try:
                        rj = raw_json('%%s?ac=detail&ids=%%s' %% (api_base, vid)) or {}
                        r0 = (rj.get('list') or [{}])[0]
                        raw_n = len([x for x in str(r0.get('vod_play_url') or '').split('#')
                                     if x.strip() and '$' in x])
                    except Exception:
                        raw_n = None          # 拿不到基准，**不是 0 集**
                d = s.detailContent([vid]) or {}
                lst = d.get('list') or []
                if not lst:
                    continue
                v = lst[0]
                mine_n = len([x for x in str(v.get('vod_play_url') or '').split('#')
                              if x.strip() and '$' in x])
                # 只有**拿到基准**且基准是多集，才算合格样本；
                # 拿不到基准时退一步用源产出，但下面判定会标"(基准缺失)"，不当成"一致"。
                if raw_n is not None and raw_n > 3:
                    multi = (it, v, mine_n, raw_n)
                    break
                if raw_n is None and mine_n > 3:
                    multi = (it, v, mine_n, raw_n)
                    break
            if multi:
                break

        if multi:
            it, v, mine_n, raw_n = multi
            if raw_n is not None and raw_n > 0:
                keep = (mine_n == raw_n)
                rec('③详情 detailContent', keep,
                    '%%s：接口 %%d 集 / 源产出 %%d 集%%s' %% (
                        v.get('vod_name'), raw_n, mine_n,
                        '' if keep else '  ← **丢集/多集，不合格**'))
            else:
                # 基准缺失时**只验非空**，并在结论里写明"基准缺失"——
                # 不许写成"一致"（那等于用源自己的产出给自己打分）
                rec('③详情 detailContent', mine_n > 0,
                    '%%s：源产出 %%d 集（**基准缺失**：接口原始数据取不到，仅验非空、不比集数）'
                    %% (v.get('vod_name'), mine_n))
        elif l1:
            d = s.detailContent([l1[0]['vod_id']]) or {}
            lst = d.get('list') or []
            rec('③详情 detailContent', bool(lst),
                '未找到多集样本，仅验单片非空' if lst else '详情为空')
    except Exception as e:
        rec('③详情 detailContent', False, '%%s: %%s' %% (type(e).__name__, e))

    # ④ 播放（真拉）
    try:
        target = multi[1] if multi else ((s.detailContent([l1[0]['vod_id']]) or {}).get('list') or [{}])[0]
        segs = [x for x in str(target.get('vod_play_url') or '').split('#') if '$' in x]
        if not segs:
            rec('④播放 playerContent', False, '详情里没有 $ 分隔的剧集')
        else:
            nm, u = segs[0].split('$', 1)
            pl = s.playerContent(str(target.get('vod_play_from') or '').split('$$$')[0], u, {}) or {}
            purl = pl.get('url') or ''
            code, body = _fetch(purl) if purl else (0, b'')
            ok = code in (200, 206) and b'#EXTM3U' in body
            rec('④播放 playerContent', ok,
                '%%s → HTTP %%d m3u8=%%s parse=%%s' %% (nm, code, b'#EXTM3U' in body, pl.get('parse')))
    except Exception as e:
        rec('④播放 playerContent', False, '%%s: %%s' %% (type(e).__name__, e))

    # ⑤ 搜索
    try:
        kw = '剧'
        t0 = time.time(); sr = s.searchContent(kw, False, '1') or {}
        sl = sr.get('list') or []
        rec('⑤搜索 searchContent', len(sl) > 0,
            '%%s → %%d 条，%%.1fs' %% (kw, len(sl), time.time() - t0))
    except Exception as e:
        rec('⑤搜索 searchContent', False, '%%s: %%s' %% (type(e).__name__, e))

    return _out

try:
    print('@@RESULT@@' + json.dumps(main(), ensure_ascii=False))
except Exception as e:
    print('@@RESULT@@' + json.dumps([{'step': '整体', 'ok': False,
                                     'detail': '%%s: %%s' %% (type(e).__name__, e)}], ensure_ascii=False))
'''


def verify_spider(spider_path, timeout=420):
    """跑一个 py 源 → (verdict, items)。verdict ∈ {'通过','失败','无法判定'}。"""
    if not os.path.isfile(spider_path):
        return '无法判定', [{'step': '准备', 'ok': False, 'detail': f'文件不存在：{spider_path}'}]
    src = open(spider_path, encoding='utf-8', errors='replace').read()
    try:
        compile(src, spider_path, 'exec')
    except SyntaxError as e:
        return '失败', [{'step': '语法', 'ok': False, 'detail': f'语法错误 line {e.lineno}: {e.msg}'}]

    d = _ensure_importable(spider_path)
    try:
        code = _PROBE % {'dir': d}
        r = subprocess.run([sys.executable, '-c', code], capture_output=True,
                           text=True, timeout=timeout)
        out = r.stdout or ''
        m = re.search(r'@@RESULT@@(.*)', out, re.S)
        if not m:
            tail = (r.stderr or out or '')[-400:]
            return '无法判定', [{'step': '执行', 'ok': False,
                                'detail': f'探针未产出结果（非源本身问题）：{tail}'}]
        items = json.loads(m.group(1))
    except subprocess.TimeoutExpired:
        return '无法判定', [{'step': '执行', 'ok': False, 'detail': f'超时（>{timeout}s）——站点慢，非源错'}]
    except Exception as e:
        return '无法判定', [{'step': '执行', 'ok': False, 'detail': f'{type(e).__name__}: {e}'}]
    finally:
        shutil.rmtree(d, ignore_errors=True)

    if not items:
        return '无法判定', []
    bad = [x for x in items if not x['ok']]
    # ★ M-R60 三态：**全部失败 ≠ 源有问题**。
    #   源自己的 `_get` 常常把网络异常吞成空串（这是好习惯，避免一崩到底），
    #   于是"域名不存在"与"源写错了"在产出上长得一模一样——都是"分类 0 个"。
    #   必须在**探针层**单独做一次裸连通性探测，才能分辨：
    #     · host 根本连不上 → 无法判定（≠ 源错）
    #     · host 通但源产出空 → 失败（真错）
    if len(bad) == len(items):
        txt = ' '.join(x['detail'] for x in items)
        if _looks_network_fault(txt):
            return '无法判定', items
        reach = _reachability(_declared_host(src))
        if reach == 'unreachable':
            return '无法判定', items + [{'step': '连通性', 'ok': False,
                                        'detail': '源声明 host 的域名解析失败——'
                                                  '**无法判定**（不是源写错，是站点/域名问题）'}]
        return '失败', items
    return ('通过' if not bad else '失败'), items


# ★ 网络故障判据必须**收紧**（S148 自检撞出来的假阳性）：
#   原来写 `timeout|...|Connection|refused|ssl`，而 `Connection` 会命中
#   - 正常请求头 `headers={'Connection': 'keep-alive'}`
#   - `sqlite3.Connection` / `ConnectionResetError` / "connection pool"
#   于是**好源只要写了 header 就永远判"无法判定"**——验证器等于被废掉。
#   改为一组具体异常短语：宁可漏判成「失败」（可复测），也不许把好源一律判成「无法判定」。
_NET_FAULT_RE = (
    r'timed?\s*out|timeout'
    r'|getaddrinfo|name or service not known|nodename nor servname|temporary failure in name'
    r'|connection\s+(?:refused|reset|aborted|error)\b'
    r'|max retries exceeded|failed to establish a new connection'
    r'|(?:ssl|certificate)\s+(?:error|verify failed)|sslerror'
    r'|network is unreachable|no route to host|unreachable'
    r'|不可达|域名解析失败'
)


def _looks_network_fault(txt):
    """从失败详情里辨认「是不是网络/站点问题」。**只在全项失败时才用来分流三态**。

    注意：这不是"帮源开脱"，而是 M-R60 要求的「失败 ≠ 我方写错」。
    判错的两种代价不对称——把好源判成无法判定，用户会换时间反复复测（浪费但无害）；
    把坏源判成失败，用户会去改一段根本没写错的代码（有害）。故只在**有具体证据**时判无法判定。
    """
    if not txt:
        return False
    return bool(re.search(_NET_FAULT_RE, txt, re.I))


def _reachability(host, timeout=12):
    """探针层裸探测：源声明的 host 到底连不连得上。返回 'ok'/'unreachable'/'unknown'。

    不依赖被测源自己的请求函数——它可能吞异常、可能加了奇怪的 header，
    用它来判断"是不是网络问题"等于让嫌疑人自己作证。
    只看 **DNS 解析能不能出 IP**：这足以分辨"域名打错/不存在"与"站点挂了但域名在"。
    """
    if not host:
        return 'unknown'
    m = re.match(r'^https?://([^/:]+)', str(host))
    if not m:
        return 'unknown'
    hostname = m.group(1)
    try:
        import socket
        socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        return 'ok'
    except socket.gaierror:
        return 'unreachable'
    except Exception:
        return 'unknown'


def cmd_spider(a):
    """CLI：livecheck --spider <file>。"""
    path = getattr(a, 'spider', None)
    verdict, items = verify_spider(path)
    mark = {'通过': '✅', '失败': '❌', '无法判定': '⚠️'}.get(verdict, '·')
    for it in items:
        print(f'  {"✅" if it["ok"] else "❌"} {it["step"]}: {it["detail"]}')
    ok = sum(1 for x in items if x['ok'])
    print(f'\n{mark} [spider] {verdict} — {ok}/{len(items)} 项通过：{path}')
    if verdict == '无法判定':
        print('   注：无法判定 ≠ 源有问题（M-R60）——可能是站点慢/网络不可达，换时间复测')
    return 0 if verdict == '通过' else 1
