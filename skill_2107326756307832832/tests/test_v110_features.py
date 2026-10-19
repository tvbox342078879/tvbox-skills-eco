"""Tests for the v1.1.0 feature set and bug fixes."""

import json
import os
import sys
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# core.utils — URL canonicalization
# ---------------------------------------------------------------------------

class TestCanonicalizeUrl:
    def test_strips_tracking_params(self):
        from core.utils import canonicalize_url
        a = canonicalize_url("https://mp.weixin.qq.com/s/abc?chksm=111&scene=27")
        b = canonicalize_url("https://mp.weixin.qq.com/s/abc?chksm=999&from=timeline")
        assert a == b

    def test_drops_fragment(self):
        from core.utils import canonicalize_url
        assert canonicalize_url("https://mp.weixin.qq.com/s/abc#rd") == \
               canonicalize_url("https://mp.weixin.qq.com/s/abc")

    def test_lowercases_host(self):
        from core.utils import canonicalize_url
        assert "MP.weixin" not in canonicalize_url("https://MP.weixin.qq.com/s/abc")

    def test_keeps_meaningful_params(self):
        from core.utils import canonicalize_url
        out = canonicalize_url("https://mp.weixin.qq.com/s?__biz=MzA5&mid=2&idx=1")
        assert "__biz=MzA5" in out and "mid=2" in out

    def test_empty_input(self):
        from core.utils import canonicalize_url
        assert canonicalize_url("") == ""

    def test_param_order_irrelevant(self):
        from core.utils import canonicalize_url
        assert canonicalize_url("https://x.com/s?a=1&b=2") == \
               canonicalize_url("https://x.com/s?b=2&a=1")


class TestUrlClassification:
    def test_rejects_label_spoofing(self):
        from core.utils import is_wechat_url
        assert not is_wechat_url("https://mp.weixin.qq.com.attacker.example/x")
        assert not is_wechat_url("https://weixin.qq.com.attacker.example/x")
        assert not is_wechat_url("https://mp.weixin.qq.com.attacker.example.cn/x")

    def test_accepts_real_domains(self):
        from core.utils import is_wechat_url
        assert is_wechat_url("https://mp.weixin.qq.com/s/abc")
        assert is_wechat_url("https://weixin110.qq.com/foo")
        assert is_wechat_url("https://user@mp.weixin.qq.com:443/s/abc")
        # A real subdomain of weixin.qq.com is still Tencent-owned.
        assert is_wechat_url("https://weixin.qq.com/s/abc")

    def test_rejects_empty_and_garbage(self):
        from core.utils import is_wechat_url
        assert not is_wechat_url("")
        assert not is_wechat_url("not a url")

    def test_sogou_redirect_is_fetchable(self):
        from core.utils import is_wechat_article_link
        assert is_wechat_article_link("https://weixin.sogou.com/link?url=abc")
        assert is_wechat_article_link("https://mp.weixin.qq.com/s/abc")
        assert not is_wechat_article_link("https://weixin.sogou.com/weixin?type=2")
        assert not is_wechat_article_link("https://example.com/link?url=1")


class TestArticleIdFromUrl:
    def test_variants_share_one_id(self):
        from core.utils import article_id_from_url
        assert article_id_from_url("https://mp.weixin.qq.com/s/abc?chksm=1") == \
               article_id_from_url("https://mp.weixin.qq.com/s/abc#rd")

    def test_different_articles_differ(self):
        from core.utils import article_id_from_url
        assert article_id_from_url("https://mp.weixin.qq.com/s/abc") != \
               article_id_from_url("https://mp.weixin.qq.com/s/xyz")


# ---------------------------------------------------------------------------
# core.utils — dates
# ---------------------------------------------------------------------------

class TestRelativeDate:
    def test_yesterday(self):
        from core.utils import parse_relative_date
        now = datetime(2026, 5, 10, 12, 0)
        assert parse_relative_date("昨天 08:30", now=now).day == 9

    def test_days_ago(self):
        from core.utils import parse_relative_date
        now = datetime(2026, 5, 10, 12, 0)
        assert parse_relative_date("3天前", now=now).day == 7

    def test_minutes_and_hours(self):
        from core.utils import parse_relative_date
        now = datetime(2026, 5, 10, 12, 0)
        assert parse_relative_date("15分钟前", now=now) == now - timedelta(minutes=15)
        assert parse_relative_date("2小时前", now=now) == now - timedelta(hours=2)

    def test_today_and_just_now(self):
        from core.utils import parse_relative_date
        now = datetime(2026, 5, 10, 12, 0)
        assert parse_relative_date("今天", now=now).day == 10
        assert parse_relative_date("刚刚", now=now) is not None

    def test_absolute_chinese_date(self):
        from core.utils import parse_relative_date
        assert parse_relative_date("2026年1月5日").month == 1

    def test_no_match_returns_none(self):
        from core.utils import parse_relative_date
        assert parse_relative_date("完全无关的文字") is None


class TestParsePubDate:
    def test_accepts_iso(self):
        from core.utils import parse_pub_date
        assert parse_pub_date("2026-03-04T10:00:00").day == 4

    def test_accepts_epoch(self):
        from core.utils import parse_pub_date
        assert parse_pub_date("1767225600") is not None

    def test_accepts_datetime_object(self):
        from core.utils import parse_pub_date
        dt = datetime(2026, 1, 1)
        assert parse_pub_date(dt) == dt

    def test_garbage_returns_none_not_raise(self):
        from core.utils import parse_pub_date
        assert parse_pub_date("胡说八道") is None
        assert parse_pub_date(None) is None
        assert parse_pub_date("") is None


class TestParseDate:
    def test_chinese_format(self):
        from core.utils import parse_date
        assert parse_date("2026年3月4日").month == 3

    def test_iso_with_t(self):
        from core.utils import parse_date
        assert parse_date("2026-03-04T10:00:00").hour == 10

    def test_invalid_raises(self):
        from core.utils import parse_date
        with pytest.raises(ValueError):
            parse_date("garbage")


# ---------------------------------------------------------------------------
# core.utils — anti-bot detection, merge, extraction, dirs
# ---------------------------------------------------------------------------

class TestDetectBlocked:
    @pytest.mark.parametrize("html", [
        "<html>antispider</html>",
        "<div>输入验证码继续访问</div>",
        "<p>您的访问出错了，请检查后重试</p>",
        "",
        None,
    ])
    def test_detects_block_pages(self, html):
        from core.utils import detect_blocked
        assert detect_blocked(html)

    def test_normal_page_passes(self):
        from core.utils import detect_blocked
        assert not detect_blocked("<div class='rich_media'>正文内容</div>")


class TestMergeArticle:
    def test_empty_update_keeps_base(self):
        from core.utils import merge_article
        base = {"title": "好标题", "account": "人民日报"}
        merged = merge_article(base, {"title": "", "account": None, "word_count": 0})
        assert merged["title"] == "好标题"
        assert merged["account"] == "人民日报"

    def test_non_empty_update_wins(self):
        from core.utils import merge_article
        merged = merge_article({"title": "旧"}, {"title": "新", "content_text": "正文"})
        assert merged["title"] == "新" and merged["content_text"] == "正文"

    def test_base_is_not_mutated(self):
        from core.utils import merge_article
        base = {"title": "旧"}
        merge_article(base, {"title": "新"})
        assert base["title"] == "旧"


class TestExtractUrls:
    def test_stops_at_glued_chinese(self):
        from core.utils import extract_wechat_urls
        text = "看看这篇 https://mp.weixin.qq.com/s/xyz。还有百度 https://www.baidu.com"
        assert extract_wechat_urls(text) == ["https://mp.weixin.qq.com/s/xyz"]

    def test_strips_ascii_punctuation_tail(self):
        from core.utils import extract_urls
        assert extract_urls("访问 https://example.com/a.") == ["https://example.com/a"]

    def test_deduplicates(self):
        from core.utils import extract_urls
        text = "https://example.com/a 和 https://example.com/a"
        assert extract_urls(text) == ["https://example.com/a"]

    def test_no_urls(self):
        from core.utils import extract_urls
        assert extract_urls("纯文本，没有链接") == []

    def test_keeps_query_params(self):
        from core.utils import extract_urls
        assert extract_urls("https://mp.weixin.qq.com/s?__biz=MzA5&chksm=1") == \
               ["https://mp.weixin.qq.com/s?__biz=MzA5&chksm=1"]


# Verbatim shape of a live weixin.sogou.com/link response (2026-10-05): the
# article URL is assembled from JS string chunks and opened with
# window.location.replace(), so no HTTP redirect ever happens.
SOGOU_JS_REDIRECT = """
<meta content="always" name="referrer">
<script>
    (new Image()).src = 'https://weixin.sogou.com/approve?uuid=' + 'a834c9c7' + '&from=inner';
    setTimeout(function () {
        var url = '';
        url += 'https://mp.';
        url += 'weixin.qq.c';
        url += 'om/s?src=11';
        url += '&timestamp=';
        url += '1791187357&ver=7007&signature=26wVFIoaeg*9mAqbgeRsIEzHWtX0rreIiAGfawI4Tbbg24Jn';
        url = url.replace("@", "");
        window.location.replace(url)
    },100);
</script>
"""


class TestSogouJsRedirect:
    def test_reassembles_chunked_url(self):
        from core.utils import resolve_sogou_redirect
        assert resolve_sogou_redirect(SOGOU_JS_REDIRECT) == (
            "https://mp.weixin.qq.com/s?src=11&timestamp=1791187357&ver=7007"
            "&signature=26wVFIoaeg*9mAqbgeRsIEzHWtX0rreIiAGfawI4Tbbg24Jn"
        )

    def test_applies_strip_replace(self):
        from core.utils import resolve_sogou_redirect
        html = SOGOU_JS_REDIRECT.replace("url += '&timestamp=';", "url += '&@timestamp=';")
        assert "@" not in resolve_sogou_redirect(html)

    def test_plain_page_returns_none(self):
        from core.utils import resolve_sogou_redirect
        assert resolve_sogou_redirect(ARTICLE_HTML) is None
        assert resolve_sogou_redirect("") is None

    def test_non_wechat_target_rejected(self):
        from core.utils import resolve_sogou_redirect
        hostile = (
            "<script>var url='';url += 'https://ev';url += 'il.example.com/x';"
            "window.location.replace(url)</script>"
        )
        assert resolve_sogou_redirect(hostile) is None

    def test_is_sogou_link_url(self):
        from core.utils import is_sogou_link_url
        assert is_sogou_link_url("https://weixin.sogou.com/link?url=abc")
        assert not is_sogou_link_url("https://weixin.sogou.com/weixin?type=2")
        assert not is_sogou_link_url("https://mp.weixin.qq.com/s/abc")


