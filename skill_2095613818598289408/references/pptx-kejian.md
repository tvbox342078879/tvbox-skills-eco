# 课件出件契约

写页 JSON / `upsert_slide` **之前**读本文件。课型与文案在 `SKILL.md` / `outlines.md`。字段以 `zagens-office schema write pptx` 为准。

**禁止** `load_skill office-pptx` 做课件。那是路演 / 公司介绍；课件纪律以本文件为准。

## 1. 课件默认（不要做成咨询稿）

- 主题默认 `academic`。小学低年级、家长会可用 `warm`。不要用 `consulting`、营销风插画底。
- `cover:false`，封面自绘 `slides[0]`，常用 `"title_style":{"engine":false}`。整份 deck **不要**再写 `top_bar`（会套到自绘封面）。若某页自绘顶栏，`top_bar_height_pt` ≥ `40 + 1.25 × 标题字号`（磅），否则白字会落在白底上。
- `include_end:false`（不要 Thank You，除非老师要）。
- 写了 `master` 必须 `"page_number": true`（一旦写 `master`，页码默认 false）。
- 标题用教学语言（「用数轴比较分数」），不用咨询腔（「核心洞察」）。
- 每页 1 个核心信息、≤5 条要点。相邻页不要同一种「标题 + 三条 bullet」。
- **默认图文并茂。** 封面 / 导入 / 新知 / 例题尽量有图。引擎只吃本地 `path`，CLI **不抓** `url`。
- 公式、表格、图标、卡片都是一等元件：`math` / `table` / `qrcode` / `code_block` / `kpi_row` / `icon` / `chart` / `flow`（`schema write pptx` 已全部枚举，可自证）。
- 公式用 `type:"math"`，**字段名 `latex`**（LaTeX 子集 → 图，非正式 OMML）。化学式下标 `baseline:"subscript"`。

### 公式（`math`）

```json
{"type":"math","x_pct":0.10,"y_pct":0.30,"w_pct":0.35,"h_pct":0.22,"latex":"\\frac{1}{2}"}
```

- **已支持**：`\frac{}{}`（真上下分式）、`\sqrt{}`（根号）、`^` `_` 上下标、四则与关系运算符、希腊字母（`\alpha` `\beta` `\pi`…）、常用符号（`\times` `\leq` `\geq` `\pm` `\sum` `\int` `\infty` `\to` `\cdot` `\ldots`…）。
- **红线**：不支持的命令（`\hat` `\vec` `\begin`…）会保留字面文本并回 `warnings[].code=math_unsupported_command` —— 看到该告警就改写法，或改用预渲染图（`path`），别直接交稿。
- **版本**：课件出件要求引擎 **≥ 0.14.2**（0.14.1 修好了 `\frac` / `\sqrt`；0.14.2 起输出父目录自动创建）。低于则先按根技能升级，不要在旧引擎上拆分式、改图片凑合。
- **公式页必须打开文件目视**：`preflight` 几何正常、`view svg` 只画 bbox，都查不出公式错。

### 写入期硬闸门（公开课 / 说课默认开）

`upsert_slide` 可带 `"assert":{"forbid":["layout_text_overflow","layout_overflow_bottom"],"mode":"fail"}`：违约返回 `ok:false` / `code=contract_violated` 且**文件不落盘**；内部草稿用 `"mode":"warn"`。**不要**把 `layout_overlap` 放进 `forbid`：标题和页眉色条每页都会报两条，`mode:fail` 会整页不落盘。叠层看 `warnings`，卡片叠层且「可修 0」则忽略。

### 视觉工具箱（公开课层次感）

| 想要的效果 | 写法 |
|-----------|------|
| 卡片 / 标注框 | `textbox` + `fill` + `radius` + `shadow:true` + `padding` |
| 强调数字 | `kpi_row`（`cards:[{value,label,fill}]`） |
| 图标（对勾 / 星标 / 齿轮…） | `icon` + `name:"check-circle-fill"`（Bootstrap Icons id） |
| 半透明叠在图上 | 同上 + `opacity:0.85` |
| 立体块 | `shape` + `shape_3d` / `rotation_3d` |

## 配图（确认大纲之后、upsert 之前）

老师给了本地图就用老师的。否则**按这一页需要什么图来选**，不要默认搜图，也不要默认生成。不要用浏览器或临时脚本抓图。

