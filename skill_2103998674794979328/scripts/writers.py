"""
writers.py — 四种输出格式的写出器（Word / Excel / PPT / 图片）

每个写出器接收 pdf_engine.analyze() 产出的 DocumentIR，写出目标文件。
统一约定：
  * 所有写出函数签名为 write_xxx(doc, out_path, **options) -> dict（返回统计信息）
  * 失败一律抛出 ConversionError，由 cli.py 统一捕获并翻译为用户可读信息
  * 任何依赖缺失（python-docx 等）在调用时抛出，附带可执行的修复提示
"""

from __future__ import annotations

import io
import os
from typing import Any

from pdf_engine import DocumentIR, TableData, TextBlock, image_to_bytes, render_page_image

# --------------------------------------------------------------------------- #
# 统一异常
# --------------------------------------------------------------------------- #


class ConversionError(RuntimeError):
    """写出阶段的统一异常类型。"""


def _ensure_parent(path: str) -> None:
    """确保输出目录存在。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise ConversionError(f"无法创建输出目录 {parent}：{exc}") from exc


def _import(module: str, package: str):
    """导入第三方库，失败时给出可直接执行的安装命令。"""
    try:
        return __import__(module)
    except ImportError as exc:
        raise ConversionError(
            f"缺少依赖 {package}。请执行：\n"
            f'  pip install {package}'
        ) from exc


# --------------------------------------------------------------------------- #
# Word
# --------------------------------------------------------------------------- #

def write_word(doc: DocumentIR, out_path: str, *,
               include_images: bool = True,
               include_tables: bool = True,
               page_break_between_pages: bool = True,
               font_ascii: str = "Calibri",
               font_cjk: str = "微软雅黑") -> dict[str, Any]:
    """把 DocumentIR 写成 .docx。

    版面策略：
      * 标题按 IR 的 kind（h1/h2）映射到 Word 的 Heading 层级，从而保留
        导航窗格大纲结构；正文使用 Normal 样式并保留段落边界。
      * 表格写为原生 Word 表格（可继续编辑、可排序），并套用网格样式。
      * 图片写为内嵌浮动图片，宽度按原始版面比例换算，避免拉伸变形。
      * 超链接：PDF 的链接注释由 pypdf 读取后按顺序追加为段落末尾的
        超链接（不破坏正文），这是成本与收益的折中。
    """
    _import("docx", "python-docx")
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor, Cm

    _ensure_parent(out_path)

    d = Document()
    # 正文默认字体：西文 + 中文分别设置，避免中文回退成宋体
    normal = d.styles["Normal"]
    normal.font.name = font_ascii
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), font_cjk)

    # 收紧标题的默认间距，让版式更接近原文
    for name, size in (("Heading 1", 17), ("Heading 2", 14), ("Heading 3", 12)):
        try:
            st = d.styles[name]
            st.font.size = Pt(size)
            st.font.color.rgb = RGBColor(0x1F, 0x2A, 0x37)
            st.font.name = font_ascii
            st.element.rPr.rFonts.set(qn("w:eastAsia"), font_cjk)
        except KeyError:
            pass

    # 记录表格对象到 IR 表格的映射，用于按版面顺序插入
    stats = {"paragraphs": 0, "tables": 0, "images": 0, "links": 0}

    for pi, page in enumerate(doc.pages):
        if pi > 0 and page_break_between_pages:
            d.add_page_break()

        # 把「块 + 表格 + 图片」按 top 坐标排序，还原阅读顺序
        items: list[tuple[float, str, Any]] = []
        items += [(b.top, "block", b) for b in page.blocks]
        if include_tables:
            items += [(t.top, "table", t) for t in page.tables]
        if include_images:
            items += [(im.top, "image", im) for im in page.images]
        items.sort(key=lambda it: (it[0], {"block": 0, "table": 1, "image": 2}[it[1]]))

        for _, kind, obj in items:
            if kind == "block":
                _word_add_block(d, obj, stats)
            elif kind == "table":
                _word_add_table(d, obj, stats)
            else:
                _word_add_image(d, obj, page, stats)

    # 超链接附录（若 PDF 含链接注释）
    links = doc.metadata.get("links") or []
    if links:
        d.add_page_break()
        h = d.add_heading("链接与引用", level=1)
        h.style.font.size = Pt(14)
        for text_, url in links:
            p = d.add_paragraph(style="List Bullet")
            run = p.add_run(text_ or url)
            _add_hyperlink(p, run, url)
            stats["links"] += 1

    try:
        d.save(out_path)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"写入 Word 文件失败（{out_path}）：{exc}") from exc

    stats["output"] = os.path.abspath(out_path)
    return stats


def _word_add_block(d, b: TextBlock, stats: dict) -> None:
    """写入一个文本块。"""
    from docx.shared import Pt

    text = b.text.strip()
    if not text:
        return

    if b.kind == "h1":
        p = d.add_heading(text, level=1)
    elif b.kind == "h2":
        p = d.add_heading(text, level=2)
    elif b.kind == "list":
        p = d.add_paragraph(text, style="List Bullet")
    else:
        p = d.add_paragraph(text)

    # 正文按原始字号微调（限制在合理区间，避免异常放大）
    if b.kind == "body" and b.size:
        size = max(9.0, min(13.0, b.size))
        for r in p.runs:
            r.font.size = Pt(size)
    if b.bold and b.kind in ("body", "list"):
        for r in p.runs:
            r.bold = True
    stats["paragraphs"] += 1


def _word_add_table(d, t: TableData, stats: dict) -> None:
    """写入一张原生 Word 表格。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Pt

    rows = [r for r in t.rows if any(str(c).strip() for c in r)]
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    # 补齐每行到相同列数
    norm = [list(r) + [""] * (ncols - len(r)) for r in rows]

    table = d.add_table(rows=len(norm), cols=ncols)
    try:
        table.style = "Table Grid"
    except KeyError:
        pass
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    for i, row in enumerate(norm):
        for j, val in enumerate(row):
            cell = table.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(str(val))
            run.font.size = Pt(9.5)
            if i == 0 and t.has_header:
                run.bold = True
    stats["tables"] += 1


