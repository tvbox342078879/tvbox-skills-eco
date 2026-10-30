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
from datetime import datetime

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


# ---------- S204：本地 py/js 源清单生成器（packlocal）+ js 静态校验 ----------
# 借鉴用户提供的「本地PY.JS读取 auto_json.py」（1026）：本地脚本源 → file:// sites 配置
# 的分发形态。探源司侧补最后一跳：扫描 → 验源（py 真跑 / js 静态）→ 出 auto_config.json。
# 三态契约沿用 M-R60；失败/无法判定逐条给原因，绝不静默剔除（M-R32）。


def _verify_js(path):
    """drpy js 源静态校验 → (verdict, items)。静态级，明示≠真跑（js 真跑需 QuickJS 宿主，正本边界）。"""
    items = []
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return '失败', [{'step': '文件存在', 'ok': False, 'detail': f'{path} 不存在或为空'}]
    items.append({'step': '文件存在', 'ok': True, 'detail': f'{os.path.getsize(path)} 字节'})
    try:
        src = open(path, encoding='utf-8').read()
        items.append({'step': 'utf-8 可读', 'ok': True, 'detail': f'{len(src)} 字符'})
    except UnicodeDecodeError:
        try:
            src = open(path, encoding='gbk', errors='replace').read()
            items.append({'step': '编码', 'ok': True, 'detail': 'utf-8 失败，gbk 容错读取'})
        except Exception as e:
            return '失败', items + [{'step': '编码', 'ok': False, 'detail': str(e)}]
    except Exception as e:
        return '失败', items + [{'step': '读取', 'ok': False, 'detail': str(e)}]

    feats = {
        '导出结构(export/globalThis/Spider)': any(k in src for k in ('export', 'globalThis', 'Spider')),
        'init/home 入口': ('function init' in src) or ('init:' in src) or ('function home' in src) or ('home:' in src),
        'category/detail': ('category' in src) and ('detail' in src),
        'play/search': ('play' in src) and ('search' in src),
        '网络请求(req/fetch/reqs)': any(k in src for k in ('req(', 'fetch(', 'reqs(', 'request(')),
    }
    hit = sum(feats.values())
    for k, v in feats.items():
        items.append({'step': f'特征·{k}', 'ok': v, 'detail': '命中' if v else '未命中'})
    if hit >= 3:
        verdict = '通过'
    elif hit >= 1:
        verdict = '无法判定'
    else:
        verdict = '失败'
    items.append({'step': '静态判定', 'ok': verdict == '通过',
                  'detail': f'{hit}/5 特征命中 → {verdict}（静态级≠真跑，js 真跑需 drpy 宿主）'})
    return verdict, items


def _verify_jar(path):
    """本地 jar 源静态校验 → (verdict, items)。

    借鉴 pro 包 demo.json（2026-09-30）：jar 本地源也是 type=3，但写法是
    `api="csp_<名>"` + `"jar": "./jar/xxx.jar"`（api 不是路径，是 csp 标识）。
    静态级只验「非空 + 是有效 zip」，不真跑 Java（真跑需壳子 spider 引擎，正本边界）。
    实证样本：FloatSpider → api="csp_FloatSpider", jar="./jar/float-spider.jar"。
    """
    import zipfile
    items = []
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return '失败', [{'step': '文件存在', 'ok': False, 'detail': f'{path} 不存在或为空'}]
    size = os.path.getsize(path)
    items.append({'step': '文件存在', 'ok': True, 'detail': f'{size} 字节'})
    try:
        with zipfile.ZipFile(path) as z:
            n = len(z.namelist())
        items.append({'step': '有效 jar(zip)', 'ok': True, 'detail': f'{n} 个条目'})
        verdict = '通过'
    except Exception as e:
        return '失败', items + [{'step': '有效 jar(zip)', 'ok': False, 'detail': str(e)}]
    items.append({'step': '静态判定', 'ok': verdict == '通过',
                  'detail': 'jar 本质是 zip；静态级≠真跑 Java（需壳子 spider 引擎）'})
    return verdict, items


