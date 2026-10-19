# -*- coding: utf-8 -*-
"""K12 试卷/答案 HTML -> 可打印 Word(.docx)，真实印刷卷版式。

优化点（相对朴素版）：
  - A4 页面，左页边距留密封线区
  - 整页左侧贯穿竖线（页面边框）模拟密封线 + 页眉"密封线内不得答题"提示
  - 得分表（黑线表格）、考生信息栏（下划线填空）
  - 大题号黑体层级、题号加粗、选项逐行
  - 作文方格纸（Table Grid 网格，非横线）
  - 正文 1.35 倍行距
  - 答案：红色答案、解析、原文依据左框、评分建议

适配两套 class 体系：
  试卷: .stuinfo/.papertitle/.papersub/table.score/.notice/.sec/.q/.opt/.lines/.comp/.footnote
  答案A(九上): .sec/.q(.no/.ans/.why/.ref/.note) + h1/.sub
  答案B(七下): .sec/.item>.q/.a/.e/.ref/.note/.fen + h1/.sub
"""
import sys
from bs4 import BeautifulSoup, Tag, NavigableString
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

INLINE = {'span', 'b', 'strong', 'em', 'i', 'u', 'br', 'sub', 'sup', 'a'}
RED = RGBColor(0xb0, 0x00, 0x20)
GRAY = RGBColor(0x55, 0x55, 0x55)


def set_run(run, font='SimSun', size=None, bold=None, color=None):
    run.font.name = font
    rPr = run._element.get_or_add_rPr()
    rf = rPr.find(qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rPr.append(rf)
    rf.set(qn('w:eastAsia'), font)
    rf.set(qn('w:ascii'), font)
    rf.set(qn('w:hAnsi'), font)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color:
        run.font.color.rgb = color


def add_run(p, text, font='SimSun', size=12, bold=False, color=None):
    r = p.add_run(text)
    set_run(r, font, size, bold, color)
    return r


def mkp(doc, align=None):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.line_spacing = 1.35
    return p


def set_border(p, sides, sz='6', color='000000'):
    pPr = p._p.get_or_add_pPr()
    pBdr = pPr.find(qn('w:pBdr'))
    if pBdr is None:
        pBdr = OxmlElement('w:pBdr')
        pPr.append(pBdr)
    for s in sides:
        e = OxmlElement('w:' + s)
        e.set(qn('w:val'), 'single')
        e.set(qn('w:sz'), sz)
        e.set(qn('w:space'), '2')
        e.set(qn('w:color'), color)
        pBdr.append(e)


def add_line(doc, n=1, color='888888'):
    for _ in range(n):
        p = mkp(doc)
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.space_before = Pt(1)
        add_run(p, '　', size=2)
        set_border(p, ['bottom'], sz='6', color=color)


def add_page_seal(doc, seal_line=True):
    """整页左侧贯穿竖线（密封线）+ 页眉提示。仅试卷加竖线，答案卷不加。"""
    if not seal_line:
        return
    sectPr = doc.sections[0]._sectPr
    pgMar = sectPr.find(qn('w:pgMar'))
    pgB = sectPr.find(qn('w:pgBorders'))
    if pgB is None:
        pgB = OxmlElement('w:pgBorders')
        if pgMar is not None:
            pgMar.addnext(pgB)
        else:
            sectPr.append(pgB)
    pgB.set(qn('w:offsetFrom'), 'page')
    for side, val in [('top', 'none'), ('left', 'single'), ('bottom', 'none'), ('right', 'none')]:
        e = OxmlElement('w:' + side)
        e.set(qn('w:val'), val)
        e.set(qn('w:sz'), '12')
        e.set(qn('w:space'), '24')
        e.set(qn('w:color'), '000000' if val != 'none' else 'auto')
        pgB.append(e)
    hdr = doc.sections[0].header
    hdr.is_linked_to_previous = False
    hp = hdr.paragraphs[0]
    hp.text = ''
    hp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    add_run(hp, '密封线内不得答题', font='SimHei', size=9, bold=True, color=GRAY)


def add_page_number(doc):
    """页脚页码用 Word 域：第 X 页 / 共 Y 页。域随实际页数自动变化，不硬编码数字。"""
    footer = doc.sections[0].footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0]
    p.text = ''
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def field(instr):
        r = p.add_run()
        begin = OxmlElement('w:fldChar')
        begin.set(qn('w:fldCharType'), 'begin')
        it = OxmlElement('w:instrText')
        it.set(qn('xml:space'), 'preserve')
        it.text = instr
        sep = OxmlElement('w:fldChar')
        sep.set(qn('w:fldCharType'), 'separate')
        t = OxmlElement('w:t')
        t.text = '1'
        end = OxmlElement('w:fldChar')
        end.set(qn('w:fldCharType'), 'end')
        for e in (begin, it, sep, t, end):
            r._r.append(e)
        set_run(r, 'SimSun', 9)

    add_run(p, '第 ', 'SimSun', 9)
    field(' PAGE ')
    add_run(p, ' 页 / 共 ', 'SimSun', 9)
    field(' NUMPAGES ')
    add_run(p, ' 页', 'SimSun', 9)


