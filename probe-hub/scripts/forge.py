#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写源模块（模块 F）——把「写一个 py 源」这件事从「临时手搓 + 手动自验」变成一条命令。

## 为什么单开一个模块（S161·P018）

`livecheck --spider` 只能**验**源，验完的源原来落在 `/tmp`，一次关机就没了；
「写」这一步更是完全没入口——上一轮（S148）跑通了 api.yyzy-tv.vip，
但产物 `/tmp/forge/spider.py` 不在任何库、不在任何账上。

本模块补三件事：
  ① **sources/** 落点——验过的源留档，不再随 /tmp 蒸发
  ② **forge verify**——验源 + 过则自动留档，一条命令完成
  ③ **forge list**——列出留档的源与最近验证结果

## 不做的事（边界写清，防它长成第二个遮天法）

- **不做「配置驱动自动生成源」**。S161 实测遮天法 v3.1：10 项宣称能力 7 项无调用点、
  23 个配置段 4 段从未被读。这套「改配置就能适配新站」的写法在我们这里是**未经验证的**，
  不引进。
- 本模块只负责**收**（留档）、**验**（真跑）、**列**（盘点）。
  真正「写」的动作仍然是人/AI 逐站写——这是诚实的能力边界，不是缺口。
  遮得住的是「写不出来」，遮不住的是「假装能自动写」。
"""
import os
import re
import sys
import json
import time
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SOURCES_DIR = os.path.join(SKILL_ROOT, 'sources')
INDEX_FP = os.path.join(SOURCES_DIR, 'index.json')


def _ensure_dir():
    os.makedirs(SOURCES_DIR, exist_ok=True)


def _load_index():
    if not os.path.exists(INDEX_FP):
        return {'version': 1, 'sources': []}
    try:
        with open(INDEX_FP, encoding='utf-8') as f:
            d = json.load(f)
        if not isinstance(d.get('sources'), list):
            raise ValueError('sources 非数组')
        return d
    except Exception as e:
        # M-R60：索引坏了 ≠ 没有源。显式报，不许折叠成空表。
        print(f'⚠️ 索引损坏（{type(e).__name__}: {e}）——**无法判定**已有哪些源，'
              f'请人工检查 {INDEX_FP}')
        return None


def _save_index(d):
    _ensure_dir()
    tmp = INDEX_FP + '.tmp'
    # 写完必须关文件再回读（S160 教训：json.dump(d, open(fp,'w')) 不关句柄就 load 会读到半截）
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX_FP)


def _upsert(entry):
    d = _load_index()
    if d is None:
        return False
    rows = d['sources']
    for i, r in enumerate(rows):
        if r.get('file') == entry['file']:
            rows[i] = entry
            break
    else:
        rows.append(entry)
    rows.sort(key=lambda r: r.get('file', ''))
    _save_index(d)
    # M-R77：写后回读**该字段的值**，不只看回执
    d2 = _load_index()
    if d2 is None:
        return False
    return any(r.get('file') == entry['file'] and r.get('last_verdict') == entry['last_verdict']
               for r in d2['sources'])


def _guess_name(path):
    """索引里的 `name`：**优先读源文件里 `getName()` 的返回值**，读不到才退回文件名。

    ★ S162 补：原实现是 `os.path.splitext(os.path.basename(path))[0]` ——
      **只取文件名，从没读过源里的 getName()**。后果是 `forge list` 显示的
      `yyzy_vip` 与源实际对外报的站点名（`优质影视`）不一致：
      索引记的是"文件叫什么"，而用户/客户端看到的是"源自称什么"，两回事。
      更要命的是我把 `getName()` 从 "优质" 改成 "优质影视" 之后，索引纹丝不动——
      **改了源、索引不跟**，又是一处"副本静默过期"（M-R40）。

    三态（M-R60）：
      · 读到非空 getName → 用它
      · 文件里没有 `def getName` → 退回文件名（源确实没声明，不算错）
      · 文件读不了 / 正则崩 → 返回带 `?` 的占位，**不冒充"没有"**
    """
    base = os.path.splitext(os.path.basename(path))[0]
    try:
        src = open(path, encoding='utf-8', errors='replace').read()
    except Exception:
        return f'{base}?'          # 读不到 ≠ 没有 getName
    # 只认 `def getName(...)` 之后**同函数体内**的第一个 return 字符串。
    # ★ 坑（S162 实测）：第一版正则写 `\)\s*:\s*\n((?:[ \t]+.*\n)+)`——要求函数体
    #   至少有一行"缩进 + 内容 + 换行"。可真实写法常是 `def getName(self):\n    return "x"`
    #   最后一行**没有尾随换行**，或 `return` 紧贴下一行——于是全被判成"没有 getName"，
    #   静默退回文件名。测试时三个自造样例全落空才抓到。
    #   改成：从 `def getName` 起截一小段，在缩进块内找第一个 return（不苛求换行）。
    m = re.search(r'def\s+getName\s*\([^)]*\)\s*:', src)
    if not m:
        return base                # 明确没有这个方法 → 退回文件名
    # 取方法头之后最多 800 字符（够装一个 return，又不会跨到别的方法里）
    seg = src[m.end(): m.end() + 800]
    for line in seg.split('\n')[1:]:          # 跳过 `def` 那一行的行尾
        s = line.strip()
        if not s or s.startswith('#'):
            continue                          # 空行/注释不算"实语句"
        r = re.search(r'return\s+([\'"])(.+?)\1', s)
        if r:
            return r.group(2).strip() or base
        # 遇到首条非 return 的实语句 → 该方法没返回字面量，别继续往下翻
        if not s.startswith(('"', "'")):
            break
    return base


def _revoke(fname):
    """把某条留档**从索引里摘掉**（文件由调用方删）。返回 True=摘干净了。

    为什么需要它（S161 探针撞出来的真缺陷）：
      故障注入那一轮，坏源被错误留档 → 探针的「清理后清零」判据只看**这次跑完**
      索引条数有没有回落到 before，却没想过**上一轮注入的脏数据已固化在索引里**。
      于是注入一轮之后，索引里永久多出一条 `bad_syntax.py`，而磁盘上文件早没了——
      变成「索引有、磁盘无」的孤儿账。**探针自己造的脏，探针必须自己擦。**
    """
    d = _load_index()
    if d is None:
        return False
    n = len(d['sources'])
    d['sources'] = [r for r in d['sources'] if r.get('file') != fname]
    if len(d['sources']) == n:
        return True                    # 本来就没有，也算干净
    _save_index(d)
    d2 = _load_index()
    if d2 is None:
        return False
    return not any(r.get('file') == fname for r in d2['sources'])


def cmd_forge(a):
    sub = getattr(a, 'cmd', None)
    if sub == 'list':
        return _cmd_list(a)
    if sub == 'verify':
        return _cmd_verify(a)
    if sub == 'classify':
        return _cmd_classify(a)
    print('用法：')
    print('  tvbox probe forge verify <源.py> [--save]   # 验源；--save 通过则留档到 sources/')
    print('  tvbox probe forge list                      # 列出留档的源与最近验证结果')
    print('  tvbox probe forge classify <api|--in 清单.json>  # 实测判定源类型（不猜路径）')
    return 1


# ---------- S162：源类型实测判定（classify） ----------
# 为什么需要它：成品源库 99 条 `源类型` 为空，和 97 条真直链混在一起分不出来。
# 第一反应是**按 url 路径猜**（`api.php/provide/vod` → CMS采集）——实测证明这是错的：
#   ① 真标准 CMS 的 `ac=class` **也返回视频列表而不是分类**，说明这参数本就不是标准，
#      拿它当判据会把"像 CMS"当成"是 CMS"；
#   ② 我按路径猜了 5 条，实测后 **4 条是错的**（含子域名带 m3u8 实为 CMS、非标准路径实为 CMS）。
# 所以判据改成看**响应行为**：打 `ac=detail&pg=1`，看返回里有没有
# `vod_play_url` + `vod_play_from` 这对字段——这才是"客户端能不能自己解析"的直接证据。
#
# 四态（M-R60），**绝不把连不上折叠成"不是 CMS"**：
#   CMS采集  ：JSON 且含 vod_play_url + vod_play_from
#   m3u8直链 ：返回体以 #EXTM3U 开头
#   JSON配置 ：JSON 但有 list 却没播放字段（结构不符，需另判）
#   无法判定 ：超时 / 非 200 / 非 JSON / 无 list —— 返回 None，**调用方不许写库**

_CLS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _cls_fetch(url, timeout=15):
    """→ (body, err)。err 非空＝无法判定（不是"没有"）。"""
    import subprocess
    try:
        r = subprocess.run(
            ['curl', '-sL', '--max-time', str(timeout), '-A', _CLS_UA,
             '-w', '\n__CODE__%{http_code}', url],
            capture_output=True, text=True, timeout=timeout + 8)
        raw = r.stdout or ''
        if '__CODE__' not in raw:
            return '', 'curl 无输出'
        body, code = raw.rsplit('\n__CODE__', 1)
        if code.strip() != '200':
            return '', f'HTTP {code.strip()}'
        return body, ''
    except Exception as e:
        return '', f'{type(e).__name__}'


def classify_one(api, timeout=15):
    """→ (源类型 or None, 依据)。None＝无法判定。"""
    api = (api or '').strip()
    if not api:
        return None, 'api 为空'
    sep = '&' if '?' in api else '?'
    body, err = _cls_fetch(api + sep + 'ac=detail&pg=1', timeout)
    if err:
        return None, f'无法判定：{err}'
    t = body.lstrip()
    if t.startswith('#EXTM3U'):
        return 'm3u8直链', '返回体以 #EXTM3U 开头'
    try:
        d = json.loads(body)
    except Exception:
        return None, f'无法判定：非 JSON（前 40 字 {t[:40]!r}）'
    lst = d.get('list') if isinstance(d, dict) else None
    if not isinstance(lst, list) or not lst:
        return None, '无法判定：JSON 但无 list'
    it = lst[0] if isinstance(lst[0], dict) else {}
    if 'vod_play_url' in it and 'vod_play_from' in it:
        return 'CMS采集', '返回含 vod_play_url + vod_play_from'
    return 'JSON配置', f'JSON 但无播放字段（keys={list(it.keys())[:6]}）'


def _cmd_classify(a):
    """forge classify：批量实测判定源类型。

    ```bash
    tvbox probe forge classify https://x.com/api.php/provide/vod   # 单条
    tvbox probe forge classify --in 待判清单.json                   # 批量
    ```
    清单格式：[{"api": "...", "name": "可选"}]
    只打印结论与依据，**不写库**——写库是业务动作，应由调用方显式做（职责分离）。
    """
    single = getattr(a, 'api', None)
    inf = getattr(a, 'infile', None)
    if single:
        v, why = classify_one(single)
        print(f"{single}\n  → {v or '无法判定'} | {why}")
        return 0 if v else 1
    if not inf:
        print('缺参数：forge classify <api> 或 --in <清单.json>')
        return 2
    if not os.path.exists(inf):
        print(f'❌ 清单不存在：{inf}')
        return 2
    rows = json.load(open(inf, encoding='utf-8'))
    if isinstance(rows, dict):
        rows = rows.get('sources') or list(rows.values())
    todo = [(r.get('api') or r.get('api接口') or '', r.get('name') or r.get('名称') or '?')
            for r in rows]
    todo = [(u, n) for u, n in todo if u]
    print(f'🔎 实测判定 {len(todo)} 条（判据＝响应行为，非 url 路径）\n')

    def one(t):
        return t[0], t[1], *classify_one(t[0])

    # ★ S163：改走统一入口（原 `cf.ThreadPoolExecutor(max_workers=8)` 就地拍并发数，
    #   且未引入 concurrency ——audit_concurrency 判据①/③ 双违规）。
    #   keep_order=True：下面按条顺序打印与落 res，错位会让「结论↔源」对不上。
    from concurrency import run_bounded, TIER_WEB, ConcurrencyError
    try:
        _rep = run_bounded(todo, one, tier=TIER_WEB, label='forge-classify',
                           keep_order=True, on_error='record')
    except ConcurrencyError as e:
        print(f'❌ {e}')
        return 2
    res = []
    for (api, name), r in zip(todo, _rep.results):
        if r is None:
            # 线程异常 → 无法判定（不许折叠成「不是 CMS」这种有结论的说法，M-R60）
            v, why = None, '判定线程异常（并发报告已记录）——无法判定'
        else:
            _, _, v, why = r
        res.append({'name': name, 'api': api, 'verdict': v, 'why': why})
        mark = {'CMS采集': '✅', 'm3u8直链': '✅', 'JSON配置': '⚠️'}.get(v, '❔')
        print(f'{mark} {str(name)[:18]:20s} → {str(v or "无法判定"):8s} | {why}')
    from collections import Counter
    print('\n汇总:', dict(Counter(x['verdict'] or '无法判定' for x in res)))
    out = getattr(a, 'out', None)
    if out:
        json.dump(res, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'→ {out}')
    print('（本命令不写库；写库请用 build.py 的 _wrap 按 schema 包装后再 batch_update）')
    return 0


def _cmd_verify(a):
    quiet = bool(getattr(a, 'quiet', False))
    _p = print
    if quiet:
        globals()['print'] = lambda *x, **k: None
    try:
        return _cmd_verify_inner(a)
    finally:
        globals()['print'] = _p


def _cmd_verify_inner(a):
    path = getattr(a, 'file', None)
    if not path:
        print('缺源文件路径：tvbox probe forge verify <源.py> [--save]')
        return 2
    if not os.path.exists(path):
        print(f'❌ 文件不存在：{path}（这是"我给的路径错了"，不是"源挂了"——M-R60 分清）')
        return 2

    sys.path.insert(0, HERE)
    import spider_verify
    verdict, items = spider_verify.verify_spider(path)
    mark = {'通过': '✅', '失败': '❌', '无法判定': '⚠️'}.get(verdict, '·')
    for it in items:
        print(f'  {"✅" if it["ok"] else "❌"} {it["step"]}: {it["detail"]}')
    ok_n = sum(1 for x in items if x['ok'])
    print(f'\n{mark} [forge] {verdict} — {ok_n}/{len(items)} 项通过：{path}')
    if verdict == '无法判定':
        print('   注：无法判定 ≠ 源有问题（M-R60）——可能站点慢/网络不可达，换时间复测；'
              '此种结果**不许留档**')

    if getattr(a, 'save', False):
        if verdict != '通过':
            print(f'⛔ 未留档：验证结果={verdict}。sources/ 只收「通过」的源——'
                  f'把没验过的源放进去，等于给下一个人埋雷。')
            return 0 if verdict == '无法判定' else 1
        dst = os.path.join(SOURCES_DIR, os.path.basename(path))
        _ensure_dir()
        if os.path.abspath(path) != os.path.abspath(dst):
            shutil.copy2(path, dst)
        entry = {
            'file': os.path.basename(dst),
            'name': _guess_name(dst),
            'verdict': verdict,
            'last_verdict': verdict,
            'steps': [{'step': it['step'], 'ok': it['ok'], 'detail': it['detail']} for it in items],
            'ok_n': ok_n,
            'total_n': len(items),
            'verified_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'src_path': os.path.abspath(path),
        }
        if _upsert(entry):
            print(f'💾 已留档：{dst}（索引已更新，回读核对通过）')
        else:
            print(f'⚠️ 留档疑似失败：文件已复制但索引回读对不上——请人工核对 {INDEX_FP}')
            return 1
    return 0 if verdict == '通过' else 1


def _cmd_list(a):
    d = _load_index()
    if d is None:
        return 1
    rows = d.get('sources') or []
    # 目录里有文件但索引里没有 = 有人绕过 forge 直接 cp 进来的，必须报出来
    on_disk = set()
    if os.path.isdir(SOURCES_DIR):
        on_disk = {f for f in os.listdir(SOURCES_DIR)
                   if f.endswith('.py') and not f.startswith('_')}
    indexed = {r.get('file') for r in rows}
    orphan = sorted(on_disk - indexed)
    missing = sorted(indexed - on_disk)

    if not rows:
        print('📭 sources/ 里还没有留档的源')
    else:
        print(f'📚 写源留档：{len(rows)} 条')
        for r in rows:
            m = {'通过': '✅', '失败': '❌', '无法判定': '⚠️'}.get(r.get('last_verdict'), '·')
            print(f'  {m} {r.get("file"):24s} {r.get("ok_n")}/{r.get("total_n")} '
                  f'{r.get("verified_at", "")}  {r.get("name", "")}')
    if orphan:
        print(f'\n⚠️ 磁盘上有但索引里没有（{len(orphan)} 个）——绕过 forge 直接放进来的，'
              f'验证结果未知：')
        for f in orphan:
            print(f'   · {f}')
    if missing:
        print(f'\n⚠️ 索引里有但磁盘上没有（{len(missing)} 个）——文件被删了但账没销：')
        for f in missing:
            print(f'   · {f}')
    return 0