1. **必须精确：** 数轴、刻度、公式、对照表用 `shape` / `math` / `table`。这类图不搜，也不交给画图模型。
2. **教学插画 / 情境：** 没有现成照片、或要按本课构图时，用当前宿主的画图 / ImageGen。提示写清对象、浅底、横版；不要大段水印字，不要真人学生脸，不要营销风。保存到 `deliverables/assets/kejian/`，看文件头（JPEG `ffd8ff` / PNG `89504e47`）再 `Read` 目视。宿主没有画图工具时，改用示意形状，不要改走临时脚本。
3. **真实照片：** 文物、地标、物种、器材、货币等必须对得上实物时，用本机 **`zagens-image`**。探测顺序与出件引擎相同：先 `zagens-image --version`，再 `~/.zagens-pro/bin/`，再 npm 全局 `node_modules/zagens-image/vendor/`。没有版本号时按根 `SKILL.md` 处理；本文件不写安装命令。只有这一页选了真实照片才探测搜图引擎。未装或搜索为空，就改用 `shape`+`textbox`，并在该页 `notes` 写「配图未就绪」。
4. **搜图参数：** 课件会给学生或家长看，只用 Wikimedia，且 `license` 为 `commercial`。查询保留 2–4 个英文实体词，横版和宽度用字段表达，不要把「高清 / 横版」写进查询。示例：

```json
{
  "query": "Chinese coin",
  "sources": ["wikimedia"],
  "limit": 8,
  "min_width": 1200,
  "orientation": "landscape",
  "license": "commercial"
}
```

先把上面的 JSON 写到 `.zagens/image-search.json`，执行 `zagens-image search --input-file .zagens/image-search.json`。再把选中的完整候选写到 `.zagens/image-download.json`，`out_dir` 用 `deliverables/assets/kejian`，`max_count` 取本页需要的张数，`write_html` 为 false，然后：

```powershell
zagens-image download --workspace . --input-file .zagens/image-download.json
```

5. **目视与署名：** 生成或下载后 `Read` 图片文件。主体不符、有水印、错字或刻度错误就重做或换下一张。搜图结果若为 CC BY 必须署名，CC BY-SA 还可能要求相同方式共享；把 `attribution` 写进该页 `notes`，不要铺成画面大字。
6. **写入 JSON：** `"type":"image","path":"C:/.../deliverables/assets/kejian/xxx.jpg"`（工作区内的**绝对路径**，Windows 用正斜杠）。CLI 同时带 `--workspace`。`warnings` 里出现 `image_skipped` 就是这页没图：`ok:true` 也不算完成，改路径再写。禁止 `url`。
7. **版式：** 左文右图或上文下图。文字 `textbox` `w_pct`≈0.50–0.55，图 `w_pct`≈0.38–0.45。**勿** slide 级 `bullets` 并排。
8. **禁止：** 学生照片、名册、教材扫描页、实验源未知授权图、把远程地址直接写进 `write` / `upsert_slide`。

检测、作业、板书、家长会沟通页可以无图。生成或搜图失败就改用 `shape`+`textbox` 画示意，并在该页 `notes` 写「配图未就绪」。

## 2. 落盘路径

- **按用途分流（不按页数）**：内部草稿 / 日常上课（≤15 页）可整份 `write`（对齐引擎「≤15 页一次写完」的约定，勿自称精修）；**公开课 / 说课 / 对外**走骨架 `write` + 逐页 `upsert_slide`，便于逐页验收与硬闸门。
- **路径**：`--path` 写成工作区内的明确文件（中文文件名可用，如 `deliverables/小数的意义.pptx`）。0.14.2 起父目录自动创建，不必先建目录。省略 `--path` 时落到 `--workspace/deliverables/<标题>.pptx`。配图文件仍要自己先写到工作区。
- 骨架 `slides[]` 每页只写 `title`（可选 `role`）。`role` / `thesis` 在 write **静默忽略**。
- 大纲里的「讲授」写入该页 `notes`（设计说明页的意图同样进 `notes`）。

```powershell
zagens-office write pptx --path deliverables/kejian.pptx --input-file skeleton.json
zagens-office edit --path deliverables/kejian.pptx --op upsert_slide --input-file slide-01.json
# 优先：有 inspect_slide 时一页一次验收（文本+LayoutIR+版式问题）
zagens-office edit --path deliverables/kejian.pptx --op inspect_slide --input "{\"target\":\"/slide[1]\"}"
# 或 batch 里带 preflight（atomic:true）；无 capabilities 时退回单条 preflight + view svg
zagens-office edit --path deliverables/kejian.pptx --op batch --input "{\"atomic\":true,\"ops\":[{\"op\":\"preflight\",\"input\":{}}]}"
zagens-office edit --path deliverables/kejian.pptx --op view --input "{\"mode\":\"svg\"}"
```

骨架最小：

```json
{"title":"课题","theme":"academic","cover":false,"include_end":false,"master":{"page_number":true},"slides":[{"title":"封面","role":"cover"},{"title":"学习目标","role":"content"}]}
```

单页 upsert 最小（`notes` = 该页讲授稿）：

