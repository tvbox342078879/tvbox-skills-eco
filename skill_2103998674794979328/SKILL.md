---
name: pdf-converter
display_name: PDF 转换器
display_name_en: PDF Converter
description: PDF 格式转换技能，支持 PDF 转 Word（.docx）、PDF 转 Excel（.xlsx）、PDF 转 PPT（.pptx）、PDF 转图片（PNG/JPG/WEBP/TIFF）。转换时保留原始排版层级（标题/正文/列表）、表格结构（转为可编辑原生表格）、插图清晰度与超链接。当用户要求把 PDF 转成 Word/Excel/PPT/图片、提取 PDF 中的表格数据、或将 PDF 页面导出为图像时使用此技能。
description_zh: 把 PDF 转成 Word、Excel、PPT 或图片，保留原始排版、表格结构与插图清晰度，全程本地转换不上传文件。
description_en: Convert PDF to Word, Excel, PPT, or images while preserving layout, table structure, and image clarity. All processing is local, with no file uploads.
category: productivity-tools
version: 1.0.0
author: 丰_Nexus
license: MIT
agent_created: true
---

# PDF Converter

> **v1.0.0** · 本地离线转换 · 不联网、不上传文件

## Overview

本技能把 PDF 转换为 Word / Excel / PPT / 图片四种格式。核心是一个两阶段管线：

1. **解析层**（`scripts/pdf_engine.py`）：把 PDF 解析为中立中间表示（IR）——文本块（含字号、粗体、分栏）、表格（单元格网格 + 表头判定）、图片（高倍渲染后按 bbox 裁剪）、文档元数据。
2. **写出层**（`scripts/writers.py`）：四个写出器把 IR 分别写成 docx / xlsx / pptx / 图片，尽量保留原始排版。

`scripts/cli.py` 是统一命令行入口，含参数解析、错误处理与质量自检。**优先通过 CLI 调用**；需要嵌入到其他 Python 代码时才直接 import。

## 权限与数据声明

使用前请确认以下边界，本技能的行为范围完全由此界定：

| 项目 | 说明 |
|------|------|
| **网络访问** | **无**。全部转换在本地完成，不发起任何网络请求，不上传任何文件内容 |
| **文件读取** | 仅读取命令行指定的 PDF 文件，以及 `pdf_engine` 缓存实例所需的重复读取；不扫描目录、不遍历其他文件 |
| **文件写入** | 仅写入 `--out` 指定的输出路径（及其父目录）；`--crop-images` 会写入输出文件旁的 `_images/` 子目录 |
| **系统调用** | **无**。不执行 shell 命令、不修改系统配置、不访问注册表 |
| **凭据收集** | **无**。`--password` 仅作为内存中的参数传给 PDF 解析库用于解密，不落盘、不外发 |
| **凭据使用** | 如需打开加密 PDF，用户需主动提供口令；不提供则明确报错退出，不会尝试猜测 |
| **敏感数据** | PDF 内容全程留在本机内存与指定输出文件中，无外发通道 |

脚本源码完全开放可审（`scripts/` 下共 3 个文件），未做任何混淆或动态代码加载。

## 依赖清单

首次使用前安装（仅需一次）。版本为**实测通过**的版本，`>=` 为兼容下限：

```bash
pip install \
  "pdfplumber>=0.11" \
  "pypdfium2>=4.30" \
  "pypdf>=5.0" \
  "Pillow>=10.0" \
  "python-docx>=1.1" \
  "openpyxl>=3.1" \
  "python-pptx>=1.0"
```

| 依赖 | 实测版本 | 用途 | 必需性 |
|------|---------|------|--------|
| `pdfplumber` | 0.11.10 | 版面解析（文本/表格/图片 bbox） | 必需 |
| `pypdfium2` | 5.13.0 | 页面渲染为位图（图片导出、插图裁剪） | 必需 |
| `pypdf` | 6.19.0 | 文档元数据、超链接注释提取 | 必需 |
| `Pillow` | 12.3.0 | 图像编解码与裁剪 | 必需 |
| `python-docx` | 1.2.0 | 写出 .docx | 仅 PDF→Word 需要 |
| `openpyxl` | 3.1.5 | 写出 .xlsx | 仅 PDF→Excel 需要 |
| `python-pptx` | 1.0.2 | 写出 .pptx | 仅 PDF→PPT 需要 |

说明：三个 Office 写出库按需导入——只做 PDF→图片时无需安装它们。缺失时错误信息会附带可直接执行的安装命令。

**环境**：Python ≥ 3.9（实测 3.13.14，Windows / macOS / Linux 通用）。无需 LibreOffice、Microsoft Office 或任何外部可执行文件。

## Quick Start

标准调用方式（把 `<skill-dir>` 替换为本技能目录）：