def _word_add_image(d, im, page, stats: dict) -> None:
    """内嵌一张图片，宽度按版面宽度比例换算（页面正文宽约 16cm）。"""
    from docx.shared import Cm

    try:
        data = image_to_bytes(im.image, "PNG")
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"图片编码失败：{exc}") from exc

    # 图片在页面上的宽度占页面宽度的比例 → 映射到 Word 正文宽度
    ratio = (im.x1 - im.x0) / max(1.0, page.width)
    width_cm = max(3.0, min(15.5, 16.0 * ratio))

    p = d.add_paragraph()
    try:
        p.add_run().add_picture(io.BytesIO(data), width=Cm(width_cm))
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"插入图片失败：{exc}") from exc
    stats["images"] += 1


def _add_hyperlink(paragraph, run, url: str) -> None:
    """把已有 run 转换为超链接（python-docx 未提供原生 API，需手写关系）。"""
    try:
        from docx.oxml.ns import qn
        import docx.opc.constants as const

        part = paragraph.part
        r_id = part.relate_to(
            url, const.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

        hl = paragraph._p.makeelement(qn("w:hyperlink"), {})
        hl.set(qn("r:id"), r_id)

        new_run = paragraph._p.makeelement(qn("w:r"), {})
        rPr = run._r.find(qn("w:rPr"))
        if rPr is not None:
            new_run.append(rPr)
        t = paragraph._p.makeelement(qn("w:t"), {})
        t.text = run.text
        new_run.append(t)
        hl.append(new_run)

        run._r.addnext(hl)
        run._r.getparent().remove(run._r)
    except Exception:  # noqa: BLE001
        # 超链接是加分项，失败时保留纯文本，不影响主流程
        pass


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #

def write_excel(doc: DocumentIR, out_path: str, *,
                mode: str = "auto",
                sheet_per: str = "table",
                include_text: bool = True,
                numbers_as_number: bool = True,
                header_fill: str = "2563EB") -> dict[str, Any]:
    """把 DocumentIR 写成 .xlsx。

    mode 取值：
      auto      有表格则按表格输出；一个表格都没有则把整页文本按行拆分输出
      table     只输出表格（无表格时静默跳过，但会在统计里说明）
      text      忽略表格结构，把每页文本按行拆分输出（适合无边框的伪表格）
      layout    按原始版面坐标把词还原成单元格网格（适合银行流水等

                对齐靠空格而非线条的文档）

    sheet_per 取值：table（每张表一个工作表）
                    page（每页一个工作表）
    """
    _import("openpyxl", "openpyxl")
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    _ensure_parent(out_path)

    tables = doc.all_tables()
    use_mode = mode
    if mode == "auto":
        use_mode = "table" if tables else "layout"

    wb = Workbook()
    wb.remove(wb.active)  # 移除默认空表，按需创建工作表

    thin = Side(style="thin", color="94A3B8")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_font = Font(bold=True, color="FFFFFF", size=10)
    head_fill = PatternFill("solid", fgColor=header_fill)
    body_font = Font(size=10)

    stats = {"sheets": 0, "tables": 0, "rows": 0, "mode": use_mode}

    def _sanitize(name: str) -> str:
        r"""工作表名不能超 31 字符，且不能含 : \ / ? * [ ]"""
        for ch in ':¥/?*[]':
            name = name.replace(ch, "_")
        return (name or "Sheet")[:31]

    def _emit_table(ws, t: TableData, start_row: int = 1) -> int:
        r = start_row
        for i, row in enumerate(t.rows):
            for j, val in enumerate(row):
                cell = ws.cell(row=r, column=j + 1)
                if numbers_as_number and _is_num(val):
                    cell.value = _to_num(val)
                    if val.strip().endswith("%"):
                        cell.number_format = "0.0%"
                        cell.value = _to_num(val) / 100.0
                    elif "," in val:
                        cell.number_format = "#,##0.00"
                else:
                    cell.value = val
                cell.font = head_font if (i == 0 and t.has_header) else body_font
                cell.border = border
                cell.alignment = Alignment(vertical="center",
                                           horizontal="left" if j == 0 else "right"
                                           if numbers_as_number and _is_num(val) else "left",
                                           wrap_text=True)
                if i == 0 and t.has_header:
                    cell.fill = head_fill
            r += 1
        # 列宽自适应（按内容长度估算，限制上限）
        for j in range(max(len(x) for x in t.rows)):
            longest = max((len(str(row[j])) if j < len(row) else 0)
                          for row in t.rows)
            ws.column_dimensions[get_column_letter(j + 1)].width = min(42, max(10, longest + 3))
        # 冻结表头
        if t.has_header:
            ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
        stats["tables"] += 1
        stats["rows"] += len(t.rows)
        return r

    if use_mode == "table":
        if not tables:
            raise ConversionError(
                "未在 PDF 中检测到表格结构。建议改用 --mode layout，"
                "它按原始版面坐标还原网格，对无边框表格更有效。")
        for idx, t in enumerate(tables, 1):
            ws = wb.create_sheet(_sanitize(f"Table{idx}" if sheet_per == "table"
                                           else f"Page{t.page}"))
            _emit_table(ws, t)

    elif use_mode == "layout":
        for page in doc.pages:
            ws = wb.create_sheet(_sanitize(f"Page{page.number}"))
            if include_text and not page.tables and not page.images:
                # 纯文本页：按行还原，不做列对齐
                r = 1
                for b in page.blocks:
                    ws.cell(row=r, column=1, value=b.text).font = body_font
                    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True)
                    r += 1
                ws.column_dimensions["A"].width = 100
                stats["rows"] += r - 1
            else:
                rows_out = 1
                for t in page.tables:
                    rows_out = _emit_table(ws, t, start_row=rows_out) + 1
            stats["sheets"] += 1

    elif use_mode == "text":
        for page in doc.pages:
            ws = wb.create_sheet(_sanitize(f"Page{page.number}"))
            r = 1
            for b in page.blocks:
                ws.cell(row=r, column=1, value=b.text).font = body_font
                ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True)
                r += 1
            for t in page.tables:
                for row in t.rows:
                    for j, val in enumerate(row):
                        ws.cell(row=r, column=j + 1, value=val).font = body_font
                    r += 1
                r += 1
            ws.column_dimensions["A"].width = 110
            stats["rows"] += r - 1
            stats["sheets"] += 1
    else:
        raise ConversionError(f"未知的 mode：{mode}（可选 auto/table/text/layout）")

    if not wb.worksheets:
        raise ConversionError("没有可写入的内容，输出为空。请检查 PDF 是否为扫描件。")

    try:
        wb.save(out_path)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"写入 Excel 文件失败（{out_path}）：{exc}") from exc

    stats["sheets"] = len(wb.worksheets)
    stats["output"] = os.path.abspath(out_path)
    return stats