```json
{"target":"/slide[3]","slide":{"title":"导入","title_style":{"engine":false},"notes":"从1元10角引入0.1。","elements":[{"type":"textbox","x_pct":0.08,"y_pct":0.22,"w_pct":0.5,"h_pct":0.6,"runs":[{"text":"1元=10角，1角写成0.1元","size":22}]},{"type":"image","path":"C:/Users/teacher/lesson/deliverables/assets/yuan-jiao.png","x_pct":0.58,"y_pct":0.22,"w_pct":0.34,"h_pct":0.55}]}}
```

## 3. 页型

| 内容 | 做法 |
|------|------|
| 封面 | 自绘：课题大字 + 年级册次课时；5 秒能看懂课型 |
| 学习目标 / 作业分层 | `flow` 或 2–3 列 `textbox` |
| 导入 | 情境一句 + 一问 + 配图；不要三页铺垫 |
| 新知 | 一页一个知识点；左文右图；少堆 bullet |
| 重难点 | 左「难」右「突破」两栏 `textbox`（**勿** slide 级 `bullets` 并排） |
| 例题 | 题干大字 + 小步提示 + 配图；答案可放次页 |
| 知识网络 | `smartart`（默认 `process` / `hierarchy`，避免 `smartart_skipped`）或 shape+textbox |
| 当堂检测 | `table` 或编号 `textbox`，3–5 题 |
| 板书 | 只留课堂要留下的结构 |
| 数据/统计 | `kpi_row` / `chart`（须有真实或老师给的数） |

无 `type:"timeline"`。时间轴用 `shape`+`textbox`。

## 4. 元素易错（课件常用）

- **先契约：** 装好引擎后先 `schema capabilities`（有则看 `formats.pptx` 里是否含 `inspect_slide` / batch 白名单）；再 `schema write pptx`；改已有再 `schema edit`。复杂 JSON 用 `--input-file`。
- **信封：** stdout 一行 JSON；`ok=false` 按 `code` / `suggestion` 改一处再试，勿换键盲重放。
- **路径狱：** `--path` / 配图 `path` 须在 `--workspace`（或 cwd）之内；禁止 `..` 逃逸。配图用工作区绝对路径，CLI 带 `--workspace`。
- **两栏 / 图文并排：** 用 `elements[].textbox` 限宽。slide 级 `bullets` 是全宽栈，并排会翻车。
- **`flow`：** 子项勿写 xy；须显式 `w_pct`；`Σw + gap×(n-1) < 1`。
- **`smartart`：** 无形状回退；约束未命中就不渲染。勿 `nodes[].image` / `shape_3d`。
- **层叠：** 自由画布页 `y_pct` 从约 0.20 起（`title_style.engine:false` 时按实际标题 bbox）。
- **`preflight` `layout_overlap`：** 优先用 `inspect_slide` 看该页 LayoutIR + 问题列表；否则核 bbox 用 `view svg`（线框在 **`result.metadata.svg`**，不是 `result.svg`）；线框**通常有输出**，仅当 `svg_len=0`（确实为空）才跳过，不当失败。假色只核占位。卡片叠层且「可修 0」忽略。确认真撞再 `avoid_overlap`。写入期 assert：**不要**把 `layout_overlap` 当失败闸门；真撞版再修。
- **batch** + `"atomic": true` 可含 `upsert_slide` / `set_text` / **`preflight`** / `set_chart_*` / `set_conditional_format`（以 `schema capabilities` 为准）。`view` / `fit_text` / `avoid_overlap` / 动画 / 切换 **仍不在** batch。无 capabilities 的旧引擎：`preflight` 改单条 `edit --op preflight`。
- **不要**对 `.pptx` 跑 PDF 的 `validate`。Windows 路径用正斜杠或整段加引号，不要 `/c/Users/...`。

## 5. 三档验收（勿混）

| 档 | 命令 | 能看见 | 看不见 |
|----|------|--------|--------|
| A0 聚合（优先） | `inspect_slide`（`{"target":"/slide[n]"}`） | 该页文本摘录 + LayoutIR bbox + 版式问题 | 配色、真图清晰度 |
| A 几何 | `preflight`（可放进 `batch`） | 溢出、字号过小、真撞版、OPC | 配色、图 |
| B 线框 | `view` `{"mode":"svg"}` | bbox / 是否出框 | 成品颜色 |
| C 真看图 | PowerPoint / WPS 打开。`screenshot` 若回 `ok:false`，改看 `ppt/media/` 里的公式图，或档 B 的线框 | 配色、字、公式清晰度 | — |

探测：`schema capabilities` 失败或没有 `inspect_slide` → 跳过 A0，用 A+B。档 A0/A/B **不是**成品预览。档 B 为空不当失败。公开课 / 说课 / 对外必须到档 C 才能说做完。内部新授草稿至少跑档 A（有 A0 则每页 upsert 后跑一次 A0）。
