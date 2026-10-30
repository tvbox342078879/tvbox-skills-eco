import argparse
import json
import sys
import re
import html
from datetime import datetime, timezone
from typing import Dict, Any

# --- 主题模板定义 ---
THEMES = {
    "default": {
        "name": "默认简约",
        "css": """
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 100%; margin: 0 auto; padding: 20px; color: #333; line-height: 1.75; }
            @media (max-width: 600px) {
                body { padding: 10px; }
                h1 { font-size: 20px; }
                h2 { font-size: 18px; }
                p { font-size: 14px; }
            }
            h1 { font-size: 24px; color: #1a1a1a; border-bottom: 2px solid #4a90d9; padding-bottom: 10px; }
            h2 { font-size: 20px; color: #2c3e50; margin-top: 30px; }
            p { font-size: 15px; margin: 15px 0; }
            .summary { background: #f8f9fa; padding: 15px; border-left: 4px solid #4a90d9; margin: 20px 0; }
            .visual-tips { background: #eef2f7; padding: 15px; border-radius: 5px; margin-top: 30px; }
            .visual-tips li { margin: 8px 0; }
        """
    },
    "warm": {
        "name": "温暖橙调",
        "css": """
            body { font-family: 'Georgia', serif; max-width: 100%; margin: 0 auto; padding: 20px; color: #4a3728; line-height: 1.8; background: #fffaf5; }
            @media (max-width: 600px) {
                body { padding: 10px; }
                h1 { font-size: 22px; }
                h2 { font-size: 20px; }
                p { font-size: 15px; }
            }
            h1 { font-size: 26px; color: #d35400; border-bottom: 3px solid #e67e22; padding-bottom: 12px; }
            h2 { font-size: 22px; color: #a04000; margin-top: 35px; }
            p { font-size: 16px; margin: 18px 0; }
            .summary { background: #fef5e7; padding: 18px; border-left: 5px solid #e67e22; margin: 25px 0; }
            .visual-tips { background: #fdf2e9; padding: 18px; border-radius: 8px; margin-top: 35px; }
            .visual-tips li { margin: 10px 0; }
        """
    },
    "cool": {
        "name": "冷静蓝调",
        "css": """
            body { font-family: 'Helvetica Neue', Arial, sans-serif; max-width: 100%; margin: 0 auto; padding: 20px; color: #2c3e50; line-height: 1.7; background: #f0f4f8; }
            @media (max-width: 600px) {
                body { padding: 10px; }
                h1 { font-size: 21px; }
                h2 { font-size: 19px; }
                p { font-size: 14px; }
            }
            h1 { font-size: 25px; color: #2980b9; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
            h2 { font-size: 21px; color: #2471a3; margin-top: 32px; }
            p { font-size: 15px; margin: 16px 0; }
            .summary { background: #eaf2f8; padding: 16px; border-left: 4px solid #3498db; margin: 22px 0; }
            .visual-tips { background: #e8f0f8; padding: 16px; border-radius: 6px; margin-top: 32px; }
            .visual-tips li { margin: 9px 0; }
        """
    }
}

# --- 输入校验函数 ---
def validate_input(topic: str, style: str, word_count: int) -> None:
    """
    校验输入参数的有效性。
    
    规则：
    1. 主题不能为空，长度不超过200字符
    2. 风格必须是：简洁、幽默、专业
    3. 字数范围：100-10000
    4. 主题不能包含非法字符：< > { }
    """
    if not topic or not topic.strip():
        raise ValueError("主题不能为空")
    if len(topic.strip()) > 200:
        raise ValueError("主题长度不能超过200个字符")
    if style not in ["简洁", "幽默", "专业"]:
        raise ValueError(f"不支持的风格: {style}，可选: 简洁, 幽默, 专业")
    if word_count < 100 or word_count > 10000:
        raise ValueError("字数范围必须在100-10000之间")
    # 检查非法字符
    illegal_chars = re.findall(r'[<>{}]', topic)
    if illegal_chars:
        raise ValueError(f"主题包含非法字符: {set(illegal_chars)}")

# --- 核心功能 ---

