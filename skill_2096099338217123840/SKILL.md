---
name: wechat-cover-generator-pil
display_name: 公众号封面生成器
display_name_en: WeChat Cover Image Generator (PIL)
description: 纯 Python PIL 绘制微信公众号首条封面图（900×383px，2.35:1），支持底图叠加、居中文字和左文右图三种模式。默认暖纸编辑风：奶油纸色背景、炭黑文字、陶土橙主图形，并根据文章主题创作有含义的右侧图标，字号自动缩减适配安全区。仅依赖 Pillow，无需联网。用于生成封面图、画封面图、做封面图、微信封面、公众号封面、左文右图、科技文章头图、模型调研/部署分析/工具测评配图等需求。
description_zh: 纯 PIL 生成 900×383 公众号封面图，默认暖纸编辑风左文右图，零联网依赖
description_en: Draw 900×383 WeChat article cover images with pure PIL; warm-editorial split layout by default, offline and Pillow-only
category: 内容创作
version: 2.3.0
---

# wechat-cover-generator-pil v2.3

纯 Python PIL 绘制微信公众号首条封面图，900×383px（2.35:1）。默认生成左文右图的暖纸编辑风封面；只有用户明确指定时才切换到底图叠加、居中文字或其他配色。

## 默认视觉身份

- 定位：个人科技作者的研究手记，不做成企业宣传物料。
- 背景：暖奶油纸色 `#F4EFE6`。
- 标题：炭黑 `#24221F`。
- 主图形：陶土橙 `#D97757`。
- 辅助色：柔和赭黄 `#C89A45`，只作少量强调。
- 构图：左侧文字约 55%，右侧主题图标约 45%。
- 视觉基准：`assets/default-cover-reference.png`。

默认不使用紫色科技渐变、霓虹光效、条纹或网格背景。不要因为文章涉及 AI 就自动改成深蓝或紫色。

## 风格示例

**TimesFM 封面图**（风格参照）：
- 底图：极简黑曜石深色底图
- 主标题：「我用Google的时间序列模型做了个测试」（32px）
- 副标题：「结果出乎意料」（26px）
- 效果：深色底图 + 白色文字 + 黑色描边，清晰可读

## 调用方式

```python
from scripts.generate_cover import generate_wechat_cover

# 模式1：底图叠加（用户明确提供底图时使用）
generate_wechat_cover(
    main_title  = "主标题文字",
    sub_title   = "副标题文字",
    output_path = "/path/to/output.png",
    bg_image    = "/path/to/bg_image.png",  # 底图路径
)

# 模式2：纯渐变背景
generate_wechat_cover(
    main_title  = "主标题文字",
    sub_title   = "副标题文字",
    output_path = "/path/to/output.png",
    bg_color    = "#0d1b2a",
)

# 模式3：左文右图示（默认；适合技术调研 / 模型部署类）
from scripts.generate_split_cover import generate_split_infographic_cover

generate_split_infographic_cover(
    model_name="Qwen3.8-27B 发布",
    headline="性能调研及部署建议",
    subtitle="评分对比 · 版本差距 · 显存预算",
    center_label="27B",
    diagram_labels=("性能", "量化", "显存", "部署"),
    output_path="/path/to/output.png",
)
```

## 底图叠加模式（仅按需使用）

用户明确要求深色、渐变或指定底图时，使用**用户提供的底图文件**（传入 `bg_image` 路径即可）。常用风格参考：

| 风格 | 适用场景 |
|--------|----------|
| 深蓝波浪 | 科技、工具类 |
| 蓝色电路节点 | 程序员、编程 |
| 极简黑曜石 | AI、机器学习 |
| 蓝宝石网络 | 数据分析 |
| 靛蓝光带 | 新能源、电子 |
| 黑紫奢华渐变 | 高端产品、金融 |

## 核心约束（强制遵守）

- **主标题一行，副标题一行，绝不换行**
- 字号自动缩减（从 80px 往下降到能塞进安全区为止）
- 安全区：画布中央 600×337px，文字不得超出
- 主副标题间距 22px，整体垂直居中于安全区
- 文字带黑色描边（stroke），确保在任何底图上可读
- 输出格式：PNG，900×383px
- **封面一律不加作者署名**（如"XX · …"）——只放眉题/标题/角标 pill，底部留白

### 左文右图示模式

- 左侧宽约 55%，依次放模型名、问题式标题和内容摘要。
- 右侧使用一个整体性主题图标；允许由中心节点和 2–4 个子元素构成，但必须能在缩略图尺寸下读懂。
- 图标先表达文章内容，再考虑装饰。性能调研可使用「推理树 / 量化压缩 / 显存分层 / 部署路径」等母题，不能每篇都套用通用芯片图。
- 默认使用暖奶油纸色纯色背景，不使用条纹、网格或紫色渐变。
- 用户点名 Codex 风格时，提取黑白、高对比、终端精度和克制的荧光绿，不复制 OpenAI/Codex Logo 或界面。
- 用户点名 Claude 风格时，提取暖纸色、陶土橙、人文编辑感和略带手绘感的推理图，不复制 Anthropic/Claude Logo。
- 使用 `scripts/generate_split_cover.py`，保留原居中封面函数不变。

## 输出路径约定

输出到当前工作目录（如 `./output/`，目录不存在会自动创建），文件名包含主题关键词。

## 可选背景色（仅用户明确指定时）

| 主题 | 色值 |
|------|------|
| 深蓝 | `#0d1b2a` |
| 极夜黑 | `#0a0a0f` |
| 科技紫 | `#1a0a2e` |
| 墨绿 | `#0a1a15` |

## 上传到微信（可选）

生成封面图后如需发布，由用户使用自己的公众号凭据上传（本技能不包含上传功能、不保存任何凭据）。若环境中有公众号发布类工具/技能，可将其 `upload_image()` 返回的 `media_id` 用于创建草稿的 `thumb_media_id`。

## 工作流

1. 确定 `main_title` + `sub_title`
2. 从文章主题中提取一个图标母题，并用一句话说明图标为什么与内容有关。
3. 默认调用 `generate_split_infographic_cover()`；只有用户明确提供底图或要求居中文字时才调用 `generate_wechat_cover()`。
4. 输出 PNG 文件到当前工作目录并告知用户路径。
5. 如用户要求发布且环境具备公众号上传能力，再协助上传获取 `media_id`；否则到此为止。

## 验收标准

- 900×383px，关键文字与图标位于安全区。
- 标题在公众号缩略图中仍可读，中文无错字。
- 右侧图标与文章主题有明确关系，且只有一个视觉焦点。
- 没有条纹、网格、通用机器人、密集电路板或无意义小图标。
- 不因参考某产品风格而复制其 Logo、字标或官方界面。

## 依赖

- Python 3
- PIL（Pillow）：`pip install pillow`
- 中文字体：
  - Windows: `NotoSansCJK-Medium.ttc` / `Microsoft YaHei` / `msyh.ttc`
  - macOS: `NotoSansCJK-Medium.ttc` / `Hiragino Sans GB`
  - Linux: `NotoSansCJK-Medium.ttc`
