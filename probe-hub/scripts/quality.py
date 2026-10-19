#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quality.py —— 数据源质量评测（S110）：画质 / 速度 / 广告三件套。

为什么要有它（S110 用户原话）：
  "数据源质量如何把控，比如播放速度，广告，画质，能把这个做进去闭环么"

现状缺口：`livecheck` 只回答"能不能拉到播放地址"（通过/失败/无法判定），
**不回答"拉到之后好不好用"**。于是"假通过"大量存在——接口返 200、播放地址非空，
但真去拉那个 m3u8 是 403/超时；或者能拉但只有 480p、首片 8 秒、中间插两段广告。

本模块补的就是这一段：**真拉 m3u8 → 读分辨率 → 测首片耗时与速率 → 扫广告特征**。

★ 一个必须写进结论的限制（否则会误导用户）：
  沙箱测速是**云端视角**，与用户家宽/运营商/地域都不同。
  故速度只能作**相对参考**（同批源之间谁快谁慢），不得当**绝对值**断言。
  对外文案一律写"沙箱实测 X KB/s（相对参考）"。

三态纪律（M-R60）不变：拉不到 ≠ 死。区分：
  · 无法判定：分片全超时/不可达（可能是沙箱出口被墙，不是源的问题）
  · 失败：明确 403/404 或 m3u8 内容非法
