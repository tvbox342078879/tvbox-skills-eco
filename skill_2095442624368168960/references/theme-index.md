# 主题索引（唯一真源）

> 本文件是主题选择的**唯一真源**。选定主题后，**只加载对应的 `theme-<id>.md`**，不要同时读多套主题，避免混色。

---

## 三套主题速览

| ID | 中文名 | 气质 | 主色 | 纸底 | 最适合 |
|---|---|---|---|---|---|
| `indigo-archive` | 靛蓝档案 | 档案馆 / 学术卷宗，衬线标题、金色点睛、克制严肃 | `#2B3A67` 靛蓝 + `#B8860B` 金 | `#F4F6FB` 冷白纸 | 深度分析、行业报告、观点长文、案例复盘 |
| `sand-magazine` | 暖砂杂志 | 杂志内页 / 长报道，暖砂底、首字下沉、pull quote | `#A85C27` 琥珀 + `#2F6B5A` 松绿 | `#FDF8F1` 暖砂 | 人物故事、行业观察、生活方式、叙事长文 |
| `electric-blueprint` | 电光蓝图 | 工程蓝图 / 产品规格书，等宽元数据、方括号编号、斑马纹参数表 | `#0B63CE` 电光蓝 + `#00A9C4` 青 | `#F5F8FC` 冷白纸 | 技术教程、产品测评、架构拆解、参数横评、踩坑记录 |

---

## 主题选择决策

```
先看内容主轴
│
├─ 是「讲清楚一个技术/产品/方案」？
│     └─ 有代码、配置、参数、目录结构 ──────────────→ electric-blueprint
│
├─ 是「输出一个判断 / 分析 / 观点」？
│     └─ 有数据、有引用、需要权威感 ────────────────→ indigo-archive
│
├─ 是「讲一个故事 / 一段经历 / 一个人的观察」？
│     └─ 重叙事、重画面、重情绪 ────────────────────→ sand-magazine
│
└─ 用户已指定主题 ──────────────────────────────────→ 直接用指定的，不推测
```

**默认兜底**：用户没指定、内容类型也不明确时，用 `electric-blueprint`（技术类账号最高频，且对纯文字长文也成立）。

**混用禁令**：一篇只用一套主题。禁止「A 主题的标题 + B 主题的卡片」—— 边框色、圆角、字体栈都不同，拼在一起会立刻显脏。

---

## 各主题权威值（跨文件一致性的唯一依据）

### indigo-archive 靛蓝档案

| 项 | 值 |
|---|---|
| 主色 PRIMARY | `#2B3A67` |
| 点睛 ACCENT | `#B8860B` |
| 深字 INK | `#18203F` |
| 正文 TEXT | `#464C66` |
| 次要 SUB | `#6B7290` |
| 弱化 FADE | `#9BA2B8` |
| 边框 BORDER | `#C8D0E4` |
| 纸底 PAPER | `#F4F6FB` |
| 浅底 TINT | `#E6EAF5` |
| 代码块底 CODE_BG | `#1E2540` |
| 代码字 CODE_TEXT | `#D7DEEF` |
| 代码强调 CODE_KEY | `#E3B23C` |
| **关键词下划线** | `border-bottom:2px solid #B8860B;font-weight:600;color:#18203F;` |
| 标题字体栈 | `Georgia,'Songti SC','Noto Serif CJK SC','Source Han Serif SC',serif` |
| 正文字号 / 行高 | 15px / 1.9 |
| 圆角 | 4px |
| 组件文件 | `references/theme-indigo-archive.md` |

### sand-magazine 暖砂杂志

| 项 | 值 |
|---|---|
| 主色 PRIMARY | `#A85C27` |
| 点睛 ACCENT | `#2F6B5A` |
| 深字 INK | `#3B2E24` |
| 正文 TEXT | `#5C4C3C` |
| 次要 SUB | `#7D6B58` |
| 弱化 FADE | `#A89684` |
| 边框 BORDER | `#E3D5C3` |
| 纸底 PAPER | `#FDF8F1` |
| 浅底 TINT | `#F5E9D8` |
| 珊瑚红（警示） | `#C2523C` |
| 代码块底 CODE_BG | `#2E2820` |
| 代码字 CODE_TEXT | `#E8DFD0` |
| 代码强调 CODE_KEY | `#C98A4B` |
| **关键词下划线** | `border-bottom:2px solid #A85C27;font-weight:600;color:#3B2E24;` |
| 标题字体栈 | `Georgia,'Songti SC','Noto Serif CJK SC','Source Han Serif SC',serif` |
| 正文字号 / 行高 | 16px / 2.0 |
| 圆角 | 10px（三套中最柔和，杂志感） |
| 阴影 | `box-shadow:0 2px 10px -4px rgba(120,90,60,0.18)` —— 仅本主题允许，极轻暖阴影 |
| 组件文件 | `references/theme-sand-magazine.md` |
| 注意 | 首字下沉用 `float`，**公众号会剥离** → 只在本地预览/导出时可见，交付给公众号的版本改用「大字号内联 span」退化为无环绕效果 |

### electric-blueprint 电光蓝图

| 项 | 值 |
|---|---|
| 主色 PRIMARY | `#0B63CE` |
| 点睛 ACCENT | `#00A9C4` |
| 深字 INK | `#0E1726` |
| 正文 TEXT | `#3D4A5C` |
| 次要 SUB | `#6E7C90` |
| 弱化 FADE | `#A3AFC0` |
| 边框 BORDER | `#D5E1F0` |
| 纸底 PAPER | `#F5F8FC` |
| 浅底 TINT | `#E4EEFB` |
| 代码块底 CODE_BG | `#0E1726` |
| 代码字 CODE_TEXT | `#CBD9EC` |
| 代码强调 CODE_KEY | `#4DD0E1` |
| **关键词下划线** | `border-bottom:2px solid #0B63CE;font-weight:600;color:#0B63CE;` |
| 标题字体栈 | `-apple-system,system-ui,'PingFang SC','Microsoft YaHei',sans-serif` |
| 等宽字体栈 | `ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace` |
| 正文字号 / 行高 | 15px / 1.9 |
| 圆角 | 3px |
| 组件文件 | `references/theme-electric-blueprint.md` |

---

## 加载规则

1. 选题后**只读** `references/theme-<id>.md` 一个文件。
2. 需要代码块、表格、图片、标签等通用件时，再读 `references/common-components.md`，并用本文件的**权威值表**替换其中的 `{{占位符}}`。
3. 排版过程中对红线的任何疑问，查 `references/platform-rules.md`。
4. **不得**把本文件的色值凭记忆写进 HTML —— 一律从对应 `theme-<id>.md` 的组件里复制，避免手误产生第四种颜色。

---

## 扩展新主题

新增主题必须同时更新三处，否则 `scripts/lint_themes.py` 会报错：

1. 新建 `references/theme-<id>.md`，结构对齐现有主题（18 个组件 + 骨架 + 配方 + 映射表）。
2. 在**本文件**补一行速览 + 一张权威值表。
3. 跑 `python scripts/lint_themes.py` 确认色值一致性通过。

新主题必须满足：主色与点睛色**不得与已有主题重复**，正文字号 15~16px，圆角 ≤10px，无渐变（渐变在微信内渲染不稳定）。阴影仅 `sand-magazine` 允许使用极轻暖阴影，其余主题靠边框与色块分层。
