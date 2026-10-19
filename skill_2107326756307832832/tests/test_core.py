"""Tests for WeChat Article Scraper core modules."""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ---------------------------------------------------------------------------
# core.utils
# ---------------------------------------------------------------------------

class TestParseDateRange:
    def test_with_dates(self):
        from core.utils import parse_date_range
        start, end = parse_date_range("2025-01-01", "2025-01-31")
        assert start.year == 2025 and start.month == 1 and start.day == 1
        assert end.year == 2025 and end.month == 1 and end.day == 31

    def test_with_days(self):
        from core.utils import parse_date_range
        start, end = parse_date_range(days=7)
        assert (end - start).days >= 6

    def test_defaults(self):
        from core.utils import parse_date_range
        start, end = parse_date_range()
        assert (end - start).days >= 29


class TestSanitizeFilename:
    def test_basic(self):
        from core.utils import sanitize_filename
        assert sanitize_filename("hello world") == "hello_world"

    def test_special_chars(self):
        from core.utils import sanitize_filename
        result = sanitize_filename('test<>:"/\\|?*file')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_chinese(self):
        from core.utils import sanitize_filename
        result = sanitize_filename("人民日报文章")
        assert "人民日报文章" in result

    def test_truncation(self):
        from core.utils import sanitize_filename
        long_name = "a" * 200
        result = sanitize_filename(long_name, max_len=50)
        assert len(result) <= 50


class TestIsWechatUrl:
    def test_mp_url(self):
        from core.utils import is_wechat_url
        assert is_wechat_url("https://mp.weixin.qq.com/s/abc123")

    def test_non_wechat(self):
        from core.utils import is_wechat_url
        assert not is_wechat_url("https://www.baidu.com")


class TestKeywordMatching:
    def test_match_any(self):
        from core.utils import match_keywords
        assert match_keywords("今天天气很好", ["天气", "下雨"])
        assert not match_keywords("今天天气很好", ["下雨", "雷电"])

    def test_match_all(self):
        from core.utils import match_keywords
        assert match_keywords("今天天气很好", ["今天", "天气"], match_all=True)
        assert not match_keywords("今天天气很好", ["今天", "下雨"], match_all=True)


class TestRateLimiter:
    def test_basic(self):
        from core.utils import RateLimiter
        limiter = RateLimiter(min_delay=0.01, max_delay=0.02)
        import time
        t0 = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - t0
        assert elapsed >= 0.01  # At least one delay


# ---------------------------------------------------------------------------
# core.filters
# ---------------------------------------------------------------------------

class TestFilterByDate:
    def test_filter(self):
        from core.filters import filter_by_date
        articles = [
            {"pub_date": "2025-01-15"},
            {"pub_date": "2025-02-15"},
            {"pub_date": "2025-03-15"},
        ]
        result = filter_by_date(articles, start_date="2025-01-01", end_date="2025-02-28")
        assert len(result) == 2

    def test_no_filter(self):
        from core.filters import filter_by_date
        articles = [{"pub_date": "2025-01-15"}]
        result = filter_by_date(articles)
        assert len(result) == 1


class TestFilterByKeywords:
    def test_or_mode(self):
        from core.filters import filter_by_keywords
        articles = [
            {"title": "AI改变世界", "content_text": "人工智能技术"},
            {"title": "天气预报", "content_text": "明天会下雨"},
        ]
        result = filter_by_keywords(articles, ["AI", "天气"])
        assert len(result) == 2

    def test_and_mode(self):
        from core.filters import filter_by_keywords
        articles = [
            {"title": "AI改变世界", "content_text": "人工智能技术"},
            {"title": "天气预报", "content_text": "明天会下雨"},
        ]
        result = filter_by_keywords(articles, ["AI", "天气"], match_all=True)
        assert len(result) == 0


class TestDeduplicate:
    def test_dedup_by_title(self):
        from core.filters import deduplicate
        articles = [
            {"title": "文章A", "link": "http://a.com"},
            {"title": "文章A", "link": "http://b.com"},
            {"title": "文章B", "link": "http://c.com"},
        ]
        result = deduplicate(articles)
        assert len(result) == 2

    def test_dedup_by_link(self):
        from core.filters import deduplicate
        articles = [
            {"title": "A", "link": "http://same.com"},
            {"title": "B", "link": "http://same.com"},
        ]
        result = deduplicate(articles)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# core.dedup
# ---------------------------------------------------------------------------

class TestDeduplicator:
    def test_exact_duplicate(self):
        from core.dedup import Deduplicator
        dd = Deduplicator()
        a1 = {"title": "Test", "link": "http://test.com", "content_text": "Hello World"}
        a2 = {"title": "Test", "link": "http://test.com", "content_text": "Hello World"}
        assert not dd.is_duplicate(a1)
        dd.mark_seen(a1)
        assert dd.is_duplicate(a2)

    def test_similar_title(self):
        from core.dedup import Deduplicator
        dd = Deduplicator(similarity_threshold=0.8)
        a1 = {"title": "微信公众号文章爬取工具发布", "link": "http://a.com", "content_text": ""}
        a2 = {"title": "微信公众号文章爬取工具正式发布", "link": "http://b.com", "content_text": ""}
        dd.mark_seen(a1)
        assert dd.is_duplicate(a2)

    def test_not_duplicate(self):
        from core.dedup import Deduplicator
        dd = Deduplicator()
        a1 = {"title": "Article A", "link": "http://a.com", "content_text": "Content A"}
        a2 = {"title": "Article B", "link": "http://b.com", "content_text": "Content B"}
        dd.mark_seen(a1)
        assert not dd.is_duplicate(a2)


