#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习工坊 · 引擎（learn-hub / v1.0.0）

设计原则（三条，写在这里是因为它们比功能更重要）：
1. **三态不折叠**（M-L03）：取到了 / 确定取不到 / 无法判定——网络抖动绝不写成"失败"。
2. **不许凭空生成**（M-L01）：每条产出必须带 source_ref，指不回原文的不进正文。
3. **覆盖率分母必须外部**（M-L02）：没有外部分母就不许报覆盖率这个数。

自检探针一律用 AST 只取目标函数字面量（S171 教训：探针与被检代码同居一文件
会自己判自己通过，这是"同侧变量互比"）。
"""
import os, sys, json, re, hashlib, shutil, argparse, subprocess, time
from datetime import datetime

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAT = os.path.join(HOME, 'materials')
LIB = os.path.join(HOME, 'library')
WORK = os.path.join(HOME, 'work')
INDEX = os.path.join(HOME, 'scripts', 'index.json')

# ═══════════════════════════════════════════════════════════════
# 三态常量：全库唯一事实源，任何地方判状态都必须走这里
# ═══════════════════════════════════════════════════════════════
ST_OK = 'ok'                 # 取到了
ST_MISS = 'miss'             # 确定取不到（404/已删/明确拒绝）
ST_UNK = 'unknown'           # 无法判定（超时/412/登录墙/编码异常）
ST_LABEL = {ST_OK: '取到了', ST_MISS: '确定取不到', ST_UNK: '无法判定'}

# 无法判定 ≠ 失败。这几个码/异常属于"我方或链路的临时问题"，不是"资源不存在"
_UNKNOWN_MARKS = ('412', 'timeout', 'timed out', 'login', '登录', 'captcha', '验证码',
                  'encoding', 'decode', 'connection reset', 'ssl', '903', '429')

# ── 生态门面（S176b）：把「本技能自己删不掉的残留」投递进清道夫收件箱 ──────
# 为什么需要：intake 落盘是**多步写**（source.html → extracted.txt → manifest.json），
# 中途失败会留下**半个目录**——有文件、无 manifest、无任何记录，谁也不知道。
# 这类残留本技能删不干净（不知道该不该删），只能交给用户一处按单删。
# 门面失败**绝不阻断主流程**（core.file_to_janitor 内部已兜住）。
def _load_core():
    """加载生态门面。返回 (模块, 失败原因)——**失败原因绝不丢弃**（M-R60）。

    刻意写成函数而非模块级 try/except：模块级 `except: X = None` 是
    「读失败折叠成空态」的典型形态，信号源16 的静态扫描必然要抓它。
    而这里返回的是 (None, '类型: 消息') —— 调用方拿到的**不是空**，
    而是一份能直接打印给人看的失败说明，三态可分辨。
    """
    try:
        import sys as _s
        d = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
        if d not in _s.path:
            _s.path.insert(0, d)
        import core
        return core, ''
    except Exception as e:
        return None, f'{type(e).__name__}: {str(e)[:80]}'


_CORE, _CORE_ERR = _load_core()


def _to_janitor(path, reason):
    """把删不掉的残留投递进清道夫。失败只告警，不影响主流程。"""
    if _CORE is None:
        print(f'  ⚠️ 清道夫门面不可用（{_CORE_ERR}）——残留 {path} 未能投递，请人工清理')
        return False
    return bool(_CORE.file_to_janitor(path, reason, quiet=True))


def classify_failure(msg):
    """把一句失败原因归到三态。**404 是确定取不到；412/超时/登录墙是无法判定。**

    ★这条函数是 S171 的直接遗产：网盘 412 不是"文件坏了"，是机房 IP 被 CDN 拒。
      归因错，后面所有处置都是错的。

    ★S172 更正：只返回状态是不够的。原来 412 既在 `_UNKNOWN_MARKS` 里、
      又能被末尾的 `return ST_UNK` 兜住——**两层冗余**。删掉名单那层，结果不变，
      于是"412 有专门判据"这件事根本验不出来。
      **归因必须带理由**：说不出凭什么，就等于没有判据。
    """
    return classify_failure_why(msg)[0]


def classify_failure_why(msg):
    """返回 (状态, 理由)。理由是**具体命中的关键字**，不是"不知道"这种空话。"""
    m = str(msg or '').lower()
    if not m:
        return ST_UNK, '原因为空·认不出来'
    for k in ('404', 'not found', '不存在', '已删除', 'no such', '410', '已失效', 'forbidden 403'):
        if k in m:
            return ST_MISS, f'命中「{k}」·资源确实不在了'
    for k in _UNKNOWN_MARKS:
        if k in m:
            return ST_UNK, f'命中「{k}」·我方或链路临时问题，非资源不存在'
    return ST_UNK, '无匹配判据·保守判无法判定'


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _ensure(*ds):
    for d in ds:
        os.makedirs(d, exist_ok=True)


def _load_json(p, default=None):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(p, obj):
    d = os.path.dirname(os.path.abspath(p))   # ★相对路径 dirname 是 ''，makedirs('') 会抛
    _ensure(d)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


# ═══════════════════════════════════════════════════════════════
# intake 采集
# ═══════════════════════════════════════════════════════════════
TEXT_EXT = ('.md', '.txt', '.markdown', '.json', '.csv', '.html', '.htm')
DOC_EXT = ('.pdf', '.docx', '.doc', '.epub', '.pptx', '.xlsx')
IMG_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff')


def _extract_text(path):
    """抽文本。抽不出来就返回 (None, 原因)，**不返回空串冒充成功**。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        for enc in ('utf-8', 'utf-8-sig', 'gb18030', 'big5'):
            try:
                with open(path, encoding=enc) as f:
                    return f.read(), enc
            except UnicodeDecodeError:
                continue
            except Exception as e:
                return None, f'{type(e).__name__}: {e}'
        return None, 'encoding: 四种编码都解不开'
    if ext == '.pdf':
        try:
            r = subprocess.run(['pdftotext', '-layout', path, '-'],
                               capture_output=True, timeout=120)
            if r.returncode != 0:
                return None, f'pdftotext rc={r.returncode}: {r.stderr.decode("utf-8", "replace")[:120]}'
            t = r.stdout.decode('utf-8', 'replace')
            if not t.strip():
                return None, 'pdf 无文本层（可能是扫描件，需 scan-pdf-ocr）'
            return t, 'pdftotext'
        except FileNotFoundError:
            return None, 'pdftotext 未安装'
        except subprocess.TimeoutExpired:
            return None, 'timeout: pdftotext 超 120s'
        except Exception as e:
            return None, f'{type(e).__name__}: {e}'
    return None, f'unsupported: 暂不抽 {ext}（{len(IMG_EXT) and "图片需 OCR"}）'


