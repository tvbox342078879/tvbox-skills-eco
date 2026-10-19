# 试卷出题与组卷 · 上架材料包

## 1. 上传包说明

- **上传文件名**：`exam-paper-generator.zip`
- **包内结构树**（内容在压缩包根目录，无外层目录）：

```
exam-paper-generator.zip
├── SKILL.md                  # 技能正文（含 7 项 frontmatter）
├── scripts/
│   └── exam-paper-generator.py             # 结构化方案生成器（纯标准库、离线）
├── test-samples/
│   ├── make_samples.py       # 造样本需求与输出
│   ├── run_tests.py          # 离线回归测试（13 条断言）
│   └── cases.json            # 截图用例配置
└── README-SUBMIT.md          # 本文件

```

- **已排除项**：`assets/`（封面与截图）不进上传包；`__pycache__/`、`*.pyc`、`.DS_Store` 由打包脚本自动清理。

## 2. frontmatter 7 字段表

| 字段 | 值 |
|---|---|
| name | exam-paper-generator |
| version | 1.0.0 |
| display_name | 试卷出题与组卷 |
| display_name_en | Exam Paper Generator |
| description_zh | Education assistant for teachers |
| description_en | Education assistant for teachers. Generates student comments and parent communication scripts, wrong-question books with similar exercises, exam papers with blueprints and layered homework designs. Outputs templates and materials, not graded assessments. |
| description | 用户要做「试卷出题与组卷」时使用；只做试卷出题与组卷相关方法，不做需专业资质的结论。 |

**平台表单另需填写项**：

- 分类：按所属赛道归类（教师教育）
- 标签：对应赛道与中文名关键词
- 运行依赖：无（仅 Python 标准库，离线）
- 是否联网：完全离线
- 定价：免费

## 3. 介绍文案

**一句话版（≤60 字）**：试卷出题与组卷 助手——按知识点与难度出卷，给双向细目表，配套离线脚本生成结构化方案，标注使用边界。

**标准版（约 150 字）**：试卷出题与组卷 方法论助手：按「教师教育」场景给可落地的结构、清单与口径框架，配套纯标准库离线脚本，覆盖从目标确认到交付的完整流程，并标注使用边界与下一步动作。

**详细版**：面向「试卷出题与组卷」阶段的方法论技能。核心能力覆盖：评语话术：批量出评语模板与家长会沟通脚本；错题本：把错题归类并生成同类变式练习；出题组卷：按知识点与难度出卷，给双向细目表；作业设计：按课时目标出分层作业与批改要点。配套 scripts/exam-paper-generator.py 是纯标准库、离线可用的结构化方案生成器，--json 可接入自动化；内置 13 条离线回归断言。边界：生成内容需教师结合学情审核使用，不替代教学判断与评分。

**英文版**：Education assistant for teachers. Generates student comments and parent communication scripts, wrong-question books with similar exercises, exam papers with blueprints and layered homework designs. Outputs templates and materials, not graded assessments.

## 4. 效果截图说明

截图由 `make_screenshots.py` 对 `scripts/exam-paper-generator.py` 的真实运行输出渲染（图上每行来自实跑结果）。封面与截图仅用于上架展示，不进上传包。

## 5. 使用示例

```bash
python3 scripts/exam-paper-generator.py "按知识点与难度出卷，给双向细目表"
python3 scripts/exam-paper-generator.py --json "按知识点与难度出卷，给双向细目表"
python3 test-samples/run_tests.py
```

## 6. 安全声明（逐条列明不做什么）

- 不做需要专业资质的结论（法律、税务、医疗、金融的最终判定）。
- 不联网、不读取工作区或用户隐私文件。
- 数值类只给口径与公式，税率/政策以当地现行规定为准。
- 形态/场景判定为确定性规则的启发式产物，执行时结合上下文复核。

## 7. 实测记录

- **环境**：macOS (darwin)，Python 3.13.12（WorkBuddy 托管运行时）。
- **回归测试**：`python3 test-samples/run_tests.py` → **13/13 通过**，退出码 0（托管 Python 与系统 /usr/bin/python3 双跑均过）。

## 8. 审核常见问题应答口径

- **Q：它能直接替我做完吗？** A：它产出结构化方案、清单与口径，具体填装与专业结论由用户完成。
- **Q：技能是否联网？** A：完全离线；脚本只处理传入的需求文本。

## 9. 提交材料清单

- [x] exam-paper-generator.zip（内容在根目录、无外层目录、非 ASCII 路径 0 处）
- [x] SKILL.md（7 项 frontmatter 齐全，无引号、无 ASCII 冒号+空格）
- [x] scripts/exam-paper-generator.py（纯标准库、离线）
- [x] test-samples/（make_samples.py / run_tests.py / cases.json，13/13 通过）
- [x] README-SUBMIT.md（本文件，十一节齐全）

## 10. 上架后维护建议

- **版本路线**：v1.0.0 覆盖本技能核心方法论 + 离线脚本 + 回归测试。后续可随赛道补充子场景。
- **要盯的变化源**：所属赛道的政策/平台规则更新；frontmatter 校验规则更新。
- **回归纪律**：改 `scripts/exam-paper-generator.py` 的 DATA 常量后必须重跑 `run_tests.py`。

## 11. 边界声明

本技能产出可安装的技能包与上架材料，不代替平台审核，上传动作由用户本人完成。本技能只出结构、清单与口径：不替代法律/税务/医疗/金融等专业结论，相关事项请咨询对应持证专业人士。