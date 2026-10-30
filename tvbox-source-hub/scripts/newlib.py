#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
newlib.py —— 线路总库·四档（S103）

用户 2026-10-16 需求：
  ① "把你的库重做，线路按全量版/纯净版/18版的分类做三个不同的文档，按相应类型入库，
     你产出的json单独做一个和这三个并列的文档"
  ② "放你产出的json这个要和监测技能联动，因为作者原仓库跑路了或者废弃了我这个json也用不了"

四档同级并列：
  📚 线路总库_四档
   ├ 📦 全量版/  线路表 + 说明
   ├ 📦 纯净版/  线路表 + 说明
   ├ 📦 18版/    线路表 + 说明
   └ 📦 产出json/ 归档表（含溯源）+ 溯源说明

命令：
  newlib build  [--dry]                    建四档结构（幂等：id 落 newlib_ids.json）
  newlib migrate [--dry] [--dirs ...]      站点包提 categories 判档 → 灌三档线路表
  newlib archive --file F.json --repo URL  归档成品json + 自动登记监测目标
  newlib relink  [--dry]                   监测结果反查 → 回写失效标记

铁律：
  · 纯净版只收"纯净"（未知/全量都不进，防漏网18）
  · 未知不折叠（未知≠干净）
  · 来源仓库缺失打"待核"，绝不静默留空
  · 写后 invalidate，防脏缓存谎报