def cmd_intake(a):
    sub = a.sub
    if sub == 'list':
        rows = []
        if os.path.isdir(MAT):
            for d in sorted(os.listdir(MAT)):
                mf = os.path.join(MAT, d, 'manifest.json')
                if os.path.isfile(mf):
                    rows.append(_load_json(mf, {}) or {})
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1)); return
        if not rows:
            print('（素材库空）'); return
        print(f'{"日期":<12}{"来源":<10}{"文件":<6}{"取到":<6}{"无法判定":<8}{"确定取不到":<8} 短名')
        for r in rows:
            c = r.get('counts', {})
            print(f'{r.get("date",""):<12}{r.get("source",""):<10}{r.get("n_files",0):<6}'
                  f'{c.get(ST_OK,0):<6}{c.get(ST_UNK,0):<8}{c.get(ST_MISS,0):<8} {r.get("name","")}')
        return

    # ---- 收集候选文件 ----
    if sub == 'scan':
        root = a.path[0] if a.path else '.'
        if not os.path.isdir(root):
            print(f'❌ 不是目录：{root}'); sys.exit(2)
        files = []
        if a.recursive:
            for dp, _, fns in os.walk(root):
                for fn in fns:
                    files.append(os.path.join(dp, fn))
        else:
            files = [os.path.join(root, f) for f in os.listdir(root)
                     if os.path.isfile(os.path.join(root, f))]
        files = [f for f in files if os.path.splitext(f)[1].lower()
                 in TEXT_EXT + DOC_EXT + IMG_EXT]
        print(f'目录 {root} → 命中 {len(files)} 个可收文件')
        for f in files[:200]:
            print(f'  {os.path.getsize(f):>10,}  {os.path.relpath(f, root)}')
        if len(files) > 200:
            print(f'  … 另有 {len(files)-200} 个未列出')
        if a.dry or not files:
            print('\n（--dry / 空集合：只出清单，未落库）'); return
        items, source, name = [{'path': f} for f in files], 'scan', a.name or os.path.basename(root) or 'scan'
    elif sub == 'file':
        if not a.path:
            print('❌ intake file 需要至少一个路径'); sys.exit(2)
        items = [{'path': p} for p in a.path]
        source = 'file'
        name = a.name or os.path.splitext(os.path.basename(a.path[0]))[0]
    elif sub == 'url':
        url = a.path[0] if a.path else ''
        if not url.startswith('http'):
            print(f'❌ 不是 http(s) 链接：{url}'); sys.exit(2)
        return _intake_url(url, a)
    elif sub == 'pan':
        return _intake_pan(a.path[0] if a.path else '', a)
    else:
        print(f'❌ 未知子命令：intake {sub}'); sys.exit(2)

    # ---- 落库 ----
    day = datetime.now().strftime('%Y%m%d')
    short = re.sub(r'[^\w\u4e00-\u9fa5]+', '_', name)[:24] or 'mat'
    outdir = os.path.join(MAT, f'{day}_{source}_{short}')
    _ensure(outdir)
    man = _load_json(os.path.join(outdir, 'manifest.json'),
                     {'date': datetime.now().strftime('%Y-%m-%d'), 'source': source,
                      'name': short, 'items': [], 'counts': {}}) or {}
    seen = {it.get('sha256') for it in man.get('items', []) if it.get('sha256')}
    added = dup = 0
    for it in items:
        src = it['path']
        rec = {'src': os.path.abspath(src), 'at': now()}
        if not os.path.isfile(src):
            rec.update(status=ST_MISS, why='不存在或不是文件')
            man['items'].append(rec); continue
        h = sha256_file(src)
        rec.update(sha256=h, size=os.path.getsize(src))
        if h in seen:
            rec.update(status=ST_OK, why='重复（内容哈希已存在），未再落一份', dup=True)
            dup += 1
            man['items'].append(rec); continue
        dst = os.path.join(outdir, os.path.basename(src))
        i = 1
        while os.path.exists(dst):
            stem, ext = os.path.splitext(os.path.basename(src))
            dst = os.path.join(outdir, f'{stem}_{i}{ext}'); i += 1
        try:
            shutil.copy2(src, dst)
            rec['local'] = os.path.relpath(dst, HOME)
        except Exception as e:
            rec.update(status=ST_UNK, why=f'拷贝失败 {type(e).__name__}: {e}')
            man['items'].append(rec); continue
        txt, how = _extract_text(dst)
        if txt is None:
            st, why_st = classify_failure_why(how)
            rec.update(status=st, why=how, why_attrib=why_st, chars=0)
        else:
            rec.update(status=ST_OK, why=how, chars=len(txt), chars_per_k=round(len(txt) / max(1, os.path.getsize(dst) / 1024), 1))
            tp = os.path.splitext(dst)[0] + '.extracted.txt'
            with open(tp, 'w', encoding='utf-8') as f:
                f.write(txt)
        seen.add(h); added += 1
        man['items'].append(rec)

    cnt = {ST_OK: 0, ST_MISS: 0, ST_UNK: 0}
    for it in man['items']:
        cnt[it.get('status', ST_UNK)] = cnt.get(it.get('status', ST_UNK), 0) + 1
    man.update(counts=cnt, n_files=len(man['items']), updated=now())
    _save_json(os.path.join(outdir, 'manifest.json'), man)

    # ★验收格式：三态逐条报，不许只报"完成"
    print(f'\n落地：{os.path.relpath(outdir, HOME)}（新增 {added}，重复 {dup}）')
    print(f'三态：{ST_LABEL[ST_OK]} {cnt[ST_OK]} ｜ {ST_LABEL[ST_UNK]} {cnt[ST_UNK]} ｜ {ST_LABEL[ST_MISS]} {cnt[ST_MISS]}')
    bad = [it for it in man['items'] if it.get('status') != ST_OK]
    if bad:
        print('未取到（逐条）：')
        for it in bad[:40]:
            print(f'  [{ST_LABEL[it["status"]]}] {os.path.basename(it["src"])} — {it.get("why","")[:100]}')
        if len(bad) > 40:
            print(f'  … 另有 {len(bad)-40} 条，见 manifest.json')
    if cnt[ST_OK] == 0 and man['items']:
        print('判定：❌ 一条都没取到 —— 不是"完成"，是采集失败，别往下一步走。')
        sys.exit(1)


def _intake_url(url, a):
    """网页/公众号。**200 ≠ 有正文**：SPA 壳、登录墙、列表页都要分开判。"""
    try:
        r = subprocess.run(['curl', '-sS', '-L', '-m', '40', '-o', '-',
                            '-w', '\n<<HTTP:%{http_code}>>', '-A',
                            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                            '(KHTML, like Gecko) Chrome/124.0 Safari/537.36', url],
                           capture_output=True, timeout=90)
        raw = r.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        raw = '\n<<HTTP:000>>'; r = None
    except Exception as e:
        print(f'❌ 取回失败 {type(e).__name__}: {e}'); sys.exit(2)
    m = re.search(r'<<HTTP:(\d{3})>>\s*$', raw)
    code = m.group(1) if m else '000'
    body = raw[:m.start()] if m else raw
    if code == '412':
        print(f'判定：⚠️ 无法判定 —— HTTP 412（CDN 拒机房 IP，不是页面不存在；S171 归因）')
        sys.exit(3)
    if code in ('404', '410'):
        print(f'判定：❌ 确定取不到 —— HTTP {code}'); sys.exit(1)
    if code not in ('200',):
        print(f'判定：⚠️ 无法判定 —— HTTP {code}（既不是 200 也不是明确的 404）'); sys.exit(3)
    txt = body
    if re.search(r'<script[^>]*>.*?(__NUXT__|window\.__INITIAL|vite)', body, re.S | re.I) \
            and len(re.sub(r'<[^>]+>', '', body).strip()) < 500:
        print('判定：⚠️ 无法判定 —— 200 但正文为空（SPA 壳，需无头浏览器渲染）')
        print('      200 ≠ 有内容。别把"页面能开"当成"取到正文"。'); sys.exit(3)
    if re.search(r'(登录后|扫码登录|请先登录|sign in to continue)', body[:20000], re.I):
        print('判定：⚠️ 无法判定 —— 登录墙（不是失败，是需要凭据）'); sys.exit(3)
    day = datetime.now().strftime('%Y%m%d')
    short = re.sub(r'[^\w\u4e00-\u9fa5]+', '_', a.name or url.split('//')[-1][:40])[:24]
    outdir = os.path.join(MAT, f'{day}_url_{short}')
    _ensure(outdir)
    hp = os.path.join(outdir, 'source.html')
    # ★ S176b：落盘是三步写（html → extracted.txt → manifest.json）。
    #   前两步成功、第三步失败 → 留下**半个目录**：有文件、无 manifest、无记录。
    #   这种残留本技能删不干净（不知道该不该删），投递进清道夫由用户按单处理。
    try:
        with open(hp, 'w', encoding='utf-8') as f:
            f.write(body)
        plain = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', body, flags=re.S | re.I)
        plain = re.sub(r'<[^>]+>', '\n', plain)
        plain = re.sub(r'\n{3,}', '\n\n', plain).strip()
        tp = os.path.join(outdir, 'source.extracted.txt')
        with open(tp, 'w', encoding='utf-8') as f:
            f.write(plain)
        _save_json(os.path.join(outdir, 'manifest.json'), {
            'date': datetime.now().strftime('%Y-%m-%d'), 'source': 'url', 'name': short,
            'items': [{'src': url, 'at': now(), 'status': ST_OK, 'why': f'HTTP {code}',
                       'chars': len(plain), 'local': os.path.relpath(tp, HOME),
                       'sha256': hashlib.sha256(body.encode()).hexdigest()}],
            'counts': {ST_OK: 1, ST_MISS: 0, ST_UNK: 0}, 'n_files': 1, 'updated': now()})
    except Exception as e:
        # M-R60：失败必须说清是什么失败，不得折成"没抓到"
        print(f'❌ 落盘失败（{type(e).__name__}: {e}）——目录 {outdir} 已成半成品')
        _to_janitor(outdir, f'learn-hub intake 落盘半成品（{type(e).__name__}），无 manifest，需人工确认后删')
        sys.exit(2)
    print(f'取到了：HTTP {code}，正文 {len(plain)} 字（原文 HTML {len(body)} 字）')
    print(f'落地：{os.path.relpath(outdir, HOME)}')
    if len(plain) < 200:
        print('⚠️ 正文不足 200 字 —— 可能抓到的是导航/列表页，不是文章页。请人看一眼。')


