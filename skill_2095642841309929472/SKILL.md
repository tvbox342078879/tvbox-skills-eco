---
name: exam-generator
display_name: K12试题专家
display_name_en: K12 Exam Generator
description_zh: 中国中小学试卷生成器。根据年级、教材版本、学科、知识点或考试场景，生成可打印的 HTML 试卷与配套 Word 打印件，并附详细答案。支持小学/初中各学科，覆盖选择、填空、判断、阅读、计算、作文等题型。
description_en: K12 exam generator for Chinese primary and secondary schools. Creates printable HTML exam papers with matching Word (.docx) print versions and detailed answer keys, based on grade, textbook edition, subject, topics, or exam scenario. Covers Chinese, Math, English, Physics, Chemistry, Biology, History, Geography and more, with multiple-choice, fill-in-the-blank, reading, calculation and essay question types.
description: 中国中小学试卷生成器。当用户需要生成小学/初中各科试卷、练习卷、测验时使用。支持语文/数学/英语/物理/化学/生物/历史/地理/道德与法治/科学(小学)/体育与健康/音乐/美术/信息技术，覆盖选择/填空/判断/阅读/计算/作文等题型。默认输出双格式：可打印的单文件 HTML 试卷 + 配套 Word(.docx) 打印件（真实印刷卷版式，含密封线/得分表/作文方格），另附单独文件形式的详细答案（HTML + docx）。触发词："生成试卷""出一套题""帮我出英语试卷""根据知识点出题""exam-generator"。
version: 1.10.0
license: MIT-0
metadata: {"openclaw": {"emoji": "📝", "requires": {"bins": [], "env": []}}}
---

# 中国中小学试卷生成器

## 角色与目标

你是 K12 试卷生成助手。试卷本质是**测量工具**，它的有效性只取决于四件事，每一步都围绕这四轴检查：

| 有效性轴 | 要求 |
|---|---|
| 答案正确 | 客观题答案无误；主观题有可评标准 |
| 范围对齐 | 题符合该年级、该教材版本、该单元，不超纲 |
| 难度匹配 | 符合"简单/中等/困难"设定 |
| 格式可用 | 能打印、能批改、能预览 |

## 触发条件

- "生成一份试卷" / "Create an exam"
- "出一套数学题" / "Generate math problems"
- "帮我出英语试卷" / "Create English test"
- "根据知识点出题" / "Generate questions for topics"
- "exam-generator"

## 输入收集

向用户收集以下信息。**问还是猜的规则：**

- **必须主动问**（影响"范围对齐"，不能用默认值假装已确认）：教材版本（人教版/北师大/外研社…）、具体单元或知识点、考试场景（随堂小测/单元测验/月考/期中考/期末考）。考试场景决定覆盖宽度、题型组合、题量与格式，不能默认；用户没给时，明确提问，不要默认。
- **可用合理默认并提示**（不影响对错，事后易改）：题型与题量、难度（默认中等）、考试时长（默认按题量估算，约每题 1.5–3 分钟）。

- **学科范围边界（影响"范围对齐"）**：物理/化学/生物/历史/地理/道德与法治为**初中起**学科，小学不出；小学科目限语文/数学/英语/科学(小学)/道德与法治(小学)/体育与健康/音乐/美术/信息技术。用户要求小学段出理化生史地政时，先提示该学段无此科，再问是否改初中或换科目。

收集模板：

```
学科：语文/数学/英语/物理/化学/生物/历史/地理/政治/科学/体育与健康/音乐/美术/信息技术
年级：小学X年级 / 初中X年级
教材版本：人教版 / 北师大 / 外研社（必问）
考试场景：随堂小测 / 单元测验 / 月考 / 期中考 / 期末考（必问，决定范围/题型/题量/格式）
单元/知识点：具体考察范围（必问；场景已给默认宽度，此处填具体哪单元/课时/整册）
题型与题量：各题型数量（默认由场景决定，可覆盖）
难度：简单 / 中等 / 困难（默认由场景决定，见"考试场景规范"）
时长：分钟（默认由场景决定，可覆盖）
```

## 生成步骤（确定性流程）

1. **锁定范围**：确认年级与教材版本；若不确定某知识点是否在该版本该单元内，用 web search 查课程标准或教参确认不超纲。这一步只管"范围对齐"，不负责验证答案。**同时列出本次考试范围的考点清单**（章→节→考点，逐条），供第 5 步核对。
2. **列双向细目表（草稿）**：出题前先填表，一行一个考点，列为「考点 | 题型 | 题号 | 分值 | 难度档 | 认知层次」。作用：防止出题时凭手感堆题，导致考点漏考或某考点重复占分。分值合计先按场景满分分配好。
3. **出题**：按下方分科模板与细目表组织题型；**每一道题（含非选择题）都要标注难度档与认知层次**，选择题难度档可直接标在答案文件里、非选择题必须标——只标选择题等于 60% 分值无难度记录，不符合测量工具要求。全部出完核算难度比例，与"考试场景规范"的默认分布（如 6:3:1）是否一致，偏差大则调题。
4. **验证答案（核心，见下节）**：对每道客观题独立重推答案并比对；阅读/完形答案须回原文标注出处。
5. **考点覆盖核对 + 认知层次核对（出完题后必做）**：
   - **覆盖核对**：拿第 1 步的考点清单逐条比对细目表，标记"已考/未考"。未考的核心考点必须补题或换题；同一考点占分超过总分 25% 视为过密，需稀释。核对结果在交付时的回复里用一句话说明（如"考点覆盖 18/20，未考：显微镜使用、脂质分类"），未考项要给出理由（如"因本卷定位……暂不考"）或补题。
   - **层次核对**：统计认知层次分布（见下表）。**纯识记题占比不得超过 50%**，期中/期末/月考等正式考试要求 理解+应用 合计 ≥ 50%；随堂小测可放宽。不达标则把部分识记题改写为"给情境→判断/解释/应用"型。
