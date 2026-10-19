# -*- coding: utf-8 -*-
"""把语料组装成带书签、可点击目录的 Word 归档文档。"""
import json, os, re, sys, time
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = r'C:\Users\Lenovo\WorkBuddy\2026-09-13-20-23-38'
SRC = os.path.join(ROOT, 'src')
OUTDIR = os.path.join(ROOT, '资料', 'Word')

WHY = {
    'deleted': '微信返回「该内容已被发布者删除 / 无法查看」——多为推广软文到期下架，或原文已被删除。',
    'unavailable': '微信未返回正文（链接已失效，或该篇需登录后查看）。',
    'no-link': '暂未收录到可用原文链接（源清单仅含标题与日期），可在微信内搜索标题查看。',
    'mirror-link': '原始微信页面未返回正文，已自动匹配到「搜狐号」上的同一篇镜像文章，请复制下方链接在浏览器中打开。',
    'need-login': '需要微信登录后查看。',
    'blocked': '被平台限制访问。',
    'too-short': '抓取到的内容过短，可能页面不完整。',
    'not-fetched': '正文尚未抓取。',
}


# ---------------- 低层 XML 工具 ----------------
_BM_ID = [1000]

def add_bookmark(paragraph, name):
    _BM_ID[0] += 1
    bid = str(_BM_ID[0])
    start = OxmlElement('w:bookmarkStart')
    start.set(qn('w:id'), bid)
    start.set(qn('w:name'), name)
    end = OxmlElement('w:bookmarkEnd')
    end.set(qn('w:id'), bid)
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def _rpr(link=True, size=None, bold=False, color=None):
    rPr = OxmlElement('w:rPr')
    if link:
        c = OxmlElement('w:color'); c.set(qn('w:val'), '0B5CAD'); rPr.append(c)
        u = OxmlElement('w:u'); u.set(qn('w:val'), 'single'); rPr.append(u)
    else:
        if color:
            c = OxmlElement('w:color'); c.set(qn('w:val'), color); rPr.append(c)
        if bold:
            b = OxmlElement('w:b'); rPr.append(b)
    if size:
        sz = OxmlElement('w:sz'); sz.set(qn('w:val'), str(int(size * 2))); rPr.append(sz)
    return rPr


def add_internal_link(paragraph, anchor, text, size=None, bold=False):
    h = OxmlElement('w:hyperlink')
    h.set(qn('w:anchor'), anchor)
    r = OxmlElement('w:r')
    r.append(_rpr(True, size, bold))
    t = OxmlElement('w:t')
    t.set(qn('xml:space'), 'preserve')
    t.text = text
    r.append(t)
    h.append(r)
    paragraph._p.append(h)


def shade(paragraph, fill):
    pPr = paragraph._p.get_or_add_pPr()
    sh = OxmlElement('w:shd')
    sh.set(qn('w:val'), 'clear'); sh.set(qn('w:color'), 'auto'); sh.set(qn('w:fill'), fill)
    pPr.append(sh)


def set_doc_defaults(doc):
    st = doc.styles['Normal']
    st.font.name = '微软雅黑'
    st.font.size = Pt(10.5)
    st._element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')
    st.paragraph_format.space_after = Pt(4)
    st.paragraph_format.line_spacing = 1.4
    for nm, sz, col in (('Heading 1', 15, 'B0322A'), ('Heading 2', 12.5, '1F2328')):
        s = doc.styles[nm]
        s.font.name = '微软雅黑'
        s._element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')
        s.font.size = Pt(sz)
        s.font.bold = True
        s.font.color.rgb = RGBColor.from_string(col)