_PACK_EXCLUDE_FILES = {
    'auto_json.py', 'auto_json.js', 'auto_loader.py', 'loader.py', 'loader.js',
    'main.py', 'main.js', 'AutoJsonGenerator.py', 'AutoJsonGenerator.js',
}


def _cmd_packlocal(a):
    """forge packlocal：扫本地目录 py/js/jar → 验源 → 生成可导入的 auto_config.json。

    ```bash
    tvbox probe forge packlocal --dir <目录> [--out auto_config.json] [--keep-unverified]
                               [--file-base file://storage/emulated/0/] [--flat]
    ```
    - py  : 走 spider_verify 五步真跑（与 forge verify 同口径）
    - js  : 静态校验（5 特征，≥3 通过；静态级≠真跑）
    - jar : 静态校验（非空 + 有效 zip；借鉴 pro 包 demo.json，jar 源也是 type=3，
            写法 `api="csp_<名>"` + `"jar":"./jar/xxx.jar"`，不是路径型 api）
    - api : **默认 `./<相对路径>`（S205 修正）** —— 壳子（OK影视 5.1.6）对 py spider 的
            `file://` 绝对路径**静默不执行**，界面只表现为「一直转圈、无任何报错」；
            用户原工具自带配置即 `"api": "./本地PY.JS读取 auto_json.py"`。
            查过现场：v2 用 `file://storage/emulated/0/...` → 转圈；v3 改 `./` → 正常。
            需要绝对路径时显式传 `--file-base file://storage/emulated/0/`，
            报告里会标 `api_mode`，不许默认回到 file://（M-R148 完成态凭落点）。
    - --flat（默认开）：生成「直接全挂」形态——每个源直接成一个 site 写进 sites，
            不包加载器包裹，对应 ok.json / demo.json 的扁平写法；关掉则不再单独标记
            mode（两种形态 JSON 结构一致，flat 是把已有正确行为显式化）。
    - 默认只收「通过」；--keep-unverified 时「无法判定」也进清单并打标
    """
    root = getattr(a, 'dir', None)
    out = getattr(a, 'out', None)
    keep_un = bool(getattr(a, 'keep_unverified', False))
    flat = bool(getattr(a, 'flat', True))
    file_base = getattr(a, 'file_base', None) or ''
    api_prefix = file_base if file_base else './'
    api_mode = 'file_base' if file_base else 'relative'
    if not root or not os.path.isdir(root):
        print('❌ --dir 必须是存在的目录')
        return 2
    root_abs = os.path.abspath(root)

    # 收集（递归 + 排除门阀 + (扩展名,模块名) 去重——口径对齐 auto_json.py）
    found, seen = [], set()
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = [d for d in dirnames if not d.startswith('__') and d not in ('test', 'backup', 'temp')]
        for f in filenames:
            lower = f.lower()
            ext = ('.js' if lower.endswith('.js')
                   else '.py' if lower.endswith('.py')
                   else '.jar' if lower.endswith('.jar') else None)
            if not ext or f in _PACK_EXCLUDE_FILES or f.startswith('自动加载'):
                continue
            # jar 的编译产物（oat/锁文件）排除
            if lower.endswith(('.flock', '.odex', '.vdex', '.class')):
                continue
            mod = os.path.splitext(f)[0]
            key = (ext, mod)
            if key in seen:
                print(f'⏭ 去重跳过：{f}（同扩展名同名模块已收）')
                continue
            seen.add(key)
            found.append((ext, os.path.join(dirpath, f)))

    sys.path.insert(0, HERE)
    import spider_verify
    sites, report = [], []
    _EXT_LABEL = {'.js': 'JS', '.py': 'PY', '.jar': 'JAR'}
    for ext, fp in sorted(found):
        rel = os.path.relpath(fp, root_abs).replace('\\', '/')
        mod = os.path.splitext(os.path.basename(fp))[0]
        if ext == '.py':
            verdict, items = spider_verify.verify_spider(fp)
        elif ext == '.jar':
            verdict, items = _verify_jar(fp)
        else:
            verdict, items = _verify_js(fp)
        ok_n = sum(1 for x in items if x['ok'])
        mark = {'通过': '✅', '失败': '❌', '无法判定': '⚠️'}.get(verdict, '·')
        print(f'{mark} [{_EXT_LABEL[ext]}] {rel} — {verdict}（{ok_n}/{len(items)} 项）')
        entry = {'file': rel, 'type': _EXT_LABEL[ext], 'verdict': verdict,
                 'ok_n': ok_n, 'total_n': len(items),
                 'items': [{'step': x['step'], 'ok': x['ok'], 'detail': x['detail']} for x in items]}
        if verdict == '通过' or (keep_un and verdict == '无法判定'):
            if ext == '.jar':
                # 借鉴 pro 包 demo.json：jar 源 api 是 csp_ 标识而非路径，jar 字段指向包
                api = 'csp_' + mod
                site = {
                    'key': 'jar_' + mod,
                    'name': mod + '[JAR]',
                    'type': 3, 'api': api, 'jar': api_prefix + rel,
                    'searchable': 1, 'quickSearch': 1, 'filterable': 1,
                    'changeable': 1, 'playerType': 2,
                }
            else:
                api = api_prefix + rel
                site = {
                    'key': ('js_' if ext == '.js' else 'py_') + mod,
                    'name': mod + ('[JS]' if ext == '.js' else '') + ('⚠未真测' if verdict != '通过' else ''),
                    'type': 3, 'api': api,
                    'searchable': 1, 'quickSearch': 1, 'filterable': 1,
                    'changeable': 1, 'playerType': 2,
                }
            sites.append(site)
            entry['in_config'] = True
            entry['api'] = api
        else:
            entry['in_config'] = False
        report.append(entry)

    n_pass = sum(1 for r in report if r['verdict'] == '通过')
    n_un = sum(1 for r in report if r['verdict'] == '无法判定')
    n_fail = sum(1 for r in report if r['verdict'] == '失败')
    print(f'\n三态汇总：通过 {n_pass} / 无法判定 {n_un} / 失败 {n_fail}；进清单 {len(sites)} 条')
    print(f'api 写法：{api_mode} → 前缀 "{api_prefix}"'
          + ('（壳子 5.1.6 可用）' if api_mode == 'relative' else
             '（⚠ file:// 绝对路径，部分壳子对 py spider 静默不执行，只在明确需要时用）'))
    print(f'分发形态：{"flat 直接全挂（每个源一个 site，不包加载器）" if flat else "loader 包裹形态"}')
    if not report:
        print('（目录里没有 py/js 源）')
        return 1

    cfg = {'sites': sites, '公告': '公众号℗知源峰汇 免费分享',
           '_meta': {'生成': time.strftime('%Y-%m-%dT%H:%M:%S'),
                     '根目录': root_abs, 'api_mode': api_mode,
                     'api_prefix': api_prefix, 'mode': 'flat' if flat else 'loader',
                     '说明': ('api 相对配置文件所在目录（壳子 5.1.6 认 ./ 写法）；'
                              'flat 形态每个源直接成 site，对应 ok.json/demo.json')
                             if api_mode == 'relative' else 'api 用 file_base 绝对路径（--file-base 显式指定）'}}
    out_path = out or os.path.join(root_abs, 'auto_config.json')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    json.dump(cfg, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    rep_path = os.path.splitext(out_path)[0] + '_验证报告.json'
    json.dump(report, open(rep_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    # 写后回读（不是只看回执）
    back = json.load(open(out_path, encoding='utf-8'))
    print(f'💾 配置 {out_path}（回读 {len(back["sites"])} 条）｜报告 {rep_path}')
    return 0


def cmd_forge(a):
    sub = getattr(a, 'cmd', None)
    if sub == 'list':
        return _cmd_list(a)
    if sub == 'verify':
        return _cmd_verify(a)
    if sub == 'classify':
        return _cmd_classify(a)
    if sub == 'audit':
        return _cmd_audit(a)
    if sub == 'profile':
        return _cmd_profile(a)
    if sub == 'packlocal':
        return _cmd_packlocal(a)
    print('用法：')
    print('  tvbox probe forge verify <源.py> [--save]   # 验源；--save 通过则留档到 sources/')
    print('  tvbox probe forge list                      # 列出留档的源与最近验证结果')
    print('  tvbox probe forge audit                     # ★留档审计：七项闸门查账实一致性（S194）')
    print('  tvbox probe forge profile <url>             # ★站点画像：特征→防御等级→推荐命令（S194）')
    print('  tvbox probe forge classify <api|--in 清单.json>  # 实测判定源类型（不猜路径）')
    print('  tvbox probe forge packlocal --dir <目录> [--out x.json]  # ★本地 py/js 扫描→验源→出清单（S204）')
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


# ---------- S194 借鉴落地①：留档审计（七项闸门） ----------
# 思路移植自道长 spider-craft/check_market.py：前置条件一项不过就整体退出一条，
# 不许带着坏账往下跑。审计对象是 sources/ 这本账（索引 index.json ↔ 磁盘 .py），
# 七项各抓一种**不同的坏法**（⑤ 与 ④ 看似重复：② 出事后 ④ 的集合比照样相等，
# 计数却会露馅——所以两项都留）：
#   ① 索引可读        坏了即致命（退出码 2），不许折叠成"没有源"（M-R60）
#   ② 条目 file 唯一   同一条源在册两次，后面每项对账都会错
#   ③ 四要素齐全      file/name/last_verdict/verified_at 缺一即残账
#   ④ 双向对账        磁盘有索引无=孤儿（绕过 forge 塞的）；索引有磁盘无=幽灵（账没销）
#   ⑤ 内部计数自洽    原始条数 = 去重后条数——② 的计数形态复视图（④ 的集合比
#                     会被重复 file 骗过：{a.py,a.py} 与 {a.py} 集合相等、计数不等；
#                     **不与磁盘比**——磁盘多文件是孤儿，④ 已按 WARN 报，再按 FAIL
#                     判一次就是同一坏法双重定罪）
#   ⑥ 在册必须「通过」 S161 铁律①：没验过/验失败的不许进 sources/
#   ⑦ 时间可解析      verified_at 坏了，"这条何时验的"就答不出来
# 退出码：0=通过（可有 WARN）；1=有 FAIL；2=索引损坏（致命）。


def _cmd_audit(a):
    d = _load_index()
    if d is None:
        print('❌ 索引不可读——审计无法进行（这不是"没有源"，是账坏了，退出码 2）')
        return 2
    rows = d.get('sources') or []
    on_disk = set()
    if os.path.isdir(SOURCES_DIR):
        on_disk = {f for f in os.listdir(SOURCES_DIR)
                   if f.endswith('.py') and not f.startswith('_')}
    indexed = [r.get('file') or '' for r in rows]
    idx_set = set(indexed)

    oks, warns, fails = [], [], []

    def gate(no, name, ok, level, why):
        mark = '✅' if ok else ('❌' if level == 'FAIL' else '⚠️')
        (oks if ok else fails if level == 'FAIL' else warns).append(f'{no} {name}')
        print(f'{mark} {no} {name}：{why}')

    gate('①', '索引可读', True, 'FAIL',
         f'{INDEX_FP} 可解析，sources 为数组（{len(rows)} 条）')

    dup = sorted({f for f in indexed if f and indexed.count(f) > 1})
    gate('②', '条目 file 唯一', not dup, 'FAIL',
         '无重复 file' if not dup else f'重复 {len(dup)} 个：{", ".join(dup)}')

    need = ('file', 'name', 'last_verdict', 'verified_at')
    bad3 = []
    for r in rows:
        miss = [k for k in need if not str(r.get(k) or '').strip()]
        if miss:
            bad3.append(f'{r.get("file") or "?"}(缺{"、".join(miss)})')
    gate('③', '四要素齐全', not bad3, 'FAIL',
         f'{len(rows)} 条均含 file/name/last_verdict/verified_at' if not bad3
         else f'{len(bad3)} 条残账：{"; ".join(bad3[:5])}')

    orphan = sorted(on_disk - idx_set)
    ghost = sorted(idx_set - on_disk)
    ok4 = not orphan and not ghost
    why4 = '磁盘 ↔ 索引一致（无孤儿无幽灵）'
    if orphan:
        why4 = (f'孤儿（磁盘有索引无）{len(orphan)} 个：{", ".join(orphan[:5])}'
                f'——绕过 forge 直接放进来的，验证结果未知')
    if ghost:
        why4 = ((why4 + '；') if orphan else '') + \
               f'幽灵（索引有磁盘无）{len(ghost)} 个：{", ".join(ghost[:5])}——文件没了账没销'
    gate('④', '双向对账', ok4, 'FAIL' if ghost else 'WARN', why4)

    # ⑤ 索引内部计数自洽：只查"原始条数 vs 去重后条数"。重复 file 时 ④ 的集合比
    #    照样相等（{a.py} == {a.py}），只有计数露馅——所以这项是 ② 的复视图。
    #    刻意**不与磁盘 .py 数比**：磁盘多出来是孤儿（④ 已按 WARN 报），
    #    这里再按 FAIL 判就是同一坏法双重定罪（S194 负向测试 A3 抓到的）。
    n_ok5 = len(rows) == len(idx_set)
    gate('⑤', '索引内部计数自洽', n_ok5, 'FAIL',
         f'{len(rows)} 条 = 去重后 {len(idx_set)} 条（无隐形重复）' if n_ok5
         else f'索引 {len(rows)} 条 ≠ 去重后 {len(idx_set)} 条——有重复 file 被集合比掩盖')

    bad6 = [f'{r.get("file")}({r.get("last_verdict")})' for r in rows
            if r.get('last_verdict') != '通过']
    gate('⑥', '在册者必须验过且通过', not bad6, 'FAIL',
         f'{len(rows)} 条 last_verdict 均为「通过」' if not bad6
         else f'{len(bad6)} 条没通过也在册：{"; ".join(bad6[:5])}（S161 铁律①）')

    bad7 = []
    for r in rows:
        ts = str(r.get('verified_at') or '')
        if not ts:
            continue                      # 缺字段归 ③ 管，这里只抓"有值但解析不了"
        try:
            datetime.fromisoformat(ts)
        except Exception:
            bad7.append(f'{r.get("file")}({ts})')
    gate('⑦', 'verified_at 可解析', not bad7, 'WARN',
         '全部可 fromisoformat 解析' if not bad7
         else f'{len(bad7)} 条时间坏：{"; ".join(bad7[:5])}')

    print(f'\n审计结果：{len(oks)} 项通过 / {len(warns)} 项警告 / {len(fails)} 项失败')
    if fails:
        print('❌ 审计不通过——账实不符或带了没验过的源，先修账再谈别的')
        return 1
    if warns:
        print('⚠️ 有警告（不阻塞）：孤儿文件建议补验补登或清走')
    else:
        print('✅ 七项闸门全过：sources/ 账实相符、在册者全部验过')
    return 0


# ---------- S194 借鉴落地②：站点画像（特征扫描 → 防御等级 → 下一步） ----------
# 判据移植自道长 spider-craft/analyze_site.py 的 43 项特征表与 judge。
# **改造点（不是照抄）**：道长的 modules_for 推荐的是它自己的 modules/*.md；
# 我们改成 `_next_steps`——直接推荐**探源司自己的命令**，画像才接得上执行链。
# 防御等级只回答"这站好不好采"，不回答"能不能采"——能不能采一律以实测为准（M-R60）。

_SITE_FEATURES = {
    # 加密特征（≥3 → 等级 2）
    'encrypt_aes': (r'AES|aes\.(?:encrypt|decrypt)', 'encrypted'),
    'encrypt_base64': (r'atob\(|btoa\(|Base64\.decode', 'encrypted'),
    'encrypt_player_aaaa': (r'player_aaaa', 'encrypted'),
    'encrypt_packer': (r'eval\(function\(', 'encrypted'),
    'encrypt_obfuscated': (r'\[_0x[a-f0-9]+\]', 'encrypted'),
    'encrypt_xor': (r'xor|XOR|charCodeAt[^;\n]{0,40}\^', 'encrypted'),
    'encrypt_sign': (r'\bsign=|_sign=|\bsignature=|X-Sign', 'encrypted'),
    'encrypt_cryptojs': (r'CryptoJS|crypto\.createCipher', 'encrypted'),
    'encrypt_gcm': (r'AES-GCM|aes-256-gcm|encryptGcm', 'encrypted'),
    'encrypt_compressed': (r'pako|inflateRaw|DecompressionStream|play_data', 'encrypted'),
    # 反爬特征（≥2 → 等级 1）
    'anticrawl_cf': (r'cf-browser-verification|challenge-platform|__cf_bm|cf-ray', 'domain_release'),
    'anticrawl_captcha': (r'captcha|验证码|g-recaptcha|h-captcha|turnstile', 'domain_release'),
    'anticrawl_waf': (r'403 Forbidden|access denied|request blocked', 'domain_release'),
    'anticrawl_referer': (r'防盗链|hotlink|Referer', 'hls_localproxy'),
    'anticrawl_ad_ts': (r'/ad\d*\.ts|/advert/', 'hls_localproxy'),
    'anticrawl_validator': (r'ge_js_validator|js_challenge|__jsl_clearance', 'domain_release'),
    # 混合内容（≥3 → 等级 3）
    'mixed_novel': (r'小说网|章节目录|novel/|book/', 'nonvideo'),
    'mixed_image': (r'图集|写真|套图|gallery', 'nonvideo'),
    'mixed_comic': (r'漫画|manga|comic/', 'nonvideo'),
    'mixed_live': (r'直播|live/|stream', 'nonvideo'),
    'mixed_music': (r'音乐|audio/|\.mp3', 'nonvideo'),
    # 高级（≥3 → 等级 4）
    'advanced_wasm': (r'wasm|WebAssembly', 'encrypted'),
    'advanced_iframe_nest': (r'<iframe', 'hls_localproxy'),
    'advanced_api_collect': (r'/api\.php/provide/vod', 'api_spa'),
    'advanced_dynamic': (r'XMLHttpRequest|fetch\(|axios|ajax', 'api_spa'),
    # 现代站点（命中任一 → 强制等级 4）
    'modern_htmx': (r'htmx(?:\.min)?\.js|HX-Request|hx-get|hx-target|hx-swap', 'api_spa'),
    'modern_cloudflare': (r'__cf_bm|cf-ray|challenge-platform|cdn-cgi|cloudflare', 'domain_release'),
    'modern_async_shell': (r'id=["\']app["\']|id=["\']root["\']|__NEXT_DATA__|__NUXT_DATA__|'
                           r'window\.__INITIAL|data-server-rendered|<noscript>', 'api_spa'),
    # 路由/结构线索（不影响等级，只影响下一步推荐）
    'route_nuxt': (r'__NUXT_DATA__|_payload\.js|window\.__NUXT__', 'api_spa'),
    'route_next': (r'__NEXT_DATA__|self\.__next_f|/_next/', 'api_spa'),
    'route_vite': (r'VITE_BASE_URL|VITE_APP_API_BASE_URL|/assets/index-[\w-]+\.js', 'api_spa'),
    'route_maccms_v10': (r'/index\.php/vod/|player_data', 'maccms'),
    'route_maccms_v8': (r'/search\.php\?searchtype=5|/frim/index', 'maccms'),
    'route_folder': (r'vod_tag|actor-detail|/actresses?/|/tags?/', 'folder_routing'),
    'route_release_page': (r'zz_line|zz_backup_line|words\.random|永久地址|最新地址|发布页',
                           'domain_release'),
    'route_pan_share': (r'pan\.quark\.cn|aliyundrive\.com|pan\.baidu\.com|'
                        r'cloud\.189\.cn|115\.com|123pan\.com|magnet:\?xt=', 'pan'),
    'route_hls_key': (r'#EXT-X-KEY|EXT-X-STREAM-INF|\.m3u8', 'hls_localproxy'),
    'route_novel_protocol': (r'novel://|vod_player', 'nonvideo'),
    'route_pics_protocol': (r'pics://|type=manga', 'nonvideo'),
}
_MODERN_KEYS = ('modern_htmx', 'modern_cloudflare', 'modern_async_shell')
_ENC_KEYS = tuple(k for k in _SITE_FEATURES if k.startswith('encrypt_'))
_HLS_KEYS = ('route_hls_key', 'anticrawl_referer', 'anticrawl_ad_ts')
_LEVEL_DESC = {
    0: '无防御（可直采）',
    1: '有反爬（验证码/CF/WAF）',
    2: '有加密（AES/base64/签名）',
    3: '混合内容（非视频占比高）',
    4: '现代 SPA / 强防御（硬采成本高）',
}


def _detect_cms(html):
    """站点结构判定（移植自道长 detect_cms，返回标签串）。"""
    if '/api.php/provide/vod' in html:
        return 'api_collect'
    if re.search(r'/index\.php/vod/|player_data|player_aaaa', html):
        return 'maccms_v10'
    if re.search(r'/search\.php\?searchtype=5|/frim/index', html):
        return 'maccms_v8'
    if '__NUXT_DATA__' in html:
        return 'nuxt_ssr'
    if '__NEXT_DATA__' in html or 'self.__next_f' in html:
        return 'nextjs_rsc'
    if re.search(r'wp-content|/wp-json/', html):
        return 'wordpress'
    if re.search(r'id=["\']app["\']|/assets/index-[\w-]+\.js', html):
        return 'spa'
    if '苹果CMS' in html or 'MacCMS' in html:
        return 'maccms_unknown_version'
    return 'nonstandard'


def _site_judge(feats):
    """防御等级 0-4（移植自道长 judge；等级名改用平实描述，不照搬修真境名）。"""
    enc = sum(1 for k in _ENC_KEYS if feats.get(k))
    anti = sum(1 for k, v in feats.items() if k.startswith('anticrawl_') and v)
    mixed = sum(1 for k, v in feats.items() if k.startswith('mixed_') and v)
    adv = sum(1 for k, v in feats.items() if k.startswith('advanced_') and v)
    modern = [k for k in _MODERN_KEYS if feats.get(k)]
    if adv >= 3:
        level = 4
    elif mixed >= 3:
        level = 3
    elif enc >= 3:
        level = 2
    elif anti >= 2:
        level = 1
    else:
        level = 0
    if modern:
        level = 4
    return level, {'enc': enc, 'anti': anti, 'mixed': mixed, 'adv': adv, 'modern': modern}


def _next_steps(cms, feats, level):
    """画像的落点：推荐**探源司自己的命令**（道长 modules_for 的改造版）。"""
    steps = []
    if cms.startswith('maccms') or cms == 'api_collect':
        steps.append(('forge classify <api.php/provide/vod 地址>',
                      '实测判源类型（CMS采集/m3u8直链/JSON配置）——按 url 路径猜 5 条错 4 条'))
        steps.append(('livecheck --kind class <api>',
                      '分类完整性检测：父子分类树，防「点分类是空的」（S101 两坑）'))
    if any(feats.get(k) for k in _HLS_KEYS):
        steps.append(('livecheck --kind m3u8 <m3u8地址>',
                      '真拉清单 + 真拉分片才算活（HTTP 200 不算）'))
        steps.append(('quality <m3u8地址>',
                      '画质/速度/广告三件套；BANDWIDTH 全等不可信，画质以 RESOLUTION 实测为准'))
    if any(feats.get(k) for k in _ENC_KEYS):
        steps.append(('probe decrypt --url <url>',
                      '页面/响应带加密特征，先破甲再看内容'))
    if feats.get('route_pan_share'):
        steps.append(('harvest pan info --url <分享链接>',
                      '命中网盘分享特征，走 pan 取料（只走 API，不碰 CDN）'))
    if level >= 3:
        steps.append(('⚠️ 优先换源，别硬刚',
                      f'防御等级 {level}（{_LEVEL_DESC[level]}）——同类替代源通常更好采'))
    if not steps:
        steps.append(('harvest scan --repo <仓库URL>',
                      '未发现防御/结构线索，可直接进采集链路找线路'))
    return steps


def _cmd_profile(a):
    """站点画像：抓 HTML → 43 项特征扫描 → 防御等级 → 推荐探源司命令。

    三态：抓不到 = 无法判定（退出码 1），**不是"这站没特征"**（M-R60）。
    画像只回答"好不好采"；"能不能采"一律以 livecheck 实测为准。
    """
    url = getattr(a, 'url', None)
    if not url:
        print('缺参数：tvbox probe forge profile <url>')
        return 2
    body, err = _cls_fetch(url)
    if err:
        print(f'⚠️ 抓不到 {url}（{err}）——无法判定（不是"无特征"），换时间复测')
        return 1
    html = body or ''
    feats = {k: bool(re.search(pat, html, re.I)) for k, (pat, _m) in _SITE_FEATURES.items()}
    hits = [k for k, v in feats.items() if v]
    cms = _detect_cms(html)
    level, cnt = _site_judge(feats)
    print(f'🌐 站点画像：{url}')
    print(f'   CMS 结构判定：{cms}')
    print(f'   防御等级：{level}/4（{_LEVEL_DESC[level]}）'
          f'——加密{cnt["enc"]}·反爬{cnt["anti"]}·混合{cnt["mixed"]}·高级{cnt["adv"]}'
          + (f'·现代SPA{cnt["modern"]}' if cnt['modern'] else ''))
    if hits:
        print(f'   命中特征（{len(hits)}/{len(_SITE_FEATURES)}）：')
        for k in hits:
            print(f'      · {k}（{_SITE_FEATURES[k][1]}）')
    else:
        print('   命中特征：无')
    print('   下一步（按判据推荐，非执行）：')
    for i, (cmd, why) in enumerate(_next_steps(cms, feats, level), 1):
        print(f'      {i}. {cmd}  —— {why}')
    return 0


if __name__ == '__main__':
    # P018③ 修复：补 forge 的等价入口（不接 probe.py 总调度，避免改 484KB 入口破坏其他命令）。
    # 用法：python3 scripts/forge.py <verify|list|classify|audit|profile> ...
    import argparse
    _p = argparse.ArgumentParser(prog='forge.py', description='写源模块（P018）')
    _sp = _p.add_subparsers(dest='cmd')
    _pv = _sp.add_parser('verify', help='验源；--save 通过则留档到 sources/')
    _pv.add_argument('file', help='源 .py 路径')
    _pv.add_argument('--save', action='store_true', help='验证通过则留档')
    _sp.add_parser('list', help='列出已留档的源与最近验证结果')
    _pc = _sp.add_parser('classify', help='实测判定源类型')
    _pc.add_argument('api', nargs='?', help='单条 api 地址')
    _pc.add_argument('--in', dest='infile', help='批量清单 json')
    _pc.add_argument('--out', dest='out', help='结论输出 json')
    _sp.add_parser('audit', help='源库审计')
    _pp = _sp.add_parser('profile', help='站点画像')
    _pp.add_argument('url', help='站点 url')
    _pl = _sp.add_parser('packlocal', help='本地 py/js 目录扫描→验源→出 auto_config.json（S204）')
    _pl.add_argument('--dir', dest='dir', help='待扫描目录（递归）')
    _pl.add_argument('--out', dest='out', help='输出配置路径（缺省 <目录>/auto_config.json）')
    _pl.add_argument('--keep-unverified', dest='keep_unverified', action='store_true',
                     help='「无法判定」也进清单并打标（默认只收「通过」）')
    _pl.add_argument('--file-base', dest='file_base', default='',
                     help='api 前缀（默认 ./ 相对路径；壳子 5.1.6 不认 file:// 绝对路径，'
                          '仅在明确需要时传 file://storage/emulated/0/）')
    _pl.add_argument('--no-flat', dest='flat', action='store_false', default=True,
                     help='关闭 flat 直接全挂形态（默认开）：每个源直接成一个 site 写进 sites，'
                          '不包加载器，对应 ok.json/demo.json 的扁平写法')
    _args = _p.parse_args()
    if not _args.cmd:
        _p.print_help()
        sys.exit(2)
    sys.exit(cmd_forge(_args))