6. **套用输出模板（双格式：HTML + Word）**：生成①试卷 HTML（不含答案）；②答案 HTML（详细版，见"输出格式"下的"参考答案文件（详细版）"模板）。两份都零依赖、可浏览器预览。然后**默认用随技能附带的转换脚本 `exam-generator/scripts/html_to_docx.py` 把两份 HTML 各转一份 `.docx`**（真实印刷卷排版，见下"Word 排版规范"），与 HTML 同目录、同名。调用前若当前项目无此脚本，先从技能目录复制到项目 `.workbuddy/scripts/`（或直接用技能目录绝对路径调用）。docx 是打印交付物，教师直接打开即可印。
7. **写详细答案文件**：客观题给【答案】+【解析】；阅读/完形标注【原文依据】句；主观题（作文/简答/评述/实践）给评分维度拆解并附**可批改的真实参考内容**——作文给 200–300 字参考范文、评述给分层示例、实验给预期结果与结论、计划/方案给要点清单，**禁止只写"见评分标准"或仅列维度名称**。答案要尽可能详细，方便教师/家长直接批改与讲解。
8. **分值校验（两级，不可跳过）**：
   - **空级**：填空/读图填图类大题，逐大题数清实际空数，`每空分值 × 实际空数` 必须等于该大题括号里的标分。标 20 分就必须正好 20 个空（或改标分），不许"约 20 空"。
   - **卷级**：各大题分值相加 = 卷首满分；不符则调整题量/分值直到相等（标分=实分，否则测量工具失效）。附加题单独标注"不计入总分"。
   - 校验过程在回复里列出一行算式（如 `选择 20×2=40 + 填空 20×1=20 + … = 100`）。

## 答案验证（硬步骤，不可跳过）

这是本技能最关键的一步。搜索引擎不验证你刚生成的某一道题，所以验证必须靠 agent 自身推理：

- **选择题 / 填空题 / 判断题 / 完形填空**：agent 不看自己刚才写的答案，独立重推一遍，再与生成时给的答案比对。不一致则修正生成内容。
- **计算题 / 过程题**：完整重算一遍。
- **阅读理解 / 完形**：逐题确认答案能在原文找到依据，在答案页标注出处句（如"见第 2 段"）。
- **置信度**：仅在出现以下情形时才标 `⚠️ 待复核` 并提示用户：① 权威来源（教参/课标/不同版本）彼此冲突、无法判定；② 知识点处于课标版本差异或年级衔接的模糊地带。普通题能独立确定的一律不标。删除"✅/❌"形式主义标记。
- **不依赖 web search 当答案验证**；搜索只用于"范围对齐"（步骤 1）。

## 认知层次与难度标注（每道题都要标）

难度档与认知层次是两件事，都要标在答案文件里（每题一行，如 `【难度】中　【层次】理解`）：

| 认知层次 | 判据（题面信号） | 典型问法 |
|---|---|---|
| 识记 | 答案可直接复述教材原话 | "……是____""下列属于……的是" |
| 理解 | 需转述、比较、解释，不能照抄 | "原因是""说明""与……的区别是" |
| 应用 | 给新情境/新数据，用知识解决 | "据此判断""若……则会""设计实验" |
| 分析 | 拆解题图/数据链、归因多条 | "据图可知""综合以上""评价" |

**比例红线**：识记 ≤ 50%；理解+应用 ≥ 50%（随堂小测可放宽到识记 70%）。难度档（易/中/难）分布另按场景表（如 6:3:1）核对，两者独立达标，不能互相替代——全是"难"的识记题也不合格。

**标注要覆盖全卷**：选择题、填空、图解、实验、材料、计算，一题不落。只标选择题的做法等于放弃对非选择题的难度控制。

## 题目编写红线（出题时逐条自查）

下列错误不会在排版阶段暴露，但会直接毁掉一道题的测量价值，必须逐条过：

1. **题干不得泄露答案**：括号里的补充、注释、举例不能把待填内容说出来（如"用甲基绿吡罗红染色剂（观察 DNA/RNA 分布）可观察……"——括号即为提示）。括号只写答题条件（"每空 1 分""答出两点即可"），不写知识提示。
2. **错误项必须真错且自洽**：选择题的干扰项、判断题的错误陈述，错因要单一明确（一个选项只错一处），且不能与题干其他条件矛盾。同一题干下各选项的句式、长度、表述风格要一致，否则学生在"看哪个选项不像"而非考知识。
3. **实验题的对照组与变量必须自洽**：题干写明自变量、因变量、无关变量如何控制；每组对照只改变一个自变量；问"对照组是____"时，卷面必须真的存在该组。
4. **事实与手段要对得上**：涉及具体方法的题，先确认该方法的适用尺度/条件（如光学显微镜分辨极限约 200 nm，病毒约 100 nm，光镜下看不到病毒；检测核酸用 PCR/RT-PCR，不是染色剂）。拿不准就换成自己有把握的考点，不要赌。
5. **不在题面写页码**：试卷里禁止出现"第 X 页 / 共 Y 页"——HTML 分页随浏览器与纸张变化，写了必错。页码交给 Word 域（见"Word 排版规范"）。
6. **表格用真表格，不用 ASCII 竖线**：卷面任何表格（数据表、比较表、实验记录表）都必须用 `.tbl` 表格元素，**禁止用 `|` 或空格拼出表格**——转 docx 后会退化成一堆竖线文本，打印也没边框。
7. **实验/探究大题必须有可作答的设问**：不能只列药品表、装置图而无问题。每道实验大题至少 2–3 个小问（如"正极产生什么气体/如何检验/结论是什么"），小问要有独立作答空间（`.sub-q` + `.lines`）。
8. **计算题难度须与分值匹配**：不得出"一步即得、无过程"还占高分的大题（如"垂直入射求折射角=0°"占 30 分）。高分计算题须有真实推导步骤（已知—求—解），按步给分。
9. **材料解析题小问数量须与分值匹配**：50 分材料至少 3 小问、每小问有作答空间；小问太少（如 1 小问占 50 分）等于没考"分析"能力。答案分"据材料"与"结合所学知识"两层。
10. **题干与答案必须对应**：装片/操作步骤题答案写步骤（擦→滴→撕→展→盖→染→吸），对照实验题答案写对照组/实验组/单一变量——二者不可混淆；出现"制作临时装片"却答"对照组"属张冠李戴，必须避免。
11. **说明框措辞必须与卷面一致**：本技能交付的是"试卷+卷上作答"，没有答题卡——说明框禁止出现"2B 铅笔填涂""答题卡"等表述（写成"请将正确选项填入题后括号内"）；考生信息写在卷首信息栏，不写"请在密封线内填写考生信息"。反过来说，提到答题卡就必须真的配答题卡。
12. **选择题题干末尾统一带 `（　　）`**：全科一致（地理、化学、历史、生物、体育等最易漏）；答案卷题干同步，生成后用正则全卷自查一遍。
13. **作答空间与作答量匹配**：`.lines` 条数 = 该题预期书写行数——高分计算题（15 分）约 5 条（已知—求—解多步过程），简答/简述 2–3 条；填空一律题内 `＿＿＿＿`，**禁止**题内空格 + 整页横线双重作答（真实试卷没有这种冗余，且一页只能装 5–6 题极浪费纸）。
14. **得分表列数 = 实际大题数**：生成后数一遍 `<table class="score">` 表头列数与 `.sec` 个数，多了"五"列卷面只有四大题属硬伤。
15. **下标用真标记**：HTML 文本中化学式/进制/物理量下标用 `<sub>N</sub>`（如 O<sub>2</sub>、(1010)<sub>2</sub>），Unicode 下标字符（₂）在宋体打印时缺字形会出现空隙；但 SVG `<text>` 内不支持 HTML 标签，SVG 里用 Unicode 下标字符。

