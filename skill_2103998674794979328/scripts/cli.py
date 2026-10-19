#!/usr/bin/env python3
"""
cli.py — PDF 转换器的统一命令行入口

用法：
    python cli.py <输入.pdf> --to <格式> --out <输出路径> [选项]

示例：
    python cli.py report.pdf --to word  --out report.docx
    python cli.py data.pdf   --to excel --out data.xlsx --mode table
    python cli.py deck.pdf   --to ppt   --out deck.pptx --layout image
    python cli.py doc.pdf    --to image --out ./pages --dpi 300 --format PNG

退出码：
    0  成功
    1  参数错误
    2  解析失败（文件不存在 / 加密 / 损坏）
    3  转换失败（依赖缺失 / 写出异常）
    4  写出成功但结果可疑（例如扫描件无文本层），需人工确认
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 保证脚本独立运行时也能 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdf_engine import PDFEngineError, analyze, close_document  # noqa: E402
from writers import ConversionError  # noqa: E402

TARGETS = {
    "word": ("docx", "write_word"),
    "docx": ("docx", "write_word"),
    "excel": ("xlsx", "write_excel"),
    "xlsx": ("xlsx", "write_excel"),
    "ppt": ("pptx", "write_pptx"),
    "pptx": ("pptx", "write_pptx"),
    "image": ("png", "write_images"),
    "png": ("png", "write_images"),
    "jpg": ("jpg", "write_images"),
    "img": ("png", "write_images"),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf-converter",
        description="把 PDF 转换为 Word / Excel / PPT / 图片，尽量保留原始排版。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", help="输入 PDF 文件路径")
    p.add_argument("--to", "-t", required=True, choices=sorted(TARGETS),
                   help="目标格式：word / excel / ppt / image")
    p.add_argument("--out", "-o", default=None,
                   help="输出文件路径（缺省时与输入同目录、同名、改扩展名）")
    p.add_argument("--pages", default=None,
                   help="仅转换指定页，如 1-3,5,8（1-based）。默认全部")
    p.add_argument("--password", default=None, help="加密 PDF 的打开口令")
    p.add_argument("--json", action="store_true",
                   help="以 JSON 输出结果（便于程序化调用）")

    # 解析层选项
    g = p.add_argument_group("解析选项")
    g.add_argument("--no-tables", action="store_true", help="跳过表格识别（更快，适合纯文字）")
    g.add_argument("--no-images", action="store_true", help="跳过图片提取（更快，纯文字场景）")

    # Word
    g = p.add_argument_group("Word 选项")
    g.add_argument("--no-page-break", action="store_true", help="不按 PDF 分页插入 Word 分页符")

    # Excel
    g = p.add_argument_group("Excel 选项")
    g.add_argument("--mode", default="auto",
                   choices=["auto", "table", "text", "layout"],
                   help="Excel 输出模式，默认 auto（自动选择）")
    g.add_argument("--sheet-per", default="table", choices=["table", "page"],
                   help="工作表划分方式")
    g.add_argument("--keep-text-format", action="store_true",
                   help="数字保持文本格式，不做数值化（适合编号、账号等）")

    # PPT
    g = p.add_argument_group("PPT 选项")
    g.add_argument("--layout", default="text", choices=["text", "image", "table"],
                   help="幻灯片版式：text（要点）/ image（整页图）/ table（原生表格）")

    # 图片
    g = p.add_argument_group("图片选项")
    g.add_argument("--format", default="PNG", choices=["PNG", "JPG", "WEBP", "TIFF"],
                   help="图片格式，默认 PNG")
    g.add_argument("--dpi", type=int, default=200,
                   help="输出分辨率，默认 200；印刷用 300，再 OCR 用 600")
    g.add_argument("--crop-images", action="store_true",
                   help="改为导出页面内的插图（而非整页），输出到 _images 子目录")

    return p


def parse_pages(spec: str | None, total: int) -> list[int] | None:
    """解析 --pages，把 '1-3,5' 展开成 [1,2,3,5]。"""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise ValueError(f"页码区间格式错误：{part}")
            if start > end:
                start, end = end, start
            out.update(range(max(1, start), end + 1))
        else:
            try:
                out.add(int(part))
            except ValueError:
                raise ValueError(f"页码格式错误：{part}")
    return sorted(p for p in out if 1 <= p <= total)


def default_out(input_path: str, target: str, fmt: str, page_count: int) -> str:
    """按目标格式推导默认输出路径。"""
    ext, _ = TARGETS[target]
    if target in ("image", "png", "jpg", "img"):
        ext = fmt.lower()
    stem = os.path.splitext(input_path)[0]
    if target in ("image", "png", "jpg", "img") and page_count > 1:
        return f"{stem}_pages"  # 多页导出为目录
    return f"{stem}.{ext}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    t0 = time.time()

    # ---- 输入校验 ----
    if not os.path.isfile(args.input):
        return _fail(f"输入文件不存在：{args.input}", 2, args.json)
    if not args.input.lower().endswith(".pdf"):
        # 不硬性拒绝，只提示——有些 PDF 没有 .pdf 后缀
        print(f"提示：{args.input} 的扩展名不是 .pdf，仍将按 PDF 解析。",
              file=sys.stderr)

    ext, writer_name = TARGETS[args.to]
    is_image = args.to in ("image", "png", "jpg", "img")

    # ---- 解析 ----
    try:
        doc = analyze(
            args.input,
            detect_tables=not args.no_tables,
            extract_images=not args.no_images,
            password=args.password,
        )
    except PDFEngineError as exc:
        return _fail(str(exc), 2, args.json)

    # ---- 页码过滤 ----
    try:
        wanted = parse_pages(args.pages, doc.page_count)
    except ValueError as exc:
        return _fail(str(exc), 1, args.json)
    if wanted is not None:
        total_pages = doc.page_count
        doc.pages = [p for p in doc.pages if p.number in wanted]
        if not doc.pages:
            return _fail(f"指定的页码超出范围（文档共 {total_pages} 页）", 1, args.json)
        # page_count 是 pages 的派生属性，过滤后自动正确

    # 收集超链接（仅 Word 需要，成本很低）
    if args.to in ("word", "docx"):
        doc.metadata["links"] = _collect_links(args.input)

    # ---- 输出路径 ----
    out_path = args.out or default_out(args.input, args.to, args.format, doc.page_count)
    out_path = os.path.abspath(out_path)

    # ---- 写出 ----
    import writers as W
    fn = getattr(W, writer_name)

    kwargs: dict = {}
    try:
        if writer_name == "write_word":
            kwargs = {"page_break_between_pages": not args.no_page_break}
        elif writer_name == "write_excel":
            kwargs = {"mode": args.mode, "sheet_per": args.sheet_per,
                      "numbers_as_number": not args.keep_text_format}
        elif writer_name == "write_pptx":
            kwargs = {"layout": args.layout}
        elif writer_name == "write_images":
            kwargs = {"fmt": args.format, "dpi": args.dpi,
                      "pages": wanted, "crop_images": args.crop_images}
        stats = fn(doc, out_path, **kwargs)
    except ConversionError as exc:
        return _fail(str(exc), 3, args.json)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{type(exc).__name__}: {exc}", 3, args.json)
    finally:
        close_document(doc.path)

    stats["elapsed_sec"] = round(time.time() - t0, 2)
    stats["source"] = os.path.abspath(args.input)
    stats["pages_processed"] = doc.page_count

    # ---- 质量自检：扫描件 / 空文本提醒 ----
    warnings = _quality_warnings(doc, args.to)

    if args.json:
        print(json.dumps({"ok": True, "stats": stats, "warnings": warnings},
                         ensure_ascii=False, indent=2))
    else:
        _print_report(args.to, stats, warnings)

    return 4 if warnings else 0


def _collect_links(path: str) -> list[tuple[str, str]]:
    """用 pypdf 抽取页面上的超链接注释，返回 (显示文本, URL) 列表。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        out: list[tuple[str, str]] = []
        for page in reader.pages:
            annots = page.get("/Annots") or []
            for a in annots:
                try:
                    obj = a.get_object()
                    if obj.get("/Subtype") != "/Link":
                        continue
                    action = obj.get("/A")
                    uri = None
                    if action is not None:
                        uri = action.get_object().get("/URI")
                    if uri:
                        out.append(("", str(uri)))
                except Exception:  # noqa: BLE001
                    continue
        # 去重，保持顺序
        seen = set()
        uniq = []
        for t, u in out:
            if u not in seen:
                seen.add(u)
                uniq.append((t, u))
        return uniq
    except Exception:  # noqa: BLE001
        return []