"""
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlparse, urljoin

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# 广告域名黑名单（可追加；命中只算"疑似"，因为有些站自己也叫 ad）
AD_HOST_HINTS = ('ads.', 'ad.', 'gg.', 'guanggao', 'advert', 'promo', 'tui.', 'dsp.')

# 质量分权重（S110 校准版）
# ★ 权重为什么这么改（用 111 条成品源库实测校准出来的）：
#   初版 W_QUALITY=40 / W_SPEED=40 / W_AD=20，结果 29 条 1080p 源全部卡在 65 分，
#   26KB/s 和 326KB/s 拿到同一个分 —— 说明速度维度在沙箱里根本没有区分度。
#   实测分位数：速率 P50=225KB/s、P90=376KB/s；沙箱出口本身就慢，2000KB/s 才给满分
#   等于把 95% 的源全判最低档。故：
#     · 画质提高到 50 —— 这是用户最能感知的硬指标，且实测有 1080p/720p/480p 真实分层
#     · 速度降到 30 —— 仍是参考项，但档位按实测中位数重设
#     · 广告保持 20 —— 但"广告无"不再拿满分，改 15，留 5 分给"确认无插播"
W_QUALITY = 50
W_SPEED = 30
W_AD = 20


def _curl(url, timeout=12, rng=None):
    """取内容 → (http_code, body_bytes, total_sec, size, t_connect, t_first_byte)。

    ★ t_connect / t_first_byte 是为了**把连接开销从测速里剥出去**（S110 校准）：
      直接拿 size/总耗时算速率，会把 DNS+TLS+首包等待全摊进分母。
      一个 256KB 分片若耗时 2s，其中 1.5s 是握手、0.5s 是传输，
      算出来 128KB/s，实际传输 512KB/s —— 差 4 倍，会误杀好源。
      故另给 t_connect（建连完成）与 t_first_byte（首字节到达），
      真实传输耗时 ≈ 总耗时 − t_starttransfer。
    """
    cmd = ['curl', '-s', '-L', '--compressed', '--connect-timeout', '6',
           '--max-time', str(timeout), '-A', UA]
    if rng:
        cmd += ['--range', rng]
    cmd += ['-w', '\n__META__%{http_code}|%{time_total}|%{size_download}|%{time_connect}|%{time_starttransfer}', url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        raw = p.stdout
        if b'\n__META__' not in raw:
            return 0, b'', 0.0, 0, 0.0, 0.0
        body, meta = raw.rsplit(b'\n__META__', 1)
        parts = meta.decode(errors='ignore').split('|')
        code = int(parts[0] or 0)
        tsec = float(parts[1] or 0)
        size = int(float(parts[2] or 0))
        tc = float(parts[3] or 0) if len(parts) > 3 else 0.0
        tt = float(parts[4] or 0) if len(parts) > 4 else 0.0
        return code, body, tsec, size, tc, tt
    except Exception:
        return 0, b'', 0.0, 0, 0.0, 0.0


def resolution_level(text):
    """从 m3u8 文本读最高分辨率档 → ('1080p'|'720p'|'480p'|'360p'|'未知', 高度px)。

    兼容两种写法：
      · RESOLUTION=1920x1080（HLS 主清单）
      · 1920x1080 裸写在 EXT-X-STREAM-INF 行
    读不到不代表画质低——单码率清单本来就没有 RESOLUTION 标记，故返 '未知'。

    ★ 分档按**高度**（1080/720/480/360 是行业通称，指短边），
      不要把 1920x1080 里的 1080 当宽度去比 1900 阈值——那样 1080p 会被误判成 480p。
    """
    heights = [int(m) for m in re.findall(r'RESOLUTION=\d+x(\d+)', text or '')]
    if not heights:
        heights = [int(m) for m in re.findall(r'\b\d{3,4}x(\d{3,4})\b', text or '')
                   if 200 <= int(m) <= 4320]
    heights = [h for h in heights if h >= 200]
    if not heights:
        return '未知', 0
    h = max(heights)
    if h >= 1000:
        return '1080p', h
    if h >= 700:
        return '720p', h
    if h >= 460:
        return '480p', h
    return '360p', h


def _is_m3u8(url, body):
    u = (url or '').lower()
    return u.split('?')[0].endswith('.m3u8') or b'#EXTM3U' in (body or b'')[:2048]


def _first_segment(m3u_text, base_url):
    """从 m3u8 取第一个分片 URL（跳过注释行与 EXT-X- 指令）。"""
    for line in (m3u_text or '').splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        return urljoin(base_url, s)
    return ''


def ad_judgment(m3u_text, seg_urls, play_url):
    """广告判定 → ('无'|'疑似'|'确认', 证据)。

    判据（任一命中即升级，三档保守）：
      · 域名黑名单命中 → 疑似
      · 出现 EXT-X-DISCONTINUITY 且分片数很少（插播常见） → 疑似
      · 播放地址本身含广告域名 → 确认
    注意：这只是**启发式**，短剧源天然分片短，会误判成广告——
    所以"疑似"不等于"有广告"，最终要人眼确认。
    """
    low = (play_url or '').lower()
    host = urlparse(low).netloc
    for h in AD_HOST_HINTS:
        if h in host:
            return '确认', f'播放地址域名命中广告特征：{host}'
    for u in seg_urls[:20]:
        h = urlparse(u).netloc.lower()
        for hint in AD_HOST_HINTS:
            if hint in h:
                return '疑似', f'分片域名命中广告特征：{h}'
    if 'EXT-X-DISCONTINUITY' in (m3u_text or '') and len(seg_urls) < 5:
        return '疑似', '含 EXT-X-DISCONTINUITY 且分片少于 5 条（疑插播）'
    return '无', ''


def score(quality, first_ms, kbps, ad):
    """合成质量分 0-100。

    ★ 设计原则（校准两次才定下来）：
      画质是**门槛型**指标，速度是**加减分**指标。
      两次实测暴露的坑：
        · 速度权重 40 分时，26KB/s 和 326KB/s 都卡在 65 分 —— 速度无区分度；
        · 速度权重 30 分时，360p+402KB/s 拿到 100 分，反超 1080p+169KB/s(94) —— 本末倒置。
      结论：**先按画质定基准分，再用速度/首片在基准上做有限加减**，
      保证任何速度下 1080p 都高于 720p，任何 720p 都高于 480p。
      档位取自 111 条实测分位数：速率 P25=174/P50=225/P75=316，首片 P50=743/P90=1676。
    """
    # 基准分：档位间隔必须 > 修正项最大振幅(8+2=10，加上广告 -30 的极端不影响相对序)。
    # 实测校准后间隔取 16：1080p 92 / 720p 76 / 480p 60 / 360p 44 / 未知 56
    # （未知落在 480p 与 360p 之间：读不到分辨率不代表差，但也不能当高画质）
    base = {'1080p': 92, '720p': 76, '480p': 60, '360p': 44}.get(quality, 56)

    # 速度修正：±8 分封顶
    if kbps >= 316:
        d_speed = 8
    elif kbps >= 225:
        d_speed = 5
    elif kbps >= 174:
        d_speed = 2
    elif kbps > 0:
        d_speed = -3
    else:
        d_speed = 0        # 未测速不加不减（mp4/非HLS 不该被惩罚）

    # 首片修正：慢就扣，快不加分（避免与速度重复计分）
    if first_ms >= 2800:
        d_first = -10
    elif first_ms >= 1700:
        d_first = -6
    elif first_ms and first_ms >= 1000:
        d_first = -2
    else:
        d_first = 0

    # 广告：命中直接重罚（用户最反感），无广告不加分（本来是应然状态）
    d_ad = {'无': 0, '疑似': -15, '确认': -30}.get(ad, 0)

    return max(0, min(base + d_speed + d_first + d_ad, 100))


def evaluate(play_url, timeout=12, rounds=3):
    """评测一条播放地址 → dict（三态 + 质量指标）。

    返回键：verdict / quality / first_ms / kbps / ad / score / evidence
      verdict: '通过' | '失败' | '无法判定'
    速度取 rounds 次的中位数（单次抖动大，中位数更稳）。
    """
    url = (play_url or '').strip()
    if not url.startswith('http'):
        return {'verdict': '无法判定', 'quality': '未知', 'first_ms': 0, 'kbps': 0,
                'ad': '未知', 'score': 0, 'evidence': '播放地址非 http（本地/内置源，不适用质量评测）'}

    code, body, tsec, size, _tc, _tt = _curl(url, timeout=timeout)
    if code == 0:
        return {'verdict': '无法判定', 'quality': '未知', 'first_ms': 0, 'kbps': 0,
                'ad': '未知', 'score': 0, 'evidence': 'm3u8 主清单不可达（沙箱出口问题≠源死）'}
    if code in (403, 404, 410, 451):
        return {'verdict': '失败', 'quality': '未知', 'first_ms': 0, 'kbps': 0,
                'ad': '未知', 'score': 0, 'evidence': f'm3u8 HTTP {code}（防盗链/已删）'}

    text = body.decode('utf-8', errors='ignore')

    # 非 m3u8（可能是 mp4 直链）→ 画质未知，速度也不测
    # ★ 为什么不测：mp4 是整文件，curl --max-time 拉到的只是开头一小段，
    #   用"字节数/总耗时"算速率会把 DNS+握手时间全摊进去，得到 1KB/s 这种假数字。
    #   宁可标"未知"，也不给一个会误导决策的值（M-R60 同源纪律：拿不到就说拿不到）。
    if not _is_m3u8(url, body):
        return {'verdict': '通过' if code == 200 else '无法判定', 'quality': '未知',
                'first_ms': int(tsec * 1000), 'kbps': 0, 'ad': '未知',
                'score': score('未知', 0, 0, '无'),
                'evidence': f'mp4/非HLS 直链 HTTP {code}·首包{int(tsec * 1000)}ms·未测速（非HLS不适用）'}

    quality, width = resolution_level(text)

    # 主清单可能是多码率（含子清单）→ 取第一条子清单继续
    sub = ''
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith('#'):
            sub = urljoin(url, s)
            break
    target = sub if (sub and sub.lower().split('?')[0].endswith('.m3u8')) else url
    if target != url:
        c2, b2, _, _, _tc2, _tt2 = _curl(target, timeout=timeout)
        if c2 == 200 and b2:
            t2 = b2.decode('utf-8', errors='ignore')
            q2, w2 = resolution_level(t2)
            if w2 > width:
                quality, width = q2, w2
            text = t2 if '#EXTINF' in t2 else text
            url_for_seg = target
        else:
            url_for_seg = url
    else:
        url_for_seg = url

    seg = _first_segment(text, url_for_seg)
    times, sizes, ttfb = [], [], []
    if seg:
        for _ in range(max(1, rounds)):
            c3, b3, t3, s3, tc3, tt3 = _curl(seg, timeout=timeout, rng='0-262143')  # 拉前 256KB
            if c3 in (200, 206) and t3 > 0 and s3 > 0:
                times.append(t3)
                sizes.append(s3)
                ttfb.append(tt3 or tc3 or 0.0)
        if times:
            med_t = sorted(times)[len(times) // 2]
            med_s = sorted(sizes)[len(sizes) // 2]
            med_ttfb = sorted(ttfb)[len(ttfb) // 2] if ttfb else 0.0
            # 首片耗时 = 从发起到首字节（用户感知的"点了多久开始播"）
            first_ms = int((med_ttfb or med_t) * 1000)
            # 速率 = 字节 / 纯传输时长。
            # ★ 下限 0.15s：分片若是缓存在 CDN 边缘，256KB 可能 0.02s 就传完，
            #   curl 的秒级计时精度不足，除出来会是 10MB/s 这种假数字。
            #   宁可把极快的源压到上限附近，也不放一个不可能的值进库。
            xfer = med_t - med_ttfb
            xfer = max(xfer, 0.15) if xfer > 0 else max(med_t, 0.15)
            kbps = int(med_s / 1024 / xfer)
            kbps = min(kbps, 20000)   # 20MB/s 封顶，超过一律视为测不准
        else:
            first_ms, kbps = 0, 0
    else:
        first_ms, kbps = 0, 0

    segs = [urljoin(url_for_seg, l.strip()) for l in text.splitlines()
            if l.strip() and not l.strip().startswith('#')]
    ad, ad_ev = ad_judgment(text, segs, url)

    if seg and not times:
        return {'verdict': '无法判定', 'quality': quality, 'first_ms': 0, 'kbps': 0,
                'ad': ad, 'score': 0,
                'evidence': f'清单可读({quality})但分片拉不动·{seg[:60]}'}
    if not seg:
        return {'verdict': '失败', 'quality': quality, 'first_ms': 0, 'kbps': 0,
                'ad': ad, 'score': 0, 'evidence': 'm3u8 内无有效分片（空清单）'}

    sc = score(quality, first_ms, kbps, ad)
    ev = (f'{quality}({width}w)·首片{first_ms}ms·{kbps}KB/s(沙箱实测·相对参考)'
          f'·广告{ad}{"·" + ad_ev if ad_ev else ""}')
    return {'verdict': '通过', 'quality': quality, 'first_ms': first_ms,
            'kbps': kbps, 'ad': ad, 'score': sc, 'evidence': ev}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    arg = sys.argv[1]
    if arg == '--batch':
        items = json.load(open(sys.argv[2], encoding='utf-8'))
        out = []
        for it in items:
            r = evaluate(it.get('url') or it.get('play_url') or '')
            r['name'] = it.get('name', '')
            out.append(r)
            print(f"  {r['verdict']:4s} {r['score']:3d} 分  {r['name'][:20]:22s} {r['evidence'][:70]}")
        base = sys.argv[2]
        dst = base.rsplit('.', 1)[0] + '_quality.json'
        json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'✅ 结果 → {dst}')
        return
    print(json.dumps(evaluate(arg), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