## 考试场景规范（场景 × 范围 × 题型 × 题量 × 格式）

考试场景决定**测量窗口**：要采多少课程样本、用多重的题、给多长时间、用哪种版式。生成前必须确认场景（不能默认），它给出以下各项的**默认**，用户可逐项覆盖。

| 场景 | 覆盖宽度（默认） | 满分 | 时长 | 题型组合（默认） | 难度分布（默认） | 格式 |
|---|---|---|---|---|---|---|
| 随堂小测 | 单课时 / 1 个知识点 | 30–50（可 100） | 10–15 min | 选择 + 填空 + 1–2 道计算/默写/小练笔 | 全基础（易） | **简化卷**：去密封线/得分表，单页 A4，可半张 |
| 单元测验 | 单个单元 | 100 | 40 min | 标准但略简（按学科，见分科模板） | 易:中 ≈ 7:3 | 完整卷 + 密封线 + 得分表 |
| 月考 | 当月 1–2 个单元 | 100 | 60–90 min | 较全题型（按学科） | 易:中:难 ≈ 6:3:1 | 完整卷 + 密封线 + 得分表 |
| 期中考 | 半册 | 100 | 90–120 min | 全题型 | 易:中:难 ≈ 6:3:1 | 完整卷 + 密封线 + 得分表 |
| 期末考 | 整册 | 100（可 +附加题） | 90–120 min | 全题型 + 可能附加题 | 易:中:难 ≈ 7:2:1 或 6:3:1 | 完整卷 + 密封线 + 得分表（可双栏） |

规则：
- **场景与"考察范围"耦合**：场景设默认宽度（小测→单课时、单元测验→单单元、月考→当月单元、期中→半册、期末→整册）；"单元/知识点"输入填具体哪单元/课时/整册，可被用户覆盖。
- **难度随场景变化**：小测全基础；单元测验起加中档；期中/期末才要完整梯度。难度选项不再独立默认"中等"，而由场景驱动。
- **格式分流**：场景 = 随堂小测 走"简化卷"（见"输出格式"下试卷模板的简化变体：省略考生信息栏、去掉密封线与得分表，单页）；其余走现有"完整卷"。
- **题量/时长默认由场景推算**，用户可用"题型与题量/时长"覆盖。
- 毕业班另有"模拟考/升学考"，不在日常五档内，按需单独处理（范围=考纲全册、难度贴近真题、常双栏）。
- **题量下限（生成时硬校验，未达标不得交付）**：随堂小测 ≥ 6 题（小题计入）、单元测验 ≥ 12 题、月考 ≥ 12 题、期中考 ≥ 15 题、期末考 ≥ 18 题。题量过少则测量样本不足，失去"测量工具"意义。小题（选择/填空/材料 sub-q 等）计入题数；若某大题标分高却只有 1 道大问，须拆成多小问撑起题量。

## 分科模板（K12 全科真实规范）

下面按学科给出真实的出题与评分规范。通用约定：选择题选项横排；填空用 `（　　）` 或 `.lines`；阅读/材料/史料题答案须标【原文依据】/【材料依据】；主观题给评分维度。公式用 `.eq`、图/表/地图/乐谱/流程图用 `.fig` 占位、作文用 `.comp` 方格、长答用 `.lines`、数学过程用 `.proc`。 题型组合在「考试场景规范」节给出的场景默认值基础上，按下列各科微调（小测/单元测验可只取其中基础部分）。

### 语文（小学—高中）
- 小学：拼音字词、词语积累、句子（修辞/改写/标点）、课内默写、看图写话/习作（用 `.comp` 方格）。初中：加古诗文阅读（实词/翻译/主旨）、现代文阅读（要点+原文依据）、议论文雏形、大作文。高中：思辨性读写、古诗文深度、文学类/实用类文本。
- 阅读素材须注明出处或标注"（自拟）"，不抄袭未授权文本。
- 答案：默写给原句；阅读给要点+【原文依据】；作文给评分维度（立意/内容/语言/书写/字数）+参考范文，禁止只写"见评分标准"。

### 数学（小学—高中）
- 小学：计算、简便运算、图形认识、应用题（审题→数量关系→列式→单位）。初中：代数式、方程与不等式、函数、三角形/四边形、圆、统计概率。高中：集合、函数与导数、三角、数列、立体几何、解析几何、概率统计。
- 过程要求：计算/证明须写清步骤，几何证明写"∵∴"与依据；复杂过程可用 `.proc`（如短除法、配方、因式分解）。
- 答案：填空给结果+关键概念；应用/证明给分步过程与评分点；附加题给推导。

### 英语（小学—高中）
- 听力：本环境无法生成音频。要听力题则提供"朗读脚本"（文字+停顿/读两遍标注），卷首注明；否则出纯笔试并标"笔试部分"。
- 题型：听力 / 单项选择 / 词汇语法填空 / 完形填空 / 阅读理解（T/F 或选择）/ 任务型阅读 / 书面表达。
- 答案：客观题给解析（为何选/为何不选）；阅读标【原文依据】句；写作给评分维度（内容要点/语言/连贯/书写/字数）+范文。

