---
name: scan-pdf-ocr
display_name: 扫描件PDF转文本
display_name_en: Scanned PDF OCR
description: Turn image-only PDFs into reliable text. Detect scanned/mixed pages, run local offline OCR (nothing leaves the machine), then structure the result. Use when a PDF yields no text, markitdown/pdfminer return empty, or PyMuPDF get_text() is empty. 处理无文本层扫描件 PDF——合同、盖章件、扫描存档、老资料；本地离线 OCR，内容不出本机，敏感合同可直接处理。触发词：扫描件、无文本层、OCR、合同扫描、盖章 PDF、图片型 PDF、识别不出来、scanned pdf、image-only pdf、ocr pdf.
description_zh: 把「提取不到一个字」的图片型 PDF 转成可校验的文本。自动识别纯扫描件与混合件，用本地离线 OCR（内容不出本机）提取全文，并给出形近字、金额、表格的质检清单与合同类结构化提取模板。适合合同、标书、盖章件、扫描存档。
description_en: Convert image-only PDFs into verifiable text. Auto-detects pure scans and mixed PDFs, runs fully local offline OCR (never uploads your files), and ships a checklist for homoglyph errors, figures and tables plus a contract-extraction template. Built for contracts, bids, stamped documents and scanned archives.
category: office
version: 1.0.0
author: 向东
agent_created: true
---

# 无文本层扫描件 PDF 处理

把「提取不到一个字」的 PDF 变成可用文本。**全程本地离线，内容不出本机**，因此合同、标书、财务资料等敏感件可直接处理，无需为数据外发征求授权。

## 零、安装依赖（首次使用）

```bash
pip install pymupdf rapidocr-onnxruntime
```

下文统一用 `$SKILL` 指代本 skill 的目录（如 `~/.workbuddy/skills/scan-pdf-ocr/`），命令均在 `$SKILL` 下执行。

## 一、先判定，别盲目 OCR

拿到 PDF 第一步永远是探测，而不是直接转：

```bash
cd $SKILL
python scripts/scan_ocr.py <pdf> --probe
```

输出会给出四种判定，对应不同走法：

| 判定 | 特征 | 走法 |
| --- | --- | --- |
| **纯扫描件** | 全文 0 字、每页都有图 | 本 skill：OCR |
| **混合件** | 部分页有文本层 | 本 skill：**默认自动模式**（有文本直抽，无文本才 OCR，省时间） |
| **有文本层** | 文本量正常 | **不用本 skill**——直接用 PyMuPDF / MarkItDown 提取即可 |
| **疑为空白/损坏** | 既无文本也无图片 | 先确认文件本身是否正常 |

> **注意 `--probe` 会列出「待 OCR 页」，混合件先看清范围再动手。**

## 二、三条路线，按优先级选

| 路线 | 适用 | 何时用 |
| --- | --- | --- |
| **① 本地离线 OCR（默认）** | 绝大多数情况，尤其**敏感件** | `rapidocr-onnxruntime`，ONNX CPU 推理，无网络请求。首选 |
| **② 读图（多模态）** | 页数少（<20）、且需要精确辨识表格结构 | 渲染 PNG 后直接读图。页数多时不经济 |
| **③ 外部 OCR 服务** | 仅当本地引擎不可用 | ⚠️ **会把未公开材料发到进程外，处理私有/受监管/未公开资料前必须先取得用户同意** |

路线 ① 与 ② 可**互相校验**：金额表、关键条款读图确认一遍。

## 三、标准操作

```bash
cd $SKILL

# 1) 先探测
python scripts/scan_ocr.py <pdf> --probe

# 2) 先试 3~5 页，确认 OCR 质量达标再全跑（省时间的关键）
python scripts/scan_ocr.py <pdf> --pages 1-5 --keep-images -o <输出目录>

# 3) 全量跑，多进程加速
python scripts/scan_ocr.py <pdf> --workers 8 -o <输出目录>
```

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `--pages` | `all` / `3` / `1-5` / `1,4,9` / `1-5,10`。**默认 all** |
| `--dpi` | 渲染清晰度，默认 200。**字迹模糊或字号小时提到 300**；仅正文大字号可降到 150 提速 |
| `--workers` | 并行进程数，建议 = CPU 核数。页多时提速明显 |
| `--keep-images` | 保留渲染 PNG 作人工复核证据。**涉及金额/表格的建议保留** |
| `--force-ocr` | 混合件也全部走 OCR（想统一口径时用） |
| `--min-chars` | 低产出页告警阈值，默认 30 字 |