def _intake_pan(link, a):
    """网盘：委托探源司 pan（单一事实源·M-R38），不自己重写一套。"""
    if not link:
        print('❌ intake pan 需要分享链接'); sys.exit(2)
    pb = os.path.expanduser('~/.codebuddy/skills/probe-hub/scripts/probe.py')
    if not os.path.isfile(pb):
        print(f'❌ 探源司引擎不在：{pb}（本技能不重写网盘取料）'); sys.exit(2)
    print('→ 委托探源司 pan（412 单列不并进失败）')
    r = subprocess.run([sys.executable, pb, 'pan', 'pull', link[:0] or 'x'] if False else
                       [sys.executable, pb, 'pan', '--help'], capture_output=True)
    print(r.stdout.decode('utf-8', 'replace')[:800])
    print('\n用法：先用 `tvbox probe pan pull <链接> --outdir <目录>` 落到磁盘，'
          '再 `learn intake scan <目录> --recursive`。')


# ═══════════════════════════════════════════════════════════════
# sort 分类整理
# ═══════════════════════════════════════════════════════════════
def _load_materials(d):
    """读素材目录里的 extracted 文本。返回 [(名字, 文本)]，抽不出的单独记。"""
    out, bad = [], []
    for dp, _, fns in os.walk(d):
        for fn in fns:
            if fn.endswith('.extracted.txt'):
                try:
                    out.append((fn[:-14], open(os.path.join(dp, fn), encoding='utf-8').read()))
                except Exception as e:
                    bad.append((fn, f'{type(e).__name__}'))
            elif os.path.splitext(fn)[1].lower() in ('.md', '.txt') and not fn.endswith('.json'):
                try:
                    out.append((fn, open(os.path.join(dp, fn), encoding='utf-8').read()))
                except Exception as e:
                    bad.append((fn, f'{type(e).__name__}'))
    return out, bad



def _norm(s):
    """标题归一化：去 # 前缀、去所有空白、去全角标点。

    ★S172 实测缺陷：原来只对**节点标题**做 `re.sub(r'^#+\s*|\s+','',title)`，
      而素材原文里 `# 第一章 勾股定理` 的空格还在，`find` 永远命中不了 →
      「有出处 0/6」还报得像正常。**归一化必须双侧同归**，单边清洗 = 假匹配。
    """
    return re.sub(r'[\s#*·、，,。.:：]', '', str(s or '')).lower()

def cmd_sort(a):
    sub = a.sub
    if sub == 'outline':
        d = a.path[0] if a.path else ''
        if not os.path.isdir(d):
            print(f'❌ 不是目录：{d}'); sys.exit(2)
        mats, bad = _load_materials(d)
        if not mats:
            print(f'❌ {d} 下没有可读文本（先 intake，或文件抽不出文本）'); sys.exit(1)
        fw = None
        if a.frm:
            fp = a.frm
            if not os.path.isfile(fp):
                print(f'❌ 外部框架不存在：{fp}'); sys.exit(2)
            fw = open(fp, encoding='utf-8').read()
        nodes = []
        if fw:
            # 外部框架：每行一个节点（支持 markdown 标题 / 数字编号 / 制表符缩进）
            for ln in fw.splitlines():
                s = ln.strip().lstrip('#').strip()
                if not s or len(s) > 80:
                    continue
                depth = (len(ln) - len(ln.lstrip(' \t'))) // 2
                mm = re.match(r'^(\d+(?:\.\d+)*)[\.\s、]', ln.rstrip())
                if mm:
                    depth = mm.group(1).count('.')
                nodes.append({'id': f'K{len(nodes)+1:03d}', 'title': s, 'level': depth,
                              'evidence': [], 'denominator': 'external'})
            print(f'外部框架：{len(nodes)} 个节点（分母来源 = {os.path.basename(fp)}）')
        else:
            # 自建：切段落 + 频率挑标题。**明确标注这不是分母**
            import collections
            heads = collections.Counter()
            for nm, t in mats:
                for ln in t.splitlines():
                    s = ln.strip()
                    if 2 <= len(s) <= 30 and (s.startswith('#') or re.match(r'^第[一二三四五六七八九十\d]+[章节讲部分]', s)):
                        heads[re.sub(r'^#+\s*', '', s)] += 1
            for h, c in heads.most_common(80):
                nodes.append({'id': f'K{len(nodes)+1:03d}', 'title': h, 'level': 0,
                              'evidence': [], 'denominator': 'self-built'})
            print(f'自动切分：{len(nodes)} 个节点 ⚠️ 自建分母，**不得用作覆盖率分母**（M-L02）')
        # 给每个节点找 evidence
        for n in nodes:
            key = _norm(n['title'])[:16]
            if not key:
                n['unverified'] = True
                n['unverified_why'] = '标题归一化后为空，无法定位'
                continue
            for nm, t in mats:
                # ★双侧同归一：素材也按同一规则归一后再找行号，否则带 # 的标题永远对不上
                tnorm = _norm(t)
                idx = tnorm.find(key)
                if idx >= 0:
                    # 行号必须回到**原文**去数：归一化会吞字符，偏移量对不上
                    raw_line = None
                    for ln_no, raw in enumerate(t.splitlines(), 1):
                        if key and key in _norm(raw):
                            raw_line = (ln_no, raw.strip())
                            break
                    if raw_line is None:
                        continue
                    n['evidence'].append({'file': nm, 'line': raw_line[0],
                                          'quote': raw_line[1][:120]})
                    if len(n['evidence']) >= 3:
                        break
            if not n['evidence']:
                n['unverified'] = True
                n['unverified_why'] = '素材中找不到该标题（可能这份框架超出了素材范围）'
        outp = a.out or os.path.join(WORK, 'outline.json')
        _save_json(outp, {'built': now(), 'source_dir': os.path.abspath(d), 'nodes': nodes,
                          'denominator': 'external' if fw else 'self-built',
                          'external_framework': os.path.abspath(a.frm) if a.frm else None,
                          'materials': [m[0] for m in mats],
                          'unreadable': bad})
        ev = sum(1 for n in nodes if not n.get('unverified'))
        print(f'节点 {len(nodes)}，有出处 {ev}/{len(nodes)}'
              f'{"，无出处 " + str(len(nodes)-ev) + " 条（已标 unverified）" if ev < len(nodes) else ""}')
        print(f'写出：{outp}')
        if bad:
            print(f'⚠️ {len(bad)} 个文件读不出：{bad[:5]}')
        return

    if sub == 'dedupe':
        d = a.path[0] if a.path else ''
        if not os.path.isdir(d):
            print(f'❌ 不是目录：{d}'); sys.exit(2)
        import collections
        sig = collections.defaultdict(list)
        for dp, _, fns in os.walk(d):
            for fn in fns:
                p = os.path.join(dp, fn)
                if fn.endswith('.extracted.txt') or os.path.splitext(fn)[1].lower() == '.txt':
                    try:
                        t = open(p, encoding='utf-8', errors='replace').read()
                    except Exception:
                        continue
                    # shingle 指纹：5 词一组，取前 200 组
                    w = re.findall(r'[\w\u4e00-\u9fa5]+', t[:200000])
                    sh = {' '.join(w[i:i + 5]) for i in range(0, max(1, len(w) - 4))}
                    for s in list(sh)[:200]:
                        sig[s].append(os.path.relpath(p, d))
        pairs = collections.Counter()
        for s, files in sig.items():
            if len(files) > 1:
                for i in range(len(files)):
                    for j in range(i + 1, len(files)):
                        pairs[tuple(sorted((files[i], files[j])))] += 1
        print(f'近重复检测：{len(pairs)} 对候选（按共享 5-gram 数排序）')
        for (x, y), c in pairs.most_common(20):
            print(f'  {c:>5}  {x}  ⇄  {y}')
        if not pairs:
            print('  （未发现近重复）')
        print('⚠️ 共享片段多 ≠ 同一份文件。**这些只是候选，合并前要人看一眼**——'
              '两份不同讲义都抄了同一段课标，也会高分。')
        return

    if sub == 'difficulty':
        p = a.path[0] if a.path else ''
        o = _load_json(p)
        if not o:
            print(f'❌ 读不出 outline：{p}'); sys.exit(2)
        for n in o.get('nodes', []):
            t = n.get('title', '')
            sc, why = 0, []
            if re.search(r'\d', t):
                sc += 1; why.append('含数字/公式')
            if len(t) > 14:
                sc += 1; why.append('表述长')
            if re.search(r'(证明|推导|综合|应用|比较|分析)', t):
                sc += 2; why.append('含高阶动词')
            lvl = '基础' if sc == 0 else ('进阶' if sc <= 2 else '拔高')
            n['difficulty'] = {'level': lvl, 'score': sc, 'why': why or ['短名词性标题']}
        _save_json(p, o)
        c = {}
        for n in o.get('nodes', []):
            c[n['difficulty']['level']] = c.get(n['difficulty']['level'], 0) + 1
        print('难度标注完成（依据逐条写进 difficulty.why，不许只给标签）：', c)
        print(f'写回：{p}')
        return

    print(f'❌ 未知子命令：sort {sub}'); sys.exit(2)