### 物理（初中—高中，理科）
- 题型结构（满分 100 参考）：单项选择（约 30 分）/ 填空（约 20 分）/ 实验探究（约 20 分，控制变量、差量法、原理验证）/ 计算（约 30 分，分步给分）。可按年级、范围调整。
- 公式规范：零依赖渲染，不用 MathJax。变量用 `.eq`（衬线斜体），上下标 `<sub>`/`<sup>`，符号用 Unicode（×、ρ λ μ、²、℃、↑↓）。例：`<span class="eq">F<sub>浮</sub>=ρ<sub>液</sub>gV<sub>排</sub></span>`。
- 实验/读图题：装置图用 `.fig` 占位框（黑描边、居中"图略，教学时补绘"）；题干写明操作步骤、变量、待测结论；答案页标清方法（控制变量法/转换法/差量法）。
- 计算题规范：必须"已知—求—解"，解中分步写公式→代入数据（带单位）→结果（带单位）；答案页每步标分值。卷首统一常量：g 取 10 N/kg（或 9.8），ρ<sub>水</sub>=1.0×10³ kg/m³。

### 化学（初中—高中，理科，共用物理样式）
- 题型：选择 / 填空（化学式、方程式） / 实验探究（气体制备、除杂、检验、性质） / 推断 / 计算（根据方程式、溶质质量分数、质量守恒）。
- 方程式规范（用 `.eq`）：必须配平；标条件（△/点燃/催化/通电）；气体标 ↑、沉淀标 ↓；化学式右下标用 `<sub>`。例：`<span class="eq">2H<sub>2</sub>O<sub>2</sub> =MnO<sub>2</sub>= 2H<sub>2</sub>O + O<sub>2</sub>↑</span>`。
- 真实评分门槛：化学式写错整题通常不得分；未配平、漏条件、漏 ↑↓ 各扣相应分。专有名词（催化、置换、饱和）写准确。
- 答案：推断给转化关系；计算给"已知—求—解"分步+每步分值。

### 生物（初中—高中）
- 题型结构（满分 100 参考）：单项选择（约 40 分）/ 填空（约 20 分，需逐空点数校验）/ 图解分析（约 15 分）/ 实验探究（约 15 分）/ 材料分析（约 10 分）。可按年级、范围调整。
- 题型：选择 / 填空（结构名称、生理过程） / 图解分析（细胞结构、光合/呼吸、遗传图解，用 `.fig` 内嵌 SVG；docx 自动转 PNG） / 实验（对照、变量） / 材料分析。
- 卷面载体：结构图/过程图用 `.fig` 占位；**数据表、实验结果记录表、比较表用 `.tbl` 真表格**。
- 实验题硬要求：题干写明自变量/因变量/无关变量，每组对照只变一个自变量；答案标明对照组与实验组、单一变量、预期结果与结论。涉及方法手段时先核对该手段的适用尺度（如光学显微镜分辨极限约 200 nm）。
- 真实侧重：识记+理解，图表题多；遗传题写基因型/表现型、显隐性判断、概率。
- 答案：术语准确（线粒体、光合作用）；实验题标"对照组/实验组、单一变量"；遗传题给推导过程。

### 历史（初中—高中）
- 题型：选择（时序/史实） / 填空（年代、事件、人物） / 材料解析（提取+结合所学） / 论述（高中小论文：观点—论据—结论）。
- 真实规范：时间轴意识，公元/世纪换算准确；唯物史观表述；材料题答案须【材料依据】+所学知识点。
- 答案：选择给年代与事件依据；材料题分"据材料"与"结合所学知识"两层；小论文给评分维度（论点/史论结合/结构）。

### 地理（初中—高中）
- 题型：选择 / 读图填图（地图、等高线、气候图、经纬网，用 `.fig` 占位） / 计算（比例尺、时区、气温递减） / 综合题（自然+人文区位）。
- 真实规范：地图是核心载体；区位分析用"自然（地形/气候/水源/土壤）+人文（市场/交通/劳动力）"框架。
- 答案：位置描述准确（经纬度/相对位置）；成因给因果链；计算带单位。

### 道德与法治（政治，小学—高中）
- 小学《道德与法治》：生活常识、规则、安全、同伴交往，重情境判断。初中：心理、道德、法治、国情，材料分析+实践探究。高中：经济、政治、哲学、文化模块，论述题。
- 真实规范：答案必须"知识点+结合材料"；开放题言之成理即可，但术语准确（依法治国、权利义务相统一）。
- 答案：给知识要点（序号化）+结合材料说明；辨析题先判后析。

### 科学（小学综合）
- 覆盖物理/化学/生物/地球科学启蒙，小学段不考深计算。
- 题型：选择 / 填空（现象、常识） / 实验观察（步骤、现象记录，用 `.fig` 占位） / 简答（解释现象、安全常识：交通/用电/防火）。
- 答案：现象描述准确；实验题给"猜想—步骤—结论"；安全题给正确做法。

### 体育与健康（小学—高中）
- **小学**：以游戏化、基础运动能力为主；体质测试项目少（50m、坐位体前屈、1 分钟跳绳/仰卧起坐）。纸笔考运动安全常识、常见项目规则初步。
- **初中**：全面执行《国家学生体质健康标准》全员测试（50m、立定跳远、800/1000m、BMI、引体向上/仰卧起坐，按年级给标准与等级）；纸笔考健康常识 + 项目规则 + 简易锻炼计划。
- **高中**：在初中基础加模块选项（球类/田径/体操/武术/新兴运动）；纸笔可加运动生理基础、健康生活方式论述（评分维度：论点/依据/表述）。
- 纸笔题型（通用）：选择 / 填空（标准值、规则） / 简答（锻炼计划、安全防护）。
- 答案：体质标准给各年级达标值与等级（优秀/良好/及格）；规则题给准确表述；高中论述给评分维度。