def render_inline_into(p, node, font='SimSun', size=12, bold=False, color=None):
    if node is None or isinstance(node, NavigableString):
        if isinstance(node, NavigableString):
            t = str(node)
            if t.strip():
                add_run(p, t, font, size, bold, color)
        return
    cls = ' '.join(node.get('class') or [])
    tag = node.name
    nf, nb, nc = font, bold, color
    if tag in ('b', 'strong'):
        nf, nb = 'SimHei', True
    if 'no' in cls:
        nb = True
    if 'ans' in cls:
        nf, nb, nc = 'SimHei', True, RED
    if 'light' in cls:
        nb = False
    if 'blank' in cls:
        add_run(p, '　　　　　　　', font, size, False, None).font.underline = True
        for ch in node.children:
            render_inline_into(p, ch, nf, size, nb, nc)
        return
    if tag == 'br':
        p.add_run().add_break()
        return
    for ch in node.children:
        if isinstance(ch, NavigableString):
            t = str(ch)
            if t.strip():
                add_run(p, t, nf, size, nb, nc)
        elif ch.name == 'br':
            p.add_run().add_break()
        elif ch.name in INLINE:
            render_inline_into(p, ch, nf, size, nb, nc)


def render_table(doc, table):
    rows = table.find_all('tr')
    if not rows:
        return
    ncol = max((len(r.find_all(['td', 'th'])) for r in rows), default=0)
    if ncol == 0:
        return
    t = doc.add_table(rows=len(rows), cols=ncol)
    try:
        t.style = 'Table Grid'
    except Exception:
        pass
    for i, r in enumerate(rows):
        cells = r.find_all(['td', 'th'])
        for j, c in enumerate(cells):
            if j >= ncol:
                break
            cell = t.cell(i, j)
            cell.text = ''
            is_head = (c.name == 'th')
            center = is_head or ('c' in (c.get('class') or []))
            pg = cell.paragraphs[0]
            pg.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
            run = pg.add_run(c.get_text(strip=True))
            set_run(run, 'SimHei' if is_head else 'SimSun', 10.5, bold=is_head)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def render_fig(doc, el):
    """把 .fig 内的 SVG 结构图（circle/ellipse/rect/line/text 子集）用 Pillow 渲染为 PNG 嵌入 docx。
    零系统依赖（不依赖 cairo）；中文走系统字体（Songti.ttc / Arial Unicode.ttc）。"""
    svg = el.find('svg')
    cap = el.find(class_='cap')
    if svg is not None:
        try:
            from PIL import Image, ImageDraw, ImageFont
            import io
            vb = svg.get('viewBox') or '0 0 680 300'
            parts = [float(x) for x in vb.split()]
            ox, oy, vbw, vbh = (parts + [0, 0, 680, 300])[:4]
            scale = 900.0 / vbw
            W, H = 900, int(vbh * scale)
            img = Image.new('RGB', (W, H), 'white')
            dr = ImageDraw.Draw(img)
            fcache = {}

            def get_font(size, family=''):
                key = int(size * scale)
                if key in fcache:
                    return fcache[key]
                try:
                    f = ImageFont.truetype('/System/Library/Fonts/Supplemental/Songti.ttc', max(10, int(size * scale)))
                except Exception:
                    f = ImageFont.load_default()
                fcache[key] = f
                return f

            def num(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0

            for node in svg.find_all(True):
                if node.name in (None, 'svg'):
                    continue
                sw = max(1, int(num(node.get('stroke-width', 1)) * scale))
                stroke = node.get('stroke')
                outline = (0, 0, 0) if stroke else None
                if node.name == 'rect':
                    x, y = num(node.get('x')), num(node.get('y'))
                    w, h = num(node.get('width')), num(node.get('height'))
                    fill = node.get('fill', 'black')
                    dr.rectangle([x * scale, y * scale, (x + w) * scale, (y + h) * scale],
                                 fill=None if fill in ('none', None) else (0, 0, 0),
                                 outline=outline, width=sw)
                elif node.name == 'circle':
                    cx, cy, r = num(node.get('cx')), num(node.get('cy')), num(node.get('r'))
                    fill = node.get('fill', 'black')
                    dr.ellipse([(cx - r) * scale, (cy - r) * scale, (cx + r) * scale, (cy + r) * scale],
                               fill=None if fill in ('none', None) else (0, 0, 0),
                               outline=outline, width=sw)
                elif node.name == 'ellipse':
                    cx, cy = num(node.get('cx')), num(node.get('cy'))
                    rx, ry = num(node.get('rx')), num(node.get('ry'))
                    dr.ellipse([(cx - rx) * scale, (cy - ry) * scale, (cx + rx) * scale, (cy + ry) * scale],
                               outline=outline, width=sw)
                elif node.name == 'line':
                    x1, y1 = num(node.get('x1')), num(node.get('y1'))
                    x2, y2 = num(node.get('x2')), num(node.get('y2'))
                    dr.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale], fill=(0, 0, 0), width=sw)
                elif node.name == 'text':
                    x, y = num(node.get('x')), num(node.get('y'))
                    fs, fam = num(node.get('font-size', 13)), node.get('font-family', '') or ''
                    anchor = node.get('text-anchor', 'start')
                    txt = node.get_text(strip=True)
                    if not txt:
                        continue
                    f = get_font(fs, fam)
                    try:
                        tw = dr.textlength(txt, font=f)
                    except Exception:
                        tw = len(txt) * fs * scale * 0.6
                    tx = x * scale
                    if anchor == 'middle':
                        tx -= tw / 2
                    elif anchor == 'end':
                        tx -= tw
                    dr.text((tx, y * scale - fs * scale * 0.35), txt, fill=(0, 0, 0), font=f)
            buf = io.BytesIO()
            img.save(buf, 'PNG')
            buf.seek(0)
            p = mkp(doc, WD_ALIGN_PARAGRAPH.CENTER)
            p.add_run().add_picture(buf, width=Cm(15.0))
        except Exception:
            p = mkp(doc)
            set_border(p, ['top', 'bottom', 'left', 'right'])
            add_run(p, '【图见试卷印刷图，此处略】', 'SimSun', 10.5, False, GRAY)
    if cap is not None:
        p = mkp(doc, WD_ALIGN_PARAGRAPH.CENTER)
        add_run(p, cap.get_text(strip=True), 'SimSun', 10.5, False, GRAY)


