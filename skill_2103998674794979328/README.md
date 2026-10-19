# PDF 转换器 · 安装使用说明

> 把 PDF 转成 Word、Excel、PPT 或图片。保留原始排版、表格可编辑、插图不失真。全程在本机完成，文件不上传。

---

## 一、这是什么

一个 WorkBuddy 技能。安装后，你只需要用一句话告诉 WorkBuddy，它就会自动调用本技能完成转换：

| 你说 | 你得到 |
|------|--------|
| "把这份 PDF 转成 Word" | 可编辑的 .docx，标题层级、表格、插图、超链接都保留 |
| "提取这份 PDF 里的表格" | 可直接计算求和的 .xlsx（数字是数值，不是文本） |
| "把这份 PDF 转成 PPT" | .pptx，可选要点式 / 整页保真 / 原生表格三种版式 |
| "把这份 PDF 每页导出成图片" | 高清 PNG/JPG，分辨率可到印刷级 300dpi |

## 二、安装（三步，约 2 分钟）

**第 1 步：导入技能包**

打开 WorkBuddy → **技能管理** → **添加技能** → **上传技能** → 选中 `pdf-converter.zip`。导入后无需重启。

**第 2 步：安装依赖（仅首次）**

本技能需要几个 Python 库做格式转换。在终端执行一次即可：

```bash
pip install pdfplumber pypdfium2 pypdf pillow python-docx openpyxl python-pptx
```

> 只转图片的话，后三个库（python-docx / openpyxl / python-pptx）可以不装。
> 无需安装 Microsoft Office 或 LibreOffice，任何电脑都能跑。

**第 3 步：直接用**

对 WorkBuddy 说一句转换需求即可，例如上传 PDF 后说"转成 Word 发我"。

## 三、怎么用

### 方式 A：对话（推荐，不用记任何命令）

```
你：帮我把年度报告.pdf 转成 Word
WorkBuddy：✓ 转换完成，输出 report.docx（2 页，1 张表格，1 张图）
```

想要特定效果时，把要求说进句子里：

- "把表格提取成 Excel，要能求和的" → 自动启用表格模式
- "转成 PPT，要保持原来的样子" → 整页保真版式
- "第 3 到 5 页导出成 PNG，印刷用的" → 指定页 + 300dpi

### 方式 B：命令行（进阶，可批处理）

技能自带命令行工具，适合脚本化批量转换：

```bash
python <技能目录>/scripts/cli.py 输入.pdf --to word --out 输出.docx
python <技能目录>/scripts/cli.py 输入.pdf --to excel --out 输出.xlsx --mode table
python <技能目录>/scripts/cli.py 输入.pdf --to ppt --out 输出.pptx --layout image
python <技能目录>/scripts/cli.py 输入.pdf --to image --out ./pages --dpi 300
```

完整参数见技能目录下的 `references/api.md`。

## 四、常见问题

**转换后没有文字 / 提示疑似扫描件？**
这份 PDF 是扫描件（整页是图片，没有文字层）。本技能会保留页面图像，但要得到可编辑文字需要先做 OCR，建议用 OCR 工具处理后重新转换。

**加密的 PDF 能转吗？**
能。提供打开口令即可（WorkBuddy 对话里直接告诉它口令，或命令行加 `--password`）。口令只在本机内存中使用，不会保存。

**表格转 Excel 后列乱了？**
无边框的"伪表格"（如银行流水）建议改用版面模式还原。对话里说"用版面模式重新转一次"，或命令行加 `--mode layout`。

**合并单元格的复杂表格呢？**
可能被拆成普通网格，建议转完后在 Excel 里小调整。

## 五、隐私说明

- 所有转换**在本机完成**，不联网、不上传任何文件内容
- 只读取你指定的那个 PDF，只写入你指定的输出位置
- 脚本源码完全开放（`scripts/` 目录，共 3 个文件），可自行审查

## 六、版本与许可

- 当前版本：v1.0.0（2026-09-21）
- 作者：丰_Nexus
- 许可：MIT
- 问题反馈：联系技能作者

---

*本技能由 WorkBuddy Skill 体系构建，转换能力基于 pdfplumber / pypdfium2 / python-docx / openpyxl / python-pptx 等开源库。*
