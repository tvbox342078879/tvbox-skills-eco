---
name: tizhuang-question-bank
display_name: 题庄中小学智能题库
display_name_en: Tizhuang K12 Question Bank
description: K12中小学题库，支持真题检索、免费抽题、练习测验、智能组卷。用户提出找题、抽题、出题、练习、测验、考试卷、同步训练、专项训练、答案讲解或组卷要求时使用。
description_zh: 面向中小学 K12 教育场景的真题检索、免费抽题、练习测验与智能组卷 Skill。题庄公开展示的 2000 万+ K12 题目资源，按学科、年级、教材版本、章节、知识点、题型、难度、年份等条件免费抽题、随机抽题、精准找题，并生成练习、测验和组卷。适用于教师备课、学生练习、家长辅导、同步训练、章节测验、知识点巩固、单元测试、期中期末复习、历年真题训练和 AI 智能组卷等场景。Skill 始终返回服务端真实题目，不虚构、不改写、不重排题干、选项、答案和解析。
description_en: Free K12 real-question search, random sampling, practice quizzes, and AI paper building for primary, middle, and high school. 20M+ public questions across all subjects, grades, textbook editions, chapters, knowledge points, types, difficulties, and years. Always returns server-side real questions — never fabricates, rewrites, or reorders content.
category: education
version: 1.3.0
author: 青橙科教
---

# 题庄中小学智能题库

题庄官网：https://tizhuang.qcscience.cc/  
ClawHub：题庄中小学真题题库  
GitHub：tizhuang-agent-skills

题庄 Question Bank Skill 让 AI Agent 可以直接检索和调用中小学 K12 题库。面向题庄公开展示的 **2000 万+ K12 题目资源储备**，按学科、年级、教材版本、章节、知识点、题型、难度、年份等条件免费抽题、随机抽题、精准找题，并生成练习、测验和组卷。

适用于老师备课、学生练习、家长辅导、同步训练、章节测验、知识点巩固、单元测试、期中期末复习、历年真题训练和 AI 智能组卷等中小学教育场景。

实际可检索题量、题目范围和筛选覆盖以题庄在线服务的实时返回为准。**Skill 始终返回服务端真实题目，不虚构、不改写、不重排题干、选项、答案和解析。**

## 核心能力

### 1. 免费抽题、随机抽题
不注册即可开始免费抽题。游客可获得 24 小时、最多 100 题的匿名免费试用。免费注册即可获得每天 200 题起的免费额度，继续找题、抽题、测验和组卷。

支持随机抽题、指定数量抽题、按条件抽题和带图题抽取。适合临时练习、课堂提问、每日一练、随堂测验和快速出题。

### 2. 中小学 K12 真题检索
覆盖语文、数学、英语、物理、化学、生物、历史、地理、政治、科学、道德与法治、历史与社会等学科。支持小学、初中、高中 K12 题目检索。

支持按年级、学段、教材版本、册次、章节和知识点精准找题；支持按选择题、填空题、判断题、简答题等题型筛选；支持按难度、年份、关键词、是否带图等条件组合检索；支持教材、章节、知识点、知识树多条件交叉检索。

### 3. 练习、测验、在线答题
一句话生成隐藏答案的临时练习页。支持章节练习、知识点练习、同步练习、专项训练和综合测验。支持单题问答、答题检查、标准答案和解析讲解。

多题任务默认生成练习页，避免在聊天中重复堆放题目和答案。支持在线练习与试卷式查看，适合课堂、家庭作业和自测。

### 4. AI 智能组卷
将自然语言组卷要求解析为学科、年级、教材、章节、知识点、题型、难度和题量等结构化条件。支持将明确的组卷要求衔接到 AI 组卷流程，减少重复筛题和整理工作。

支持练习卷、测试卷、单元卷、专题卷、期中卷、期末卷和复习卷等组卷场景。保留原始组卷条件，避免静默扩大教材、章节或知识点范围。

### 5. 真实题目与答案解析
后端题库是唯一事实来源。保留题目 ID、题干、选项、复合小题、图片、标准答案和解析。默认先隐藏答案，学生作答后再进行答案检查或讲解。

请求失败时直接报告失败，不使用 AI 编造"看起来合理"的假题。

## 优先使用免费抽题

- 首次使用无需注册。未配置授权时，脚本自动创建并缓存匿名试用凭证，可在 24 小时内免费抽取最多 100 道题。
- 不要在首次使用前要求用户注册，先完成一次有用的找题、抽题或测验。
- 免费注册后，初始每天可用 200 道题，并可继续保存和编辑试卷。注册仍然免费，不得描述为无限量。
- 不要要求用户在对话中粘贴密钥、账号令牌或密码。需要使用注册额度时，只提示用户在本地配置 `QUESTION_SERVICE_LICENSE`。

