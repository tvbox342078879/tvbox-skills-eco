---
name: extract-images
version: 1.0.1
display_name: 提取图片
display_name_en: Web Image Extractor
description_zh: 提取网页或公众号文章正文图片并保存为 JPG。自动定位正文区域，去除广告图、Logo、头像、二维码、图标、背景图等无关内容，统一转换为 JPG 格式并保存到同一文件夹。当用户需要从网页链接、公众号文章链接、新闻页面、博客文章中提取正文配图、下载文章图片、抓取页面图片时使用。触发词：提取图片、下载文章图片、抓取网页图片、保存正文配图、提取公众号图片。
description_en: Extracts main-content images from web pages or WeChat official-account articles and saves them as JPG files. Automatically locates the article body, filters out ads, logos, avatars, QR codes, icons and background images, converts everything to JPG, and saves all images into one folder. Use when the user needs to extract or download article images from a web link, news page or blog post.
---


# 提取图片

从指定网页或公众号文章链接中提取正文（主内容）图片，自动过滤广告图、Logo、头像、二维码、图标、背景图等无关内容，统一转换为 JPG 格式并自动保存到同一个文件夹。

## 使用方式

### 步骤

1. 向用户确认或提取目标链接 `url`（网页链接或公众号文章链接均可）。
2. 确定保存目录 `output`（默认为 `extracted_images`，建议按文章标题或时间命名，例如 `output/微信文章配图`）。
3. 执行脚本：

```bash
python <skill_dir>/scripts/extract_images.py --url "目标链接" --output <保存目录>
```

4. 脚本运行完成后，向用户汇报保存目录与成功/失败张数，并用 `yyb-image-gallery` 或文件列表展示结果。

### 脚本说明

- `scripts/extract_images.py` 完成全部流程：抓取页面 → 定位正文区域 → 提取并过滤图片 → 下载 → 转 JPG → 保存。
- 正文定位优先级：微信公众号正文（`#js_content` / `.rich_media_content`）→ `article` → 常见正文容器 → 按图片密度兜底。
- 图片来源优先取懒加载属性（`data-src` 等），兼容微信公众号。
- 过滤规则：命中广告/Logo/头像/二维码/图标/背景/占位等关键词的节点或其祖先节点，以及 `data:`、`.svg`、1x1 占位图、像素追踪等 URL 特征，一律剔除。
- JPG 转换：GIF 取首帧；PNG 透明底自动铺白色背景，保证输出图片无透明黑底问题。
- 输出文件名为 `001.jpg`、`002.jpg` … 顺序编号。

## 注意事项

- 脚本依赖 `requests`、`beautifulsoup4`、`Pillow`，如缺失需先用 `pip install requests beautifulsoup4 Pillow` 安装。
- 部分网站（尤其微信公众号）有反爬风控，脚本已内置 UA 伪装、重试与忽略 SSL 证书校验；若仍失败，如实告知用户无法提取。
- 若页面正文图片过少或为纯广告页，脚本可能返回 0 张，需如实反馈，不要虚构结果。