def add_composition_grid(doc, cols=20, rows=26):
    """作文方格纸：网格表格，每格等宽等高，空单元格。"""
    t = doc.add_table(rows=rows, cols=cols)
    try:
        t.style = 'Table Grid'
    except Exception:
        pass
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in t.rows:
        r.height = Cm(0.72)
        for c in r.cells:
            c.text = ''
            c.width = Cm(0.72)
            c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def process_block(doc, el, base=12):
    if not isinstance(el, Tag):
        return
    if el.name in ('script', 'style'):
        return
    cls = ' '.join(el.get('class') or [])
    name = el.name

    if name == 'table':
        render_table(doc, el)
        return
    if 'papertitle' in cls:
        p = mkp(doc, WD_ALIGN_PARAGRAPH.CENTER)
        add_run(p, el.get_text(strip=True), 'SimHei', 18, True)
        return
    if 'papersub' in cls or 'sub' in cls:
        p = mkp(doc, WD_ALIGN_PARAGRAPH.CENTER)
        add_run(p, el.get_text(strip=True), 'SimSun', 10.5, False, GRAY)
        return
    if 'school' in cls:
        p = mkp(doc, WD_ALIGN_PARAGRAPH.CENTER)
        add_run(p, el.get_text(strip=True), 'SimHei', 12, False, GRAY)
        return
    if 'vol' in cls:
        p = mkp(doc)
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(4)
        add_run(p, el.get_text(strip=True), 'SimHei', 13, True)
        return
    if 'stuinfo' in cls:
        p = mkp(doc)
        for ch in el.children:
            render_inline_into(p, ch, 'SimSun', 11)
        return
    if 'notice' in cls:
        p = mkp(doc)
        set_border(p, ['top', 'bottom', 'left', 'right'])
        for ch in el.children:
            render_inline_into(p, ch, 'SimSun', 11)
        return
    if 'sec' in cls:
        p = mkp(doc)
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(2)
        add_run(p, el.get_text(strip=True), 'SimHei', 14, True)
        return
    if 'footnote' in cls:
        # 跳过"参考答案见单独文件"过时说明；其余 footnote 作底部分隔
        txt = el.get_text()
        if '本卷参考答案' in txt and '单独文件' in txt:
            return
        p = mkp(doc, WD_ALIGN_PARAGRAPH.CENTER)
        add_run(p, el.get_text('\n', strip=True), 'SimSun', 9, False, GRAY)
        return
    if 'item' in cls:
        for ch in el.children:
            if isinstance(ch, Tag):
                process_block(doc, ch, base)
        return
    if 'opt' in cls:
        spans = [c for c in el.children if isinstance(c, Tag) and c.name == 'span']
        if len(spans) == 4:
            t = doc.add_table(rows=2, cols=2)
            t.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for idx, sp in enumerate(spans):
                cell = t.cell(idx // 2, idx % 2)
                cell.text = ''
                pg = cell.paragraphs[0]
                pg.paragraph_format.line_spacing = 1.2
                render_inline_into(pg, sp, 'SimSun', 11)
            tbl_pr = t._tbl.tblPr
            borders = OxmlElement('w:tblBorders')
            for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                e = OxmlElement('w:' + edge)
                e.set(qn('w:val'), 'none')
                borders.append(e)
            tbl_pr.append(borders)
        else:
            p = mkp(doc)
            for i, sp in enumerate(spans):
                render_inline_into(p, sp, 'SimSun', 11)
                if i < len(spans) - 1:
                    p.add_run().add_break()
        return
    if 'lines' in cls:
        add_line(doc, 1)
        return
    if 'fig' in cls:
        render_fig(doc, el)
        return
    if 'comp' in cls:
        add_composition_grid(doc)
        return
    if 'a' in cls or 'e' in cls:
        p = mkp(doc)
        for ch in el.children:
            render_inline_into(p, ch, 'SimSun', 12)
        return
    if 'fen' in cls:
        p = mkp(doc)
        add_run(p, el.get_text(strip=True), 'SimSun', 11, False, GRAY)
        return
    if 'ref' in cls:
        p = mkp(doc)
        set_border(p, ['left'], sz='12', color='999999')
        for ch in el.children:
            render_inline_into(p, ch, 'SimSun', 12)
        return
    if 'note' in cls:
        p = mkp(doc)
        add_run(p, el.get_text('\n', strip=True), 'SimSun', 10.5, False, GRAY)
        return
    if 'q' in cls:
        has_marker = (el.find(class_='no') is not None) or (el.find(class_='ans') is not None)
        p = mkp(doc)
        for ch in el.children:
            if isinstance(ch, NavigableString):
                t = str(ch)
                if t.strip():
                    add_run(p, t, 'SimSun', 12, bold=not has_marker)
            elif ch.name in INLINE:
                render_inline_into(p, ch, 'SimSun', 12, bold=not has_marker)
            else:
                process_block(doc, ch, base)
        return
    if name in ('div', 'p', 'section'):
        p = mkp(doc)
        for ch in el.children:
            if isinstance(ch, NavigableString):
                t = str(ch)
                if t.strip():
                    add_run(p, t, 'SimSun', 12)
            elif ch.name in INLINE:
                render_inline_into(p, ch)
            else:
                process_block(doc, ch, base)
        return
    txt = el.get_text(strip=True)
    if txt:
        p = mkp(doc)
        add_run(p, txt, 'SimSun', 12)


def convert(html_path, docx_path):
    with open(html_path, encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'SimSun'
    style.font.size = Pt(12)
    rPr = style.element.get_or_add_rPr()
    rf = rPr.find(qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rPr.append(rf)
    rf.set(qn('w:eastAsia'), 'SimSun')

    sec = doc.sections[0]
    sec.page_width = Cm(21)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.5)
    sec.bottom_margin = Cm(2.5)
    sec.left_margin = Cm(2.8)
    sec.right_margin = Cm(2.0)

    node = soup.body
    while True:
        kids = [c for c in node.children if isinstance(c, Tag)]
        found = None
        for c in kids:
            cc = set(c.get('class') or [])
            if cc & {'sheet', 'paper', 'wrap'}:
                found = c
                break
        if found:
            node = found
        else:
            break

    has_paper = (node.find(class_='stuinfo') is not None) or (node.find('table', class_='score') is not None)
    add_page_seal(doc, seal_line=has_paper)
    add_page_number(doc)

    for ch in node.children:
        if isinstance(ch, Tag):
            process_block(doc, ch, 12)
    doc.save(docx_path)
    print('OK', docx_path)


if __name__ == '__main__':
    for i in range(1, len(sys.argv), 2):
        convert(sys.argv[i], sys.argv[i + 1])