def _quality_warnings(doc, target: str) -> list[str]:
    """转换结果的可信度自检，返回需人工确认的提示列表。"""
    warns: list[str] = []
    total_chars = sum(len(b.text) for b in doc.all_blocks())
    avg = total_chars / max(1, doc.page_count)

    if avg < 40:
        warns.append(
            f"该 PDF 平均每页仅提取到 {avg:.0f} 个字符，很可能是扫描件（图片型 PDF）。"
            "本次转换只还原了图像，文字层缺失。如需可编辑文字，"
            "请先做 OCR（例如 用 ocrmypdf 预处理后再转换）。")

    if target in ("excel", "xlsx") and not doc.all_tables():
        warns.append("未检测到表格结构。已按版面模式输出，"
                     "建议打开文件确认列是否对齐，必要时人工调整。")

    if target in ("word", "docx"):
        # 表格或图片与文本块大量重叠时，提示可能存在重复内容
        for page in doc.pages:
            for t in page.tables:
                for im in page.images:
                    if abs(t.top - im.top) < 5:
                        warns.append(
                            f"第 {page.number} 页的表格与图片位置高度重合，"
                            "可能出现内容重复，请人工核对。")
                        break

    return warns


def _print_report(target: str, stats: dict, warnings: list[str]) -> None:
    """人类可读的结果输出。"""
    label = {"word": "Word", "docx": "Word", "excel": "Excel", "xlsx": "Excel",
             "ppt": "PPT", "pptx": "PPT"}.get(target, "图片")
    print(f"✓ 转换完成（{label}）")
    print(f"  源文件    : {stats['source']}")
    if stats.get("output"):
        print(f"  输出文件  : {stats['output']}")
    if stats.get("output_dir"):
        print(f"  输出目录  : {stats['output_dir']}")
    print(f"  处理页数  : {stats['pages_processed']}")

    if target in ("excel", "xlsx"):
        print(f"  输出模式  : {stats.get('mode')}")
        print(f"  工作表数  : {stats.get('sheets')}   表格数: {stats.get('tables')}"
              f"   总行数: {stats.get('rows')}")
    elif target in ("ppt", "pptx"):
        print(f"  幻灯片数  : {stats.get('slides')}   表格: {stats.get('tables')}"
              f"   图片: {stats.get('images')}")
    elif target in ("word", "docx"):
        print(f"  段落数    : {stats.get('paragraphs')}   表格: {stats.get('tables')}"
              f"   图片: {stats.get('images')}")
    else:
        print(f"  导出图片  : {stats.get('images')} 张   分辨率: {stats.get('dpi')} dpi"
              f"   格式: {stats.get('format')}")
        files = stats.get("files") or []
        for f in files[:5]:
            print(f"    - {f}")
        if len(files) > 5:
            print(f"    … 另有 {len(files) - 5} 个文件")

    print(f"  耗时      : {stats['elapsed_sec']}s")

    if warnings:
        print("\n⚠ 请人工确认：")
        for w in warnings:
            print(f"  - {w}")


def _fail(msg: str, code: int, as_json: bool) -> int:
    """统一错误输出。"""
    if as_json:
        print(json.dumps({"ok": False, "error": msg, "code": code},
                         ensure_ascii=False, indent=2))
    else:
        print(f"✗ 转换失败：{msg}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