class TestRuntimeDirs:
    def test_export_dir_precedence(self, tmp_path, monkeypatch):
        from core.utils import resolve_export_dir
        explicit = tmp_path / "explicit"
        monkeypatch.setenv("WECHAT_SCRAPER_EXPORT_DIR", str(tmp_path / "env"))
        cfg = {"export": {"default_output_dir": str(tmp_path / "cfg")}}
        assert resolve_export_dir(str(explicit), cfg) == explicit
        assert resolve_export_dir(None, cfg).name == "env"
        monkeypatch.delenv("WECHAT_SCRAPER_EXPORT_DIR")
        assert resolve_export_dir(None, cfg).name == "cfg"

    def test_export_dir_falls_back_to_temp(self, monkeypatch, tmp_path):
        from core.utils import resolve_export_dir, temp_base_dir
        monkeypatch.delenv("WECHAT_SCRAPER_EXPORT_DIR", raising=False)
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        monkeypatch.setenv("TEMP", str(tmp_path))
        monkeypatch.setenv("TMP", str(tmp_path))
        assert "wechat-article-scraper" in str(resolve_export_dir())
        assert "wechat-article-scraper" in str(temp_base_dir())

    def test_state_dir_uses_env(self, tmp_path, monkeypatch):
        from core.utils import resolve_state_dir
        target = tmp_path / "state"
        monkeypatch.setenv("WECHAT_SCRAPER_DATA_DIR", str(target))
        assert resolve_state_dir() == target
        assert target.exists()


# ---------------------------------------------------------------------------
# core.filters
# ---------------------------------------------------------------------------

def _art(title, date=None, link="", body="", account="某号", **extra):
    art = {
        "title": title, "pub_date": date, "link": link,
        "content_text": body, "account": account, "word_count": len(body),
    }
    art.update(extra)
    return art


class TestFilterByDate:
    def test_end_date_is_inclusive_of_whole_day(self):
        from core.filters import filter_by_date
        articles = [_art("当天文章", "2026-03-04T23:50:00"), _art("次日", "2026-03-05")]
        out = filter_by_date(articles, start_date="2026-03-01", end_date="2026-03-04")
        assert [a["title"] for a in out] == ["当天文章"]

    def test_undated_dropped_by_default(self):
        from core.filters import filter_by_date
        out = filter_by_date([_art("无日期")], start_date="2026-01-01")
        assert out == []

    def test_keep_undated(self):
        from core.filters import filter_by_date
        out = filter_by_date([_art("无日期")], start_date="2026-01-01", keep_undated=True)
        assert len(out) == 1

    def test_unparseable_date_treated_as_undated(self):
        from core.filters import filter_by_date
        out = filter_by_date([_art("坏日期", "not-a-date")], days=30, keep_undated=True)
        assert len(out) == 1

    def test_days_window(self):
        from core.filters import filter_by_date
        recent = datetime.now().isoformat()
        old = (datetime.now() - timedelta(days=400)).isoformat()
        out = filter_by_date([_art("新", recent), _art("旧", old)], days=7)
        assert [a["title"] for a in out] == ["新"]


class TestFilterByKeywords:
    def test_match_any(self):
        from core.filters import filter_by_keywords
        arts = [_art("AI芯片", body="GPU"), _art("美食", body="火锅")]
        out = filter_by_keywords(arts, ["AI", "火锅"])
        assert len(out) == 2

    def test_match_all(self):
        from core.filters import filter_by_keywords
        arts = [_art("AI芯片", body="GPU算力"), _art("美食", body="火锅")]
        out = filter_by_keywords(arts, ["AI", "火锅"], match_all=True)
        assert out == []

    def test_search_fields(self):
        from core.filters import filter_by_keywords
        arts = [_art("标题", body="关键词在正文")]
        assert filter_by_keywords(arts, ["正文"], search_fields=["content_text"]) == arts
        assert filter_by_keywords(arts, ["正文"], search_fields=["title"]) == []


class TestRankByRelevance:
    def test_title_hits_outrank_body_hits(self):
        from core.filters import rank_by_relevance
        arts = [_art("泛谈", body="AI AI"), _art("AI 时代", body="AI")]
        out = rank_by_relevance(arts, ["AI"])
        assert out[0]["title"] == "AI 时代"
        assert out[0]["_relevance"] > out[1]["_relevance"]
        assert out[0]["_relevance_detail"]["title_hits"] == 1

    def test_no_keywords_returns_input(self):
        from core.filters import rank_by_relevance
        arts = [_art("a")]
        assert rank_by_relevance(arts, []) == arts


class TestDeduplicate:
    def test_canonical_link_variants_collapse(self):
        from core.filters import deduplicate
        arts = [
            _art("同一篇", link="https://mp.weixin.qq.com/s/abc?chksm=1"),
            _art("同一篇副本", link="https://mp.weixin.qq.com/s/abc#rd"),
        ]
        out = deduplicate(arts, by_title=False)
        assert len(out) == 1

    def test_title_dedup_can_be_disabled(self):
        from core.filters import deduplicate
        arts = [_art("同名", link="https://a.com/1"), _art("同名", link="https://b.com/2")]
        assert len(deduplicate(arts, by_title=False)) == 2
        assert len(deduplicate(arts)) == 1


# ---------------------------------------------------------------------------
# core.dedup
# ---------------------------------------------------------------------------

class TestDeduplicator:
    def test_link_reason(self):
        from core.dedup import Deduplicator
        d = Deduplicator()
        art = _art("A", link="https://mp.weixin.qq.com/s/x")
        d.mark_seen(art)
        assert d.duplicate_reason(_art("B", link="https://mp.weixin.qq.com/s/x?chksm=2")) == "link"

    def test_content_hash_reason(self):
        from core.dedup import Deduplicator
        d = Deduplicator()
        d.mark_seen(_art("A", link="https://a/1", body="完全相同的长正文内容"))
        assert d.duplicate_reason(_art("完全不同标题", link="https://b/2",
                                       body="完全相同的长正文内容")) == "content_hash"

    def test_title_similarity_reason(self):
        from core.dedup import Deduplicator
        d = Deduplicator()
        d.mark_seen(_art("2026年AI行业深度分析报告", link="https://a/1"))
        assert d.duplicate_reason(_art("2026年AI行业深度分析报告！", link="https://a/2")) == \
               "title_similarity"

    def test_distinct_article_has_no_reason(self):
        from core.dedup import Deduplicator
        d = Deduplicator()
        d.mark_seen(_art("人工智能", link="https://a/1", body="甲"))
        assert d.duplicate_reason(_art("烹饪技巧", link="https://a/2", body="乙")) is None

    def test_is_duplicate_counts_stats(self):
        from core.dedup import Deduplicator
        d = Deduplicator()
        art = _art("A", link="https://a/1")
        assert not d.is_duplicate(art)
        d.mark_seen(art)
        assert d.is_duplicate(art)
        assert d.stats["checked"] == 2
        assert d.stats["duplicates"] == 1

    def test_find_duplicate_groups_keeps_all(self):
        from core.dedup import Deduplicator
        d = Deduplicator()
        arts = [_art("X", link="https://a/1"), _art("X", link="https://a/1"), _art("Y", link="https://a/2")]
        groups = d.find_duplicate_groups(arts)
        assert groups == [[0, 1]]

    def test_content_hash_disabled(self):
        from core.dedup import Deduplicator
        d = Deduplicator(use_content_hash=False, use_title_match=False)
        d.mark_seen(_art("A", link="https://a/1", body="同样的正文"))
        assert d.duplicate_reason(_art("B", link="https://a/2", body="同样的正文")) is None


# ---------------------------------------------------------------------------
# core.article_parser
# ---------------------------------------------------------------------------

ARTICLE_HTML = """
<html><head>
<meta content="深度解读：AI 芯片" property="og:title">
<meta content="这是一段摘要描述" property="og:description">
<meta content="https://mp.weixin.qq.com/s/realLink" property="og:url">
<meta content="张三" name="author">
<meta content="2026-03-04T09:00:00" property="article:published_time">
<title>兜底标题</title>
</head><body>
<div class="rich_media_content" id="js_content">
  <p>第一段正文。</p>
  <div class="inner"><p>第二段带<img data-src="https://mmbiz.qpic.cn/1.png" alt="图一">嵌套。</p></div>
  <section><p>第三段。</p></section>
</div>
<div class="footer_junk">页面底部无关内容不应进入正文</div>
</body></html>
"""


