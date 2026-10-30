#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
无文本层扫描件 PDF  ->  本地离线 OCR  ->  带页码的纯文本。

设计前提：**内容不出本机**。全部依赖本地 ONNX 推理，不调用任何在线服务，
因此可以直接处理合同、标书、财务资料等敏感件，无需额外授权。

用法
----
    # 1) 先探测：是不是扫描件？混合件？
    python scan_ocr.py <pdf> --probe

    # 2) 全量转换（自动模式：有文本层的页直接抽text，没文本的才OCR）
    python scan_ocr.py <pdf> -o 输出目录

    # 3) 先试 3 页看效果，确认 OCR 质量后再全跑
    python scan_ocr.py <pdf> --pages 1-5 -o out

    # 4) 强制全部走 OCR（混合件想统一口径时用）
    python scan_ocr.py <pdf> --force-ocr -o out

    # 5) 多进程加速（8 核机器约快 4-6 倍）
    python scan_ocr.py <pdf> --workers 8 -o out

依赖
----
    pip install pymupdf rapidocr-onnxruntime
"""

import argparse
import os
import sys
import time
from multiprocessing import Pool

OUT_EXT = ".png"


# ---------------------------------------------------------------- 引擎

def _get_renderer(dpi):
    def render(args):
        """渲染单页为 PNG。放在模块顶层以便 Windows spawn 可 pickle。"""
        pdf_path, idx, out_png, dpi = args
        import pymupdf
        doc = pymupdf.open(pdf_path)
        try:
            doc[idx].get_pixmap(dpi=dpi).save(out_png)
        finally:
            doc.close()
        return idx, out_png
    return render


_RENDER_FN = _get_renderer(200)


def _render(args):
    # Windows spawn 需要顶层函数
    pdf_path, idx, out_png, dpi = args
    import pymupdf
    doc = pymupdf.open(pdf_path)
    try:
        doc[idx].get_pixmap(dpi=dpi).save(out_png)
    finally:
        doc.close()
    return idx, out_png


# 每进程缓存一个 OCR 实例，避免反复加载模型
_OCR = {}


def _ocr_worker(args):
    """OCR 单页，返回 (idx, [文本行...])。"""
    idx, png = args
    global _OCR
    if "engine" not in _OCR:
        from rapidocr_onnxruntime import RapidOCR
        _OCR["engine"] = RapidOCR()
    res, _ = _OCR["engine"](png)
    lines = [item[1] for item in res] if res else []
    return idx, lines


# ---------------------------------------------------------------- 主流程

def probe(pdf_path, min_text=20):
    """探测 PDF 结构：逐页统计文本量，判断扫描件 / 混合件 / 文本 PDF。"""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    n = doc.page_count
    rows = []
    for i in range(n):
        pg = doc[i]
        t = pg.get_text().strip()
        imgs = len(pg.get_images(full=True))
        rows.append((i + 1, len(t), imgs, pg.rotation))
    doc.close()

    total = sum(r[1] for r in rows)
    img_pages = sum(1 for r in rows if r[2] > 0)
    ocr_pages = [r[0] for r in rows if r[1] < min_text]
    hybrid = 0 < len(ocr_pages) < n

    print(f"文件       : {os.path.basename(pdf_path)}")
    print(f"页数       : {n}")
    print(f"全文文本量 : {total} 字")
    print(f"含图片页   : {img_pages} / {n}")
    print(f"无文本层页 : {len(ocr_pages)} / {n}")
    if rows and rows[0][3]:
        rots = sorted({r[3] for r in rows})
        print(f"页面旋转   : {rots} 度（get_pixmap 会自动摆正，无需手动处理）")
    print("-" * 56)

    if total == 0 and img_pages >= n:
        verdict = "【纯扫描件】全文无文本层 —— 必须走 OCR（或读图）"
    elif hybrid:
        head = ocr_pages[:12]
        more = " ..." if len(ocr_pages) > 12 else ""
        verdict = (f"【混合件】部分页有文本层 —— 建议用自动模式，"
                   f"仅对 {len(ocr_pages)} 页做 OCR\n"
                   f"           待 OCR 页：{head}{more}")
    elif total == 0:
        verdict = "【疑为空白/纯图】既无文本也无图片 —— 先确认文件是否损坏"
    else:
        verdict = "【有文本层的 PDF】无需 OCR，直接用 pymupdf / markitdown 提取即可"
    print(verdict)

    # 抽样预览：展示文本最多的 3 页各前 60 字
    rich = sorted(rows, key=lambda r: -r[1])[:3]
    for pno, cnt, _, _ in rich:
        if cnt > 0:
            print(f"  参考·第{pno}页（{cnt}字）摘要见 -o 提取结果")
    return rows, ocr_pages


def parse_pages(spec, n):
    """解析页范围：支持 all / 3 / 1-5 / 1,4,9 / 1-5,10"""
    if spec in ("all", "", None):
        return list(range(n))
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a) - 1, int(b)))
        else:
            out.add(int(part) - 1)
    return sorted(i for i in out if 0 <= i < n)


def run(pdf_path, outdir, dpi, pages_spec, force_ocr, workers,
        min_text, min_chars, keep_images, txt_name):
    import pymupdf

    os.makedirs(outdir, exist_ok=True)
    imgdir = os.path.join(outdir, "pages")
    os.makedirs(imgdir, exist_ok=True)

    doc = pymupdf.open(pdf_path)
    n = doc.page_count
    doc.close()

    targets = parse_pages(pages_spec, n)
    print(f"目标页 {len(targets)}/{n}  dpi={dpi}  workers={workers}", flush=True)

    # ---- 1. 渲染
    t0 = time.time()
    need_render = []
    for i in targets:
        png = os.path.join(imgdir, f"{i+1:04d}{OUT_EXT}")
        need_render.append((pdf_path, i, png, dpi))

    if workers > 1:
        try:
            with Pool(min(workers, len(need_render))) as pool:
                rendered = dict(pool.map(_render, need_render))
        except Exception as e:
            print(f"[warn] 多进程渲染失败({e})，退回串行", file=sys.stderr)
            rendered = dict(_render(a) for a in need_render)
    else:
        rendered = dict(_render(a) for a in need_render)
    print(f"渲染完成 {time.time()-t0:.0f}s", flush=True)

    # ---- 2. 决定每页用哪种方式取文本
    plan = []  # (idx, mode)
    for i in targets:
        mode = "ocr"
        if not force_ocr:
            import pymupdf as _f
            d = _f.open(pdf_path)
            try:
                if len(d[i].get_text().strip()) >= min_text:
                    mode = "text"
            finally:
                d.close()
        plan.append((i, mode))
    n_text = sum(1 for _, m in plan if m == "text")
    print(f"取文方式：直接抽取 {n_text} 页 / OCR {len(plan)-n_text} 页", flush=True)

    # ---- 3. 提取
    t1 = time.time()
    results = {}
    if workers > 1:
        jobs = [(i, rendered[i]) for i, m in plan if m == "ocr"]
        if jobs:
            try:
                with Pool(min(workers, len(jobs))) as pool:
                    for idx, lines in pool.imap_unordered(_ocr_worker, jobs):
                        results[idx] = lines
                        if len(results) % 10 == 0:
                            print(f"  OCR {len(results)}/{len(jobs)} 页 "
                                  f"累计{time.time()-t1:.0f}s", flush=True)
            except Exception as e:
                print(f"[warn] 多进程 OCR 失败({e})，退回串行", file=sys.stderr)
                results = {}
    if len(results) < sum(1 for _, m in plan if m == "ocr"):
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        for i, m in plan:
            if m == "ocr" and i not in results:
                res, _ = engine(rendered[i])
                results[i] = [it[1] for it in res] if res else []

    import pymupdf as _f
    d = _f.open(pdf_path)
    try:
        for i, m in plan:
            if m == "text":
                results[i] = [ln.strip() for ln in d[i].get_text().split("\n") if ln.strip()]
    finally:
        d.close()

    print(f"提取完成 {time.time()-t1:.0f}s", flush=True)

    # ---- 4. 组装输出
    buf, low = [], []
    for i in targets:
        lines = results.get(i, [])
        buf.append(f"\n===== 第 {i+1} 页 =====\n" + "\n".join(lines))
        if len(lines) < 3 or sum(len(x) for x in lines) < min_chars:
            low.append((i + 1, sum(len(x) for x in lines)))

    txt_path = os.path.join(outdir, txt_name)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))

    total_chars = sum(sum(len(x) for x in results.get(i, [])) for i in targets)
    print("-" * 56)
    print(f"输出文本 : {txt_path}")
    print(f"总字数   : {total_chars}")
    if low:
        print(f"[!] 低产出页 {len(low)} 个（可能空白/分辨率不足/纯图章），建议复核：")
        for pno, cnt in low[:20]:
            print(f"      第{pno}页  仅 {cnt} 字")
        if len(low) > 20:
            print(f"      ... 另有 {len(low)-20} 页")
    if not keep_images:
        for p in rendered.values():
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(imgdir)
        except OSError:
            pass
        print("已清理临时图片（复核证据已删，如需复核请加 --keep-images）")
    else:
        print(f"保留渲染图: {imgdir}")
    return txt_path


def main():
    ap = argparse.ArgumentParser(
        description="无文本层扫描件 PDF -> 本地离线 OCR（内容不出本机）")
    ap.add_argument("pdf", help="PDF 文件路径")
    ap.add_argument("--probe", action="store_true",
                    help="仅探测是否为扫描件/混合件，不做转换")
    ap.add_argument("-o", "--outdir", default="", help="输出目录（默认 PDF 同名目录）")
    ap.add_argument("--pages", default="all",
                    help="页范围：all / 3 / 1-5 / 1,4,9（默认 all）")
    ap.add_argument("--dpi", type=int, default=200,
                    help="渲染 DPI，200 为清晰度与耗时的平衡点（默认 200）")
    ap.add_argument("--workers", type=int, default=1,
                    help="并行进程数，建议设为 CPU 核数（默认 1，即串行）")
    ap.add_argument("--force-ocr", action="store_true",
                    help="强制所有页走 OCR（混合件默认只 OCR 无文本层的页）")
    ap.add_argument("--min-text", type=int, default=20,
                    help="判定「有文本层」的最少字数（默认 20）")
    ap.add_argument("--min-chars", type=int, default=30,
                    help="低产出页告警阈值（默认 30 字）")
    ap.add_argument("--keep-images", action="store_true",
                    help="保留渲染出的 PNG，用于人工复核")
    ap.add_argument("--txt", default="_OCR全文.txt", help="输出文本文件名")
    args = ap.parse_args()

    pdf = os.path.abspath(args.pdf)
    if not os.path.exists(pdf):
        sys.exit(f"[error] 文件不存在: {pdf}")

    if args.probe:
        probe(pdf, args.min_text)
        return

    outdir = args.outdir or os.path.splitext(pdf)[0] + "_ocr"
    stem = os.path.splitext(os.path.basename(pdf))[0]
    txt_name = args.txt if args.txt != "_OCR全文.txt" else f"{stem}_OCR全文.txt"

    run(pdf, os.path.abspath(outdir), args.dpi, args.pages,
        args.force_ocr, max(1, args.workers), args.min_text,
        args.min_chars, args.keep_images, txt_name)


if __name__ == "__main__":
    main()