**耗时参考**：A4 扫描件、dpi=200、单进程约 **5.7 s/页**（67 页 ≈ 6.4 分钟）；8 进程可压到 1/4 左右。

**输出**：`<输出目录>/<文件名>_OCR全文.txt`，按 `===== 第 N 页 =====` 分页，便于对照原件核对。

## 四、OCR 之后必做的四件事

**不要直接把 OCR 结果当事实用。** 详见 `references/ocr_quality_checklist.md`，核心四条：

1. **形近字纠错** —— 常见误识见对照表。人名、公司名、客体名称**引用前必须回原图核对**。
2. **金额与数字不可全信** —— 工程量清单、单价表、财务数字 OCR 极易错位，**必须回图逐格核对**。这是最容易出事故的地方。
3. **保留 OCR 全文** —— 全文 txt 另存归档，日后复核不必重新识别。
4. **个人信息脱敏** —— 扫描件常含联系人姓名、手机号、银行账号、税号。**一律不转述、不用于联系**；金额与合作条款按敏感信息管控。

## 五、结构化提取清单

提取完文本后，按文档类型套用：

### 通用文档四问
① 这份文件回答什么问题？ ② 关键数字有哪些？ ③ 谁在什么时候发布的？ ④ 能复用在哪（会议/规划/对外）？

### 合同类六块（最常用）

| 块 | 提取内容 |
| --- | --- |
| 1 当事人 | 各方全称、关系图、住所地、法定代表人 |
| 2 标的与期限 | 名称、范围、地点、工期 |
| 3 **价格** | 单价、计价单位、税率、阶梯加价（运距/规格）、调价机制 —— **最高价值块** |
| 4 资金与保证金 | 各类保证金比例、质保金、预付款、进度款节奏、账期 |
| 5 考核与罚则 | 考核指标、违约金分档、解除条件 |
| 6 附件 | 设备清单、技术标准、独立协议 |

> **技巧：合同总价栏空白时，用保证金比例反推。** 各类保证金若都写明了「占合同暂定总价 X%」，分别反推出的基数应当一致——**一致即说明这几个数字大概率识别正确**；不一致则必有漏识别，须回原图核对。这是给 OCR 结果做数字背书最快的一招。

> **同批多份同类文件时**：优先做**横向价格带对比表**，价值远高于单份笔记。

## 六、已知坑

- **`page.rotation` 不为 0**（常见 270°）无需手动摆正，`get_pixmap()` 会自动处理；若某页识别结果异常少，检查该页旋转或是否歪斜扫描。
- **RapidOCR 返回值结构**是 `[box, text, score]`，**text 是第 2 个元素**；score 是字符串不是 float，别当数值用。
- **Windows 路径**：传给 Python 的路径要用 `D:/...` 形式，MSYS 风格的 `/d/...` 会让 pymupdf 报 FileNotFoundError。
- **后台长任务**：`| tail` 会缓冲输出，看起来像卡住——进程实际在跑，等完成通知即可。
- **首次 import 慢**：ONNX 模型首次加载约 1~2 s，属正常。
- **识别不出就别硬撑**：若某页反复调 dpi 仍识别不佳，直接**读图**处理该页，比反复重试省时间。

## 七、与其他 skill 的分工

本 skill 的边界很明确：**只负责把扫描件变成可信文本，并做第一轮结构化提取。**

- 拿到的文本要入库、建档、做后续加工 → 交回你的其他工作流处理，本 skill 不越界。
- 前置判断若是「有文本层的 PDF」→ 用 MarkItDown 之类的常规转换工具即可，不必走 OCR。

## 八、实测基线（供调优参考）

在 A4 黑白扫描件、dpi=200、**单进程**条件下：约 **5.7 s/页**，67 页约 6.4 分钟。设 `--workers 8` 后可压到约 1/4。

首次调用会下载/加载 ONNX 模型，约 1~2 s，之后每页稳定。若你的机器更快或页更密，把 `--workers` 调到 CPU 核数即可。