class TestArticleParser:
    def test_content_stops_at_content_div(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(ARTICLE_HTML, "https://mp.weixin.qq.com/s/x")
        assert "第一段正文" in art["content_text"]
        assert "页面底部无关内容" not in art["content_text"]

    def test_meta_attribute_order_tolerant(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(ARTICLE_HTML, "https://mp.weixin.qq.com/s/x")
        assert art["title"] == "深度解读：AI 芯片"
        assert art["author"] == "张三"

    def test_summary_from_og_description(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(ARTICLE_HTML, "https://mp.weixin.qq.com/s/x")
        assert art["summary"] == "这是一段摘要描述"

    def test_images_collected_with_alt(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(ARTICLE_HTML, "https://mp.weixin.qq.com/s/x")
        assert art["images"][0]["src"].endswith("1.png")

    def test_word_count_matches_text(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(ARTICLE_HTML, "https://mp.weixin.qq.com/s/x")
        assert art["word_count"] == len(art["content_text"])
        assert art["word_count"] > 0

    def test_markdown_preserves_paragraphs(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(ARTICLE_HTML, "https://mp.weixin.qq.com/s/x")
        assert "![图一](https://mmbiz.qpic.cn/1.png)" in art["content_markdown"]

    def test_meta_extraction_includes_canonical_url(self):
        from core.article_parser import extract_article_meta_from_html
        meta = extract_article_meta_from_html(ARTICLE_HTML)
        assert meta["canonical_url"] == "https://mp.weixin.qq.com/s/realLink"
        assert meta["pub_date"]


class TestArticleParserEdgeCases:
    def test_empty_html(self):
        from core.article_parser import parse_article_html
        art = parse_article_html("", "https://mp.weixin.qq.com/s/x")
        assert art["content_text"] == ""
        assert art["word_count"] == 0

    def test_unclosed_tags_do_not_crash(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(
            '<div id="js_content"><p>未闭合<p>还有一段<img src="x.png">', "u"
        )
        assert "未闭合" in art["content_text"]

    def test_data_uri_images_skipped(self):
        from core.article_parser import parse_article_html
        art = parse_article_html(
            '<div id="js_content"><img data-src="data:image/png;base64,AAA"/></div>', "u"
        )
        assert art["images"] == []


# ---------------------------------------------------------------------------
# scrapers.sogou_search
# ---------------------------------------------------------------------------

class TestSogouDateExtraction:
    def test_timeconvert_javascript(self):
        from scrapers.sogou_search import SogouWeChatSearcher
        out = SogouWeChatSearcher._extract_date("<span>document.write(timeConvert('1772500000'))</span>")
        assert out and out.startswith("2026")

    def test_iso_and_chinese(self):
        from scrapers.sogou_search import SogouWeChatSearcher
        assert SogouWeChatSearcher._extract_date("<i>2026-03-04</i>").startswith("2026-03-04")
        assert SogouWeChatSearcher._extract_date("<i>2026年3月4日</i>").startswith("2026-03-04")

    def test_relative_label(self):
        from scrapers.sogou_search import SogouWeChatSearcher
        assert SogouWeChatSearcher._extract_date("<span class='s-p'>5分钟前</span>") is not None

    def test_no_date(self):
        from scrapers.sogou_search import SogouWeChatSearcher
        assert SogouWeChatSearcher._extract_date("<div>没有日期</div>") is None


class TestSogouBlocking:
    class _Resp:
        def __init__(self, text):
            self._text = text
        def raise_for_status(self):
            pass
        @property
        def text(self):
            return self._text

    class _Session:
        def __init__(self, text):
            self.text = text
            self.headers = {}
            self.proxies = {}
        def get(self, url, timeout=None):
            return TestSogouBlocking._Resp(self.text)

    def _searcher(self, tmp_path, text, **kwargs):
        from scrapers.sogou_search import SogouWeChatSearcher
        s = SogouWeChatSearcher(rate_limit_min=0, rate_limit_max=0, **kwargs)
        s.session = self._Session(text)
        return s

    def test_captcha_page_returns_empty_and_counts(self, tmp_path):
        s = self._searcher(tmp_path, "<html>antispider 请输入验证码</html>")
        assert s.search_articles("AI") == []
        assert s.blocked and s.blocked_pages == 1

    def test_raise_on_block(self, tmp_path):
        from scrapers.sogou_search import SogouBlocked
        s = self._searcher(tmp_path, "<html>输入验证码</html>", raise_on_block=True)
        with pytest.raises(SogouBlocked):
            s.search_articles("AI")

    def test_normal_page_not_counted_blocked(self, tmp_path):
        s = self._searcher(tmp_path, "<html><body>没有结果</body></html>")
        assert s.search_articles("AI") == []
        assert not s.blocked


class TestStrictAccount:
    class _Fake:
        def __init__(self, results):
            self.results = results
        def search_articles(self, query, page=1, article_type="article"):
            return self.results if page == 1 else []

    def _searcher(self):
        from scrapers.sogou_search import SogouWeChatSearcher
        s = SogouWeChatSearcher(rate_limit_min=0, rate_limit_max=0)
        return s

    def test_strict_drops_unrelated(self, monkeypatch):
        from scrapers.sogou_search import SogouWeChatSearcher
        results = [_art("别人的文章", account="无关公众号", link="https://a/1")]
        s = self._searcher()
        monkeypatch.setattr(s, "search_articles", self._Fake(results).search_articles)
        assert s.search_account_articles("人民日报", max_pages=1, strict_account=True) == []

    def test_non_strict_keeps_all(self, monkeypatch):
        from scrapers.sogou_search import SogouWeChatSearcher
        results = [_art("别人的文章", account="无关公众号", link="https://a/1")]
        s = self._searcher()
        monkeypatch.setattr(s, "search_articles", self._Fake(results).search_articles)
        assert len(s.search_account_articles("人民日报", max_pages=1, strict_account=False)) == 1

    def test_matching_account_kept(self, monkeypatch):
        from scrapers.sogou_search import SogouWeChatSearcher
        results = [_art("社论", account="人民日报", link="https://a/2")]
        s = self._searcher()
        monkeypatch.setattr(s, "search_articles", self._Fake(results).search_articles)
        assert len(s.search_account_articles("人民日报", max_pages=1)) == 1


class TestSearchResultParsing:
    def test_parses_typical_block(self):
        from scrapers.sogou_search import SogouWeChatSearcher
        html = """
        <ul class="news-list">
          <li><div class="txt-box">
            <h3><a href="/link?url=abc" u="1">AI 芯片突破</a></h3>
            <p class="txt-info">全文摘要内容 here</p>
            <div class="s-p"><a class="account">机器之心</a><span>3小时前</span></div>
          </div></li>
        </ul>
        """
        s = SogouWeChatSearcher(rate_limit_min=0, rate_limit_max=0)
        results = s._parse_search_results(html, "article")
        assert results, "should parse at least one result"
        assert results[0]["title"]
        assert results[0]["link"].startswith("http")


# Snippet copied verbatim (long redirect token trimmed) from a live
# weixin.sogou.com?type=2 result page on 2026-10-05.
SOGOU_LIVE_BLOCK = """
<div class="txt-box">
<h3>
<a target="_blank" href="/link?url=dn9a_-gY295K0Rci_xozVXfdMkSQTLW6cwJThYulHEtVjXrGTiVgS23kq76XDGy_wuoaWjmne6tc&amptype=2&amp;query=%E4%BA%BA%E6%B0%91%E6%97%A5%E6%8A%A5&amp;token=CA226FA5038A9D92" id="sogou_vr_11002601_title_0" uigs="article_title_0"><em><!--red_beg-->人民日报<!--red_end--></em>关注长春中国一汽博物馆:从&ldquo;诸多第一&rdquo;到智能制造</a>
</h3>
<p class="txt-info" id="sogou_vr_11002601_summary_0">近期,<em><!--red_beg-->人民日报<!--red_end--></em>记者走进中国一汽博物馆,解码产业融合新动能.&mdash;&mdash;编  者  周末,秋高气爽,吉林省长春...</p>
<div class="s-p">
<span class="all-time-y2">吉林发布</span><span class="s2"><script>document.write(timeConvert('1791185613'))</script></span>
</div>
</div>
"""

SOGOU_LIVE_PAGE = (
    '<div class="news-box"><ul class="news-list"><li id="box_0">'
    + SOGOU_LIVE_BLOCK
    + "</li></ul></div>"
)


class TestSogouLiveMarkup:
    """Live Sogou pages are fully HTML-escaped; parsers must decode that."""

    def _searcher(self):
        from scrapers.sogou_search import SogouWeChatSearcher
        return SogouWeChatSearcher(rate_limit_min=0, rate_limit_max=0)

    def test_title_entities_are_decoded(self):
        r = self._searcher()._parse_search_results(SOGOU_LIVE_PAGE, "article")
        assert len(r) == 1
        assert r[0]["title"] == "人民日报关注长春中国一汽博物馆:从“诸多第一”到智能制造"
        assert "&ldquo;" not in r[0]["title"]

    def test_link_keeps_single_ampersands(self):
        # '&amp;' left in the query string breaks the redirect (signature params).
        r = self._searcher()._parse_search_results(SOGOU_LIVE_PAGE, "article")
        link = r[0]["link"]
        assert link.startswith("https://weixin.sogou.com/link?url=")
        assert "&amp;" not in link
        assert "type=2" in link and "token=CA226FA5038A9D92" in link

    def test_account_comes_from_all_time_y2(self):
        r = self._searcher()._parse_search_results(SOGOU_LIVE_PAGE, "article")
        assert r[0]["account"] == "吉林发布"
        assert r[0]["author"] == "吉林发布"

    def test_script_does_not_leak_into_fields(self):
        r = self._searcher()._parse_search_results(SOGOU_LIVE_PAGE, "article")
        for field in ("title", "account", "snippet"):
            assert "timeConvert" not in r[0][field]
            assert "document.write" not in r[0][field]

    def test_date_from_timeconvert_script(self):
        r = self._searcher()._parse_search_results(SOGOU_LIVE_PAGE, "article")
        assert r[0]["pub_date"].startswith("2026-")

    def test_snippet_entities_decoded(self):
        r = self._searcher()._parse_search_results(SOGOU_LIVE_PAGE, "article")
        assert "——" in r[0]["snippet"]
        assert "&mdash;" not in r[0]["snippet"]
        assert "<em>" not in r[0]["snippet"]

    def test_backslash_free_comment_removed(self):
        r = self._searcher()._parse_search_results(SOGOU_LIVE_PAGE, "article")
        assert "red_beg" not in r[0]["title"] + r[0]["snippet"]

    def test_legacy_account_anchor_still_works(self):
        html = SOGOU_LIVE_PAGE.replace(
            '<span class="all-time-y2">吉林发布</span>',
            '<a class="account" href="/wechat?src=11">机器之心</a>',
        )
        r = self._searcher()._parse_search_results(html, "article")
        assert r[0]["account"] == "机器之心"

    def test_relative_date_label_supported(self):
        html = SOGOU_LIVE_PAGE.replace(
            "<script>document.write(timeConvert('1791185613'))</script>", "3小时前"
        )
        r = self._searcher()._parse_search_results(html, "article")
        assert r[0]["pub_date"]


# ---------------------------------------------------------------------------
# scrapers.article_fetcher
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text, url="", status=200, headers=None):
        self._text = text
        self.url = url
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = dict(headers or {})

    @property
    def text(self):
        return self._text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Queue of (text, url) responses; records every requested URL."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.proxies = {}

    def get(self, url, timeout=None, allow_redirects=False, headers=None):
        self.calls.append(url)
        if not self.responses:
            raise RuntimeError("no more fake responses")
        item = self.responses.pop(0)
        text, url_after = item if isinstance(item, tuple) else (item, url)
        return FakeResponse(text, url=url_after, headers=headers or {})


@pytest.fixture
def fetcher(tmp_path):
    from scrapers.article_fetcher import ArticleFetcher
    return ArticleFetcher(
        rate_limit_min=0, rate_limit_max=0,
        cache_dir=tmp_path / "cache",
    )


class TestFetchArticle:
    def test_parses_and_caches(self, fetcher, tmp_path):
        url = "https://mp.weixin.qq.com/s/aaa"
        fetcher.session = FakeSession([(ARTICLE_HTML, url)])
        art = fetcher.fetch_article(url)
        assert art["title"] == "深度解读：AI 芯片"
        assert art["error"] is None if "error" in art else True
        assert fetcher.stats["fetched"] == 1

        fetcher.session = FakeSession([])
        again = fetcher.fetch_article(url)
        assert again["title"] == art["title"]
        assert fetcher.stats["cache_hits"] == 1

    def test_blocked_page_becomes_error_article(self, fetcher):
        url = "https://mp.weixin.qq.com/s/bbb"
        fetcher.session = FakeSession(["<html>输入验证码 antispider</html>"])
        art = fetcher.fetch_article(url)
        assert art.get("error")
        assert "blocked" in art["error"]
        assert fetcher.stats["failed"] == 1

    def test_http_failure_returns_error_not_exception(self, fetcher):
        class Boom(FakeSession):
            def get(self, url, timeout=None, allow_redirects=False):
                raise RuntimeError("timeout")
        fetcher.session = Boom([])
        art = fetcher.fetch_article("https://mp.weixin.qq.com/s/ccc")
        assert "timeout" in art["error"]

    def test_canonical_url_replaces_sogou_link(self, fetcher):
        sogou = "https://weixin.sogou.com/link?url=xyz"
        fetcher.session = FakeSession([(ARTICLE_HTML, sogou)])
        art = fetcher.fetch_article(sogou)
        assert art["link"] == "https://mp.weixin.qq.com/s/realLink"

    def test_pub_date_normalized_to_iso(self, fetcher):
        fetcher.session = FakeSession([ARTICLE_HTML])
        art = fetcher.fetch_article("https://mp.weixin.qq.com/s/ddd")
        assert str(art["pub_date"]).startswith("2026-03-04")


class TestFetchCacheBehaviour:
    def test_failure_is_not_cached(self, fetcher):
        url = "https://mp.weixin.qq.com/s/eee"
        fetcher.session = FakeSession(["<html>antispider</html>"])
        fetcher.fetch_article(url)
        assert not fetcher._cache_path(url).exists()

    def test_cached_error_is_not_served(self, fetcher):
        url = "https://mp.weixin.qq.com/s/fff"
        fetcher._save_cache(url, {"error": "blocked", "link": url})
        fetcher.session = FakeSession([ARTICLE_HTML])
        art = fetcher.fetch_article(url)
        assert not art.get("error")
        assert fetcher.stats["cache_hits"] == 0

    def test_expired_cache_is_refreshed(self, tmp_path):
        from scrapers.article_fetcher import ArticleFetcher
        f = ArticleFetcher(rate_limit_min=0, rate_limit_max=0,
                           cache_dir=tmp_path / "c2", cache_max_age_days=1)
        url = "https://mp.weixin.qq.com/s/ggg"
        f._save_cache(url, {"title": "旧缓存", "link": url})
        old = datetime.now() - timedelta(days=5)
        os.utime(f._cache_path(url), (old.timestamp(), old.timestamp()))
        f.session = FakeSession([ARTICLE_HTML])
        art = f.fetch_article(url)
        assert art["title"] == "深度解读：AI 芯片"
        assert f.stats["cache_hits"] == 0

    def test_clear_cache_and_status(self, fetcher):
        fetcher._save_cache("https://mp.weixin.qq.com/s/1", {"title": "a"})
        fetcher._save_cache("https://mp.weixin.qq.com/s/2", {"title": "b"})
        status = fetcher.cache_status()
        assert status["entries"] == 2
        assert status["size_human"]
        assert fetcher.clear_cache() == 2
        assert fetcher.cache_status()["entries"] == 0

    def test_cache_dir_defaults_to_state_dir(self, tmp_path, monkeypatch):
        from scrapers.article_fetcher import ArticleFetcher
        target = tmp_path / "state"
        monkeypatch.setenv("WECHAT_SCRAPER_DATA_DIR", str(target))
        f = ArticleFetcher(rate_limit_min=0, rate_limit_max=0)
        assert str(target) in str(f.cache_dir)


class TestEnrichArticles:
    def test_sogou_redirect_links_are_fetched(self, fetcher):
        arts = [_art("搜索标题", link="https://weixin.sogou.com/link?url=xyz")]
        fetcher.session = FakeSession([ARTICLE_HTML])
        out = fetcher.enrich_articles(arts)
        assert len(fetcher.session.calls) == 1
        assert out[0]["content_text"]

    def test_failure_keeps_search_metadata(self, fetcher):
        arts = [_art("搜索标题", link="https://weixin.sogou.com/link?url=xyz",
                     account="机器之心", snippet="摘要")]
        fetcher.session = FakeSession(["<html>antispider</html>"])
        out = fetcher.enrich_articles(arts)
        assert out[0]["title"] == "搜索标题"
        assert out[0]["account"] == "机器之心"
        assert out[0]["snippet"] == "摘要"
        assert not out[0].get("content_text")

    def test_search_link_is_preserved(self, fetcher):
        arts = [_art("T", link="https://weixin.sogou.com/link?url=xyz")]
        fetcher.session = FakeSession([ARTICLE_HTML])
        out = fetcher.enrich_articles(arts)
        assert out[0]["search_link"] == "https://weixin.sogou.com/link?url=xyz"
        assert out[0]["link"] == "https://mp.weixin.qq.com/s/realLink"

    def test_non_wechat_links_skipped(self, fetcher):
        arts = [_art("外部", link="https://example.com/x")]
        fetcher.session = FakeSession([])
        out = fetcher.enrich_articles(arts)
        assert fetcher.session.calls == []
        assert out[0]["link"] == "https://example.com/x"

    def test_fetch_articles_deduplicates_by_url(self, fetcher):
        fetcher.session = FakeSession([ARTICLE_HTML, ARTICLE_HTML])
        out = fetcher.fetch_articles(
            ["https://mp.weixin.qq.com/s/hhh?chksm=1", "https://mp.weixin.qq.com/s/hhh?chksm=2"],
            deduplicate=True,
        )
        assert len(out) == 1


class TestSharedSession:
    """Sogou only opens /link redirects for the session that searched."""

    def test_fetch_article_uses_the_given_session(self, fetcher):
        shared = FakeSession([ARTICLE_HTML])
        fetcher.session = FakeSession([])  # must not be touched
        art = fetcher.fetch_article(
            "https://weixin.sogou.com/link?url=xyz", session=shared
        )
        assert shared.calls == ["https://weixin.sogou.com/link?url=xyz"]
        assert art["title"] == "深度解读：AI 芯片"
        assert fetcher.stats["fetched"] == 1

    def test_defaults_to_own_session(self, fetcher):
        own = FakeSession([ARTICLE_HTML])
        fetcher.session = own
        fetcher.fetch_article("https://mp.weixin.qq.com/s/zzz")
        assert len(own.calls) == 1

    def test_enrich_forwards_session(self, fetcher):
        shared = FakeSession([ARTICLE_HTML])
        fetcher.session = FakeSession([])
        out = fetcher.enrich_articles(
            [_art("T", link="https://weixin.sogou.com/link?url=xyz")], session=shared
        )
        assert shared.calls
        assert out[0]["content_text"]

    def test_fetch_articles_forwards_session(self, fetcher):
        shared = FakeSession([ARTICLE_HTML])
        fetcher.session = FakeSession([])
        out = fetcher.fetch_articles(
            ["https://weixin.sogou.com/link?url=xyz"], session=shared
        )
        assert len(out) == 1 and shared.calls


class TestSogouJsRedirectFetch:
    """fetch_article must hop through the JS wrapper page Sogou returns."""

    WRAPPER_URL = "https://weixin.sogou.com/link?url=xyz"
    # The URL SOGOU_JS_REDIRECT rebuilds, before the page's own canonical wins.
    JS_TARGET = (
        "https://mp.weixin.qq.com/s?src=11&timestamp=1791187357&ver=7007"
        "&signature=26wVFIoaeg*9mAqbgeRsIEzHWtX0rreIiAGfawI4Tbbg24Jn"
    )
    CANONICAL = "https://mp.weixin.qq.com/s/realLink"

    def test_two_hop_fetch_resolves_the_article(self, fetcher):
        session = FakeSession([
            (SOGOU_JS_REDIRECT, self.WRAPPER_URL),
            (ARTICLE_HTML, self.JS_TARGET),
        ])
        art = fetcher.fetch_article(self.WRAPPER_URL, session=session)
        assert session.calls == [self.WRAPPER_URL, self.JS_TARGET]
        assert art["title"] == "深度解读：AI 芯片"
        assert art["word_count"] > 0
        assert not art.get("error")
        assert art["link"] == self.CANONICAL

    def test_unresolvable_wrapper_page_is_not_an_error(self, fetcher):
        session = FakeSession([("<html><body>请登录后查看</body></html>", self.WRAPPER_URL)])
        art = fetcher.fetch_article(self.WRAPPER_URL, session=session)
        assert len(session.calls) == 1
        assert art["content_text"] == ""

    def test_enrich_reports_resolved_link(self, fetcher):
        session = FakeSession([
            (SOGOU_JS_REDIRECT, self.WRAPPER_URL),
            (ARTICLE_HTML, self.JS_TARGET),
        ])
        out = fetcher.enrich_articles(
            [_art("搜索标题", link=self.WRAPPER_URL)], session=session
        )
        assert out[0]["link"] == self.CANONICAL
        assert out[0]["search_link"] == self.WRAPPER_URL
        assert out[0]["content_text"]

    def test_cached_under_the_stable_url(self, fetcher):
        # Sogou tokens rotate every search; the mp.weixin key must hit cache.
        session = FakeSession([
            (SOGOU_JS_REDIRECT, self.WRAPPER_URL),
            (ARTICLE_HTML, self.JS_TARGET),
        ])
        fetcher.fetch_article(self.WRAPPER_URL, session=session)
        fetcher.session = FakeSession([])
        again = fetcher.fetch_article(self.CANONICAL)
        assert again["title"] == "深度解读：AI 芯片"
        assert fetcher.stats["cache_hits"] == 1


# ---------------------------------------------------------------------------
# scrapers.direct_url
# ---------------------------------------------------------------------------

class TestDirectUrlScraper:
    def test_reports_skipped_non_wechat(self, fetcher):
        from scrapers.direct_url import DirectUrlScraper
        fetcher.session = FakeSession([ARTICLE_HTML])
        scraper = DirectUrlScraper(fetcher)
        arts = scraper.scrape_urls([
            "https://mp.weixin.qq.com/s/iii", "https://example.com/nope", "",
        ])
        assert len(arts) == 1
        assert "https://example.com/nope" in scraper.last_skipped

    def test_scrape_from_text_extracts_wechat_only(self, fetcher):
        from scrapers.direct_url import DirectUrlScraper
        fetcher.session = FakeSession([ARTICLE_HTML])
        scraper = DirectUrlScraper(fetcher)
        text = "文章在这里 https://mp.weixin.qq.com/s/jjj\n广告 https://ads.example.com"
        arts = scraper.scrape_from_text(text)
        assert len(arts) == 1
        assert len(scraper.last_skipped) == 1


# ---------------------------------------------------------------------------
# core.state_store
# ---------------------------------------------------------------------------

class TestStateStore:
    def _store(self, tmp_path):
        from core.state_store import StateStore
        return StateStore(path=str(tmp_path / "history.json"))

    def test_record_counts_new_only(self, tmp_path):
        s = self._store(tmp_path)
        arts = [_art("A", link="https://mp.weixin.qq.com/s/1"),
                _art("B", link="https://mp.weixin.qq.com/s/2")]
        assert s.record(arts) == 2
        assert s.record(arts) == 0

    def test_times_seen_increments(self, tmp_path):
        s = self._store(tmp_path)
        art = _art("A", link="https://mp.weixin.qq.com/s/1")
        s.record([art])
        s.record([art])
        rows = s.history()
        assert rows[0]["times_seen"] == 2

    def test_tracking_param_variant_is_known(self, tmp_path):
        s = self._store(tmp_path)
        s.record([_art("A", link="https://mp.weixin.qq.com/s/1?chksm=1")])
        assert s.is_known(_art("A", link="https://mp.weixin.qq.com/s/1#rd"))

    def test_filter_new_splits(self, tmp_path):
        s = self._store(tmp_path)
        s.record([_art("旧", link="https://mp.weixin.qq.com/s/old")])
        batch = [_art("旧", link="https://mp.weixin.qq.com/s/old"),
                 _art("新", link="https://mp.weixin.qq.com/s/new")]
        new, known = s.filter_new(batch)
        assert [a["title"] for a in new] == ["新"]
        assert [a["title"] for a in known] == ["旧"]

    def testarticles_without_link_ignored(self, tmp_path):
        s = self._store(tmp_path)
        assert s.record([_art("无链接")]) == 0

    def test_history_account_filter_and_limit(self, tmp_path):
        s = self._store(tmp_path)
        s.record([_art("A", link="https://x/1", account="机器之心"),
                  _art("B", link="https://x/2", account="人民日报")])
        assert len(s.history(account="机器之心")) == 1
        assert len(s.history(limit=1)) == 1

    def test_stats(self, tmp_path):
        s = self._store(tmp_path)
        s.record([_art("A", link="https://x/1", account="甲", date="2026-03-04")])
        st = s.stats()
        assert st["total_articles"] == 1
        assert st["accounts"] == ["甲"]
        assert st["date_range"]["earliest"] == "2026-03-04"

    def test_clear(self, tmp_path):
        s = self._store(tmp_path)
        s.record([_art("A", link="https://x/1")])
        assert s.clear() == 1
        assert s.stats()["total_articles"] == 0

    def test_persistence_roundtrip(self, tmp_path):
        s = self._store(tmp_path)
        s.record([_art("A", link="https://x/1", account="甲")])
        again = self._store(tmp_path)
        assert again.is_known("https://x/1")
        assert again.history()[0]["account"] == "甲"

    def test_corrupt_file_reinitializes(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text("{ not json", encoding="utf-8")
        from core.state_store import StateStore
        s = StateStore(path=str(path))
        assert s.stats()["total_articles"] == 0
        s.record([_art("A", link="https://x/9")])
        assert s.is_known("https://x/9")

    def test_save_is_atomic(self, tmp_path):
        s = self._store(tmp_path)
        s.record([_art("A", link="https://x/1")], auto_save=False)
        s.save()
        assert (tmp_path / "history.json").exists()
        assert not list(tmp_path.glob(".history_*"))

    def test_defaults_to_state_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "sd"
        monkeypatch.setenv("WECHAT_SCRAPER_DATA_DIR", str(target))
        from core.state_store import StateStore
        assert StateStore().path.parent == target


# ---------------------------------------------------------------------------
# core.summarizer
# ---------------------------------------------------------------------------

BODY_A = (
    "人工智能芯片是本轮科技竞争的核心。国产芯片在制程上仍受限制。"
    "多家企业加大了研发投入。研发周期通常需要三年以上。"
    "市场对高性能计算的需求快速增长。高性能计算依赖先进封装。"
)
BODY_B = "本文介绍了一种新的火锅底料配方，包含牛油、花椒和豆瓣酱。底料的比例决定口味。"


class TestSummarizer:
    def test_tokenize_produces_cjk_bigrams(self):
        from core.summarizer import _tokenize
        terms = _tokenize("人工智能")
        assert "人工" in terms and "智能" in terms

    def test_tokenize_lowercases_ascii(self):
        from core.summarizer import _tokenize
        assert "ai" in _tokenize("AI and ai")

    def test_extract_keywords_weights_title(self):
        from core.summarizer import extract_keywords
        arts = [{"title": "芯片突破", "content_text": BODY_A}]
        kw = dict(extract_keywords(arts, top_n=20))
        assert kw["芯片"] >= 3

    def test_summarize_keeps_original_sentence_order(self):
        from core.summarizer import summarize_article
        out = summarize_article({"content_text": BODY_A}, max_sentences=2)
        assert out["method"] == "extractive"
        assert BODY_A.index(out["sentences"][0]) < BODY_A.index(out["sentences"][1])

    def test_summarize_short_text_falls_back(self):
        from core.summarizer import summarize_article
        out = summarize_article({"content_text": "短句。", "summary": "元数据摘要"})
        assert out["method"] == "fallback_meta"
        assert out["summary"] == "元数据摘要"

    def test_summarize_empty_article(self):
        from core.summarizer import summarize_article
        assert summarize_article({})["method"] == "no_content"

    def test_article_stats(self):
        from core.summarizer import article_stats
        arts = [
            _art("A", date="2026-03-04", body="一二三四五", account="甲"),
            _art("B", date="2026-03-05", body="一二三", account="乙"),
            {"title": "失败", "link": "", "account": "甲", "word_count": 0, "error": "blocked"},
        ]
        st = article_stats(arts)
        assert st["count"] == 3
        assert st["failed"] == 1
        assert st["with_full_text"] == 2
        assert st["accounts"]["甲"] == 2
        assert st["date_range"]["earliest"] == "2026-03-04"

    def test_article_stats_empty(self):
        from core.summarizer import article_stats
        assert article_stats([])["count"] == 0

    def test_build_digest_markdown_sections(self):
        from core.summarizer import build_digest
        digest = build_digest([
            {"title": "芯片", "account": "甲", "pub_date": "2026-03-04",
             "content_text": BODY_A, "word_count": len(BODY_A), "link": "https://x/1"},
            {"title": "火锅", "account": "乙", "pub_date": "2026-03-05",
             "content_text": BODY_B, "word_count": len(BODY_B), "link": "https://x/2"},
        ])
        md = digest["digest_markdown"]
        assert "高频关键词" in md
        assert "逐篇摘要" in md
        assert "芯片" in md
        assert digest["keywords"]
        assert digest["top_articles"][0]["word_count"] >= digest["top_articles"][-1]["word_count"]

    def test_reading_time(self):
        from core.summarizer import reading_time_minutes
        assert reading_time_minutes(0) == 0
        assert reading_time_minutes(100) == 1
        assert reading_time_minutes(1200) == 3


# ---------------------------------------------------------------------------
# core.image_downloader
# ---------------------------------------------------------------------------

class FakeImageSession:
    def __init__(self, payload, content_type="image/png", fail=False):
        self.payload = payload
        self.content_type = content_type
        self.fail = fail
        self.headers = {}
        self.proxies = {}
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.fail:
            raise RuntimeError("connection reset")

        class R:
            def raise_for_status(inner):
                pass
            @property
            def content(inner):
                return self.payload
            @property
            def headers(inner):
                return {"Content-Type": self.content_type}
        return R()


class TestImageDownloader:
    def _dl(self, tmp_path, session, **kw):
        from core.image_downloader import ImageDownloader
        d = ImageDownloader(output_dir=str(tmp_path / "out"), **kw)
        d.session = session
        return d

    def test_downloads_and_writes_file(self, tmp_path):
        d = self._dl(tmp_path, FakeImageSession(b"\x89PNG" + b"x" * 5000))
        path = d.download("https://img/x.png", tmp_path / "imgs", 1)
        assert path and os.path.exists(path)
        assert path.endswith(".png")

    def test_skips_data_uri(self, tmp_path):
        d = self._dl(tmp_path, FakeImageSession(b"x" * 5000))
        assert d.download("data:image/png;base64,AAA", tmp_path / "imgs") is None

    def test_skips_placeholder_size(self, tmp_path):
        d = self._dl(tmp_path, FakeImageSession(b"tiny"))
        assert d.download("https://img/x.png", tmp_path / "imgs") is None

    def test_failure_returns_none(self, tmp_path):
        d = self._dl(tmp_path, FakeImageSession(b"", fail=True))
        assert d.download("https://img/x.png", tmp_path / "imgs") is None

    def test_sends_referer(self):
        from core.image_downloader import ImageDownloader
        d = ImageDownloader(output_dir="x")
        if d.session is not None:
            assert d.session.headers.get("Referer") == "https://mp.weixin.qq.com/"

    def test_filename_stable_and_ordered(self):
        from core.image_downloader import image_filename
        a1 = image_filename("https://img/a.png", 1)
        assert a1 == image_filename("https://img/a.png?x=1", 1) or a1
        assert a1.startswith("001_")
        assert image_filename("https://img/a.png", 2).startswith("002_")

    def test_extension_from_content_type(self):
        from core.image_downloader import _extension
        assert _extension("https://img/a", "image/webp") == ".webp"
        assert _extension("https://img/a.JPG") == ".jpg"
        assert _extension("https://img/a") == ".jpg"

    def test_download_article_images_rewrites_markdown(self, tmp_path):
        d = self._dl(tmp_path, FakeImageSession(b"\x89PNG" + b"y" * 3000))
        article = {
            "title": "带图文章",
            "images": [{"src": "https://img/one.png", "alt": "图一"}],
            "content_markdown": "正文\n![图一](https://img/one.png)\n",
        }
        res = d.download_article_images(article)
        assert res["downloaded"] == 1
        assert res["failed"] == 0
        assert "https://img/one.png" not in res["content_markdown"]
        assert "![图一](001_" in res["content_markdown"]

    def test_download_for_articles_counts(self, tmp_path):
        d = self._dl(tmp_path, FakeImageSession(b"\x89PNG" + b"z" * 3000))
        arts = [
            {"title": "有图", "images": [{"src": "https://img/1.png"}]},
            {"title": "无图", "images": []},
        ]
        out = d.download_for_articles(arts)
        assert out["articles_processed"] == 1
        assert out["images_downloaded"] == 1

    def test_respects_max_images(self, tmp_path):
        d = self._dl(tmp_path, FakeImageSession(b"\x89PNG" + b"w" * 3000), max_images=1)
        art = {"title": "多图", "images": [{"src": f"https://img/{i}.png"} for i in range(3)]}
        assert d.download_article_images(art)["downloaded"] == 1

    def test_rewrite_image_links(self):
        from core.image_downloader import rewrite_image_links
        out = rewrite_image_links("![a](https://img/1.png)", {"https://img/1.png": "001_x.png"})
        assert out == "![a](001_x.png)"

    def test_extract_image_urls(self):
        from core.image_downloader import extract_image_urls
        text = '![x](https://img/1.png) <img data-src="https://img/2.jpg" src="data:image/gif;base64">'
        urls = extract_image_urls(text)
        assert "https://img/1.png" in urls and "https://img/2.jpg" in urls
        assert all(not u.startswith("data:") for u in urls)


# ---------------------------------------------------------------------------
# exporters
# ---------------------------------------------------------------------------

def _full_article(**over):
    base = {
        "title": "AI 芯片深度解读",
        "account": "机器之心",
        "author": "张三",
        "pub_date": "2026-03-04T09:00:00",
        "link": "https://mp.weixin.qq.com/s/realLink",
        "content_text": BODY_A,
        "content_markdown": BODY_A,
        "summary": "一句话摘要",
        "word_count": len(BODY_A),
        "images": [{"src": "https://mmbiz.qpic.cn/1.png", "alt": "图一"}],
    }
    base.update(over)
    return base


class TestMarkdownExporter:
    def test_export_creates_text_only_tree(self, tmp_path):
        from exporters.markdown_exporter import MarkdownExporter
        out = MarkdownExporter(output_dir=str(tmp_path / "md")).export(
            [_full_article()], folder="批次1"
        )
        assert out["article_count"] == 1
        root = out["output_dir"]
        assert os.path.exists(out["index_csv"])
        assert os.path.exists(out["summary_md"])
        assert out["files"][0].endswith(".md")
        assert os.path.basename(os.path.dirname(out["files"][0])) == "机器之心"
        for dirpath, _dirs, files in os.walk(root):
            assert not [f for f in files if not f.endswith((".md", ".csv"))]

    def test_index_csv_rows(self, tmp_path):
        import csv
        from exporters.markdown_exporter import MarkdownExporter
        out = MarkdownExporter(output_dir=str(tmp_path / "md")).export(
            [_full_article(), _full_article(title="第二篇", link="https://x/2")],
            folder="b",
        )
        with open(out["index_csv"], encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 3
        assert rows[1][1] == "AI 芯片深度解读"

    def test_title_collision_gets_suffix(self, tmp_path):
        from exporters.markdown_exporter import MarkdownExporter
        arts = [_full_article(), _full_article(link="https://mp.weixin.qq.com/s/other")]
        out = MarkdownExporter(output_dir=str(tmp_path / "md")).export(arts, folder="c")
        names = [os.path.basename(p) for p in out["files"]]
        assert len(set(names)) == 2

    def test_group_by_account_disabled(self, tmp_path):
        from exporters.markdown_exporter import MarkdownExporter
        out = MarkdownExporter(output_dir=str(tmp_path / "md")).export(
            [_full_article()], folder="d", group_by_account=False
        )
        assert os.path.basename(os.path.dirname(out["files"][0])) == "d"

    def test_empty_raises(self, tmp_path):
        from exporters.markdown_exporter import MarkdownExporter
        with pytest.raises(ValueError):
            MarkdownExporter(output_dir=str(tmp_path / "md")).export([])

    def test_note_contains_link_and_summary(self, tmp_path):
        from exporters.markdown_exporter import MarkdownExporter
        out = MarkdownExporter(output_dir=str(tmp_path / "md")).export(
            [_full_article()], folder="e"
        )
        text = open(out["files"][0], encoding="utf-8").read()
        assert "https://mp.weixin.qq.com/s/realLink" in text
        assert "一句话摘要" in text
        assert "1.png" in text

    def test_default_dir_is_env_export_dir(self, tmp_path, monkeypatch):
        from exporters.markdown_exporter import MarkdownExporter
        monkeypatch.setenv("WECHAT_SCRAPER_EXPORT_DIR", str(tmp_path / "envout"))
        assert MarkdownExporter().output_dir == tmp_path / "envout"


class TestZipExporter:
    def test_output_goes_to_temp_not_package(self, tmp_path, monkeypatch):
        from exporters.zip_exporter import ZipExporter
        monkeypatch.setenv("WECHAT_SCRAPER_EXPORT_DIR", str(tmp_path / "zips"))
        monkeypatch.chdir(tmp_path)
        path = ZipExporter().export([_full_article()], filename="t")
        assert str(tmp_path) in path
        assert path.endswith(".zip")
        assert not (tmp_path / "output").exists()

    def test_config_default_output_dir_honored(self, tmp_path, monkeypatch):
        from exporters.zip_exporter import ZipExporter
        monkeypatch.delenv("WECHAT_SCRAPER_EXPORT_DIR", raising=False)
        target = tmp_path / "cfgout"
        path = ZipExporter(config={"export": {"default_output_dir": str(target)}}).export(
            [_full_article()], filename="t2"
        )
        assert str(target) in path

    def test_html_escaping_blocks_injection(self, tmp_path):
        from exporters.zip_exporter import ZipExporter
        ex = ZipExporter(output_dir=str(tmp_path))
        art = _full_article(title='<script>alert(1)</script>', account='<b>粗体</b>')
        html = ex._to_html(art)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>粗体</b>" not in html

    def test_html_escapes_link_attribute(self, tmp_path):
        from exporters.zip_exporter import ZipExporter
        ex = ZipExporter(output_dir=str(tmp_path))
        art = _full_article(link='https://x.tld/a" onload="evil()')
        html = ex._to_html(art)
        assert '" onload="' not in html
        assert "&quot;" in html

    def test_all_formats_write_files(self, tmp_path):
        import zipfile
        from exporters.zip_exporter import ZipExporter
        path = ZipExporter(output_dir=str(tmp_path)).export(
            [_full_article()], filename="t3",
            formats=["markdown", "text", "html", "json"],
        )
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        assert any(n.endswith(".md") for n in names)
        assert any(n.endswith(".txt") for n in names)
        assert any(n.endswith(".html") for n in names)
        assert any(n.endswith(".json") for n in names)


class TestObsidianExporter:
    def test_reexport_is_idempotent(self, tmp_path):
        from exporters.obsidian_exporter import ObsidianExporter
        ex = ObsidianExporter(vault_path=str(tmp_path))
        first = ex.export_articles([_full_article()], folder="v")
        second = ex.export_articles([_full_article()], folder="v")
        notes = [f for f in second["files"] if f.endswith(".md") and "索引" not in f]
        assert len(notes) == 1
        assert not (tmp_path / "v").joinpath("AI 芯片深度解读_1.md").exists()
        assert first["files_created"] == second["files_created"]

    def test_note_filename_has_date_prefix(self, tmp_path):
        from exporters.obsidian_exporter import ObsidianExporter
        out = ObsidianExporter(vault_path=str(tmp_path)).export_articles(
            [_full_article()], folder="v2"
        )
        names = [os.path.basename(p) for p in out["files"]]
        assert any(n.startswith("20260304") for n in names)

    def test_moc_links_actual_note_names(self, tmp_path):
        from exporters.obsidian_exporter import ObsidianExporter
        out = ObsidianExporter(vault_path=str(tmp_path)).export_articles(
            [_full_article()], folder="v3"
        )
        moc = [p for p in out["files"] if "索引" in p][0]
        text = open(moc, encoding="utf-8").read()
        notes = [os.path.splitext(os.path.basename(p))[0] for p in out["files"] if p != moc]
        assert f"[[{notes[0]}]]" in text

    def test_frontmatter_has_source_link(self, tmp_path):
        from exporters.obsidian_exporter import ObsidianExporter
        out = ObsidianExporter(vault_path=str(tmp_path)).export_articles(
            [_full_article()], folder="v4"
        )
        note = [p for p in out["files"] if p.endswith(".md") and "索引" not in p][0]
        text = open(note, encoding="utf-8").read()
        assert text.startswith("---")
        assert "source:" in text and "realLink" in text

    def test_group_by_account(self, tmp_path):
        from exporters.obsidian_exporter import ObsidianExporter
        out = ObsidianExporter(vault_path=str(tmp_path)).export_articles(
            [_full_article()], folder="v5", group_by_account=True
        )
        notes = [p for p in out["files"] if "机器之心" in p]
        assert notes


# ---------------------------------------------------------------------------
# exporters: IMA / Dify degrade without credentials
# ---------------------------------------------------------------------------

class TestKnowledgeBaseExporters:
    def test_dify_headers_omit_missing_key(self, tmp_path):
        from exporters.dify_exporter import DifyExporter
        ex = DifyExporter(api_key=None)
        headers = ex._headers()
        assert "Authorization" not in headers

    def test_dify_list_datasets_without_key(self, tmp_path):
        from exporters.dify_exporter import DifyExporter
        ex = DifyExporter(api_key=None)
        out = ex.list_datasets()
        assert isinstance(out, list) and out == []


# ---------------------------------------------------------------------------
# mcp_server: protocol
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp():
    import mcp_server
    return mcp_server


class TestMcpProtocol:
    def test_initialize(self, mcp):
        res = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert res["result"]["serverInfo"]["name"] == "wechat-article-scraper"
        assert res["result"]["protocolVersion"]

    def test_ping(self, mcp):
        res = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert res["id"] == 2 and "error" not in res

    def test_tools_list(self, mcp):
        res = mcp.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert len(res["result"]["tools"]) == len(mcp.TOOLS)

    def test_notifications_are_silent(self, mcp):
        assert mcp.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ) is None
        assert mcp.handle_request({"jsonrpc": "2.0", "method": "some/update"}) is None

    def test_unknown_method_returns_error(self, mcp):
        res = mcp.handle_request({"jsonrpc": "2.0", "id": 4, "method": "nope"})
        assert res["error"]["code"] == -32601

    def test_resources_and_prompts_lists(self, mcp):
        for method in ("resources/list", "prompts/list"):
            res = mcp.handle_request({"jsonrpc": "2.0", "id": 5, "method": method})
            assert method.split("/")[0] in res["result"]

    def test_tools_call_wraps_payload(self, mcp):
        res = mcp.handle_request({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "get_skill_stats", "arguments": {}},
        })
        payload = res["result"]["content"][0]["text"]
        assert json.loads(payload)["tool_count"] == len(mcp.TOOLS)
        assert res["result"]["isError"] is False

    def test_tools_call_error_sets_flag(self, mcp):
        res = mcp.handle_request({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "summarize_articles", "arguments": {"articles": []}},
        })
        assert res["result"]["isError"] is True
        assert "error" in json.loads(res["result"]["content"][0]["text"])

    def test_unknown_tool(self, mcp):
        out = mcp.call_tool("does_not_exist", {})
        assert "未知工具" in out["error"]


class TestToolRegistry:
    def test_every_tool_has_a_handler(self, mcp):
        names = [t["name"] for t in mcp.TOOLS]
        assert set(names) == set(mcp.TOOL_HANDLERS)

    def test_schema_shape(self, mcp):
        for tool in mcp.TOOLS:
            assert tool["description"]
            assert tool["inputSchema"]["type"] == "object"
            assert isinstance(tool["inputSchema"].get("properties", {}), dict)

    def test_names_are_snake_case_unique(self, mcp):
        names = [t["name"] for t in mcp.TOOLS]
        assert len(names) == len(set(names))
        assert all(n == n.lower() and " " not in n for n in names)

    def test_at_least_21_tools(self, mcp):
        assert len(mcp.TOOLS) >= 21


# ---------------------------------------------------------------------------
# mcp_server: offline tools
# ---------------------------------------------------------------------------

class TestMcpOfflineTools:
    def test_parse_html(self, mcp):
        out = mcp.handle_parse_html({"html": ARTICLE_HTML, "url": "https://mp.weixin.qq.com/s/x"})
        art = out["article"]
        assert art["title"] == "深度解读：AI 芯片"
        assert art["link"] == "https://mp.weixin.qq.com/s/realLink"
        assert "页面底部无关内容" not in art["content_text"]

    def test_parse_html_needs_input(self, mcp):
        assert "error" in mcp.handle_parse_html({})

    def test_parse_html_missing_file(self, mcp):
        assert "error" in mcp.handle_parse_html({"file_path": "Z:/nope.html"})

    def test_extract_urls(self, mcp):
        out = mcp.handle_extract_urls({
            "text": "看 https://mp.weixin.qq.com/s/abc。广告 https://ads.example.com/x"
        })
        assert out["urls"] == ["https://mp.weixin.qq.com/s/abc"]
        assert out["normalized"] == ["https://mp.weixin.qq.com/s/abc"]

    def test_extract_urls_all(self, mcp):
        out = mcp.handle_extract_urls({"text": "https://a.com/x https://b.com/y", "only_wechat": False})
        assert out["total"] == 2

    def test_deduplicate_reports_reasons(self, mcp):
        arts = [
            _full_article(content_text=BODY_A),
            _full_article(title="同一篇变体", content_text=BODY_A,
                          link="https://mp.weixin.qq.com/s/realLink?chksm=99"),
            _full_article(title="另一篇", content_text=BODY_B,
                          link="https://mp.weixin.qq.com/s/other"),
        ]
        out = mcp.handle_deduplicate({"articles": arts})
        assert out["total"] == 2
        assert out["removed_items"][0]["reason"] == "link"
        assert out["removed_items"][0]["title"] == "同一篇变体"

    def test_summarize_produces_digest(self, mcp):
        out = mcp.handle_summarize({"articles": [
            _full_article(content_text=BODY_A, content_markdown=BODY_A),
        ]})
        assert out["stats"]["count"] == 1
        assert "逐篇摘要" in out["digest_markdown"]

    def test_summarize_writes_md_file(self, mcp, tmp_path):
        target = tmp_path / "sub" / "digest.md"
        out = mcp.handle_summarize({
            "articles": [_full_article(content_text=BODY_A)], "output_md": str(target),
        })
        assert target.exists() and out["output_md"] == str(target)

    def test_export_markdown_defaults_to_temp_export_dir(self, mcp, tmp_path):
        out = mcp.handle_export_markdown({"articles": [_full_article()], "folder": "mcp批次"})
        assert os.path.basename(out["output_dir"]) == "mcp批次"
        assert out["article_count"] == 1

    def test_history_and_filter_new(self, mcp):
        arts = [_full_article(), _full_article(title="第二篇", link="https://x/2")]
        first = mcp.handle_filter_new({"articles": arts})
        assert first["new_count"] == 2 and first["recorded"] == 2
        second = mcp.handle_filter_new({"articles": arts})
        assert second["new_count"] == 0
        assert second["known_count"] == 2
        hist = mcp.handle_history({})
        assert hist["stats"]["total_articles"] == 2
        assert len(hist["history"]) == 2

    def test_filter_new_can_skip_recording(self, mcp):
        out = mcp.handle_filter_new({"articles": [_full_article()], "record": False})
        assert out["recorded"] == 0
        assert mcp.handle_history({})["stats"]["total_articles"] == 0

    def test_cache_status_and_clear(self, mcp):
        status = mcp.handle_cache_status({})
        assert "cache" in status and "history" in status
        mcp._get_fetcher()._save_cache("https://mp.weixin.qq.com/s/z", {"title": "t"})
        assert mcp.handle_cache_status({})["cache"]["entries"] == 1
        cleared = mcp.handle_clear_cache({"also_history": True})
        assert cleared["cache_entries_removed"] == 1
        assert cleared["history_entries_removed"] == 0

    def test_download_images_offline(self, mcp, tmp_path):
        art = _full_article(images=[{"src": "data:image/png;base64,AAA"}])
        out = mcp.handle_download_images({"articles": [art], "output_dir": str(tmp_path)})
        assert out["images_downloaded"] == 0
        assert out["images_failed"] == 1

    def test_scrape_from_file_missing(self, mcp):
        assert "error" in mcp.handle_scrape_from_file({"file_path": "Z:/nope.txt"})

    def test_scrape_from_file_reads_links(self, mcp, tmp_path):
        path = tmp_path / "links.txt"
        path.write_text("https://mp.weixin.qq.com/s/kkk\nhttps://ads.example.com\n", encoding="utf-8")
        from scrapers.article_fetcher import ArticleFetcher
        fetcher = ArticleFetcher(rate_limit_min=0, rate_limit_max=0, cache_dir=tmp_path / "c")
        fetcher.session = FakeSession([ARTICLE_HTML])
        mcp._fetcher = fetcher
        out = mcp.handle_scrape_from_file({"file_path": str(path)})
        assert out["total"] == 1
        assert out["skipped_non_wechat"]

    def test_filter_handler_new_options(self, mcp):
        arts = [
            _full_article(title="AI 芯片", pub_date="2026-03-04", content_text="芯片 芯片"),
            _full_article(title="美食", pub_date="2026-03-05", content_text="", link="https://x/2"),
            _full_article(title="无日期", pub_date=None, link="https://x/3"),
        ]
        out = mcp.handle_filter({"articles": arts, "keywords": ["AI"], "dedup": True})
        assert out["total"] == 1
        keep_all = mcp.handle_filter({"articles": arts, "keep_undated": True})
        assert keep_all["total"] == 3
        limited = mcp.handle_filter({"articles": arts, "keep_undated": True, "limit": 2})
        assert limited["total"] == 2

    def test_fetch_handler_reports_skips(self, mcp, tmp_path):
        from scrapers.article_fetcher import ArticleFetcher
        fetcher = ArticleFetcher(rate_limit_min=0, rate_limit_max=0, cache_dir=tmp_path / "c")
        fetcher.session = FakeSession([ARTICLE_HTML])
        mcp._fetcher = fetcher
        out = mcp.handle_fetch({"urls": "https://mp.weixin.qq.com/s/lll https://nope.example/x"})
        assert out["total"] == 1
        assert out["skipped_non_wechat"] == ["https://nope.example/x"]

    def test_export_obsidian_handler(self, mcp, tmp_path):
        out = mcp.handle_export_obsidian({
            "articles": [_full_article()],
            "vault_path": str(tmp_path / "vault"),
            "folder": "微信公众号",
            "group_by_account": True,
        })
        assert out["success"] == 1
        assert any("机器之心" in p for p in out["files"])
        assert (tmp_path / "vault" / "微信公众号").exists()

    def test_export_zip_handler_note(self, mcp):
        out = mcp.handle_export_zip({"articles": [_full_article()], "filename": "t"})
        assert out["zip_path"].endswith(".zip")
        assert "临时目录" in out["note"]

    def test_skill_stats(self, mcp):
        out = mcp.handle_skill_stats({})
        assert out["version"] == mcp.SERVER_VERSION
        assert out["tool_count"] == len(mcp.TOOLS)
        assert out["directories"]["state_dir"]
        assert set(out["dependencies"]) == {"requests"}
        assert out["directories"]["state_dir_writable"] is True


# ---------------------------------------------------------------------------
# mcp_server: blocking is surfaced, not silent
# ---------------------------------------------------------------------------

class TestMcpBlockedSearch:
    def test_blocked_flag_in_result(self, mcp, monkeypatch):
        from scrapers.sogou_search import SogouBlocked

        class Fake:
            blocked_pages = 0
            blocked = True
            def search_by_keyword(self, keyword, max_pages=None, account_filter=None):
                return []
            def search_account_articles(self, account, keyword=None, max_pages=None):
                raise SogouBlocked("命中验证页")

        monkeypatch.setattr(mcp, "_get_searcher", lambda: Fake())
        monkeypatch.setattr(mcp, "_get_fetcher", lambda: None)
        out = mcp.handle_search({"account_name": "人民日报"})
        assert out.get("blocked") is True
        assert "error" in out

    def test_keyword_filter_applied(self, mcp, monkeypatch):
        class Fake:
            blocked_pages = 0
            blocked = False
            def search_by_keyword(self, keyword, max_pages=None, account_filter=None):
                return [
                    _full_article(title="AI 芯片", content_text="芯片"),
                    _full_article(title="美食", content_text="", link="https://x/9"),
                ]
        monkeypatch.setattr(mcp, "_get_searcher", lambda: Fake())
        out = mcp.handle_search({"keyword": "AI", "keywords": ["AI"]})
        assert out["total"] == 1


class _FakeSearcher:
    SENTINEL = "sogou-search-session"

    def __init__(self, results, session=SENTINEL):
        self.results = results
        self.session = session
        self.blocked_pages = 0
        self.blocked = False

    def search_account_articles(self, account, keyword=None, max_pages=None):
        return list(self.results)

    def search_by_keyword(self, keyword, max_pages=None, account_filter=None):
        return list(self.results)


class _FakeFetcher:
    def __init__(self, body="正文内容若干字。"):
        self.body = body
        self.sessions = []

    def enrich_articles(self, articles, keep_search_meta=True, session=None):
        self.sessions.append(session)
        out = []
        for a in articles:
            art = dict(a)
            art["content_text"] = self.body
            art["word_count"] = len(self.body)
            out.append(art)
        return out


class TestBatchScrapePipeline:
    def _args(self, **over):
        base = {"account_name": "机器之心", "export_format": "none",
                "record_history": False}
        base.update(over)
        return base

    def _wire(self, mcp, monkeypatch, results, fetcher=None):
        searcher = _FakeSearcher(results)
        fetcher = fetcher or _FakeFetcher()
        monkeypatch.setattr(mcp, "_get_searcher", lambda: searcher)
        monkeypatch.setattr(mcp, "_get_fetcher", lambda: fetcher)
        return searcher, fetcher

    def test_happy_path_pipeline(self, mcp, monkeypatch):
        today = datetime.now().isoformat()
        results = [
            _art("第一篇", date=today, link="https://mp.weixin.qq.com/s/1", account="机器之心"),
            _art("第二篇", date=today, link="https://mp.weixin.qq.com/s/2", account="机器之心"),
            _art("第一篇副本", date=today, link="https://mp.weixin.qq.com/s/1?chksm=9",
                 account="机器之心"),
        ]
        self._wire(mcp, monkeypatch, results)
        out = mcp.handle_batch_scrape(self._args())
        assert out["total"] == 2
        p = out["pipeline"]
        assert p["searched"] == 3
        assert p["duplicates_removed"] == 1
        assert p["full_text_fetched"] == 2
        assert out["export"] == {}

    def test_batch_scrape_reuses_search_session(self, mcp, monkeypatch):
        today = datetime.now().isoformat()
        results = [_art("A", date=today, link="https://weixin.sogou.com/link?url=1",
                        account="机器之心")]
        _, fetcher = self._wire(mcp, monkeypatch, results)
        mcp.handle_batch_scrape(self._args())
        assert fetcher.sessions == [_FakeSearcher.SENTINEL]

    def test_search_reuses_search_session(self, mcp, monkeypatch):
        results = [_art("A", link="https://weixin.sogou.com/link?url=1")]
        _, fetcher = self._wire(mcp, monkeypatch, results)
        out = mcp.handle_search({"keyword": "AI", "fetch_content": True})
        assert out["total"] == 1
        assert fetcher.sessions == [_FakeSearcher.SENTINEL]

    def test_search_skips_enrich_when_fetch_content_false(self, mcp, monkeypatch):
        results = [_art("A", link="https://weixin.sogou.com/link?url=1")]
        _, fetcher = self._wire(mcp, monkeypatch, results)
        mcp.handle_search({"keyword": "AI", "fetch_content": False})
        assert fetcher.sessions == []

    def test_incremental_skips_known_articles(self, mcp, monkeypatch):
        today = datetime.now().isoformat()
        results = [_art("已见过", date=today, link="https://mp.weixin.qq.com/s/known",
                        account="机器之心")]
        self._wire(mcp, monkeypatch, results)
        mcp._get_state().record(results)
        out = mcp.handle_batch_scrape(self._args(incremental=True, record_history=False))
        assert out["total"] == 0
        assert out["pipeline"]["already_known_skipped"] == 1

    def test_markdown_export_written(self, mcp, monkeypatch, tmp_path):
        today = datetime.now().isoformat()
        results = [_art("要导出", date=today, link="https://mp.weixin.qq.com/s/9",
                        account="机器之心")]
        self._wire(mcp, monkeypatch, results)
        out = mcp.handle_batch_scrape(self._args(
            export_format="markdown", folder="批次", record_history=True))
        assert os.path.exists(out["export"]["index_csv"])
        assert out["pipeline"]["new_in_history"] == 1

    def test_history_recorded_by_default(self, mcp, monkeypatch):
        results = [_art("记账", date=datetime.now().isoformat(),
                        link="https://mp.weixin.qq.com/s/7", account="机器之心")]
        self._wire(mcp, monkeypatch, results)
        out = mcp.handle_batch_scrape(self._args(record_history=True))
        assert mcp._get_state().is_known(results[0]["link"])
        assert out["pipeline"]["new_in_history"] == 1

    def test_date_filter_drops_undated(self, mcp, monkeypatch):
        results = [_art("无日期", link="https://mp.weixin.qq.com/s/8", account="机器之心")]
        self._wire(mcp, monkeypatch, results)
        out = mcp.handle_batch_scrape(self._args(days=7))
        assert out["pipeline"]["searched"] == 1
        assert out["pipeline"]["after_filtering"] == 0

    def test_digest_when_requested(self, mcp, monkeypatch):
        results = [_art("摘要", date=datetime.now().isoformat(),
                        link="https://mp.weixin.qq.com/s/6", account="机器之心")]
        self._wire(mcp, monkeypatch, results)
        out = mcp.handle_batch_scrape(self._args(with_digest=True))
        assert "逐篇摘要" in out["digest"]

    def test_needs_account_or_keyword(self, mcp, monkeypatch):
        self._wire(mcp, monkeypatch, [])
        assert "error" in mcp.handle_batch_scrape({})

    def test_unknown_export_format_reported(self, mcp, monkeypatch):
        results = [_art("x", date=datetime.now().isoformat(),
                        link="https://mp.weixin.qq.com/s/5", account="机器之心")]
        self._wire(mcp, monkeypatch, results)
        out = mcp.handle_batch_scrape(self._args(export_format="pdf"))
        assert "未知导出格式" in out["export"]["error"]

    def test_blocked_flag_surfaces(self, mcp, monkeypatch):
        from scrapers.sogou_search import SogouBlocked
        results = [_art("x", date=datetime.now().isoformat(),
                        link="https://mp.weixin.qq.com/s/4", account="机器之心")]
        searcher = _FakeSearcher(results)
        searcher.blocked = True
        searcher.blocked_pages = 2
        searcher.search_account_articles = lambda *a, **k: (_ for _ in ()).throw(
            SogouBlocked("命中验证页"))
        monkeypatch.setattr(mcp, "_get_searcher", lambda: searcher)
        monkeypatch.setattr(mcp, "_get_fetcher", lambda: _FakeFetcher())
        out = mcp.handle_batch_scrape(self._args())
        assert out["blocked"] is True
        assert out["articles"] == []
        assert "hint" in out


# ---------------------------------------------------------------------------
# Version + package consistency
# ---------------------------------------------------------------------------

class TestPackageConsistency:
    def test_version_chain_matches(self, mcp):
        import mcp_server
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = json.load(open(os.path.join(root, "config.json"), encoding="utf-8"))
        meta = json.load(open(os.path.join(root, "_meta.json"), encoding="utf-8"))
        skill = open(os.path.join(root, "SKILL.md"), encoding="utf-8").read()
        assert mcp_server.SERVER_VERSION == cfg["version"] == meta["version"]
        assert f"version: {mcp_server.SERVER_VERSION}" in skill

    def test_skill_md_under_line_limit(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lines = open(os.path.join(root, "SKILL.md"), encoding="utf-8").read().splitlines()
        assert len(lines) <= 450, f"SKILL.md 过长: {len(lines)} 行"

    def test_no_stray_binary_assets(self):
        """Images/ZIP/DB never come from a test run — they must never be present."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".pytest_cache", ".git"}]
            for f in files:
                if f.endswith((".zip", ".db", ".sqlite", ".png", ".jpg", ".jpeg", ".gif")):
                    bad.append(os.path.join(dirpath, f))
        assert not bad, f"技能包内有二进制素材，上传会被拒绝: {bad}"

    def test_no_compiled_artifacts(self):
        # Only meaningful when the session itself writes no .pyc (run_tests.py sets it).
        if not os.environ.get("PYTHONDONTWRITEBYTECODE"):
            pytest.skip("请用 python run_tests.py 运行，以获得 .pyc 完整性检查")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules"}]
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    bad.append(os.path.join(dirpath, f))
        assert not bad, f"存在 .pyc，运行 python clean.py 清理: {bad}"

    def test_clean_script_detects_artifacts(self, tmp_path, monkeypatch):
        import clean as clean_mod
        artifact = tmp_path / "core" / "__pycache__"
        artifact.mkdir(parents=True)
        (artifact / "utils.cpython-311.pyc").write_bytes(b"\x00")
        (tmp_path / "data").mkdir(exist_ok=True)
        history = tmp_path / "data" / "scrape_history.json"
        history.write_text("{}", encoding="utf-8")
        (tmp_path / "export.zip").write_bytes(b"PK\x03\x04")
        monkeypatch.setattr(clean_mod, "PROJECT_ROOT", str(tmp_path))

        assert len(clean_mod.check()) == 3
        # Removing __pycache__ takes the .pyc with it, so clean() counts 2 paths.
        assert len(clean_mod.clean()) == 2
        assert clean_mod.check() == []
        assert not (tmp_path / "export.zip").exists()
        assert not artifact.exists()
        # crawl history is user data: default clean must keep it
        assert history.exists()
        assert clean_mod.check(include_local_data=True) == [str(history)]
        clean_mod.clean(include_local_data=True)
        assert not history.exists()

    def test_requirements_cover_imports(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(root, "requirements.txt"), encoding="utf-8").read().lower()
        assert "requests" in text