# ---------------- 组装 ----------------
def build(doc_path, title, subtitle, parts, notes):
    """parts: [{'name':..., 'items':[...]}] 每个 part 一个分节标题"""
    t0 = time.time()
    doc = Document()
    set_doc_defaults(doc)

    # ---- 封面 ----
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(title); r.font.size = Pt(30); r.bold = True
    r.font.color.rgb = RGBColor.from_string('B0322A')
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(subtitle); r.font.size = Pt(12); r.font.color.rgb = RGBColor.from_string('666666')
    doc.add_paragraph()
    total = sum(len(x['items']) for x in parts)
    ok = sum(1 for x in parts for i in x['items'] if i['status'] == 'ok')
    chars = sum(i['chars'] for x in parts for i in x['items'] if i['status'] == 'ok')
    for line in [
        f'共收录    {total} 篇',
        f'已取到正文 {ok} 篇（约 {chars/10000:.1f} 万字）',
        f'生成日期  {time.strftime("%Y-%m-%d")}',
    ]:
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(line); r.font.size = Pt(11.5); r.font.color.rgb = RGBColor.from_string('333333')
    doc.add_paragraph()
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run('提示：点击下方「目录」中的标题可跳转；也可用 Word「视图 → 导航窗格」按标题任意跳转。')
    r.font.size = Pt(9.5); r.font.color.rgb = RGBColor.from_string('888888')
    doc.add_page_break()

    # ---- 说明 ----
    doc.add_heading('使用说明与数据来源', level=1)
    for line in notes:
        pp = doc.add_paragraph(line)
        pp.paragraph_format.space_after = Pt(3)
    doc.add_page_break()

    # ---- 目录 ----
    doc.add_heading('目录', level=1)
    p = doc.add_paragraph('（点击标题直接跳转，蓝色为可点击链接）')
    p.runs[0].font.size = Pt(9); p.runs[0].font.color.rgb = RGBColor.from_string('888888')
    bm_map = {}
    seq = 0
    for part in parts:
        items = part['items']
        hp = doc.add_heading(f"{part['name']}（{len(items)} 篇）", level=2)
        # 分节标题也做书签
        sec_bm = f'SEC{len(bm_map)+1}'
        add_bookmark(hp, sec_bm)
        cur_y = None
        for it in items:
            seq += 1
            bm = f'B{seq:05d}'
            it['_bm'] = bm
            y = (it['date'] or '')[:4]
            if y != cur_y:
                cur_y = y
                yp = doc.add_paragraph()
                yr = yp.add_run(f'▍{y} 年')
                yr.bold = True; yr.font.size = Pt(10.5)
                yr.font.color.rgb = RGBColor.from_string('B0322A')
                yp.paragraph_format.space_before = Pt(7)
                yp.paragraph_format.space_after = Pt(2)
            tp = doc.add_paragraph()
            tp.paragraph_format.space_after = Pt(0)
            tp.paragraph_format.line_spacing = 1.25
            add_internal_link(tp, bm, f"{it['date']}　{it['title']}", size=9.5)
            if it['status'] != 'ok':
                rr = tp.add_run('　[镜像链接]' if it['status'] == 'mirror-link' else '　[未取到正文]')
                rr.font.size = Pt(8)
                rr.font.color.rgb = RGBColor.from_string('1D4ED8' if it['status'] == 'mirror-link' else 'B45309')
    doc.add_page_break()

    # ---- 正文 ----
    seq = 0
    for part in parts:
        items = part['items']
        if seq:
            doc.add_page_break()
        hp = doc.add_heading(part['name'], level=1)
        hp.runs[0].font.size = Pt(20)
        pp = doc.add_paragraph(part.get('desc', ''))
        pp.runs[0].font.size = Pt(10); pp.runs[0].font.color.rgb = RGBColor.from_string('777777')
        for it in items:
            doc.add_page_break()
            seq += 1
            h = doc.add_heading(it['title'], level=1)
            h.paragraph_format.space_before = Pt(0)
            add_bookmark(h, it['_bm'])
            # 元信息
            m = doc.add_paragraph()
            m.paragraph_format.space_after = Pt(10)
            bits = [it['date']]
            if it.get('author'):
                bits.append('署名：' + it['author'])
            bits.append('正文 ' + str(it['chars']) + ' 字' if it['status'] == 'ok' else '正文未获取')
            rr = m.add_run('　·　'.join(bits))
            rr.font.size = Pt(9); rr.font.color.rgb = RGBColor.from_string('888888')
            if it.get('url'):
                rr2 = m.add_run('　·　原文：' + it['url'])
                rr2.font.size = Pt(8.5); rr2.font.color.rgb = RGBColor.from_string('0B5CAD')
            if it['status'] == 'ok':
                for line in it['text'].split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('[图片]'):
                        bp = doc.add_paragraph()
                        r = bp.add_run('［图片］' + line.replace('[图片]', '').strip())
                        r.font.size = Pt(8.5); r.font.color.rgb = RGBColor.from_string('AAAAAA')
                        bp.paragraph_format.space_after = Pt(2)
                        continue
                    bp = doc.add_paragraph(line)
                    bp.paragraph_format.first_line_indent = Cm(0.74)
                    bp.paragraph_format.space_after = Pt(3)
            else:
                ism = it['status'] == 'mirror-link'
                bp = doc.add_paragraph()
                r = bp.add_run(('【可读镜像】' if ism else '【正文未能获取】') + WHY.get(it['status'], '未知原因。'))
                r.font.size = Pt(10.5)
                r.font.color.rgb = RGBColor.from_string('1D4ED8' if ism else 'B45309')
                shade(bp, 'EEF3FF' if ism else 'FDF6EC')
                if it.get('url'):
                    bp2 = doc.add_paragraph()
                    r2 = bp2.add_run('阅读链接：' + it['url'])
                    r2.font.size = Pt(9); r2.font.color.rgb = RGBColor.from_string('0B5CAD')

    os.makedirs(os.path.dirname(doc_path), exist_ok=True)
    doc.save(doc_path)
    size = os.path.getsize(doc_path) / 1024 / 1024
    print(f'[OK] {os.path.basename(doc_path)}  {total} 篇  有正文 {ok} 篇  '
          f'{chars/10000:.1f} 万字  {size:.1f} MB  {time.time()-t0:.0f}s')
    return dict(total=total, ok=ok, chars=chars, size=size)


