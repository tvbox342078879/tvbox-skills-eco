"""
pdf_engine.py — PDF 解析核心层（共享模块，不直接对外调用）

职责：把一份 PDF 解析成中立的中间表示（IR），供 Word / Excel / PPT / 图片
四个写出器复用。所有对第三方 PDF 库的直接依赖都收敛在本文件，便于升级与排错。

设计要点：
  * 版面分析基于 pdfplumber 的 word / line / rect 几何信息，而不是纯文本流，
    因此可以保留分栏、缩进、段落边界。
  * 表格用 pdfplumber 的 extract_tables()，基于线条与文本对齐推断单元格。
  * 图片用 pdfplumber 的 page.images 取 bbox，再通过页面整页高倍渲染后裁剪
    得到清晰位图（比直接抽内嵌图更通用：可正确处理矢量图与蒙版图）。
  * 文字颜色不从 PDF 直接取（成本高、收益低），改为在写出阶段用启发式判断，
    详见 writer 模块。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

log = logging.getLogger("pdf_engine")

# 整页渲染用于裁剪图片时的倍率（相对 72dpi 的基准）。
# 3.0 ≈ 216 dpi，足以让裁剪出的插图清晰，同时不至于让内存爆炸。
CROP_RENDER_SCALE = 3.0


class PDFEngineError(RuntimeError):
    """解析阶段的统一异常类型。"""


# --------------------------------------------------------------------------- #
# 中间表示（IR）
# --------------------------------------------------------------------------- #

@dataclass
class TextBlock:
    """一个逻辑文本块（近似一个段落或一个标题）。"""
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float          # 主要字号（pt）
    bold: bool           # 是否粗体
    kind: str = "body"   # body | title | h1 | h2 | list
    column: int = 0      # 分栏索引，0 为最左栏


@dataclass
class TableData:
    """一张表格。rows 为二维字符串数组，首行视情况作为表头。"""
    rows: list[list[str]]
    x0: float
    top: float
    x1: float
    bottom: float
    has_header: bool = False


@dataclass
class ImageData:
    """一张从页面上裁剪出来的位图。"""
    image: Image.Image
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass
class PageIR:
    """单页的中间表示。"""
    number: int                                  # 1-based
    width: float
    height: float
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageData] = field(default_factory=list)
    # 页面级整页渲染图（懒加载，仅图片/PPT 写出器需要时生成）
    _rendered: Image.Image | None = None

    def render(self, scale: float = 2.0) -> Image.Image:
        """整页渲染为 PIL 图像。结果按 scale 缓存复用。"""
        if self._rendered is None:
            self._rendered = _render_page(self.number - 1, scale=scale)
        return self._rendered


@dataclass
class DocumentIR:
    """整份 PDF 的中间表示。"""
    path: str
    pages: list[PageIR]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def all_blocks(self) -> list[TextBlock]:
        return [b for p in self.pages for b in p.blocks]

    def all_tables(self) -> list[TableData]:
        return [t for p in self.pages for t in p.tables]


# --------------------------------------------------------------------------- #
# 页面渲染
# --------------------------------------------------------------------------- #

_open_docs: dict[str, pdfium.PdfDocument] = {}
_doc_passwords: dict[str, str | None] = {}


def _get_pdfium(path: str, password: str | None = None) -> pdfium.PdfDocument:
    """按路径缓存 PdfDocument（渲染页面很频繁，重复打开开销大）。"""
    doc = _open_docs.get(path)
    if doc is None:
        try:
            doc = pdfium.PdfDocument(path, password=password)
        except Exception as exc:  # noqa: BLE001
            hint = "（口令错误或缺失）" if password is not None else \
                   "（文件可能已加密，请用 --password 提供，或已损坏）"
            raise PDFEngineError(
                f"无法打开展示层渲染文档{hint}：{exc}") from exc
        _open_docs[path] = doc
        _doc_passwords[path] = password
    return doc


def _render_page(page_index: int, scale: float = 2.0, path: str | None = None) -> Image.Image:
    """用 pypdfium2 把指定页渲染成 PIL.Image（RGB）。"""
    if path is None:
        # 兼容单文档场景：取唯一缓存项
        if len(_open_docs) != 1:
            raise PDFEngineError("渲染前必须通过 open_document() 打开文档")
        path = next(iter(_open_docs))
    doc = _get_pdfium(path)
    try:
        page = doc[page_index]
        bitmap = page.render(scale=scale)
        return bitmap.to_pil().convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise PDFEngineError(f"渲染第 {page_index + 1} 页失败：{exc}") from exc


def render_page_image(path: str, page_index: int, scale: float = 2.0) -> Image.Image:
    """公开接口：渲染指定页为图像（0-based 索引）。"""
    return _render_page(page_index, scale=scale, path=path)


def close_document(path: str) -> None:
    """释放渲染层缓存。"""
    doc = _open_docs.pop(path, None)
    if doc is not None:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# 版面分析
# --------------------------------------------------------------------------- #

def _detect_columns(words: list[dict], page_width: float) -> list[float]:
    """通过词起点 x 的直方图猜测分栏边界。

    单栏文档会返回空列表；双栏文档会返回一条位于页面中部的分界位置。
    算法很保守——只在明显出现「中间空白带」时才判定为多栏，避免把
    缩进较深的单栏文档误判成两栏。
    """
    if not words:
        return []
    # 统计横向投影：把页面切成 40 个桶，看哪些桶几乎没有词起点
    buckets = 40
    hist = [0] * buckets
    for w in words:
        idx = min(buckets - 1, int(w["x0"] / page_width * buckets))
        hist[idx] += 1
    total = sum(hist) or 1
    # 找中间 1/3 区域里的空白谷
    lo, hi = buckets // 3, buckets * 2 // 3
    quiet = [i for i in range(lo, hi) if hist[i] <= total * 0.005]
    if len(quiet) >= 3:
        return [(quiet[len(quiet) // 2] + 0.5) / buckets * page_width]
    return []


def _group_words_into_lines(words: list[dict], tolerance: float = 2.5) -> list[list[dict]]:
    """按 top 坐标把词聚成行。"""
    if not words:
        return []
    words = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: list[list[dict]] = []
    current = [words[0]]
    for w in words[1:]:
        # 与当前行基准 top 的偏差在容差内，视为同一行
        if abs(w["top"] - current[0]["top"]) <= tolerance:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    lines.append(current)
    return lines


def _dominant_size(chars: list[dict]) -> float:
    """取一段文字里出现频次最高的字号（忽略极小噪声）。"""
    sizes = [round(c.get("size", 0), 1) for c in chars if c.get("size")]
    if not sizes:
        return 0.0
    counts: dict[float, int] = {}
    for s in sizes:
        counts[s] = counts.get(s, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _is_bold(chars: list[dict]) -> bool:
    """判断一段文字是否粗体：字体名含 Bold/Black/Heavy，或笔画明显加粗。"""
    if not chars:
        return False
    names = " ".join(str(c.get("fontname", "")) for c in chars).lower()
    if any(k in names for k in ("bold", "black", "heavy", "semibold", "demi")):
        return True
    return False


def _classify_block(block: TextBlock, body_size: float) -> str:
    """用字号与长度启发式判断块类型。

    判断顺序很重要：短文本 + 大字号 → 标题；短文本 + 带项目符号 → 列表项；
    其余归为正文。
    """
    text = block.text.strip()
    if not text:
        return "body"
    if body_size and block.size >= body_size * 1.45 and len(text) <= 60:
        return "h1"
    if body_size and block.size >= body_size * 1.18 and len(text) <= 80:
        return "h2"
    if text[0] in "•·▪◦‣∙-–*" or (len(text) > 2 and text[:2] in ("(1", "1.", "2.", "3.")):
        return "list"
    return "body"


def _bbox_in(inner: tuple[float, float, float, float],
             outer: tuple[float, float, float, float]) -> bool:
    """判断 inner 是否基本落在 outer 内（允许 1pt 误差）。"""
    ix0, it, ix1, ib = inner
    ox0, ot, ox1, ob = outer
    return ix0 >= ox0 - 1 and it >= ot - 1 and ix1 <= ox1 + 1 and ib <= ob + 1


def _overlap_ratio(a: tuple[float, float, float, float],
                   b: tuple[float, float, float, float]) -> float:
    """a 被 b 覆盖的面积比例。"""
    ax0, at, ax1, ab = a
    bx0, bt, bx1, bb = b
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ab, bb) - max(at, bt))
    inter = iw * ih
    area = max(1e-6, (ax1 - ax0) * (ab - at))
    return inter / area


def _extract_images(page, path: str, page_index: int,
                    table_boxes: list[tuple], text_boxes: list[tuple]) -> list[ImageData]:
    """裁剪页面上的图片区域。

    策略：先按 page.images 的 bbox 找出候选区域，再剔除
    「与表格重叠」或「被大量文字覆盖」的伪区域（这类通常是背景色块或
    文字下的浅色底纹）。最后整页高倍渲染后按 bbox 裁剪，保证清晰度。
    """
    raw = getattr(page, "images", None) or []
    candidates: list[tuple[float, float, float, float]] = []
    for img in raw:
        box = (img["x0"], img["top"], img["x1"], img["bottom"])
        w, h = box[2] - box[0], box[3] - box[1]
        # 过小的区域通常是装饰性色块，直接忽略
        if w < 12 or h < 12:
            continue
        # 与表格重叠超过 60% 的区域交给表格处理
        if any(_overlap_ratio(box, tb) > 0.6 for tb in table_boxes):
            continue
        # 被文字大量覆盖的区域（>50%）判定为底纹而非插图
        covered = sum(_overlap_ratio(box, tx) for tx in text_boxes)
        if covered > 0.5:
            continue
        candidates.append(box)

    if not candidates:
        return []

    # 合并高度重叠的候选框（PDF 里一张图常被切成多个 XObject）
    merged: list[tuple[float, float, float, float]] = []
    for box in sorted(candidates, key=lambda b: (b[1], b[0])):
        for i, m in enumerate(merged):
            if _overlap_ratio(box, m) > 0.7 or _overlap_ratio(m, box) > 0.7:
                merged[i] = (min(box[0], m[0]), min(box[1], m[1]),
                             max(box[2], m[2]), max(box[3], m[3]))
                break
        else:
            merged.append(box)

    full = render_page_image(path, page_index, scale=CROP_RENDER_SCALE)
    sx = full.width / float(page.width)
    sy = full.height / float(page.height)

    out: list[ImageData] = []
    for (x0, t, x1, b) in merged:
        px0 = max(0, int(x0 * sx))
        pt_ = max(0, int(t * sy))
        px1 = min(full.width, int(x1 * sx))
        pb = min(full.height, int(b * sy))
        if px1 - px0 < 10 or pb - pt_ < 10:
            continue
        out.append(ImageData(image=full.crop((px0, pt_, px1, pb)),
                             x0=x0, top=t, x1=x1, bottom=b))
    return out


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #

def analyze(path: str, *, max_pages: int | None = None,
            detect_tables: bool = True,
            extract_images: bool = True,
            password: str | None = None) -> DocumentIR:
    """解析 PDF，返回 DocumentIR。

    参数：
      path            输入 PDF 路径
      max_pages       仅解析前 N 页（None 表示全部）
      detect_tables   是否做表格识别（关闭可提速，适合纯文字文档转 Word）
      extract_images  是否裁剪图片（关闭可提速，纯文字场景无需）
      password        加密 PDF 的打开口令

    抛出：PDFEngineError（文件不存在 / 加密 / 损坏）
    """
    import os
    if not os.path.isfile(path):
        raise PDFEngineError(f"输入文件不存在：{path}")

    # 预打开一次，尽快暴露加密/损坏问题，并建立渲染缓存
    try:
        _get_pdfium(path, password=password)
    except PDFEngineError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PDFEngineError(f"无法打开 PDF：{exc}") from exc

    pages: list[PageIR] = []
    try:
        with pdfplumber.open(path, password=password) as pdf:
            n = len(pdf.pages)
            limit = n if max_pages is None else min(n, max_pages)
            for i in range(limit):
                try:
                    pages.append(_analyze_page(pdf, i, path, detect_tables, extract_images))
                except Exception as exc:  # noqa: BLE001
                    # 单页失败不影响整份文档：记录空页并继续
                    log.warning("第 %d 页解析失败，已跳过：%s", i + 1, exc)
                    p = pdf.pages[i]
                    pages.append(PageIR(number=i + 1,
                                        width=float(p.width), height=float(p.height)))
    except PDFEngineError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PDFEngineError(f"解析 PDF 失败：{exc}") from exc

    meta = _read_metadata(path)
    return DocumentIR(path=path, pages=pages, metadata=meta)


def _read_metadata(path: str) -> dict[str, Any]:
    """用 pypdf 读取文档元信息（标题/作者等），失败时返回空字典。"""
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        md = r.metadata or {}
        return {
            "title": str(md.get("/Title", "") or ""),
            "author": str(md.get("/Author", "") or ""),
            "subject": str(md.get("/Subject", "") or ""),
            "producer": str(md.get("/Producer", "") or ""),
            "page_count": len(r.pages),
        }
    except Exception:  # noqa: BLE001
        return {}


def _analyze_page(pdf, page_index: int, path: str,
                  detect_tables: bool, extract_images: bool) -> PageIR:
    """解析单页。"""
    page = pdf.pages[page_index]
    width, height = float(page.width), float(page.height)
    pir = PageIR(number=page_index + 1, width=width, height=height)

    # ---- 表格先解析，后面用它排除图片误检 ----
    table_boxes: list[tuple[float, float, float, float]] = []
    if detect_tables:
        try:
            for t in page.find_tables():
                rows = t.extract()
                if not rows:
                    continue
                clean = [["" if c is None else str(c).replace("\n", " ").strip()
                          for c in row] for row in rows]
                # 丢掉完全空白的表
                if not any(any(c for c in row) for row in clean):
                    continue
                x0, top, x1, bottom = t.bbox
                box = (float(x0), float(top), float(x1), float(bottom))
                table_boxes.append(box)
                pir.tables.append(TableData(
                    rows=clean, x0=box[0], top=box[1], x1=box[2], bottom=box[3],
                    has_header=_looks_like_header(clean),
                ))
        except Exception as exc:  # noqa: BLE001
            log.warning("第 %d 页表格识别失败：%s", page_index + 1, exc)

    # ---- 文本 ----
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False,
                               extra_attrs=["size", "fontname"]) or []
    col_bounds = _detect_columns(words, width)

    def column_of(x: float) -> int:
        return sum(1 for c in col_bounds if x >= c)

    # 排除表格区域的词，避免表格文字被重复写入正文
    body_words = []
    for w in words:
        wb = (w["x0"], w["top"], w["x1"], w["bottom"])
        if any(_overlap_ratio(wb, tb) > 0.5 for tb in table_boxes):
            continue
        body_words.append(w)

    lines = _group_words_into_lines(body_words)

    # 用「字符数加权」的字号众数作为正文字号基准。
    # 不能用词数中位数：中文没有空格，一个段落就是一个词，
    # 词数中位数会被标题字号带偏；而正文的字符总量通常远大于标题。
    from collections import Counter
    char_weights: Counter = Counter()
    for w in body_words:
        s = round(w.get("size", 0), 1)
        if s:
            char_weights[s] += len(w.get("text", ""))
    body_size = char_weights.most_common(1)[0][0] if char_weights else 0.0

    # 把行合并成块：同栏 + 行距正常 + 不是新标题 → 归入同一段
    raw_blocks: list[TextBlock] = []
    for line in lines:
        text = " ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"])).strip()
        if not text:
            continue
        x0 = min(w["x0"] for w in line)
        x1 = max(w["x1"] for w in line)
        top = min(w["top"] for w in line)
        bottom = max(w["bottom"] for w in line)
        size = max((w.get("size", 0) or 0) for w in line)
        bold = _is_bold([{"fontname": w.get("fontname", "")} for w in line])
        col = column_of(x0)

        if raw_blocks:
            prev = raw_blocks[-1]
            gap = top - prev.bottom
            # 合并条件：同栏、垂直间距小于 1.2 倍行高、字号接近、前块未以句末结束的概率高
            line_h = max(bottom - top, 1.0)
            same_col = prev.column == col
            close = -2 <= gap <= line_h * 1.2
            same_size = abs(prev.size - size) < 0.6
            if same_col and close and same_size and prev.kind == "body":
                prev.text = (prev.text + " " + text).strip()
                prev.x1 = max(prev.x1, x1)
                prev.bottom = max(prev.bottom, bottom)
                continue

        raw_blocks.append(TextBlock(text=text, x0=x0, top=top, x1=x1, bottom=bottom,
                                    size=size, bold=bold, column=col))

    # 分类块类型
    for b in raw_blocks:
        b.kind = _classify_block(b, body_size)
    pir.blocks = raw_blocks

    # ---- 图片 ----
    if extract_images:
        try:
            text_boxes = [(b.x0, b.top, b.x1, b.bottom) for b in raw_blocks]
            pir.images = _extract_images(page, path, page_index, table_boxes, text_boxes)
        except Exception as exc:  # noqa: BLE001
            log.warning("第 %d 页图片提取失败：%s", page_index + 1, exc)

    return pir


def _looks_like_header(rows: list[list[str]]) -> bool:
    """判断首行是否为表头：首行每格都非空，且与第二行宽度/类型有差异。"""
    if len(rows) < 2:
        return False
    first = rows[0]
    second = rows[1]
    if not first or any(not str(c).strip() for c in first):
        return False
    # 首行全部是短文本、且第二行出现数字 → 大概率是表头
    first_short = all(len(str(c)) <= 24 for c in first)
    second_numeric = sum(1 for c in second if _is_numberish(c))
    return first_short and second_numeric >= max(1, len(second) // 3)


def _is_numberish(v: Any) -> bool:
    s = str(v).strip().replace(",", "").replace("%", "").replace("¥", "").replace("$", "")
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def image_to_bytes(img: Image.Image, fmt: str = "PNG", quality: int = 95) -> bytes:
    """把 PIL 图像编码为字节流，供写入 Office 文档使用。"""
    buf = io.BytesIO()
    f = fmt.upper()
    if f in ("JPG", "JPEG"):
        img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    else:
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
