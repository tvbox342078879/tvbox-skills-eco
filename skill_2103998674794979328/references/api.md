# API 参考

本文档面向需要深入定制或嵌入式调用的场景。常规转换直接用 `cli.py` 即可。

## 解析层 pdf_engine.py

### analyze(path, *, max_pages=None, detect_tables=True, extract_images=True, password=None) -> DocumentIR

把 PDF 解析为 DocumentIR。所有异常统一抛 `PDFEngineError`。

| 参数 | 说明 |
|------|------|
| `path` | 输入 PDF 绝对路径 |
| `max_pages` | 只解析前 N 页（提速用） |
| `detect_tables` | 关闭后跳过表格识别（纯文字转 Word 时可提速约 40%） |
| `extract_images` | 关闭后跳过图片提取（不需要插图时使用） |
| `password` | 加密 PDF 的口令，同时用于渲染层与解析层 |

### DocumentIR 结构

```
DocumentIR
├── path: str                    # 源文件路径
├── metadata: dict               # title/author/subject/producer/page_count
│                                # （Word 转换时 cli 会额外注入 "links"）
└── pages: list[PageIR]
    ├── number / width / height  # 页码（1-based）与页面尺寸（pt）
    ├── blocks: list[TextBlock]  # 文本块（近段落粒度）
    │     .text / .size / .bold
    │     .kind: body | h1 | h2 | list
    │     .column: int           # 分栏索引
    ├── tables: list[TableData]  # 表格
    │     .rows: list[list[str]] # 二维网格（已清洗空白）
    │     .has_header: bool      # 表头判定结果
    └── images: list[ImageData]  # 插图（PIL.Image + bbox）
```

### 其他函数

- `render_page_image(path, page_index, scale=2.0)` — 渲染整页为 PIL Image（0-based 索引）。
- `close_document(path)` — 释放渲染层文档缓存。CLI 在退出前自动调用。
- `image_to_bytes(img, fmt="PNG", quality=95)` — PIL 图像编码为字节流。

## 写出层 writers.py

所有写出器签名：`write_xxx(doc: DocumentIR, out_path: str, **options) -> dict`，返回统计信息字典，失败抛 `ConversionError`。

### write_word(doc, out_path, *, include_images=True, include_tables=True, page_break_between_pages=True, font_ascii="Calibri", font_cjk="微软雅黑")

返回 `{paragraphs, tables, images, links, output}`。超链接附录只在 `doc.metadata["links"]` 非空时生成。

### write_excel(doc, out_path, *, mode="auto", sheet_per="table", include_text=True, numbers_as_number=True, header_fill="2563EB")

返回 `{sheets, tables, rows, mode, output}`。

- `mode`：`auto` / `table` / `text` / `layout`。`table` 模式下无表格会抛错并建议改用 `layout`。
- `numbers_as_number=True` 时，千分位数字转 int/float，百分比转小数并设 `0.0%` 格式。

### write_pptx(doc, out_path, *, layout="text", slide_size="auto", title_from="heading", max_bullets=14)

返回 `{slides, tables, images, layout, output}`。

- `slide_size`：`auto`（沿用 PDF 首页宽高比）/ `16:9` / `4:3`。
- 项目符号字号按条目数自适应（20/16/13pt），超出 `max_bullets` 会折行提示。

### write_images(doc, out_path, *, fmt="PNG", dpi=200, pages=None, jpg_quality=95, crop_images=False)

返回 `{images, dpi, format, files, output 或 output_dir}`。

- `out_path` 支持 `{page}` 占位符；目录输入自动按页命名；单页 PDF 可直接指定完整文件名。
- `crop_images=True` 时忽略整页导出，改为把 `doc.pages[i].images` 写到输出目录旁的 `_images/`。
- `fmt` 支持 PNG / JPG / WEBP / TIFF；`dpi` 限制在 36–1200。

## CLI 退出码约定

| 码 | 含义 | 典型场景 |
|----|------|---------|
| 0 | 成功 | |
| 1 | 参数错误 | 页码越界、mode 拼写错误 |
| 2 | 解析失败 | 文件不存在、加密、损坏 |
| 3 | 转换失败 | 依赖缺失、输出目录不可写 |
| 4 | 成功但存疑 | 扫描件、表格与图片重叠疑似重复 |

`--json` 模式返回 `{"ok": bool, "stats": {...}, "warnings": [...]}` 或 `{"ok": false, "error": "...", "code": N}`。

## 解析策略说明（调参参考）

- **正文字号基准**：按字符数加权的字号众数（中文无空格分词，词数中位数会被标题带偏）。
- **标题判定**：h1 = 字号 ≥ 1.45×正文 且 ≤ 60 字符；h2 = ≥ 1.18×正文 且 ≤ 80 字符；带项目符号的行归 list。
- **段落合并**：同栏、行距 ≤ 1.2 倍行高、字号差 < 0.6pt 的相邻行合并为一段。
- **分栏检测**：只在页面横向投影出现明显空白带（中间 1/3 区域）时判定多栏，避免误判深缩进。
- **表格**：pdfplumber 线条检测；表头 = 首行全非空且短、次行含 ≥ 1/3 数字。
- **图片**：`page.images` bbox 过滤（< 12pt 的装饰块、与表格重叠 > 60%、被文字覆盖 > 50% 的区域），3 倍渲染后裁剪。
