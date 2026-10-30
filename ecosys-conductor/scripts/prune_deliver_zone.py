import os, re, sys, shutil, datetime

ZONE = '/workspace/产出区_1016'

def mmdd_of(name):
    m = re.findall(r'_(\d{4})_', name)
    return m[0] if m else None

def check_links(zone, moved_names):
    broken = []
    for root, _, files in os.walk(zone):
        if '归档_' in root or '_bak_' in root or '__pycache__' in root:
            continue
        for fn in files:
            if fn in moved_names:
                continue
            p = os.path.join(root, fn)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding='utf-8', errors='ignore') as fh:
                    txt = fh.read()
            except Exception:
                continue
            for m in moved_names:
                if m in txt:
                    broken.append((fn, m))
                    break
    return broken

def gen_index(keep_list):
    path = os.path.join(ZONE, 'INDEX_产物区_倒序.md')
    lines = ['# 产物区导航（最新在顶·倒序）', '',
             '> 自动生成。产物区只保留最新 10 个交付物，其余整体归档于 `归档_产物_*` 子目录。',
             '> 本索引倒序列出当前保留项，最新在顶部。', '']
    for i, f in enumerate(keep_list, 1):
        lines.append(f'{i}. {f}')
    lines.append('')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))
    return path

def flatten_old_archives(go):
    # S1031 真根治：把历史「归档_产物_<ts>」时间戳目录拍平进固定「归档_产物」，消除套娃。
    # 旧版 prune 每轮把 excluded 里的历史归档目录整体 move 进固定目录，嵌套被原样复制 → 越积越深。
    ARCHIVE = '归档_产物'
    arch_dir = os.path.join(ZONE, ARCHIVE)
    os.makedirs(arch_dir, exist_ok=True)
    # 递归收集全部 归档_产物_<ts> 时间戳目录（含已沉入固定归档内部的嵌套层），防套娃复发
    old = []
    for root, dirs, _ in os.walk(ZONE):
        for d in list(dirs):
            if d == ARCHIVE:
                continue
            if d.startswith('归档_产物_') and os.path.isdir(os.path.join(root, d)):
                old.append(os.path.join(root, d))
    if not old:
        return
    print(f'[flatten] 发现 {len(old)} 个旧时间戳归档目录，准备拍平')
    if not go:
        print('[DRY] 未执行，加 --go 执行。')
        return
    for d in old:
        src = d
        for root, _, files in os.walk(src):
            for fn in files:
                sp = os.path.join(root, fn)
                dp = os.path.join(arch_dir, fn)
                if os.path.exists(dp):
                    base, ext = os.path.splitext(fn)
                    dp = os.path.join(arch_dir, f'{base}__{os.path.basename(d)}{ext}')
                shutil.move(sp, dp)
        shutil.rmtree(src)
    print(f'[flatten] 已拍平 {len(old)} 个旧目录 → {arch_dir}（固定归档，不再套娃）')

def run_by_mtime(keep, go):
    ARCHIVE = '归档_产物'  # 固定单一归档目录（S1031 根治）
    flatten_old_archives(go)  # 先拍平历史时间戳归档目录，消除套娃
    all_items = os.listdir(ZONE)
    excluded = [f for f in all_items if f == '__pycache__' or f.startswith('归档') or f.startswith('_bak_') or f.startswith('INDEX_')]
    cand = [f for f in all_items if f not in excluded]
    cand.sort(key=lambda f: os.path.getmtime(os.path.join(ZONE, f)), reverse=True)
    keep_list = cand[:keep]
    archive_list = cand[keep:]
    print('=== by-mtime 保留最新 %d 个（最新在顶）===' % keep)
    for i, f in enumerate(keep_list, 1):
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(ZONE, f)))
        print(f'  {i:>2}. {f} ({mt.strftime("%m-%d %H:%M")})')
    print(f'将归档: {len(archive_list)} 个（含 {len(excluded)} 个归档残留）')
    if not go:
        print('[DRY] 未移动。确认后加 --go 执行。')
        return
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    arch_dir = os.path.join(ZONE, ARCHIVE)
    os.makedirs(arch_dir, exist_ok=True)
    rollback = f'/tmp/prune_rollback_{ts}'
    shutil.copytree(ZONE, rollback)
    moved = [f for f in archive_list if f != ARCHIVE]
    for f in moved:
        shutil.move(os.path.join(ZONE, f), os.path.join(arch_dir, f))
    moved_files = [m for m in moved if os.path.isfile(os.path.join(ZONE, m))]
    broken = check_links(ZONE, set(moved_files))
    if broken:
        print(f'⚠️ {len(broken)} 处断链，按 M-R139 回滚')
        for b in broken[:10]:
            print('   ', b)
        shutil.rmtree(arch_dir)
        for f in moved:
            s = os.path.join(rollback, f)
            if os.path.exists(s):
                shutil.move(s, os.path.join(ZONE, f))
        shutil.rmtree(rollback)
        print('✅ 已回滚，产物区恢复原状')
        return
    shutil.rmtree(rollback)
    idx = gen_index(keep_list)
    print(f'✅ 归档 {len(moved)} 个 → {arch_dir}')
    print(f'✅ 保留 {len(keep_list)} 个，零断链校验通过')
    print(f'✅ 倒序索引已生成: {idx}（M-R139 回滚副本已删）')

def run_by_date(keep, go):
    flatten_old_archives(go)  # 防递归套娃复发（与 by-mtime 同闸）
    items = sorted(os.listdir(ZONE))
    dated, undated = {}, []
    def valid(mm):
        v = int(mm); mo, da = v // 100, v % 100
        return 101 <= v <= 1231 and 1 <= mo <= 12 and 1 <= da <= 31
    for f in items:
        mm = mmdd_of(f)
        if mm and valid(mm):
            dated.setdefault(mm, []).append(f)
        else:
            undated.append(f)
    recent = sorted(dated.keys(), key=lambda x: int(x))[-keep:]
    keep_set = set()
    for k in recent:
        keep_set.update(dated[k])
    archive_dated = [f for k in dated if k not in recent for f in dated[k]]
    print(f'保留最近 {keep} 段: {recent}，归档 {len(archive_dated)} 个，散件不动 {len(undated)} 个')
    if not go:
        print('[DRY] 未移动。')
        return
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    arch_dir = os.path.join(ZONE, '归档_产物')
    os.makedirs(arch_dir, exist_ok=True)
    rollback = f'/tmp/prune_rollback_{ts}'
    shutil.copytree(ZONE, rollback)
    moved = archive_dated
    for f in moved:
        shutil.move(os.path.join(ZONE, f), os.path.join(arch_dir, f))
    broken = check_links(ZONE, set(moved))
    if broken:
        print(f'⚠️ {len(broken)} 断链，回滚')
        shutil.rmtree(arch_dir)
        for f in moved:
            s = os.path.join(rollback, f)
            if os.path.exists(s):
                shutil.move(s, os.path.join(ZONE, f))
        shutil.rmtree(rollback)
        return
    shutil.rmtree(rollback)
    print(f'✅ 归档 {len(moved)} 个，保留 {len(keep_set)} 个，零断链')

def main():
    dry = '--dry' in sys.argv
    go = '--go' in sys.argv
    keep = 10
    for i, a in enumerate(sys.argv):
        if a == '--keep':
            keep = int(sys.argv[i + 1])
    if '--by-mtime' in sys.argv:
        run_by_mtime(keep, go and not dry)
    else:
        run_by_date(keep, go and not dry)

if __name__ == '__main__':
    main()