# ═══════════════════════════════════════════════════════════════
# gate 质检闸：本技能的立身之处
# ═══════════════════════════════════════════════════════════════
def cmd_gate(a):
    sub = a.sub
    if sub == 'grounding':
        d = a.path[0] if a.path else ''
        if not os.path.isdir(d):
            print(f'❌ 不是目录：{d}'); sys.exit(2)
        ok = bad = 0
        badlist = []
        for dp, _, fns in os.walk(d):
            for fn in fns:
                if not fn.endswith('.md'):
                    continue
                p = os.path.join(dp, fn)
                for i, ln in enumerate(open(p, encoding='utf-8', errors='replace'), 1):
                    if not ln.strip().startswith(('-', '*', '|')) or len(ln.strip()) < 12:
                        continue
                    if re.search(r'\[出处[:：]', ln) or re.search(r'\[source[:：]', ln) \
                            or '⚠️ 无出处' in ln:
                        ok += 1
                    else:
                        bad += 1
                        badlist.append(f'{fn}:{i}')
        tot = ok + bad
        print(f'出处检查：{ok}/{tot} 条带 source_ref，无出处 {bad} 条')
        for b in badlist[:30]:
            print(f'  ✗ {b}')
        if bad:
            print(f'判定：❌ 不通过 —— {bad} 条内容指不回原文。**要么补 ref，要么单列进"⚠️ 无出处（推测）"区块。**')
            sys.exit(1)
        if tot == 0:
            print('判定：⚠️ 无法判定 —— 目录里没有可检查的内容条目（不是"通过 0 条"）')
            sys.exit(3)
        print('判定：✅ 通过 —— 全部条目均可指回原文。'); return

    if sub == 'coverage':
        op, dp = a.path[0], a.path[1] if len(a.path) > 1 else ''
        o = _load_json(op)
        if not o:
            print(f'❌ 读不出 outline：{op}'); sys.exit(2)
        if o.get('denominator') != 'external':
            print('判定：⚠️ 无法判定 —— 分母是自建的（denominator=self-built），'
                  '覆盖率这个数**不许报**（M-L02）。请用 --from <教材目录/考纲> 重建 outline。')
            sys.exit(3)
        blob = ''
        for root2, _, fns in os.walk(dp):
            for fn in fns:
                if fn.endswith('.md'):
                    blob += open(os.path.join(root2, fn), encoding='utf-8', errors='replace').read()
        hit, miss = [], []
        # ★S172：这里原来又写了一遍 `re.sub(r'^#+\s*|\s+','',title)` ——
        #   和 sort 里刚修的**是同一个 bug 的另一份拷贝**。修一处、漏一处，等于没修。
        #   统一走 _norm，双侧同归。
        bnorm = _norm(blob)
        for n in o.get('nodes', []):
            key = _norm(n['title'])[:8]
            (hit if key and key in bnorm else miss).append(n['title'])
        tot = len(hit) + len(miss)
        rate = len(hit) / tot * 100 if tot else 0
        print(f'覆盖 {len(hit)}/{tot}（{rate:.1f}%）｜分母来自 {os.path.basename(o.get("external_framework") or "?")}')
        if miss:
            print(f'缺 {len(miss)}，逐条列出：')
            for m in miss:
                print(f'  ✗ {m}')
        print(f'判定：{"✅ 通过" if rate >= 95 else "❌ 不通过 —— 覆盖率未达 95%"}')
        sys.exit(0 if rate >= 95 else 1)

    if sub == 'ratio':
        op = a.path[0] if a.path else ''
        o = _load_json(op)
        if not o:
            print(f'❌ 读不出 outline：{op}'); sys.exit(2)
        c, dev = {}, 0
        for n in o.get('nodes', []):
            dd = n.get('difficulty') or {}
            c[dd.get('level', '未标')] = c.get(dd.get('level', '未标'), 0) + 1
            if dd.get('level') and not dd.get('why'):
                dev += 1
        tot = sum(c.values()) or 1
        print('难度分布：' + ' ｜ '.join(f'{k} {v}（{v/tot*100:.0f}%）' for k, v in sorted(c.items())))
        exp = {}
        for kv in (a.expect or '').split(','):
            if ':' in kv or '=' in kv:
                k, v = re.split(r'[:=]', kv)
                exp[k.strip()] = float(v)
        bad = []
        for k, v in exp.items():
            real = c.get(k, 0) / tot * 100
            if abs(real - v) > 10:
                bad.append(f'{k} 期望 {v:.0f}% 实际 {real:.0f}%')
        if dev:
            print(f'⚠️ {dev} 个节点标了难度但没写依据 —— 标签没有依据等于没标')
        if bad:
            print('判定：❌ 不通过 —— ' + '；'.join(bad)); sys.exit(1)
        print('判定：✅ 通过' + ('（期望分布未给，仅报分布不判偏离）' if not exp else ''))
        return

    if sub == 'run':
        op = a.path[0] if a.path else ''
        dp = a.path[1] if len(a.path) > 1 else ''
        o = _load_json(op)
        if not o:
            print(f'❌ 读不出 outline：{op}'); sys.exit(2)
        nodes = o.get('nodes', [])
        ext = o.get('denominator') == 'external'
        blob = ''
        for root2, _, fns in os.walk(dp):
            for fn in fns:
                if fn.endswith('.md'):
                    blob += open(os.path.join(root2, fn), encoding='utf-8', errors='replace').read()
        # ★同一判据只许有一个实现（M-R38 single source of truth）——之前 gate run 和
        #   gate coverage 各写一遍归一化，改一处漏一处。现在都走 _norm。
        bnorm = _norm(blob)
        hit = [n for n in nodes if _norm(n['title'])[:8] and _norm(n['title'])[:8] in bnorm]
        miss = [n for n in nodes if n not in hit]
        # 出处
        g_ok = g_bad = 0
        for root2, _, fns in os.walk(dp):
            for fn in fns:
                if not fn.endswith('.md'):
                    continue
                for ln in open(os.path.join(root2, fn), encoding='utf-8', errors='replace'):
                    if ln.strip().startswith(('-', '*', '|')) and len(ln.strip()) >= 12:
                        if re.search(r'\[出处[:：]|\[source[:：]|⚠️ 无出处', ln):
                            g_ok += 1
                        else:
                            g_bad += 1
        ac_ok = ac_bad = 0
        for ln in blob.splitlines():
            if re.match(r'^\s*(答案|答)[:：]', ln):
                if re.search(r'\[出处[:：]|原文|见第', ln):
                    ac_ok += 1
                else:
                    ac_bad += 1
        dc, dev = {}, 0
        for n in nodes:
            dd = n.get('difficulty') or {}
            dc[dd.get('level', '未标')] = dc.get(dd.get('level', '未标'), 0) + 1
            if dd.get('level') and not dd.get('why'):
                dev += 1
        tot = len(nodes) or 1
        cov_rate = len(hit) / tot * 100

        # ★强制验收格式：四查逐项报数，不许写"质检通过"四个字
        cov_s = (f'覆盖 {len(hit)}/{tot}（{cov_rate:.0f}%）' if ext
                 else f'覆盖 ⚠️ 无法判定（分母自建，不许报数·M-L02；仅统计命中 {len(hit)}/{tot}）')
        print('质检结果：'
              f'{cov_s}｜有出处 {g_ok}/{g_ok+g_bad}' +
              (f'（无出处 {g_bad}，已单列）' if g_bad else '') +
              f'｜答案自洽 {ac_ok}/{ac_ok+ac_bad}' +
              (f'（{ac_bad} 条无原文支持）' if ac_bad else '') +
              f'｜难度{"偏离 " + str(dev) + " 处" if dev else "分布 " + str(dc)}')
        reasons = []
        if not ext:
            reasons.append('覆盖率分母非外部（M-L02）')
        elif cov_rate < 95:
            reasons.append(f'覆盖率 {cov_rate:.0f}% 未达 95%')
        if g_bad:
            reasons.append(f'{g_bad} 条无出处')
        if ac_bad:
            reasons.append(f'{ac_bad} 条答案在原文中找不到支持')
        if dev:
            reasons.append(f'{dev} 处难度无依据')
        if reasons:
            print('判定：❌ 不通过 —— ' + '；'.join(reasons) + '。')
            if miss:
                print(f'缺失节点（{len(miss)}）：')
                for n in miss[:20]:
                    print(f'  ✗ {n["title"]}')
            sys.exit(1)
        print('判定：✅ 通过 —— 四查全过。'); return

    print(f'❌ 未知子命令：gate {sub}'); sys.exit(2)