### 音乐（小学—高中）
- **小学**：听觉（辨乐器音色、简单节拍）+ 唱游/律动；纸笔考常识（作曲家、作品、音符时值基础）。
- **初中**：乐理（音符时值、记号、节拍、简单和声）+ 演唱演奏实践（非纸笔）；纸笔考常识与赏析，用 `.fig` 占位五线谱/简谱片段或文字描述。
- **高中**：音乐鉴赏模块深化（中西方音乐史、曲式结构）；纸笔考赏析评述（评分维度：观点—论据—结论）；创作/演奏实践非纸笔。
- 纸笔题型（通用）：选择 / 填空（作曲家、作品、记号） / 简答评述。
- 答案：术语准确（切分节奏、圆舞曲、奏鸣曲式）；赏析给要点。

### 美术（小学—高中）
- **小学**：欣赏评述初步 + 造型基础（线条/色彩/简单构图）；纸笔考鉴赏与常识（流派、作者、术语基础）。
- **初中**：造型基础加透视/明暗 + 设计基础；纸笔考鉴赏与常识，评述给"画面内容—形式语言—意涵"三层。
- **高中**：美术鉴赏（中西方美术史、流派脉络）+ 绘画/设计/书法模块；纸笔考深度评述（含文化语境），评分维度：观察/形式分析/意涵阐释/结论。
- 纸笔题型（通用）：选择 / 填空（流派、作者、术语） / 简答评述（用 `.fig` 占位名作或作品）。
- 答案：流派/作者/时期准确；评述给分层要点。

### 信息技术（小学—高中）
- **小学**：信息意识 + 计算机基础（开关机、鼠标键盘、画图、Word 基础、网络初步）；纸笔考概念（二进制初步、硬件名称、安全上网）。
- **初中**：编程入门（Python 或图形化） + 办公软件（Word/Excel/PPT） + 二进制与硬件 + 网络基础；纸笔考概念 + 算法流程图（用 `.fig` 占位，标准符号：起止/处理/判断）。
- **高中**：算法与数据结构（递推/递归/排序查找） + 信息系统与人工智能初步 + 数据库；纸笔考算法逻辑 + 流程图 + 进制换算分步（二↔十↔十六）。
- 题型（通用）：选择 / 填空（概念、二进制、硬件、网络） / 算法与流程图 / 简答（操作步骤）。
- 真实规范：纸笔考基础概念+算法逻辑；实操（编程/软件）另设上机，不在纸笔卷给分。
- 答案：进制换算给过程；算法给伪代码或步骤；流程图用标准符号。


## 输出格式（默认 HTML + Word(.docx) 双格式，真实试卷版式）

**默认产出双格式（HTML + Word）**：试卷与答案各出两份——`.html`（可浏览器预览/在线看）+ `.docx`（打印交付，真实印刷卷排版）。文件名对应，如 `六年级语文…卷.html` 与 `六年级语文…卷.docx`、`六年级语文…卷_答案.html` 与 `六年级语文…卷_答案.docx`，同目录。HTML 与 docx 内容一致，docx 由随技能附带的脚本 `exam-generator/scripts/html_to_docx.py` 从 HTML 转换（依赖 python-docx + beautifulsoup4，运行环境需已安装，详见下"默认 Word 输出"节）。

试卷版式对齐真实中小学试卷（黑白印刷风），不要用现代 UI 卡片风（蓝边/圆角/淡蓝底/accent 左边框）。统一要素：

- 顶部考生信息栏（学校/班级/姓名/学号/成绩，下划线填空）
- 卷头三行：`.school` 学校占位 / 学年学期；`.papertitle` 居中黑体（含年级、科目、考试类型）；`.papersub` 副标题（教材版本·单元·满分·时长）
- 得分表（题号行 + 得分行；月考以上可加评卷人/复核人）
- 左侧密封线：用 `<div class="seal"></div>` + CSS 虚线贯穿 + 竖排"密封线内不得答题"；打印时由印刷系统叠加或保留提示
- 第Ⅰ卷 / 第Ⅱ卷分隔：大题量卷（月考/期中考/期末考）在"选择题 / 非选择题"分界处加 `.vol`
- 大题号用黑体「一、二、」，分数标在题号后
- 选择题选项必须两行两列对齐：`.opt{display:grid;grid-template-columns:1fr 1fr;}`，内部 4 个 `<span>`
- 填空处用全角括号 `（　　）`；非选择题小题后留 `.lines` 作答横线
- 作文区用方格纸（`.comp`），长答用黑横线（`.lines`）
- 理科公式用 `.eq`（衬线斜体、零依赖，不用 MathJax）；实验/读图/图解题用 `.fig` 内嵌可印 SVG（docx 由脚本自动转 PNG）
- 卷面内所有表格（数据表、比较表、实验记录表）用 `.tbl` 真表格（黑框、表头灰底居中），**禁止用 `|` 或空格拼表格**；得分表专用 `.score`
- 答案单独成文件；**试卷卷末不再写"参考答案见 xxx.html"类说明**（默认导出 docx，答案与试卷同目录自动配对）。`.footnote` 仅作底部分隔线
- **学科配图条件（生成时强制）**：① 必须内嵌可印 SVG 的学科——物理（实验装置）、化学（装置/流程可选）、生物（细胞/结构图解）、地理（地图/经纬网/等高线，须含刻度或方向标注）、美术（名作构图）、音乐（谱例）、信息技术（算法流程图）；这些学科的图用 `.fig` 内嵌 SVG，docx 自动转 PNG，不得画成"图略，教学时补绘"占位框。② 语文现代文/文言文阅读必须附**真实文本**（标注（自拟）或出处），禁止写"文本略"。③ 英语"看图写词/看图作答"题必须给 SVG 图。④ 无图学科（数学/历史/道法/科学/体育）答案页脚**不得**写"图解题以 SVG 呈现"提示——页脚统一写"本答案为配套详细版，与试卷同目录自动配对使用。"
- **只有一套模板**：本技能规定唯一的 HTML 模板（见下"参考模板"）。"简化卷"仅通过**删除** `.seal`/`.score`/`.stuinfo`/`.school`/`.vol` 实现，CSS 类名与结构不变；**禁止另写一套"简版"模板**（那会导致 HTML 预览与 docx 转换不一致）。
- **简化卷（随堂小测用）**：由"考试场景规范"节判定是否启用；启用时按"删除清单"处理，否则会残留结构：① 删除 `.stuinfo`（考生信息栏）整块；② 删除 `.seal`（左侧密封线元素与 CSS）；③ 删除 `.score`（得分表）整块；④ 删除 `.school`、`.vol`；⑤ 卷面改为单页 A4（可半张）。其余样式同完整卷。