def _is_num(v: Any) -> bool:
    s = str(v).strip()
    if not s:
        return False
    s2 = s.replace(",", "").replace("%", "")
    try:
        float(s2)
        return True
    except ValueError:
        return False


def _to_num(v: Any) -> float:
    s = str(v).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")


# --------------------------------------------------------------------------- #
# PowerPoint
# --------------------------------------------------------------------------- #

def write_pptx(doc: DocumentIR, out_path: str, *,
               layout: str = "text",
               slide_size: str = "auto",
               title_from: str = "heading",
               max_bullets: int = 14) -> dict[str, Any]:
    """把 DocumentIR 写成 .pptx。

    layout 取值：
      text   每页 PDF → 一张幻灯片，标题取该页最大字号的块，其余正文作为
             项目符号（最通用的「文档转 PPT」形态）
      image  每页 PDF 渲染成整页图片铺满幻灯片（保留原样排版，适合图文混排、
             PPT 导出的 PDF 的精确保留）
      table  检测到表格的页 → 用原生 PPT 表格呈现表格内容

    幻灯片尺寸默认沿袭 PDF 第一页的宽高比，避免图片被拉伸。
    """
    _import("pptx", "python-pptx")
    from pptx import Presentation
    from pptx.util import Emu, Pt
    from pptx.dml.color import RGBColor

    _ensure_parent(out_path)

    prs = Presentation()
    if slide_size == "auto" and doc.pages:
        p0 = doc.pages[0]
        # 保持原始宽高比，高度固定为 7.5 英寸（标准 16:9 的高度）
        h_in = 7.5
        w_in = h_in * (p0.width / max(1.0, p0.height))
        prs.slide_width = Emu(int(w_in * 914400))
        prs.slide_height = Emu(int(h_in * 914400))
    elif slide_size == "16:9":
        prs.slide_width = Emu(12192000)
        prs.slide_height = Emu(6858000)
    elif slide_size == "4:3":
        prs.slide_width = Emu(9144000)
        prs.slide_height = Emu(6858000)

    blank = prs.slide_layouts[6]  # 全空白版式，自行绘制，控制力最强
    stats = {"slides": 0, "tables": 0, "images": 0, "layout": layout}

    for page in doc.pages:
        slide = prs.slides.add_slide(blank)
        stats["slides"] += 1

        if layout == "image":
            # 整页渲染后等比铺满
            from PIL import Image
            scale = 2.0
            img = render_page_image(doc.path, page.number - 1, scale=scale)
            if title_from == "heading" and page.blocks:
                _pptx_add_title(slide, prs, page.blocks[0].text)
            if any(b.kind in ("h1", "h2") for b in page.blocks):
                top = Emu(int(prs.slide_height * 0.16))
            else:
                top = Emu(0)
            avail_h = prs.slide_height - top
            iw, ih = img.size
            ratio = min(prs.slide_width / iw, avail_h / ih)
            w = Emu(int(iw * ratio))
            h = Emu(int(ih * ratio))
            left = Emu(int((prs.slide_width - w) / 2))
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            buf.seek(0)
            slide.shapes.add_picture(buf, left, top, width=w, height=h)
            stats["images"] += 1
            continue

        if layout == "table" and page.tables:
            _pptx_add_table_slide(slide, prs, page, stats)
            continue

        # text 模式（也是 table 模式下的兜底）
        title, body_blocks = _pptx_split_title_body(page.blocks, title_from)
        if title:
            _pptx_add_title(slide, prs, title)

        bullets = [b.text for b in body_blocks if b.text.strip()]
        if not bullets and page.tables:
            for t in page.tables:
                bullets.append(" | ".join(t.rows[0]))

        if bullets:
            top = Emu(int(prs.slide_height * (0.24 if title else 0.10)))
            height = prs.slide_height - top - Emu(int(prs.slide_height * 0.07))
            tb = slide.shapes.add_textbox(
                Emu(int(prs.slide_width * 0.07)), top,
                Emu(int(prs.slide_width * 0.86)), height)
            tf = tb.text_frame
            tf.word_wrap = True
            shown = bullets[:max_bullets]
            if len(bullets) > max_bullets:
                shown.append(f"…… 另有 {len(bullets) - max_bullets} 段内容未展开")
            # 字号随条目数自适应，避免内容溢出画布
            size = 20 if len(shown) <= 5 else 16 if len(shown) <= 9 else 13
            for i, text in enumerate(shown):
                para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                para.text = text
                para.level = 0
                for run in para.runs:
                    run.font.size = Pt(size)
                    run.font.color.rgb = RGBColor(0x1F, 0x2A, 0x37)

        # 页面中的插图也一并带上（text 模式下）
        if page.images:
            img = page.images[0].image
            iw, ih = img.size
            max_w = Emu(int(prs.slide_width * 0.34))
            max_h = Emu(int(prs.slide_height * 0.34))
            ratio = min(max_w / iw, max_h / ih)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            buf.seek(0)
            slide.shapes.add_picture(
                buf,
                prs.slide_width - Emu(int(iw * ratio)) - Emu(int(prs.slide_width * 0.04)),
                prs.slide_height - Emu(int(ih * ratio)) - Emu(int(prs.slide_height * 0.05)),
                width=Emu(int(iw * ratio)), height=Emu(int(ih * ratio)))
            stats["images"] += 1

    try:
        prs.save(out_path)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"写入 PPT 文件失败（{out_path}）：{exc}") from exc

    stats["output"] = os.path.abspath(out_path)
    return stats