```bash
PY="<managed-python>"   # 使用 WorkBuddy 托管 Python 的绝对路径
S="<skill-dir>/scripts"

# PDF → Word（保留标题层级、原生表格、插图、超链接）
"$PY" "$S/cli.py" report.pdf --to word --out report.docx

# PDF → Excel（表格转为可编辑工作表，数字自动数值化）
"$PY" "$S/cli.py" data.pdf --to excel --out data.xlsx --mode table

# PDF → PPT（text=要点式 / image=整页图保真 / table=原生表格）
"$PY" "$S/cli.py" deck.pdf --to ppt --out deck.pptx --layout image

# PDF → 图片（300dpi 适合印刷，600dpi 适合再 OCR）
"$PY" "$S/cli.py" doc.pdf --to image --out ./pages --dpi 300 --format PNG

# 只转指定页 / 加密 PDF / 导出页面内插图
"$PY" "$S/cli.py" doc.pdf --to word --out o.docx --pages 1-3,5
"$PY" "$S/cli.py" enc.pdf --to excel --out o.xlsx --password secret123
"$PY" "$S/cli.py" doc.pdf --to image --out pg.png --crop-images
```

输出缺省时与输入同目录同名；`--json` 输出机器可读结果。退出码：0 成功；1 参数错误；2 解析失败（不存在/加密/损坏）；3 转换失败（依赖缺失/写出异常）；4 成功但结果存疑（如扫描件）。

## Format-Specific Guidance

### PDF → Word

- 标题映射到 Word 的 Heading 1/2（保留导航窗格大纲），表格写为原生可编辑表格，图片按原始版面比例内嵌，超链接自动收集为文末「链接与引用」附录。
- 默认中文字体为微软雅黑（西文 Calibri），可通过直接调用 `write_word(font_cjk=...)` 修改。
- 分栏文档会按阅读顺序还原；`--no-page-break` 可去掉分页符得到连续文档。

### PDF → Excel

`--mode` 是关键参数，按文档特征选择：

| 模式 | 适用场景 |
|------|---------|
| `auto`（默认） | 有表格用表格模式，否则自动切换 layout |
| `table` | 有完整边框线的标准表格（财务报表、对账单） |
| `layout` | 无边框、靠对齐排版的伪表格（银行流水、工资条） |
| `text` | 只要文本内容，不需要表格结构 |

数字（含千分位、百分比）自动转为数值类型，可直接参与计算；`--keep-text-format` 保留文本格式（工号、账号场景）。每张表格独立工作表，表头自动加冻结与配色。

### PDF → PPT

`--layout` 三选一：`text`（默认，提取标题+要点，适合文字型报告）、`image`（整页渲染铺满幻灯片，版式 100% 保真，适合 PPT 导出的 PDF 或图文混排文档）、`table`（检测到表格的页用原生表格呈现）。幻灯片尺寸自动沿用 PDF 首页宽高比。

### PDF → 图片

- 分辨率由 `--dpi` 控制：200（屏幕）/ 300（印刷）/ 600（再 OCR）。
- 多页自动按页码命名输出；`--out` 支持 `{page}` 占位符（如 `shot_{page}.png`）。
- `--crop-images` 只导出页面内的插图（输出到 `_images/` 子目录），适合收集报告里的图表素材。

## Error Handling

CLI 已统一处理以下场景，转述错误时保留给出的修复建议：

- **文件不存在 / 非 PDF** → exit 2，提示检查路径。
- **加密 PDF** → 未带 `--password` 时报「Incorrect password error」，提示用户提供口令后重试。
- **扫描件（无文字层）** → 转换仍会完成（页面以图像形式保留），但 exit 4 并提示「疑似扫描件，如需可编辑文字请先 OCR」。**务必把这个警告转述给用户**，不要当作成功隐瞒。
- **依赖缺失** → 错误信息附带 `pip install` 命令，直接执行即可修复。
- **单页解析失败** → 跳过该页继续，不中断整体转换。

## Programmatic API

需要在代码中嵌入时（同一目录下）：

```python
from pdf_engine import analyze
from writers import write_word, write_excel, write_pptx, write_images

doc = analyze("input.pdf")                       # 解析为 IR
stats = write_word(doc, "output.docx")           # 或其他三个写出器
# doc.pages[i].blocks / .tables / .images 可直接访问解析结果
```

各写出器的完整参数说明见 @references/api.md；IR 数据结构与解析策略也记录在该文件中。

## Known Limitations

- 扫描件无文字层，转换结果不含可编辑文本（会触发 exit 4 警告）；需先用 OCR 工具处理。
- 复杂嵌套表格（合并单元格跨行跨列）可能被拆平为普通网格。
- 文字颜色、艺术字等纯视觉属性不保留；PDF 表单域（AcroForm）内容不在本技能范围内。
- 图片提取采用「整页渲染 + bbox 裁剪」策略，矢量图、蒙版图可正确导出，但与文字大量重叠的装饰底纹会被有意过滤。

## 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-09-21 | 首个版本。支持 PDF→Word/Excel/PPT/图片；含解析层 IR、四种 Excel 模式、三种 PPT 版式、加密 PDF 与扫描件处理 |