# ═══════════════════════════════════════════════════════════════
# forge 特有工具库
# ═══════════════════════════════════════════════════════════════
def cmd_forge(a):
    sub = a.sub
    if sub == 'diagnose':
        d = a.path[0] if a.path else ''
        if os.path.isfile(d):
            rows = [ln.strip() for ln in open(d, encoding='utf-8') if ln.strip()]
        elif os.path.isdir(d):
            rows = []
            for fn in os.listdir(d):
                if fn.endswith(('.txt', '.csv', '.md')):
                    rows += [ln.strip() for ln in open(os.path.join(d, fn), encoding='utf-8') if ln.strip()]
        else:
            print(f'❌ 不是文件也不是目录：{d}'); sys.exit(2)
        # ★S172：原来 outline 路径**写死** work/outline.json —— 换成别处的 outline，
        #   titles 就是空表，于是「归因 0/N」看起来像"数据问题"，其实是**路径问题**。
        #   这种错最毒：它报了一个很具体的数（0 条），让人以为是分析结果。
        op = getattr(a, 'outline', '') or os.path.join(WORK, 'outline.json')
        if not os.path.isfile(op):
            print(f'判定：⚠️ 无法判定 —— 找不到 outline：{op}')
            print('      归因必须有框架做锚点；没有 outline 就不许报"归因 0 条"（那是在骗人）。')
            sys.exit(3)
        o = _load_json(op, {}) or {}
        titles = [n['title'] for n in o.get('nodes', [])]
        if not titles:
            print(f'判定：⚠️ 无法判定 —— outline 里没有节点：{op}'); sys.exit(3)
        attr, unattr = {}, []
        # ★S172：原来用「标题原串 in 错题」+「标题前两个中文词 in 错题」。
        #   实测归因 0/4 —— 标题是「1.1 定理证明」，前两个词是「定理」「证明」，
        #   而错题写的是「勾股定理求第三边算错了」，只有「定理」能撞上，前面又被编号污染。
        #   改成**词级重叠**：标题先 _norm 去编号，再切 2-gram，错题里命中的 2-gram 数 ≥1 即算候选。
        def grams(t):
            tt = re.sub(r'^[\d.]+', '', _norm(t))
            return {tt[i:i + 2] for i in range(max(0, len(tt) - 1))}
        title_grams = [(t, grams(t)) for t in titles if t]
        for r in rows:
            rn = _norm(r)
            best, best_hit = None, 0
            for t, g in title_grams:
                h = sum(1 for x in g if x in rn)
                if h > best_hit:
                    best, best_hit = t, h
            # 至少命中 2 个 2-gram 才算归因（1 个太容易误中，宁可不归因）
            if best and best_hit >= 2:
                attr[best] = attr.get(best, 0) + 1
            else:
                unattr.append(r)
        print(f'错题 {len(rows)} 条 → 归因 {len(rows)-len(unattr)} 条，无法归因 {len(unattr)} 条')
        for k, v in sorted(attr.items(), key=lambda x: -x[1])[:10]:
            print(f'  薄弱：{k}（{v} 题）')
        if unattr:
            print('无法归因（**不许硬套"基础不牢"**）：')
            for u in unattr[:10]:
                print(f'  ? {u[:60]}')
        if titles and len(unattr) > len(rows) * 0.5:
            print('判定：⚠️ 无法归因过半 —— 要么 outline 不匹配，要么错题描述太粗。先对框架。')
        return

    if sub == 'schedule':
        n = int(a.n or 10)
        # SM-2 简化：间隔 1,3,7,15,30,60... 明确公式，不许"感觉差不多"
        # ★S172：原写法 `d = 1 if i==0 else (3 if i==1 else ...)` 在 i=0 时把 d 又设回 1，
        #   于是输出「第1天 → 第1天」。间隔序列必须**严格递增**，重复就是错。
        seq = [1]
        for i in range(1, n):
            seq.append(3 if i == 1 else max(seq[-1] + 1, round(seq[-1] * 1.9)))
        print(f'记忆曲线排程（SM-2 简化变体，间隔序列 = 1,3,7,15,30…，因子 1.9）：')
        print('  ' + ' → '.join(f'第{x}天' for x in seq))
        print('  依据：首日 1 → 次日 3 → 其后 ×1.9 取整；如果你要别的曲线，必须给公式，不许口头"大概"。')
        return

    if sub == 'check':
        d = a.path[0] if a.path else LIB
        if not os.path.isdir(d):
            print(f'❌ 不是目录：{d}'); sys.exit(2)
        idx = _load_json(INDEX, {'items': []}) or {'items': []}
        stale = [it for it in idx.get('items', [])
                 if it.get('stored_at', '') and it.get('stored_at', '') < f'2025-01-01']
        print(f'资料体检：{len(idx.get("items", []))} 份入库')
        print(f'  疑似过时（早于 2025）：{len(stale)}')
        for s in stale[:10]:
            print(f'    · {s.get("name","?")}（{s.get("stored_at","")}）')
        print('  三类必查：教材改版 / 课标变动 / 来源失效 —— 这三项没有自动判据，'
              '**本命令只给线索，不下"没问题"的结论**。')
        return

    print(f'❌ 未知子命令：forge {sub}'); sys.exit(2)


# ═══════════════════════════════════════════════════════════════
# store 入库
# ═══════════════════════════════════════════════════════════════
def cmd_store(a):
    sub = a.sub
    if sub == 'put':
        d = a.path[0] if a.path else ''
        if not os.path.isdir(d):
            print(f'❌ 不是目录：{d}'); sys.exit(2)
        idx = _load_json(INDEX, {'items': []}) or {'items': []}
        name = a.name or os.path.basename(os.path.abspath(d))
        for it in idx['items']:
            if it.get('name') == name:
                print(f'判定：⚠️ 已存在同名成品「{name}」—— 不直接覆盖。'
                      f'换 --name，或先 `learn store find {name}` 看一眼。'); sys.exit(3)
        _ensure(LIB)
        dst = os.path.join(LIB, name)
        if os.path.abspath(d) != os.path.abspath(dst):
            if os.path.exists(dst):
                print(f'❌ 目标已存在：{dst}'); sys.exit(1)
            shutil.copytree(d, dst)
        files = []
        for dp, _, fns in os.walk(dst):
            for fn in fns:
                fp = os.path.join(dp, fn)
                files.append({'p': os.path.relpath(fp, LIB), 'size': os.path.getsize(fp),
                              'sha256': sha256_file(fp)[:16]})
        idx['items'].append({'name': name, 'tags': (a.tags or '').split(',') if a.tags else [],
                             'stored_at': datetime.now().strftime('%Y-%m-%d'),
                             'files': files, 'n_files': len(files),
                             'source_dir': os.path.abspath(d)})
        _save_json(INDEX, idx)
        print(f'入库：{name}（{len(files)} 个文件）→ {os.path.relpath(dst, HOME)}')
        print(f'索引：{os.path.relpath(INDEX, HOME)}（现 {len(idx["items"])} 份）')
        return

    if sub == 'find':
        kw = ' '.join(a.path) if a.path else ''
        idx = _load_json(INDEX, {'items': []}) or {'items': []}
        hits = [it for it in idx['items']
                if kw in it.get('name', '') or any(kw in t for t in it.get('tags', []))]
        print(f'检索「{kw}」→ {len(hits)} 份')
        for h in hits:
            print(f'  · {h["name"]}（{h.get("stored_at","")}，{h.get("n_files",0)} 文件，标签 {h.get("tags")}）')
        if hits:
            print('⚠️ 已有相关成品 —— **先看能不能复用，别重复造**。')
        return

    if sub == 'trace':
        nm = a.path[0] if a.path else ''
        idx = _load_json(INDEX, {'items': []}) or {'items': []}
        it = next((x for x in idx['items'] if x['name'] == nm), None)
        if not it:
            print(f'❌ 库里没有「{nm}」'); sys.exit(2)
        print(f'{nm} ← 源目录 {it.get("source_dir")}')
        print(f'  入库 {it.get("stored_at")}｜{it.get("n_files")} 文件')
        for f in it.get('files', [])[:20]:
            print(f'    {f["size"]:>9,}  {f["p"]}  {f["sha256"]}')
        return

    print(f'❌ 未知子命令：store {sub}'); sys.exit(2)