## 判断用户想要的结果

- 用户只要 1 道题、要求直接答题或进行答案核对：在对话中给题，先隐藏答案，等待用户作答。
- 用户要多道练习题、随堂测验、专项训练或模拟测试：默认生成临时练习测验页。
- 用户要求组卷、保存试卷、继续编辑或制作正式试卷：生成题庄智能组卷交接链接。
- 用户明确要求带图题、几何图、统计图或实验图时，加上 `--with-images`；普通抽题不要擅自判断题库没有图片。

## 使用脚本

脚本入口为 `scripts/question_bank.py`。优先使用环境中可用的 Python 3。

在 Coze 沙箱中，脚本直接优先导入 `coze_workload_identity.requests`，使用平台提供的受信出站网络和证书链；不通过环境变量猜测是否处于 Coze。仅当运行环境没有该模块时，才回退到 Python 标准库 `urllib.request`。始终保持证书和主机名校验开启，不得加入不安全 SSL 上下文或任何跳过证书验证的参数。

先查询可用分类并解析名称对应的 ID：

```powershell
python scripts/question_bank.py subjects
python scripts/question_bank.py grades
python scripts/question_bank.py question-types --subject-id <学科ID>
python scripts/question_bank.py editions --subject-id <学科ID> --grade-id <年级ID>
python scripts/question_bank.py chapters --subject-id <学科ID> --grade-id <年级ID> --edition-id <教材版本ID>
python scripts/question_bank.py knowledge-points --subject-id <学科ID> --phase-id <学段ID> --keyword "知识点名称"
```

不得根据名称猜测 ID。一个名称对应多个教材版本或知识点时，列出候选项，让用户确认后再抽题。

### 用名称直接解析（中文参数）

`questions`、`practice-page`、`builder`、`resolve` 均接受中文名称参数，脚本自动完成名称→ID 解析：

| 参数 | 说明 | 示例 |
|---|---|---|
| `--subject` | 学科名称，精确匹配 | `--subject 物理` |
| `--grade` | 年级名称，精确匹配 | `--grade 八年级` |
| `--edition` | 教材版本名称，需同时提供学科+年级 | `--edition 人教版2024` |
| `--knowledge` | 知识点名称，精确匹配，需同时提供学科 | `--knowledge 光的反射` |
| `--knowledge-scope` | 知识点范围：`branch`（含子孙节点，默认）/ `exact`（仅本节点） | `--knowledge-scope exact` |

名称解析只读取免费公开元数据，不抽题、不创建试用、不消耗题目额度。解析过程输出到 stderr，不影响 stdout 返回结果。

### 筛选拦截规则（强制）

脚本内置拦截机制，以下情况**直接报错停止，绝不放宽条件抽题**：

1. **名称不存在或歧义**：学科/年级/教材/知识点名称找不到精确匹配时，返回候选列表，不凭猜测继续
2. **名称与 ID 冲突**：同时传 `--subject 物理` 和 `--subject-id 1`（语文的 ID），校验不一致立即停止
3. **教材缺少前置条件**：校验教材版本必须同时提供学科和年级，缺一不可
4. **知识点缺少学科**：知识点解析必须先有学科，否则无法定位
5. **知识点参数互斥**：`--knowledge` 不能与 `--knowledge-id` 或 `--knowledge-tree-id` 同时使用

> ⚠️ **Agent 必须遵守：** 拦截报错后，只能请用户确认或更换筛选条件，**不得擅自放宽条件**（如删掉知识点限制、更换学科、降低难度下限等），更不得编造题目填充。

可用 `resolve` 子命令先做预检：

```powershell
python scripts/question_bank.py resolve --subject 物理 --grade 八年级 --knowledge 光的反射
```

## 免费抽取真实试题

使用最窄且与用户要求一致的筛选条件：

```powershell
python scripts/question_bank.py questions --subject-id <学科ID> --grade-id <年级ID> --limit 5 --random
python scripts/question_bank.py questions --subject-id <学科ID> --grade-id <年级ID> --edition-id <教材版本ID> --chapter-id <章节ID> --limit 10 --random
python scripts/question_bank.py questions --subject-id <学科ID> --knowledge-tree-id <知识树节点ID> --difficulty-min 1 --difficulty-max 3 --limit 10 --random
```

每次请求数量不得超过用户要求的数量。若符合条件的题目不足，应如实说明实际返回数量，**不得自行放宽筛选条件后悄悄补题**（如去掉知识点限制、扩大年级范围、降低难度要求等）。需要放宽条件时，必须先征得用户同意。