"""
import os, sys, json, glob, time, argparse, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = '/root/.codebuddy/skills/library'
PROBE_DIR = '/root/.codebuddy/skills/probe-hub/scripts'
SPACE = '8w5cnqCqQJ0wNoF2Hb6efH'          # TVBox 生态空间
PARENT = 'GCLJWbFGS1BmYbj6JCX9it'        # 底包库根（新库挂在它下面）
DB_SRC = '[源库联合]'                     # S159：原写死旧库（S144 已清空）→ 索引恒空
DB_UPSTREAM = 'aCSvley568A0aW3sggzJwA'   # 上游仓库监测册
IDS_FILE = os.path.join(HERE, 'newlib_ids.json')

sys.path.insert(0, HERE)
try:
    from core import (rows_of, invalidate, file_to_janitor,
                      rows_of_t, SRC_DBS)          # S159：源库联合所需
except Exception:
    # ⚠️ 降级实现**故意让 rows_of_t 返回 ok=False**，而不是 (True, [])。
    #    原实现 `def rows_of(db): return []` 会把「core 导不进来」伪装成「库是空的」，
    #    调用方拿到空表继续跑，产出「0 条待迁移」的假结论（M-R60 读失败≠空态）。
    def rows_of(db): return []
    def rows_of_t(db): return (False, [])
    SRC_DBS = [('线路源库', ''), ('成品源库', '')]   # S160：与 core.SRC_DBS 保持同名同序
    def invalidate(db=None): pass
    def file_to_janitor(node_id, reason='', quiet=True): return False

# 复用源枢库曹的内容分级判据（M-R38 单一事实源）
try:
    from link import classify_adult, ADULT_WORDS
except Exception:
    ADULT_WORDS = ['伦理', '三级', '激情', '成人', '18禁', '里番', '无码', 'H漫', '情色', '禁片']
    def classify_adult(cats):
        if not cats: return '未知'
        cats = [str(c).strip() for c in cats if str(c).strip()]
        if not cats: return '未知'
        hit = [c for c in cats if any(w in c for w in ADULT_WORDS)]
        if not hit: return '纯净'
        return '18' if len(hit) == len(cats) else '全量'


def _run(script, *args):
    """调 library 脚本，返回 stdout。"""
    r = subprocess.run([sys.executable, f'{LIB}/{script}', *args],
                       capture_output=True, text=True)
    return r.stdout.strip()


# ---------- id 管理（幂等：绝不重复建库）----------
def load_ids():
    """读四档结构 id 清单。三态（M-R60）：文件不存在=首期(空)；损坏=显式告警，不折叠成空。"""
    if not os.path.exists(IDS_FILE):
        return {}
    try:
        return json.load(open(IDS_FILE, encoding='utf-8'))
    except Exception as e:
        print(f'  ⚠️ 四档 id 清单损坏（{os.path.basename(IDS_FILE)}：{type(e).__name__}）'
              f'——禁止当"未建库"处理（M-R60：损坏≠首期），请人工修复后再跑')
        raise SystemExit(2)


def save_ids(d):
    json.dump(d, open(IDS_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


# ---------- 建文件夹 / 表 / 文档 ----------
def mk_folder(title, parent=PARENT, space=SPACE):
    out = _run('manage/create_folder.py', '--title', title, '--space-id', space, '--parent-id', parent)
    for ln in out.splitlines():
        if ln.startswith('KS_FOLDER_CREATE'):
            cols = ln.split('\t')
            if len(cols) >= 2:
                return cols[1]
    return None


def mk_db(title, properties, parent, space=SPACE):
    schema = {'title': title, 'properties': properties, 'space_id': space, 'parent_id': parent}
    out = _run('database/create_database.py', '--schema', json.dumps(schema, ensure_ascii=False))
    # 输出含 JSON 行 + 可能的 KS_USER_REPLY 行 → 只解析首个 { 开头行
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith('{'):
            try:
                d = json.loads(ln)
                return d.get('database_id')
            except Exception:
                continue
    return None


def mk_doc(title, markdown, parent, space=SPACE):
    out = _run('space_api.py', 'space.importer.create-doc', '--title', title,
               '--markdown', markdown, '--space-id', space, '--parent-id', parent)
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith('{'):
            try:
                d = json.loads(ln)
                dd = d.get('data') or {}
                node = dd.get('node') or {}
                return (node.get('id') or dd.get('nodeBlockId')
                        or d.get('id') or d.get('nodeBlockId'))
            except Exception:
                continue
    return None


# ---------- 表 schema ----------
def line_props():
    """三档线路表（同构）。"""
    return [
        {'name': '名称', 'config': {'text': {}}},
        {'name': 'api接口', 'config': {'text': {}}},
        {'name': 'host', 'config': {'text': {}}},
        {'name': '内容分级', 'config': {'select': {'options': [
            {'text': '纯净'}, {'text': '全量'}, {'text': '18'}, {'text': '未知'}]}}},
        {'name': '内容分类', 'config': {'text': {}}},
        {'name': '体系', 'config': {'select': {'options': [
            {'text': 'guard系'}, {'text': 'lytvs系'}, {'text': '直链系'},
            {'text': '其他系'}, {'text': '全系'}]}}},
        {'name': '可用体系', 'config': {'multi_select': {'options': [
            {'text': 'A·CatVod标准系'}, {'text': 'B·moji Guard系'},
            {'text': 'C·零jar纯直链系'}, {'text': 'D·Amns系'}, {'text': '🔞成人直链系'}]}}},
        {'name': '源库record_id', 'config': {'text': {}}},
        {'name': '入库时间', 'config': {'date': {}}},
    ]


def archive_props():
    """产出json·归档表（含溯源）。"""
    return [
        {'name': '文件名', 'config': {'text': {}}},
        {'name': '路径', 'config': {'text': {}}},
        {'name': '档位', 'config': {'select': {'options': [
            {'text': '全量版'}, {'text': '纯净版'}, {'text': '18版'},
            {'text': '交互版'}, {'text': '其他'}]}}},
        {'name': '来源仓库', 'config': {'text': {}}},
        {'name': '来源作者', 'config': {'text': {}}},
        {'name': '来源监测id', 'config': {'text': {}}},
        {'name': '站点数', 'config': {'number': {}}},
        {'name': '版本', 'config': {'text': {}}},
        {'name': '生成时间', 'config': {'date': {}}},
        {'name': '存活状态', 'config': {'select': {'options': [
            {'text': '存活'}, {'text': '已封禁或删除'}, {'text': '无法判定'}, {'text': '待查'}]}}},
        {'name': '失效标记', 'config': {'select': {'options': [
            {'text': '有效'}, {'text': '失效'}, {'text': '待核'}]}}},
    ]


# ---------- build：建四档结构 ----------
def _kids(pid):
    """列某节点下的子节点 (id, title, kind)。"""
    out = _run('space_api.py', 'space.workspace.node-info', '--node-id', pid)
    kids = []
    try:
        n = json.loads(out)['data']['node']
        for k in (n.get('nodes') or []):
            oo = _run('space_api.py', 'space.workspace.node-info', '--node-id', k)
            try:
                nn = json.loads(oo)['data']['node']
                kids.append((k, nn.get('title', ''), nn.get('kind', '')))
            except Exception:
                pass
    except Exception:
        pass
    return kids


def _find_kid(pid, want_kind, title_kw):
    """在 pid 下找 kind 与标题关键词都匹配的子节点 id。"""
    for k, t, kind in _kids(pid):
        if kind == want_kind and title_kw in str(t):
            return k
    return None


def cmd_build(a):
    ids = load_ids()
    if ids.get('root') and not a.force:
        print(f'ℹ️ 已存在四档结构（newlib_ids.json）→ 复用，不重复建库')
        print(f'   根: {ids.get("root")}')
        for k in ('全量版', '纯净版', '18版', '产出json'):
            print(f'   {k:8} 表={ids.get("tables", {}).get(k, "-")}')
        # ★ 复用时也跑清道夫钩子（每轮建库都自动清理历史重复节点）
        _file_dups(ids.get('root'))
        return 0

    plan = [
        ('全量版', '全量版·线路表', '全量版·说明'),
        ('纯净版', '纯净版·线路表', '纯净版·说明'),
        ('18版', '18版·线路表', '18版·说明'),
        ('产出json', '产出json·归档表', '产出json·溯源说明'),
    ]
    if a.dry:
        print('═══ [--dry] 将建四档结构 ═══')
        print(f'  根文件夹：📚 线路总库_四档（space={SPACE} parent={PARENT}）')
        for fold, tbl, doc in plan:
            print(f'   ├ 📦 {fold}/  🗂 {tbl}  📄 {doc}')
        print('  （演示模式未创建，去掉 --dry 执行）')
        return 0

    print('═══ 建四档结构 ═══')
    root = mk_folder('📚 线路总库_四档', PARENT)
    if not root:
        print('❌ 根文件夹创建失败'); return 1
    print(f'  根 {root}')
    ids = {'root': root, 'space': SPACE, 'tables': {}, 'folders': {}, 'docs': {}}
    for fold, tbl, doc in plan:
        # 幂等：已存在同名文件夹/表/文档 → 复用其 id，不重复建
        fid = _find_kid(root, 'folder', fold) or mk_folder(f'📦 {fold}', root)
        print(f'  📦 {fold} → {fid}')
        ids['folders'][fold] = fid
        did = _find_kid(fid, 'database', tbl) \
            or mk_db(f'🗂 {tbl}', (archive_props() if fold == '产出json' else line_props()), fid)
        print(f'     🗂 {tbl} → {did}')
        ids['tables'][fold] = did
        docid = _find_kid(fid, 'doc', doc) \
            or mk_doc(f'📄 {doc}', _doc_md(fold), fid)
        print(f'     📄 {doc} → {docid}')
        ids['docs'][fold] = docid
    save_ids(ids)
    print(f'\n✅ 四档结构已建，id 落盘 {IDS_FILE}')
    # ★ 生态联动：建库后自动检测"多余/重复节点"并投递清道夫收件箱（AI 删不掉的归一处）
    _file_dups(root)
    return 0


def _file_dups(root):
    """S104 生态联动钩子：扫四档根下重复的同名说明文档，多出来的自动投递清道夫。
    只投递**明确重复**（同名文档 >1 个）——保守，绝不误投正常节点。"""
    try:
        for fid in load_ids().get('folders', {}).values():
            byname = {}
            for k, t, kind in _kids(fid):
                if kind == 'doc':
                    byname.setdefault(t or '', []).append(k)
            for title, nids in byname.items():
                for extra in nids[1:]:   # 保留第一个，其余为重复
                    file_to_janitor(extra, f'重复文档（同名「{title}」已保留一份）')
    except Exception as e:
        print(f'  ⚠️ 清道夫联动跳过（{type(e).__name__}）——不影响建库')


def _doc_md(fold):
    base = {
        '全量版': '本表收录**分类全留**的线路（含伦理片等18分类）。做「全量版」json 时用本表。',
        '纯净版': '本表**只收录内容分级=纯净**的线路（无任何18分类）。做「纯净版」json 时用本表。'
                 '铁律：未知/全量一律不入本表，防漏网18源。',
        '18版': '本表收录**全部内容为18分类**的线路（纯18站）。做「18版」json 时用本表。',
        '产出json': '本表记录探源司产出的成品 json（含**来源仓库**溯源）。'
                   '与上游仓库监测联动：作者原仓库跑路/废弃 → 本 json 失效 → 自动置「失效标记=失效」。',
    }
    return f"""# {fold}·说明