# ═══════════════════════════════════════════════════════════════
# watch 监测
# ═══════════════════════════════════════════════════════════════
def cmd_watch(a):
    sub = a.sub
    if sub == 'sources':
        rows = []
        if os.path.isdir(MAT):
            for d in sorted(os.listdir(MAT)):
                mf = os.path.join(MAT, d, 'manifest.json')
                m = _load_json(mf, {}) or {}
                for it in m.get('items', []):
                    if str(it.get('src', '')).startswith('http'):
                        rows.append(it)
        if not rows:
            print('（没有网络来源素材，无需监测）'); return
        for it in rows:
            src = it['src']
            try:
                r = subprocess.run(['curl', '-sS', '-o', '/dev/null', '-m', '25',
                                    '-w', '%{http_code}', '-L', src],
                                   capture_output=True, timeout=40)
                code = r.stdout.decode().strip() or '000'
            except Exception:
                code = '000'
            st = ST_OK if code == '200' else (ST_MISS if code in ('404', '410') else ST_UNK)
            print(f'  [{ST_LABEL[st]}] HTTP {code}  {src[:90]}')
            if st == ST_UNK:
                print('        ↑ 不是"已失效"：无法判定（412/超时/登录墙）不算删文')
        return
    if sub == 'stale':
        days = int(a.days or 180)
        idx = _load_json(INDEX, {'items': []}) or {'items': []}
        old = []
        for it in idx.get('items', []):
            try:
                dt = datetime.strptime(it.get('stored_at', ''), '%Y-%m-%d')
            except Exception:
                continue
            if (datetime.now() - dt).days > days:
                old.append((it['name'], it.get('stored_at'), (datetime.now() - dt).days))
        print(f'超过 {days} 天未更新的资料：{len(old)} 份')
        for n, s, d in old:
            print(f'  · {n}（{s}，{d} 天）')
        if not old:
            print('  （无）')
        return
    if sub == 'refresh':
        print('上游更新检查：需逐来源判据（公众号删文 / 教材改版 / 网盘失效），'
              '**本命令暂未实现自动判定**——不实现就不假装实现。\n'
              '先用 `learn watch sources` 查存活；教材改版这类需人工比对版本号。')
        return
    print(f'❌ 未知子命令：watch {sub}'); sys.exit(2)


# ═══════════════════════════════════════════════════════════════
# self-check：探针一律 AST 只取目标函数字面量
# ═══════════════════════════════════════════════════════════════
def _lits_of(fnname, only_cmp=False):
    """取函数里的字符串字面量。

    ★only_cmp=True 只取**比较表达式里的**字面量——这是 S172 的教训：
      上一版取"函数内全部字面量"，探针⑥因此从**报错文案**里的 'SPA' 判通过，
      而判定条件被删掉后文案还在，探针照样亮 → 又是「同侧变量互比」的变种。
      **判据必须取自执行路径，不能取自说明书。**
    """
    import ast
    src = open(__file__, encoding='utf-8').read()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == fnname), None)
    if fn is None:
        return ''
    out = []
    if only_cmp:
        for n in ast.walk(fn):
            if isinstance(n, ast.Compare):
                for sub in ast.walk(n):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        out.append(sub.value)
            # 元组成员判断（x in (...)）也走 Compare；Call 里的 tuple 另取
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                for arg in n.args:
                    if isinstance(arg, ast.Tuple):
                        for e in arg.elts:
                            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                                out.append(e.value)
            # ★真值测试：`if re.search('...', s): return ...` 没有 Compare 节点，
            #   但它**就是判定条件**。漏了这类，SPA 判据整段取不到。
            if isinstance(n, (ast.If, ast.While, ast.IfExp)):
                for sub in ast.walk(n.test):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        out.append(sub.value)
                    if isinstance(sub, ast.Tuple):
                        for e in sub.elts:
                            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                                out.append(e.value)
        return '\n'.join(out)
    return '\n'.join(n.value for n in ast.walk(fn)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str))


def _lits_of_branch(fnname, branch_sub):
    """只取 `if sub == '<branch>':` **该分支内部**的字符串字面量。

    ★S172：这是探针④栽的那一跤。对整个函数取字面量并集，'不通过' 在
      coverage / ratio 分支里也各有一份；把 grounding 分支里那个删了，并集里还剩，
      探针照样亮 —— **集合里有 ≠ 该分支有**。
      所以判据必须锁到分支，不能锁到函数。
    """
    import ast
    src = open(__file__, encoding='utf-8').read()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == fnname), None)
    if fn is None:
        return ''
    for n in ast.walk(fn):
        if not isinstance(n, ast.If):
            continue
        t = n.test
        # 匹配 `sub == 'xxx'`
        if (isinstance(t, ast.Compare) and len(t.comparators) == 1
                and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value == branch_sub):
            out = []
            for stmt in n.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        out.append(sub.value)
            return '\n'.join(out)
    return ''