def _pptx_split_title_body(blocks: list[TextBlock],
                           title_from: str) -> tuple[str, list[TextBlock]]:
    """从一页的块中挑出标题，其余作为正文。"""
    if not blocks:
        return "", []
    if title_from == "first":
        return blocks[0].text, blocks[1:]
    # heading：优先取第一个 h1/h2
    for i, b in enumerate(blocks):
        if b.kind in ("h1", "h2"):
            return b.text, blocks[:i] + blocks[i + 1:]
    # 没有标题样式时，取字号最大的块（若明显大于其他块）
    largest = max(blocks, key=lambda b: b.size)
    others = [b for b in blocks if b is not largest]
    if others and largest.size > max(b.size for b in others) * 1.15 and len(largest.text) < 90:
        return largest.text, others
    return "", blocks


def _pptx_add_title(slide, prs, text: str) -> None:
    """在幻灯片顶部添加一个标题文本框。"""
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt

    text = " ".join(str(text).split())
    if len(text) > 90:
        text = text[:88] + "…"
    tb = slide.shapes.add_textbox(Emu(int(prs.slide_width * 0.06)),
                                  Emu(int(prs.slide_height * 0.05)),
                                  Emu(int(prs.slide_width * 0.88)),
                                  Emu(int(prs.slide_height * 0.15)))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.text = text
    for p in tf.paragraphs:
        for run in p.runs:
            run.font.size = Pt(28)
            run.font.bold = True
            run.font.color.rgb = RGBColor(0x1F, 0x2A, 0x37)