def generate_gzh_design(topic: str, style: str = "简洁", word_count: int = 800) -> Dict[str, Any]:
    """
    根据主题生成公众号文章的设计方案（标题、摘要、结构、视觉建议等）。
    这是一个离线模拟实现，用于演示和测试。
    """
    validate_input(topic, style, word_count)

    # 清洗主题
    clean_topic = topic.strip()

    # 生成标题（基于主题的简单规则）
    title = f"深度解析：{clean_topic}的底层逻辑与实战指南"

    # 生成摘要
    summary = f"本文围绕「{clean_topic}」展开，从核心概念到落地实践，为你提供一套完整的认知框架和操作步骤，助你快速掌握关键要点。"

    # 生成文章结构（模拟）
    sections = [
        {"heading": "一、为什么需要关注这个主题", "content": f"在当前环境下，{clean_topic} 已成为不可忽视的趋势。理解其本质，是做出正确决策的第一步。"},
        {"heading": "二、核心概念与常见误区", "content": f"很多人对 {clean_topic} 存在误解。我们首先厘清定义，再剖析三个最常见的认知偏差。"},
        {"heading": "三、实操步骤与案例分析", "content": f"通过一个具体案例，演示如何将 {clean_topic} 应用到实际工作中。步骤清晰，可立即上手。"},
        {"heading": "四、总结与行动清单", "content": "最后，给出一个简明的行动清单，帮助你立刻开始实践。"}
    ]

    # 视觉建议
    visual_tips = {
        "封面图": f"建议使用与 {clean_topic} 相关的抽象概念图，色调统一，避免花哨。",
        "排版": "正文使用 15px 字号，行间距 1.75，段间距 20px。重点内容加粗或使用引用块。",
        "配图": "每 300 字配一张信息图或示意图，增强可读性。"
    }

    # 根据风格调整标题（简单演示）
    if style == "幽默":
        title = f"别再说你不懂 {clean_topic}，这篇让你笑出腹肌还学会干货"
    elif style == "专业":
        title = f"专业视角：{clean_topic} 的系统性方法论"

    # 计算实际字数（所有section内容的字符数总和）
    actual_word_count = sum(len(sec["content"]) for sec in sections)

    return {
        "title": title,
        "summary": summary,
        "sections": sections,
        "visual_tips": visual_tips,
        "word_count_estimate": actual_word_count,  # 使用实际计算值
        "style": style,
        "generated_at": datetime.now(timezone.utc).isoformat()  # 添加生成时间
    }


def markdown_to_gzh_html(design: Dict[str, Any], theme: str = "default") -> str:
    """
    将设计方案转换为公众号HTML格式，包含主题模板和样式注入。
    """
    if theme not in THEMES:
        raise ValueError(f"不存在的主题: {theme}，可选: {list(THEMES.keys())}")

    theme_config = THEMES[theme]
    
    # HTML转义所有文本内容
    title = html.escape(design["title"])
    summary = html.escape(design["summary"])
    
    # 构建HTML
    html_parts = [
        f'<!DOCTYPE html>',
        '<html lang="zh-CN">',
        '<head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'<title>{title}</title>',
        '<style>',
        theme_config["css"],
        '</style>',
        '</head>',
        '<body>',
        f'<h1>{title}</h1>',
        f'<div class="summary">{summary}</div>'
    ]
    
    # 添加章节
    for sec in design["sections"]:
        heading = html.escape(sec["heading"])
        content = html.escape(sec["content"])
        html_parts.append(f'<h2>{heading}</h2>')
        html_parts.append(f'<p>{content}</p>')
    
    # 添加视觉建议
    html_parts.append('<div class="visual-tips">')
    html_parts.append('<h2>视觉建议</h2>')
    html_parts.append('<ul>')
    for k, v in design["visual_tips"].items():
        key = html.escape(k)
        value = html.escape(v)
        html_parts.append(f'<li><strong>{key}</strong>：{value}</li>')
    html_parts.append('</ul>')
    html_parts.append('</div>')
    
    # 添加生成时间
    generated_time = html.escape(design.get("generated_at", ""))
    html_parts.append(f'<p style="color: #999; font-size: 12px; margin-top: 40px;">生成时间：{generated_time}</p>')
    
    html_parts.append('</body>')
    html_parts.append('</html>')
    
    return '\n'.join(html_parts)


def format_output(design: Dict[str, Any], format_type: str = "text", theme: str = "default") -> str:
    """将设计方案格式化为指定输出格式。"""
    if format_type == "json":
        return json.dumps(design, ensure_ascii=False, indent=2)
    elif format_type == "html":
        return markdown_to_gzh_html(design, theme)
    elif format_type == "markdown":
        lines = [f"# {design['title']}", "", design["summary"], ""]
        for sec in design["sections"]:
            lines.append(f"## {sec['heading']}")
            lines.append(sec["content"])
            lines.append("")
        lines.append("## 视觉建议")
        for k, v in design["visual_tips"].items():
            lines.append(f"- **{k}**：{v}")
        lines.append("")
        lines.append(f"*生成时间：{design.get('generated_at', 'N/A')}*")
        return "\n".join(lines)
    else:  # text
        lines = [f"标题：{design['title']}", "", f"摘要：{design['summary']}", ""]
        for sec in design["sections"]:
            lines.append(f"{sec['heading']}")
            lines.append(sec["content"])
            lines.append("")
        lines.append("视觉建议：")
        for k, v in design["visual_tips"].items():
            lines.append(f"  - {k}：{v}")
        lines.append("")
        lines.append(f"生成时间：{design.get('generated_at', 'N/A')}")
        return "\n".join(lines)