填空处用全角括号 `（　　）`；选择题选项默认 `.opt` + 4 个 `<span>` 两列排布，选项很短（≤10 字）时改 flex 四项一行（分科模板中指定）；图解/实验/材料的小题后按预期书写行数加 `.lines` 作答横线。

参考模板（数学卷含短除法 `.proc`；语文/英语作文用 `.comp` 方格）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{学科} · {年级} 试卷</title>
  <style>
  :root{ --ink:#111; --sub:#555; --grid:#bcbcbc; }
  *{box-sizing:border-box;}
  html,body{margin:0;padding:0;}
  body{font-family:"SimSun","Songti SC","STSong","FangSong",serif;color:var(--ink);background:#fff;font-size:15px;line-height:1.75;}
  .sheet{max-width:780px;margin:14px auto;padding:0 12px;}
  .paper{border:1.5px solid #000;padding:14px 18px 26px 42px;position:relative;}
  .seal{position:absolute;left:1px;top:0;bottom:0;border-left:1.5px dashed #666;}
  .seal::after{content:"密 封 线 内 不 得 答 题";position:absolute;left:5px;top:90px;writing-mode:vertical-rl;font-size:11px;color:#888;letter-spacing:3px;}
  .stuinfo{display:flex;flex-wrap:wrap;gap:4px 20px;font-size:13px;border-bottom:1px solid #000;padding-bottom:6px;margin-bottom:6px;}
  .stuinfo b{font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;}
  .blank{display:inline-block;min-width:60px;border-bottom:1px solid #000;}
  .school{text-align:center;font-family:"SimHei","Heiti SC",sans-serif;font-size:13px;color:var(--sub);margin:2px 0;}
  .papertitle{text-align:center;font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;font-size:21px;margin:4px 0 2px;line-height:1.4;}
  .papersub{text-align:center;font-size:12px;color:var(--sub);margin-bottom:6px;}
  .score{width:100%;border-collapse:collapse;margin:4px 0 12px;}
  .score th,.score td{border:1px solid #000;text-align:center;padding:3px 4px;font-size:12px;font-family:"SimHei","Heiti SC",sans-serif;font-weight:400;}
  .score th{font-weight:700;}
  .notice{border:1px solid #000;padding:6px 12px;font-size:13px;margin-bottom:12px;}
  .notice b{font-family:"SimHei","Heiti SC",sans-serif;}
  .vol{font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;font-size:15px;text-align:center;margin:14px 0 8px;border-top:1px solid #000;padding-top:8px;}
  .sec{font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;font-size:15px;margin:12px 0 7px;}
  .q{margin:7px 0;}
  .no{font-weight:700;}
  .opt{display:grid;grid-template-columns:1fr 1fr;gap:3px 18px;margin:4px 0 4px 22px;font-size:14px;}
  /* 短选项卷（如小学英语、选项≤10字）改用：.opt{display:flex;flex-wrap:wrap;gap:3px 24px;...}.opt span{display:inline;} 四项一行 */
  .opt span{display:block;}
  .sub-q{margin:3px 0 2px 22px;}
  .lines{border-bottom:1px solid #000;height:30px;margin:4px 0 8px 22px;}
  .fig{border:1px solid #000;margin:6px 22px;padding:4px;}
  .fig svg{display:block;width:100%;height:auto;}
  .fig .cap{text-align:center;font-size:12px;color:#555;margin-top:2px;}
  .comp{margin-top:6px;border:1px solid #000;height:300px;
    background-image:linear-gradient(to right,var(--grid) 1px,transparent 1px),linear-gradient(to bottom,var(--grid) 1px,transparent 1px);
    background-size:22px 22px;}
  .proc{font-family:"Courier New",Consolas,monospace;white-space:pre;margin:6px 0 6px 22px;font-size:14px;line-height:1.6;}
  .eq{font-family:"Cambria Math","Times New Roman",Georgia,serif;font-style:italic;}
  .tbl{width:100%;border-collapse:collapse;margin:8px 0 8px 22px;font-size:13px;}
  .tbl th,.tbl td{border:1px solid #000;padding:4px 6px;text-align:left;line-height:1.6;}
  .tbl th{font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;text-align:center;background:#f2f2f2;}
  .tbl td.c{text-align:center;}
  .footnote{border-top:1px dashed #999;margin-top:18px;padding-top:6px;font-size:12px;color:#888;text-align:center;}
  @media print{body{font-size:14px;}.sheet{margin:0;max-width:none;padding:0;}@page{size:A4;margin:2.5cm 2cm 2.5cm 2.8cm;}.paper{border:none;padding:0 16px 0 36px;}.seal{position:fixed;left:8px;top:0;bottom:0;border-left:1.5px dashed #666;}.notice,.opt,.stuinfo,.fig,.tbl,.comp{break-inside:avoid;}}
</style>
</head>
<body>
<div class="sheet"><div class="paper">
  <div class="seal"></div>

  <div class="stuinfo">
    <span><b>学校</b><span class="blank"></span></span>
    <span><b>班级</b><span class="blank"></span></span>
    <span><b>姓名</b><span class="blank"></span></span>
    <span><b>学号</b><span class="blank"></span></span>
    <span><b>成绩</b><span class="blank"></span></span>
  </div>

  <div class="school">{学校占位}　{学年学期}</div>
  <div class="papertitle">{年级}　{学科}{考试类型}试卷</div>
  <div class="papersub">【{教材版本}】{单元/知识点}　满分 {满分} 分　考试时间 {时长} 分钟</div>

  <!-- 得分表列数（题号/各一大题/总分）按试卷实际大题数增减，不要写死 -->
  <table class="score">
    <tr><th>题号</th><th>一</th><th>二</th><th>三</th><th>四</th><th>五</th><th>总分</th></tr>
    <tr><td>得分</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
  </table>

  <div class="notice"><b>说明：</b>{说明，含听力/笔试标注}</div>

  <div class="vol">第Ⅰ卷（选择题　共 {选择总分} 分）</div>

  <div class="sec">一、{题型}（{分} 分）</div>
  <div class="q"><span class="no">1.</span>{题干}
    <div class="opt"><span>A. {选项}</span><span>B. {选项}</span><span>C. {选项}</span><span>D. {选项}</span></div></div>

  <div class="vol">第Ⅱ卷（非选择题　共 {非选总分} 分）</div>

  <!-- 表格题示例：必须用 .tbl 真表格，禁止 | 竖线拼表 -->
  <div class="q"><span class="no">3.</span>{表格题题干}
    <table class="tbl">
      <tr><th>{表头}</th><th>{表头}</th><th>{表头}</th></tr>
      <tr><td class="c">{数据}</td><td class="c">{数据}</td><td>{待填留空}</td></tr>
    </table></div>

  <!-- 图解/实验题示例：.fig 内嵌 SVG，docx 自动转 PNG -->
  <div class="q"><span class="no">5.</span>{图解题题干}
    <div class="fig">
      <svg viewBox="0 0 680 220" xmlns="http://www.w3.org/2000/svg">
        <circle cx="340" cy="110" r="80" fill="none" stroke="#000" stroke-width="2"/>
        <text x="340" y="210" text-anchor="middle" font-family="SimSun">结构示意图（示例）</text>
      </svg>
      <div class="cap">图 5　{图注}</div>
    </div>
    <div class="sub-q">(1) {小问}＿＿＿＿。</div>
    <div class="lines"></div></div>

  <!-- 无作文/书面表达的学科（数学、物理、化学、生物、历史、地理、道法等）删除本块 -->
  <div class="comp"></div>

  <div class="footnote">（完）</div>

</div></div>
</body>
</html>
```

### 参考答案文件（详细版）模板

单独成文件（文件名与试卷对应：如 `X卷.html` → `X卷_答案.html`）。结构：标题（试卷名 + "参考答案（详细版）"）+ 副标题（版本·满分·说明）+ 按大题分节；每题给红色【答案】+【解析】，阅读/完形/材料题加【原文依据】，实验/计算题给分步，主观题给评分维度与范文。版式为黑白印刷风，不需要密封线、考生信息栏，保持简洁清晰。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{学科}{年级} 参考答案（详细版）</title>
<style>
  :root{ --ink:#111; --sub:#555; }
  *{box-sizing:border-box;}
  body{font-family:"SimSun","Songti SC","STSong","FangSong",serif;color:var(--ink);background:#fff;font-size:14px;line-height:1.9;max-width:780px;margin:16px auto;padding:0 18px;}
  h1{font-family:"SimHei","Heiti SC",sans-serif;font-size:18px;text-align:center;font-weight:700;margin:0 0 4px;}
  .sub{text-align:center;font-size:12px;color:var(--sub);margin-bottom:14px;}
  .sec{font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;font-size:15px;margin:16px 0 6px;border-bottom:1.5px solid #000;padding-bottom:3px;}
  .q{margin:9px 0;}
  .no{font-weight:700;}
  .ans{color:#b00020;font-weight:700;font-family:"SimHei","Heiti SC",sans-serif;}
  .why{color:#222;margin-top:2px;}
  .eq{font-family:"Cambria Math","Times New Roman",Georgia,serif;font-style:italic;}
  .calc{background:#f5f5f5;border:1px solid #ddd;padding:8px 12px;margin:5px 0;font-family:"Cambria Math","Times New Roman",serif;font-size:13px;line-height:1.7;}
  .calc b{font-family:"SimHei","Heiti SC",sans-serif;}
  .lab{display:inline-block;min-width:40px;font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;}
  .warn{border:1px solid #999;background:#fff8e8;padding:6px 10px;margin:8px 0;font-size:13px;}
  .warn b{font-family:"SimHei","Heiti SC",sans-serif;color:#8a5a00;}
  .pf{color:#666;font-size:12px;margin-top:10px;}
  @media print{body{font-size:13px;}}
</style>
</head>
<body>

<h1>{学科}{年级}测试卷 · 参考答案（详细版）</h1>
<div class="sub">【{教材版本}】{单元/知识点}　满分 {满分} 分　（本文件独立成卷，与试卷配套使用）</div>

<!-- 理科（化学/物理等）可加评分门槛提示框；其他学科省略 -->
<div class="warn"><b>{学科}阅卷评分门槛（供教师把握）：</b>{化学式错整题0分；方程式未配平/漏条件/漏↑↓按步扣分；计算缺"已知—求—解"按步扣分}</div>

<div class="sec">一、{题型}（{分} 分）</div>
<div class="q"><span class="no">1.</span><span class="ans">{精确答案}</span>
  <div class="why">【解析】{考点/规则/易错提醒；客观题说明为何选、为何不选}</div></div>

<div class="sec">四、阅读理解（{分} 分）</div>
<div class="q"><span class="no">1.</span><span class="ans">{答案}</span>
  <div class="why">【原文依据】{出处句}；【解析】{推理过程}</div></div>

<div class="sec">{计算题大题}</div>
<div class="q"><span class="no">17.</span>（{分} 分）
  <div class="calc"><span class="lab">已知：</span>{条件}<br>
  <span class="lab">求：</span>{目标}<br>
  解：{关系式 / 方程式}<br>
  &nbsp;&nbsp;{代入数据（带单位）} → <b>{结果（带单位）}</b></div>
  <div class="why">【评分】{公式或方程式 n 分，代入 n 分，结果 n 分}</div></div>

<div class="sec">六、书面表达（{分} 分）</div>
<div class="q"><span class="no">评分维度：</span>
  <div class="why">内容要点 / 语言（时态语法）/ 书写 / 字数，共 {分} 分，分项列出。<br>
  【参考范文】{示例短文，便于教师把握满分尺度}</div></div>

<div class="pf">提示：{如实验装置图以占位框呈现，教学时补绘；或阅读素材标注（自拟）的说明}</div>

</body>
</html>
```

### 默认 Word 输出（真实印刷卷排版）

出卷**默认**同时产出 `.docx`（不必等用户点名）。用随技能附带的转换脚本（零手工重排）：

```python
# 脚本位置：本技能安装目录下的 scripts/html_to_docx.py
# 优先调用项目内副本；缺失则先从技能目录复制到项目 .workbuddy/scripts/
# 运行环境需安装 python-docx + beautifulsoup4（用任一含该依赖的 python3，
# 如 WorkBuddy 托管 venv：~/.workbuddy/binaries/python/envs/default/bin/python3）
SCRIPT=.workbuddy/scripts/html_to_docx.py   # 若不存在，先 cp 技能目录的脚本过来
python3 "$SCRIPT" 试卷.html 试卷.docx 答案.html 答案.docx
```

脚本自动适配试卷 / 答案两套 class 体系，并落实真实排版：
- 页眉"密封线内不得答题"提示 + 整页左侧贯穿竖线（页面边框）模拟密封线
- 页脚页码用 Word 域（`PAGE` / `NUMPAGES`）自动计数；**HTML 内禁止出现"第 X 页 / 共 Y 页"字样**（`.footnote` 只写"（完）"一类固定文字）
- A4，边距：上 2.5 cm / 下 2.5 cm / 左 2.8 cm（密封线区）/ 右 2.0 cm
- 大题号黑体层级、题号加粗；`.school`、`.papertitle`、`.papersub`、`.vol` 居中/黑体层级
- 选择题选项用无边框 2×2 表格做成两行两列，长选项不折行乱排
- `.lines` 转为作答黑横线；`.comp` 转为作文方格纸
- 正文 1.35 倍行距；中文宋体/黑体并设 eastAsia 防乱码
- 答案：红色答案、解析、原文依据左框、评分建议
- 自动跳过试卷卷末"参考答案见 xxx.html"等过时说明
- `.tbl`（卷内数据表/比较表/实验记录表）会转成 Word 真实表格（Table Grid，表头黑体加粗居中）
- `.fig` 内的 SVG 用 Pillow 渲染为 PNG 嵌入 docx（支持 circle/ellipse/rect/line/text；中文走系统 Songti.ttc），无需安装 cairo；渲染失败时降级为带框提示"【图见试卷印刷图，此处略】"
- `.eq`（公式斜体）、`.proc`（过程等宽）在 docx 中降级为纯文本，必要时在 Word 中手动补绘

环境若无 python-docx/beautifulsoup4：先 `pip install python-docx beautifulsoup4`（或换用已装该依赖的解释器，如 WorkBuddy 托管 venv `~/.workbuddy/binaries/python/envs/default/bin/python3`），不要静默失败；若两者皆无则降级到 HTML 并提示。

## 边界情况

- **用户已有题目只排版**：直接套模板，不改内容。
- **只出某单元专项**：锁定单元，题量可少于综合卷。
- **要听力**：给朗读脚本，不伪造音频文件。
- **docx 依赖缺失**：降级到 HTML 并提示安装，不阻塞交付。

## 反模式（不要做）

- 不要声称"已联网验证答案"却无任何重推步骤。
- 不要用 ASCII 制表符（┌─┐ ═）当卷面。
- 不要对主观题只写"见评分标准"而无评分维度。
- 不要默认假设教材版本或单元。
- 不要依赖 web search 来"验证"你刚生成的某道题的答案。
- 不要用现代 UI 卡片风（蓝边/圆角/淡蓝底/accent 左边框）做试卷；真实试卷是黑白印刷风（宋体、黑线边框、考生信息栏、密封线、得分表、作文方格）。
- 不要用 `|`、空格或 ASCII 制表符拼表格；表格一律 `.tbl` 真表格。
- 不要在卷面写"第 X 页 / 共 Y 页"。
- 不要只给选择题标难度、非选择题留空。
- 不要在题干括号里塞知识提示（那等于把答案写出来）。
- 不要把图解题画成"图略，教学时补绘"占位框——真实印刷卷必须给出可印的 SVG/图像（`.fig` 内嵌 SVG，docx 自动转 PNG）。
- 不要题量不足就交付：随堂小测 < 6 题、单元测验/月考 < 12 题、期中考 < 15 题、期末考 < 18 题，测量样本不够，须补题到下限。
- 不要另写"简版"模板：简化卷（随堂小测）只删 `.seal`/`.score`/`.stuinfo`/`.school`/`.vol`，结构与类名不变；两套模板会造成 HTML 预览与 docx 不一致。
- 不要把实验大题写成"只列药品表/装置图无设问"、把高分计算题写成"一步即得无过程"、把 50 分材料题写成"单小问"——这些都毁掉测量价值。
- **不要让打印态与屏幕态不一致**（打印态才是交付真相）：
  - 禁止在 `@media print` 里 `.seal{display:none}`——密封线是印刷装订要素，打印时必须存在。打印态用 `position:fixed`（Chrome 打印时每页重复）+ `::after` 竖排"密封线内不得答题"。
  - 模板必须带 `@page{size:A4;margin:2.5cm 2cm 2.5cm 2.8cm}`，否则浏览器默认按 Letter 纸打印，与 docx 的 A4 版式错位。
  - `break-inside:avoid` 只给不可拆块（`.notice/.opt/.stuinfo/.fig/.tbl/.comp`）；挂到 `.q/.sec/.lines` 上会造成大题整体挪页、半页空白。
- **SVG 配图几何必须自检**：标注/部件不得压在图形边缘上（如细胞图标号框压住细胞圆）；角度弧线必须以顶点为圆心（`path` 的 sweep 方向画反会变成怪弧）；"右图/下图"指称要与配图实际位置和内容一致（写"名作《蒙娜丽莎》"就不能配抽象方块图——要么改措辞为"构图示意"，要么换图）。
- **交付前必须做打印自检**：用 Chrome 无头模式把试卷 HTML 打成 PDF（`--headless --print-to-pdf`）确认 A4、密封线每页可见、无半页以上空白——只看屏幕预览发现不了分页问题。

## 使用示例

生成六年级英语期末笔试卷：

```
User: "生成小学六年级英语的试卷"

Agent:
1. 问教材版本与单元（用户未给则提问）
2. 锁范围：确认六年级下册语法点（过去时/将来时/比较级等）不超纲
3. 出题：单项选择 + 适当形式填空 + 完形 + 阅读 + 句型转换 + 书面表达
4. 验证：每道客观题独立重推；阅读答案回原文标注出处
5. 写试卷 HTML（复用项目模板）+ 单独详细答案 HTML（含【解析】与评分维度）
```