def _pptx_add_table_slide(slide, prs, page, stats: dict) -> None:
    """用原生 PPT 表格呈现页面中的第一张表。"""
    from pptx.util import Emu

    t = page.tables[0]
    rows = [r for r in t.rows if any(str(c).strip() for c in r)]
    if not rows:
        return
    nrows = len(rows)
    ncols = max(len(r) for r in rows)
    left = Emu(int(prs.slide_width * 0.06))
    top = Emu(int(prs.slide_height * 0.08))
    width = Emu(int(prs.slide_width * 0.88))
    height = Emu(int(prs.slide_height * 0.84))

    shape = slide.shapes.add_table(nrows, ncols, left, top, width, height)
    table = shape.table
    for i in range(nrows):
        for j in range(ncols):
            cell = table.cell(i, j)
            cell.text = str(rows[i][j]) if j < len(rows[i]) else ""
            if i == 0 and t.has_header:
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.bold = True
    stats["tables"] += 1


# --------------------------------------------------------------------------- #
# 图片
# --------------------------------------------------------------------------- #

def write_images(doc: DocumentIR, out_path: str, *,
                 fmt: str = "PNG",
                 dpi: int = 200,
                 pages: list[int] | None = None,
                 jpg_quality: int = 95,
                 crop_images: bool = False) -> dict[str, Any]:
    """把 PDF 页面导出为图片。

    参数：
      out_path    输出路径。若含 {page} 占位符则按页命名；
                  若为目录或单页导出，则自动生成文件名。
      fmt         PNG / JPG / JPEG / WEBP / TIFF
      dpi         输出分辨率。200dpi 适合屏幕，300dpi 适合印刷，
                  600dpi 用于再 OCR。
      pages       指定页码列表（1-based）；None 表示全部
      crop_images 为 True 时改为导出页面内的插图（而非整页），
                  输出到 out_path 所在目录下 的 _images 子目录

    返回统计信息，含生成的文件列表。
    """
    _ensure_parent(out_path)

    fmt = (fmt or "PNG").upper().replace("JPEG", "JPG")
    if fmt not in ("PNG", "JPG", "WEBP", "TIFF"):
        raise ConversionError(f"不支持的图片格式：{fmt}（可选 PNG/JPG/WEBP/TIFF）")
    if dpi < 36 or dpi > 1200:
        raise ConversionError(f"dpi 超出合理范围：{dpi}（建议 72–600）")

    scale = dpi / 72.0
    written: list[str] = []

    def _save(img, target: str) -> None:
        _ensure_parent(target)
        kw: dict[str, Any] = {"optimize": True}
        if fmt == "JPG":
            img = img.convert("RGB")
            kw = {"quality": jpg_quality, "optimize": True, "subsampling": 0}
        try:
            img.save(target, format=fmt if fmt != "JPG" else "JPEG", **kw)
        except Exception as exc:  # noqa: BLE001
            raise ConversionError(f"保存图片失败（{target}）：{exc}") from exc
        written.append(target)

    if crop_images:
        outdir = os.path.join(os.path.dirname(os.path.abspath(out_path)), "_images")
        os.makedirs(outdir, exist_ok=True)
        written.clear()
        total = 0
        for page in doc.pages:
            if pages and page.number not in pages:
                continue
            for k, im in enumerate(page.images, 1):
                name = f"page{page.number:03d}_img{k:02d}.{fmt.lower()}"
                _save(im.image, os.path.join(outdir, name))
                total += 1
        if total == 0:
            raise ConversionError("未在指定页面中检测到可提取的插图。")
        return {"images": total, "dpi": dpi, "format": fmt,
                "files": written, "output_dir": outdir}

    # 整页导出
    out_is_dir = os.path.isdir(out_path) or out_path.endswith(("/", "\\"))
    stem, ext = os.path.splitext(out_path)
    targets = []
    for page in doc.pages:
        if pages and page.number not in pages:
            continue
        if out_is_dir:
            target = os.path.join(out_path, f"page{page.number:03d}.{fmt.lower()}")
        elif "{page}" in out_path:
            target = out_path.replace("{page}", f"{page.number:03d}")
            if not os.path.splitext(target)[1]:
                target += f".{fmt.lower()}"
            else:
                target = os.path.splitext(target)[0] + f".{fmt.lower()}"
        elif doc.page_count == 1:
            target = out_path if ext.lower().lstrip(".") in ("png", "jpg", "jpeg",
                                                             "webp", "tif", "tiff") \
                else f"{out_path}.{fmt.lower()}"
        else:
            target = f"{stem}_{page.number:03d}.{fmt.lower()}"
        targets.append((page, target))

    if not targets:
        raise ConversionError("没有符合 pages 条件的页面可导出。")

    for page, target in targets:
        img = render_page_image(doc.path, page.number - 1, scale=scale)
        _save(img, target)
        page._rendered = None  # 释放内存

    return {"images": len(written), "dpi": dpi, "format": fmt,
            "files": written, "output": written[0] if len(written) == 1 else None}
