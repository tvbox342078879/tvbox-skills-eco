#!/usr/bin/python
# coding=utf-8
import re, json, requests
from urllib.parse import quote
from base.spider import Spider


class Spider(Spider):
    def getName(self):
        # S162 修：原返回 "优质"——违反本目录 README 第 3 条「getName 写站点中文名，
        # 不要写『优质』『通用』这类无信息量的名字」。一个源叫"优质"，用户在列表里
        # 根本分不出它是哪个站，出问题时也无从定位。改用站点名。
        return "优质影视"

    def init(self, extend=""):
        self.host = "https://api.yyzy-tv.vip"
        self.api = self.host + "/inc/apijson.php"
        self.header = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": self.host + "/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.session = requests.Session()
        self.session.headers.update(self.header)
        self.categories = []

    def _get(self, url):
        for _ in range(2):
            try:
                r = self.session.get(url, timeout=15, verify=False)
                if r.status_code == 200 and r.text:
                    return r.text
            except Exception:
                continue
        return ""

    def _json(self, url):
        txt = self._get(url)
        if not txt:
            return {}
        try:
            d = json.loads(txt)
        except Exception:
            return {}
        return d if isinstance(d, dict) else {}

    def _fix(self, url):
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return self.host + url
        return url

    def _clean(self, s):
        return re.sub(r"\s+", " ", str(s or "")).strip()

    def isVideoFormat(self, url):
        return bool(url) and bool(re.search(r"\.(?:m3u8|mp4|flv|mkv|ts)(?:$|[?#])", str(url), re.I))

    def manualVideoCheck(self):
        return False

    def homeContent(self, filter):
        """分类树 → TVBox class 列表。

        ★ 实测坑（S148 用真实站点走流程时撞出来的）：本 API 的 `ac=class` **不返回 `type_pid`**，
        而父分类（电影/连续剧/综艺/动漫，id 1–4）是**空壳**——`ac=detail&t=1` 返回 `total:0`，
        子分类（动作片 id5、国产剧 id12…）才有数据。若照搬「直接取 cls[0]」，
        用户在首页点第一个分类就是**一片空白**——这正是探源司 §3.7 记录的那个坑。
        处理：不靠 `type_pid`（没有），靠**实测探针**判空壳——对候选分类各取一页看 total，
        为 0 的直接剔除（并保留原顺序）。只探前若干个，避免一次打几十个请求。
        """
        result = {"class": [], "filters": {}}
        d = self._json(self.api + "?ac=class")
        cls = d.get("class") or []
        if not cls:
            d2 = self._json(self.api + "?ac=detail&pg=1")
            seen, out = set(), []
            for it in (d2.get("list") or []):
                tid, tname = str(it.get("type_id") or ""), self._clean(it.get("type_name"))
                if tid and tid not in seen and tname:
                    seen.add(tid)
                    out.append({"type_id": tid, "type_name": tname})
            cls = out

        cand = [{"type_id": str(x.get("type_id") or ""), "type_name": self._clean(x.get("type_name"))}
                for x in cls if self._clean(x.get("type_name")) and str(x.get("type_id") or "")]

        # 空壳探测：只探前 8 个（父分类都在最前），有子分类数据就不探了
        alive, probe_n = [], 0
        for c in cand:
            if probe_n < 8 and not alive:
                probe_n += 1
                dd = self._json(f"{self.api}?ac=detail&t={c['type_id']}&pg=1")
                if int(dd.get("total") or 0) > 0:
                    alive.append(c)
                continue
            alive.append(c)
        self.categories = alive if alive else cand
        result["class"] = self.categories
        return result

    def homeVideoContent(self):
        d = self._json(self.api + "?ac=detail&pg=1")
        return {"list": self._parse_list(d.get("list") or [])}

    def _parse_list(self, items):
        out, seen = [], set()
        for it in items:
            try:
                vid = str(it.get("vod_id") or "")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                out.append({
                    "vod_id": vid,
                    "vod_name": self._clean(it.get("vod_name")),
                    "vod_pic": self._fix(it.get("vod_pic")),
                    "vod_remarks": self._clean(it.get("vod_remarks")),
                })
            except Exception:
                continue
        return out

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg or 1)
        d = self._json(f"{self.api}?ac=detail&t={tid}&pg={pg}")
        lst = self._parse_list(d.get("list") or [])
        total = int(d.get("total") or 0)
        limit = int(d.get("limit") or 20)
        pagecount = int(d.get("pagecount") or (max(1, (total + limit - 1) // limit) if total else 1))
        return {"list": lst, "page": pg, "pagecount": pagecount,
                "limit": limit, "total": total}

    def detailContent(self, ids):
        vid = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        d = self._json(f"{self.api}?ac=detail&ids={vid}")
        items = d.get("list") or []
        if not items:
            return {"list": []}
        it = items[0]
        froms = [self._clean(x) for x in str(it.get("vod_play_from") or "").split("$$$") if self._clean(x)]
        urls = str(it.get("vod_play_url") or "").split("$$$")
        play_f, play_u = [], []
        for i, seg in enumerate(urls):
            eps = []
            for part in str(seg).split("#"):
                if "$" in part:
                    nm, u = part.split("$", 1)
                    u = self._fix(self._clean(u))
                    if u:
                        eps.append(f"{self._clean(nm)}${u}")
                elif part.strip():
                    u = self._fix(self._clean(part))
                    if u:
                        eps.append(f"第{len(eps) + 1}集${u}")
            if eps:
                play_f.append(froms[i] if i < len(froms) else f"线路{i + 1}")
                play_u.append("#".join(eps))
        if not play_u:
            return {"list": []}
        v = {"vod_id": vid, "vod_name": self._clean(it.get("vod_name")),
             "type_name": self._clean(it.get("type_name")),
             "vod_pic": self._fix(it.get("vod_pic")),
             "vod_year": self._clean(it.get("vod_year")),
             "vod_area": self._clean(it.get("vod_area")),
             "vod_remarks": self._clean(it.get("vod_remarks")),
             "vod_actor": self._clean(it.get("vod_actor")),
             "vod_director": self._clean(it.get("vod_director")),
             "vod_content": self._clean(it.get("vod_content")),
             "vod_play_from": "$$$".join(play_f), "vod_play_url": "$$$".join(play_u)}
        return {"list": [v]}

    def searchContent(self, key, quick, pg="1"):
        d = self._json(f"{self.api}?ac=detail&wd={quote(str(key))}&pg={int(pg or 1)}")
        return {"list": self._parse_list(d.get("list") or []), "page": int(pg or 1)}

    def playerContent(self, flag, id, vipFlags):
        u = self._fix(str(id or ""))
        if self.isVideoFormat(u):
            return {"parse": 0, "playUrl": "", "url": u, "header": self.header}
        txt = self._get(u)
        if txt:
            for pat in (r'"(https?://[^"]+\.m3u8[^"]*)"', r"var\s+(?:now|playurl)\s*=\s*[\"']([^\"']+)",
                        r'<iframe[^>]+src=["\']([^"\']+)'):
                m = re.search(pat, txt, re.I)
                if m:
                    cand = self._fix(m.group(1).replace("&amp;", "&"))
                    if self.isVideoFormat(cand):
                        return {"parse": 0, "playUrl": "", "url": cand, "header": self.header}
        return {"parse": 1, "playUrl": "", "url": u, "header": self.header}

    def localProxy(self, param):
        return None