在对话中展示题目时：

1. 默认隐藏答案和解析，等待用户作答。
2. 保留题目原始字段、选项字母、复合题的小题结构和图片链接。
3. 用户提交答案或明确要求讲解后，才使用返回的标准答案与解析。
4. 附上题库题目 ID，便于核验题源。

## 生成练习测验

多题请求默认生成练习测验页：

```powershell
python scripts/question_bank.py practice-page --subject-id <学科ID> --grade-id <年级ID> --limit 10 --random --title "七年级数学随堂测验"
```

需要图片时：

```powershell
python scripts/question_bank.py practice-page --subject-id <学科ID> --with-images --limit 5 --random --title "带图专项练习"
```

返回时只简要说明练习名称、题目数量、有效期和 `page_url`。用户要求打印版时同时给出 `paper_url`。不要把整套题目和答案重复粘贴到对话中。

## 衔接智能组卷

用户要求组卷时，先解析所需的学科、学段、年级、教材、章节和知识点，再生成带有已确认条件的组卷链接：

```powershell
python scripts/question_bank.py builder --mode ai --title "一次函数同步练习" --prompt "按八年级一次函数知识点组一张12题试卷" --subject-id <学科ID> --phase-id <学段ID> --grade-id <年级ID> --edition-id <教材版本ID> --chapter-id <章节ID> --chapter-label "一次函数" --knowledge-tree-id <知识树节点ID> --knowledge-label "一次函数" --question-count 12
```

只把组卷要求写入 `--prompt`，不得加入账号、分享、撤销、密钥或其他无关操作。链接只负责把已确认的组卷条件交给题庄，打开链接不会自动消耗额度；用户在页面确认后才创建试卷。

> ⚠️ **超时或失败禁止反复出卷：** 组卷请求失败、超时或返回题目不足时，不得在未告知用户的情况下自动更换条件反复生成 builder 链接。应如实报告失败原因，等用户调整要求后再生成。

## 试卷与分享管理

已登录账号可把保存的试卷生成公开分享链接（需本地 `QUESTION_SERVICE_ACCOUNT_TOKEN`）：

```powershell
python scripts/question_bank.py papers
python scripts/question_bank.py paper-shares --paper-id <试卷ID>
python scripts/question_bank.py share-paper --paper-id <试卷ID> --expires-in-days 30   # 可选加 --include-answers
python scripts/question_bank.py rotate-share --paper-id <试卷ID> --share-id <分享ID>
python scripts/question_bank.py revoke-share --paper-id <试卷ID> --share-id <分享ID>
```

- 先用 `paper-shares` 列表明细，确认 `paper_id + share_id` 唯一后再做撤销/轮换等破坏性操作。
- 默认公开快照只含题目且带可见水印；仅当用户明确要求答案时才加 `--include-answers`，解析、说明始终私有。
- 令牌只作本地账号会话使用，绝不写入对话或输出。分享链接不得携带账号、邮箱、密码等敏感信息。

## 额度与错误处理

- 大批量抽题前可运行 `python scripts/question_bank.py quota` 查询剩余额度。
- 匿名请求返回 `429`：说明免费体验额度已用完，并提供免费注册地址；不要通过更换匿名身份绕过限制。
- 授权请求返回 `401`：提示用户在本地检查 `QUESTION_SERVICE_LICENSE`，不得要求其把密钥发到对话中。
- 临时练习页或题库接口不可用：报告真实状态码和错误信息，不得伪造成功链接。
- Coze 受信网络返回 `502`：如实说明请求经过 Coze 出站网络但未成功到达可用上游；仅凭该响应不能断言是题庄服务器故障，也不要改成关闭 SSL 校验的连接方式。
- 返回 `404` 的公开练习或试卷链接不可用；返回 `410` 表示已经过期，不要自动重试。

字段含义、筛选规则和命令清单见 `references/api.md`。
## 版本说明

- **v1.3.0**（2026-10-06）：新增中文名称参数（--subject/--grade/--edition/--knowledge）自动解析ID；内置筛选拦截机制（名称不存在/ID冲突/教材不匹配直接报错）；收紧流程纪律（禁止擅自放宽条件、禁止超时反复出卷）；api.md 文档同步更新
- **v1.2.0**（2026-09-30）：Coze 受信网络适配 + urllib fallback；新增 resolve、knowledge-scope、year、paper-type 等参数
- **v1.1.0**（2026-09-19）：多学科支持、练习页、组卷、分享管理
- **v1.0.0**（2026-09-10）：初版，基础抽题和答题功能