{base.get(fold, '')}

## 字段
- 三档线路表：名称 / api接口 / host / 内容分级 / 内容分类 / 体系 / 可用体系 / 源库record_id / 入库时间
- 产出json·归档表：文件名 / 路径 / 档位 / 来源仓库 / 来源作者 / 来源监测id / 站点数 / 版本 / 生成时间 / 存活状态 / 失效标记

## 口径
- 内容分级判据：站点 `categories` 命中伦理词表（{'/'.join(ADULT_WORDS)}）
- 纯净=无命中；全量=有命中且有正常分类；18=有命中且全是18分类；未知=无 categories
"""


# ---------- migrate：站点包提 categories → 灌三档表 ----------
def cmd_migrate(a):
    ids = load_ids()
    if not ids.get('tables'):
        print('❌ 四档结构未建，先跑 newlib build'); return 1
    dirs = a.dirs or ['/workspace']
    # 收集站点 → 按 api 聚合（同 api 多站名取最保守）
    api_sites = {}
    files = 0
    for dd in dirs:
        for fp in glob.glob(os.path.join(dd, '**', '*.json'), recursive=True):
            try:
                d = json.load(open(fp, encoding='utf-8-sig'))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            sites = d.get('sites')
            if not isinstance(sites, list):
                continue
            files += 1
            for s in sites:
                if not isinstance(s, dict):
                    continue
                api = str(s.get('api') or '').strip()
                if not api:
                    continue
                api_sites.setdefault(api, []).append(s)
    print(f'═══ 迁移扫描：{files} 个含sites的json，{len(api_sites)} 个唯一 api ═══')

    # 源库索引（补体系/可用体系/record_id）
    # S159：三库联合建索引。原只读旧库（空）→ src_idx 恒空 → 每条候选都「源库无记录」，
    #   于是「补体系/可用体系/record_id」全部落空，迁移结果丢字段却看不出来。
    src_idx = {}
    for _nm, _db in SRC_DBS:
        _ok, _rows = rows_of_t(_db)
        if not _ok:
            print(f'   ⚠️ 源库 {_nm} 读取失败——索引不完整，勿据此判定"源库无此 api"')
            continue
        for r in _rows:
            src_idx[str(r.get('api接口', ''))[:400]] = r

    buckets = {'纯净': [], '全量': [], '18': [], '未知': []}
    for api, sites in api_sites.items():
        lv = {'未知': 0, '纯净': 1, '全量': 2, '18': 3}
        worst = '纯净'
        allcats = []
        name = sites[0].get('name') or api
        for s in sites:
            cats = s.get('categories')
            allcats.extend(cats or [])
            v = classify_adult(cats)
            if lv[v] > lv[worst]:
                worst = v
        if not allcats:
            # 所有站都无 categories → 未知
            worst = '未知'
        # 源库"可用体系"是逗号连接的整体串 → 必须拆成 multi_select 数组元素，
        # 否则 API 报 invalid fields（S103 实测坑）
        _src = src_idx.get(api[:400], {}) or {}
        _us = _src.get('可用体系')
        if isinstance(_us, list):
            _sysk = [str(x).strip() for x in _us if str(x).strip()]
        else:
            _sysk = [p.strip() for p in str(_us or '').replace('，', ',').split(',') if p.strip()]
        if not _sysk:
            _sysk = ['A·CatVod标准系']
        # 源库"体系"同理可能是逗号串 → select 只认单值，取第一个
        _sysv = str(_src.get('体系') or '其他系')
        _sysv = _sysv.replace('，', ',').split(',')[0].strip() or '其他系'
        buckets[worst].append({
            '名称': {'text': str(name)[:80]},
            'api接口': {'text': api[:400]},
            'host': {'text': api[:200]},
            '内容分级': {'select': worst},
            '内容分类': {'text': json.dumps(allcats, ensure_ascii=False)[:2000] if allcats else ''},
            '体系': {'select': _sysv},
            '可用体系': {'multi_select': _sysk},
            '源库record_id': {'text': ''},
            '入库时间': {'date': time.strftime('%Y-%m-%dT%H:%M:00Z', time.gmtime())},
        })
    print(f'  判档：纯净 {len(buckets["纯净"])} ｜ 全量 {len(buckets["全量"])} '
          f'｜ 18 {len(buckets["18"])} ｜ 未知 {len(buckets["未知"])}（未知不入表）')

    if a.dry:
        print('  [--dry] 未写入。去掉 --dry 执行灌表。')
        return 0

    for level in ('纯净', '全量', '18'):
        recs = buckets[level]
        if not recs:
            print(f'  {level}版：0 条，跳过')
            continue
        tbl = ids['tables'].get({'纯净': '纯净版', '全量': '全量版', '18': '18版'}[level])
        ok = fail = 0
        for i in range(0, len(recs), 100):
            chunk = recs[i:i + 100]
            r = subprocess.run(
                [sys.executable, f'{LIB}/database/batch_add_database_records.py',
                 '--database-id', tbl, '--records', json.dumps(chunk, ensure_ascii=False)],
                capture_output=True, text=True)
            out = (r.stdout or '').strip()
            if r.returncode != 0 or not out:
                fail += len(chunk)
                if i == 0:
                    print(f'  ⚠ {level}版批{i} RC={r.returncode} '
                          f'ERR={(r.stderr or "")[:300]} OUT={out[:200]}')
                continue
            try:
                d = json.loads(out)
            except Exception:
                # 末尾可能夹 KS_USER_REPLY 等行 → 逐行找 JSON
                d = None
                for ln in out.splitlines():
                    ln = ln.strip()
                    if ln.startswith('{'):
                        try:
                            d = json.loads(ln); break
                        except Exception:
                            continue
            if not isinstance(d, dict):
                fail += len(chunk)
                if i == 0:
                    print(f'  ⚠ {level}版批{i} 解析失败 OUT={out[:200]}')
                continue
            res = d.get('results') or []
            good = sum(1 for x in res if x.get('success'))
            if i == 0 and good == 0:
                print(f'  ⚠ {level}版批0 原始返回：{out[:600]}')
            ok += good
            fail += len(chunk) - good
        print(f'  {level}版 → 表 {tbl}：成功 {ok} / 失败 {fail}')
    return 0


# ---------- archive：归档成品json + 登记监测 ----------
def cmd_archive(a):
    ids = load_ids()
    if not ids.get('tables', {}).get('产出json'):
        print('❌ 四档结构未建，先跑 newlib build'); return 1
    if not os.path.exists(a.file):
        print(f'❌ 文件不存在：{a.file}'); return 1
    try:
        d = json.load(open(a.file, encoding='utf-8-sig'))
    except Exception as e:
        print(f'❌ 解析失败：{e}'); return 1
    sites = d.get('sites') if isinstance(d, dict) else []
    nsite = len(sites or [])
    fn = os.path.basename(a.file)
    # 来源仓库：命令行给 or sidecar
    repo = a.repo or ''
    if not repo:
        side = a.file.rsplit('.', 1)[0] + '.meta.json'
        if os.path.exists(side):
            try:
                repo = (json.load(open(side, encoding='utf-8')) or {}).get('来源仓库', '')
            except Exception as e:
                # sidecar 是可选溯源增强；损坏时显式告警但不阻断（后续失效标记=待核兜底）
                print(f'  ⚠️ 溯源 sidecar 解析失败（{os.path.basename(side)}：{type(e).__name__}）'
                      f'——本次来源仓库留空，失效标记=待核')
    author = ''
    tid = ''
    is_git = False
    if repo:
        sys.path.insert(0, PROBE_DIR)
        try:
            import probe
            owner, repon, platform, host = probe._parse_repo(repo)
            # 仅当 host 是已知 git 托管平台时才算可监测
            if owner and platform in ('GitHub', 'Gitee', 'GitLab', 'GitCode', 'Bitbucket', 'Codeberg'):
                author = owner; tid = repon.lower(); is_git = True
        except Exception:
            pass
    # 档位：命令行 > 按站点实际分级判档（最保守聚合）> 文件名猜测
    level = a.level or _level_from_sites(sites) or _guess_level(fn)
    rec = {
        '文件名': {'text': fn[:120]},
        '路径': {'text': os.path.abspath(a.file)[:300]},
        '档位': {'select': level},
        '来源仓库': {'text': repo[:300] if repo else '待核'},
        '来源作者': {'text': author[:80]},
        '来源监测id': {'text': tid},
        '站点数': {'number': nsite},
        '版本': {'text': a.version or ''},
        '生成时间': {'date': time.strftime('%Y-%m-%dT%H:%M:00Z', time.gmtime())},
        '存活状态': {'select': '待查'},
        '失效标记': {'select': '有效' if is_git else '待核'},
    }
    if a.dry:
        print('═══ [--dry] 归档预览 ═══')
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0
    tbl = ids['tables']['产出json']
    out = _run('database/batch_add_database_records.py',
               '--database-id', tbl, '--records', json.dumps([rec], ensure_ascii=False))
    print(f'  归档写入：{out[:200]}')
    # 登记监测目标（仅 git 仓库可监测）
    if is_git:
        reg = _register_monitor(repo, author)
        print(f'  监测登记：{reg}')
    elif repo:
        print(f'  ⚠️ 来源非 git 仓库（{repo[:50]}）→ 失效标记=待核，不入监测册')
    else:
        print('  ⚠️ 无来源仓库 → 失效标记=待核（补 --repo 后可登记监测）')
    invalidate(tbl)
    return 0


def _guess_level(fn):
    if '18' in fn or '🔞' in fn: return '18版'
    if '纯净' in fn: return '纯净版'
    if '全量' in fn: return '全量版'
    return '其他'


def _level_from_sites(sites):
    """按包内站点实际 categories 判档（最保守聚合），映射到归档表档位。
    代表性门槛：带 categories 的站须 ≥ 总站 50%，否则返 ''（交回文件名猜测），
    防止"161 站只有 6 站带分类"时以偏概全。"""
    if not isinstance(sites, list) or not sites:
        return ''
    lv = {'未知': 0, '纯净': 1, '全量': 2, '18': 3}
    worst = '纯净'
    n_cat = 0
    for s in sites:
        if not isinstance(s, dict):
            continue
        cats = s.get('categories')
        if cats:
            n_cat += 1
        v = classify_adult(cats)
        if lv[v] > lv[worst]:
            worst = v
    if n_cat == 0 or n_cat * 2 < len(sites):
        return ''
    return {'纯净': '纯净版', '全量': '全量版', '18': '18版', '未知': '其他'}[worst]


def _register_monitor(url, author):
    """登记监测目标到 DB_UPSTREAM（复用探源司 save_targets，双写云端+本地）。"""
    sys.path.insert(0, PROBE_DIR)
    try:
        import probe
        existing = {t.get('仓库URL') for t in probe.load_targets()[1]}
        if url in existing:
            return f'ℹ️ 已登记过：{url}'
        owner, repo, platform, host = probe._parse_repo(url)
        if not owner:
            return f'❌ 无法解析 owner/repo：{url}'
        branch = probe._detect_default_branch(owner, repo, platform, host)
        rec = {
            'id': repo.lower(), '仓库名': repo, '仓库URL': url, '作者': author or owner,
            '平台': platform, '分支': branch or 'main', '配置路径': '',
            '监测周期': '每周', '关联底包': '线路总库_四档',
            '存活状态': '待查', '当前commit': '', '内容指纹': '',
            '活跃度': '待查', '上次检查': '', '下次到期': '', '变更摘要': '',
        }
        probe.save_targets([rec])
        return f'✅ 已登记监测：{repo.lower()}（分支 {branch}）'
    except Exception as e:
        return f'❌ 登记失败：{e}'


# ---------- relink：监测结果反查 → 回写失效标记 ----------
def _paged_records(db):
    """分页读库，含 record_id（rows_of 不含 record_id，relink 必须用这个）。"""
    recs, cursor = [], None
    # ★ M-R115 禁二 / M-R100：加轮次上限，防「has_more 恒真 + 游标不动」的死循环。
    for _ in range(200):
        args = ['database/query_database_record.py', '--database-id', db, '--page-size', '200']
        if cursor:
            args += ['--start-cursor', cursor]
        out = _run(*args)
        d = None
        for ln in (out or '').splitlines():
            ln = ln.strip()
            if ln.startswith('{'):
                try:
                    d = json.loads(ln); break
                except Exception:
                    continue
        if not isinstance(d, dict):
            break
        recs += d.get('results') or []
        nxt = d.get('next_cursor')
        if d.get('has_more') and nxt and nxt != cursor:
            cursor = nxt
        else:
            break                      # 游标未前进 → 立即停
    return recs


def cmd_relink(a):
    ids = load_ids()
    tbl = ids.get('tables', {}).get('产出json')
    if not tbl:
        print('❌ 四档结构未建'); return 1
    sys.path.insert(0, PROBE_DIR)
    try:
        import probe
    except Exception as e:
        print(f'❌ 无法加载探源司：{e}'); return 1
    # 读监测册存活状态
    _, targets = probe.load_targets()
    alive = {t.get('id'): t.get('存活状态') for t in targets}
    print(f'═══ 监测反查：监测册 {len(targets)} 个目标 ═══')
    # 读归档表（含 record_id）
    recs = _paged_records(tbl)
    dead_ids = {k for k, v in alive.items() if v in ('已封禁/删除', '已封禁或删除', '封禁', '删除')}
    print(f'  监测册中已封禁/删除：{len(dead_ids)} 个 → {" ".join(sorted(dead_ids)) or "（无）"}')
    # 反向：按监测id找归档记录
    updates = []
    for r in recs:
        mid = str(r.get('来源监测id') or '')
        cur = str(r.get('失效标记') or '')
        if mid and mid in dead_ids:
            if cur != '失效':
                updates.append(mid)
        elif mid and alive.get(mid) == '存活' and cur != '有效':
            updates.append(mid)
    print(f'  需回写 {len(updates)} 条')
    if a.dry:
        for m in updates[:20]:
            print(f'     {m} → 失效/有效')
        print('  [--dry] 未写入')
        return 0
    # 实际回写：按 record_id 批量更新存活状态/失效标记
    if not updates:
        print('  ✓ 无需回写（归档表与监测册一致）')
        return 0
    pairs = {}
    for r in recs:
        mid = str(r.get('来源监测id') or '')
        rid = r.get('record_id') or r.get('id')
        if mid and rid:
            pairs.setdefault(mid, []).append(rid)
    n_ok = 0
    for mid in updates:
        st = alive.get(mid)
        if st in ('已封禁/删除', '已封禁或删除', '封禁', '删除'):
            props = {'存活状态': {'select': '已封禁或删除'}, '失效标记': {'select': '失效'}}
        else:
            props = {'存活状态': {'select': '存活'}, '失效标记': {'select': '有效'}}
        batch = [{'record_id': rid, 'properties': props} for rid in pairs.get(mid, [])]
        if not batch:
            continue
        out = _run('database/batch_update_database_records.py',
                   '--database-id', tbl, '--records', json.dumps(batch, ensure_ascii=False))
        d = None
        for ln in (out or '').splitlines():
            ln = ln.strip()
            if ln.startswith('{'):
                try:
                    d = json.loads(ln); break
                except Exception:
                    continue
        got = sum(1 for x in ((d or {}).get('results') or []) if x.get('success'))
        n_ok += got
        print(f'  {mid} → {props["失效标记"]["select"]}（{got} 条）')
    invalidate(tbl)
    print(f'  ✓ relink 完成：回写 {n_ok} 条')
    return 0


# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(prog='newlib.py')
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build'); b.add_argument('--dry', action='store_true')
    b.add_argument('--force', action='store_true')
    m = sub.add_parser('migrate'); m.add_argument('--dry', action='store_true')
    m.add_argument('--dirs', nargs='*', default=['/workspace'])
    ar = sub.add_parser('archive'); ar.add_argument('--file', required=True)
    ar.add_argument('--repo', default=''); ar.add_argument('--level', default='')
    ar.add_argument('--version', default=''); ar.add_argument('--dry', action='store_true')
    rl = sub.add_parser('relink'); rl.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    return {'build': cmd_build, 'migrate': cmd_migrate,
            'archive': cmd_archive, 'relink': cmd_relink}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