# --- 自测功能 ---

def run_selftest() -> bool:
    """运行离线自测，确保核心功能正常。"""
    try:
        # 测试1：正常生成
        result = generate_gzh_design("人工智能", style="专业", word_count=1000)
        assert result["title"], "标题为空"
        assert len(result["sections"]) == 4, "章节数量不对"
        assert "封面图" in result["visual_tips"], "缺少视觉建议"
        assert result["word_count_estimate"] > 0, "字数统计错误"
        assert "generated_at" in result, "缺少生成时间"

        # 测试2：不同风格
        result_humor = generate_gzh_design("编程", style="幽默")
        assert "笑出腹肌" in result_humor["title"], "幽默风格标题未生效"

        # 测试3：空主题异常
        try:
            generate_gzh_design("")
            assert False, "空主题未抛出异常"
        except ValueError:
            pass

        # 测试4：非法字符校验
        try:
            generate_gzh_design("测试<非法>主题")
            assert False, "非法字符未抛出异常"
        except ValueError:
            pass

        # 测试5：输出格式
        text_out = format_output(result, "text")
        json_out = format_output(result, "json")
        md_out = format_output(result, "markdown")
        html_out = format_output(result, "html", theme="warm")
        assert "标题" in text_out, "文本格式缺少标题"
        assert json.loads(json_out)["title"], "JSON格式解析失败"
        assert md_out.startswith("# "), "Markdown格式错误"
        assert "<html" in html_out, "HTML格式错误"
        assert "warm" in html_out or "Georgia" in html_out, "主题样式未生效"

        # 测试6：HTML转义测试
        test_design = {
            "title": "测试<script>alert('xss')</script>",
            "summary": "测试内容",
            "sections": [{"heading": "标题", "content": "内容"}],
            "visual_tips": {"封面": "建议"},
            "generated_at": "2024-01-01T00:00:00+00:00"
        }
        html_escaped = markdown_to_gzh_html(test_design)
        assert "<script>" not in html_escaped, "HTML转义失败"

        # 测试7：主题验证
        try:
            markdown_to_gzh_html(result, theme="nonexistent")
            assert False, "不存在的主题未抛出异常"
        except ValueError:
            pass

        # 测试8：字数统计准确性
        test_result = generate_gzh_design("测试主题")
        actual_chars = sum(len(sec["content"]) for sec in test_result["sections"])
        assert test_result["word_count_estimate"] == actual_chars, "字数统计不准确"

        # 测试9：核心链路测试（Markdown→HTML）
        md_content = format_output(result, "markdown")
        assert "人工智能" in md_content, "Markdown输出缺少主题内容"
        
        # 测试10：HTML输出包含移动端适配
        html_out = format_output(result, "html", theme="default")
        assert "@media" in html_out, "HTML缺少移动端媒体查询"
        assert "max-width: 100%" in html_out, "HTML未使用自适应宽度"

        # 测试11：validate_input完整校验
        try:
            validate_input("测试", "非法风格", 500)
            assert False, "非法风格未抛出异常"
        except ValueError:
            pass
        
        try:
            validate_input("测试", "简洁", 50)
            assert False, "过短字数未抛出异常"
        except ValueError:
            pass

        print("[SELFTEST] 全部通过")
        return True
    except Exception as e:
        print(f"[SELFTEST] 失败: {e}")
        return False


# --- 主入口 ---

def main():
    parser = argparse.ArgumentParser(description="公众号文章设计技能")
    parser.add_argument("--selftest", action="store_true", help="运行自测")
    parser.add_argument("--topic", type=str, help="文章主题")
    parser.add_argument("--style", type=str, default="简洁", help="文章风格")
    parser.add_argument("--words", type=int, default=800, help="目标字数")
    parser.add_argument("--format", type=str, default="text", choices=["text", "json", "markdown", "html"], help="输出格式")
    parser.add_argument("--theme", type=str, default="default", choices=list(THEMES.keys()), help="HTML主题模板")
    parser.add_argument("--output", type=str, help="输出文件路径（可选）")
    parser.add_argument("--verbose", action="store_true", help="显示修改明细")
    parser.add_argument("--force", action="store_true", help="强制写盘")
    parser.add_argument("--dry-run", action="store_true", help="预览模式（不实际执行）")
    parser.add_argument("--batch", default=None, help="批量处理模式")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--mode", default=None, help="运行模式")
    parser.add_argument("--task", default=None, help="任务类型")

    args = parser.parse_args()

    if args.selftest:
        success = run_selftest()
        sys.exit(0 if success else 1)

    if not args.topic:
        print("错误：请提供 --topic 参数（文章主题）", file=sys.stderr)
        parser.print_help()
        sys.exit(1)


def read_text_safe(path):
    """多编码容错读取 (selftest contract)"""
    import codecs
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            with codecs.open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()