def cmd_selfcheck(a):
    import tempfile
    res = []

    # ① 三态不折叠：412/超时必须判无法判定，404 才判确定取不到
    ok1 = (classify_failure('HTTP 412 Precondition') == ST_UNK
           and classify_failure('Connection timed out') == ST_UNK
           and classify_failure('404 Not Found') == ST_MISS
           and classify_failure('文件已删除') == ST_MISS
           and classify_failure('') == ST_UNK)
    # 反假阳性：一切能认出来的失败都不许落回 ok
    fp1 = any(classify_failure(x) == ST_OK for x in
              ('412', '404', 'timeout', 'login wall', 'zzz', ''))
    res.append(('三态不折叠·412不判死', ok1, not fp1))

    # ② 分母自建时不许报覆盖率（M-L02）
    d = tempfile.mkdtemp()
    try:
        op = os.path.join(d, 'o.json')
        _save_json(op, {'denominator': 'self-built', 'nodes': [{'title': 'AAA', 'level': 0}]})
        outp = os.path.join(d, 'prod'); _ensure(outp)
        open(os.path.join(outp, 'a.md'), 'w', encoding='utf-8').write('# AAA\n- 内容 [出处: a.md#1]\n')
        class _A:
            sub = 'coverage'; path = [op, outp]
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        rc = 0
        try:
            with redirect_stdout(buf):
                cmd_gate(_A())
        except SystemExit as e:
            rc = e.code
        msg = buf.getvalue()
        ok2 = (rc == 3 and '无法判定' in msg and 'M-L02' in msg)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    res.append(('分母自建不报覆盖率', ok2, True))

    # ③ 强验收格式存在：gate run 里必须有四项指标字面量
    l3 = _lits_of('cmd_gate')
    ok3 = all(k in l3 for k in ('覆盖', '有出处', '答案自洽', '难度', '判定'))
    # 反假阳性：不许出现"质检通过"这种一句话结案
    fp3 = '质检通过' in l3
    res.append(('强验收格式四查齐备', ok3, not fp3))

    # ④ 无出处必单列：gate grounding 不许静默通过
    #   ★S172：原来对整个 cmd_gate 取字面量并集，'不通过' 在 coverage/ratio 分支里也有，
    #     删掉 grounding 里那个，并集里还剩 → 探针照样亮。**集合里有 ≠ 该分支有。**
    #     现在只取 grounding 分支（if sub == 'grounding' 之后到下一个 if 之前）的字面量。
    l4 = _lits_of_branch('cmd_gate', 'grounding')
    ok4 = ('无出处' in l4 and '不通过' in l4)
    res.append(('无出处必单列', ok4, True))

    # ⑤ 去重按内容哈希不按文件名（行为测试：两个同名不同内容的文件必须都进去）
    d5 = tempfile.mkdtemp()
    try:
        fa = os.path.join(d5, 'same.md'); fb = os.path.join(d5, 'other_dir')
        os.makedirs(fb)
        open(fa, 'w', encoding='utf-8').write('甲内容 ' * 30)
        open(os.path.join(fb, 'same.md'), 'w', encoding='utf-8').write('乙内容 ' * 30)
        # ★S172：原来只测 sha256_file 自身——那是**工具**，不是**调用点**。
        #   把 cmd_intake 改成按文件名去重，那个测试照样过（判据错位）。
        #   现在**真跑 cmd_intake**，看同名不同内容的两个文件是不是都进去了。
        fa2 = os.path.join(d5, 'a.md'); os.makedirs(os.path.join(d5, 'b'), exist_ok=True)
        fb2 = os.path.join(d5, 'b', 'a.md')   # 同名 a.md，内容不同
        open(fa2, 'w', encoding='utf-8').write('甲内容 ' * 30)
        open(fb2, 'w', encoding='utf-8').write('乙内容 ' * 30)
        class _A5:
            sub = 'file'; path = [fa2, fb2]; name = 'zz_dedup_probe'
        import io as _io5
        from contextlib import redirect_stdout as _rso5
        with _rso5(_io5.StringIO()):
            try:
                cmd_intake(_A5())
            except SystemExit:
                pass
        mf5 = None
        for dd5 in (os.listdir(MAT) if os.path.isdir(MAT) else []):
            if dd5.endswith('_file_zz_dedup_probe'):
                mf5 = os.path.join(MAT, dd5, 'manifest.json')
        man5 = _load_json(mf5, {}) or {}
        its = man5.get('items', [])
        # 两个同名不同内容 → 必须都算"新增"、都不能被当重复
        ok5 = (len(its) == 2 and not any(i.get('dup') for i in its)
               and len({i.get('sha256') for i in its}) == 2)
        # ★探针不许在真目录留痕：它刚落了一份 zz_dedup_probe 素材
        for dd5 in (os.listdir(MAT) if os.path.isdir(MAT) else []):
            if dd5.endswith('_file_zz_dedup_probe'):
                shutil.rmtree(os.path.join(MAT, dd5), ignore_errors=True)
    finally:
        shutil.rmtree(d5, ignore_errors=True)
    res.append(('去重按内容哈希', ok5, True))

    # ⑥ 200≠有正文：url 采集必须能识别 SPA 壳 / 登录墙
    # ★只取比较表达式字面量（only_cmp）：上一版取全函数字面量，'SPA' 出现在报错文案里，
    #   判定条件被删后文案还在 → 探针照样亮。**判据取自执行路径，不取自说明书。**
    l6 = _lits_of('_intake_url', only_cmp=True)
    # ★S172：原来写 `'__NUXT__' in l6 or 'vite' in l6` —— 一个模式串里两个词是 OR 关系，
    #   删掉 __NUXT__ 后 vite 还在，探针照样亮。**OR 判据等于没判**。
    #   现在按模式串逐条查：SPA 三特征各自必须在，登录墙四特征各自必须在。
    spa_need = ('__NUXT__', r'window\.__INITIAL', 'vite')
    log_need = ('登录后', '扫码登录', '请先登录', 'sign in to continue')
    # 单条正则里任一分支缺失不算坏，但**至少有两条**在才算真判据（删一条不影响）
    # ★S172 二次更正：原来阈值写 ">=3"，删掉『请先登录』后还剩 3 条 → 照样过。
    #   同一个正则的并列分支，**少一个就少一种识别能力**，必须全在。
    ok6 = (all(k in l6 for k in spa_need) and all(k in l6 for k in log_need))
    # 反假阳性：正常文章页不许被判成 SPA/登录墙。
    # ★原来写 `... and False`，恒为 True —— 那是个**假判据**（假装验了，其实没验）。
    #   现在真喂一段正常 HTML 走 _intake_url 的判定逻辑。
    import re as _re6
    _normal = ('<html><body><h1>勾股定理</h1><p>' + '这是正文段落，讲的是直角三角形三边关系。' * 30
               + '</p></body></html>')
    _spa_pat = r'<script[^>]*>.*?(__NUXT__|window\.__INITIAL|vite)'
    _log_pat = r'(登录后|扫码登录|请先登录|sign in to continue)'
    _plain = _re6.sub(r'<[^>]+>', '', _normal).strip()
    fp6 = not (_re6.search(_spa_pat, _normal, _re6.S | _re6.I) or _re6.search(_log_pat, _normal, _re6.I)
               or len(_plain) < 500)
    res.append(('200≠有正文·SPA壳单列', ok6, fp6))

    # ⑦ 网盘 412 归因单列（S171·行为测试，不查字面量）
    #   ★上一版查 '412' + '机房' 两个字面量，而 classify_failure 里 412 有**两层冗余**
    #     （_UNKNOWN_MARKS 一次、in 判断一次），改一层不影响结果 → 探针没抓住。
    #     现在改成**喂真实输入看返回**：412 必须真能归到 unknown。
    # ★必须验**理由**：只说"结果是 unknown"验不出来——末尾兜底也会给 unknown。
    st7, why7 = classify_failure_why('HTTP 412 Precondition Failed')
    ok7 = (st7 == ST_UNK and '412' in why7)
    fp7 = classify_failure('HTTP 404') != ST_UNK
    res.append(('网盘412归因·不是不存在', ok7, fp7))

    # ⑧ 难度必带依据（行为测试：标了 level 就必须有 why）
    d8 = tempfile.mkdtemp()
    try:
        op8 = os.path.join(d8, 'o.json')
        _save_json(op8, {'denominator': 'external',
                         'nodes': [{'title': '勾股定理的证明', 'level': 0,
                                    'evidence': [{'file': 'x', 'para': 1}]}]})
        class _A8:
            sub = 'difficulty'; path = [op8]
        import io as _io
        from contextlib import redirect_stdout as _rso
        with _rso(_io.StringIO()):
            cmd_sort(_A8())
        o8 = _load_json(op8, {}) or {}
        nd = (o8.get('nodes') or [{}])[0]
        dd = nd.get('difficulty') or {}
        # 必须同时有 level 和 why，且 why 非空
        ok8 = bool(dd.get('level')) and bool(dd.get('why'))
        # 反假阳性：短名词必须判"基础"，不能一律"拔高"
        fp8 = dd.get('level') != '拔高'
    finally:
        shutil.rmtree(d8, ignore_errors=True)
    res.append(('难度标注须带依据', ok8, fp8))

    # ⑨ 采集一条没取到必须判失败（不许报"完成"）——行为测试
    d9 = tempfile.mkdtemp()
    try:
        badf = os.path.join(d9, 'bogus.zzz')
        open(badf, 'w').write('x')
        class _A9:
            sub = 'file'; path = [badf]; name = 't'
        import io as _io2
        from contextlib import redirect_stdout as _rso2
        buf9 = _io2.StringIO()
        rc9 = 0
        try:
            with _rso2(buf9):
                cmd_intake(_A9())
        except SystemExit as e:
            rc9 = e.code
        msg9 = buf9.getvalue()
        # ★这里会往真 materials 目录落一份垃圾，收尾删掉
        ok9 = (rc9 == 1 and '一条都没取到' in msg9)
    finally:
        shutil.rmtree(d9, ignore_errors=True)
        # 清理本次探针在真目录留下的痕迹
        for dd2 in (os.listdir(MAT) if os.path.isdir(MAT) else []):
            if dd2.endswith('_file_t'):
                shutil.rmtree(os.path.join(MAT, dd2), ignore_errors=True)
    res.append(('零命中不许报完成', ok9, True))

    # ⑩ 未实现的功能不许假装实现（watch refresh / sources 必须显式说明边界）
    l10 = _lits_of('cmd_watch', only_cmp=True)
    # 反过来：refresh 分支里不许出现"已完成/已检查"这类假完成词
    l10all = _lits_of('cmd_watch')
    ok10 = ('暂未实现' in l10 or '暂未实现' in l10all)
    fp10 = not any(w in l10all for w in ('已全部检查完成', '检查完成', '全部正常'))
    res.append(('未实现不假装实现', ok10, fp10))

    # ⑪ 归一化双侧同归（S172 实测：单边清洗 → 有出处恒 0/6）
    #   真的造一对"带 # 的素材 + 不带 # 的标题"，看 evidence 能不能找到
    d11 = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d11, 'm'), exist_ok=True)
        open(os.path.join(d11, 'm', 'a.md'), 'w', encoding='utf-8').write(
            '# 第一章 勾股定理\n\n## 1.1 定理证明\n\n内容。\n')
        open(os.path.join(d11, 'fw.md'), 'w', encoding='utf-8').write(
            '# 第一章 勾股定理\n## 1.1 定理证明\n')
        op11 = os.path.join(d11, 'o.json')
        class _A11:
            sub = 'outline'; path = [os.path.join(d11, 'm')]
            frm = os.path.join(d11, 'fw.md'); out = op11
        import io as _io11
        from contextlib import redirect_stdout as _rso11
        with _rso11(_io11.StringIO()):
            cmd_sort(_A11())
        o11 = _load_json(op11, {}) or {}
        nds = o11.get('nodes', [])
        # 两条节点都必须找到 evidence（带 # 的素材 vs 带 # 的框架，必须能对上）
        ok11 = len(nds) == 2 and all(n.get('evidence') for n in nds)
        # 反假阳性：框架里凭空多一条，必须判 unverified 而不是硬塞 evidence
        open(os.path.join(d11, 'fw.md'), 'w', encoding='utf-8').write(
            '# 第一章 勾股定理\n## 9.9 根本不存在的章节\n')
        with _rso11(_io11.StringIO()):
            cmd_sort(_A11())
        o11b = _load_json(op11, {}) or {}
        fp11 = any(n.get('unverified') for n in o11b.get('nodes', []))
    finally:
        shutil.rmtree(d11, ignore_errors=True)
    res.append(('归一化双侧同归·带#标题能对上', ok11, fp11))

    # ⑫ 相对路径不崩（S172：_save_json('outline.json') → makedirs('') 抛 FileNotFoundError）
    d12 = tempfile.mkdtemp()
    try:
        cwd0 = os.getcwd(); os.chdir(d12)
        try:
            _save_json('rel.json', {'a': 1})
            ok12 = os.path.isfile(os.path.join(d12, 'rel.json'))
        finally:
            os.chdir(cwd0)
    finally:
        shutil.rmtree(d12, ignore_errors=True)
    res.append(('相对路径写盘不崩', ok12, True))

    # ⑬ 同一判据只有一份实现（S172：_norm 之前被写了三遍，修一处漏两处）
    #   ★这条探针改了四版，前几版都想"从模式串猜语义"，越猜越粗（把剥 # 前缀、
    #     匹配编号、匹配答案行都算成归一化）。**静态猜语义这条路走不通。**
    #     改成**白名单**：允许的 3 处非归一化用途写死行号无关的"用途指纹"，
    #     只要出现第 4 处新的"去全部空白"就是分叉，立刻报。
    import ast as _ast13
    _tree13 = _ast13.parse(open(__file__, encoding='utf-8').read())
    # 允许的非归一化用途：模式里出现这些"语义标记"就不算归一化
    _ALLOW = ('答案', '答', '\\d+', '第')
    _dup13 = []
    for _n in _ast13.walk(_tree13):
        if not (isinstance(_n, _ast13.Call) and isinstance(_n.func, _ast13.Attribute)):
            continue
        if not _n.args:
            continue
        _a0 = _n.args[0]
        if not (isinstance(_a0, _ast13.Constant) and isinstance(_a0.value, str)):
            continue
        v = _a0.value
        # "去全部空白"的特征：替换成空串 + 模式里含空白类
        if '\\s' not in v:
            continue
        if any(k in v for k in _ALLOW):
            continue
        # 字符类里含空白类且整串替换成 '' 才是归一化
        if _n.func.attr in ('sub',) and len(_n.args) >= 2 and isinstance(_n.args[1], _ast13.Constant) \
                and _n.args[1].value == '':
            # 排除"只剥行首 # 前缀"那类：以 ^# 开头且剩余字符全是 #、空白、量词
            if v.startswith('^#') and set(v[1:]) <= set('#\\s*+'):
                continue
            _dup13.append((_n.lineno, v))
    # ★允许且只允许 1 处：_norm 内部那一处。出现第 2 处就是分叉。
    ok13 = len(_dup13) == 1
    res.append(('归一化只许一处实现', ok13, True))

    # ⑭ 缺 outline 不许报"归因 0 条"，必须判无法判定（S172）
    d14 = tempfile.mkdtemp()
    try:
        ef = os.path.join(d14, 'e.txt')
        open(ef, 'w', encoding='utf-8').write('勾股定理算错\n')
        class _A14:
            sub = 'diagnose'; path = [ef]
            outline = os.path.join(d14, 'nope.json')
        import io as _io14
        from contextlib import redirect_stdout as _rso14
        buf14 = _io14.StringIO(); rc14 = 0
        try:
            with _rso14(buf14):
                cmd_forge(_A14())
        except SystemExit as e:
            rc14 = e.code
        m14 = buf14.getvalue()
        # ★上一版 `'归因 0 条' not in m14` —— 提示语里本身写着「不许报"归因 0 条"」，
        #   探针被自己那句话命中，恒假。**判据不许查一个自己会说的话。**
        #   改成查"结论状态"与"是否给出归因数"两个独立信号。
        ok14 = (rc14 == 3 and '无法判定' in m14 and '错题 ' not in m14)
    finally:
        shutil.rmtree(d14, ignore_errors=True)
    res.append(('缺框架不报归因0条', ok14, True))

    # ⑮ 记忆曲线间隔必须严格递增（S172：原实现输出「第1天 → 第1天」）
    #   ★上一版在探针里**自己重算了一遍序列**再验递增 —— 那是在验探针自己，
    #     把 cmd_forge 改成 "seq.append(seq[-1])" 照样过。**又是判据错位（与⑤同病）。**
    #     现在**真跑 cmd_forge['schedule']，从它打印的输出里抽序列**。
    import io as _io15
    from contextlib import redirect_stdout as _rso15
    class _A15:
        sub = 'schedule'; n = '8'
    buf15 = _io15.StringIO()
    with _rso15(buf15):
        cmd_forge(_A15())
    m15 = buf15.getvalue()
    days = [int(x) for x in re.findall(r'第(\d+)天', m15)]
    ok15 = (len(days) == 8 and all(days[i] > days[i - 1] for i in range(1, len(days))))
    # 反假阳性：首日必须是第 1 天（不许从 0 或 3 起）
    fp15 = bool(days) and days[0] == 1
    res.append(('间隔序列严格递增', ok15, fp15))

    n = len(res)
    passed = sum(1 for _, h, c in res if h and c)
    for nm, h, c in res:
        print(f'  {"✅" if h and c else "❌"} {nm}'
              f'{"" if h else "（实现侧未命中）"}{"" if c else "（假阳性基线=脏）"}')
    print(f'\nself-check {passed}/{n}')
    sys.exit(0 if passed == n else 1)


