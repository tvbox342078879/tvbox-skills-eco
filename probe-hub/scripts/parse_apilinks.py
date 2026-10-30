#!/usr/bin/env python3
"""parse_apilinks.py —— 把 TVBox 聚合分发站的接口清单解析为 monitor 种子 JSON。

输入：tvboxapilinks.txt（每行 `组名, url1, url2, ...`，每组 1~6 线）
输出：seed_monitor_666.json（[{name, urls, kind, seed_baseline, source_file,
      extracted_sha12, extracted_at, 在线状态, cadence}]）

★666.zip 实证驱动：作者「沫离」聚合分发站把全行业 55 组主流接口浓缩进一个
  txt。直接拿它批量登记进 monitor 种子库，比从 GitHub 翻找准得多。本脚本生成
  「离线种子基线」——记录真实接口 URL 与来源指纹，公开 URL 待补后再转在线轮询。
"""
import os, sys, json, hashlib, datetime


def _sha12(path):
    h = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    return h[:12]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else '/tmp/pak_out/tvboxapilinks.txt'
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'harvest_candidates', 'seed_monitor_666.json')
    if not os.path.exists(src):
        print(f'❌ 源文件不存在：{src}'); sys.exit(2)
    sha12 = _sha12(src)
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    items = []
    with open(src, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            parts = [p.strip() for p in ln.split(',') if p.strip()]
            if len(parts) < 2:
                continue
            name = parts[0]
            urls = parts[1:]
            items.append({
                'name': name,
                'urls': urls,
                'kind': 'tvbox-iface',
                'seed_baseline': True,
                'source_file': os.path.basename(src),
                'extracted_sha12': sha12,
                'extracted_at': now,
                '在线状态': '离线种子（待启用）',
                'cadence': '每周',
            })
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({'items': items}, open(out, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    lines = sum(1 for _ in open(src, encoding='utf-8') if _.strip())
    print(f'✅ 解析 {os.path.basename(src)}：{lines} 行 → {len(items)} 组接口')
    print(f'   来源指纹 sha12={sha12}  输出 → {out}')
    # 统计各线数分布
    from collections import Counter
    dist = Counter(len(it['urls']) for it in items)
    print('   各线数分布：' + '，'.join(f'{k}线×{v}' for k, v in sorted(dist.items())))


if __name__ == '__main__':
    main()