if __name__ == '__main__':
    corpus = json.load(open(os.path.join(SRC, 'corpus.json'), encoding='utf-8'))
    print('语料条目:', len(corpus))

    def pick(acc):
        return [c for c in corpus if c['account'] == acc]

    def norm(items):
        out = []
        for c in items:
            out.append(dict(title=c['title'], date=c['date'], author=c.get('author', ''),
                            status=c['status'], chars=c['chars'], text=c['text'], url=c.get('url', '')))
        out.sort(key=lambda x: x['date'] or '')
        return out

    jb = norm(pick('九边'))
    lz = norm(pick('六镇'))
    lw = norm(pick('卢克文'))

    NOTES = [
        '一、收录范围',
        '  本文档收录「九边」大号、「六镇」（九边小号）与「卢克文工作室」三个微信公众号的文章篇目，按时间正序排列。',
        '',
        '二、怎么用',
        '  1. 最方便的方式：Word 顶部「视图 → 勾选『导航窗格』」，左侧会出现全部文章标题，点任意标题直接跳转。',
        '  2. 也可以回到前面的「目录」页，点击蓝色标题跳转。',
        '  3. 每篇文章都单独成页，并已打上书签（书签名为 B00001 递增）。',
        '',
        '三、正文覆盖说明',
        '  正文通过公开网页逐篇抓取，未使用任何账号凭证、不涉及登录态。',
        '  未能取到正文的篇目，多为推广软文（投放到期后被平台下架）或原文已被作者删除；',
        '  另有部分篇目在所有公开清单中都只有标题与日期、没有原文链接。',
        '  对于这些篇目，本文档做了两件事：① 用底色方框标注具体原因；② 若该篇在公开的镜像站点',
        '  （如搜狐号）能自动匹配到同一篇文章，则在标题后标「镜像链接」并给出可阅读的网址。',
        '  需要 100% 全量正文（含被删文章）时，只能用本人微信登录公众平台后借助开源工具导出，',
        '  这需要用户本人在场扫码，无法代为完成。',
        '',
        '四、其他交付物',
        '  · 文章全集阅读器.html —— 左侧点标题、右侧直接读全文（含搜索与筛选）',
        '  · 文章包（TXT）.zip —— 按账号分文件夹，一篇一个 txt 文件',
        '',
        '五、版权提示',
        '  全部文章版权归原作者「九边」「六镇」「卢克文工作室」所有。本文档仅为个人学习、检索与离线阅读之用，',
        '  请勿用于任何商业用途或二次传播。',
        '',
        '生成时间：' + time.strftime('%Y-%m-%d %H:%M'),
    ]

    build(os.path.join(OUTDIR, '九边（含小号六镇）文章全集.docx'),
          '九边 · 文章全集',
          '含大号「九边」与小号「六镇」│ 带书签 · 可点击目录 · 按时间正序',
          [
              dict(name='第一部分　九边（大号）', desc='九边本人的主阵地，含署名“九边 / 二号头目 / 跑哥”的文章。', items=jb),
              dict(name='第二部分　六镇（九边小号）', desc='九边的小号，多在小黑屋期间更新，文风与主号一致。', items=lz),
          ], NOTES)

    build(os.path.join(OUTDIR, '卢克文工作室文章全集.docx'),
          '卢克文工作室 · 文章全集',
          '公众号「卢克文工作室」全部篇目 │ 带书签 · 可点击目录 · 按时间正序',
          [dict(name='卢克文工作室', desc='卢克文本人署名文章 + 工作室作者团（仗剑观星、岱山、风雨如歌、云梦竹等）署名文章。', items=lw)],
          NOTES)
