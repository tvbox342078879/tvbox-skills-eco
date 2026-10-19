# -*- coding: utf-8 -*-
"""
base.spider stub —— TVBox py 运行时的最小基类模拟（S116）。

真实 TVBox py 环境里 `base/spider.py` 提供的 Spider 基类：六大接口默认实现 +
fetch/post/getProxyUrl/getCache/setCache 等助手。沙箱真跑 py spider 时用它顶替。

实现口径（从 46 个真实 py 源码的 self.* 调用差集统计得出，非照记忆硬写）：
  fetch: 17 个文件调用 ｜ post: 11 ｜ getProxyUrl: 8 ｜ getCache: 4 ｜ setCache: 4
  TokenExpired: 1 ｜ html: 1
"""
import requests


class TokenExpired(Exception):
    pass


class Spider:
    """TVBox py Spider 基类的最小可用模拟。"""

    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

    def __init__(self):
        # S116 修复（瓜子实证）：部分 spider（如瓜子）自带 __init__ 且不调
        # super().__init__()——Python 里基类 __init__ 根本不执行，而真机是
        # Java 基类、构造链隐式向上永远成立。故实例状态一律懒初始化
        # （_get_session），不对"基类 __init__ 跑过"做任何假设。
        pass

    def _get_session(self):
        sess = self.__dict__.get('_session')
        if sess is None:
            sess = requests.Session()
            self._session = sess
        return sess

    # ---------- 六大接口：默认空实现（子类覆盖） ----------
    def init(self, extend=""):
        pass

    def homeContent(self, filter):
        return {}

    def homeVideoContent(self):
        return {}

    def categoryContent(self, tid, pg, filter, extend):
        return {}

    def detailContent(self, ids):
        return {}

    def searchContent(self, key, quick):
        return {}

    def playerContent(self, flag, id, vipFlags):
        return {}

    def isVideo(self, url):
        return True

    def getName(self):
        return ''

    # ---------- 基类助手（真实 TVBox 语义的最小模拟） ----------
    def fetch(self, url, headers=None, timeout=12, method='GET', data=None,
              cookies=None, allow_redirects=True, **kw):
        return self._get_session().request(
            method, url,
            headers=headers or {'User-Agent': self.UA},
            data=data, cookies=cookies, timeout=timeout,
            allow_redirects=allow_redirects)

    def post(self, url, data=None, headers=None, timeout=12, cookies=None, **kw):
        return self.fetch(url, headers=headers, timeout=timeout, method='POST',
                          data=data, cookies=cookies, **kw)

    def getProxyUrl(self, url='', headers=None, **kw):
        # 真机里本地代理转发；沙箱直连目标
        return url

    _cache = {}

    def getCache(self, key):
        return self._cache.get(key)

    def setCache(self, key, value):
        self._cache[key] = value

    def html(self, url, headers=None, timeout=12):
        r = self.fetch(url, headers=headers, timeout=timeout)
        return r.text

    def log(self, *args):
        pass