# ---------------------------------------------------------------------------
# core.article_parser
# ---------------------------------------------------------------------------

class TestArticleParser:
    def test_parse_meta(self):
        from core.article_parser import extract_article_meta_from_html
        html = '''
        <meta property="og:title" content="测试文章标题">
        <meta name="author" content="测试作者">
        <script>var nickname = "测试公众号";</script>
        <script>var ct = "1700000000";</script>
        '''
        meta = extract_article_meta_from_html(html)
        assert meta["title"] == "测试文章标题"
        assert meta["author"] == "测试作者"
        assert meta["account"] == "测试公众号"

    def test_parse_article(self):
        from core.article_parser import parse_article_html
        html = '''
        <html><head><meta property="og:title" content="测试标题"></head>
        <body>
        <div id="js_content" class="rich_media_content">
        <p>这是第一段内容。</p>
        <p>这是第二段<strong>加粗</strong>内容。</p>
        <img data-src="https://example.com/img1.jpg" alt="图片1">
        </div>
        </body></html>
        '''
        result = parse_article_html(html, "https://mp.weixin.qq.com/s/test")
        assert result["title"] == "测试标题"
        assert "第一段" in result["content_text"]
        assert "**加粗**" in result["content_markdown"]
        assert len(result["images"]) >= 1


# ---------------------------------------------------------------------------
# exporters.zip_exporter
# ---------------------------------------------------------------------------

class TestZipExporter:
    def test_export(self):
        from exporters.zip_exporter import ZipExporter
        articles = [
            {
                "title": "测试文章",
                "account": "测试公众号",
                "author": "作者",
                "pub_date": "2025-01-15",
                "content_text": "这是测试内容。",
                "content_markdown": "# 测试\n\n这是测试内容。",
                "link": "https://mp.weixin.qq.com/s/test",
                "word_count": 7,
                "images": [],
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = ZipExporter(output_dir=tmpdir)
            zip_path = exporter.export(articles, filename="test_export")
            assert os.path.exists(zip_path)

            import zipfile
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                assert any("markdown" in n for n in names)
                assert any("text" in n for n in names)
                assert any("INDEX" in n for n in names)
                assert any("SUMMARY" in n for n in names)
                assert any("metadata" in n for n in names)


# ---------------------------------------------------------------------------
# exporters.obsidian_exporter
# ---------------------------------------------------------------------------

class TestObsidianExporter:
    def test_export(self):
        from exporters.obsidian_exporter import ObsidianExporter
        articles = [
            {
                "title": "Obsidian测试文章",
                "account": "测试号",
                "pub_date": "2025-01-01",
                "content_markdown": "# Hello\n\nContent here.",
                "link": "https://mp.weixin.qq.com/s/obsidian_test",
                "word_count": 4,
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = ObsidianExporter(vault_path=tmpdir)
            result = exporter.export_articles(articles, folder="TestFolder")
            assert result["success"] == 1
            assert os.path.isdir(os.path.join(tmpdir, "TestFolder"))


# ---------------------------------------------------------------------------
# Integration: search + filter pipeline
# ---------------------------------------------------------------------------

class TestPipeline:
    """Test the search→filter→export pipeline with mock data."""

    def test_filter_and_export_pipeline(self):
        from core.filters import filter_by_date, filter_by_keywords, deduplicate
        from exporters.zip_exporter import ZipExporter

        # Mock articles
        articles = [
            {"title": "AI最新进展", "account": "科技日报", "pub_date": "2025-06-01",
             "content_text": "人工智能最新进展报道", "word_count": 100},
            {"title": "天气预报", "account": "气象台", "pub_date": "2025-06-02",
             "content_text": "明天有雨", "word_count": 50},
            {"title": "AI与天气", "account": "科技日报", "pub_date": "2025-05-01",
             "content_text": "AI预测天气", "word_count": 200},
            {"title": "AI最新进展", "account": "科技日报", "pub_date": "2025-06-01",
             "content_text": "人工智能最新进展报道", "word_count": 100},  # duplicate
        ]

        # Step 1: Filter by date
        dated = filter_by_date(articles, start_date="2025-06-01", end_date="2025-06-30")
        assert len(dated) == 3  # Excludes May

        # Step 2: Filter by keyword
        keyworded = filter_by_keywords(dated, ["AI"])
        assert len(keyworded) == 2

        # Step 3: Deduplicate
        deduped = deduplicate(keyworded)
        assert len(deduped) == 1  # Removes duplicate

        # Step 4: Export
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = ZipExporter(output_dir=tmpdir)
            zip_path = exporter.export(deduped, filename="pipeline_test")
            assert os.path.exists(zip_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])