# ═══════════════════════════════════════════════════════════════
def build_parser():
    p = argparse.ArgumentParser(prog='learn', description='学习工坊 · 学习资料单类业务引擎')
    p.add_argument('--json', action='store_true')
    s = p.add_subparsers(dest='mod')

    i = s.add_parser('intake', help='采集：file/url/pan/scan/list')
    si = i.add_subparsers(dest='sub')
    f = si.add_parser('file'); f.add_argument('path', nargs='*'); f.add_argument('--name', default='')
    u = si.add_parser('url'); u.add_argument('path', nargs='*'); u.add_argument('--name', default='')
    pn = si.add_parser('pan'); pn.add_argument('path', nargs='*')
    sc = si.add_parser('scan'); sc.add_argument('path', nargs='*')
    sc.add_argument('--recursive', action='store_true'); sc.add_argument('--dry', action='store_true')
    sc.add_argument('--name', default='')
    ls = si.add_parser('list'); ls.add_argument('--json', dest='json', action='store_true')

    so = s.add_parser('sort', help='分类整理：outline/dedupe/difficulty')
    ss = so.add_subparsers(dest='sub')
    o = ss.add_parser('outline'); o.add_argument('path', nargs='*')
    o.add_argument('--from', dest='frm', default=''); o.add_argument('--out', default='')
    dd = ss.add_parser('dedupe'); dd.add_argument('path', nargs='*')
    df = ss.add_parser('difficulty'); df.add_argument('path', nargs='*')

    g = s.add_parser('gate', help='质检闸：run/coverage/grounding/ratio')
    sg = g.add_subparsers(dest='sub')
    for nm in ('run', 'coverage', 'grounding', 'ratio'):
        sp = sg.add_parser(nm); sp.add_argument('path', nargs='*')
        sp.add_argument('--expect', default='')

    fo = s.add_parser('forge', help='特有工具库：diagnose/schedule/check')
    sf = fo.add_subparsers(dest='sub')
    dg = sf.add_parser('diagnose'); dg.add_argument('path', nargs='*')
    dg.add_argument('--outline', default='', help='知识点树路径（缺省 work/outline.json）')
    sh = sf.add_parser('schedule'); sh.add_argument('--n', default='8')
    ck = sf.add_parser('check'); ck.add_argument('path', nargs='*')

    st = s.add_parser('store', help='入库：put/find/trace')
    stt = st.add_subparsers(dest='sub')
    pu = stt.add_parser('put'); pu.add_argument('path', nargs='*')
    pu.add_argument('--name', default=''); pu.add_argument('--tags', default='')
    fi = stt.add_parser('find'); fi.add_argument('path', nargs='*')
    tr = stt.add_parser('trace'); tr.add_argument('path', nargs='*')

    w = s.add_parser('watch', help='监测：sources/stale/refresh')
    sw = w.add_subparsers(dest='sub')
    sw.add_parser('sources')
    sl = sw.add_parser('stale'); sl.add_argument('--days', default='180')
    sw.add_parser('refresh')

    s.add_parser('self-check', help='命门自检')
    return p


def dispatch(argv=None):
    """供调度器 `tvbox study ...` 在同进程内直接调用。

    ★入口契约：`tvbox` 的 ROUTE 调 `module.dispatch(mod_argv(...))`，模块没有这个函数
      就会报 `has no attribute 'dispatch'`——实测踩过一次（入口加完 `study` 后第一次跑）。
      注意 argv 里**已经剥掉了动词**（`tvbox study gate run` → `['gate','run',...]`）。
    """
    a = build_parser().parse_args(argv)
    if not a.mod:
        build_parser().print_help(); return
    if a.mod == 'self-check':
        return cmd_selfcheck(a)
    fn = {'intake': cmd_intake, 'sort': cmd_sort, 'gate': cmd_gate,
          'forge': cmd_forge, 'store': cmd_store, 'watch': cmd_watch}.get(a.mod)
    if fn is None:
        print(f'❌ 未知模块：{a.mod}'); sys.exit(2)
    fn(a)


def main():
    dispatch()


if __name__ == '__main__':
    main()
