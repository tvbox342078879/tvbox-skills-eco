#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探源司（probe-hub）：上游侦察引擎 —— 监测作者仓库 / 搜索新仓库 / 采集高质量线路

三模块:
  monitor  监测底包库原作者的第三方仓库动态（存活/内容变更/新目录/更新活跃度）
  search   搜索更多有效可用的第三方仓库（GitHub/Gitee → rank → verify）
  harvest  采集高质量线路（sites/lives/parses → 测活 → 去伪 → 候选 → ingest 入库）

子命令:
  monitor check [--repo URL] [--all] [--json]   四类信号逐条三态
  monitor diff  [--repo URL]                    快照对比（本次 vs 上次）
  monitor forecast [--repo URL] [--json]        活跃度趋势外推→弃更风险（S093）
  monitor status                                监测目标总览表
  monitor add --url URL [--author X] [--cadence 每周] [--branch main] [--config-path P] [--related 底包]
  monitor due                                   列出到期未检查目标
  search  github --topic tvbox [--limit N]
  search  gitee  --keyword tvbox [--limit N]
  search  rank   --in raw.json
  search  verify --repo URL
  harvest scan   --repo URL [--path 子目录]
  harvest pull   --url FILE_URL --kind sites|lives|parses
  harvest filter [--in candidates.json]
  harvest export [--out harvest_MMDD.json]
  harvest dispatch
  self-check                                    十监测器活体探针 + 假阳性基线

监测器铁律（M-R55~M-R60）：所有监测函数三态返回（真阳性/真阴性/无法判定），
禁 `except: return []` 折叠；activity_log.json 作为数据基座损坏态拒绝回写覆盖。

库: DB_SRC=wW5xpbS663nA6XK8a7DNsp / DB_DEAD=92dSuG6uUbxitGuyx4Saaf
"""
import os, sys, io, json, time, argparse, subprocess, hashlib, re, shutil, zlib
import ast, builtins, inspect, textwrap   # S194：供 _undefined_names（self-check ⑰d 探针）
from collections import Counter
from urllib.parse import urlparse, urlsplit, unquote, quote
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ---------- M-R114 统一并发入口（S110 / S163 收口） ----------
# 本文件所有真实测活一律走 concurrency.run_bounded（B 档，对公网站点上限 6）。
# ★ S163：原 `_mk_pool()` 直接 new 一个 ThreadPoolExecutor——虽然档位算对了，
#   但仍**绕过统一入口的写集合登记/拖进程护栏/收益实测**，且 audit_concurrency
#   的"整文件放行"正是被它触发的。现已全部改走 run_bounded，本函数删除。
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')


def _count_success(out, expect):
    """解析写入回执 → (成功条数, 错误串)。**不信任退出码，只信任 results**。"""
    try:
        d = json.loads(out)
    except Exception:
        return 0, (out or '')[:120] or '空回执'
    if isinstance(d, dict) and d.get('error'):
        return 0, str(d['error'])[:120]
    rs = (d or {}).get('results') if isinstance(d, dict) else None
    if not isinstance(rs, list):
        return 0, f'回执无 results（{str(d)[:80]}）'
    good = sum(1 for r in rs if isinstance(r, dict) and r.get('success'))
    err = ''
    for r in rs:
        if isinstance(r, dict) and not r.get('success'):
            err = str(r.get('error') or r.get('message') or '')[:120]
            break
    return good, (err if good < expect else '')



LIB = '/root/.codebuddy/skills/library'
HUB = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
BLK = '/root/.codebuddy/skills/tvbox-source-blacklist/scripts/blk.py'
sys.path.insert(0, HUB)
from core import SRC_DBS as _SRC_DBS, http_get   # noqa: E402
_SRC_BY_NAME = dict(_SRC_DBS)
# ★ S160：py/csp 两库合并为「线路源库」，原按名取 'py线路源库'/'成品源库' 会 KeyError。
#   本技能**实际只读 DB_DEAD / DB_PROD**；DB_SRC 仅作兼容别名，勿新增读取。
DB_SRC = _SRC_BY_NAME['线路源库']         # 兼容别名；本技能不读它，勿新增读取
DB_DEAD = 'hGwE41OpJDOR8CW0sLUwRx'     # 🚫 探源司·失信host库（1017 新建，取代旧「失效源库」）
DB_DEAD_LEGACY = '92dSuG6uUbxitGuyx4Saaf'  # 🗑 旧失效源库（已清空废弃，仅留 ID 供回溯）
DB_UPSTREAM = 'aCSvley568A0aW3sggzJwA'  # 📡 上游仓库监测册（S088 建库；空=退回本地登记册）
DB_CANDIDATE = 'khHl458xi8qK15lVaYfbvU'  # 🔍 搜索候选册（S095 建库；空=仅本地）
DB_HARVEST = 'E5I6T2UXIS7CB5r0AkimRl'    # 📥 采集候选册（S095 建库；空=仅本地）
DB_PROD = _SRC_BY_NAME['成品源库']        # ✅ 成品源库（坐标取自 core，勿再写死）
                                          #    只存"真实测活通过"的成品线路源——拿来即可放进 json

# 云端批量写入单批上限（S095）：平台 `batch_add/update` 实测限 1–100 条，
# 超出即整批被拒（"records 必须包含 1–100 个对象"）。大仓库一次采集上百条，故分批。
CLOUD_BATCH = 100

SELF_DIR = os.path.dirname(os.path.abspath(__file__))
TARGETS = os.path.join(SELF_DIR, 'monitor_targets.json')   # 监测目标登记册
ALOG = os.path.join(SELF_DIR, 'activity_log.json')         # 采样史数据基座
HARVEST_DIR = os.path.join(SELF_DIR, 'harvest_candidates') # 采集产出暂存

UA = 'okhttp/3.12.0'                                        # TVBox 伪装 UA
UA_WEB = ('Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
          'Chrome/120 Mobile Safari/537.36')                # 浏览器 UA：发布页探活回退专用（蓝奏云对 okhttp 吐空，实测 0B vs 6551B）
CURL = 'curl'

# 测活判定码（单一事实源：与 tvbox-interface/scripts/pipeline.py 的 ALIVE_CODES 同口径）
# M-R3：监测口径须与上游技能同口径；中立起见在此显式复制并锚定来源，不跨技能 import。
ALIVE_CODES = {200, 206, 301, 302, 303, 307, 308}

# ── 多平台适配（S091）──────────────────────────────────────────────
# 各代码托管平台的 API 基址与路径模板。**单一事实源**：平台差异只在此表 +
# `_platform_meta` 分支里声明，其余代码一律走统一结构（防平台分支散落各处）。
#
# 说明：
#  · GitHub / Gitee 用 REST API（匿名可读，有配额）；GitLab 用 v4（project 须 URL 编码 `owner%2Frepo`）。
#  · **Gitea 系（含自建 Gitea / 腾讯工蜂）走同一套 `/api/v1/repos/...` 兼容接口**——
#    host 不同但路径同构，故归为 'Gitea' 一族、由 host 动态拼基址（见 `_platform_base`）。
#  · raw 基址：各平台"取原始文件"的路径形态不同（见 `_find_config_path`）。
PLATFORM_RAW = {
    'GitHub': 'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/',
    'Gitee':  'https://gitee.com/{owner}/{repo}/raw/{branch}/',
    'GitLab': 'https://gitlab.com/{owner}/{repo}/-/raw/{branch}/',
}
# Gitea 系（自建/工蜂）raw 基址需 host：'https://{host}/{owner}/{repo}/raw/branch/{branch}/'


def _platform_base(platform, host):
    """按平台取 API 基址。Gitea 系依赖 host（自建/工蜂），其余为固定域名。"""
    if platform == 'GitHub':
        return 'https://api.github.com'
    if platform == 'GitLab':
        return 'https://gitlab.com/api/v4'
    if platform == 'Gitee':
        return 'https://gitee.com/api/v5'
    if platform == 'Gitea':
        return f'https://{host}/api/v1'
    return ''


def _parse_ts(s):
    """鲁棒解析 ISO8601 时间戳 → epoch 秒；失败返回 None（三态载体，**不折叠成 0**）。

    S091：各平台时间格式不一——GitHub 用 `2026-02-09T12:00:00Z`（UTC），
    GitLab 用 `2026-10-15T14:11:38.000-04:00`（带偏移+毫秒），Gitee 用
    `2025-09-25T00:32:16+08:00`。硬编码 `%Y-%m-%dT%H:%M:%SZ` 会把后两者**静默解析失败**
    → `pushed_at=None` → 活跃度误判"无法判定"。故统一用 `fromisoformat` 并归一化 'Z'。
    返回 None 表示"无法解析"（与"确实没有时间"共用，但调用方据 None 走'无法判定'分支，
    不会误报"活跃/停滞"——三态可区分）。
    """
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if t.endswith('Z'):
        t = t[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)   # 无时区信息 → 按 UTC 处理（保守）
        return dt.timestamp()
    except Exception:
        # 回退 1：仅取日期部分（某些平台只给 YYYY-MM-DD）
        try:
            return datetime.strptime(t[:10], '%Y-%m-%d').replace(
                tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
        # 回退 2：HTTP-date（RFC 1123，如 "Sun, 18 Oct 2026 07:21:13 GMT"）
        # 发布源型目标的 Last-Modified 头即此格式；不解析则活跃度恒"无法判定"。
        try:
            from email.utils import parsedate_to_datetime
            _dt = parsedate_to_datetime(t)
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=timezone.utc)
            return _dt.timestamp()
        except Exception:
            return None

# 共享效率核心（S123 收窄：仅同一进程内与 link/blk/evolve 共享 core 模块实例→缓存/并行池复用；跨技能 CLI 单命令调用时各自独立实例，无跨进程复用）
sys.path.insert(0, HUB)
from core import (rows_of, rows_of_t, api, add_records, invalidate,
                  file_to_janitor, register_monitor)  # noqa: E402

# ★ 生态联动（S106 补接门面）：
#   · harvest prune 剔除的失效候选集/废弃中间产物 AI 删不掉 → core.file_to_janitor 投递清道夫
#   · 上游侦察发现新失效模式（新停更原因/新仓库类型）→ core.register_monitor 登记监察司
def _eco_linked_probe():
    """自证已接门面：返回 (清道夫, 监察司) 可达性，供 inspect 联动覆盖判据与自检。"""
    return (callable(file_to_janitor), callable(register_monitor))


# ══════════════════ 三态原语（M-R55~M-R60：禁裸 except 折叠成空） ══════════════════

def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _next_due(cadence, base_ts=None):
    """按监测周期算下次到期（ISO8601）。未知周期 → 每周（保守默认）。"""
    days = {'每日': 1, '每周': 7, '每月': 30}.get(cadence or '每周', 7)
    ts = (base_ts or time.time()) + days * 86400
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


def _persist_note(cloud_ok, local_ok):
    """双写结果显式上报（禁静默：任一失败都要让用户看见）。"""
    if cloud_ok and local_ok:
        print('   💾 已双写：云端 DB_UPSTREAM + 本地备份')
    elif local_ok:
        print('   ⚠️ 云端同步失败（本地已存，可稍后重跑 monitor add 或 check 补同步）')
    elif cloud_ok:
        print('   ⚠️ 本地备份写入失败（云端已存）')
    else:
        print('   ❌ 云端与本地均写入失败——请检查库权限/磁盘')


def _sh(cmd, timeout=20):
    """执行外部命令，返回 (rc, stdout)。失败给哨兵前缀（M-R59：失败信号可靠统一）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or '')
    except Exception as e:
        return -1, f'⛔命令失败:{type(e).__name__}'


def _sh_bytes(cmd, timeout=20):
    """执行外部命令，返回 (rc, bytes)——专供二进制正文（zip/jar 前 N KB）。

    为什么不复用 _sh：_sh 走 text=True 按 UTF-8 解码，zip 这类二进制前 64KB 含非法
    字节序列 → UnicodeDecodeError 被 except 吞成 rc=-1 → 正文指纹恒空、误报"正文
    取不到"（小虎斑更新包 4.56MB 即踩此坑）。二进制一律用本函数拿原始 bytes。
    """
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or b'')
    except Exception:
        return -1, b''


# ══════════════════ GitHub 鉴权（S089：token 多源解析 → 解除 60/h 限流） ══════════════════
#
# 背景（S088 暴露）：未鉴权 API 限额 60 req/h，一次 monitor check 就吃掉 3 个请求
# （repos/commits/tree），几只仓库即触碰上限 → 'ratelimit' → 近30天提交/目录树
# 全部"无法判定"。这不是 bug 而是**配额**，只有凭据能解。
#
# 设计要点：
#  ① **多源解析，顺序即优先级**：环境变量 → token 文件 → gh CLI（loginctl 已登录）。
#     环境变量优先，便于临时覆盖；文件次之，便于持久；gh 兜底，零配置。
#  ② **绝不把 token 写进日志/输出**：`_mask()` 只暴露前后 4 位；`limits` 命令显示时遮罩。
#  ③ **三态**：'ok'（拿到）/ 'none'（确实没有）/ 'fail'（读取过程出错，**不得当没有**）——
#     若把读取失败折叠成"无 token"，用户会以为"我配了但没生效"，无从排查（M-R60）。
#  ④ **token 缺失不阻断**：降级为未鉴权，照常可用，只是受限——不得因无凭据而报错退出。

_TOKEN_CACHE = {'resolved': False, 'state': 'none', 'value': '', 'source': ''}
ENV_NAMES = ('GITHUB_TOKEN', 'GH_TOKEN', 'TVBOX_GITHUB_TOKEN', 'GITHUB_PAT')


def _token_file_path():
    """token 文件位置（与监测册同目录，便于随技能走）。"""
    return os.path.join(SELF_DIR, '.gh_token')


def _global_token_path():
    """全局令牌路径（S209：全组件单一事实源，更新令牌只需覆盖它一处）。

    SELF_DIR=skills/probe-hub/scripts，上三级即 /root/.codebuddy/.gh_token。
    本地 token 文件仅作临时覆盖（token --set）用；本地缺失时自动回退全局，
    不依赖符号链接等本机状态（换机恢复后照样生效）。
    """
    return os.path.normpath(os.path.join(SELF_DIR, '..', '..', '..', '.gh_token'))


def _load_token(refresh=False):
    """三态解析 GitHub token → (state, token, source)。state ∈ {'ok','none','fail'}。

    优先级：环境变量 > token 文件 > gh CLI。结果进程内缓存（**仅缓存成功/确定无**，
    读取异常不缓存——M-R60⑧：失败结果禁止进缓存，否则一次失败毒化整进程）。
    """
    if _TOKEN_CACHE['resolved'] and not refresh:
        return _TOKEN_CACHE['state'], _TOKEN_CACHE['value'], _TOKEN_CACHE['source']

    def _accept(v, src):
        v = (v or '').strip()
        # GitHub token 形态：ghp_/github_pat_/gho_/ghs_/ghu_/ghr_ 前缀，或 40 位十六进制
        if len(v) >= 20 and not any(ch.isspace() for ch in v):
            _TOKEN_CACHE.update({'resolved': True, 'state': 'ok',
                                 'value': v, 'source': src})
            return True
        return False

    def _settle(state, value, source):
        """无论 ok/none/fail 都写缓存（M-R60⑧ 反例修正）。

        S089 实证：'none' 分支若直接 return 而不写缓存，则**状态由 ok 变 none 后，
        缓存里仍滞留旧 token**——随后 `_gh_headers()`（不 refresh）命中脏缓存，
        把已失效的 token 继续注入请求头。故**任何确定结果（含 none/fail）都必须
        覆盖缓存**，否则"状态变了缓存没变"= 另一种静默折叠。
        """
        _TOKEN_CACHE.update({'resolved': True, 'state': state,
                             'value': value, 'source': source})
        return state, value, source

    # ① 环境变量
    for n in ENV_NAMES:
        if os.environ.get(n) and _accept(os.environ[n], f'环境变量 {n}'):
            return 'ok', _TOKEN_CACHE['value'], _TOKEN_CACHE['source']

    # ② token 文件（首行，忽略 # 注释）：本地优先；本地不存在时回退全局令牌
    #    （S209 单一事实源：全局 /root/.codebuddy/.gh_token 一处更新全组件生效，
    #    换机恢复由 clone_restore.sh 自动落位，不再依赖符号链接）
    fp = _token_file_path()
    if not os.path.exists(fp):
        _gfp = _global_token_path()
        if os.path.exists(_gfp):
            fp = _gfp
    if os.path.exists(fp):
        try:
            with io.open(fp, encoding='utf-8') as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln or ln.startswith('#'):
                        continue
                    if _accept(ln, f'token 文件 {os.path.basename(fp)}'):
                        return 'ok', _TOKEN_CACHE['value'], _TOKEN_CACHE['source']
            return _settle('none', '', f'token 文件存在但无有效行（{fp}）')
        except Exception as e:
            # 读取异常**不得折叠成"无 token"**——显式 fail，让用户知道是权限/编码问题
            return _settle('fail', '', f'token 文件读取失败：{type(e).__name__}')

    # ③ gh CLI（可能已 login）
    rc, out = _sh(['gh', 'auth', 'token'], timeout=10)
    if rc == 0 and _accept(out, 'gh CLI'):
        return 'ok', _TOKEN_CACHE['value'], _TOKEN_CACHE['source']

    return _settle('none', '', '')


def _mask(tok):
    """token 遮罩显示（禁全量外泄）。"""
    if not tok:
        return '(未配置)'
    if len(tok) <= 8:
        return '*' * len(tok)
    return tok[:4] + '*' * (len(tok) - 8) + tok[-4:]


def _gh_headers(extra=None):
    """构造 GitHub API 请求头；有 token 则加 Authorization（无则不加，走匿名）。"""
    st, tok, _src = _load_token()
    hs = ['-H', 'Accept: application/vnd.github+json',
          '-H', 'X-GitHub-Api-Version: 2022-11-28']
    if st == 'ok' and tok:
        hs += ['-H', f'Authorization: Bearer {tok}']
    if extra:
        hs += extra
    return hs


def _rate_limit(timeout=15):
    """查当前配额 → (state, dict)。dict 含 limit/remaining/reset/used。

    S089 实测观察：GitHub 的 `rate_limit` 端点响应**偶被 CDN 缓存**，可能返回
    **上一个时间窗**的陈旧值（表现为 remaining 忽 0 忽 28、reset 时间戳跳动约 16 分钟）。
    这不是本地 bug，但会让用户困惑，故：
      · 若 `reset` 已过期（早于当前时间）→ 标注 'stale'，提示"数据可能陈旧，以实际
        调用返回的限流响应为准"，**不得当作"额度已满"阻断后续操作**。

    ★ S181（实测，比上面那条更严重）：**CDN 缓存能返回「剩余 58」而真实是 0。**
      实测经过：配额耗尽后等 143 秒，`probe token` 报「剩余 58 · 重置 13:05」，
      于是放心跑 `monitor check`——6 个仓库全部限流空转。直连 `/rate_limit`
      复核：`remaining=0, reset=12:35`。缓存的是一小时前的快照。
      「报剩余充足」比「报已耗尽」危险得多：前者让人**主动去做无效的事**。
      两层修：
        ① 请求加 `Cache-Control: no-cache`（打掉 CDN 缓存这一类）；
        ② 对 `remaining > 0` 的结果**不无条件相信**——`_api_get` 真撞上 403/429 时，
           调用方应把「本端点说还有额度」与「实际已限流」并列呈现，不许只信前者。
      ② 的落点在 `cmd_monitor_check` 的预检里（见 where 参数）。

    ★★ S181 第二轮实测（更重要，推翻了上面「CDN 缓存」的解释）：
      加禁缓存头后，**同一秒内连查三次仍会在两个值之间跳**：
          shell 路径: (0, reset=1792470954)   py 路径: (45, reset=1792472730)
          shell 路径: (45, reset=1792472730)  py 路径: (0, reset=1792470954)
      两个 reset 相差 **30 分钟**，说明不是缓存，而是 **GitHub 侧存在多个配额桶**
      （不同边缘节点各自计数），请求随机落到哪个桶就报哪个值。
      结论：**「查配额」这个动作从原理上就答不准**，无论怎么加头。
      故本函数的 remaining 只能作**参考值**，判决权必须交给**实际调用的返回码**：
        · 真撞 403/429 → 就是没额度，不管本函数说还有多少
        · 真返回 200  → 就是有额度，不管本函数说 0
      调用方（`cmd_monitor_check` 预检 / `cmd_token` 展示）都按这个口径写。
    """
    url = 'https://api.github.com/rate_limit'
    rc, out = _sh([CURL, '-sS', '-L', '-m', str(timeout), '-A', UA,
                   '-H', 'Cache-Control: no-cache', '-H', 'Pragma: no-cache']
                  + _gh_headers() + [url], timeout=timeout + 5)
    if rc != 0 or not out:
        return 'fail', {}
    try:
        d = json.loads(out)
        core_rl = (d.get('resources') or {}).get('core') or {}
        if not isinstance(core_rl, dict) or 'limit' not in core_rl:
            return 'fail', {}
        core_rl = dict(core_rl)
        # 陈旧检测：reset 已过期说明这是上一窗口的缓存快照
        reset = core_rl.get('reset') or 0
        if reset and reset < time.time() - 60:
            core_rl['stale'] = True
        else:
            core_rl['stale'] = False
        return 'ok', core_rl
    except Exception:
        return 'fail', {}


# ── 域名型监测目标（S204 扩能）：companion 本地真源 ───────────────────────
# 云端 _cloud_row_to_target 只读 18 固定列、不含 'kind'，且 _cloud_upsert 对非
# select/num/date 列（如 '种子' 嵌套 list、'名称'/'kind' 非云列）会静默丢/拒收。
# 故域名型**结构字段**（模式/种子/前缀格式/名称）只存本地 companion，每次 load
# 按 id 反推 'kind' 并注入，绝不依赖云端 round-trip。
_DOMAIN_COMPANION = os.path.join(SELF_DIR, 'monitor_domains.json')
_DOMAIN_TRANSIENT = ('模式', '种子', '前缀格式')   # 仅存 companion，save 时剥离

# ── 发布源型监测目标（S199·借鉴小虎斑）：对象存储直连包 ──────────────────
# 背景：现有 monitor 只认 GitHub 类仓库（有 commit 维度）。小虎斑系 TVBox 壳的
# 配置是「对象存储直连包」（如 xiaohuban.cn-nb1.rains3.com 上的 hban.fuck 容器），
# 无 commit、无 git 历史——传统监测对其是**黑洞**（不被登记或永远"无法判定"）。
# 故新增 pub-package 型：以「发布源 URL」为锚，HTTP 轻量探活（Content-Length /
# Last-Modified / ETag + 前 64KB 内容指纹），与快照比对即知「包在不在 / 变没变」。
# 结构字段（发布源URL/包类型/关联底包/监测周期）只存本地 companion，
# 每次 load 按 id 注入 kind='pub-package'，绕开云端非标列丢失（同 domain-set 思路）。
_PUB_COMPANION = os.path.join(SELF_DIR, 'monitor_pubsources.json')
_PUB_TRANSIENT = ('发布源URL', '包类型', '关联底包', '监测周期')  # 仅存 companion


def _load_pub_companion():
    """读发布源型 companion（本地真源）。返回 dict 或 None（缺失/损坏）。"""
    if not os.path.exists(_PUB_COMPANION):
        return None
    try:
        d = json.load(open(_PUB_COMPANION, encoding='utf-8'))
        return d if isinstance(d, dict) else None
    except Exception as e:
        print(f'  ⚠️ 发布源 companion 文件 {os.path.basename(_PUB_COMPANION)} '
              f'解析失败（{type(e).__name__}）——发布源型目标本轮无法判活')
        return None


def _inject_pub_sources(targets):
    """对发布源型目标注入结构字段（按 id 反推 kind，绕开云端丢列）。原地注入并返回。

    - companion 里登记的 id → 必为 pub-package（本地真源权威）；
    - repo 型 / domain-set 完全不碰；
    - 兜底：companion 有、登记册无 → 按 companion 补出最小指针（不凭空造待查空壳）。
    """
    comp = _load_pub_companion()
    if not comp:
        return targets
    pubs = comp.get('pubs', {}) or {}
    present = {t.get('id') for t in targets if isinstance(t, dict)}
    for t in targets:
        if not isinstance(t, dict):
            continue
        spec = pubs.get(t.get('id', ''))
        if not spec:
            continue
        t['kind'] = 'pub-package'            # 反推 kind（云端 round-trip 会丢）
        for k in ('发布源URL', '包类型', '名称', '关联底包', '监测周期'):
            if k in spec and spec[k] not in (None, ''):
                t[k] = spec[k]
        if not t.get('名称'):
            t['名称'] = spec.get('名称', t.get('id', ''))
    # 兜底补出：companion 有、登记册无 → 补最小指针（继承本地健康字段，不造空壳）
    _local_map = {}
    if os.path.exists(TARGETS):
        try:
            _d = json.load(open(TARGETS, encoding='utf-8'))
            for _t in _d.get('targets', []) or []:
                if isinstance(_t, dict) and _t.get('id'):
                    _local_map[_t['id']] = _t
        except Exception:
            pass
    for _id, spec in pubs.items():
        if _id in present:
            continue
        old = _local_map.get(_id) or {}
        targets.append({
            'id': _id, 'kind': 'pub-package',
            '名称': spec.get('名称') or old.get('名称') or _id,
            '发布源URL': spec.get('发布源URL') or old.get('发布源URL', ''),
            '包类型': spec.get('包类型') or old.get('包类型') or 'zip',
            '平台': '发布源',
            '监测周期': spec.get('监测周期') or old.get('监测周期') or '每周',
            '关联底包': spec.get('关联底包') or old.get('关联底包', ''),
            '存活状态': old.get('存活状态') or '待查',
            '活跃度': old.get('活跃度') or '待查',
            '上次检查': old.get('上次检查') or '',
            '下次到期': old.get('下次到期') or '',
            '变更摘要': old.get('变更摘要') or '',
        })
        present.add(_id)
    return targets


def _load_domain_companion():
    """读域名型 companion（本地真源）。返回 dict 或 None（缺失/损坏）。"""
    if not os.path.exists(_DOMAIN_COMPANION):
        return None
    try:
        d = json.load(open(_DOMAIN_COMPANION, encoding='utf-8'))
        return d if isinstance(d, dict) else None
    except Exception as e:
        print(f'  ⚠️ 域名 companion 文件 {os.path.basename(_DOMAIN_COMPANION)} '
              f'解析失败（{type(e).__name__}）——域名型目标本轮无法判活')
        return None


def _inject_domain_sets(targets):
    """对域名型目标注入结构字段（按 id 反推 kind，绕开云端丢列）。原地注入并返回。

    - companion 里登记的 id → 必为 domain-set（本地真源权威）；
    - 缺结构字段时该目标存活置 '无法判定'（不冒充当仓库解析失败）；
    - repo 型目标完全不碰；
    - 兜底：companion 有、但登记册（云端/本地）缺失的域名目标，按 companion
      补出最小指针——保证「companion 即权威」：即使云端 sync 因非标 平台 选项
      被拒，域名目标也不会从监测里凭空消失。
    """
    comp = _load_domain_companion()
    if not comp:
        return targets
    domains = comp.get('domains', {}) or {}
    present = {t.get('id') for t in targets if isinstance(t, dict)}
    for t in targets:
        if not isinstance(t, dict):
            continue
        spec = domains.get(t.get('id', ''))
        if not spec:
            continue
        t['kind'] = 'domain-set'            # 反推 kind（云端 round-trip 会丢）
        for k in ('模式', '种子', '前缀格式', '名称', '关联底包', '监测周期'):
            if k in spec and spec[k] not in (None, ''):
                t[k] = spec[k]
        if not t.get('名称'):
            t['名称'] = spec.get('名称', t.get('id', ''))
    # 兜底补出：companion 有、登记册无 → 补最小指针。
    # ★ 关键：云端无这些行时（如 select 拒收的历史遗留），绝不能凭空造「待查」空壳——
    #   那会把本地文件里已有的真实健康数据（存活/上次检查/摘要）整片抹掉，
    #   表现为「刚跑完检查，due 又说从没检查过」。故先从本地登记册同名项继承健康字段。
    _local_map = {}
    if os.path.exists(TARGETS):
        try:
            _d = json.load(open(TARGETS, encoding='utf-8'))
            for _t in _d.get('targets', []) or []:
                if isinstance(_t, dict) and _t.get('id'):
                    _local_map[_t['id']] = _t
        except Exception:
            pass
    # ★ S204：云端优先的**新鲜度校准**。云端那行可能是更早的快照
    #   （如手动补写时写入的旧值），而本地随后跑检查产生了更新的结果却还没同步上去。
    #   若一律信云端，就会出现「本地 14:11 的结论被云端 13:59 的旧值盖掉」——
    #   表现为「刚查完，再看又变回上一条结论」。按 上次检查 时间戳取新者。
    for t in targets:
        if not isinstance(t, dict) or t.get('kind') != 'domain-set':
            continue
        old = _local_map.get(t.get('id'))
        if old and (old.get('上次检查') or '') > (t.get('上次检查') or ''):
            for k in ('存活状态', '活跃度', '上次检查', '下次到期', '变更摘要'):
                if old.get(k):
                    t[k] = old[k]
            t['_src'] = '本地较新'
    for _id, spec in domains.items():
        if _id in present:
            continue
        old = _local_map.get(_id) or {}
        targets.append({
            'id': _id, 'kind': 'domain-set',
            '名称': spec.get('名称') or old.get('名称') or _id, '平台': '域名型',
            '监测周期': spec.get('监测周期') or old.get('监测周期') or '每周',
            '关联底包': spec.get('关联底包') or old.get('关联底包', ''),
            # 健康字段一律继承本地已有值，没有才落「待查/空」
            '存活状态': old.get('存活状态') or '待查',
            '活跃度': old.get('活跃度') or '待查',
            '上次检查': old.get('上次检查') or '',
            '下次到期': old.get('下次到期') or '',
            '变更摘要': old.get('变更摘要') or '',
            '模式': spec.get('模式'), '种子': spec.get('种子'),
            '前缀格式': spec.get('前缀格式', ''),
        })
        present.add(_id)
    return targets


def load_targets():
    """三态读监测目标登记册 → (state, targets)。**云端优先**，本地兜底。

    S088：主存储迁云端 DB_UPSTREAM（本地文件在沙箱重置/跨会话会丢）。
    读序：① 云端有记录 → 用它（权威）；② 云端空（真·首期）→ 回退本地（可能是
    尚未同步的历史登记）；③ 云端**不可达** → 回退本地并**显式告警**（不得当空册）。
      · ('ok',   [...])  正常（列表可能为空=尚无登记目标）
      · ('none', [])     两边都不存在（真·未初始化）
      · ('corrupt', [])  两边都损坏/结构不符（**禁当空册**，M-R60）
    """
    cst, crows = _cloud_rows()
    if cst == 'ok' and crows:
        targets = _inject_pub_sources(_inject_domain_sets([_cloud_row_to_target(r) for r in crows]))
        # ★ S200（Q-1029-02 修复）：云端固定 18 列不存扩展字段（kind/seed_urls/
        #   在线状态/source_file），运行时目标丢 kind → _probe_target 误路由到
        #   _probe_repo，接口探针从未调用。按 id 与本地登记册合并，回补云端缺失的
        #   扩展字段（本地结构为权威，仅填缺项，不覆盖云端动态状态）。
        targets = _merge_local_ext(targets)
        return 'ok', targets
    if cst == 'fail':
        print('  ⚠️ 云端监测册不可达（未授权/网络异常）——本次回退本地登记册；'
              '请勿据此判定「无监测目标」')
    # 回退本地（云端为空/未配置/不可达）
    if not os.path.exists(TARGETS):
        return ('ok', []) if cst == 'ok' else ('none', [])
    try:
        d = json.load(open(TARGETS, encoding='utf-8'))
        t = d.get('targets', [])
        if not isinstance(t, list):
            raise ValueError('targets 非数组')
        return 'ok', _inject_pub_sources(t)
    except Exception as e:
        print(f'  ⚠️ 监测登记册损坏无法读取：{os.path.basename(TARGETS)} 解析失败'
              f'（{type(e).__name__}）——请人工修复（勿据此判定「无监测目标」）')
        return 'corrupt', []


def _merge_local_ext(cloud_targets):
    """按 id 与本地登记册（monitor_targets.json）合并，回补云端缺失的扩展字段。

    S200（Q-1029-02 修复）：云端 DB_UPSTREAM 固定 18 列，save 时 skip_cols 跳过
    kind/seed_urls/在线状态/source_file，load 时 _cloud_row_to_target 也不取这些列
    → 运行时 179/186 目标丢 kind，60 个 iface 被 _probe_target 误路由到 _probe_repo，
    接口探针从未调用。本地登记册是扩展字段的单一事实源，此处按 id 回补云端缺项。
    仅回补云端**缺失**的扩展字段，云端动态状态字段（存活/活跃度/上次检查/内容指纹）
    不动，避免覆盖真实监测结论。
    """
    if not os.path.exists(TARGETS):
        return cloud_targets
    try:
        d = json.load(open(TARGETS, encoding='utf-8'))
        local = {str(t.get('id')): t for t in d.get('targets', []) if isinstance(t, dict)}
    except Exception:
        return cloud_targets
    if not local:
        return cloud_targets
    ext_keys = ('kind', 'seed_urls', '在线状态', 'source_file')
    out = []
    for t in cloud_targets:
        tid = str(t.get('id'))
        lt = local.get(tid)
        if lt:
            for k in ext_keys:
                lv = lt.get(k)
                cv = t.get(k)
                if k == 'seed_urls':
                    if (not cv) and lv:
                        t[k] = lv
                elif (cv in (None, '', False)) and lv not in (None, '', False, []):
                    t[k] = lv
        out.append(t)
    return out


def _cloud_row_to_target(r):
    """云端行 → 本地 target dict（字段名对齐；select 列取显示文本）。"""
    def _txt(v):
        if isinstance(v, dict):
            return v.get('text') or v.get('value') or ''
        return v if v is not None else ''
    t = {}
    for k in ('id', '仓库名', '仓库URL', '作者', '平台', '分支', '配置路径',
              '监测周期', '关联底包', '存活状态', '当前commit', '内容指纹',
              '活跃度', '上次检查', '下次到期', '变更摘要'):
        t[k] = _txt(r.get(k))
    for k in ('文件数', '近30天提交'):
        v = r.get(k)
        if isinstance(v, dict):
            v = v.get('number') if 'number' in v else v.get('text')
        try:
            t[k] = int(v) if v not in (None, '') else None
        except Exception:
            t[k] = None
    return t


def _cloud_delete(db_id, id_field, keys):
    """按业务主键从云端**真删除**记录（S143）。

    为什么需要它：`save_targets` 走的是 upsert——只新增/更新，**永不删除**。
    所以 `monitor remove` 若只写回"少了某条的列表"，云端那条会原地不动，
    下次 `load_targets` 云端优先 → 被删的目标"复活"。此处显式调 batch-delete。
    返回 (ok, n)。**失败不得静默**——调用方须提示。
    """
    if not db_id or not keys:
        return False, 0
    try:
        m = _cloud_id_map(db_id, id_field)
        # ★ S156：m 为 None = 映射读失败（**无法判定**，不是空表）。此时绝不能返回
        #   (True, 0) ——那会让"计划删 N 条、实际删 0 条"看起来像"本来就没得删"，
        #   云端残留被静默放过。显式报失败，交人工。
        if m is None:
            print('  ⚠️ record_id 映射读取失败——**无法判定哪些要删**，本轮不做删除'
                  '（本地已删，云端残留需人工处理）')
            return False, 0
        rids = [m[str(k).strip()] for k in keys if str(k).strip() in m]
        if not rids:
            return True, 0
        ok = True
        for i in range(0, len(rids), 100):        # 与写入同口径：单次 1–100 条
            out = api('database/batch_delete_database_records.py',
                      '--database-id', db_id,
                      '--record-ids', json.dumps(rids[i:i + 100]))
            ok = ok and ('error' not in str(out)[:40])
        invalidate(db_id)
        return ok, len(rids)
    except Exception as e:
        print(f'  ⚠️ 云端删除失败（{type(e).__name__}: {e}）——本地已删，云端残留需人工处理')
        return False, 0


def _merge_with_disk(targets):
    """并发保护：与磁盘上的最新登记册合并，只保住「更新」的那份。

    规则（按 id 对齐）：
      · 磁盘有、传入无  → 保留磁盘项（防止本次漏写把它删掉）；
      · 两边都有        → 谁的「上次检查」更晚用谁；都无则用传入（本次是权威）；
      · 传入有、磁盘无  → 用传入（新登记/兜底补出）。
    读磁盘失败 → 直接返回传入列表（**不得因合并失败而拒绝落盘**）。
    """
    if not os.path.exists(TARGETS):
        return targets
    try:
        disk = json.load(open(TARGETS, encoding='utf-8')).get('targets') or []
    except Exception:
        return targets
    disk_map = {t.get('id'): t for t in disk if isinstance(t, dict) and t.get('id')}
    out, seen = [], set()
    for t in targets:
        if not isinstance(t, dict):
            continue
        _id = t.get('id')
        seen.add(_id)
        old = disk_map.get(_id)
        if old is None:
            out.append(t)
            continue
        # 比较新鲜度：上次检查 更晚者为新
        new_ts = t.get('上次检查') or ''
        old_ts = old.get('上次检查') or ''
        out.append(old if old_ts > new_ts else t)
    # 磁盘有、本次没带上 → 保留（避免误删）
    for _id, old in disk_map.items():
        if _id not in seen:
            out.append(old)
    return out


def save_targets(targets):
    """写回登记册。**双写**：云端 DB_UPSTREAM（主）+ 本地 JSON（备份/离线兜底）。

    S088：本地文件在沙箱重置/跨会话时会丢，故迁云端；但**云端不可达不得导致本地也写不成**
    （否则断网即无法登记）。故双写、各自独立上报结果：
    返回 (cloud_ok, local_ok)。调用方须显式提示任一失败（禁静默）。

    ★ S204 并发保护：本地是全量覆盖写。若两个进程几乎同时 check（如并行跑三个目标），
    后写的那个基于**旧快照**，会把另一个刚写进去的健康字段整条覆盖回旧值——
    实测：并行 check 三个域名目标后，只有最后一个的「上次检查/存活」留下，另两个变回空。
    这不是谁写错了，是「全量覆盖 + 读到旧快照」的固有竞态。
    修法：落盘前把磁盘上的最新内容读出来，对**本次未产生新结果**的条目
    （本次 上次检查 早于磁盘上的），用磁盘版本覆盖传入版本——只保住更新的那份，
    不阻止本次真正的新结论落盘。
    """
    # ① 本地（原子写，离线兜底）
    merged = _merge_with_disk(targets)
    d = {'_note': '探源司·上游仓库监测登记册（云端 DB_UPSTREAM 的本地备份）',
         '_schema': _load_schema_doc(), 'targets': merged}
    tmp = TARGETS + '.tmp'
    local_ok = False
    try:
        json.dump(d, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        os.replace(tmp, TARGETS)
        local_ok = True
    except Exception as e:
        print(f'  ⚠️ 本地登记册写入失败（{type(e).__name__}: {e}）')
    # ② 云端（主存储）
    cloud_ok = _cloud_save_targets(targets)
    return cloud_ok, local_ok


def _cloud_rows(db_id=None):
    """读云端某库行 → (state, rows)。

    S095 参数化：`db_id` 缺省取 DB_UPSTREAM（监测册），其余库显式传入。
      · ('ok', [...])      正常
      · ('none', [])       库存在但**尚无记录**（真·首期，可初始化）
      · ('fail', [])       云端不可达/未授权（**不得当空册**，M-R60）
      · ('unset', [])      库未配置 → 退回纯本地模式
    """
    db_id = db_id or DB_UPSTREAM
    if not db_id:
        return 'unset', []
    try:
        # rows_of_t 契约（S083·M-R60）：返回 (True/False, rows) 布尔标志——
        # True=读到有效数据（rows 可能为空表=真·空库），False=读取失败（不得当空库）
        ok, rows = rows_of_t(db_id)
    except Exception:
        return 'fail', []
    if not ok or rows is None:
        return 'fail', []                 # 读失败/结构失配 → 不得折当空册
    return 'ok', rows                     # rows 可为 []（真·空库，可初始化）


def _cloud_id_map(db_id=None, text_field='id'):
    """取云端『业务 id → record_id』映射 → dict。

    S095 参数化：`db_id`/`text_field` 可指定；缺省沿用 DB_UPSTREAM/'id'。
    S088：`get_database_content` **不返回 record_id**（只有业务字段），必须用
    `query_database_record.py` 才拿得到。曾因误用 `rows_of` 取 record_id 而永远
    判为"新增"，一次 check 就插出 4 条重复行（实证：4→1 已清理）。
    失败返回空 dict（调用方退化为"全量新增"，但**不得**因此静默——由 save 的返回值提示）。
    """
    db_id = db_id or DB_UPSTREAM
    m = {}
    try:
        cur, guard = None, 0
        while True:
            cmd = ['python3', f'{LIB}/database/query_database_record.py',
                   '--database-id', db_id, '--page-size', '100']
            if cur:
                cmd += ['--start-cursor', cur]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
            d = json.loads(out)
            for r in d.get('results', []):
                k = str(r.get(text_field) or '').strip()
                if k and r.get('record_id'):
                    m[k] = r['record_id']
            if not d.get('has_more'):
                break
            nc = d.get('next_cursor')
            # ★ S156：游标不前进即退出。S110 记载的坑——参数写错（--cursor 而非
            #   --start-cursor）时服务端不报错、每次返回首页，循环**死转**。
            #   本函数原先只靠 guard>25 兜底，等于"转 25 圈才发现有毛病"；
            #   而 5810 处的同型循环早就有这道闸门。两处口径不同 → 这里补齐（M-R38）。
            if not nc or nc == cur:
                break
            cur = nc
            guard += 1
            if guard > 25:
                break
    except Exception as e:
        # ★ S156：原 `except: return {}` 把"读不到"折叠成"没有已存行"——M-R60 命门。
        #   后果很具体：调用方看到空 dict 就按"全新的表"走 **全量新增**，
        #   而 docstring 自己写着"曾因误用 rows_of 而永远判为新增，一次 check 就插出 4 条重复行"。
        #   同一个坑，另一条路（网络抖动/超时）照样能掉进去。
        #   三态：返回 None 表示**无法判定**，调用方须显式区分"空表"与"读失败"。
        _why = f'{type(e).__name__}: {str(e)[:120]}'
        print(f'  ⚠️ 取 record_id 失败（{_why}）——**无法判定是否已有行**，不得当作空表做全量新增',
              file=sys.stderr)
        return None
    return m


def _cloud_upsert(rows, db_id, id_field, select_cols=(), num_cols=(), skip_cols=(),
                  date_cols=()):
    """通用云端 upsert（S095）：按 `id_field` 定位已有行 → 更新；否则新增。

    参数：
      · rows        —— 待同步的 dict 列表（键即云端字段名）
      · db_id       —— 目标库 id（空 → 直接返回 False，视为未配置）
      · id_field    —— 业务主键字段名（如 'id'/'候选id'）
      · select_cols —— 须按 `{'select': 值}` 包装的列名（S027 铁律）
      · num_cols    —— 须按 `{'number': 值}` 包装的数值列
      · skip_cols   —— 不写入云端的本地专用列（如内部 `_kind`、`score`）
      · date_cols   —— ★S169 须按 `{'date': 'ISO串'}` 包装的**日期列**。

    ★S169 事故（静默丢列，比 S143 更隐蔽）：`上次检查`/`下次到期` 在库里是
    `date` 类型，原实现一律按 `{'text': 值}` 写。平台**不报错**——回执照旧
    `{"results":[{"success":true}]}`，但那两列根本没落库，回读永远是空。
    实证：同一批写「变更摘要(text)+上次检查(date)」，text 成功了、date 静默丢；
    换成 `{'date': '2026-10-19T06:10:17Z'}` 立刻回读可见。
    后果是 `monitor status` 永远「上次检查 —」、`monitor due` 永远「到期」，
    每次巡检都重跑全量——**功能看着在跑，其实是空转**。
    铁律：只信「回执 success」会漏掉丢列；必须靠回读判别（self-check 已设卡）。

    返回 (ok, n_add, n_upd)。**失败不得静默**——由调用方提示。
    """
    if not db_id:
        return False, 0, 0
    by_id = _cloud_id_map(db_id, id_field)
    # ★ S156：m 为 None = 映射读失败。**不许当空 dict** ——否则每一行都会被判成"新增"，
    #   而 docstring 里明写着这个坑造成过"一次 check 插出 4 条重复行"。三态：直接判失败返回。
    if by_id is None:
        print('  ⚠️ record_id 映射读取失败——**无法判定已有行**，本轮 upsert 全部跳过'
              '（避免整表重复插入）')
        return False, 0, 0
    sel, num, skip = set(select_cols), set(num_cols), set(skip_cols)
    dat = set(date_cols)
    to_add, to_upd = [], []
    for t in rows:
        props = {}
        for k, v in t.items():
            if k in skip or v in (None, ''):
                continue
            if k in sel:
                props[k] = {'select': str(v)}      # S027：select 列必须显式包装
            elif k in dat:
                props[k] = {'date': str(v)}        # S169：date 列不是 text
            elif k in num:
                props[k] = {'number': v} if isinstance(v, (int, float)) \
                    else {'text': str(v)}
            else:
                props[k] = {'text': str(v)}
        if not props:
            continue                                # 全空行不写（防污染）
        rid = by_id.get(str(t.get(id_field) or '').strip())
        if rid:
            to_upd.append({'record_id': rid, 'properties': props})
        else:
            to_add.append(props)
    try:
        ok = True
        # S095 修：平台单次写入上限 **1–100 条**（`batch_add/update` 实测报
        # "records 必须包含 1–100 个对象"）——大仓库一次采集可上百条，必须**分批**，
        # 否则整批被拒、云同步全失效（真实数据采集必现）。
        #
        # S143 修：**原判定 `'error' not in out[:40]` 会漏报**——实测写入接口在
        # 字段不存在（如新增「评分依据」列尚未建）时返回
        #   {"error": "records[0] 校验后为空，无有效字段"}
        # 而 batch_add 逐条成功时返回 `{"results":[...]}`。原写法只看前 40 字符，
        # 且**从不校验 results 是否真成功**，于是"整批被拒"也报 ✅（S143 实证：
        # 候选册 50 条的评分推送三天前就失败了，界面一直显示 ✅）。
        # 现改为：显式判定 error 键 + 统计 results 里 success=true 的条数。
        _BATCH = CLOUD_BATCH
        n_ok_add = n_ok_upd = 0
        first_err = ''

        # _count_success 已提为模块级函数（S144：blacklist 等命令要复用）

        for i in range(0, len(to_add), _BATCH):
            chunk = to_add[i:i + _BATCH]
            out = api('database/batch_add_database_records.py',
                      '--database-id', db_id,
                      '--records', json.dumps(chunk, ensure_ascii=False))
            got, err = _count_success(out, len(chunk))
            n_ok_add += got
            if got < len(chunk):
                ok = False
                first_err = first_err or err
        for i in range(0, len(to_upd), _BATCH):
            chunk = to_upd[i:i + _BATCH]
            out = api('database/batch_update_database_records.py',
                      '--database-id', db_id,
                      '--records', json.dumps(chunk, ensure_ascii=False))
            got, err = _count_success(out, len(chunk))
            n_ok_upd += got
            if got < len(chunk):
                ok = False
                first_err = first_err or err
        if not ok:
            print(f'  ⚠️ 云端写入**部分/全部失败**：新增成功 {n_ok_add}/{len(to_add)}、'
                  f'更新成功 {n_ok_upd}/{len(to_upd)}'
                  + (f'；首因：{first_err}' if first_err else ''))
        invalidate(db_id)   # 写后失效缓存（铁律），防后续读命中脏缓存
        return ok, n_ok_add, n_ok_upd
    except Exception as e:
        print(f'  ⚠️ 云端同步失败（{type(e).__name__}: {e}）——本地已存，可稍后重试')
        return False, 0, 0


def _cloud_save_targets(targets):
    """把监测登记册全量同步到云端（upsert：按 id 定位已有行 → 更新；否则新增）。

    返回 bool（True=同步成功）。**失败不得静默**——调用方提示。
    S095：改走通用 `_cloud_upsert`（单一事实源），列类型声明不变。
    ★ S204 扩能：域名型指针项只同步云端有列的字段（结构字段走 companion 本地真源，
    云端无 '种子'/'名称'/'kind' 等列，强写会静默丢/拒收）。仓库型原样同步。
    """
    _COLS = {'id', '仓库名', '仓库URL', '作者', '平台', '分支', '配置路径',
             '监测周期', '关联底包', '存活状态', '当前commit', '内容指纹',
             '活跃度', '上次检查', '下次到期', '变更摘要', '文件数', '近30天提交'}
    rows = []
    for t in (targets or []):
        # ★ S199 修复：发布源型（pub-package）与域名型同源——结构字段（kind/
        #   发布源URL/包类型）走 companion 本地真源，云端无这些列。若不过滤直接
        #   rows.append(t) 上传，非标列会触发云端整条拒收，连 18 列里的健康字段
        #   （存活状态/活跃度/内容指纹/上次检查）也上不去云 → 发布源监测结论无法
        #   持久化。故与 domain-set 同口径：只 sync 18 列字段。
        if isinstance(t, dict) and t.get('kind') in ('domain-set', 'pub-package'):
            row = {k: v for k, v in t.items() if k in _COLS}
            # ★ 云端「平台」列只有 GitHub/Gitee/GitLab/其他 四个 select 选项，
            #   非标值（域名型/发布源/空）会被整条拒收（实测首因 invalid fields: [平台]）。
            #   云端落「其他」，本地登记册仍保留原值供人眼识别（单一事实源在本地）。
            if row.get('平台') not in ('GitHub', 'Gitee', 'GitLab', '其他'):
                row['平台'] = '其他'
            rows.append(row)
        else:
            rows.append(t)
    ok, n_add, n_upd = _cloud_upsert(
        rows, DB_UPSTREAM, 'id',
        select_cols=('平台', '监测周期', '存活状态', '活跃度'),
        num_cols=('文件数', '近30天提交'),
        # ★S169：这两列在库里是 date 类型，按 text 写会**静默丢列**（回执仍 success）。
        date_cols=('上次检查', '下次到期'),
        # ★666（点⑥离线种子基线·tvbox-iface）：扩展中文字段为本地专用元数据，
        #   云端 DB_UPSTREAM 固定 18 列不接受（实测 invalid fields: [名称 kind 在线状态 ...]），
        #   按 L251 模式只推标准列、扩展字段留本地（monitor_targets.json 全量保真）。
        skip_cols=('_schema', '_note', 'score', '名称', 'kind', '在线状态',
                   'seed_urls', 'seed_baseline', 'source_file',
                   'extracted_sha12', 'extracted_at'))
    return ok


def _cloud_sync_search(repos):
    """把搜索候选清单同步到 DB_CANDIDATE（S095）。

    字段映射（本地 dict → 云端列）：
      候选id   = md5(platform|url) 前12位（稳定指纹，跨次重跑同一候选 dedup）
      仓库名   = full_name          平台 = platform（select）
      URL      = url                星标 = stars（number）
      评分     = score（number）    更新时间 = pushed_at
      描述     = desc               来源关键词 = kw（若带）
      状态     = 待评估（select，新候选默认）  采集时间 = now_iso()
    返回 (ok, n_add, n_upd)。**本地已先存**（`_save_search`），此处仅追加云同步。
    """
    if not DB_CANDIDATE:
        return False, 0, 0
    rows = []
    for r in (repos or []):
        if not isinstance(r, dict) or not r.get('url'):
            continue
        rows.append({
            '候选id': _cand_id(r.get('platform', ''), r['url']),
            '仓库名': r.get('full_name', ''),
            '平台': r.get('platform', ''),
            'URL': r['url'],
            '星标': r.get('stars', 0),
            '评分': r.get('score', 0),
            '评分依据': r.get('score_why', ''),
            '更新时间': r.get('pushed_at', ''),
            '描述': r.get('desc', ''),
            '来源关键词': r.get('kw', ''),
            '状态': r.get('status') or '待评估',
            '采集时间': now_iso(),
        })
    return _cloud_upsert(rows, DB_CANDIDATE, '候选id',
                         select_cols=('平台', '状态'),
                         num_cols=('星标', '评分'))


def _cand_id(platform, url):
    """候选稳定指纹：sha1(platform|url) 前12位（M-R60⑯：白名单键须稳定指纹）。

    用 URL 而非序号——同一仓库跨平台/跨次搜索得到同一 id → upsert 幂等，
    不会因重复搜索插出重复行（同 S088 record_id 教训）。
    """
    h = hashlib.sha1(f'{platform}|{url}'.encode('utf-8')).hexdigest()
    return 'sr-' + h[:12]


def _harvest_select_options(field='分组类目'):
    """从云端 schema 读 DB_HARVEST 某 select 列的**合法选项**（S148）。

    为什么必须读而不是硬编码：select 列只接受选项内的值，写别的云端**整条拒收**
    （S148 实测：129 条只进 63 条，66 条因 `py`/`采集` 不在选项里被静默挡掉，
    回执只给一句「invalid fields: [分组类目]」，不看回执就会以为全写进去了）。
    选项是库的属性，库改了代码要跟得上 → 动态读；读不到返回 None（无法判定），
    调用方据此**显式告警**，不得当成"没有限制"。
    """
    try:
        q = os.path.join(SELF_DIR, '..', '..', 'library', 'database',
                         'get_database_schema.py')
        q = os.path.abspath(q)
        if not os.path.exists(q):
            return None
        r = subprocess.run(['python3', q, '--database-id', DB_HARVEST],
                           capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout or '{}')
        d = d.get('data') or d
        for f in (d.get('fields') or d.get('properties') or []):
            if (f.get('name') or f.get('field_name')) == field:
                opts = [o.get('text') for o in
                        ((f.get('config') or {}).get('options') or []) if o.get('text')]
                return opts or None
    except Exception:
        return None
    return None


# 技术形态类目 → 库内内容品类选项 的映射（只在目标值确实存在时才用）
_CAT_ALIAS = {
    'py': '直链', '采集': '直链', 'http': '直链', 'js': '直链',
    'csp': '配置', 'app': 'APP', '4K': '4k', '音乐听书': '音乐', '听书': '听书',
}


def _fit_category(cat, opts):
    """把采集口径的类目映射到库内合法选项。返回 (值, 是否被改)。

    三步：① 本来就在选项里 → 原样  ② 查别名表且目标在选项里 → 用别名
    ③ 都不行 → '其他'（若 '其他' 也不在选项里，退回第一个选项）
    """
    c = str(cat or '').strip()
    if not opts:
        return c or '其他', False
    if c in opts:
        return c, False
    alias = _CAT_ALIAS.get(c)
    if alias and alias in opts:
        return alias, True
    return ('其他' if '其他' in opts else opts[0]), True


def _cloud_sync_harvest(lines):
    """把采集候选清单同步到 DB_HARVEST（S095）。

    字段映射：
      候选id   = sha1(api接口) 前12位（稳定指纹）
      api接口 / 名称 / 分组类目（select）/ 来源仓库
      测活状态 = 未测（select，新候选默认；filter 后可回写）
      入库状态 = 待入库（select）  采集时间 = now_iso()
    返回 (ok, n_add, n_upd)。
    """
    if not DB_HARVEST:
        return False, 0, 0
    # S148：select 列只收选项内的值，写别的整条拒收——先把类目对齐到库内合法值
    _opts = _harvest_select_options('分组类目')
    if _opts is None:
        print('  ⚠️ 取不到 DB_HARVEST「分组类目」选项表——无法预校验，'
              '若云端拒收请看回执（不静默）')
    _remap = Counter()
    rows = []
    for it in (lines or []):
        if not isinstance(it, dict) or not it.get('api接口'):
            continue
        cat, changed = _fit_category(it.get('分组类目', '其他'), _opts)
        if changed:
            _remap[f'{it.get("分组类目", "其他")}→{cat}'] += 1
        rows.append({
            '候选id': _harvest_id(it['api接口']),
            'api接口': it['api接口'],
            '名称': it.get('名称', ''),
            '分组类目': cat,
            '来源仓库': it.get('来源仓库', ''),
            # 点③：live.json 溯源结构范本——四溯源字段透传（缺则留空，不静默丢弃）
            'sourceUrl': it.get('sourceUrl', ''),
            'mirror': it.get('mirror', ''),
            'ua': it.get('ua', ''),
            'size': it.get('size', ''),
            '测活状态': it.get('测活状态') or '未测',
            '入库状态': it.get('入库状态') or '待入库',
            '采集时间': now_iso(),
        })
    if _remap:
        print('  🔧 分组类目已按库内选项对齐：'
              + ' '.join(f'{k}:{v}' for k, v in _remap.most_common()))
    return _cloud_upsert(rows, DB_HARVEST, '候选id',
                         select_cols=('分组类目', '来源仓库', 'sourceUrl', 'mirror',
                                      'ua', 'size', '测活状态', '入库状态'))


def _harvest_id(api):
    """采集候选稳定指纹：sha1(api) 前12位。"""
    h = hashlib.sha1(str(api).encode('utf-8')).hexdigest()
    return 'hv-' + h[:12]


def _load_schema_doc():
    try:
        return json.load(open(TARGETS, encoding='utf-8')).get('_schema', {})
    except Exception:
        return {}


def load_alog():
    """三态读采样史数据基座 → (state, obj)。
      · ('ok', {...})     正常
      · ('none', {...})   文件不存在（真·首期，允许初始化）
      · ('corrupt', {...}) **损坏**——调用方必须拒绝回写覆盖（M-R60）
    """
    if not os.path.exists(ALOG):
        return 'none', {'latest_ts': '', 'repos': {}}
    try:
        d = json.load(open(ALOG, encoding='utf-8'))
        if not isinstance(d.get('repos'), dict):
            raise ValueError('repos 非对象')
        return 'ok', d
    except Exception as e:
        print(f'  ⚠️ 采样史基座损坏：activity_log.json 解析失败（{type(e).__name__}）——'
              f'**拒绝回写**以免销毁历史，请人工修复后重跑')
        return 'corrupt', {'latest_ts': '', 'repos': {}}


def save_alog(obj, allow_init=True):
    """写回采样史。损坏态**拒绝覆盖** → (saved, reason)。"""
    state, _ = load_alog()
    if state == 'corrupt':
        return False, '基座损坏，拒绝回写（防销毁历史，M-R60）'
    if state == 'none' and not allow_init:
        return False, '基座不存在且未允许初始化'
    tmp = ALOG + '.tmp'
    json.dump(obj, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    os.replace(tmp, ALOG)
    return True, 'ok'


# ══════════════════ 纯函数判定器（可被 self-check 合成输入驱动） ══════════════════

def _alive_verdict(code):
    """存活判定（纯函数）→ 三态字符串。
      · '存活'         200/206/3xx
      · '已封禁/删除'  404/403/451（作者跑路/删库/被封嫌疑）
      · '无法判定'     000/超时/限流/其他（**非死**，网络问题）
    """
    if code in ALIVE_CODES:
        return '存活'
    if code in (404, 410):
        return '已封禁/删除'
    if code in (403, 451):
        return '已封禁/删除'
    if code in (0, None):
        return '无法判定'
    if code == 429:              # GitHub 限流：不是"不存在"
        return '无法判定'
    return '无法判定'


def _content_sha(data):
    """内容指纹（纯函数）：接受 bytes/str → sha256 前 12 位。空输入返回 ''。"""
    if data is None:
        return ''
    if isinstance(data, str):
        data = data.encode('utf-8', 'replace')
    return hashlib.sha256(data).hexdigest()[:12]


def _dir_diff(old_dirs, new_dirs):
    """目录差集（纯函数）→ (added, removed)，均排序去重。"""
    o, n = set(old_dirs or []), set(new_dirs or [])
    return sorted(n - o), sorted(o - n)


def _activity_verdict(n_30d, pushed_at_ts, ref_ts=None):
    """活跃度判定（纯函数）→ 三态字符串。

    S088：S087 用真实仓库（lxcjy/IPTV2）验证时暴露阈值口径过糙——该仓库 290 个文件、
    仍在被广泛使用，仅因作者 **248 天**未 push 就被判「归档」（=废弃），会**误导处置**
    （用户看到"归档"可能直接放弃一个仍有效的源）。据实证重划四档：
      · 近30天有提交 或 ≤90 天有 push      → '活跃'
      · 90~365 天有 push                   → '停滞'（休更，仍可用）
      · >365 天无 push                     → '归档'（疑弃更，须复核）
      · 数据全缺失                          → '无法判定'
    **关键原则**：'归档' 是"疑似弃更"的**提示**，不是"判死"——下游不得据此直接停用源，
    须交人工/交叉验证（M-R44：零命中≠缺陷；此处：久未更新≠失效）。
    """
    if n_30d is None and not pushed_at_ts:
        return '无法判定'
    ref = ref_ts or time.time()
    if isinstance(n_30d, int) and n_30d > 0:
        return '活跃'
    if pushed_at_ts:
        try:
            p = float(pushed_at_ts)
            days = (ref - p) / 86400.0
            if days <= 90:
                return '活跃'
            if days <= 365:
                return '停滞'
            return '归档'
        except Exception:
            return '无法判定'
    return '无法判定'


# ── 活跃度预测（S093·roadmap 末项）：趋势外推弃更风险 ──────────────────────
FORECAST_MIN_PTS = 6          # 有效样本下限（低于此不预测——2 期数据外推=编造）
FORECAST_HORIZON_DAYS = 60    # 外推视界（天）


def _forecast_risk(hist, ref_ts=None, min_pts=FORECAST_MIN_PTS):
    """活跃度趋势外推（纯函数）→ 四态结果 dict。

    与 `_activity_verdict`（**瞬时判定**：此刻活跃/停滞/归档）**互补**：
    本函数看**趋势**——"近30天提交数"随时间是在升还是在降，据此外推**弃更风险**。

    三态铁律（M-R60）：
      · `None`（该期未取到）**绝不填充为 0**——填充会把"读不到"说成"零提交"
        （S088 命门同型），必须**跳过**并计入有效点缺失。故有效点 = 非 None 的期数。
      · 有效点 < `min_pts` → **'数据不足'**（明确不预测，而非硬凑一个结论）——
        绝不拿 2 期数据外推（那是"编造"不是"预测"）。
      · 时间跨度不足（有效点集中在极短窗口）→ 同样 '数据不足'（趋势需时间展开）。

    方法：对有效点 (采样时刻, n_commits_30d) 做**最小二乘线性回归**（抗单点噪声，
    优于首尾差）。斜率 < 0 且显著下降 → 风险升高；斜率 ≈ 0 → 稳定；> 0 → 增长。

    返回 dict：
      {state: 'ok'|'数据不足'|'无法判定', risk: '升高'|'稳定'|'增长'|None,
       slope: float|None（每期提交数变化）, n_pts: 有效点数, horizon: 视界天数,
       detail: str（人话说明）}
    """
    ref = ref_ts or time.time()
    # 收集有效点：None 跳过（不填充 0），并解析每期的采样时刻
    pts = []
    for s in (hist or []):
        v = s.get('n_commits_30d')
        if not isinstance(v, int):
            continue                       # 该期未取到 → 跳过（不折叠成 0）
        t = _parse_ts(s.get('ts'))
        if t is None:
            continue                       # 时刻不可解析 → 跳过（不采用无坐标点）
        pts.append((t, v))
    n_pts = len(pts)
    if n_pts < min_pts:
        return {'state': '数据不足', 'risk': None, 'slope': None, 'n_pts': n_pts,
                'horizon': FORECAST_HORIZON_DAYS,
                'detail': f'有效采样 {n_pts} 期 < 下限 {min_pts} 期——不足以外推趋势'
                          f'（需持续监测积累历史；缺的期多为未取到，非零提交）'}
    # 时间跨度检查：有效点窗口至少 ≥ 7 天，否则趋势无意义（如分钟级重复采样）
    span_days = (max(t for t, _ in pts) - min(t for t, _ in pts)) / 86400.0
    if span_days < 7:
        return {'state': '数据不足', 'risk': None, 'slope': None, 'n_pts': n_pts,
                'horizon': FORECAST_HORIZON_DAYS,
                'detail': f'有效采样跨度仅 {span_days:.1f} 天 < 7 天——窗口太短，'
                          f'趋势不可判（需按监测周期持续积累）'}
    # 最小二乘线性回归：以"天"为自变量（便于斜率解释），n_commits_30d 为因变量
    xs = [(t - min(tt for tt, _ in pts)) / 86400.0 for (t, _) in pts]
    ys = [v for (_, v) in pts]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return {'state': '无法判定', 'risk': None, 'slope': None, 'n_pts': n_pts,
                'horizon': FORECAST_HORIZON_DAYS,
                'detail': '采样时刻全同（无时间展开）——无法回归'}
    slope_per_day = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    # 外推视界末的预测值（用于风险分级）
    pred = my + slope_per_day * FORECAST_HORIZON_DAYS
    # 风险分级：以"视界内预测提交数相对当前均值的变化"为准（相对量，抗量纲）
    if my > 0:
        rel = (pred - my) / my
    else:
        rel = 1.0 if slope_per_day > 0 else (0.0 if slope_per_day == 0 else -1.0)
    if slope_per_day < 0 and (pred <= 0 or rel <= -0.5):
        risk = '升高'
    elif slope_per_day > 0 and rel >= 0.5:
        risk = '增长'
    else:
        risk = '稳定'
    return {'state': 'ok', 'risk': risk, 'slope': round(slope_per_day, 4),
            'n_pts': n_pts, 'horizon': FORECAST_HORIZON_DAYS,
            'detail': f'基于 {n_pts} 期有效采样（跨度 {span_days:.1f} 天）：'
                      f'提交趋势 {"↓" if slope_per_day < 0 else ("↑" if slope_per_day > 0 else "→")}'
                      f'{abs(slope_per_day):.3f}/天，外推 {FORECAST_HORIZON_DAYS} 天'
                      f'预计 {pred:.1f} 次/月 → 弃更风险 **{risk}**'}




def _http_code(url, timeout=10):
    """探测 URL 的 HTTP 状态码。失败返回 0（调用方按 _alive_verdict 判"无法判定"）。"""
    rc, out = _sh([CURL, '-sS', '-o', '/dev/null', '-w', '%{http_code}',
                   '-m', str(timeout), '-A', UA, url], timeout=timeout + 4)
    if rc != 0 or not out.strip():
        return 0
    try:
        return int(out.strip().splitlines()[-1])
    except Exception:
        return 0


_RAW_SLOW = {'hit': False}      # S148：raw 直连是否已实测到慢（慢则后续优先走镜像）
# ★ S163：整进程**一次性**判定直连可用性（不是每条都试）。
#   实测（本网络）：raw.githubusercontent.com 直连 100% 20s 超时（0 字节），
#   镜像 gh-proxy 0.3–0.5s 就拿到真文件。而原逻辑只在"直连成功但 >8s"时置 _RAW_SLOW，
#   直连**直接超时**永远不置 → 每个文件白等 (20s×2 重试 + 1s sleep) + 镜像两次 10s，
#   单文件最坏 61s。monitor check 6 仓库 44.8s、scan 一仓库 18.5s 全是这么耗掉的。
#   现改为：先做一次 3s 探测，不通就把镜像排到最前（仍**保底尝试直连**，只是排最后）。
_RAW_REACH = {'probed': False}


def _raw_direct_ok(timeout=3):
    """一次性探测 raw 直连是否可用（3s 够短，不通就别再等）。三态：True/False。"""
    if not _RAW_REACH['probed']:
        rc, _ = _sh([CURL, '-sS', '-o', '/dev/null', '-m', str(timeout),
                     '-A', UA, 'https://raw.githubusercontent.com/robots.txt'],
                    timeout=timeout + 3)
        _RAW_REACH['probed'] = True
        _RAW_REACH['ok'] = (rc == 0)
    return _RAW_REACH.get('ok', False)


def _fetch(url, timeout=15):
    """拉取 URL 文本 → (state, text)。state ∈ {'ok','fail','notfound'}。

    S088·M-R60：**HTTP 错误页不得折叠成"拉取成功"**。S087 用真实仓库验证时暴露：
    `_sh` 只判 curl 返回码，而 `raw.githubusercontent.com` 对不存在的文件返回
    HTTP 404 + 正文 `"404: Not Found"`（14 字节），curl 退出码仍是 0 →
    被当作 `('ok', '404: Not Found')`。后果链：① 用错误页算出一个**看似合法的指纹**；
    ② 真实存在的配置文件因先命中假候选而**永不被检查**；③ 监测结论完全失真
    （"装成功的哑巴"）。故必须显式取 HTTP 状态码，并把 404/410 单列为 'notfound'
    （**不存在 ≠ 拉取失败**——前者是"该路径无此文件"，后者是"网络/服务问题"，
    语义不可折叠）。
    """
    # S148：raw.githubusercontent 在本网络**极慢且不稳定**——同一 16KB 文件多次实测
    # 29s / 34s / 38s，偶尔直接超时。原 `-m 15` 必然失败 → 存在的文件被判"拉取失败"，
    # 于是同仓库一次采 129 条、一次采 55 条（**结果不可复现**，比错更难查）。
    # 实测镜像：gh-proxy.com 1.2s、ghfast.top 2.6s、直连 34s → 慢时走镜像。
    # 顺序：先直连（拿最新），失败/超时再走镜像；**notfound 不重试**（文件真不存在）。
    _urls = [url]
    if 'raw.githubusercontent.com' in url:
        _mirrors = [f'https://gh-proxy.com/{url}', f'https://ghfast.top/{url}']
        # 三条判据排出顺序：① 本进程已实测慢过 ② 一次性探测直连不通 ③ 否则直连优先
        if _RAW_SLOW['hit'] or not _raw_direct_ok():
            _urls = _mirrors + [url]      # 镜像优先，直连保底（不放弃"能直连"的可能）
        else:
            _urls = _urls + _mirrors
    _base = max(timeout, 20)
    rc, out = 1, ''
    # ★ S167 修：原写法对**任何**直连 URL 都问 `_raw_direct_ok()` 来定超时档，
    #   而那个函数探的是 raw.githubusercontent 通不通——
    #   于是拉 127.0.0.1 的探针也会去真连一次 GitHub，把"raw 不通"缓存给整个进程。
    #   实测后果：第 21 个探针（打本地端口）顺手把 _RAW_REACH 探成 False，
    #   第 23 个探针（慢标记）随即拿镜像优先、直连压根不跑 → 慢标记恒 False → self-check 一半失败。
    #   正确语义：这个"直连不通就缩短超时"的优化**只对 raw 域名有意义**，
    #   别的 host 一律用 _base（本地/内网本来就不该被 GitHub 的可达性影响）。
    _is_raw = 'raw.githubusercontent.com' in url
    for _u in _urls:
        _is_direct = (_u == url)
        # 直连给足超时；镜像只给短超时（实测镜像 1–3s，慢就是它挂了，立刻换下一个）
        # ★ S163：直连已知不通时（排在最后），别再用 20s——那是纯浪费。给 8s 保底探测。
        if _is_direct:
            _t0 = _base if (not _is_raw or _raw_direct_ok()) else min(_base, 8)
        else:
            _t0 = 10
        _t_start = time.time()
        _got = False
        for _try in range(2):
            if _try:
                time.sleep(1)
            # C1：底层取数统一走 core.http_get（保留镜像重试/慢标记/状态码语义）
            st, _pl, _ = http_get(_u, timeout=_t0, ua=UA, return_status=True)
            if st == 'ok' and _pl:
                _code, _body = _pl            # _pl = (http_status, text)
                out = f'{_body}\n{_code}'     # 维持 rpartition('\n') 解析契约
                rc = 0
                _got = True
                break
        if _got:
            # 直连成功但耗时超过 8s → 记下"慢"，后续优先走镜像
            if _u == url and time.time() - _t_start > 8:
                _RAW_SLOW['hit'] = True
            break
    if rc != 0 or not out:
        return 'fail', ''
    # 末行为 HTTP 状态码（-w 注入）；正文为主体
    body, _, tail = out.rpartition('\n')
    code = tail.strip()
    if not code.isdigit():
        # 无法取到状态码（异常输出）→ 保守判失败，不冒险当成功
        return 'fail', ''
    code = int(code)
    if code in (404, 410):
        return 'notfound', ''
    if code >= 400:
        return 'fail', ''
    # 二次防御：GitHub raw 偶发在 200 下夹带错误正文（代理/CDN 行为），显式拦截
    if body.strip() in ('404: Not Found', 'Not Found'):
        return 'notfound', ''
    return 'ok', body


def _gh_api(path, timeout=15):
    """调 GitHub API → (state, obj)。state ∈ {'ok','fail','ratelimit'}。

    S089：**自动携带 token**（多源解析，见 `_load_token`）——有 token 限额升至
    5000 req/h（原匿名 60/h），近30天提交/目录树才真正可用。token 缺失**不报错**，
    静默降级为匿名，只是受限。
    403/429 → 'ratelimit'（**不是**"不存在"，M-R57 三态：无法判定 ≠ 不存在）。

    S091：**收口为 `_api_get` 的 GitHub 封装**（M-R38 单一事实源）——限流识别逻辑
    只保留一份，避免多平台各自实现导致判定分叉。
    """
    return _api_get('https://api.github.com', path, timeout=timeout,
                    headers=_gh_headers())


def _parse_repo(url):
    """从仓库 URL 解析 (owner, repo, platform, host)。失败返回 (None, None, '其他', '')。

    S091 扩展：新增 host 返回（Gitea 系需 host 拼 API 基址）与 **Gitea/工蜂识别**：
      · github.com → GitHub；gitee.com → Gitee；gitlab.com → GitLab
      · **其他 host**（自建 Gitea / 腾讯工蜂 git.code.tencent.com 等）→ 'Gitea' 族
        （路径同构，走 `/api/v1/repos/...` 兼容接口）
    """
    try:
        u = urlparse(url)
        host = (u.netloc or '').lower()
        host = re.sub(r'^www\.', '', host)
        host = host.split(':')[0]              # 去端口
        parts = [p for p in u.path.split('/') if p]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            repo = re.sub(r'\.git$', '', repo)
            if 'github' in host:
                return owner, repo, 'GitHub', host
            if 'gitee' in host:
                return owner, repo, 'Gitee', host
            if 'gitlab' in host:
                return owner, repo, 'GitLab', host
            # 极狐 GitLab（jihulab.com）：host 里不含 "gitlab" 字样，但同样是 GitLab 系，
            # 路径同构 /-/raw/。S198 实测：漏判会落到 Gitea 兜底，导致云端「平台」枚举
            # 拒收（invalid fields [平台]），玄珠仓库两次登记失败。
            if 'jihulab' in host:
                return owner, repo, 'GitLab', host
            # 自建 Gitea / 工蜂 / 其他 Git 服务：按 Gitea 兼容接口尝试（host 驱动）
            return owner, repo, 'Gitea', host
    except Exception:
        pass
    return None, None, '其他', ''


def _find_config_path(owner, repo, branch, platform='GitHub', host=''):
    """探测仓库内主配置文件路径 → (raw_base, 候选列表)。

    S091：raw 基址按平台归一（GitHub raw.githubusercontent / Gitee /raw/ /
    GitLab /-/raw/ / Gitea 自建 host），消除平台硬编码散落。
    """
    if platform == 'Gitee':
        raw = PLATFORM_RAW['Gitee'].format(owner=owner, repo=repo, branch=branch)
    elif platform == 'GitLab':
        raw = PLATFORM_RAW['GitLab'].format(owner=owner, repo=repo, branch=branch)
    elif platform == 'Gitea' and host:
        raw = f'https://{host}/{owner}/{repo}/raw/branch/{branch}/'
    else:
        raw = PLATFORM_RAW['GitHub'].format(owner=owner, repo=repo, branch=branch)
    return raw, ['config.json', 'tvbox.json', 'box.json', 'api.json']


def _discover_configs(owner, repo, branch, platform='GitHub', host='', limit=60):
    """遍历仓库目录树 → 可能的 TVBox 配置路径（S148）。

    **为什么必须补这一步**：`_find_config_path` 只猜 4 个固定文件名
    （config/tvbox/box/api.json）。但真实仓库的主配置常叫 `aa.json` / `fun.json` /
    `js/box.json`——S148 实测 `cluntop/tvbox` 有 **231 个 json**，4 个猜名一个都不中，
    于是报「未找到可解析的线路文件」。这是**采集器只认约定名**，不是仓库没料。

    判据：**猜不到 ≠ 没有**。猜不到就翻目录树，不是直接宣布没料。

    返回按「像主配置的程度」排序的路径列表（不含 _find_config_path 已有的 4 个猜名）：
      ① 根目录的 .json 优先（主配置几乎都在根）
      ② 其次一级子目录的 .json
      ③ 跳过明显非配置的（package.json / test.json / .github/ / node_modules / lock）
      ④ 数量上限 limit —— **这是安全阀，不是"只看这些就够了"**。
         原默认 12 是**错的**：S148 实测 cluntop/tvbox 共 231 个 json，
         只试前 12 个就宣布"取最多"，等于用不完整样本下完整结论。
         现默认 60，并且**把总数一并返回**，调用方必须能看见被截断这件事（M-R60）。
    """
    import urllib.request
    import urllib.parse
    api = None
    if platform == 'Gitee':
        api = f'https://gitee.com/api/v5/repos/{owner}/{repo}/git/trees/{branch}?recursive=1'
    elif platform == 'GitLab':
        api = (f'https://gitlab.com/api/v4/projects/'
               f'{urllib.parse.quote(owner + "/" + repo, safe="")}'
               f'/repository/tree?recursive=true&per_page=100&ref={branch}')
    elif platform == 'Gitea' and host:
        api = f'https://{host}/api/v1/repos/{owner}/{repo}/git/trees/{branch}?recursive=true'
    else:
        api = (f'https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}'
               f'?recursive=1')
    try:
        # C1b：底层取数统一走 core.http_get（保留 token/重试/限流分类语义）
        _hdr = {'Accept': 'application/vnd.github+json'}
        # `_load_token()` 返回 (state, token, source) 三元组，**不是字符串**——
        # 直接当字符串拼进 Authorization 会得到 401（S148 踩过一次）。
        _st, _tk, _ = _load_token()
        if _st == 'ok' and _tk and platform in ('GitHub', ''):
            _hdr['Authorization'] = f'Bearer {_tk}'
        # S148：匿名限流 60 次/h，连跑两条命令就可能撞上 403/429。
        # 一次失败不等于"仓库不可采"——重试 3 次（退避 1/3/7s）。
        # 仍失败时**把限流与网络故障分开报**：限流是"稍后再试"，网络是"可能真没有"。
        last = None
        for _attempt, _wait in enumerate((0, 1, 3)):
            if _wait:
                time.sleep(_wait)
            _st2, _pl, _rs = http_get(api, timeout=25, ua=UA, headers=_hdr)
            if _st2 == 'ok':
                d = json.loads(_pl)
                tree = d.get('tree') or d.get('items') or []
                last = None
                break
            _code = None
            if _rs and _rs.startswith('http_'):
                try:
                    _code = int(_rs[5:])
                except Exception:
                    _code = None
            _e = RuntimeError('HTTP %s' % _rs)
            _e.code = _code
            last = _e
            if _code not in (403, 429, 500, 502, 503):
                break                      # 4xx 除限流外，重试无意义
        if last is not None:
            raise last
    except Exception as e:
        # M-R60：取不到目录树 = 无法判定，返回 None（与"树是空的"严格区分）
        _code = getattr(e, 'code', None)
        if _code in (403, 429):
            print(f'   ⚠️ 目录树取不到：GitHub **限流**（HTTP {_code}，匿名 60 次/h）'
                  f'——稍后重试或配 token；**不代表仓库没有配置**')
        else:
            print(f'   ⚠️ 目录树取不到（{type(e).__name__}'
                  f'{f" HTTP {_code}" if _code else ""}）——无法用遍历兜底')
        return None
    skip_dir = ('.github/', 'node_modules/', '.git/', 'dist/', 'build/',
                'test/', 'tests/', '.vscode/')
    skip_name = ('package.json', 'package-lock.json', 'tsconfig.json',
                 'composer.json', 'manifest.json', 'requirements.json')
    root_lv1 = []
    for t in tree:
        p = str(t.get('path') or '')
        if not p.lower().endswith('.json'):
            continue
        if any(p.startswith(s) or f'/{s}' in p for s in skip_dir):
            continue
        if os.path.basename(p).lower() in skip_name:
            continue
        depth = p.count('/')
        (root_lv1.append if depth <= 1 else (lambda x: None))(p)
    # 按「层级浅 → 名字短」排序：主配置通常又浅又短
    # ★ S148 补：但**"名字短"与"线路多"毫无关系**——实测 `aa.json`(55) 靠名字短排在前，
    #   而 `fun.json`(127) / `jsm.json`(114) 靠后。所以这里只做**粗排**（决定谁先被试），
    #   真正的"取最全"由 cmd_harvest_scan 拉下来数条数决定（见 _best_of）。
    root_lv1.sort(key=lambda x: (x.count('/'), len(x), x))
    _all = [p for p in root_lv1 if p not in ('config.json', 'tvbox.json',
                                             'box.json', 'api.json')]
    # ★ S148：把"共发现多少"一并交出去。只返回 [:limit] 会让调用方误以为"就这些"——
    #   实测 cluntop/tvbox 共 **231 个 json**，前 12 名里最全的是 fun.json(127)，
    #   但真正的"最全"可能排在 12 名之后。调用方必须能看见被截断这件事（M-R60）。
    return _all[:limit], len(_all)


# ══════════════════ 模块 A：monitor（上游仓库监测） ══════════════════

def _api_get(base, path, timeout=30, headers=None):
    """通用 API GET → (state, obj)。state ∈ {'ok','fail','ratelimit','notfound','unauthorized'}。

    ★ S190（实测）：`timeout=15` 对**提交记录接口**不够。
      songlees355-wq/okay 的 `/commits?per_page=100` 返回 439KB，实测耗时 **19.1s**
      → 被判 fail → 界面写"近30天提交=未取到"。而换个字段少的仓库就是 1-2s。
      也就是说：**同样是"未取到"，有的是限流、有的是超时**，处置动作完全不同
      （等配额 vs 调超时），折叠成一句话就等于没告诉人任何东西。
      这里把默认提到 30s，并在 `_why_of` 里把 fail 明确点名"超时/网络中断"。
      不设更大值：单仓 30s × 6 仓并发已是可接受上限，再大就该走异步了。

    S091：多平台统一取数入口（原仅 GitHub 有 `_gh_api`）。三态不折叠：
      · 网络失败/非 JSON → 'fail'
      · 限流（403/429 或正文含 rate limit）→ 'ratelimit'（**不是**"不存在"）
      · 404 → 'notfound'
      · **401/鉴权失败 → 'unauthorized'**（S094 新增）

    S094·M-R60：**必须取真实 HTTP 状态码**——此前只看正文，导致 GitLab 搜索
    `{"message":"401 Unauthorized"}` 被当成 'ok'（**错误响应被当成功**），
    下游拿到 `{}` 后静默产出"0 结果"或崩溃。401 与"真·空结果"**语义不可折叠**。
    """
    url = f'{base}{path}'
    _hs = list(headers or [])
    rc, out = _sh([CURL, '-sS', '-L', '-m', str(timeout), '-A', UA,
                   '-w', '\n%{http_code}'] + _hs + [url], timeout=timeout + 5)
    if rc != 0 or not out:
        return 'fail', None
    body, _, tail = out.rpartition('\n')
    try:
        code = int(tail.strip())
    except Exception:
        code = 0                       # 取不到状态码 → 走语义兜底判定
    # 401/403 鉴权失败（不是限流、更不是"不存在"）
    if code == 401:
        return 'unauthorized', None
    # 限流优先识别（HTTP 码藏在正文/头部；GitLab 用 {"message":"429 Too Many Requests"}；
    # Gitee/GitHub 用 message 含 rate limit）——**先判语义，再判 JSON**
    low = body[:600].lower()
    if code == 429 or 'rate limit' in low or 'too many requests' in low or '"429"' in low:
        return 'ratelimit', None
    try:
        d = json.loads(body)
    except Exception:
        return 'fail', None
    if isinstance(d, dict):
        msg = str(d.get('message', '')).lower()
        if 'rate limit' in msg or 'too many requests' in msg:
            return 'ratelimit', d
        # 语义兜底：某些平台 401/403 由正文承载（取不到状态码时）
        if code in (401, 403) or 'unauthorized' in msg or 'invalid token' in msg:
            return 'unauthorized', d
        if d.get('error') and 'not found' in str(d.get('error')).lower():
            return 'notfound', d
    if code == 404:
        return 'notfound', d if isinstance(d, dict) else None
    if code >= 400:
        return 'fail', d if isinstance(d, dict) else None
    return 'ok', d


def _undefined_names(fn):
    """静态查：fn 体内『读了但没绑定』的名字（排除局部绑定 / 模块全局 / 内建）。

    ★ S194 为什么要有这个函数：2026-10-21 `monitor add` 五个新目标全部失败，
      报 `NameError: name 'rec' is not defined`（修完又冒出 `_cfg_try`）。
      病根是改动时把别处的变量名抄进了 `_platform_meta`——
      **语法合法、编译通过、lint 不报**，只有真跑到那条分支才炸，
      而存量目标从不需要再探测默认分支，于是洞一直开着没人看见。

    用法：self-check ⑰d 探针用它查真身 + 查一个故意写坏的样本（反向验证）。
    ⚠️ 局限（诚实边界）：只看**单个函数体**，不解析闭包外层作用域——
      故被查函数最好是模块级函数；查嵌套函数会把外层局部变量误报成未定义。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    fnode = tree.body[0]
    bound = {a.arg for a in fnode.args.args} | {a.arg for a in fnode.args.kwonlyargs}
    if fnode.args.vararg:
        bound.add(fnode.args.vararg.arg)
    if fnode.args.kwarg:
        bound.add(fnode.args.kwarg.arg)
    for n in ast.walk(fnode):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                bound.add((al.asname or al.name).split('.')[0])
        elif isinstance(n, ast.Global):
            bound.update(n.names)
        elif isinstance(n, ast.Nonlocal):
            bound.update(n.names)
    g = fn.__globals__
    used = {n.id for n in ast.walk(fnode)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(x for x in used
                  if x not in bound and x not in g and not hasattr(builtins, x))


def _platform_meta(platform, owner, repo, branch, host=''):
    """取平台侧仓库元信息 → 统一结构 dict（含每项三态）。

    返回：{'state', 'pushed_at', 'n_commits_30d', 'dirs', 'n_files',
           'default_branch', 'note'}
      · state ∈ {'ok','fail','ratelimit','unsupported'}——**unsupported 不假装成功**
      · 单项取不到 → None（活跃度侧走'无法判定'），**绝不折叠成 0/空**（M-R60）

    平台差异（S091 各适配）：
      · GitHub：/repos/o/r → /repos/o/r/commits?sha= → /repos/o/r/git/trees/{b}?recursive=1
      · GitLab：/projects/{o%2Fr} → /repository/commits?ref_name= → /repository/tree?recursive=true
      · Gitee ：/repos/o/r → /repos/o/r/commits?sha= → （无树 API，靠 raw 指纹 + 主页）
      · Gitea ：/repos/o/r → /repos/o/r/commits?sha= → /repos/o/r/git/trees/{b}?recursive=true
    """
    out = {'state': 'ok', 'pushed_at': None, 'n_commits_30d': None,
           'dirs': [], 'n_files': None, 'default_branch': None, 'note': ''}
    base = _platform_base(platform, host)
    if not base or platform == '其他':
        out['state'] = 'unsupported'
        out['note'] = f'平台 {platform} 暂不支持 API 监测（仅可测活）'
        return out

    # ★ S189e 真因（配了 token 仍然全是匿名 60/h）：
    #
    #   `_api_get(base, path, timeout, headers=None)` —— headers **默认空**。
    #   S091 把「GitHub 专用 _gh_api」重构成「多平台统一 _api_get」时，
    #   新写的本函数 4 处调用**全部没有传 headers**，于是：
    #     · 老路径 `_gh_api` 仍传 `headers=_gh_headers()` → 带 token（没人调它了）
    #     · 新路径 `_platform_meta` 不传              → **永远匿名**
    #   实测（2026-10-20）：token 配好、`rate_limit` 报 5000/5000、
    #   `tvbox probe monitor check --all` 仍只有 1/6 取到数据，
    #   note 却写着「未鉴权 60/h」——因为请求根本没带 Authorization。
    #
    #   不是"配额不够"，是**配额没用上**。这类 bug 特别难查：
    #   配置动作(✅)与使用动作(❌)各自看着都正常，只有把两者连起来看才发现断点。
    #   收口修法：整个函数只构造**一次** headers，下面 4 处全部复用（M-R38 单一事实源），
    #   不许再出现"某处忘了传"。
    _hs = _gh_headers()

    # ① 仓库元信息（pushed_at / default_branch）
    if platform == 'GitLab':
        proj = f'{owner}%2F{repo}'
        st, d = _api_get(base, f'/projects/{proj}', headers=_hs)
    else:
        st, d = _api_get(base, f'/repos/{owner}/{repo}', headers=_hs)
    if st == 'ratelimit':
        out['state'] = 'ratelimit'
        out['note'] = f'{platform} API 限流——无法判定'
        return out
    if st != 'ok' or not isinstance(d, dict):
        out['state'] = st if st in ('notfound',) else 'fail'
        out['note'] = f'{platform} 元信息获取失败（{st}）'
        return out

    # 各平台 pushed_at 字段名不一（GitHub/Gitee: pushed_at；GitLab: last_activity_at/pushed_at）
    for k in ('pushed_at', 'last_activity_at', 'updated_at'):
        if d.get(k):
            out['pushed_at'] = _parse_ts(d[k])
            if out['pushed_at']:
                break
    out['default_branch'] = d.get('default_branch') or d.get('default_branch_name')

    # ② 目录树（GitHub/GitLab/Gitea 有；Gitee 无标准树 API → 明确标注不支持，非"空"）
    if platform == 'GitLab':
        proj = f'{owner}%2F{repo}'
        st3, tree = _api_get(base, f'/projects/{proj}/repository/tree'
                                   f'?recursive=true&per_page=100', headers=_hs)
    elif platform == 'Gitee':
        # Gitee 无递归树 API：用 /git/trees/{branch}?recursive=1（v5 支持有限）尝试
        st3, tree = _api_get(base, f'/repos/{owner}/{repo}/git/trees/{branch}?recursive=1',
                             headers=_hs)
    else:
        st3, tree = _api_get(base, f'/repos/{owner}/{repo}/git/trees/{branch}?recursive=1',
                             headers=_hs)
    if st3 == 'ok' and isinstance(tree, list):
        # GitLab tree 返回 list（每项含 path）；GitHub/Gitea 返回 {'tree':[...]}
        paths = [x.get('path', '') for x in tree if isinstance(x, dict)]
        if paths:
            out['dirs'] = sorted(p for p in paths if p)
            out['n_files'] = len(out['dirs'])
    elif st3 == 'ok' and isinstance(tree, dict):
        paths = [x.get('path', '') for x in tree.get('tree', []) if isinstance(x, dict)]
        if out['dirs'] or paths:
            out['dirs'] = sorted(p for p in paths if p)
            out['n_files'] = len(out['dirs'])

    # ③ 近 30 天提交（GitLab 用 ref_name，其余用 sha）
    if platform == 'GitLab':
        proj = f'{owner}%2F{repo}'
        st2, commits = _api_get(base, f'/projects/{proj}/repository/commits'
                                      f'?per_page=100&ref_name={branch}', headers=_hs)
    elif platform == 'Gitee':
        st2, commits = _api_get(base, f'/repos/{owner}/{repo}/commits'
                                      f'?per_page=100&sha={branch}', headers=_hs)
    else:
        st2, commits = _api_get(base, f'/repos/{owner}/{repo}/commits'
                                      f'?per_page=100&sha={branch}', headers=_hs)
    if st2 == 'ok' and isinstance(commits, list):
        cutoff = time.time() - 30 * 86400
        n = 0
        for c in commits:
            if not isinstance(c, dict):
                continue
            # 各平台提交时间字段：GitHub/Gitee 嵌套 commit.author.date；GitLab 顶层 created_at
            dt = ''
            if isinstance(c.get('commit'), dict):
                dt = ((c['commit'].get('author') or {}).get('date')
                      or c['commit'].get('committer', {}).get('date') or '')
            if not dt:
                dt = c.get('created_at') or c.get('committed_date') or ''
            ts = _parse_ts(dt)
            if ts is not None and ts >= cutoff:
                n += 1
        out['n_commits_30d'] = n
    # ★ S190（用户 2026-10-20 23:14 质问「不是你一直敲代码，为啥说技能在工作了」后加）
    #
    #   问题不是"取不到"，是**取不到的时候没人说为什么**。
    #   实测这一晚：`monitor check` 报「近30天提交=未取到」，我为了查原因手写了
    #   **9 个临时脚本**（打 API、量耗时、抓返回码…）。而脚本本身一个字没提原因。
    #   这不是"技能在工作"，这是**技能把活推回给人**，还装成已经测过了。
    #
    #   修法：把「为什么没取到」变成返回值的一部分（`why`），下游直接打印。
    #   判据：每个 None 字段必须带一条 why；有值就不写 why（省得满屏噪声）。
    #   三态口径不变（M-R60）：fail ≠ ratelimit ≠ notfound ≠ timeout，不许并成一句"失败了"。
    out['why'] = {}
    out['why_state'] = {}          # ★ 机器判据（别拿中文文案当判据，见下）
    if out['n_files'] is None:
        out['why']['n_files'] = _why_of(st3, '目录树接口')
        out['why_state']['n_files'] = st3
    if out['n_commits_30d'] is None:
        out['why']['n_commits_30d'] = _why_of(st2, '提交记录接口')
        out['why_state']['n_commits_30d'] = st2
    # ★ S194 修真缺陷（两处未定义变量 + 一段误植）：
    #   原此处写 `rec` 与 `_cfg_try`——本函数两个都没有（返回字典叫 out，
    #   而 `_cfg_try` 是 `_repo_probe` 的局部变量，1604 行）。后果：任何走
    #   「探测默认分支」路径的 `monitor add` 必抛 NameError，5 个新目标全登记失败；
    #   存量目标掩盖了这个洞（它们从不需要再探测分支）。
    #   且 **content_sha 不属于本函数职责**——指纹由 `_repo_probe` 取，
    #   它已在 1612-1614 行自己写了「未取到」的原因（写进 note）。此处复写属越权。
    #   本函数该管的 None 字段是 pushed_at / n_files / n_commits_30d，
    #   后两者已有 why，故这里补 pushed_at（S190「每个 None 必须带 why」）。
    if out['pushed_at'] is None:
        out['why']['pushed_at'] = ('仓库元信息接口已通，但响应里无 pushed_at/'
                                   'last_activity_at/updated_at（字段缺失，非失败）')
        out['why_state']['pushed_at'] = 'missing'
    # ★ S190b（反向验证踩出来的真坑，必须记下来）：
    #   自诊断的条件原本写成 `any('限流' in msg for msg in why.values())` ——
    #   **拿中文文案当判据**。而 `_why_of` 的 ratelimit 文案是
    #   「被限流（配额耗尽…）」，里面没有"限流"两字中的？有的——但换个措辞就断了，
    #   而且我改文案时根本不会想到有个 if 在匹配它。反向验证实测：该报警**一次没触发**。
    #   这正是本项目反复踩的同一个坑：**判据与被判对象耦合在自然语言上**。
    #   铁律：状态一律用 state 码判，文案只给人看。
    return out


def _why_of(state, what):
    """把一次 API 调用的三态 state 翻译成「人一眼能懂、且能照着修」的原因。

    ★ 这一层存在的理由：`state='fail'` 对上层毫无信息量——同样是 fail，
      「限流」要等、「超时」要调参、「404」要改登记册、「401」要换 token，
      **四个完全不同的处置动作被同一个词折叠了**（M-R60 禁的正是这种折叠）。
    """
    return {
        'ratelimit': f'{what}被限流（配额耗尽，等重置或配 token）',
        # 超时与网络抖动在本项目里是**最常见**的 fail，必须单独点名：
        # 实测 songlees355-wq/okay 的 commits 接口耗时 19s，而 _api_get 默认 15s 超时，
        # 于是被判 fail，界面上写"未取到"——用户以为是"仓库没提交"，其实只是慢。
        'fail': f'{what}请求失败（超时/网络中断，或返回非 JSON）',
        'notfound': f'{what} 404（仓库不存在，或登记的分支名与实际不符）',
        'unauthorized': f'{what} 401（token 无效或无权限）',
        'unsupported': f'{what}该平台不支持',
    }.get(state, f'{what}返回未知状态 {state!r}')


def _probe_repo(t):
    """对单个监测目标做四类信号采样 → dict（含每项三态）。
    返回结构：{alive, content_sha, n_files, n_commits_30d, pushed_at, dirs, note}
    任一项失败 → 该项给 '无法判定'/None，绝不用空值冒充"无变化"。

    S091：平台差异收进 `_platform_meta`——本函数只负责组装，不再内联平台分支。
    """
    url = t.get('仓库URL', '')
    branch = t.get('分支', 'main') or 'main'
    owner, repo, platform, host = _parse_repo(url)
    rec = {'alive': '无法判定', 'content_sha': '', 'n_files': None,
           'n_commits_30d': None, 'pushed_at': None, 'dirs': [], 'note': '',
           'alive_by': '', 'why': {}, 'why_state': {}}

    # ① 存活状态：探测仓库主页 HTTP 码
    code = _http_code(url)
    rec['alive'] = _alive_verdict(code)
    rec['alive_by'] = '主页探测'
    rec['note'] = f'HTTP {code}' if code else '网络不可达'

    if not owner or not repo:
        rec['note'] = 'URL 无法解析出 owner/repo'
        return rec

    # ②+③+④ 平台元信息（pushed_at / 目录树 / 近30天提交），统一走适配层
    meta = _platform_meta(platform, owner, repo, branch, host)
    mst = meta.get('state')
    rec['pushed_at'] = meta.get('pushed_at')
    rec['n_commits_30d'] = meta.get('n_commits_30d')
    # ★ S190：把"为什么没取到"一并带出（原先只带 None，原因全丢在 _platform_meta 里）
    rec['why'] = meta.get('why') or {}
    rec['why_state'] = meta.get('why_state') or {}

    # ★ S169 修：存活判定改用 API 结论优先。
    #   原实现只看 `github.com/{owner}/{repo}` 主页 HTTP 码。实测该域名在本网络
    #   **时通时断**：IPTV2 拿到 200、而 TVBox_live/tvbox/TVboxOSC/tvbox-cfg 全是
    #   25s 超时→000。可它们的 API 全 200、文件数/近30天提交都取到了——仓库活得好好的，
    #   报告却写「存活=无法判定」。这是**监测面缺口伪装成健康度**（M-R113 同款）：
    #   「无法判定」看着比「死」温和，久了就没人追。
    #   判据层次（都是**实测结论**，不是猜测）：
    #     · API ok               → 存活（API 能返回 repos 元信息，仓库必然存在）
    #     · API notfound         → 已封禁/删除（API 权威 404，比主页 404 可信）
    #     · API ratelimit/失败   → 退回主页探测结论（此时主页是**唯一**证据源）
    #   主页探测结果不丢，记在 alive_by 里，便于分辨「凭什么判的」。
    if mst == 'ok' and code != 200 and rec['alive'] != '存活':
        rec['alive'] = '存活'
        rec['alive_by'] = 'API元信息（仓库主页不可达）'
    elif mst == 'notfound':
        rec['alive'] = '已封禁/删除'
        rec['alive_by'] = 'API 404'
    if meta.get('dirs'):
        rec['dirs'] = meta['dirs']
        rec['n_files'] = meta['n_files']
    # 默认分支探测（登记分支若为占位 main/master，可据实校正）
    if meta.get('default_branch'):
        rec['default_branch'] = meta['default_branch']
    # note 组装（三态显式：限流/失败/不支持各有明确措辞，禁静默）
    if mst == 'ratelimit':
        if platform == 'GitHub':
            _ts, _tv, _ = _load_token()
            rec['note'] = (('GitHub API 限流——token 已配置但仍触顶（多目标并发所致），稍后重试即可')
                           if _ts == 'ok' else
                           ('GitHub API 限流（未鉴权 60/h）——无法判定；'
                            '配 token 可升至 5000/h：`tvbox probe token`'))
        else:
            rec['note'] = meta.get('note') or f'{platform} API 限流——无法判定'
    elif mst in ('fail', 'notfound'):
        rec['note'] = meta.get('note') or f'{platform} 元信息获取失败'
    elif mst == 'unsupported':
        rec['note'] = meta.get('note') or f'平台 {platform} 暂不支持 API 监测'
    else:
        # ok：附上平台与关键值摘要，便于人工核对（不是必须，但提高可观测性）
        rec['note'] = f'{platform} API 正常'
    if rec['alive_by'] and rec['alive_by'] != '主页探测':
        rec['note'] += f'；存活依据={rec["alive_by"]}'

    # ⑤ 内容指纹：拉主配置 raw 算 sha（各平台 raw 路径见 _find_config_path）
    #
    # ★ S193 补（用户 00:11「查填 token 后说问题」）：**指纹取不到时原来一声不吭**。
    #   实测：6 个目标里 3 个「内容指纹=—」，而输出里既没 why 也没 note 分支——
    #   用户只看到一根横杠，无从判断是"没有配置文件"还是"网络拿不到"。
    #   这正是 S190 定下的铁律被漏掉的一处：**每个 None 字段必须带一条 why**。
    #   而且指纹与目录树是两套通道：目录树走 api.github.com（带 token），
    #   指纹走 raw.githubusercontent.com（**不带 token、本网络需走镜像**）——
    #   所以"目录树取到了、指纹取不到"是完全可能的组合，不能共用一条结论。
    cfg = t.get('配置路径', '') or ''
    raw_base, cands = _find_config_path(owner, repo, branch, platform, host)
    cand_list = [cfg] if cfg else cands
    _cfg_try = []
    for c in cand_list:
        st, txt = _fetch(raw_base + c)
        _cfg_try.append(f'{c}={st}')
        if st == 'ok' and txt:
            rec['content_sha'] = _content_sha(txt)
            rec['config_path'] = c
            break
    if not rec['content_sha']:
        rec['note'] = (rec.get('note') or '') + \
            f'；内容指纹未取到（试过 {", ".join(_cfg_try[:4])}）'

    return rec


def _domain_addr_verdict(mode, code):
    """域名型单地址判定（与 repo 探测不同，专用于直接域名/host）。

    q43 测活口径：直接域名对探测路径往往只回 4xx，但只要服务器响应（2xx/3xx/4xx）
    即视为该地址可达/存活；仅 5xx/超时/不可达=无法判定（不轻判死，M-R60）。
    模式差异：
      · host-set / ip-direct：4xx（含 403/404）一律当"服务在"→ 存活；
      · suffix-set：404/410/451 是「后缀通配已撤」→ 已封禁/删除（其余响应→存活）。
    返回三态字符串（存活/已封禁/删除/无法判定），不引新枚举。
    """
    if code is None or code == 0:
        return '无法判定'            # 超时/不可达（非死）
    if mode == 'suffix-set':
        if code in (404, 410, 451):
            return '已封禁/删除'      # 后缀通配失效
        if code < 500:
            return '存活'             # 2xx/3xx/4xx（服务在）
        return '无法判定'             # 5xx
    # host-set / ip-direct：直接域名/host 探活路径，4xx 一律算"服务器在"
    if code < 500:
        return '存活'                 # 2xx/3xx/4xx 均算服务器在（含 403/404 半活）
    return '无法判定'                 # 5xx


def _probe_domain_set(t):
    """域名型集合探针：按模式构造待探测 URL，逐地址 HTTP 探测，三态聚合判活。

    返回结构与 _probe_repo 对齐（含 'alive' 键，防 cmd_monitor_check 回写 KeyError）：
    {alive, content_sha, n_files, n_commits_30d, pushed_at, dirs, note,
     alive_by, why, why_state, 集合明细}
    聚合严格落三态（不引新枚举，避免下游断裂）：
      任一活→存活；全死/通配失效→已封禁/删除；全超时/不可达→无法判定；
      混合（死+超时、无活）→ 已封禁/删除（有确认死信号优先于超时歧义）。
    """
    mode = t.get('模式', 'host-set')
    seeds = t.get('种子') or []
    rec = {'alive': '无法判定', 'content_sha': '', 'n_files': None,
           'n_commits_30d': None, 'pushed_at': None, 'dirs': [], 'note': '',
           'alive_by': '', 'why': {}, 'why_state': {}, '集合明细': []}
    if not seeds:
        rec['note'] = 'companion 无种子'
        rec['alive_by'] = '结构缺失'
        return rec
    urls = []
    for s in seeds:
        if mode == 'suffix-set':
            import random, string
            rnd = ''.join(random.choices(string.ascii_lowercase, k=8))
            urls.append(f'https://test{rnd}.{s}.work')   # 复现 q43 探测前缀
        else:   # host-set / ip-direct：种子已是完整 URL
            urls.append(s if isinstance(s, str) and s.startswith('http')
                        else f'http://{s}')
    n_alive = n_dead = n_undecided = 0
    detail = []
    for u in urls:
        code = _http_code(u, timeout=8)
        verdict = _domain_addr_verdict(mode, code)
        detail.append({'url': u, 'code': code, 'verdict': verdict})
        if verdict == '存活':
            n_alive += 1
        elif verdict == '已封禁/删除':
            n_dead += 1
        else:
            n_undecided += 1
    rec['集合明细'] = detail
    # 严格三态聚合（不引新枚举，避免下游断裂）
    if n_alive > 0:
        rec['alive'] = '存活'
        rec['alive_by'] = '集合内任一地址存活'
    elif n_dead > 0:
        rec['alive'] = '已封禁/删除'
        rec['alive_by'] = '集合内地址全死/通配失效'
    else:
        rec['alive'] = '无法判定'
        rec['alive_by'] = '集合内地址全超时/不可达'
    rec['note'] = (f'集合{len(urls)}中：{n_alive}活/{n_dead}死/{n_undecided}超时；'
                   f'模式={mode}')
    return rec


# ─────────────────────────────────────────────────────────────────────────
# 发布源型探针（S199·借鉴小虎斑）：对象存储直连包，无 commit 维度
# ─────────────────────────────────────────────────────────────────────────
def scan_pub_source(url, timeout=20):
    """发布源轻量探针：HTTP 探活对象存储直连包 → 三态 dict。

    填补传统 monitor 只认 GitHub 仓库的黑洞：小虎斑系配置是对象存储直连包
    （无 git 历史、无 commit），靠它即可知「包在不在 / 变没变」。

    三态：
      · 取到（200/206，有 Content-Length）→ 返回 bytesize/sha12/last_modified/etag
      · 取不到（404/410/连接拒/域名挂）→ alive='已失效'
      · 无法判定（超时/412/5xx/编码异常）→ alive='无法判定'
    轻量：只取前 64KB（Range）算 sha12 检测变更，不整包下载（大包也快）。
    """
    if not url:
        return {'alive': '无法判定', 'note': '发布源URL 为空', 'sha12': '', 'bytesize': None,
                'last_modified': '', 'etag': '', 'why_state': {'pub': 'empty'}}
    try:
        rc, head = _sh([CURL, '-sS', '-I', '-L', '-m', str(timeout), '-A', UA, url])
        _head_up = (head or '').upper()
        if rc != 0:
            return {'alive': '无法判定', 'note': f'发布源探测命令失败（{head[:80] or "超时"}）',
                    'sha12': '', 'bytesize': None, 'last_modified': '', 'etag': '',
                    'why_state': {'pub': 'cmd_fail'}}
        if ('404 NOT FOUND' in _head_up or '410 GONE' in _head_up
                or 'HTTP/2 404' in _head_up or 'HTTP/1.1 404' in _head_up
                or 'HTTP/2 410' in _head_up):
            return {'alive': '已失效', 'note': '发布源返回 404/410（包已下架/路径失效）',
                    'sha12': '', 'bytesize': None, 'last_modified': '', 'etag': '',
                    'why_state': {'pub': '404'}}
        rc2, body_b = _sh_bytes([CURL, '-sS', '-L', '-m', str(timeout), '-A', UA, '-r', '0-65535', url])
        sha12 = _content_sha(body_b) if rc2 == 0 and body_b else ''
        last_modified = ''
        etag = ''
        bytesize = None
        for _ln in (head or '').splitlines():
            _l = _ln.lower()
            if _l.startswith('last-modified:'):
                last_modified = _ln.split(':', 1)[1].strip()
            elif _l.startswith('etag:'):
                etag = _ln.split(':', 1)[1].strip().strip('"')
            elif _l.startswith('content-length:'):
                try:
                    bytesize = int(_ln.split(':', 1)[1].strip())
                except Exception:
                    bytesize = None
        if not sha12:
            # 回退：Range 被拦（反爬/挑战页，如实测蓝奏云）时，轻量 GET 探活。
            # --max-filesize 限 256KB 防误下大包；页面型锚点（蓝奏云发布页）含动态
            # token（实测连抓两次 sha 不同），故只探活不采指纹，防「内容变更」误报。
            rc3, body_fb = _sh_bytes([CURL, '-sS', '-L', '-m', str(timeout),
                                      '--max-filesize', '262144', '-A', UA_WEB, url])
            if rc3 == 0 and body_fb:
                return {'alive': '存活',
                        'note': '发布页探活成功（Range 被拦，GET 回退；页面型锚点无稳定指纹）',
                        'sha12': '', 'bytesize': bytesize, 'last_modified': last_modified,
                        'etag': etag, 'why_state': {'pub': 'no_body_get_ok'}}
            return {'alive': '无法判定', 'note': '发布源 HEAD 可达但正文取不到（可能需鉴权/反爬）',
                    'sha12': '', 'bytesize': bytesize, 'last_modified': last_modified,
                    'etag': etag, 'why_state': {'pub': 'no_body'}}
        return {'alive': '存活', 'note': '发布源直连包可达',
                'sha12': sha12, 'bytesize': bytesize, 'last_modified': last_modified,
                'etag': etag, 'why_state': {}}
    except Exception as e:
        return {'alive': '无法判定', 'note': f'发布源探测异常：{type(e).__name__}',
                'sha12': '', 'bytesize': None, 'last_modified': '', 'etag': '',
                'why_state': {'pub': 'except'}}


def _probe_pub_source(t):
    """发布源型目标的探测派发：组装与 _probe_repo 同构的 rec，便于主流程复用。"""
    url = t.get('发布源URL') or ''
    r = scan_pub_source(url)
    rec = {
        'alive': r['alive'],
        'activity': '无法判定',          # 包型无提交维度，活跃度改由"更新时间"判定
        'note': r.get('note', ''),
        'content_sha': r.get('sha12', ''),
        'n_files': None,
        'n_commits_30d': None,
        'dirs': [],
        'pushed_at': None,
        'why': {},
        'why_state': r.get('why_state', {}),
        'pub_bytesize': r.get('bytesize'),
        'pub_last_modified': r.get('last_modified'),
        'pub_etag': r.get('etag'),
    }
    _lm_ts = _parse_ts(r.get('last_modified', '')) if r.get('last_modified') else None
    rec['pushed_at'] = _lm_ts          # 让主流程 _activity_verdict 走"按推送时间判定"口径，与仓库型统一；否则发布源型活跃度恒为"无法判定"（S199 整合 bug）
    if _lm_ts:
        rec['activity'] = _activity_verdict(None, _lm_ts)
    return rec


def _probe_tvbox_iface(t):
    """TVBox 接口型端点探测：HTTP GET 配置 → 校验为合法 TVBox json → 三态。

    ★为什么单列一类：TVBox 接口是「配置 URL」（如 xxxx/tvbox.json、api.php/...），
      既非 git 仓库也非对象存储包。旧 monitor 仅 repo 型完全覆盖不到，
      导致「作者聚合分发站里的 55 组接口」没法纳入监测（666.zip 实证）。
    """
    urls = t.get('seed_urls') or ([t['url']] if t.get('url') else [])
    if not urls:
        return {'alive': '无法判定', 'activity': '无法判定',
                'note': 'tvbox-iface 无可用 URL', 'content_sha': '',
                'n_files': None, 'n_commits_30d': None, 'dirs': [],
                'pushed_at': None, 'why_state': {'iface': 'no_url'}}
    url = urls[0]
    rec = {'alive': '无法判定', 'activity': '无法判定', 'note': '',
           'content_sha': '', 'n_files': None, 'n_commits_30d': None,
           'dirs': [], 'pushed_at': None, 'why_state': {}}
    try:
        rc, body = _sh_bytes([CURL, '-sS', '-L', '-m', '20', '-A', UA, url])
        if rc != 0:
            rec['alive'] = '无法判定'
            rec['note'] = f'接口 GET 失败（{body[:80] or "超时"}）'
            rec['why_state'] = {'iface': 'cmd_fail'}
            return rec
        try:
            j = json.loads(body.decode('utf-8', 'replace'))
        except Exception:
            rec['alive'] = '已失效'
            rec['note'] = '接口返回非 JSON（疑似域名被接管/返回错误页）'
            rec['why_state'] = {'iface': 'not_json'}
            return rec
        _ok = bool(isinstance(j, dict) and (j.get('sites') or j.get('type')
                                            or j.get('video') or j.get('list')))
        if not _ok:
            rec['alive'] = '已失效'
            rec['note'] = '接口返回 JSON 但非 TVBox 结构（无 sites/type）'
            rec['why_state'] = {'iface': 'not_tvbox'}
            return rec
        rec['alive'] = '存活'
        rec['note'] = f'TVBox 接口存活（sites/type 校验通过，{len(body)}B）'
        rec['content_sha'] = _content_sha(body)
        rec['why_state'] = {'iface': 'ok'}
    except Exception as e:
        rec['alive'] = '无法判定'
        rec['note'] = f'接口探测异常：{type(e).__name__}'
        rec['why_state'] = {'iface': 'except'}
    return rec


# ─────────────────────────────────────────────────────────────────────────
# 生命周期声明探针（S199·借鉴小虎斑 mark.json 自报）：与实测活跃度正交交叉
# ─────────────────────────────────────────────────────────────────────────
_LIFECYCLE_STATES = ('正式', '测试', '弃更', '停止')
_LIFECYCLE_KEYWORDS = {            # 自报态关键词 → 归一态
    '仅供测试': '测试', '测试版': '测试', 'beta': '测试', 'test': '测试',
    '弃更': '弃更', '已停更': '弃更', '不再维护': '弃更', 'deprecated': '弃更',
    '停止': '停止', '已停止': '停止', 'stop': '停止',
    '正式': '正式', '稳定版': '正式', 'release': '正式', 'stable': '正式',
}


def _extract_decl(d, raw):
    """从 mark.json（可能是 数组/对象/嵌套 list）递归抽取自报信号。

    小虎斑真实结构：[{'name':..., 'list':[{'name':'小虎斑本地更新',
    'version':'26.10.18-仅供测试'}, ...]}]，version 嵌套在 list[0].version——
    顶层 .get('version') 直接失效。故递归遍历所有层级，从 version/note/name
    等字段抽取，并拼成 hay 文本供关键词判定自报态。
    """
    vers, notes, dates = [], [], []
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                kl = str(k).lower()
                if isinstance(v, str) and v.strip():
                    if kl in ('version', 'ver'):
                        vers.append(v.strip())
                    elif kl in ('note', 'status', 'remark', 'name', 'title',
                                'desc', 'description'):
                        notes.append(v.strip())
                    elif kl in ('date', 'update', 'updated', 'time'):
                        dates.append(v.strip())
                if isinstance(v, (list, dict)):
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    if isinstance(d, (list, dict)):
        walk(d)
    if not vers:  # 兜底：原文里抓形如 26.10.18 / 2026.10.18 的版本号
        m = re.search(r'\d{2}(?:\.\d{1,2}){1,2}(?:-[^\s",}]+)?', raw or '')
        if m:
            vers.append(m.group(0))
    hay = ' '.join(vers + notes + dates)
    return (vers[0] if vers else '', notes[0] if notes else '',
            dates[0] if dates else '', hay)


def scan_lifecycle_decl(t):
    """轻量自报探针：尝试抓作者自报的生命周期信号 → 三态 dict。

    小虎斑在 mark.json 自报 version=26.10.18-仅供测试——作者自报态与实测活跃度
    是正交的两个维度（自报"测试"≠实测"活跃"，交叉才有意义）。

    三态：
      · 取到自报 → 解析出 (自报版本, 自报态, 自报日期)
      · 无自报（找不到可直连的声明文件/关键词）→ state='无自报'
      · 无法判定（超时/网络异常）→ state='无法判定'
    """
    url = t.get('发布源URL') or t.get('仓库URL') or ''
    base = url.rsplit('/', 1)[0] if url and '/' in url else ''
    cand = []
    if t.get('kind') == 'pub-package' and base:
        cand = [f'{base}/mark.json', f'{base}/version', f'{base}/version.txt']
    elif t.get('仓库URL'):
        cand = [f'{url.rstrip("/")}/raw/main/mark.json',
                f'{url.rstrip("/")}/raw/master/mark.json',
                f'{url.rstrip("/")}/raw/main/version',
                f'{url.rstrip("/")}/raw/master/version']
    for u in cand:
        rc, out = _sh([CURL, '-sS', '-L', '-m', '12', '-A', UA, u])
        if rc != 0 or not out:
            continue
        try:
            d = json.loads(out)
        except Exception:
            d = None
        ver, note, date, _hay = _extract_decl(d, out)
        decl_state = '正式'
        _hay = _hay.lower()
        for kw, st in _LIFECYCLE_KEYWORDS.items():
            if kw.lower() in _hay:
                decl_state = st
                break
        if ver or note:
            return {'state': '取到', '自报版本': ver, '自报态': decl_state,
                    '自报日期': date, 'note': f'自报信号来自 {u}'}
    if not cand:
        return {'state': '无自报', '自报版本': '', '自报态': '', '自报日期': '',
                'note': '该目标不含可直连的自报文件'}
    return {'state': '无自报', '自报版本': '', '自报态': '', '自报日期': '',
            'note': '候选自报文件均未取到（可能不公开/路径不同）'}


def cross_lifecycle(decl, activity):
    """生命周期声明 × 实测活跃度 正交交叉判据 → 结论 dict。

    四个象限：
      · 声明弃更/停止 ∩ 实测活跃   → 冲突（自报弃更却仍在动，声明可能过期或 fork 别处）
      · 声明正式       ∩ 实测停滞/归档 → 风险（声明正式却长期不动）
      · 声明测试       ∩ 任意实测     → 提示（仅供测试源，稳定性低）
      · 其余                            → 一致
    """
    ds = (decl or {}).get('自报态', '') or ''
    if (decl or {}).get('state') != '取到' or not ds:
        return {'level': '无', 'msg': '无有效自报，跳过交叉'}
    if ds in ('弃更', '停止') and activity in ('活跃', '停滞'):
        return {'level': '冲突', 'msg': f'自报「{ds}」但实测「{activity}」——声明可能过期或非同一分支'}
    if ds == '正式' and activity in ('停滞', '归档'):
        return {'level': '风险', 'msg': f'自报「正式」但实测「{activity}」——长期不动，疑似隐性停更'}
    if ds == '测试':
        return {'level': '提示', 'msg': '自报「测试」——仅供测试源，稳定性低于正式发布'}
    return {'level': '一致', 'msg': f'自报「{ds}」与实测「{activity}」一致'}


def _probe_target(t):
    """监测探测分发器：按 kind 选择探针。

    - domain-set（域名型集合）：走 _probe_domain_set（按集合判活）；
    - pub-package（对象存储直连包）：走 _probe_pub_source（HTTP 轻量探活）；
    - 其余（仓库型）：保持原 _probe_repo 行为（向后兼容 200+ 目标）。
    """
    if t.get('kind') == 'domain-set':
        return _probe_domain_set(t)
    if t.get('kind') == 'pub-package':
        return _probe_pub_source(t)
    if t.get('kind') == 'tvbox-iface':
        return _probe_tvbox_iface(t)
    return _probe_repo(t)


def _record_sample(t, rec):
    """把一次采样并入 activity_log（merge 语义，只追加）。返回 (saved, diff_info)。

    S088·M-R60：**本期目录树取不到时不得参与差集**。S087 实证——GitHub API 限流使
    `dirs=[]`，若直接与上期 290 项比对，会报出 **"移除目录×290"**（=谎报"作者删光了
    所有文件"）。这是"失败折叠成空态"的最恶性形态：**读失败被当作"内容清空"**，
    比"无结论"更危险（会触发错误的应急处置）。故差集仅在**两期目录都有效**时计算；
    任一期无效 → 该维度标 '无法判定'，不产出 added/removed。
    """
    state, alog = load_alog()
    if state == 'corrupt':
        return False, {'error': '基座损坏拒绝回写'}
    url = t.get('仓库URL') or t.get('id') or ''   # 域名型无 URL → 用 id 作键，避免撞 ''
    repos = alog.setdefault('repos', {})
    node = repos.setdefault(url, {'history': []})
    hist = node['history']
    prev = hist[-1] if hist else None
    diff = {}
    if prev:
        # 目录树有效性：None（取不到）与 []（真是空仓）语义不同，前者不得参与差集
        cur_dirs_ok = isinstance(rec.get('n_files'), int)
        prev_dirs_ok = isinstance(prev.get('n_files'), int)
        if cur_dirs_ok and prev_dirs_ok:
            added, removed = _dir_diff(prev.get('dirs', []), rec.get('dirs', []))
            diff['dirs_added'] = added
            diff['dirs_removed'] = removed
        else:
            diff['dirs_undecidable'] = True   # 本期或上期目录树缺失 → 无法判定
        diff['commit_changed'] = prev.get('commit') != t.get('当前commit')
        diff['content_changed'] = bool(prev.get('content_sha') and rec.get('content_sha')
                                       and prev['content_sha'] != rec['content_sha'])
    hist.append({'ts': now_iso(), 'alive': rec.get('alive'),
                 'content_sha': rec.get('content_sha'), 'n_files': rec.get('n_files'),
                 'n_commits_30d': rec.get('n_commits_30d'), 'dirs': rec.get('dirs', [])})
    node['history'] = hist[-60:]      # 保留近 60 期
    alog['latest_ts'] = now_iso()
    saved, _ = save_alog(alog)
    return saved, diff


def cmd_monitor_check(a):
    """monitor check：扫全部/单目标，四类信号逐条三态，回写状态与采样。"""
    state, targets = load_targets()
    if state == 'corrupt':
        print('❌ 监测登记册损坏，无法检查（请人工修复 monitor_targets.json）')
        sys.exit(2)
    if state == 'none' or not targets:
        print('ℹ️ 尚无监测目标。用 `tvbox probe monitor add --url <repo_url>` 登记第一个。')
        return
    sel = targets
    if a.repo:
        sel = [t for t in targets if a.repo in (t.get('仓库URL', ''), t.get('id', ''))]
        if not sel:
            print(f'❌ 未找到目标：{a.repo}')
            sys.exit(1)
    print(f'📡 监测上游仓库（{len(sel)} 个目标）')
    # ★ P2（S163）：跑之前先看配额，别让一半仓库白跑。
    #   实测：匿名 60/h，一次 check 约耗 3 请求，6 仓库 18 次——额度被前面耗光时
    #   后半批全部「无法判定」，5 分钟白等。预检不拦（无法判定≠死，M-R60），
    #   只把后果说明白，让"现在跑"或"先配 token"成为有依据的决定。
    _ls, _rl = _rate_limit()
    if _ls == 'ok' and isinstance(_rl.get('remaining'), int):
        _need = len(sel) * 3
        if _rl['remaining'] < _need:
            # ★ S169 修：原话一律「等配额重置」，但 reset 已过期时（'stale'）等 0 分钟，
            #   那句话把人往"再等等"上带——实测 14:01 显示「重置 4 分钟后」，到点再查
            #   仍是 0，因为真值受 CDN 缓存与配额桶切换双重影响，**不会按时翻绿**。
            _reset = _rl.get('reset') or 0
            _wait = max(0, int((_reset - time.time()) / 60)) if _reset else 0
            if _rl.get('stale') or _wait == 0:
                _tail = ('⚠️ 但 reset 时刻已过/数据陈旧——**等不会变好**，'
                         '唯一确定解法是配 token（匿名 60/h 配额对 6 仓库的巡检本就偏紧）。')
            else:
                _tail = f'或约 {_wait} 分钟后重试（reset {time.strftime("%H:%M", time.localtime(_reset))}）。'
            print(f'   ⚠️ 配额预检：剩余 {_rl["remaining"]} < 本次约需 {_need}'
                  f'（{len(sel)} 仓库×3 请求）——后半批大概率「无法判定」白跑。'
                  f'可先 `tvbox probe token --set <串>` 升至 5000/h，{_tail}')
            # ★ S181：这个预检**只是提示，不是判决**。实测反例很多：
            #   预检说「剩余 0」，单仓库那一跑却完整取到 文件数=778。
            #   原因是 /rate_limit 在多个配额桶间跳值。不加这句，用户会以为
            #   「剩 0 就一定白跑」而放弃，白白错过能跑的那几轮。
            print('   ℹ️ 但该预检**不可靠**（GitHub 配额桶有多套，同一时刻可能查到不同的桶）：'
                  '实测出现过「预检说 0、实际取到数据」。仍可照跑，看末尾的实测结论。')
    # ★ P1（S163）：探测阶段接入生态统一并发入口（M-R38：用 concurrency.run_bounded，
    #   不另造 ThreadPoolExecutor——S111 遗留正是"5 处直接吃 --workers 绕过统一入口"）。
    #   只并发**网络探测**（_probe_repo，纯读、无共享态）；回写/落盘仍在主线程串行做
    #   （_record_sample/save_targets 写共享文件，并发写＝竞态）。
    #   keep_order=True：回写字段按 t 顺序对应，绝不能错位（错位＝把 A 的状态写到 B——S162 同款事故）。
    from concurrency import run_bounded, TIER_WEB
    _HUB = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
    if _HUB not in sys.path:
        sys.path.insert(0, _HUB)
    _rep = run_bounded(sel, _probe_target, tier=TIER_WEB, keep_order=True,
                       label='monitor-check', on_error='record')
    rows = []
    for t, rec in zip(sel, _rep.results):
        if rec is None:
            # 探测线程异常被 record 收走 → 该仓库给"无法判定"兜底，不装成有结论（M-R60）
            rec = {'alive': '无法判定', 'activity': '无法判定', 'note':
                   f'探测线程异常（见并发报告 {_rep.fail_count()} 条失败）',
                   'content_sha': '', 'n_files': None, 'n_commits_30d': None}
        act = _activity_verdict(rec.get('n_commits_30d'), rec.get('pushed_at'))
        # 回写目标字段（S088：补齐 18 字段——文件数/近30天提交/当前commit/下次到期，
        # 使云端库的"最近一次采样快照"列全部活起来）
        # ★ S181：**存活状态同上，不许用「本轮无法判定」抹掉上期结论**。
        #   实测：限流轮里 IPTV2 的「存活」被刷成「无法判定」（它上一轮是「存活」）。
        #   与活跃度同一口径：测到了就更新，没测到就沿用并注明，都没有才写「无法判定」。
        _prev_alive = t.get('存活状态')
        _alive_carry = bool(rec['alive'] == '无法判定' and _prev_alive
                            and _prev_alive != '无法判定')
        if not _alive_carry:
            t['存活状态'] = rec['alive']
        t['内容指纹'] = rec.get('content_sha', '')
        # ★ S181（实测）：**「本轮无法判定」不许覆盖「上期已知结论」**（M-R82 同款）。
        #   原实现 `t['活跃度'] = act` 无条件写，于是限流那轮一跑，6 个仓库里
        #   5 个的「活跃」全被刷成「无法判定」——明明上一轮才实测到 近30天提交=74。
        #   这不是"更诚实"，是**用一次测量失败抹掉已有事实**：账面从此看不出
        #   这些仓库到底活不活跃，下游读到「无法判定」会当成"从没测过"。
        #   判据分层（与 n_files/n_commits_30d 的 isinstance 保护同一口径）：
        #     · 本轮测到了      → 覆盖（事实更新）
        #     · 没测到、上期有值 → **保留上期值**，摘要里明写「本轮未重测，沿用上期」
        #     · 上期也没有      → 才写「无法判定」（真的没有事实可依）
        _prev_act = t.get('活跃度')
        _carried = bool(act == '无法判定' and _prev_act and _prev_act != '无法判定')
        if _carried:
            _note = (rec.get('note') or '') + f'；⚠️ 本轮活跃度未重测，沿用上期「{_prev_act}」'
        else:
            t['活跃度'] = act
            _note = rec.get('note', '')
        if _alive_carry:
            _note += f'；⚠️ 本轮存活未判定，沿用上期「{_prev_alive}」'
        t['上次检查'] = now_iso()
        t['变更摘要'] = _note
        if isinstance(rec.get('n_files'), int):
            t['文件数'] = rec['n_files']
        if isinstance(rec.get('n_commits_30d'), int):
            t['近30天提交'] = rec['n_commits_30d']
        t['下次到期'] = _next_due(t.get('监测周期', '每周'))
        saved, diff = _record_sample(t, rec)
        # ★ S189d 真根因（前三次都改错了地方）：
        #   上面 `if not _alive_carry: t['存活状态'] = rec['alive']` 把**沿用/更新**
        #   已经写进 t 了。于是下一个循环里 `t.get('存活状态')` 与 `rec['alive']`
        #   必然相等（沿用时长这样也相等），`_alive_carry` 重新算一遍永远是 False
        #   ——②那个 `if _alive_carry: mark='🕓'` 是**死代码，一次都进不去**。
        #   实测证据（注入调试）：rec.alive=无法判定、t存活=存活，carry 仍报 False。
        #   修法：判定不能在下游重算，必须在**回写那一刻**定下来，随行带走。
        #   S189c 只改图标表达式、S189b 只改横幅位置，都属"改了显示没改取数"。
        rows.append((t, rec, act, diff, saved, _alive_carry))

    # P3 用：读采样史判"是否首轮"（三态：读失败不报首轮也不报对比，交给上面原有的告警）
    _astate, _alog = load_alog()
    aload_repos = _alog.get('repos', {}) if _astate == 'ok' else {}
    for t, rec, act, diff, saved, _alive_carry in rows:
        # 打印用「账面结论」而非「本轮结论」：沿用上期时要说清，不能装作本轮测到。
        # **必须先算显示值再打标题**——mark 图标也要跟着账面结论走，
        # 否则限流轮会把一个已知「存活」的仓库打成 ⚠️（看着像死了）。
        _act_show = t.get('活跃度') or act
        if _act_show != act:
            _act_show = f'{_act_show}（沿用上期·本轮未重测）'
        _alive_show = t.get('存活状态') or rec['alive']
        _alive_raw = _alive_show
        if _alive_show != rec['alive']:
            _alive_show = f'{_alive_show}（沿用上期·本轮未判定）'
        # ★ S189c 修（用户实测发现）：沿用上期时不许显示 ✅。
        #
        #   原实现：mark 取 `_alive_raw`（沿用后的账面值），于是限流轮里
        #   一个"上期存活、本轮根本没测"的仓库照样打 ✅。实测 6 个仓库全 ✅，
        #   而汇总结论写着「本轮 1/6 取到新数据」——**同屏自相矛盾**。
        #
        #   原作者加这段注释时的顾虑是对的：「否则限流轮会把一个已知存活的仓库
        #   打成 ⚠️，看着像死了」。但修法走到了另一头：**为了不让人误判成"死"，
        #   结果让人误判成"活"**。两个方向都是错的。
        #
        #   按三态契约（M-R60 / §3.5.3「取不到 ≠ 目标不可达」）：
        #     本轮测到 + 活      → ✅ 存活（事实）
        #     本轮测到 + 死      → ⛔ 已封禁/删除（事实）
        #     本轮测到 + 判不了  → ⚠️ 无法判定（事实）
        #     本轮没测到、沿用上期 → 🕓 未测（沿用上期：存活）  ← 原有实现缺这一档
        #   🕓 这个图标本身就说明"这不是本轮的事实"，不会被误读成活或死。
        if _alive_carry:
            mark = '🕓'
        else:
            mark = {'存活': '✅', '已封禁/删除': '⛔', '无法判定': '⚠️'}.get(_alive_raw, '❓')
        print(f'\n  {mark} {t.get("仓库名") or t.get("名称") or t.get("仓库URL") or t.get("id")}')
        # ★ S181（实测）：**None 不是「没数据」，是「没取到」**。
        #   原实现直接 f-string 插值，取不到就印出字面量 `None`：
        #      文件数=None  近30天提交=None
        #   而紧跟着的 note 仍写「GitHub API 正常」——同一条输出里自相矛盾。
        #   实测场景：匿名配额 60/h 耗尽后，后半批仓库的 tree/commits 接口被限流，
        #   n_files/n_commits_30d 变 None，可 meta 主干（repos 元信息）是 ok 的，
        #   于是 note 照旧报"正常"。人看到 `None` 只会当成"这个仓库没数据"，
        #   不会想到"是我配额没了"。这正是 M-R60 禁的「空值冒充结论」。
        if t.get('kind') == 'pub-package':
            _nf = None
            _nc = None
            _bs = rec.get('pub_bytesize')
            _lm = rec.get('pub_last_modified') or '—'
            _bs_s = f'{_bs}B' if isinstance(_bs, int) else '未取到'
            print(f'     存活={_alive_show}  活跃度={_act_show}  包大小={_bs_s}'
                  f'  更新时间={_lm}  指纹={rec.get("content_sha") or "—"}')
        else:
            _nf = rec.get('n_files')
            _nc = rec.get('n_commits_30d')
            _nf_s = _nf if isinstance(_nf, int) else '未取到'
            _nc_s = _nc if isinstance(_nc, int) else '未取到'
            print(f'     存活={_alive_show}  活跃度={_act_show}  文件数={_nf_s}'
                  f'  近30天提交={_nc_s}  指纹={rec.get("content_sha") or "—"}')
        # ★ S199：生命周期声明 × 实测活跃度 正交交叉（pub-package 自动跑；
        #   repo 型仅在单目标 --repo 指定时跑，避免常规巡检拖慢全量）
        _do_life = (t.get('kind') == 'pub-package') or bool(a.repo)
        if _do_life:
            _decl = scan_lifecycle_decl(t)
            _cross = cross_lifecycle(_decl, t.get('活跃度'))
            if _cross['level'] in ('冲突', '风险', '提示'):
                _tag = {'冲突': '⚠️冲突', '风险': '⚠️风险', '提示': 'ℹ️提示'}[_cross['level']]
                print(f'     {_tag} 生命周期交叉：{_cross["msg"]}')
        # ★ S190（核心改动）：**未取到必须自带原因**，不许把"为什么"留给人去查。
        #
        #   原实现只写一句"部分字段未取到（…多为配额耗尽或限流）"——那是**猜测**，
        #   而且猜错过：实测 songlees355-wq/okay 的 commits 接口耗时 19s > 15s 超时，
        #   真实原因是被判超时，可屏幕上写着"配额耗尽"。同一句话害我等配额等了一小时。
        #   现在原因来自 `_platform_meta` 的实际返回码（三态），不是推测。
        _why = rec.get('why') or {}
        for _k, _label in (('n_files', '文件数'), ('n_commits_30d', '近30天提交')):
            if _k in _why:
                print(f'       ↳ {_label}未取到：{_why[_k]}')
        if (_nf is None or _nc is None) and rec.get('note') \
                and '部分字段未取到' not in (rec.get('note') or '') \
                and t.get('kind') not in ('domain-set', 'pub-package'):
            # NOTE（S190）：原因已由上面逐条打印，这里只留一句总括，不再猜具体成因。
            # ★ S204：域名型目标**本就不产生** 文件数/提交数（它按集合判活）。
            #   对它说「部分字段未取到」＝把「不适用」说成「没取到」，
            #   用户会去排查网络/配额，其实没有任何东西该取。
            rec['note'] += '；⚠️ 部分字段未取到（原因见上方 ↳ 行）'
        # ★ S181：本轮没测到、账面沿用上期时，note 不能说「无法判定」——
        #   那与上一行的「存活=存活」直接打架。改成「本轮未取到，账面沿用上期」。
        if (_act_show != act or _alive_raw != rec['alive']) and rec.get('note'):
            rec['note'] = (rec['note']
                           .replace('——无法判定；', '——本轮未取到，账面沿用上期；')
                           .replace('——无法判定', '——本轮未取到')
                           .replace('存活=无法判定', '存活本轮未判定'))
        if rec.get('note'):
            print(f'     note: {rec["note"]}')
        if diff and not diff.get('error'):
            ch = []
            if diff.get('content_changed'): ch.append('内容变更')
            if diff.get('dirs_added'): ch.append(f'新增目录×{len(diff["dirs_added"])}')
            if diff.get('dirs_removed'): ch.append(f'移除目录×{len(diff["dirs_removed"])}')
            if diff.get('dirs_undecidable') and t.get('kind') != 'domain-set':
                ch.append('目录变更无法判定（本期或上期目录树未取到）')
            if ch:
                print(f'     🔔 相对上期：{"、".join(ch)}')
        if not saved:
            print('     ⚠️ 采样未落盘（基座损坏，历史未更新）')
        # ★ P3（S163）：首轮检查后明说"基线已建立"。实测首轮输出全是
        #   「目录变更无法判定（本期或上期目录树未取到）」——用户会误读成功能坏了；
        #   实际是基线刚存、下期起才有对比对象。用采样史期数判（≥2 才算有对比）。
        _h = ((aload_repos or {}).get(t.get('仓库URL') or t.get('id', '')) or {}).get('history', [])
        if diff and diff.get('dirs_undecidable') and len(_h) <= 1 \
                and t.get('kind') != 'domain-set':
            print('     📁 首轮基线已建立——下次检查起，目录变更即可对比')

    # ★ S181：**用实测结果判决本轮到底有没有在监测**，而不是信 `/rate_limit`。
    #   实测依据：`/rate_limit` 会在两个配额桶之间乱跳（同一秒连查三次，0 与 45 交替，
    #   reset 相差 30 分钟）——**这个端点从原理上答不准**。而「本轮实际拿到了几个字段」
    #   是确定的事实。用它做判决，才不会出现「报额度充足、实际全空转」。
    # ★ S204：域名型目标不产生 文件数/近30天提交（那是仓库型指标），
    #   若一并计入「取到数据」统计，会让域名型目标永远显示「0/1 取到=等于没监测」，
    #   再配上「配额端点自报…全限流」的归因——而域名型走的是 curl，跟 GitHub 配额毫无关系。
    #   这是把仓库型的账套到域名型头上，属归因错位（§3.13 ③ 同族）。故分开统计。
    _repo_rows = [r for r in rows if r[0].get('kind') not in ('domain-set', 'pub-package')]
    _dom_rows = [r for r in rows if r[0].get('kind') == 'domain-set']
    _got = sum(1 for _t, _r, _a, _d, _s, _c in _repo_rows
               if isinstance(_r.get('n_files'), int)
               or isinstance(_r.get('n_commits_30d'), int))
    _n = len(_repo_rows)
    if _n and _got == 0:
        print(f'\n  ⛔ 本轮 0/{_n} 取到数据——**实际等于没监测**（全部沿用上期账面）。')
        _ls, _rl = _rate_limit()
        _rem = _rl.get('remaining') if _ls == 'ok' else None
        print(f'     配额端点自报「剩余 {_rem}」，但实测全限流——'
              f'该端点会在多个配额桶间跳值，**不可作为判断依据**。')
        print('     确定解法：`tvbox probe token --set <串>`（60/h → 5000/h）。')
    elif _n and _got < _n:
        # ★ S195：这里原来一律写"（配额偏紧）"——**归因错位**，与 §3.13 ③ 同族。
        #   实测：39/45 取到数据、GitHub token 明明已配（5000/h 剩 4881），
        #   真正缺的是 GitLab/Gitee 两平台各自的凭据，跟 GitHub 配额毫无关系。
        #   把「哪家平台的账」混成一句"配额偏紧"，会让人去配 GitHub token 白忙。
        #   改法：按**平台**分别算取到率，只对真正缺凭据的平台点名，不再猜原因。
        _miss = {}
        for _t, _r, _a, _d, _s, _c in _repo_rows:   # ★ S204：只统计仓库型
            if not (isinstance(_r.get('n_files'), int) or isinstance(_r.get('n_commits_30d'), int)):
                _pf = _t.get('平台') or '未知'
                _miss.setdefault(_pf, []).append(
                    _t.get('仓库名') or _t.get('名称')
                    or _t.get('仓库URL') or _t.get('id') or '?')
        print(f'\n  ⚠️ 本轮 {_got}/{_n} 个目标取到新数据，其余沿用上期账面。')
        for _pf, _names in sorted(_miss.items(), key=lambda x: -len(x[1])):
            if _pf == 'GitHub':
                _hint = '（GitHub 一般不会整批失败，若是同批全挂先查 token 是否真带上）'
            else:
                _hint = f'（{_pf} 平台元信息需该平台凭据，缺则只能落「无法判定」，不是死）'
            print(f'     · {_pf} 未取到 {len(_names)} 条：{", ".join(_names[:5])}'
                  + (' …' if len(_names) > 5 else '') + _hint)
    elif _n:
        print(f'\n  ✅ 本轮 {_got}/{_n} 个目标全部取到新数据。')

    # ★ S204：域名型目标单列结论（不走 文件数/提交数，改按集合判活口径汇报）
    if _dom_rows:
        _dl = {'存活': 0, '已封禁/删除': 0, '无法判定': 0}
        for _t, _r, _a, _d, _s, _c in _dom_rows:
            _dl[_r.get('alive', '无法判定')] = _dl.get(_r.get('alive', '无法判定'), 0) + 1
        print(f'\n  🌐 域名型目标 {len(_dom_rows)} 个（按集合判活，不取文件数/提交数）：'
              f'存活 {_dl.get("存活", 0)}、已封禁/删除 {_dl.get("已封禁/删除", 0)}、'
              f'无法判定 {_dl.get("无法判定", 0)}')
        for _t, _r, _a, _d, _s, _c in _dom_rows:
            print(f'     · {_t.get("名称") or _t.get("id")}：{_r.get("note") or "—"}')
    # ★ S199：发布源型目标单列结论（走 HTTP 轻量探活，非仓库型，不取文件数/提交数）
    _pub_rows = [r for r in rows if r[0].get('kind') == 'pub-package']
    if _pub_rows:
        _pl = {'存活': 0, '已封禁/删除': 0, '无法判定': 0}
        for _t, _r, _a, _d, _s, _c in _pub_rows:
            _pl[_r.get('alive', '无法判定')] = _pl.get(_r.get('alive', '无法判定'), 0) + 1
        print(f'\n  📦 发布源型目标 {len(_pub_rows)} 个（HTTP 轻量探活，非仓库型）：'
              f'存活 {_pl.get("存活", 0)}、已失效 {_pl.get("已封禁/删除", 0)}、'
              f'无法判定 {_pl.get("无法判定", 0)}')
        for _t, _r, _a, _d, _s, _c in _pub_rows:
            _bs = _r.get('pub_bytesize')
            _bs_s = f'{_bs}B' if isinstance(_bs, int) else '—'
            print(f'     · {_t.get("名称") or _t.get("id")}：{_r.get("note") or "—"}'
                  f'（包大小={_bs_s} 更新={_r.get("pub_last_modified") or "—"}）')

    # ★ S190·自诊断（这一条是本轮真正的核心，不是锦上添花）
    #
    #   由来：用户 2026-10-20 23:14 质问「不是你一直敲代码，为啥说技能在工作了」。
    #   复盘：那一晚 `monitor check` 报了 4 次"未取到"，我为了查明原因**手写了 9 个
    #   临时脚本**（打 API 量耗时、抓返回码、比对分支名……）。而脚本自己一句原因都没有。
    #   这不是技能在工作，是**技能把排查工作原样推回给人，还让人以为已经测过了**。
    #
    #   现在把今晚人工排查的路径固化成判据，让脚本自己走一遍：
    #     ① 配了 token，却整批出现 ratelimit → 不是配额问题，是**token 没用上**
    #        （真实事故：`_platform_meta` 4 处调用漏传 headers，token 配好却全程匿名）
    #     ② 全部失败但错因是 fail（非 ratelimit）→ 不是配额，是网络/超时
    #     ③ 单个目标失败 → 点名是哪个、为什么（不再一句"其余沿用上期"带过）
    #
    #   为什么要"配了 token 还限流"这个特判：正常配好后不可能整批限流（5000/h）。
    #   一旦出现，就说明**配置与使用之间断了**——今晚正是这种，而它连续 3 次逃过了
    #   人工复查（因为 rate_limit 端点自报 5000，看着一切正常）。
    _tok_state, _tok_val, _tok_src = _load_token()
    _why_cnt = {}
    _rl_hit = 0                    # 用 state 码计数（不拿中文文案当判据，S190b）
    for _t, _r, _a, _d, _s, _c in rows:
        for _k, _msg in (_r.get('why') or {}).items():
            _why_cnt[_msg] = _why_cnt.get(_msg, 0) + 1
        for _k, _st in (_r.get('why_state') or {}).items():
            if _st == 'ratelimit':
                _rl_hit += 1
    _has_rl = _rl_hit > 0
    if _got < _n and _tok_state == 'ok' and _has_rl:
        print('     🚨 自诊断：token 已配置（{0}），却仍触发限流——'
              '**这不可能是配额不够**（5000/h）。'.format(_tok_src))
        print('        极可能是「配置了但请求没带上」：检查各 API 调用是否都传了 headers。')
        print('        （S190 真实事故：_platform_meta 4 处调用漏传 headers，'
              'token 全程未生效）')
    if _why_cnt:
        print('\n  📋 本轮取不到的逐条原因：')
        for m, c in sorted(_why_cnt.items(), key=lambda x: -x[1]):
            print(f'     ×{c}  {m}')

    ck, lk = save_targets(targets)
    if not ck:
        print('   ⚠️ 云端登记册同步失败（本地已存，可稍后重跑）')
    if not lk:
        print('   ⚠️ 本地登记册写入失败')
    if a.json:
        # ★ 同一处隐患（S189d）：rows 由 5 元组改 6 元组，这里是**第四处解包点**。
        #   不同步就是 ValueError: too many values to unpack —— 而且只在 --json 时才炸，
        #   平时跑不出来。改元组结构必须一次把全部解包点找齐（grep 'in rows'）。
        print(json.dumps([{'url': t.get('仓库URL'), 'alive': r['alive'], 'activity': ac,
                           'carried': c}
                          for t, r, ac, _, _, c in rows], ensure_ascii=False, indent=2))


def cmd_monitor_diff(a):
    """monitor diff：本次采样 vs 上期快照，输出变更明细。"""
    state, targets = load_targets()
    if state != 'ok' or not targets:
        print('ℹ️ 无监测目标或无历史。')
        return
    astate, alog = load_alog()
    if astate == 'corrupt':
        print('❌ 采样史基座损坏，无法对比（请人工修复 activity_log.json）')
        sys.exit(2)
    sel = targets
    if a.repo:
        sel = [t for t in targets if a.repo in (t.get('仓库URL', ''), t.get('id', ''))]
    repos = alog.get('repos', {})
    for t in sel:
        url = t.get('仓库URL') or t.get('id') or ''
        hist = (repos.get(url) or {}).get('history', [])
        print(f'\n  📁 {t.get("仓库名") or t.get("名称") or url}（{len(hist)} 期采样）')
        if len(hist) < 2:
            print('     样本不足 2 期——无法对比（M-R9：不足 2 期不外推）')
            continue
        prev, cur = hist[-2], hist[-1]
        print(f'     存活: {prev.get("alive")} → {cur.get("alive")}')
        if prev.get('content_sha') and cur.get('content_sha'):
            same = prev['content_sha'] == cur['content_sha']
            print(f'     内容: {"无变化" if same else "🔔 已变更"}'
                  f'（{prev["content_sha"]} → {cur["content_sha"]}）')
        # S088：目录树任一期缺失 → 无法判定，不得产出 added/removed（防谎报"清空"）
        if not isinstance(prev.get('n_files'), int) or not isinstance(cur.get('n_files'), int):
            print('     目录: ⚠️ 无法判定（本期或上期目录树未取到，不产出变更结论）')
        else:
            added, removed = _dir_diff(prev.get('dirs', []), cur.get('dirs', []))
            if added:
                print(f'     🔔 新增目录（{len(added)}）：{", ".join(added[:5])}'
                      + (' …' if len(added) > 5 else ''))
            if removed:
                print(f'     🔔 移除目录（{len(removed)}）：{", ".join(removed[:5])}'
                      + (' …' if len(removed) > 5 else ''))
            if not added and not removed and prev.get('content_sha') == cur.get('content_sha'):
                print('     ✅ 无变更')


def cmd_monitor_forecast(a):
    """monitor forecast：活跃度趋势外推（S093）→ 弃更风险。

    与 monitor diff（看"这一期变了什么"）互补——本命令看"这条趋势要往哪走"。
    数据源：采样史基座 `activity_log.json` 的历史 `n_commits_30d` 序列。
    """
    astate, alog = load_alog()
    if astate == 'corrupt':
        print('❌ 采样史基座损坏，无法预测（请人工修复 activity_log.json）')
        sys.exit(2)
    tstate, targets = load_targets()
    if tstate == 'corrupt':
        print('❌ 监测登记册损坏（请人工修复 monitor_targets.json）')
        sys.exit(2)
    if tstate == 'none' or not targets:
        print('ℹ️ 尚无监测目标。用 `tvbox probe monitor add --url <repo_url>` 登记。')
        return
    sel = targets
    if a.repo:
        sel = [t for t in targets if a.repo in (t.get('仓库URL', ''), t.get('id', ''))]
        if not sel:
            print(f'❌ 未找到目标：{a.repo}')
            sys.exit(1)
    repos = alog.get('repos', {})
    out = []
    for t in sel:
        url = t.get('仓库URL') or t.get('id') or ''
        hist = (repos.get(url) or {}).get('history', [])
        res = _forecast_risk(hist)
        out.append({'url': url, 'name': t.get('仓库名') or t.get('名称') or url, **res})
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f'🔮 活跃度趋势外推（{len(sel)} 个目标·基于采样史）')
    for r in out:
        icon = {'升高': '🔴', '稳定': '🟡', '增长': '🟢'}.get(r['risk'], '⚪')
        print(f'\n  {icon} {r["name"]}')
        if r['state'] != 'ok':
            print(f'     ⚠️ {r["state"]}：{r["detail"]}')
        else:
            print(f'     风险={r["risk"]}  斜率={r["slope"]}/天  有效点={r["n_pts"]}')
            print(f'     {r["detail"]}')


def cmd_monitor_status(a):
    """monitor status：监测目标总览表。"""
    state, targets = load_targets()
    if state == 'corrupt':
        print('❌ 监测登记册损坏（请人工修复 monitor_targets.json）')
        sys.exit(2)
    if state == 'none' or not targets:
        print('ℹ️ 尚无监测目标。用 `tvbox probe monitor add --url <repo_url>` 登记。')
        return
    print(f'📡 上游仓库监测台（{len(targets)} 个目标）\n')
    print(f'  {"owner/repo":<34} {"平台":<7} {"存活":<10} {"活跃度":<6} {"上次检查":<20}')
    print('  ' + '─' * 90)
    for t in targets:
        nm = _target_label(t)[:32]
        print(f'  {nm:<34} {t.get("平台",""):<7} {t.get("存活状态","待查"):<10} '
              f'{t.get("活跃度","待查"):<6} {(t.get("上次检查") or "—")[:19]:<20}')
        # ★ S169：存活若靠 API 元信息判的（主页不可达），必须显形——
        #   否则「存活」是个结论，用户看不出仓库主页其实连不上（M-R113）。
        _note = t.get('变更摘要') or ''
        if '存活依据=' in _note:
            print(f'  {"":<34} └─ {_note}')
    due = [t for t in targets if _is_due(t)]
    if due:
        print(f'\n  ⏰ {len(due)} 个已到期未检查：' + ', '.join(
            _target_label(t) for t in due))


def _target_label(t):
    """★ S195：显示名必须带 owner，否则 `TVBox`×4 / `tvbox`×5 分不清是谁的。

    修复的是 id 撞名之后遗留的**可读性**问题：id 早已是 `platform-owner-repo`
    复合键（不可混淆），但表格里只打印 repo 名，人眼仍无法区分。
    优先取 URL 的 owner/repo；取不到才退回「仓库名」字段。
    """
    url = (t.get('仓库URL') or '').rstrip('/')
    m = re.search(r'https?://[^/]+/([^/]+)/([^/?#]+)', url)
    if m:
        repo = re.sub(r'\.git$', '', m.group(2))
        return f'{m.group(1)}/{repo}'
    return (t.get('仓库名') or t.get('名称') or url or t.get('id') or '?')[:32]


def _is_due(t):
    """判断目标是否到期（按监测周期与上次检查时间）。"""
    last = t.get('上次检查') or ''
    if not last:
        return True
    try:
        ts = datetime.strptime(last, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        return True
    days = {'每日': 1, '每周': 7, '每月': 30}.get(t.get('监测周期', '每周'), 7)
    return (time.time() - ts) > days * 86400


def cmd_monitor_due(a):
    """monitor due：列出到期未检查目标。"""
    state, targets = load_targets()
    if state != 'ok' or not targets:
        print('ℹ️ 无监测目标。')
        return
    due = [t for t in targets if _is_due(t)]
    if not due:
        print('✅ 无到期目标（全部在监测周期内）')
        return
    print(f'⏰ {len(due)} 个到期未检查：')
    for t in due:
        print(f'   • {t.get("仓库名") or t.get("名称") or t.get("仓库URL") or t.get("id")}'
              f'（{t.get("监测周期","每周")}·上次 {t.get("上次检查") or "从未"}）')


def _detect_default_branch(owner, repo, platform, host=''):
    """探测仓库默认分支 → 'main' / 'master' / ''（无法判定）。
    S088：S087 用真实仓库验证时暴露——登记默认写 'main'，但大量老仓库仍是 'master'
    （实证 lxcjy/IPTV2）。分支写错会让**所有** raw 路径 404，进而把监测结论全盘带偏。
    S091：平台泛化——GitHub/GitLab/Gitee/Gitea 均优先问 API（限流 → 退化为双路径试探）。"""
    meta = _platform_meta(platform, owner, repo, 'main', host)
    if meta.get('state') == 'ok' and meta.get('default_branch'):
        return meta['default_branch']
    # API 不可达（限流/失败）：用双分支试探（各拉 README，谁 200 用谁）
    for b in ('main', 'master'):
        raw, _ = _find_config_path(owner, repo, b, platform, host)
        st, _ = _fetch(raw + 'README.md')
        if st == 'ok':
            return b
    return ''


def cmd_monitor_add(a):
    """monitor add：登记新监测目标（代码零改动）。"""
    state, targets = load_targets()
    if state == 'corrupt':
        print('❌ 登记册损坏，拒绝写入（防覆盖，M-R60）')
        sys.exit(2)
    url = a.url
    if any(t.get('仓库URL') == url for t in targets):
        print(f'ℹ️ 已登记：{url}')
        return
    owner, repo, platform, host = _parse_repo(url)
    if not owner:
        print(f'❌ URL 无法解析出 owner/repo：{url}')
        sys.exit(1)
    # S195：id 改「平台-作者-仓库名」复合键——仓库名做 id 会让重名仓（tvbox×3、TVBox×3）
    # 在云端 id_map 里互踩，后写覆盖前写（实测 2026-10-21 批量登记暴露）。
    tid = f'{platform.lower()}-{owner}-{repo}'.lower()
    # S088：分支不可盲信默认 'main'——探测真实默认分支（写错则 raw 全 404）
    branch = a.branch
    if not branch or branch == 'main':
        det = _detect_default_branch(owner, repo, platform, host)
        if det:
            branch = det
            if a.branch and a.branch != det:
                print(f'   ℹ️ 默认分支自动修正：{a.branch} → {det}（原值会导致 raw 路径全 404）')
    rec = {
        'id': tid, '仓库名': repo, '仓库URL': url, '作者': a.author or owner,
        '平台': platform, '分支': branch or a.branch, '配置路径': a.config_path or '',
        '监测周期': a.cadence, '关联底包': a.related or '',
        '存活状态': '待查', '当前commit': '', '内容指纹': '',
        '活跃度': '待查', '上次检查': '', '下次到期': '', '变更摘要': '',
    }
    targets.append(rec)
    ck, lk = save_targets(targets)
    print(f'✅ 已登记监测目标：{repo}（{platform}·{a.cadence}）')
    print(f'   id={tid}  URL={url}  分支={rec["分支"]}')
    _persist_note(ck, lk)
    if not a.config_path:
        print('   ℹ️ 未指定配置路径 → 检查时将按常见命名自动探测（config.json 等）')


def cmd_monitor_import(a):
    """monitor import：从 seed JSON 批量登记监测目标（支持离线种子基线）。

    ★666 实证驱动：作者聚合分发站里的「55 组 TVBox 接口」是配置 URL，
      旧 `monitor add` 仅支持单条 git 仓库 URL，没法批量登记。本命令读
      `[{name, urls:[...], kind, seed_baseline, ...}]` 数组循环建目标，
      按 kind 分流（tvbox-iface / repo / pub-package），去重按 id。
    """
    import re as _re
    import hashlib as _hl
    state, targets = load_targets()
    if state == 'corrupt':
        print('❌ 登记册损坏，拒绝写入（防覆盖，M-R60）')
        sys.exit(2)
    seed = json.load(open(a.file, encoding='utf-8'))
    if isinstance(seed, dict):
        seed = seed.get('items') or seed.get('targets') or []
    added, skipped = 0, 0
    for it in seed:
        name = (it.get('name') or it.get('名称') or '').strip()
        urls = it.get('urls') or []
        if not name or not urls:
            skipped += 1
            continue
        kind = it.get('kind', 'tvbox-iface')
        # ★id 唯一性：中文组名 slug 会塌成空串导致批量碰撞（666 实证：60 组只写入 10 条）。
        #   改用「可读 ascii 前缀 + 名称|主URL 哈希」保证每条唯一。
        _asc = _re.sub(r'[^0-9a-zA-Z]', '', name)[:6]
        _h = _hl.md5((name + '|' + (urls[0] if urls else '')).encode('utf-8')).hexdigest()[:8]
        tid = it.get('id') or ('iface-' + _asc + '-' + _h if _asc else 'iface-' + _h)
        if any(t.get('id') == tid for t in targets):
            skipped += 1
            continue
        baseline = bool(it.get('seed_baseline', True))
        rec = {
            'id': tid, '名称': name, 'kind': kind,
            'seed_urls': urls,
            'seed_baseline': baseline,
            'source_file': it.get('source_file', ''),
            'extracted_sha12': it.get('extracted_sha12', ''),
            'extracted_at': it.get('extracted_at', now_iso()),
            '在线状态': it.get('在线状态', '离线种子（待启用）' if baseline else '存活'),
            '存活状态': '待查', '活跃度': '待查',
            '监测周期': it.get('cadence', '每周'),
            '上次检查': '', '下次到期': '', '变更摘要': '',
        }
        targets.append(rec)
        added += 1
    ck, lk = save_targets(targets)
    print(f'✅ 批量导入完成：新增 {added} 条，跳过 {skipped} 条（重复/缺字段）')
    _persist_note(ck, lk)


# S143：测试残留识别词——自检/演示曾把 `gitee.com/easy999/test1`、`github.com/qist/tvbox`
# 写进真册，导致 `monitor status/due` 长期混着 2 个"假目标"。判定不靠人工记忆，靠特征：
_PURGE_PATTERNS = ('/test1', '/test', 'qist/tvbox')


def _purge_reason(t):
    """判断某目标是否属"测试残留"，返回原因串（空串=不是）。"""
    url = (t.get('仓库URL') or '').lower()
    tid = (t.get('id') or '').lower()
    if tid in ('test1', 'test', 'tvbox', 'demo'):
        return f'id 为测试保留名「{tid}」'
    for p in _PURGE_PATTERNS:
        if p in url:
            return f'URL 命中测试样例「{p}」'
    if t.get('上次检查') and t.get('存活状态') == '待查' and t.get('关联底包') == '线路总库_四档':
        # 自检脚本写入的固定指纹组合（正常登记极少同时具备这三项）
        return '同具「待查 + 线路总库_四档 + 无监测历史」的脚本指纹'
    return ''


def cmd_monitor_purge_test(a):
    """monitor purge-test：识别并清理测试残留目标（S143）。缺省只预演。"""
    state, targets = load_targets()
    if state == 'corrupt':
        print('❌ 登记册损坏，拒绝写入（M-R60）')
        sys.exit(2)
    hits, keep = [], []
    for t in targets:
        r = _purge_reason(t)
        (hits if r else keep).append((t, r) if r else t)
    if not hits:
        print(f'✅ 无测试残留（共 {len(targets)} 个目标，全部保留）')
        return
    print(f'🧹 识别到 {len(hits)} 个测试残留目标：')
    for t, r in hits:
        print(f'   · {t.get("id"):<10} {t.get("仓库URL"):<45} ← {r}')
    if not a.yes:
        print(f'\n   预演模式——未写入。确认无误后加 --yes 执行'
              f'（将保留 {len(keep)} 个真实目标）')
        return
    # S143 关键：先云端**真删除**，再写本地——save_targets 只 upsert，
    # 单靠它删不掉云端行，下次 load_targets 云端优先会让"已删目标复活"。
    dok, dn = _cloud_delete(DB_UPSTREAM, 'id', [t.get('id') for t, _ in hits])
    ck, lk = save_targets(keep)
    print(f'\n   🗑 已移除 {len(hits)} 个，保留 {len(keep)} 个真实目标'
          f'（云端真删 {dn if dok else "⚠️失败"}）')
    _persist_note(ck, lk)


def cmd_monitor_remove(a):
    """monitor remove：撤登记单个目标（缺省只预演，--yes 才写）。"""
    state, targets = load_targets()
    if state == 'corrupt':
        print('❌ 登记册损坏，拒绝写入（M-R60）')
        sys.exit(2)
    key = a.target
    hit = [t for t in targets
           if key in (t.get('id', ''), t.get('仓库URL', ''), t.get('仓库名', ''))
           or key in (t.get('仓库URL', '') or '')]
    if not hit:
        print(f'❌ 未匹配到目标：{key}（当前共 {len(targets)} 个）')
        sys.exit(1)
    keep = [t for t in targets if t not in hit]
    for t in hit:
        print(f'   · {t.get("id"):<10} {t.get("仓库URL")}')
    if not a.yes:
        print(f'\n   预演模式——未写入（将移除 {len(hit)}、保留 {len(keep)}）。加 --yes 执行')
        return
    dok, dn = _cloud_delete(DB_UPSTREAM, 'id', [t.get('id') for t in hit])
    ck, lk = save_targets(keep)
    print(f'   🗑 已移除 {len(hit)} 个，剩余 {len(keep)} 个'
          f'（云端真删 {dn if dok else "⚠️失败"}）')
    _persist_note(ck, lk)


# ══════════════════ 模块 B：search（搜索更多仓库） ══════════════════

def cmd_search_github(a):
    """search github：GitHub 仓库搜索（topic/keyword）。"""
    q = a.topic
    st, d = _gh_api(f'/search/repositories?q={q}+tvbox+in:name,description,readme'
                    f'&sort=updated&per_page={max(1, min(a.limit, 100))}')
    if st == 'ratelimit':
        _tok_st, _tok, _tok_src = _load_token()
        if _tok_st == 'ok':
            print('⚠️ GitHub 搜索限流——token 已配置但仍触顶（搜索 API 单独限额 30/min）'
                  '，请稍等 1 分钟后重试')
        else:
            print('⚠️ GitHub 搜索限流（未鉴权：搜索 10/min）——无法判定，请稍后；'
                  '配 GITHUB_TOKEN 可提升至 30/min（`tvbox probe token` 查看配置法）')
        sys.exit(1)
    if st != 'ok' or not isinstance(d, dict):
        print('⚠️ GitHub 搜索失败（网络/API 异常）——无法判定，不写空结果')
        sys.exit(1)
    items = d.get('items', [])
    out = _rank_repos(items, 'GitHub', q)
    _save_search(out)
    print(f'🔍 GitHub 搜索「{q}」→ {len(out)} 个候选（按更新/星标排序）')
    _print_repos(out[:a.limit])


def cmd_search_gitlab(a):
    """search gitlab：GitLab 仓库搜索（S091 新增，走 v4 search API）。

    GitLab 搜索用 `/search?scope=projects`；关键词与 GitHub 同构（tvbox）。
    """
    kw = a.keyword
    from urllib.parse import quote as _q
    st, d = _api_get('https://gitlab.com/api/v4',
                     f'/search?scope=projects&search={_q(kw)}'
                     f'&per_page={max(1, min(a.limit, 50))}')
    if st == 'ratelimit':
        print('⚠️ GitLab 搜索限流——无法判定，请稍后（匿名搜索配额较低）')
        sys.exit(1)
    if st == 'unauthorized':
        print('⚠️ GitLab 搜索需鉴权（匿名 401）——**无法判定**，'
              '请配 GitLab token 后重试（不写空结果，避免谎报"无此项目"）')
        sys.exit(1)
    if st != 'ok' or not isinstance(d, list):
        print('⚠️ GitLab 搜索失败（网络/API 异常）——无法判定，不写空结果')
        sys.exit(1)
    # 归一化为统一仓库结构（供 _rank_repos 复用）
    items = [{
        'full_name': x.get('path_with_namespace', ''),
        'html_url': x.get('web_url', ''),
        'description': x.get('description') or '',
        'stargazers_count': x.get('star_count', 0) or 0,
        'pushed_at': x.get('last_activity_at', ''),
        'default_branch': x.get('default_branch'),
    } for x in d if isinstance(x, dict)]
    out = _rank_repos(items, 'GitLab', kw)
    _save_search(out)
    print(f'🔍 GitLab 搜索「{kw}」→ {len(out)} 个候选')
    _print_repos(out[:a.limit])


def cmd_search_gitee(a):
    """search gitee：Gitee 仓库搜索（国内可达）。

    S094：① 改用统一 `_api_get`（原自写一套 curl，无三态、无状态码）；
    ② **区分"真·无结果"与"因未鉴权而无结果"**——Gitee 搜索 API 匿名常返回空列表，
    若不区分会谎报"Gitee 上没有 tvbox 仓库"（M-R60 折叠）。
    """
    kw = a.keyword
    from urllib.parse import quote as _q
    st, d = _api_get('https://gitee.com/api/v5',
                     f'/search/repositories?q={_q(kw)}&per_page={max(1, min(a.limit, 50))}')
    if st == 'unauthorized':
        print('⚠️ Gitee 搜索需鉴权（匿名不可用）——**无法判定**，'
              '请配 Gitee access_token 后重试（不写空结果，避免谎报"无此仓库"）')
        sys.exit(1)
    if st == 'ratelimit':
        print('⚠️ Gitee 搜索限流——无法判定，请稍后')
        sys.exit(1)
    if st != 'ok' or not isinstance(d, list):
        print('⚠️ Gitee 搜索失败（网络/API 异常）——无法判定，不写空结果')
        sys.exit(1)
    if not d:
        print(f'⚠️ Gitee 搜索「{kw}」返回**空列表**——注意：Gitee 匿名搜索常返回空，'
              f'此结果**不可信**（可能需 access_token）。如需确认请配凭据后重试。')
        # 不写空结果文件（避免下游误当"Gitee 确无结果"）
        return
    out2 = _rank_repos(d, 'Gitee', kw)
    _save_search(out2)
    print(f'🔍 Gitee 搜索「{kw}」→ {len(out2)} 个候选')
    _print_repos(out2[:a.limit])


# ── S143：候选评分模型（替代 `stars*1.0 + 新鲜度` 的污染式计分） ──────────────
# 旧公式缺陷（实证）：星标 4240 的仓库得 4300 分、星标 0 的仓库得 ~60 分，而候选册
# 里 33/50 条星标=0 → **25 条并列 60 分**，排序在前 25 名内完全失效；且"更新新鲜度"
# 是无上界的加数，一个刚推送的空壳仓库可以压过星标上千的老牌源（如 209★ 的
# github-trending 只排第 3）。根因：把"量"和"质"线性相加，量纲不可比。
#
# 新模型：**分层加权 + 逐项可解释**（M-R60：判据须自证，分数要能说清怎么来的）
#   ① 相关性 rel  —— 名字/描述命中 TVBox 生态词（tvbox/moontv/… ）或"线路源特征词"
#                    （spider/jar/api/源/接口/live…）。**这是准入项**：不相关的仓库
#                    星标再高也不该排在相关仓库前面。
#   ② 体量   mass —— log10(1+stars) 归一化，避免"一个 4k 星"碾压全场。
#   ③ 新鲜   fresh—— 90 天半衰期指数衰减（不是线性加分，永不超过 1）。
#   ④ 存活   alive—— 有 pushed_at 且能解析得 1，取不到给 0.5（无法判定≠死）。
# 权重：rel 0.40 / mass 0.25 / fresh 0.25 / alive 0.10，映射到 0–100。
# 输出附 `score_why`（各分项原文），使 "为什么排这里" 可当场复核。

_REL_STRONG = ('tvbox', 'tbox', '影视', '点播', '直播源', '线路', '线路源', '接口',
               '影视仓', '采集站', '源站')
_REL_SOFT = ('moon', 'catvod', 'spider', 'jar', 'api', 'cms', 'vod', 'play', 'm3u8',
             'm3u', 'iptv', 'epg', 'live', 'line', '直播', 'tv', 'box', 'json',
             'config', '源')
# 说明：早期列表把 `line`/`源`/`直播` 放到 STRONG，实测把 "免费节点订阅"
# （Jsnzkpg/Jsnzkpg，4240★，实为 Clash 机场节点）判成满相关 → 相关性失真。
# 现将泛词降级 SOFT，只在名字里出现**TVBox 生态专有词**时才记满。


def _rel_score(full, desc):
    """相关性 0–1：名字里强命中记满，描述里软命中记部分。"""
    t = f'{full} {desc}'.lower()
    name = (full or '').lower()
    strong = sum(1 for w in _REL_STRONG if w in t)
    soft = sum(1 for w in _REL_SOFT if w in t)
    in_name = any(w in name for w in _REL_STRONG)
    if strong == 0 and soft == 0:
        return 0.0
    if in_name:
        return 1.0                       # 名字点题：直接满相关
    # 仅描述命中：强词 0.30/个、软词 0.06/个，封顶 0.9（不到满——没在名字里点题）
    return min(0.9, strong * 0.30 + soft * 0.06)


def _score_repo(stars, pushed, rel, now=None):
    """返回 (score0_100, why_str)。四分量加权，每项 0–1。"""
    import math
    now = now if now is not None else time.time()
    mass = min(1.0, math.log10(1 + max(0, int(stars or 0))) / 3.0)   # 1000★ 即满
    fresh, alive = 0.5, 0.5                                          # 取不到→半信（无法判定）
    try:
        ts = datetime.strptime(pushed, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=timezone.utc).timestamp()
        age = max(0.0, (now - ts) / 86400.0)
        fresh = 0.5 ** (age / 90.0)      # 90 天半衰期
        alive = 1.0
    except Exception:
        pass
    s = (rel * 0.40 + mass * 0.25 + fresh * 0.25 + alive * 0.10) * 100
    why = (f'相关{rel:.2f}×.40 体量{mass:.2f}×.25 新鲜{fresh:.2f}×.25 '
           f'存活{alive:.2f}×.10')
    return round(s, 1), why


def _rank_repos(items, platform, kw=None):
    """对候选仓库打分排序（S143 重写：分层加权，逐项可解释）。

    旧实现把 `stars` 直接当分数加、再叠一个无上界的新鲜度加成 → 25 条零星期仓库
    全部并列 60 分，排序失效。现改为 `_score_repo` 四分量加权，并写入 `score_why`。

    S095：保留 `kw`（来源关键词）——写入每条候选，供 DB_CANDIDATE「来源关键词」列。
    """
    out = []
    now = time.time()
    for it in items:
        full = it.get('full_name') or it.get('path_with_namespace') or it.get('name', '')
        url = it.get('html_url') or it.get('url') or (
            f'https://gitee.com/{full}' if platform == 'Gitee' else '')
        stars = it.get('stargazers_count') or it.get('stars_count') or 0
        pushed = it.get('pushed_at') or it.get('updated_at') or ''
        desc = (it.get('description') or '')[:80]
        rel = _rel_score(full, desc)
        score, why = _score_repo(stars, pushed, rel, now)
        out.append({'full_name': full, 'url': url, 'platform': platform,
                    'stars': stars, 'pushed_at': pushed, 'desc': desc,
                    'rel': round(rel, 2), 'score': score, 'score_why': why,
                    'kw': kw or ''})
    # 排序键：分数优先；同分再用星标、再用仓库名（保证**确定性**，不靠稳定排序碰运气）
    out.sort(key=lambda x: (-x['score'], -x['stars'], x['full_name']))
    return out


def _dedup_gate(repos):
    """S199 准入去重闸（用户 2026-10-22 指令固化）：
    新仓库进候选册前，先对比**两道名单**——① 上游监测册（已准入的重复）②
    不可用仓库册（实测判死的，禁再回来）。两册都不在才放行进候选。

    返回 (放行列表, 拦截监测册列表, 拦截不可用册列表)。名单读不到时：
    监测册读不到 = 闸失效，**放行但打印告警**（不能因闸坏而停止采集）；
    不可用册读不到 = 无死名单可比，等价于空集（首次运行常态）。
    """
    gate_new, hit_mon, hit_dead = [], [], []
    try:
        _st, _tg = load_targets()
        mon_urls = {t.get('仓库URL') for t in (_tg or []) if isinstance(t, dict)}
    except Exception as _e:
        sys.stderr.write(f'  ⚠️ 去重闸：监测册读取失败（{_e}）——本轮跳过监测册比对\n')
        mon_urls = set()
    dead_fp = os.path.join(SELF_DIR, 'dead_repos.json')
    dead_urls = set()
    if os.path.exists(dead_fp):
        try:
            _d = json.load(open(dead_fp, encoding='utf-8'))
            dead_urls = {r.get('url') for r in (_d.get('repos') or [])
                         if isinstance(r, dict)}
        except Exception as _e:
            sys.stderr.write(f'  ⚠️ 去重闸：不可用册损坏（{_e}）——本轮跳过死册比对\n')
    for r in (repos or []):
        u = (r or {}).get('url')
        if not u:
            continue
        if u in dead_urls:
            hit_dead.append(u)
        elif u in mon_urls:
            hit_mon.append(u)
        else:
            gate_new.append(r)
    if hit_mon:
        print(f'   ⏭ 去重闸：{len(hit_mon)} 个已在监测册，不重复进候选册')
    if hit_dead:
        print(f'   🚫 去重闸：{len(hit_dead)} 个在不可用仓库册（实测判死），拒绝入册')
    return gate_new, hit_mon, hit_dead


def _save_search(repos, platform=None, sync_cloud=True):
    """保存搜索结果——**合并语义**（M-R60⑧ 变体：禁止空/单平台结果覆盖他平台有效结果）。

    S094 实证缺陷：原为**覆盖写**，后跑 `search gitee`（匿名空列表）把先前
    `search github` 的 5 条结果**整表覆盖成 []** → `search rank` 报"已重排 0 个候选"。
    这使"某平台搜索失败"静默销毁"另一平台的成功结果"（跨源覆盖 = 折叠的写侧形态）。
    修复：按 `url` 去重**合并**，且**空列表不覆盖已有非空**（除非显式要求清空）。

    S095：末尾同步云端 DB_CANDIDATE。`sync_cloud=False` 供**自检探针**用——
    探针须完全隔离（不得因探针写入污染真库，也不得让真库状态干扰断言）。
    """
    p = os.path.join(SELF_DIR, 'search_results.json')
    old = []
    if os.path.exists(p):
        try:
            _d = json.load(open(p, encoding='utf-8'))
            old = _d.get('repos', []) if isinstance(_d, dict) else (_d or [])
            if not isinstance(old, list):
                old = []
        except Exception as _e:
            # M-R60⑫：旧文件**损坏**≠"没有旧结果"——不得静默当空（会丢弃已积累候选）。
            # 显式告警，并保留旧文件为 .bak 备查（不销毁证据）。
            sys.stderr.write(f'  ⚠️ 旧搜索结果文件损坏（{type(_e).__name__}）——'
                             f'本次以新结果为准，但**旧候选可能丢失**，'
                             f'原文件已备份为 search_results.json.bak\n')
            try:
                os.replace(p, p + '.bak')
            except Exception:
                pass
            old = []
    # S199 准入去重闸：新结果先过「监测册+不可用册」两道名单，再谈合并
    repos, _hm, _hd = _dedup_gate(repos)
    # 合并：新结果优先（同 url 覆盖），保留旧结果中未出现的
    merged = {}
    for r in old:
        if isinstance(r, dict) and r.get('url'):
            merged[r['url']] = r
    for r in (repos or []):
        if isinstance(r, dict) and r.get('url'):
            merged[r['url']] = r
    merged_list = list(merged.values())
    json.dump({'ts': now_iso(), 'repos': merged_list},
              open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    # S095：本地存好后同步云端 DB_CANDIDATE（全量 merged 清单 upsert，幂等）
    if DB_CANDIDATE and sync_cloud:
        _cok, _na, _nu = _cloud_sync_search(merged_list)
        print(f'   ☁️ 云端搜索候选册同步：{"✅" if _cok else "⚠️失败"}'
              f'（新增 {_na} / 更新 {_nu}，共 {len(merged_list)} 条）')
    return p


def _print_repos(repos):
    for r in repos:
        sc = r.get('score')
        sct = f'{sc:6.1f}' if isinstance(sc, (int, float)) else '     —'
        print(f'   {sct} ★{r["stars"]:<5} {r["full_name"]:<36} {r["desc"][:40]}')


def cmd_search_rank(a):
    """search rank：对已保存/给定候选**重新打分**并排序（S143）。

    旧实现只做 `sorted(items, key=score)`——对**旧公式算出的分数**重排，
    60 分并列的 25 条永远并列，重排等于空转。现改为：拿原始字段（stars/pushed_at/
    desc/full_name）**按当前公式重算**，再落库。`--why` 可看每条的分项拆解。
    """
    if a.input and os.path.exists(a.input):
        d = json.load(open(a.input, encoding='utf-8'))
        items = d.get('repos', d if isinstance(d, list) else [])
    else:
        p = os.path.join(SELF_DIR, 'search_results.json')
        if not os.path.exists(p):
            print('ℹ️ 无候选（先跑 search github/gitee）')
            return
        items = json.load(open(p, encoding='utf-8')).get('repos', [])
    if not items:
        print('ℹ️ 候选为空，无需重排')
        return
    # 逐条重算（保留既有平台的 url/kw/desc 等原字段，只覆盖分数）
    ranked, changed = [], 0
    now = time.time()
    for it in items:
        rel = _rel_score(it.get('full_name', ''), it.get('desc', ''))
        sc, why = _score_repo(it.get('stars', 0), it.get('pushed_at', ''), rel, now)
        old = it.get('score')
        if not isinstance(old, (int, float)) or abs(old - sc) > 0.05:
            changed += 1
        it = dict(it)
        it.update({'rel': round(rel, 2), 'score': sc, 'score_why': why})
        ranked.append(it)
    ranked.sort(key=lambda x: (-x['score'], -x.get('stars', 0), x.get('full_name', '')))
    _save_search(ranked)
    print(f'📊 已重算并重排 {len(ranked)} 个候选（分数变化 {changed} 条）：')
    _print_repos(ranked[:20])
    if getattr(a, 'why', False):
        print('\n   同分拆解（前 5）：')
        for r in ranked[:5]:
            print(f'   {r["score"]:6.1f}  {r["full_name"]:<34} {r.get("score_why", "")}')


def cmd_search_verify(a):
    """search verify：对入围仓库做存活+含线路文件校验（去伪）。

    S094 两处修复：
      ① 存活判定改走**平台 API**（`_platform_meta`）——原用 `_http_code(仓库主页)`，
         GitHub 主页常因重定向/UA 返回 0 → **误判"无法判定"**（明明仓库好好的）；
      ② 默认分支**自动探测**（原硬编码 'main'，老仓库多为 master → 漏检配置）。
    """
    url = a.repo
    owner, repo, platform, host = _parse_repo(url)
    if not owner:
        print(f'❌ URL 无法解析：{url}')
        sys.exit(1)
    print(f'🔎 校验 {owner}/{repo}（{platform}）')
    # ① 存活：走平台 API（比裸探测主页可靠）
    meta = _platform_meta(platform, owner, repo, '', host)
    mstate = meta.get('state')
    if mstate == 'ok':
        alive, branch = '存活', (meta.get('default_branch') or 'main')
        print(f'   存活：存活（API 正常，默认分支 {branch}）')
    elif mstate == 'ratelimit':
        alive, branch = '无法判定', 'main'
        print('   存活：无法判定（API 限流）')
    elif mstate == 'notfound':
        alive, branch = '已封禁/删除', 'main'
        print('   存活：已封禁/删除（API 404）')
    else:
        alive, branch = '无法判定', 'main'
        print(f'   存活：无法判定（API {mstate}）')
    # ② 配置探测：走自动探测的分支
    #    S144·P5：**仓库存活 ≠ 文件能拉**。旧版探到「第一个 HTTP 200 且以 { 开头」就 break，
    #    于是「仓库在、但 raw 被墙/CORS/私有化/文件是 LFS 指针」这条路根本没验过——
    #    表现为 verify 说「有效可用」，monitor add 之后每次拉都失败。
    #    现在逐个候选登记三态（ok/无此文件/拉不动/非JSON），并把「拉不动」与「没有」分开。
    raw_base, cands = _find_config_path(owner, repo, branch, platform)
    found = None
    n_fail = 0
    raw_state = {}
    for c in cands:
        st, txt = _fetch(raw_base + c)
        if st == 'ok' and txt and txt.strip().startswith('{'):
            try:
                cfg = json.loads(txt)
                n_sites = len((cfg.get('sites') or []))
                n_lives = len((cfg.get('lives') or []))
                raw_state[c] = f'ok(sites={n_sites},lives={n_lives})'
                if not found:
                    found = c
                    print(f'   ✅ 含线路文件 {c}：sites={n_sites} lives={n_lives}')
            except Exception:
                raw_state[c] = '非合法JSON（疑加密/分片/LFS指针）'
                print(f'   ⚠️ 找到 {c} 但非合法 JSON（可能是加密/分片配置）')
        elif st == 'fail':
            n_fail += 1
            raw_state[c] = '拉不动'
        else:
            raw_state[c] = '无此文件'
    # raw 可达性小结：把「几个文件拉不动」显形，别让它藏在「未探测到」里
    reach = [k for k, v in raw_state.items() if v == '拉不动']
    if reach:
        print(f'   ⚠️ raw 可达性：{len(reach)}/{len(cands)} 个候选**拉不动**'
              f'（{",".join(reach[:3])}）——仓库活着但原始文件取不到，'
              f'「存活」不能推出「能用」')
    if not found:
        if n_fail == len(cands):
            print('   ⚠️ 配置探测**全部拉取失败**——无法判定（网络问题，非"无配置"）')
        else:
            print('   ⚠️ 未探测到标准线路文件（config.json 等）——可能路径不同或需鉴权')
    # 建议：存活 + 含配置 = 可纳入；存活但无配置 = 存疑；无法判定 = 不纳入但说明原因
    if alive == '存活' and found:
        advice = '有效可用，可 `tvbox probe monitor add --url {}` 纳入监测'.format(url)
    elif alive == '存活':
        advice = '存活但未探测到标准配置——可手动确认路径后再纳入'
    elif alive == '无法判定':
        advice = '存活无法判定（非"已死"）——建议稍后重试或配凭据后再判'
    else:
        advice = '已封禁/删除——不纳入'
    print(f'\n   建议：{advice}')


# ══════════════════ 模块 C：harvest（采集高质量线路） ══════════════════

# 线路四类判据 = 整包坊（tvbox-interface）单一事实源（M-R38）。
# harvest 与 split 必须同一口径，否则同一源在此算「配置」、在整包坊算「采集」，
# 下游 ingest 类目分叉。运行时按需 import（同 blk 复用模式），失败显式降级。
_IFACE_PIPE = None
_IFACE_TRIED = False


def _classify_fn():
    """取整包坊 classify_line；取不到返回 None（降级，不抛）。"""
    global _IFACE_PIPE, _IFACE_TRIED
    if not _IFACE_TRIED:
        _IFACE_TRIED = True
        try:
            _p = os.path.join(os.path.dirname(SELF_DIR), '..',
                              'tvbox-interface', 'scripts')
            _p = os.path.normpath(_p)
            if os.path.isdir(_p):
                sys.path.insert(0, _p)
                import pipeline as _iface        # noqa: F401
                if hasattr(_iface, 'classify_line'):
                    _IFACE_PIPE = _iface
        except Exception:
            _IFACE_PIPE = None
    return _IFACE_PIPE


def _extract_parses(cfg, repo=''):
    """从配置对象抽 `parses` → 解析候选（S173）。

    为什么单独抽出来：`_extract_lines` 把 sites/lives/parses 混在一个列表里，
    后两者靠 `_kind` 区分，但**下游 filter 一律走 `_alive_line`**——
    那是给「能直接出播放地址的源」用的判据，parses 是「给个影片页才出流」的服务，
    拿同一把尺子量必然全判死。所以采集侧必须把 parses 单独拎成一条链。

    实测（cluntop/tvbox box.json）：21 条 parses，HTTP 全通，真出流仅 3 条。
    故本函数**只负责抽，不负责判**——判定交 `livecheck --kind parse`（M-R38）。
    """
    out = []
    for it in (cfg.get('parses') or []):
        if not isinstance(it, dict):
            continue
        u = str(it.get('url') or it.get('api') or '').strip()
        if not u or not u.startswith(('http://', 'https://')):
            continue
        out.append({
            '名称': str(it.get('name') or it.get('title') or '')[:60] or u[:60],
            'api接口': u,
            '来源包': repo,
            '解析类型': it.get('type'),        # TVBox 的 type：0=网页 1=json 4=混合
            'kind': 'parse',
        })
    return out


def cmd_harvest_parse(a):
    """harvest parse：从**别人已发布的包**里采解析接口 → 真实出流闸门 → 候选落盘。

    ★ 这是"解析库"的上游。解析库不是凭空攒的，是从别人的包里捞出来的——
      任何一份 TVBox 配置都可能带 parses，而维护者本人都没验过它们是否还活着。

    三道闸（缺一道就是垃圾入库）：
      ① **抽取**：只认 parses 数组里的 http(s) 地址；name 空则用 url 兜底。
      ② **去重**：按 host+path 指纹去重（同名不同址、同址不同名都算重复）。
      ③ **真实出流**：`livecheck --kind parse` —— 只认"真跑到流并拉到分片"。
         实测 21 条全 HTTP 可通，真出流只有 3 条；只看 200 会收进 18 条垃圾。

    产出 `parse_candidates_<MMDD>.json`，**不入库**（入库是 addto 的事，
    且解析库的库坐标另行确认——M-R109 建库不可逆）。
    """
    srcs = []
    if a.url:
        srcs.append((a.url, ''))
    if a.repo:
        owner, repo, platform, host = _parse_repo(a.repo)
        if not owner:
            print(f'❌ URL 无法解析：{a.repo}')
            sys.exit(1)
        branch = a.branch or _detect_default_branch(owner, repo, platform, host) or 'main'
        raw_base, cands = _find_config_path(owner, repo, branch, platform)
        for c in cands:
            srcs.append((raw_base + c, f'{owner}/{repo}'))

    if not srcs:
        print('用法：tvbox probe harvest parse --url <配置地址> | --repo <仓库URL>')
        return 1

    picked = None
    for u, tag in srcs:
        st, txt = _fetch(u)
        if st != 'ok' or not txt or not txt.strip().startswith('{'):
            continue
        try:
            cfg = json.loads(txt)
        except Exception:
            continue
        got = _extract_parses(cfg, tag or u)
        if got and (picked is None or len(got) > len(picked[0])):
            picked = (got, u)
    if not picked:
        print('ℹ️ 这些配置里**没有 parses 数组**（多数包只带 sites/lives）——不是失败，是这类包本来就不含解析')
        return 0

    items, src = picked
    print(f'📦 来源 {src}')
    print(f'🔍 抽到解析 {len(items)} 条（抽 ≠ 能用，下面逐条真验）')

    # ② 去重：host+path 指纹（查询串常带 token，算进去会让同一服务重复收款）
    seen, uniq = set(), []
    for it in items:
        sp = urlparse(it['api接口'])
        fp = f'{(sp.netloc or "").lower()}{sp.path}'
        if fp in seen:
            continue
        seen.add(fp)
        uniq.append(it)
    if len(uniq) != len(items):
        print(f'   去重：{len(items)} → {len(uniq)}（同 host+path 视为同一服务）')

    # ③ 真实出流闸门
    # ★ 串行不并发：每次 check_parse 都要起一个 chromium（约 2-4s 启动 + 12s 等出流），
    #   并发会同时开 N 个浏览器把内存打满，反而更慢。串行还能逐条打印进度，
    #   20 条一批约 4 分钟——可接受。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import livecheck
    ok, bad, unk = [], [], []
    for n, it in enumerate(uniq, 1):
        v, ev = livecheck.check_parse(it['api接口'], wait_s=a.wait)
        it['真实出流'], it['出流证据'] = v, ev
        mark = {'通过': '✅', '失败': '❌'}.get(v, '⚠️')
        print(f'   [{n:2d}/{len(uniq)}] {mark} {it["名称"][:12]:14s} {ev[:66]}', flush=True)
        (ok if v == '通过' else (bad if v == '失败' else unk)).append(it)

    print(f'   ✅ 真出流 {len(ok)} / ❌ 不出流 {len(bad)} / ⚠️ 无法判定 {len(unk)}')
    for it in ok:
        print(f'   ✅ {it["名称"][:14]:16s} {it["出流证据"][:76]}')
    for it in bad[:8]:
        print(f'   ❌ {it["名称"][:14]:16s} {it["出流证据"][:76]}')
    for it in unk[:8]:
        print(f'   ⚠️ {it["名称"][:14]:16s} {it["出流证据"][:76]}')

    outp = os.path.join(HARVEST_DIR, f'parse_candidates_{time.strftime("%m%d")}.json')
    json.dump({'来源': src, '生成时间': time.strftime('%Y-%m-%d %H:%M'),
               '通过': ok, '不出流': bad, '无法判定': unk},
              open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'💾 候选落盘：{outp}')
    print('   ⚠️ 未入库。入库需解析库坐标（M-R109 建库不可逆），先确认再 addto。')
    return 0


def _extract_lines(cfg):
    """从配置对象抽取线路 → 候选列表（contracts candidate-list 格式）。

    **类目判据复用整包坊 classify_line**（M-R38 单一事实源）；若整包坊不可用
    则**显式降级**为本地保守判据（并在 stderr 提示），绝不静默用错口径。
    """
    iface = _classify_fn()
    if iface is None:
        sys.stderr.write('  ⚠️ 整包坊判据不可用——harvest 类目降级为保守判据'
                         '（py/采集判定缺失，统一归「其他」），与 split 口径可能分叉\n')
    out = []
    for kind in ('sites', 'lives', 'parses'):
        arr = cfg.get(kind) or []
        if not isinstance(arr, list):
            continue
        for it in arr:
            if not isinstance(it, dict):
                continue
            api = str(it.get('api') or it.get('url') or '').strip()
            if not api:
                continue
            name = str(it.get('name') or it.get('title') or '')[:80] or api[:80]
            cat = iface.classify_line(kind, it) if iface else _fallback_category(kind, it)
            out.append({
                'api接口': api,
                '名称': name,
                '分组类目': cat,
                '_kind': kind,
            })
    return out


def _fallback_category(kind, item):
    """整包坊不可用时的保守判据（仅用 type，不做 api 形态识别）。"""
    if kind == 'lives':
        return '直播'
    if kind == 'parses':
        return '其他'
    return '直链' if item.get('type') == 0 else '其他'



def cmd_harvest_scan(a):
    """harvest scan：遍历仓库内配置文件，抽取线路候选。"""
    url = a.repo
    owner, repo, platform, host = _parse_repo(url)
    if not owner:
        print(f'❌ URL 无法解析：{url}')
        sys.exit(1)
    # S092：默认分支**自动探测**（同 monitor 口径）——硬编码 'main' 会让老仓库
    # （多为 master）raw 全 404 → 谎报"未找到线路文件"（S088 在 monitor 侧修过的同型缺陷）
    if a.branch:
        branch = a.branch
    else:
        branch = _detect_default_branch(owner, repo, platform, host) or 'main'
    raw_base, cands = _find_config_path(owner, repo, branch, platform)
    if a.path:
        cands = [f'{a.path.rstrip("/")}/config.json'] + cands
    found_cfg = None
    n_notfound, n_fail = 0, 0
    # ★ S148 修缺陷：原来**命中第一个合法 json 就 break**，采到的是"碰巧第一个能解析的"，
    #   不是"最全的"。实测 `cluntop/tvbox`：fun.json 127 / jsm.json 114 / box.json 129 / aa.json 55
    #   ——条数差 2.3 倍，采到哪个纯看谁先返回。这不是"选优"，是**碰运气**。
    #   改为：全部候选都取一遍，按线路条数取最多；平手时取路径更浅的（主配置通常在根）。
    _cand_hits = []        # [(n_lines, path, cfg)]
    _empty = 0
    for c in cands:
        st, txt = _fetch(raw_base + c)
        if st == 'ok' and txt and txt.strip().startswith('{'):
            try:
                _cfg = json.loads(txt)
            except Exception:
                # 拉到了但非合法 JSON——不是"网络失败"，是内容问题，继续试下一候选
                n_notfound += 1
                continue
            _n = len(_extract_lines(_cfg))
            if not _n:
                _empty += 1          # 合法 json 但没线路 → 不是失败，也不该占名额
                continue
            _cand_hits.append((_n, c, _cfg))
        elif st == 'fail':
            n_fail += 1
        else:
            n_notfound += 1
    # S148：约定名候选与目录树候选**一起比**——约定名命中不代表它就是最全的。
    # （实测 box.json=129 是全仓最多，但 fun.json=127 / jsm.json=114 紧随其后；
    #   换一个仓库，"最全的"完全可能不叫 box.json，而叫 fun.json。）
    _tried = list(cands)          # 实际试过的候选（约定名 + 目录树发现的）
    _disc_total = 0
    if not a.path:
        # ★ S148 修：**不再只试前 12 个**。原 limit=12 在候选 40 个时直接截断，
        #   于是"取最多"这个结论本身就不成立——截断掉的那些里可能就有最全的。
        #   「只看前 N 个然后宣布最全」= 用不完整的样本下完整的结论（M-R60 同型错误）。
        #   改为**全量试**，并用并发把耗时压回可接受范围（候选多时串行会 RTT × 40）。
        _budget = 60          # 候选上限（防病态仓库拖垮整条命令），超了要显式说
        _disc_r = _discover_configs(owner, repo, branch, platform, host, limit=_budget)
        _disc, _disc_total = _disc_r if isinstance(_disc_r, tuple) else (_disc_r or [], 0)
        if _disc:
            _cut = _disc_total > len(_disc)
            print(f'   🔎 目录树另发现 {_disc_total} 个 json，全部比对'
                  + (f'（超出安全上限 {_budget}，只试前 {len(_disc)} 个'
                     f'——**最全者可能未入选**）' if _cut else ''))
            for c in _disc:
                _tried.append(c)

            def _probe_one(_c):
                """拉一个候选并数线路。返回 ('hit', n, path, cfg) / ('empty',) /
                ('notjson',) / ('fail',)。**在子线程里跑，必须把异常收干净**——
                子线程抛异常不会传到主线程，会静默变成"没结果"。"""
                try:
                    _st, _txt = _fetch(raw_base + _c)
                except Exception:
                    return ('fail',)
                if _st == 'ok' and _txt and _txt.strip().startswith('{'):
                    try:
                        _cfg = json.loads(_txt)
                    except Exception:
                        return ('notjson',)
                    _n = len(_extract_lines(_cfg))
                    return ('hit', _n, _c, _cfg) if _n else ('empty',)
                return ('fail',) if _st == 'fail' else ('notjson',)

            # ★ S163/S111：改走统一入口（原为直吃 --workers 的 ThreadPoolExecutor，
            #   绕过档位限流）。keep_order 必须开——下面按 _rs 顺序累计命中数。
            from concurrency import run_bounded, TIER_WEB
            _rep = run_bounded(_disc, _probe_one, tier=TIER_WEB,
                               label='harvest-scan-disc', keep_order=True, on_error='record')
            _rs = [(_r if _r is not None else ('fail',)) for _r in _rep.results]
            for _r in _rs:
                if _r[0] == 'hit':
                    _cand_hits.append((_r[1], _r[2], _r[3]))
                elif _r[0] == 'empty':
                    _empty += 1
                elif _r[0] == 'fail':
                    n_fail += 1
                else:
                    n_notfound += 1
        elif _disc_r is None:
            print('   ⚠️ 目录树取不到 → 本次只在 4 个约定名里比，**"最全"结论不完整**')
    n_tried = len(_tried)
    if _cand_hits:
        # 取线路最多；平手取路径浅（段数少）且字典序靠前的，保证**可复现**（不随返回顺序漂移）
        _cand_hits.sort(key=lambda x: (-x[0], x[1].count('/'), x[1]))
        _best_n, _best_path, _best_cfg = _cand_hits[0]
        found_cfg = (_best_path, _best_cfg)
        if len(_cand_hits) > 1:
            _top = _cand_hits[:5]
            print(f'   🔍 候选配置 {len(_cand_hits)} 个有线路，取最多者：'
                  + ' / '.join(f'{p}={n}' for n, p, _ in _top))
        print(f'   ✅ 命中 {_best_path}（{_best_n} 条线路）')
    elif not found_cfg:
        print(f'   已试 {n_tried} 个候选：{_empty} 个是合法 json 但无 lines，'
              f'{n_notfound} 个不是 json，{n_fail} 个拉取失败')
    if not found_cfg:
        # M-R60 三态命门：**"网络失败"≠"仓库无此文件"**——不得都折叠成"未找到"。
        # 若全部候选都失败，则本次**无法判定**（不是"作者没配置"），必须显式区分。
        if n_fail == n_tried:
            print(f'⚠️ 无法判定：{owner}/{repo} 的全部候选配置均**拉取失败**（网络/服务问题）'
                  f'——本次采集**无结论**，请勿据此认为"仓库无线路文件"')
            sys.exit(2)
        if n_fail:
            print(f'⚠️ 部分候选拉取失败（{n_fail}/{n_tried}）——'
                  f'已试尽可得候选但未命中配置，结论**不完整**（存在网络不确定）')
        print(f'⚠️ 未在 {owner}/{repo} 找到可解析的线路文件——无法采集'
              f'（分支={branch}，共试 {n_tried} 个候选）')
        sys.exit(1)
    cpath, cfg = found_cfg
    lines = _extract_lines(cfg)
    print(f'📥 采集 {owner}/{repo}（{cpath}）→ {len(lines)} 条线路候选')
    if not lines:
        print('   ℹ️ 文件可解析但无 sites/lives/parses 线路')
        return
    outp = os.path.join(HARVEST_DIR, f'raw_{repo}_{time.strftime("%m%d")}.json')
    for _l in lines:
        _l['来源仓库'] = f'{owner}/{repo}'      # S095：供 DB_HARVEST「来源仓库」列
    json.dump(lines, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    from collections import Counter
    print('   类型分布：' + ' '.join(f'{k}:{v}' for k, v in
                                  Counter(x['_kind'] for x in lines).items()))
    cok, n_add, n_upd = _cloud_sync_harvest(lines)   # S095：同步 DB_HARVEST
    if DB_HARVEST:
        print(f'   ☁️ 云端采集候选册同步：{"✅" if cok else "⚠️失败"}'
              f'（新增 {n_add} / 更新 {n_upd}）')
    print(f'   已存：{outp}')
    print(f'   下一步：tvbox probe harvest filter --in {outp}')


def cmd_harvest_pull(a):
    """harvest pull：拉取单文件并抽取线路。"""
    st, txt = _fetch(a.url)
    if st != 'ok' or not txt:
        print(f'⚠️ 拉取失败（网络异常）——无法采集：{a.url}')
        sys.exit(1)
    try:
        cfg = json.loads(txt)
    except Exception as e:
        print(f'⚠️ 非合法 JSON（{type(e).__name__}）——无法采集')
        sys.exit(1)
    lines = [x for x in _extract_lines(cfg) if x['_kind'] == a.kind] if a.kind \
        else _extract_lines(cfg)
    _src = _parse_repo(a.url)[0]
    _src = f'{_src}/{_parse_repo(a.url)[1]}' if _src else a.url
    for _l in lines:
        _l['来源仓库'] = _src
    outp = os.path.join(HARVEST_DIR, f'pull_{time.strftime("%m%d")}.json')
    json.dump(lines, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    cok, n_add, n_upd = _cloud_sync_harvest(lines)   # S095：同步 DB_HARVEST
    if DB_HARVEST:
        print(f'   ☁️ 云端采集候选册同步：{"✅" if cok else "⚠️失败"}'
              f'（新增 {n_add} / 更新 {n_upd}）')
    print(f'📥 拉取并抽取 {len(lines)} 条线路 → {outp}')


def source_fp(api):
    """源指纹（S144·黑名单主键；S145 补取法变体归一）。

    为什么不能用 host 当黑名单主键（2026-10-17 用真实数据打脸）：
      · 成品源库 122 个 host 中，4 个 host 被旧失效库判「整域全灭」，但成品库里
        同 host 的源实测通过（apilj.com / aosikazy.com / m3u8.apiyhzy.com）。
        按 host 拉黑 = 一条出事、整个域名陪葬。
      · 同一个源有多种写法：`/provide/vod`、`/provide/vod/at/json`、
        `/provide/vod/?ac=list` 会被当成 3 条不同记录，一条通过两条判死，互相矛盾。

    ★ S145 实测补漏：旧口径只处理 scheme/www/尾斜杠/query，**漏了 CMS 站的取法变体**。
      成品源库 191 条里，`cj.lziapi.com` 被存成 6 条、`api.guangsuapi.com` 6 条、
      `360zy.com` 5 条…… 191 条其实只有 **107 个 host**，另有 84 条是同一站的重复。
      实测三家变体（v1.71s 那次）返回 `44 类 / 159587 条` vs `44 类 / 159578 条` 完全同源。

    归一化（按「去掉不改语义的部分」逐条）：
      1. scheme 忽略（http/https 同源）
      2. host 小写、去端口、去 www
      3. path **去尾部取法段**：`/at/json`、`/at/josn`、`/at/xml`（输出格式，非站点标识）
      4. path **去 `/from/<源名>` 段**（指定播放源，同一站的不同镜像）
      5. path 去尾斜杠、折叠连续斜杠（`provide//vod` → `provide/vod`）
      6. 丢 query（ac=list / ac=videolist / at=json 只是取法）与 fragment

    >>> source_fp('https://M3U8.apiyhzy.com/api.php/provide/vod/at/xml/?ac=list')
    'm3u8.apiyhzy.com/api.php/provide/vod'
    >>> source_fp('https://cj.lziapi.com/api.php/provide/vod/from/lzm3u8/')
    'cj.lziapi.com/api.php/provide/vod'
    """
    a = (api or '').strip()
    if not a:
        return ''
    # ★S169 脏 URL 前置校验：实测失信库里有一条
    #   `https://https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u`
    #   （协议头重复）。原归一化把它切成 host='https'、path='/raw.githubusercontent.com/…'，
    #   内层剥代理的分支又要求 `m.start() > 0` 而 `://` 正好在 0 位 → 不触发 →
    #   于是产出指纹 `https/raw.githubusercontent.com/…`，**host 位置混进了协议名**。
    #   这种指纹既能被写进黑名单，又永远匹配不到真实 api（真实 api 归一成
    #   `raw.githubusercontent.com/…`），等于一条无效记录占着 L1 名额还骗过体检。
    #   这里显式标出来：返回带 `!脏URL` 前缀的指纹，调用方（mark）据此拒写。
    if re.match(r'^https?://https?://', a, flags=re.I) or \
            re.match(r'^https?://(https?|ftp)://', a, flags=re.I):
        return '!脏URL:' + a.split('://', 2)[2][:60].lower()
    try:
        s = urlsplit(a if '//' in a else '//' + a)
    except Exception:
        return a.lower()
    host = (s.netloc or '').lower().split(':')[0]
    if host.startswith('www.'):
        host = host[4:]
    path = s.path or ''

    # ⑧ 剥代理前缀（S146）：形如 `git.yylx.win/https://raw.githubusercontent.com/...`
    #    外层只是"转发器"，真正的源是内层 URL。不剥则同一条线路因走不同代理被判成两条。
    #    外层域名不入指纹——否则换个代理就变成"另一条线路"。
    m = re.search(r'https?://', path, flags=re.I)
    if m and m.start() > 0:
        inner = path[m.start():]
        try:
            si = urlsplit(inner)
            if si.netloc:
                return source_fp(inner)      # 用内层 URL 重新归一
        except Exception:
            pass

    # ⑦ URL 解码（S146）：中文路径与 %XX 编码路径是同一资源
    #    实测 play.iptv365.org/天天开心/... 与 /%E5%A4%A9%E5%A4%A9.../... sha256 完全相同。
    path = unquote(path)

    # ④ 去 /from/<源名> 段（可能出现在中段或末尾）
    path = re.sub(r'/from/[^/]+', '', path, flags=re.I)
    # ③ 去 /at/<格式> 段（json/josn/xml 都是输出格式，不是站点）
    path = re.sub(r'/at/[^/]+', '', path, flags=re.I)
    # ⑤ 折叠连续斜杠 + 去尾斜杠
    path = re.sub(r'/{2,}', '/', path).rstrip('/')
    return (host + path) if host else a.lower()


def _alive_line(api, timeout=6):
    """线路测活（S097 升级为**真实可用**口径·M-R38 单一事实源）。

    旧口径仅判 HTTP 码（可达≠能播），用户拿来放进 json 会转圈。
    新口径复用 `livecheck.py`：点播真拉 vod_play_url / 直播真拉分片字节流。
    返回 (ok, code)；ok 仅当 verdict=='通过'（三态：无法判定**不算通过**）。

    ★S169 证书类补一道复验：证书过期会让 curl 在校验阶段就失败，
    真实内容根本没被看到。实测 ryzy.tv / lz.118318.xyz / taopianapi.com
    三条证书过期源，跳过校验后都能拉到真实播放地址与分片（92KB）。
    若不复验，`pardon` 永远无法平反这类误杀——因为"实测不通过"这一关就过不去，
    而它们其实一直活着。故：**初次判定为无法判定且原因是证书类**时，
    再做一次跳校验复验；通过的把证据带 `【跳过证书校验】` 前缀返回，
    调用方（如 pardon）可据此平反，同时看得见"这是跳校验得到的结论"。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import livecheck
        v, ev, kind = livecheck.check_auto(api, timeout=max(timeout, 10))
        if v != '通过' and livecheck._last_err_kind in livecheck.CERT_KINDS:
            v2, ev2, _ = livecheck.check_auto_ignore_cert(api, timeout=max(timeout, 15))
            if v2 == '通过':
                return True, f'证书类失败({livecheck._last_err_kind})→{ev2}'
            return False, f'{ev}；复验: {ev2}'
        return (v == '通过'), ev
    except Exception as e:
        # 引擎异常不可折叠成"通过"，降级回 HTTP 码判（并显式标注）
        rc, out = _sh([CURL, '-s', '-o', '/dev/null', '-w', '%{http_code}',
                       '-m', str(timeout), '-A', UA, '-r', '0-300', api], timeout=timeout + 3)
        try:
            code = int(out.strip().splitlines()[-1])
        except Exception:
            code = 0
        return False, f'引擎异常({type(e).__name__})降级HTTP {code}'


def cmd_harvest_repack(a):
    """harvest repack：从**已有 TVBox 包**里抽线路 + 真测活 + 判能否跨包平移（S142）。

    病根（2026-10-17 溯源）：用户 10-14 说「存活直链能加进我其他包么」「松视那两条加进l包」，
    这个动作当年是我**临时写 python 脚本**干的——抽 type=0、逐条测活、查 key 冲突、
    判 jar 相对路径能否平移。做完就散在对话里，没固化成命令 → 换会话就重来。

    本命令把那条动线固化，四件事一次做完：
      ① 抽：按 `--kind` 抽 sites/lives/parses；默认只抽**可跨包平移**的（无 jar / jar 为绝对 URL）
      ② 测：逐条走 `livecheck` 真测活（JSON/XML/m3u8/py 四形态全覆盖）
      ③ 判：标出不可平移的原因（相对路径 jar / spider 依赖 / csp 死壳）
      ④ 查：给了 `--target` 就做 key 冲突检查，直接告诉你"能不能加、加进去叫什么"

    用法：
      tvbox probe harvest repack --pkg <包.json> [--kind sites] [--target <目标包.json>]
                                 [--only-live] [--out <产物.json>]
    """
    pkg_path = a.pkg
    if not os.path.exists(pkg_path):
        print(f'❌ 包文件不存在：{pkg_path}')
        sys.exit(1)
    try:
        raw = open(pkg_path, encoding='utf-8', errors='replace').read()
        cfg = json.loads('\n'.join(l for l in raw.split('\n')
                                   if not l.strip().startswith('//')))
    except Exception as e:
        print(f'❌ 包不是合法 json（{type(e).__name__}）——无法采集')
        sys.exit(1)
    if isinstance(cfg, list):
        cfg = {'sites': cfg}

    spider = str(cfg.get('spider') or '')
    print(f'📦 源包：{os.path.basename(pkg_path)}')
    print(f'   spider：{spider or "(无)"}'
          f'{"  ← 相对路径，整包不可远程加载" if spider and not spider.startswith("http") else ""}')

    kinds = [a.kind] if a.kind else ['sites', 'lives', 'parses']
    cands = []
    for k in kinds:
        for it in (cfg.get(k) or []):
            if not isinstance(it, dict):
                continue
            api = str(it.get('api') or it.get('url') or '').strip()
            if not api:
                continue
            jar = str(it.get('jar') or '').strip()
            # 可平移判据：api 是远程 http(s)，且不依赖本地相对路径 jar
            remote = api.startswith(('http://', 'https://'))
            jar_rel = bool(jar) and not jar.startswith(('http://', 'https://'))
            portable = remote and not jar_rel
            reason = ''
            if not remote:
                reason = 'api 非远程地址（本地/类名）'
            elif jar_rel:
                reason = f'依赖本地相对 jar（{jar}）→ 跨包不可用'
            # S142：`parses` 是**解析器**不是线路（不能当源加进包），单列出来别混进统计
            if k == 'parses':
                reason = reason or '解析器（parses）非线路源，不参与加包'
                portable = False
            cands.append({
                '_kind': k,
                'api接口': api,
                '名称': str(it.get('name') or it.get('title') or api[:60]),
                'key': str(it.get('key') or ''),
                'type': it.get('type'),
                '可平移': portable,
                '不可平移原因': reason,
                '原站': it,
            })
    # S144·P6：结论行必须回答「几条能用」，不是「抽出几条」。
    # 旧文案「抽出 219 条」被误解成「有 219 条可用」，而真正可用的要等测活后才出现——
    # 抽出的条数是**分母**，可用条数才是**答案**。分母冒充答案是最容易骗过自己的错位。
    print(f'   抽出 {len(cands)} 条（{"sites/lives/parses" if not a.kind else a.kind}）'
          f'｜结构可平移 {sum(1 for c in cands if c["可平移"])} 条'
          f'（⚠️ 这只是分母，可用数要等第③步测活）')

    if not cands:
        print('ℹ️ 该包无线路条目')
        return

    # ② 真测活（并发）——先给全量填默认"跳过"，再只对 todo 打网络
    for c in cands:
        c['判类'] = '-'
        c['测活'] = '未测'
        c['测活证据'] = '（parses 解析器不做线路测活）' if c['_kind'] == 'parses' else ''

    todo = [c for c in cands if c['可平移'] and c['_kind'] != 'parses'] if a.only_live \
        else [c for c in cands if c['_kind'] != 'parses']
    def probe1(c):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import livecheck
            v, ev, kd = livecheck.check_auto(c['api接口'], timeout=15)
            return c, v, ev, kd
        except Exception as e:
            return c, '无法判定', f'引擎异常({type(e).__name__})', '?'
    # ★ S163/S111：改走统一入口。**keep_order 必须开**——下面 pmap 按 id(c) 回填，
    #   但 probed 的顺序还决定了后续对 cands 的遍历打印顺序；更关键的是原 ex.map 天然保序。
    from concurrency import run_bounded, TIER_WEB, ConcurrencyError
    try:
        _rep = run_bounded(todo, probe1, tier=TIER_WEB, workers=getattr(a, 'workers', None),
                           label='repack-probe', keep_order=True, on_error='record')
    except ConcurrencyError as _e:
        print(f'❌ --workers 超限：{_e}')
        return 2
    probed = [(_r if _r is not None else (c, '无法判定', '探测线程异常（并发报告已记录）', '?'))
              for c, _r in zip(todo, _rep.results)]
    pmap = {id(c): (v, ev, kd) for c, v, ev, kd in probed}
    for c in cands:
        v, ev, kd = pmap.get(id(c), ('未测', '（--only-live 或 parses 跳过）', '-'))
        c['判类'] = kd
        c['测活'] = '跳过' if v == '未测' else v
        c['测活证据'] = ev

    ok = [c for c in cands if c['测活'] == '通过' and c['可平移']]
    ng = [c for c in cands if c['测活'] == '失败']
    un = [c for c in cands if c['测活'] == '无法判定']
    sk = [c for c in cands if c['测活'] == '跳过']

    print(f'\n🧪 真实测活：✅通过 {len(ok)}（且可跨包平移）| ❌失败 {len(ng)} | '
          f'⚠️无法判定 {len(un)} | ⏭跳过 {len(sk)}（不可平移/解析器/*未测）')
    from collections import Counter
    print(f'   判类分布：' + ' '.join(f'{k}:{v}' for k, v in
          Counter(c['判类'] for c in cands).items()))
    for c in ok:
        print(f'   ✅ [{c["判类"]:5}] {c["名称"][:26]:26} {c["测活证据"][:58]}')
    for c in ng[:15]:
        print(f'   ❌ [{c["判类"]:5}] {c["名称"][:26]:26} {c["测活证据"][:58]}')
    for c in un[:10]:
        print(f'   ⚠️ [{c["判类"]:5}] {c["名称"][:26]:26} {c["测活证据"][:58]}')
    blocked = [c for c in cands if not c['可平移']]
    if blocked:
        print(f'   🚫 不可平移 {len(blocked)} 条（不静默丢，逐条给原因）：')
        for c in blocked[:10]:
            print(f'      {c["名称"][:24]:24} {c["不可平移原因"]}')

    # ④ key 冲突检查（给了目标包才做）
    if a.target:
        if not os.path.exists(a.target):
            print(f'\n⚠️ 目标包不存在：{a.target}（跳过 key 冲突检查）')
        else:
            try:
                rawt = open(a.target, encoding='utf-8', errors='replace').read()
                tcfg = json.loads('\n'.join(l for l in rawt.split('\n')
                                            if not l.strip().startswith('//')))
                if isinstance(tcfg, list):
                    tcfg = {'sites': tcfg}
                tkeys = {str(s.get('key') or '') for s in (tcfg.get('sites') or [])
                         if isinstance(s, dict)}
                tapis = {str(s.get('api') or '') for s in (tcfg.get('sites') or [])
                         if isinstance(s, dict)}
                conflict = [c for c in ok if (c['key'] and c['key'] in tkeys)
                            or c['api接口'] in tapis]
                print(f'\n🎯 目标包：{os.path.basename(a.target)}'
                      f'（现有 sites {len(tcfg.get("sites") or [])} 个）')
                print(f'   可加 {len(ok) - len(conflict)} 条 → 加后 '
                      f'{len(tcfg.get("sites") or []) + len(ok) - len(conflict)} 个')
                if conflict:
                    print(f'   ⚠️ key/api 冲突 {len(conflict)} 条（需改名或跳过）：')
                    for c in conflict:
                        print(f'      {c["名称"][:24]:24} key={c["key"]}')
            except Exception as e:
                print(f'\n⚠️ 目标包解析失败（{type(e).__name__}）——跳过冲突检查')

    # 落盘（只有可平移且测活通过的才进候选）
    out = a.out or os.path.join(HARVEST_DIR, f'repack_{time.strftime("%m%d_%H%M")}.json')
    export = [{'api接口': c['api接口'], '名称': c['名称'], '分组类目':
               ('直播' if c['判类'] == 'm3u8' else
                ('直链' if (c['type'] == 0 and c['_kind'] == 'sites') else '采集')),
               '判类': c['判类'], '测活证据': c['测活证据'],
               '_kind': c['_kind'], 'key': c['key'], 'type': c['type'],
               '可平移': c['可平移'], '不可平移原因': c['不可平移原因'],
               '来源包': os.path.basename(pkg_path)} for c in cands]
    json.dump(export, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    # S144·P6 收口：一句话结论（可用 = 可平移 且 测活通过）
    print(f'\n✅ 结论：这个包共 {len(cands)} 条，其中 **{len(ok)} 条可用**'
          f'（可平移且真实测活通过）｜失败 {len(ng)} ｜无法判定 {len(un)} ｜跳过 {len(sk)}')
    print(f'\n💾 全量逐条结论（含失败/不可平移，可追溯）：{out}')
    # S144·P7：回读核对「抽出的条目字段没被压扁」——原站 dict 里除 api/name 之外的
    # 兄弟字段（jar/playerType/ua/ext/…）必须原样带到 export，否则"加进别的包"就丢配置。
    try:
        back = json.load(open(out, encoding='utf-8'))
        by_api = {str(x.get('api接口')): x for x in back}
        shrink = 0
        for c in cands:
            e = by_api.get(c['api接口'])
            if not e:
                continue          # 去重合并属正常
            lost_f = [f for f in c['原站'] if f not in (e.get('原站') or {})]
            if lost_f:
                shrink += 1
                if shrink <= 5:
                    print(f'  ⚠️ `{c["名称"][:20]}` 原站字段未带全：{lost_f}')
        if shrink:
            print(f'  🚨 {shrink} 条抽出的原站配置不完整（跨包加会丢字段）——请勿直接下发')
            sys.exit(1)
        print(f'  ✅ 原生态校验通过（{len(cands)} 条原站配置整条带出，字段无损）')
    except SystemExit:
        raise
    except Exception as e:
        print(f'  ⚠️ 原生态校验读回失败（{type(e).__name__}）——产物未验证')

    print(f'   下一步：tvbox probe harvest promote --in {out}'
          f'（只把测活通过的写进成品源库）')


def _jsm_group_of(name, api):
    """jsm 分组类目——**调用源枢库曹的正本**，不在探源司复制一份（S147）。

    正本：`tvbox-source-hub/scripts/split_jsm.py::group_of`（它才是 jsm 拆包的正主）。
    这里只做「找正本 → import → 调用」，找不到时**显式报错**，不悄悄降级成另一套管法——
    两套判据各走各的，同一源在两处归进不同分组，正是 S146 刚收拾过的「同一条线路两个分组」。
    """
    cands = [
        os.path.join(os.path.dirname(os.path.dirname(SELF_DIR)),
                     'tvbox-source-hub', 'scripts'),
        os.path.expanduser('~/.codebuddy/skills/tvbox-source-hub/scripts'),
    ]
    for d in cands:
        if os.path.isfile(os.path.join(d, 'split_jsm.py')):
            if d not in sys.path:
                sys.path.insert(0, d)
            try:
                import split_jsm
                return split_jsm.group_of(name, api), ''
            except Exception as e:
                return '', f'正本 import 失败（{type(e).__name__}: {e}）'
    return '', f'未找到 split_jsm.py（试过 {len(cands)} 处）'


def cmd_decrypt(a):
    """decrypt：把「看不出是什么」的响应体变成「看得懂」的（S148·吸收遮天破甲模块）。

    为什么探源司需要这个（不是"顺手加个工具"）：
    采集时最耗时的不是拉取，是**面对一堆乱码判断"这是加密？是报错？还是我拉错了地址"**。
    没有这一层，只能把乱码当"拉取成功但内容奇怪"——采出来的源一条都用不了，还说不出原因。
    本命令把判断过程**外显成结论**：每种形态 + 每种候选解密 + 每个信号的依据都打印出来。

    五项能力（均已在 `armor.self_test` 里用向量自证，非"能跑就算"）：
    - `shape`  响应体形态分诊（json/hls/html-page/xml-doc/gzip/base64-*/hex-*/url-encoded/binary）
               注意 **html-page 必须单列**：被墙/被 Cloudflare 拦都返回 HTML，混进 text 会被误当配置
    - `chain`  源码里的变换链信号（按出现顺序），补齐 JS 侧 `atob`/`JSON.parse`/`decodeURIComponent`
    - `aes`    AES-CBC 解密 + **严格** PKCS7（填充非法即失败，不返回半成品）
    - `img`    图片 XOR 还原（按魔数反推密钥，还原后校验魔数）
    - `m3u8`   m3u8 代理重写（KEY 与分片都转绝对地址）
    """
    # ★ S194 修：经 tvbox 入口分发时 sys.path 不带本目录 → No module named 'armor'
    #   （同款病已在 2683/3016/3951/6032 行修过，decrypt 入口漏修——实测复现 exit 1）
    _d = os.path.dirname(os.path.abspath(__file__))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    import armor as _am
    if a.selftest:
        _e = _am.self_test()
        return 0 if not _e else 1

    if a.file:
        try:
            with open(a.file, 'rb') as f:
                body = f.read()
        except Exception as e:
            print(f'❌ 读不到文件 {a.file}：{type(e).__name__}: {e}')
            return 2
    elif a.url:
        st, body_s = _fetch(a.url, timeout=a.timeout)
        if st != 'ok' or not body_s:
            # M-R60：拉不到 ≠ 内容为空 ≠ 没有这个东西
            print(f'❌ 拉取失败（state={st}）——**无法判定**内容形态，不是"内容异常"')
            print(f'   若该站需要特定 UA/Referer，先用 harvest pull --url 拿本地文件再 --file 分析')
            return 2
        body = body_s.encode('utf-8', 'surrogateescape')
    elif a.aes_data:
        body = b''            # 只给十六进制密文时，无需输入文件
    else:
        print('❌ 需要 --file / --url / --aes-data 之一')
        return 2

    if body:
        print(f'📦 输入 {len(body)} 字节')
    if a.chain:
        src = body.decode('utf-8', 'replace')
        sigs = _am.analyze_transform_chain(src)
        print(f'🔗 变换链信号（按出现顺序）：{sigs if sigs else "[]（未识别到已知变换）"}')
        if not sigs:
            print('   ⚠️ 空信号 ≠ 没有加密：可能用了非标写法（自定义函数名/打包器）。'
                  '别据此断定"明文"，应实测解一次看结果')
        return 0

    cls = _am.classify_response_body(body, a.content_type or '')
    print(f'🧬 形态：{cls["shape"]}'
          + (f'（content-type: {cls["content_type"]}）' if cls.get('content_type') else ''))
    _shape = cls['shape']
    if _shape == 'html-page':
        print('   ⚠️ HTML 页面：**通常说明没拿到数据**（被墙/被拦/地址错/需要 Referer）。')
        print('      不要当"文本配置"往下走——先查 HTTP 状态与是否需要 UA/Cookie。')
    elif _shape == 'text':
        print('   纯文本：既非结构化也未识别出编码。可能是明文配置，也可能是**非标加密**——'
              '运行 --chain 看信号。')

    if a.aes_key:
        key = a.aes_key.encode()
        iv = (a.aes_iv or '').encode()
        try:
            data = bytes.fromhex(a.aes_data) if a.aes_data else body
        except ValueError:
            print('❌ --aes-data 不是合法十六进制串')
            return 2
        out = _am.aes_cbc_decrypt_and_unpad(key, iv or b'\x00' * 16, data)
        if out is None:
            print('❌ AES 解密失败：密钥/IV 不对，或填充非法。')
            print('   **严格判定**：填充非法一律判失败，不返回"大概对"的半成品'
                  '（错误的半成品比报错更危险——会被当成正常数据用下去）')
            return 1
        print(f'✅ AES 解密成功，{len(out)} 字节：')
        try:
            print(out.decode('utf-8')[:800])
        except UnicodeDecodeError:
            print(f'   （非 UTF-8）hex: {out[:120].hex()}')
        return 0

    if a.img:
        mime, dec, was = _am.xor_decrypt_image(body)
        if was and mime:
            print(f'✅ 图片 XOR 还原成功：{mime}，{len(dec)} 字节（还原后魔数校验通过）')
            if a.out:
                with open(a.out, 'wb') as f:
                    f.write(dec)
                print(f'   已写出：{a.out}')
        else:
            print('❌ 未识别为 XOR 加密图片（魔数反推失败）')
        return 0

    if a.m3u8 and a.base:
        txt = body.decode('utf-8', 'replace')
        out = _am.rewrite_m3u8_proxy(txt, a.base, a.proxy or 'http://127.0.0.1:9978/proxy?url=')
        print('✅ m3u8 已重写为绝对代理地址：')
        print(out[:1200])
        return 0
    return 0


def cmd_harvest_splitjsm(a):
    """harvest splitjsm：把 jsm 聚合配置拆成线路候选，并**按 api 形态分诊**（S147）。

    病根（2026-10-17 溯源）：SKILL.md §4 扩展点「harvest 深度抽取（jsm 拆包复用
    tvbox-interface 的 split）」挂了一轮又一轮没做。老的 `split_jsm.py` 是**模块级执行**
    的一次性脚本（写死 /tmp/jsm.json → /tmp/to_add.json），既不接受参数也不区分形态——
    实测 A1015 包 164 条里有 150 条是 csp/drpy/本地相对路径，**根本不是可采集的外链**，
    却被一并当成候选产物丢给下游。分母冒充答案，就是本条要修的错位。

    与 repack 的分工：repack 管「从 TVBox 包里抽已配好的线路」，本命令管「从 jsm 这种
    **聚合配置清单**里拆出候选」——后者条目是 `api` 形态混杂的原始声明，需要先分诊。

    用法：
      tvbox probe harvest splitjsm --pkg <jsm配置.json> [--out <产物.json>]
                                   [--no-probe] [--keep-nonhttp] [--push]
    """
    pkg_path = a.pkg
    if not os.path.exists(pkg_path):
        print(f'❌ 配置不存在：{pkg_path}')
        sys.exit(1)
    # M-R60 三态：读不到 ≠ 空文件 ≠ 有内容。读失败直接失败退出，不当成「0 条」。
    try:
        raw = open(pkg_path, encoding='utf-8-sig', errors='replace').read()
    except Exception as e:
        print(f'🚫 读取失败（{type(e).__name__}: {e}）——无法判定该配置是否为空，本轮不产出')
        sys.exit(1)
    try:
        cfg = json.loads('\n'.join(l for l in raw.split('\n')
                                  if not l.strip().startswith('//')))
    except Exception as e:
        print(f'❌ 不是合法 json（{type(e).__name__}: {e}）')
        sys.exit(1)
    if isinstance(cfg, list):
        cfg = {'sites': cfg}
    if not isinstance(cfg, dict):
        print(f'❌ 顶层既不是 object 也不是 array（是 {type(cfg).__name__}）')
        sys.exit(1)

    n_sites = len(cfg.get('sites') or [])
    n_lives = len(cfg.get('lives') or [])
    print(f'📦 配置：{os.path.basename(pkg_path)}')
    print(f'   sites {n_sites} 条 | lives {n_lives} 条')

    # ① 拆：走正本 split_jsm.split_pkg（纯函数，不读写磁盘）
    _g, _gerr = _jsm_group_of('', '')
    if _gerr:
        print(f'🚫 取不到分组正本：{_gerr}')
        print('   本命令**不复制**一份分组表凑合用——两套判据会把同一源归进两个分组')
        sys.exit(1)
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import split_jsm
        cands = split_jsm.split_pkg(cfg)
    except Exception as e:
        print(f'🚫 拆包失败（{type(e).__name__}: {e}）')
        sys.exit(1)

    print(f'   拆出 {len(cands)} 条（去重后；⚠️ 这是**分母**，可用数看下面分诊）')
    if not cands:
        print('ℹ️ 该配置无线路条目（sites/lives 均为空或全缺 api）')
        return

    # ② 分诊：形态走 _lj_api_kind（与 repack/livecheck 同一口径）
    KIND_WHY = {
        'csp': 'csp_ 集成解析（壳内配置，非可采集外链）',
        'py': 'py 脚本源（需配套 py 壳，不能单独当外链采集）',
        'js': 'js/drpy 脚本源（需配套 js 壳或本地 lib）',
        'local': '本地相对路径（./ 开头，离开原包即失效）',
        'other': '非 http 的其它形态（类名/空壳）',
    }
    for c in cands:
        k = _lj_api_kind(c['api接口'])
        c['形态'] = k
        c['可采集'] = (k == 'http')
        c['不可采集原因'] = '' if k == 'http' else KIND_WHY.get(k, f'形态 {k}')

    ok = [c for c in cands if c['可采集']]
    ng = [c for c in cands if not c['可采集']]
    print(f'\n🔬 分诊：✅ 可采集外链 {len(ok)} 条 | 🚫 非外链 {len(ng)} 条'
          f'（{"合计 " + str(len(cands)) if len(ok) + len(ng) == len(cands) else "⚠️ 两边加起来对不上总数"}）')
    from collections import Counter as _C
    print('   非外链形态分布：' + ' '.join(f'{k}:{v}' for k, v in
          _C(c['形态'] for c in ng).most_common()))
    print('   可采集分组分布：' + ' '.join(f'{k}:{v}' for k, v in
          _C(c['分组类目'] for c in ok).most_common()))

    # ③ 真测活（可选）：只测可采集的，跳过的显式写「未测」不写「通过」
    for c in cands:
        c['测活'] = '未测'
        c['测活证据'] = '（非外链，不做线路测活）' if not c['可采集'] else ''
    if a.no_probe:
        for c in ok:
            c['测活证据'] = '（--no-probe 跳过）'
        print('\n🧪 测活：已按 --no-probe 跳过（本轮**不主张**任何一条可用）')
    elif ok:
        print(f'\n🧪 真实测活：{len(ok)} 条外链（并发 {a.workers}）…')

        def _p1(c):
            try:
                import livecheck
                v, ev, kd = livecheck.check_auto(c['api接口'], timeout=15)
                return c, v, ev, kd
            except Exception as e:
                return c, '无法判定', f'引擎异常({type(e).__name__})', '?'
        from concurrency import run_bounded, TIER_WEB, ConcurrencyError
        try:
            _rep = run_bounded(ok, _p1, tier=TIER_WEB, workers=getattr(a, 'workers', None),
                               label='splitjsm-probe', keep_order=True, on_error='record')
        except ConcurrencyError as _e:
            print(f'❌ --workers 超限：{_e}')
            return 2
        for c, _r in zip(ok, _rep.results):
            if _r is None:
                # 线程异常 → 无法判定，不装成通过也不装成不通过（M-R60）
                c['测活'], c['测活证据'] = '无法判定', '探测线程异常（并发报告已记录）'
            else:
                c, v, ev, kd = _r
                c['测活'], c['测活证据'] = v, ev
        p_ok = [c for c in ok if c['测活'] == '通过']
        p_ng = [c for c in ok if c['测活'] == '失败']
        p_un = [c for c in ok if c['测活'] == '无法判定']
        print(f'   ✅通过 {len(p_ok)} | ❌失败 {len(p_ng)} | ⚠️无法判定 {len(p_un)}')
        for c in p_ok:
            print(f'   ✅ {c["名称"][:24]:24} {c["测活证据"][:56]}')
        for c in p_ng[:10]:
            print(f'   ❌ {c["名称"][:24]:24} {c["测活证据"][:56]}')

    if ng and not a.keep_nonhttp:
        print(f'\n🚫 非外链 {len(ng)} 条**不进产物**（逐条原因见产物 `全部条目` 与下方清单）：')
        for c in ng[:12]:
            print(f'   [{c["形态"]:5}] {c["名称"][:22]:22} {c["不可采集原因"]}')
        if len(ng) > 12:
            print(f'   …另 {len(ng) - 12} 条同类，完整清单见产物')

    # ④ 落盘：默认只写可采集的（下游能直接吃）；全量逐条结论另存 `全部条目`，不静默丢
    out = a.out or os.path.join(HARVEST_DIR, f'splitjsm_{time.strftime("%m%d_%H%M")}.json')
    payload = [c for c in cands if c['可采集'] or a.keep_nonhttp]
    export = [{'api接口': c['api接口'], '名称': c['名称'], '分组类目': c['分组类目'],
               '形态': c['形态'], '可采集': c['可采集'],
               '不可采集原因': c['不可采集原因'],
               '测活': c['测活'], '测活证据': c['测活证据'],
               '来源配置': os.path.basename(pkg_path)} for c in payload]
    doc = {
        '来源配置': os.path.basename(pkg_path),
        '总数': len(cands),
        '可采集': len(ok),
        '非外链': len(ng),
        '候选': export,
        '全部条目': [{'api接口': c['api接口'], '名称': c['名称'], '分组类目': c['分组类目'],
                     '形态': c['形态'], '可采集': c['可采集'],
                     '不可采集原因': c['不可采集原因'],
                     '测活': c['测活'], '测活证据': c['测活证据']} for c in cands],
    }
    try:
        json.dump(doc, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'🚫 产物写入失败（{type(e).__name__}: {e}）')
        sys.exit(1)

    # ⑤ 回读核对（M-R101 同源教训：脚本跑完没报错 ≠ 结果对）
    try:
        back = json.load(open(out, encoding='utf-8'))
        if back.get('总数') != len(cands):
            print(f'  🚨 回读总数不符：写前 {len(cands)} / 读回 {back.get("总数")}')
            sys.exit(1)
        if len(back.get('全部条目') or []) != len(cands):
            print(f'  🚨 回读全部条目数不符：{len(back.get("全部条目") or [])} != {len(cands)}')
            sys.exit(1)
        if len(ok) + len(ng) != len(cands):
            print(f'  🚨 分诊计数对不上：{len(ok)} + {len(ng)} != {len(cands)}')
            sys.exit(1)
        print(f'  ✅ 回读核对通过（{len(cands)} 条，分诊 {len(ok)}+{len(ng)} 无遗漏）')
    except SystemExit:
        raise
    except Exception as e:
        print(f'  ⚠️ 回读核对失败（{type(e).__name__}）——产物未验证')

    # ⑥ 可选推 DB_HARVEST
    if a.push:
        try:
            cok, n_add, n_upd = _cloud_sync_harvest(export)
            print(f'   {"✅" if cok else "⚠️"} DB_HARVEST 同步：新增 {n_add} / 更新 {n_upd}')
        except Exception as e:
            print(f'   ⚠️ DB_HARVEST 同步失败（{type(e).__name__}: {e}）')

    # S144·P6 收口：先说「几条能用」，再说「抽了几条」
    # M-R60：没测 ≠ 测了不通过。`--no-probe` 下报「通过 0 条」是把「未测」冒充成「已判死」，
    # 用户会以为这批外链全废了，实际只是没验。
    n_pass = sum(1 for c in ok if c['测活'] == '通过')
    if a.no_probe:
        _v = '未测（--no-probe）'
    else:
        _v = f'真实测活通过 {n_pass} 条'
    print(f'\n✅ 结论：这个配置共 {len(cands)} 条 → **可采集外链 {len(ok)} 条'
          f'（{_v}）**，其余 {len(ng)} 条是 csp/py/js/本地，拆包拆不出外链')
    print(f'💾 全量逐条结论：{out}')
    if n_pass:
        print(f'   下一步：tvbox probe harvest promote --in {out}（只把测活通过的写进成品源库）')
    elif ok and not a.no_probe:
        print('   ℹ️ 本轮无测活通过项——**不要**拿去入库；先看上面失败原因')


def load_blacklist(verbose=True):
    """读失信 host 库 → 三层黑名单（S144）。

    三层按**证据强度**分级，不按 host：
      L1 精确黑  fingerprint 连续失败 ≥2 次且两次间隔 ≥1 天 → 直接丢弃，不测活
      L2 观察    fingerprint 失败 1 次（未达 L1）        → 拦下强制测活，过则撤销
      L3 域名预警 同 host 下 ≥3 个不同 fingerprint 全 L1  → **只降权，不丢弃**

    R1 平反优先：判死状态=已平反 的记录一律不进拦截集
    R2 活源免疫：成品库里存在且实测通过的 fingerprint，禁止写入 L1

    返回 dict(l1=set, l2=set, l3host=set, rows=list)。
    """
    L1, L2, L3, rows = set(), set(), set(), []
    try:
        rows = rows_of(DB_DEAD)
    except Exception as e:
        if verbose:
            print(f'  ⚠️ 失信库不可读（{type(e).__name__}）——本次跳过黑名单（不误判为"已筛"）')
        return {'l1': L1, 'l2': L2, 'l3host': L3, 'rows': []}

    for r in rows:
        if r.get('判死状态') == '已平反':
            continue
        f = (r.get('源指纹') or '').strip() or source_fp(r.get('样例api') or '')
        lvl = (r.get('拉黑层级') or '').strip()
        # 兼容无层级字段的旧记录：整域→仅入 L3 预警，单条→按次数定
        if not lvl:
            lvl = 'L2观察' if str(r.get('拉黑范围', '')).startswith('单条') else 'L3域名预警'
        if lvl.startswith('L1') and f:
            L1.add(f)
        elif lvl.startswith('L2') and f:
            L2.add(f)
        elif lvl.startswith('L3'):
            h = (r.get('host') or '').strip()
            if h:
                L3.add(h)
    # L3 规则：同 host 下 ≥3 个不同 fingerprint 全 L1 才算域名预警
    byhost = {}
    for f in L1:
        h = f.split('/')[0]
        byhost.setdefault(h, set()).add(f)
    L3 = {h for h, fs in byhost.items() if len(fs) >= 3} | L3
    if verbose:
        print(f'  🚫 黑名单：L1 精确黑 {len(L1)} 个指纹 / L2 观察 {len(L2)} 个 / '
              f'L3 域名预警 {len(L3)} 个（仅降权）')
    return {'l1': L1, 'l2': L2, 'l3host': L3, 'rows': rows}


def cmd_harvest_filter(a):
    """harvest filter：测活 + 去重 + 黑名单预筛。不静默丢弃，逐条给结论。"""
    inp = a.input or _latest_harvest()
    if not inp or not os.path.exists(inp):
        print('ℹ️ 无候选文件（先跑 harvest scan/pull）')
        return
    items = json.load(open(inp, encoding='utf-8'))
    if isinstance(items, dict):
        items = items.get('records') or items.get('items') or []
    print(f'🧪 过滤 {len(items)} 条候选（测活 + 去重 + 黑名单预筛）')
    # S144：三层黑名单（L1 丢弃 / L2 转测活 / L3 只降权），主键是源指纹不是 host
    bl = load_blacklist()
    L1, L2, L3H = bl['l1'], bl['l2'], bl['l3host']

    keep, drop = [], []
    # 并发真实测活（S097：点播拉片/直播拉流，非仅HTTP码）
    def probe1(it):
        return it, _alive_line(it['api接口'], timeout=12)
    from concurrency import run_bounded, TIER_WEB
    _rep = run_bounded(items, probe1, tier=TIER_WEB, label='filter-probe',
                       keep_order=True, on_error='record')
    results = [(_r if _r is not None else (it, (False, '无法判定：探测线程异常（并发报告已记录）')))
               for it, _r in zip(items, _rep.results)]
    seen_fp = set()
    for it, (ok, ev) in results:
        api = it['api接口']
        f = source_fp(api)
        if f in seen_fp:
            drop.append((api, '重复(指纹相同)'))
            continue
        seen_fp.add(f)
        # L1 精确黑：直接丢弃，不测活（最省事也最狠的一档）
        if f in L1:
            drop.append((api, 'L1精确黑命中'))
            continue
        # L2 观察：**不直接丢**，走真实测活；通过则放行（.见下方 keep）
        if not ok:
            tag = 'L2观察·测活仍失败' if f in L2 else f'真实测活未通过({ev[:40]})'
            drop.append((api, tag))
            continue
        obj = {k: it[k] for k in ('api接口', '名称', '分组类目',
                                  'sourceUrl', 'mirror', 'ua', 'size') if k in it}
        obj['测活证据'] = ev
        if f in L2:
            obj['平反'] = True          # 标记：L2 观察项实测通过 → 待撤销黑名单
        if (urlparse(api).netloc or '').lower() in L3H:
            obj['降权'] = True          # L3 域名预警：仍收录，但排序靠后
        keep.append(obj)
    # L3 降权：收录的源里，host 命中预警的排到队尾（不丢弃）
    if any(o.get('降权') for o in keep):
        keep.sort(key=lambda o: 1 if o.get('降权') else 0)
        n_l3 = sum(1 for o in keep if o.get('降权'))
        print(f'  ⚠️ L3 域名预警降权 {n_l3} 条（已收录，排到队尾）')
    n_fp = sum(1 for o in keep if o.get('平反'))
    if n_fp:
        print(f'  ♻️ L2 观察项实测通过 {n_fp} 条（待撤销黑名单，见 filter 输出的 平反=true）')
    print(f'  ✅ 保留 {len(keep)} 条 | 🚫 剔除 {len(drop)} 条')
    from collections import Counter
    for reason, n in Counter(r for _, r in drop).most_common():
        print(f'     剔除原因 {reason}: {n}')
    outp = inp.replace('.json', '_filtered.json')
    json.dump(keep, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    # S095：回写云端 DB_HARVEST 测活状态（保留→存活；剔除→失效/无法判定）
    # 点⑥：接入多生态分诊闸——越界（漫画/书源/音源）不进 DB_HARVEST，留痕不静默丢
    _crows = []
    _oob = []
    for it, (ok, ev) in results:
        _api = it.get('api接口')
        if not _api:
            continue
        enter, reason = can_enter_harvest(it)
        if not enter:
            _oob.append({'reason': reason, 'candidate': it})
            continue
        _crows.append({
            'api接口': _api, '名称': it.get('名称', ''),
            '分组类目': it.get('分组类目', '其他'),
            '来源仓库': it.get('来源仓库', ''),
            '生态类别': it.get('生态类别', ''),
            '测活状态': '存活' if ok else '失效',
            '入库状态': '待入库',
        })
    if _oob:
        _record_outofscope(_oob)
        print(f'  🚧 生态越界 {len(_oob)} 条（已留痕 harvest_outofscope.json，不进 DB_HARVEST）')
    cok, n_add, n_upd = _cloud_sync_harvest(_crows)
    if DB_HARVEST:
        print(f'   ☁️ 云端测活状态回写：{"✅" if cok else "⚠️失败"}'
              f'（新增 {n_add} / 更新 {n_upd}）')
    print(f'   已存：{outp}')
    print(f'   下一步：tvbox probe harvest export --in {outp}')


def _latest_harvest():
    if not os.path.isdir(HARVEST_DIR):
        return None
    fs = [os.path.join(HARVEST_DIR, f) for f in os.listdir(HARVEST_DIR)
          if f.endswith('.json') and '_filtered' in f]
    if not fs:
        fs = [os.path.join(HARVEST_DIR, f) for f in os.listdir(HARVEST_DIR)
              if f.endswith('.json')]
    return max(fs, key=os.path.getmtime) if fs else None


def cmd_harvest_export(a):
    """harvest export：导出为 contracts candidate-list 格式，供 ingest 消费。"""
    inp = a.input or _latest_harvest()
    if not inp or not os.path.exists(inp):
        print('ℹ️ 无候选（先跑 harvest filter）')
        return
    items = json.load(open(inp, encoding='utf-8'))
    if isinstance(items, dict):
        items = items.get('records') or items.get('items') or []
    clean = [{k: it[k] for k in ('api接口', '名称', '分组类目',
                                 'sourceUrl', 'mirror', 'ua', 'size') if k in it}
             for it in items if it.get('api接口')]
    if not clean:
        print('⚠️ 无有效候选（缺 api接口 必填字段）')
        return
    outp = a.out or os.path.join(HARVEST_DIR, f'harvest_{time.strftime("%m%d")}.json')
    json.dump(clean, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'📤 导出 {len(clean)} 条候选（candidate-list 格式）→ {outp}')
    print(f'   下一步：tvbox probe harvest dispatch（交 ingest 入库，走 blk guard 第一道闸）')


def cmd_harvest_dispatch(a):
    """harvest dispatch：把 export 产物交给 tvbox ingest 入库（不绕过闸门）。"""
    inp = a.input or _latest_harvest()
    if not inp or os.path.exists(inp) is False or 'harvest_' not in os.path.basename(inp):
        # 必须是 export 产物
        outs = [os.path.join(HARVEST_DIR, f) for f in os.listdir(HARVEST_DIR)
                if f.startswith('harvest_') and f.endswith('.json')] \
            if os.path.isdir(HARVEST_DIR) else []
        if not outs:
            print('ℹ️ 无 export 产物（先跑 harvest export）')
            return
        inp = max(outs, key=os.path.getmtime)
    print(f'🚚 交 ingest 入库：{inp}')
    print('   经 blk.py guard（第一道闸）→ 放行→源总库 / 拦截→失效库')
    _cmd = [sys.executable, os.path.join(HUB, 'link.py'),
            'ingest', '--candidates', inp]
    if getattr(a, 'dry', False):
        _cmd.append('--dry')
        print('   ℹ️ --dry 演示模式：只校验契约与闸门，不写入源总库')
    rc, out = _sh(_cmd, timeout=180)
    if rc != 0:
        print(f'⚠️ ingest 执行失败（rc={rc}）：\n{out[-500:]}')
        sys.exit(1)
    print(out[-800:] if out else '（ingest 无输出）')
    if not getattr(a, 'dry', False):
        print('   ℹ️ 收工别忘了：tvbox snapshot 刷新权威快照（规则47）')


def cmd_search_cloud(a):
    """search cloud：查看云端搜索候选册（DB_CANDIDATE，S095）。"""
    if not DB_CANDIDATE:
        print('ℹ️ 未配置搜索候选库（DB_CANDIDATE 为空）')
        return
    st, rows = _cloud_rows(DB_CANDIDATE)
    if st == 'fail':
        print('⚠️ 云端搜索候选册不可达（未授权/网络异常）——**无法判定**，'
              '请稍后重试（勿据此认为"无候选"）')
        sys.exit(1)
    if st == 'unset':
        print('ℹ️ 未配置搜索候选库')
        return
    if not rows:
        print('ℹ️ 云端搜索候选册为空（先跑 tvbox probe search github/gitee/gitlab）')
        return
    rows = sorted(rows, key=lambda r: _cloud_num(r.get('评分')), reverse=True)
    lim = getattr(a, 'limit', 30) or 30
    print(f'☁️ 云端搜索候选册（DB_CANDIDATE）共 {len(rows)} 条，显示前 {min(lim, len(rows))}：\n')
    print(f'   {"平台":<8}{"仓库名":<38}{"星标":<7}{"状态":<8}来源关键词')
    print('   ' + '─' * 72)
    for r in rows[:lim]:
        _star = _cloud_num(r.get('星标'))
        _star = int(_star) if _star == int(_star) else _star
        print(f'   {str(_cloud_txt(r.get("平台"))):<8}'
              f'{str(_cloud_txt(r.get("仓库名")))[:36]:<38}'
              f'{str(_star):<7}'
              f'{str(_cloud_txt(r.get("状态"))):<8}'
              f'{_cloud_txt(r.get("来源关键词"))}')
    print('\n   ℹ️ 这些是**跨会话持久**的搜索候选（本地 search_results.json 会随沙箱重置丢失）')


def cmd_harvest_prune(a):
    """harvest prune：拉作者原包 → 逐条真实测活 → 剔除失效 → 产出「备用」版（S097）。

    用户明确要求（2026-10-16）：
      "我要用作者原仓库做我自己的 json，所以要保证这个仓库是活的、是我可以远程拉取的，
       找到之后还要看里面的线路源是否是活的，在作者原包的基础上把失效线路删了等我备用。"

    三步（全部真实，非仅HTTP码）：
      ① 仓库活体校验：拉取作者原包 URL，确认可远程拉取且为合法 JSON。
      ② 逐条真实测活：复用 livecheck（点播拉 vod_play_url / 直播拉分片）。
      ③ 剔除失效 → 产出备用版：保留 sites/lives/parses 中"通过"的，
         dead 列表逐条列出（不静默丢弃，M-R32），落盘 `<作者包名>_备用_<MMDD>.json`。
    三态：无法判定的**不保留也不标死**——单列"待复测"，避免网络抖动误删好源。
    """
    st, txt = _fetch(a.url)
    if st != 'ok' or not txt:
        print(f'❌ ① 仓库不可远程拉取（{st}）——不可用：{a.url}')
        sys.exit(1)
    try:
        cfg = json.loads(txt)
    except Exception as e:
        print(f'❌ ① 非合法 JSON（{type(e).__name__}）——不可用（可能是错误页/HTML）')
        sys.exit(1)
    owner, repo, platform, host = _parse_repo(a.url)
    src = f'{owner}/{repo}' if owner else a.url
    n_s = len(cfg.get('sites') or [])
    n_l = len(cfg.get('lives') or [])
    n_p = len(cfg.get('parses') or [])
    print(f'✅ ① 仓库活体：可远程拉取｜{src}｜sites {n_s} / lives {n_l} / parses {n_p}')

    # ② 逐条真实测活（三态）
    lines = _extract_lines(cfg)
    print(f'🔎 ② 逐条真实测活 {len(lines)} 条（点播拉片/直播拉流）...')
    def _probe(l):
        _u = l.get('api接口') or l.get('api') or l.get('url') or ''
        return l, _alive_line(_u, timeout=12)
    from concurrency import run_bounded, TIER_WEB
    _rep = run_bounded(lines, _probe, tier=TIER_WEB, label='export-probe',
                       keep_order=True, on_error='record')
    results = [(_r if _r is not None else (l, (False, '无法判定：探测线程异常（并发报告已记录）')))
               for l, _r in zip(lines, _rep.results)]
    passed, dead, unknown = [], [], []
    for l, (ok, ev) in results:
        rec = dict(l)
        rec['测活证据'] = str(ev)[:200]
        if ok:
            passed.append(rec)
        elif '无法判定' in str(ev) or '超时' in str(ev):
            unknown.append(rec)
        else:
            dead.append(rec)
    print(f'   通过 {len(passed)} / 失败 {len(dead)} / 无法判定 {len(unknown)}')

    # ③ 产出备用版（剔除失效；无法判定单列不删）
    #
    # S144·P8 修 bug：旧 `_rebuild` 只认 `passed` 的 api → **"无法判定"的条目被一起删掉了**，
    # 与 docstring 承诺的「无法判定不保留也不标死，单列待复测」直接矛盾。
    # 网络抖动一次就误删好源，是不可逆损失。改为：保留 = 通过 ∪ 无法判定，仅删明确失效。
    keep_set = {x.get('api接口') or x.get('api') or x.get('url') for x in passed}
    keep_set |= {x.get('api接口') or x.get('api') or x.get('url') for x in unknown}

    def _rebuild(kind_key):
        return [s for s in (cfg.get(kind_key) or [])
                if str(s.get('api') or s.get('url') or '') in keep_set]

    backup = dict(cfg)
    backup['sites'] = _rebuild('sites')
    backup['lives'] = _rebuild('lives')
    backup['parses'] = _rebuild('parses')
    stamp = time.strftime('%m%d')
    base = os.path.splitext(os.path.basename(a.url.split('?')[0]))[0] or '作者原包'
    outdir = a.outdir or HARVEST_DIR
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, f'{base}_备用_{stamp}.json')
    json.dump(backup, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    # 剔除清单（逐条给结论，不静默）
    rep = os.path.join(outdir, f'{base}_剔除清单_{stamp}.json')
    json.dump({'剔除(明确失效)': dead, '待复测(无法判定)': unknown},
              open(rep, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'✅ ③ 备用版已产出（失效已剔除）：{out}')
    print(f'   · 保留 sites {len(backup["sites"])} / lives {len(backup["lives"])} / parses {len(backup["parses"])}'
          f'（含待复测 {len(unknown)} 条，未判死不删）')
    print(f'   · 剔除清单：{rep}')
    # S144·P7：`_rebuild` 只按 api 白名单重建三段 → 极易把 spider/自定义兄弟键丢掉。
    # 落盘后回读比对：顶层键 + 保留条目的字段集合都不能少。
    # `mutable`：sites/lives/parses 三段**本来就要裁剪**，值必然变——
    #   但「变」不等于「丢」，字段集合仍须完整。不声明 mutable 会把正常裁剪判成篡改（假红）。
    try:
        _back = json.load(open(out, encoding='utf-8'))
        _ok, _reds, _ = check_preserve(cfg, _back, mutable=('sites', 'lives', 'parses'))
        if not _ok:
            print(f'  ❌ 备用版丢失原包字段（{out}）——请勿直接下发，先修透传')
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f'  ⚠️ 原生态校验读回失败（{type(e).__name__}）——备用版未验证')
    # S144·P8：留存批次自动复核——"产出的文件"必须能读回、条数对得上、
    # 且**每一条保留条目都真的在原包里存在**（防 _rebuild 的 api 比对写成宽松匹配而误留）。
    try:
        _chk = json.load(open(out, encoding='utf-8'))
        _bad = []
        for _k in ('sites', 'lives', 'parses'):
            _orig = {str(s.get('api') or s.get('url') or '') for s in (cfg.get(_k) or [])}
            for s in (_chk.get(_k) or []):
                _a = str(s.get('api') or s.get('url') or '')
                if _a not in _orig:
                    _bad.append(f'{_k}:{_a[:40]}')
            _dead_apis = {str(x.get('api接口') or x.get('api') or x.get('url') or '') for x in dead}
            _leaked = [s for s in (_chk.get(_k) or [])
                       if str(s.get('api') or s.get('url') or '') in _dead_apis]
            if _leaked:
                _bad.append(f'{_k} 混入已判死 {len(_leaked)} 条')
        if _bad:
            print(f'  🚨 留存批次复核失败：{_bad[:5]}')
            sys.exit(1)
        _expect = {_k: len([s for s in (cfg.get(_k) or [])
                            if str(s.get('api') or s.get('url') or '') in keep_set])
                   for _k in ('sites', 'lives', 'parses')}
        _actual = {_k: len(_chk.get(_k) or []) for _k in ('sites', 'lives', 'parses')}
        if _expect != _actual:
            print(f'  🚨 留存批次条数对不上：期望 {_expect} 实得 {_actual}')
            sys.exit(1)
        print(f'  ✅ 留存批次复核通过（{_actual} 全部可在原包溯源，无判死条目混入）')
    except SystemExit:
        raise
    except Exception as e:
        print(f'  ⚠️ 留存批次复核读回失败（{type(e).__name__}）')


    for d in dead[:15]:
        print(f'      ❌ 剔除 {d.get("name", "")[:26]:26} {d.get("测活证据", "")}')
    for u in unknown[:10]:
        print(f'      ⚠️ 待复测 {u.get("name", "")[:24]:24} {u.get("测活证据", "")}')
    print('   ℹ️ 下一步：tvbox probe harvest promote --in <候选>  把通过者写入成品源库')
    # ★ 生态联动（S106）：剔除的失效候选/废弃中间产物 → 投递清道夫；
    #   上游侦察发现新失效模式 → 登记监察司风险面（下轮 scan 自动接手）。
    try:
        if dead:
            # S156 修：原在这里投递「剔失清单 json」——那是**本地文件**，不是云端节点。
            #   清道夫只收云端节点；本地文件 AI 自己能删，且这份清单**要留着可还原**
            #   （剔失清单是回滚依据），本就不该清。故不投递，改打印它的落盘位置。
            # ★ S158 修：原写 `{out_json}`，而本函数里根本没有这个变量（真名是 `rep`）
            #   → 每次走到这里必抛 NameError，又被外层 `except` 吞成
            #   「生态门面联动跳过」，于是**剔失清单路径从来没被打印过**，
            #   而人只看到"联动跳过"，以为只是门面没连上。改用真变量名。
            print(f'      📄 剔失清单（保留·可还原）：{rep}')
        register_monitor('上游新失效模式', 'naming_compliance',
                         '上游仓库出现新停更原因/新仓库类型（原监测四信号未覆盖）时，监测册接手跟踪')
    except Exception as e:
        print(f'  ⚠️ 生态门面联动跳过（{type(e).__name__}）：备用版已产出，不受影响')


def cmd_quality_one(a):
    """C8（T6）：S110 单条质量评测入口。实现全在 quality.py，薄封装（M-R38 单一实现）。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import quality
    quality.main(a.url)
    return 0


def cmd_quality_batch_run(a):
    """C8（T6）：S110 批量质量评测回填两库入口。实现全在 quality_batch.py，薄封装。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import quality_batch
    argv = ['--db', a.db, '--rounds', str(a.rounds),
            '--timeout', str(a.timeout), '--workers', str(a.workers)]
    if getattr(a, 'limit', 0):
        argv += ['--limit', str(a.limit)]
    if getattr(a, 'only_pass', False):
        argv.append('--only-pass')
    if getattr(a, 'dry', False):
        argv.append('--dry')
    return quality_batch.main(argv)


def cmd_content_probe_run(a):
    """C8（T6）：内容层探查入口。实现全在 content_probe.py，薄封装。

    原仓库配置缺失时 content_probe.main 自带守卫（打印警告优雅返回），不崩。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import content_probe
    content_probe.main()
    return 0


def cmd_livecheck(a):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import livecheck
    if getattr(a, 'spider', None):
        import spider_verify
        return spider_verify.cmd_spider(a)
    if a.batch:
        return livecheck.cmd_batch(a)
    if a.url:
        return livecheck.cmd_one(a)
    print('用法：tvbox probe livecheck <url> [--kind vod|m3u8|py|auto]')
    print('     tvbox probe livecheck --spider <源.py>   # 跑代码，逐接口验证')
    print('     tvbox probe livecheck --batch <file.json> [--json]')
    return 1


def cmd_forge(a):
    """模块 F：写源（S161·P018）。验源 + 留档 + 盘点，实现全在 forge.py。

    为什么不把逻辑写在这里：本文件已 6000+ 行，写源是**独立的一条线**，
    混进来会让「验源」和「留档」两件事的边界再次糊掉——S148 就是验完不留档，
    产物落 /tmp 随关机蒸发。模块化是为了让「不留档」不可能再悄悄发生。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import forge
    return forge.cmd_forge(a)


def cmd_shellcrack(a):
    """模块 G：壳源侦察与抠链（S196）。实现全在 shellcrack.py，此处薄封装（M-R38 单一实现）。"""
    import shellcrack
    return shellcrack.cmd_shellcrack(a)



# ---------- harvest lightjson：全类型「轻量可编辑 json」生成器（S112·全类型版）----------
# 复刻历史「原仓库 → 轻量 json」链路的**全类型**处理经验（规则59·招式 T-J 升级）。
#
# ★ 金标准判据（实测自用户亲手产物 A1015_58站_盒子迷_纯可用.json vs 91站原版）：
#   · csp 类线：**看 ext 字段形态**——ext 是 dict(内联配置) → 保留（csp_hit）；
#               ext 是相对路径字符串(./XBPQ/xx.json) → 剔除（远程壳加载不到本地文件=死壳）。
#               （注意：不是按类名一刀切，同类 csp_XBPQ 有站点留、有站点剔，判据是 ext）
#   · py 线：远程 http(s)://xx.py → 测活通过则保留；本地 ./py/xx.py → 剔除（依赖本地文件）
#   · js 线：远程 drpy → 保留（带 ./lib/*.js 依赖）；本地 ./js/xx.js → 剔除
#   · http 直链：测活通过保留（m3u8 直播 / 苹果CMS 采集站）
#   · 基础设施（spider/wallpaper/lives/parses/rules/ads/flags/hosts）：**原样透传**
LIGHTJSON_KINDS = ('直链', '直播', '采集', '点播')


# ---------- P7「原生态保留」校验（S144）----------
# 病根：lightjson / repack / prune 都会「读原包 → 改结构 → 写新包」。
# 只要中间用了白名单重建（`cfg = {k: pkg[k] for k in order}`），原包里**没被列进白名单的
# 键就整体消失**——而 json.dump 照样成功、退出码照样 0、条数照样对得上。
# 2026-10-17 之前的金标准版就是白名单重建：`sites` 之外的兄弟键（如自定义 `ijk`/`siteIcon`/
# `version`）全部被静默吃掉，人看不出来，只有用户装进盒子发现少东西才暴露。
#
# 判据（M-R32 不静默丢弃的延伸）：**结构可以变，字段不能凭空没**。
#   ① 顶层键：原包有、新包无 → 红（丢失，除非在 allow_drop 白名单里显式声明）
#   ② 逐条站点键：同 key/api 的站点，原条有、新条无的字段名 → 红（丢了配置）
#   ③ 透传值：标为「原样透传」的键（spider/lives/parses/...）必须值完全相等 → 不等即红
# 三红任一 → 命令退出码非 0，绝不"写成功就算过"。
PRESERVE_PASSTHROUGH_KEYS = ('spider', 'wallpaper', 'logo', 'warningText', 'lives',
                             'parses', 'flags', 'hosts', 'rules', 'ads')


def check_preserve(pkg_in, cfg_out, *, allow_drop=(), strict_passthrough=True, quiet=False,
                   mutable=(), verified=None):
    """原生态保留校验：输入包 vs 输出包，逐层比对字段集合，返回 (ok, reds, warns)。

    - allow_drop：允许顶层缺失的键（必须是**有意识的**删，比如 `sites` 换成裁剪后的）
    - strict_passthrough：PRESERVE_PASSTHROUGH_KEYS 里的键要求值完全相等（透传不许改）
    - mutable：**本来就要改**的键（addto 加线时 targets 段必然变），跳过"值相等"比对，
               但仍校验字段不丢——改是允许的，丢是不允许的。
    - verified：{键: 已验证的期望新值}——**已验证的格式转换**（S113b：spider 相对→
                绝对，转换前已过活体探测）。仅当输出值恰等于期望值才豁免并记 warn；
                任何其它改动仍按"静默篡改"报红。铁不放松：防的是白名单吃字段，
                不是防有据可查的 §4.2c 转换。
    """
    reds, warns = [], []
    if not isinstance(pkg_in, dict) or not isinstance(cfg_out, dict):
        return True, reds, warns      # 非 dict 形态（旧直链模式）不做本校验

    # ① 顶层键
    allow = set(allow_drop) | {'sites', 'lives'}
    lost = [k for k in pkg_in if k not in cfg_out and k not in allow]
    if lost:
        reds.append(f'顶层键丢失 {len(lost)} 个：{lost}（白名单重建吃掉了，用户盒子会缺配置）')

    # ③ 透传值必须一模一样（mutable 里的键本来就要变，不比对值）
    _mutable = set(mutable)
    _verified = verified or {}
    if strict_passthrough:
        for k in PRESERVE_PASSTHROUGH_KEYS:
            if k in pkg_in and k in cfg_out and k != 'sites' and k not in _mutable:
                if pkg_in[k] != cfg_out[k]:
                    if k in _verified and cfg_out[k] == _verified[k]:
                        warns.append(f'透传键 `{k}` 经 §4.2c 验证转换'
                                     f'（{str(pkg_in[k])[:30]} → {str(_verified[k])[:50]}）')
                        continue
                    reds.append(f'透传键 `{k}` 被改动（原样透传不许加工）')

    # ② 逐条站点字段
    def _sidx(seq):
        d = {}
        for s in (seq or []):
            if not isinstance(s, dict):
                continue
            k = str(s.get('key') or '').strip() or str(s.get('api') or '').strip()
            if k:
                d[k] = s
        return d
    sin, sout = _sidx(pkg_in.get('sites')), _sidx(cfg_out.get('sites'))
    field_lost = {}
    for k, s in sout.items():
        o = sin.get(k)
        if not o:
            continue
        miss = [f for f in o if f not in s]
        if miss:
            field_lost.setdefault(tuple(sorted(miss)), []).append(str(s.get('name') or k)[:20])
    for fields, who in field_lost.items():
        reds.append(f'站点字段丢失 {list(fields)}（{len(who)} 个站受影响，如 {who[:3]}）')

    # 站点数：输出比输入多 → 说明不是"裁剪"而是"混入了别的东西"
    if len(sout) > len(sin) and sin:
        warns.append(f'输出站点 {len(sout)} > 输入 {len(sin)}（非裁剪动作？请确认没混入）')

    if not quiet:
        if reds:
            print(f'  🚨 原生态校验未过（{len(reds)} 项）：')
            for r in reds:
                print(f'     · {r}')
        else:
            print(f'  ✅ 原生态校验通过（顶层键 {len(pkg_in)}→{len(cfg_out)}，'
                  f'站点 {len(sout)} 条字段无损）')
        for w in warns:
            print(f'  ⚠️ {w}')
    return (not reds), reds, warns


def _lj_is_zhilian(api):
    """http(s) 直链（采集站/直播 m3u8）。"""
    a = (api or '').strip().lower()
    return a.startswith('http://') or a.startswith('https://')


def _lj_api_kind(api):
    """api 形态分诊：csp / py / js / http / local / other。"""
    a = (api or '').strip()
    al = a.lower()
    if a.startswith('csp_'):
        return 'csp'
    if al.endswith('.py') or '/py/' in al or 'pyplugin' in al:
        return 'py'
    if al.endswith('.js') or 'drpy' in al or '/js/' in al:
        return 'js'
    if al.startswith('http://') or al.startswith('https://'):
        return 'http'
    if a.startswith('./') or a.startswith('../') or a.startswith('/'):
        return 'local'
    return 'other'


# ---------- 点⑥：多生态分诊闸（M-R60 三态——越界留痕不静默丢弃）----------
HARVEST_OUTOFSCOPE = os.path.join(HARVEST_DIR, 'harvest_outofscope.json')
# 生态越界类别：属其它软件生态，不进 TVBox 候选册（但留痕可回看）
_OUT_OF_SCOPE_ECO = ('漫画书源', '阅读书源', '音源')


def _classify_ecosystem(it):
    """内容生态分诊（点⑥）：从既有字段 / URL 特征 / 内容结构判定生态类别。

    取值 ∈ {tvbox接口, 漫画书源, 阅读书源, 音源, 其他}。
    优先用抽取脚本/调用方已打的 `生态类别`；否则按特征启发式判定。
    """
    eco = (it.get('生态类别') or '').strip()
    if eco in ('tvbox接口', '漫画书源', '阅读书源', '音源', '其他'):
        return eco
    api = (it.get('api接口') or it.get('url') or '')
    # 特征串同时扫「键+值」，否则 bookSourceUrl 这类键特征会被漏判
    blob = ' '.join(f'{k} {v}' for k, v in it.items())
    al = api.lower()
    blob_l = blob.lower()
    # 阅读/漫画书源：含 bookSourceUrl / bookSource 特征（漫画分组优先判漫画书源）
    if 'booksource' in blob_l:
        grp = str(it.get('bookSourceGroup') or it.get('分组类目') or '')
        if '漫画' in grp or 'comic' in grp.lower() or 'manga' in grp.lower():
            return '漫画书源'
        return '阅读书源'
    # TVBox 结构特征（m3u8/http 直链、tvbox 分组）→ 优先于通用关键词，
    # 避免「名字含漫画的直播 m3u8」被通用关键词误判为漫画书源（点⑥ 不误杀 tvbox 接口）
    if ('.m3u8' in al or '.m3u' in al or 'tvbox' in al
            or it.get('分组类目') in ('直播', '点播', '接口')):
        return 'tvbox接口'
    # 漫画书源：漫画特征关键词（仅当前述书源/TVBox 结构均未命中时生效）
    if any(k in blob_l for k in ('漫画', 'comic', 'manga', 'manhua')):
        return '漫画书源'
    # 音源：音乐特征
    if any(k in blob_l for k in ('music', '音乐', 'song', '网易云',
                                 'qq音乐', '咪咕', '酷狗', '虾米')):
        return '音源'
    return 'tvbox接口'  # 默认保守进册（TVBox 范围），不误杀


def can_enter_harvest(it):
    """多生态分诊闸（点⑥·M-R60）：返回 (是否进候选册, 原因)。

    - tvbox接口 / 其他 → 进册（True）
    - 漫画书源 / 阅读书源 / 音源 → 越界，不写 DB_HARVEST，留痕 harvest_outofscope.json
    回填 `生态类别` 到 it，供下游与留痕使用（不静默丢弃，呼应 M-R60 空集合不覆盖非空）。
    """
    eco = _classify_ecosystem(it)
    it['生态类别'] = eco
    if eco in _OUT_OF_SCOPE_ECO:
        return False, f'生态越界({eco})'
    return True, 'in-scope(tvbox)'


def _record_outofscope(records):
    """点⑥：生态越界候选留痕（M-R60 三态——不静默丢弃，可回看）。
    追加写 harvest_candidates/harvest_outofscope.json。"""
    try:
        cur = json.load(open(HARVEST_OUTOFSCOPE, encoding='utf-8')) \
            if os.path.exists(HARVEST_OUTOFSCOPE) else []
        if not isinstance(cur, list):
            cur = []
    except Exception:
        cur = []
    cur.extend(records)
    json.dump(cur, open(HARVEST_OUTOFSCOPE, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


def _lj_ext_is_relative(ext):
    """判 ext 是否为"相对路径字符串"（远程壳加载不到 → 死壳判据）。

    金标准：ext 是 dict / list（内联配置）→ False（保留）；ext 是 './xx.json' 形态 → True（剔除）。
    """
    if isinstance(ext, (dict, list)):
        return False
    if not isinstance(ext, str):
        return False
    e = ext.strip()
    if not e:
        return False
    if e.startswith('http://') or e.startswith('https://'):
        return False
    return e.startswith('./') or e.startswith('../') or e.startswith('/')


def _lj_site_type(api):
    """sites[].type 取值：m3u8 直播=0；http 直链=1；csp 类线=3；py=3。"""
    a = (api or '').lower()
    if a.startswith('csp_') or a.endswith('.py') or '/py/' in a or 'pyplugin' in a:
        return 3
    return 0 if '.m3u8' in a else 1


def _lj_key(api, name=''):
    """稳定 key：probe-<8位hash>。同一 api 恒同 key（幂等可复现）。"""
    import hashlib
    h = hashlib.md5(('|'.join([(api or '').strip(), (name or '').strip()])).encode('utf-8')).hexdigest()[:8]
    return f'probe-{h}'


def _lj_load_items(inp):
    """读入候选（兼容 candidate-list / filtered / 成品库查询结果）。"""
    if not inp or not os.path.exists(inp):
        return []
    raw = json.load(open(inp, encoding='utf-8'))
    if isinstance(raw, dict):
        raw = raw.get('results') or raw.get('records') or raw.get('items') or []
    return [x for x in raw if isinstance(x, dict) and x.get('api接口')]


def _lj_load_sites_from_package(pkg_path):
    """从"完整整包 json"读入 sites（含 ext 等全字段），用于全类型轻量化。

    与 _lj_load_items 的区别：候选库记录只有 {名称,api接口,分组类目}，
    拿不到 ext；而整包 sites[] 有完整字段，才能做 csp 的 ext 判据。
    """
    if not pkg_path or not os.path.exists(pkg_path):
        return None
    raw = json.load(open(pkg_path, encoding='utf-8-sig'))
    if isinstance(raw, dict) and 'sites' in raw:
        return raw
    return None


# ══════════════════ 分组唯一性收敛（S146·M-R103） ══════════════════

# 「取法变体」的规范度打分：分越低越规范（越该保留）
def _canon_rank(url):
    """返回 (扣分, 理由列表)。同站多形态时保留扣分最低的那条（S146）。

    依据（2026-10-17 实测，20 组同源比对）：
      · `/at/<格式>` 与 `/from/<源名>` 是**取法**不是站点 → 有就扣分
      · `/provide/vod` 简写是 macCMS 的非标准入口，`/api.php/provide/vod` 才是标准 → 简写扣分
      · `seaxml` 是早已废弃的接口名 → 扣分最多
      · `%XX` 编码路径可读性差 → 扣分（实测与解码后同一文件，sha 相同）
      · 走代理前缀的实现依赖第三方存活 → 扣分
      · 带 query（ac=list 之类）不是"接口地址"本身 → 轻微扣分
    """
    u = (url or '').strip()
    penalty, why = 0, []
    try:
        s = urlsplit(u if '//' in u else '//' + u)
    except Exception:
        return 0, []
    path = s.path or ''

    if re.search(r'/at/[^/]+', path, re.I):
        penalty += 100; why.append('带/at/取法段')
    if re.search(r'/from/[^/]+', path, re.I):
        penalty += 100; why.append('带/from/取法段')
    if re.search(r'/seaxml/', path, re.I):
        penalty += 300; why.append('seaxml废弃接口')
    if re.search(r'(?<!/api\.php)/provide/vod', path, re.I):
        penalty += 50; why.append('简写provide/vod')
    if '%' in path:
        penalty += 20; why.append('URL编码路径')
    if re.search(r'https?://', path, re.I):
        penalty += 200; why.append('走代理前缀')
    if s.query:
        penalty += 5; why.append('带query')
    if u.lower().startswith('http://'):
        penalty += 1; why.append('明文http')
    return penalty, why


def _same_line(a_url, b_url, timeout=15, cache=None):
    """判定两条 api 是不是**同一条线路**（S146 的实证口径，不靠猜测）。

    三判据，命中任一即为同一条：
      ① 片库总量相同（total）
      ② 抽同一条片（用 A 的首条 id 去 B 查），播放地址相同
      ③ 文件内容相同（js/txt 类，sha256 比对）

    返回 (是否同一条, 证据串)。
    拿不到数据时返回 (None, 原因) —— **不可判定不许当同一条**（M-R102 同款纪律）。
    """
    cache = cache if cache is not None else {}
    _SENTINEL = object()          # S146/M-R60：缓存里存的是「取到了什么」，不是「空」

    def _get(u, extra=''):
        """取 URL 内容。返回 (ok, text)；ok=False 表示**取数失败**，与「内容为空」严格区分。

        S146 修：旧版 `except: raw = ''` 把「curl 超时/不存在」折成空串，与「站点真返回空」
        完全同形 → 后面 `if not a: return None` 会把网络问题当成「判不了」，把
        「站点返回空」也当成「判不了」，两者本该分开报。M-R60：读失不许折成空态。
        """
        key = (u, extra)
        hit = cache.get(key, _SENTINEL)
        if hit is not _SENTINEL:
            return hit
        full = u + ('&' if '?' in u else '?') + extra if extra else u
        try:
            r = _sh([CURL, '-s', '-m', str(timeout), '-A', UA, '-L', full],
                    timeout=timeout + 3)
            raw = r[1] if isinstance(r, tuple) else r
        except Exception:
            cache[key] = (False, '')      # 取数失败：显式 False，不假装是空内容
            return cache[key]
        cache[key] = (True, raw or '')
        return cache[key]

    # ③ 文件类：sha256
    if re.search(r'\.(js|json|txt|py|m3u|m3u8)$', (a_url or '').split('?')[0], re.I) \
            and not re.search(r'api\.php|/provide/', a_url or '', re.I):
        oka, ha = _get(a_url)
        okb, hb = _get(b_url)
        if not (oka and okb):
            return None, f'文件取不到（{"A" if not oka else ""}{"B" if not okb else ""} 侧抓取失败/超时）'
        if ha and hb:
            sa = hashlib.sha256(ha.encode('utf-8', 'ignore')).hexdigest()[:16]
            sb = hashlib.sha256(hb.encode('utf-8', 'ignore')).hexdigest()[:16]
            if sa == sb:
                return True, f'文件内容相同 sha={sa} size={len(ha)}B'
            return False, f'文件内容不同 sha {sa}({len(ha)}B) vs {sb}({len(hb)}B)'
        # 取到了但内容为空：这是「站点返回空」，与「取不到」是两回事
        return None, f'文件内容为空（A={len(ha)}B B={len(hb)}B，可能是空文件或返回空壳）'

    # ①② CMS 接口
    oka, da = _get(a_url, 'ac=list')
    okb, db = _get(b_url, 'ac=list')
    if not (oka and okb):
        return None, f'接口取不到（{"A" if not oka else ""}{"B" if not okb else ""} 侧抓取失败/超时）'
    ja = jb = None
    try:
        ja = json.loads(da)
    except Exception:
        pass
    try:
        jb = json.loads(db)
    except Exception:
        pass
    if not isinstance(ja, dict) or not isinstance(jb, dict):
        return None, '非JSON（可能是XML/HTML，需人工看）'
    ta, tb = ja.get('total'), jb.get('total')
    same_total = (ta is not None and ta == tb)
    # 抽片：用 A 首条 id 去 B 查
    la = ja.get('list') or []
    play_same = False
    play_ev = ''
    if la:
        vid = la[0].get('vod_id')
        if vid is not None:
            _oka, va = _get(a_url, f'ac=videolist&ids={vid}')
            _okb, vb = _get(b_url, f'ac=videolist&ids={vid}')
            try:
                xa = (json.loads(va).get('list') or [{}])[0]
                xb = (json.loads(vb).get('list') or [{}])[0]
                pa = re.findall(r'https?://[^\s#$]+', xa.get('vod_play_url') or '')
                pb = re.findall(r'https?://[^\s#$]+', xb.get('vod_play_url') or '')
                if pa and pb:
                    play_same = (pa[0] == pb[0])
                    play_ev = f'同片{vid}首播 {pa[0][:60]}'
            except Exception:
                pass
    if same_total and play_same:
        return True, f'total={ta} 且 {play_ev}'
    if same_total:
        return True, f'total={ta}（片库总量相同；首播不同可能只差播放源）'
    if play_same:
        return True, f'{play_ev}（首播相同；total {ta} vs {tb} 差 {(tb or 0) - (ta or 0):+d}，属实时波动）'
    return False, f'total {ta} vs {tb} 与首播都不同'


def cmd_harvest_dedup(a):
    """harvest dedup：一条线路只允许留一条（S146·M-R103）。

    病根：S145 按「源指纹」把 191 降到 114，但**同站不同取法**（`/at/json`、`/seaxml/`、
    `/provide/vod` 简写）指纹不同 → 同一条线路在库里以不同 api 存了多条，
    而且**横跨 multiple 分组**（360zy 在"影视"和"直链"各一份）。用户原话：
    「只保留一条真实的路线，去重，同一条线路在几个分组里就是不对的」。

    判据（不是猜，是实测）：
      ① 同 host 才可能是同一条（跨 host 一律视为不同线路）
      ② 规范度打分 `_canon_rank` 选保留者（分最低=最规范）
      ③ **实证** `_same_line`：total 相同 / 同片首播相同 / 文件 sha 相同 → 同一条
      ④ 判不出来的（实测失败/非JSON）**不合并**，标 `待人工`，宁可留两条也不误删

    用法：
      python3 probe.py harvest dedup --db               # 读成品源库
      python3 probe.py harvest dedup --in xx.json       # 读本地 json（数组）
      python3 probe.py harvest dedup --pkg xx.json      # 读 TVBox 包的 sites[]
      ... --dry-run      只出计划不删（默认就是 dry-run，要 --apply 才真删）
    """
    from collections import defaultdict

    # ---- ① 取数据 ----
    rows, src_desc, rid_key = [], '', None
    if a.db is not None:
        for db_id in (a.db if a.db else [DB_PROD]):
            st, rr = _cloud_rows(db_id)
            if st != 'ok':
                print(f'❌ 读库失败（{st}）：{db_id} —— 不得当空库处理')
                return
            rows += rr
            src_desc = f'云端库 {db_id}'
        rid_key = 'record_id'
    elif a.pkg:
        raw = json.load(open(a.pkg, encoding='utf-8-sig'))
        sites = raw.get('sites') or []
        rows = [{'api接口': s.get('api'), '名称': s.get('name'),
                 '分组类目': s.get('group') or '', '源类型': s.get('type')}
                for s in sites]
        src_desc = f'包 {os.path.basename(a.pkg)} 的 sites[]'
        rid_key = None
    else:
        inp = a.input or _latest_harvest()
        if not inp or not os.path.exists(inp):
            print('ℹ️ 无输入（用 --db / --in / --pkg 指定）')
            return
        rows = json.load(open(inp, encoding='utf-8'))
        if isinstance(rows, dict):
            rows = rows.get('records') or rows.get('items') or []
        src_desc = os.path.basename(inp)
        rid_key = 'record_id'

    print(f'🧹 分组唯一性收敛 · 源：{src_desc} · {len(rows)} 条')

    # S146 修：`_cloud_rows` 走 get_database_content，**不返回 record_id**（S088 已知）。
    # 不补这一步，--apply 会「计划删 5 条、实际删 0 条」还回一句「没有可删的 record_id」。
    if a.db is not None and rows:
        # S146/M-R60：取 id 失败**不许折成空 map**——空 map 会让「计划删 N、实际删 0」
        # 看起来像「本来就没得删」。取不到就整轮不删，并显式说不删的原因。
        idmap, _id_err = {}, ''
        try:
            idmap = _cloud_id_map(
                (a.db[0] if a.db else DB_PROD),
                text_field=('api接口' if 'api接口' in (rows[0] or {}) else 'api'))
            # ★ S156：_cloud_id_map 现在用 None 表示"读失败"（不再折成 {}）。
            #   这里必须把它还原成 _id_err 分支——否则 None 会一路走到 idmap.get() 报 TypeError。
            if idmap is None:
                _id_err = 'record_id 映射读取失败（无法判定，见 stderr 原因）'
                idmap = {}
        except Exception as e:
            _id_err = f'{type(e).__name__}: {e}'
        if _id_err:
            print(f'  🚫 record_id 映射取数失败（{_id_err}）——本轮**不做任何删除**，避免删错记录')
            drop = []
        elif not idmap:
            print('  🚫 record_id 映射为空——无法定位云端记录，本轮**不做任何删除**')
            drop = []
        miss = 0
        for r in rows:
            key = str(r.get('api接口') or r.get('api') or r.get('url') or '').strip()
            rid = idmap.get(key)
            if rid:
                r['record_id'] = rid
            else:
                miss += 1
        if miss:
            print(f'  ⚠️ {miss} 条取不到 record_id —— 绝不盲删（会删错记录）')

    # ---- ② 按 host 分组（不同域名一律视为不同线路，不合并）----
    by_host = defaultdict(list)
    for i, r in enumerate(rows):
        api = str(r.get('api接口') or r.get('api') or r.get('url') or '').strip()
        if not api:
            continue
        try:
            h = (urlsplit(api if '//' in api else '//' + api).netloc or '').lower().split(':')[0]
        except Exception as e:
            # S146/M-R60：解析失败当作独立分组（host 用原串占位），**不折成 ''**——
            # 折成 '' 会把一批解析失败的源塞进同一个桶，然后在桶内互相比对、可能误删。
            h = f'__unparsed__{api}'
            print(f'  ⚠️ host 解析失败，按独立分组处理：{api[:80]}（{type(e).__name__}: {e}）')
        if h.startswith('www.'):
            h = h[4:]
        by_host[h].append({'idx': i, 'row': r, 'api': api,
                           'rank': _canon_rank(api),
                           'fp': source_fp(api),
                           'group': str(r.get('分组类目') or '')})

    keep, drop, pending = [], [], []
    for h, items in by_host.items():
        if len(items) == 1:
            keep.append(items[0])
            continue
        # 先按指纹归并（同指纹必是同一条，无需实测）
        by_fp = defaultdict(list)
        for it in items:
            by_fp[it['fp']].append(it)
        # 每个指纹先选出代表（规范度最低分那条）
        reps = []
        for fp, its in by_fp.items():
            its.sort(key=lambda x: (x['rank'][0], len(x['api'])))
            reps.append(its[0])
            for x in its[1:]:
                drop.append({**x, 'reason': f'同指纹重复（{x["fp"]}）'})
        # 不同指纹但同 host：实测判定是不是同一条线路
        if len(reps) > 1:
            reps.sort(key=lambda x: (x['rank'][0], len(x['api'])))
            head = reps[0]
            for cand in reps[1:]:
                same, ev = _same_line(head['api'], cand['api'], timeout=a.timeout)
                if same is True:
                    drop.append({**cand,
                                 'reason': f'同一条线路（实测：{ev}）→ 保留更规范的 {head["api"]}',
                                 'evidence': ev})
                elif same is None:
                    keep.append(cand)
                    pending.append({**cand, 'reason': f'无法判定（{ev}）——已保留，需人工裁决'})
                else:
                    keep.append(cand)          # 实测确认是两条不同线路，都留
        keep.append(reps[0] if len(reps) > 1 else reps[0])

    # ---- ③ 跨分组自检：同一指纹不许出现在两个分组 ----
    fp2g = defaultdict(set)
    for k in keep:
        fp2g[k['fp']].add(k['group'])
    cross = {fp: gs for fp, gs in fp2g.items() if len(gs) > 1}

    # ---- ④ 报告 ----
    print(f'  ✅ 保留 {len(keep)} 条 | 🗑 删除 {len(drop)} 条 | ⚠️ 待人工 {len(pending)} 条')
    if drop:
        print('\n  删除明细：')
        for d in drop:
            nm = d['row'].get('名称') or d['row'].get('name') or ''
            print(f'    · [{d["group"] or "-"}] {nm}  {d["api"]}')
            print(f'        理由：{d["reason"]}')
    if pending:
        print('\n  ⚠️ 待人工（无法判定，已保留不删）：')
        for p in pending:
            nm = p['row'].get('名称') or p['row'].get('name') or ''
            print(f'    · [{p["group"] or "-"}] {nm}  {p["api"]}')
            print(f'        {p["reason"]}')
    if cross:
        print(f'\n  🚨 仍有 {len(cross)} 个指纹跨多个分组（说明判据漏了）：')
        for fp, gs in cross.items():
            print(f'    · {fp} → {gs}')
    else:
        print('\n  ✅ 分组唯一性：没有任何一条线路同时出现在两个分组')

    # host 维度补充说明（S146）：同一**服务**上放两个**不同文件**是正常的
    # （如 raw.githubusercontent.com 上两个不同仓库的 drpy2.min.js）。
    # 只有同 host 且路径无法区分的才算问题，这里只提示，不当作失败。
    h2g = defaultdict(set)
    for k in keep:
        h2g[k['api'].split('//')[-1].split('/')[0].lower()].add(k['group'])
    multi_host = {h: gs for h, gs in h2g.items() if len(gs) > 1}
    if multi_host:
        print(f'  ℹ️ {len(multi_host)} 个 host 跨分组（内容不同，属正常）：')
        for h, gs in multi_host.items():
            print(f'    · {h} → {gs}')

    plan = {'source': src_desc,
            'keep': [{'api': k['api'], '名称': k['row'].get('名称') or k['row'].get('name'),
                      '分组类目': k['group'],
                      **({'record_id': k['row'].get('record_id')} if rid_key else {})}
                     for k in keep],
            'drop': [{'api': d['api'], '名称': d['row'].get('名称') or d['row'].get('name'),
                      '分组类目': d['group'], '理由': d['reason'],
                      **({'record_id': d['row'].get('record_id')} if rid_key else {})}
                     for d in drop],
            'pending': [{'api': p['api'], '名称': p['row'].get('名称') or p['row'].get('name'),
                         '分组类目': p['group'], '理由': p['reason']}
                        for p in pending]}
    outp = a.out or os.path.join(os.getcwd(), time.strftime('dedup_%m%d_%H%M.json'))
    json.dump(plan, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'   计划已存：{outp}')

    # ---- ⑤ 真删（须 --apply，且只在 --db 模式下）----
    if not a.apply:
        print('   （dry-run：未删除任何记录；确认无误后加 --apply 才真删）')
        return
    if a.db is None:
        print('   ⚠️ 只有 --db 模式能云端真删；本地文件请用 --out 产物手工处理')
        return
    rids = [d['row'].get('record_id') for d in drop if d['row'].get('record_id')]
    if not rids:
        print('   ℹ️ 没有可删的 record_id')
        return
    db_id = (a.db[0] if a.db else DB_PROD)
    ok_n, err = _cloud_delete_ids(db_id, rids)
    print(f'   🗑 删除判真：{ok_n}/{len(rids)}' + (f'（错误：{err}）' if err else ''))
    invalidate(db_id)
    st, after = _cloud_rows(db_id)
    print(f'   复核：云端现 {len(after)} 条（删前 {len(rows)}）')


def _cloud_delete_ids(db_id, rids, chunk=40):
    """按 record_id 批量删，**以 results[].success 判真**（M-R99）。返回 (成功数, 错误串)。"""
    good, err = 0, ''
    rids = [r for r in rids if r]
    for i in range(0, len(rids), chunk):
        batch = rids[i:i + chunk]
        try:
            out = api('database/batch_delete_database_records.py',
                      '--database-id', db_id, '--record-ids', json.dumps(batch))
            d = json.loads(out) if isinstance(out, str) else out
            for r in d.get('results', []):
                if r.get('success'):
                    good += 1
                else:
                    err += f'{r.get("id")} '
        except Exception as e:
            err += f'[{type(e).__name__}: {e}] '
    return good, err.strip()


def cmd_harvest_lightjson(a):
    """harvest lightjson（S112 全类型版）：把整包/候选生成 TVBox 轻量可编辑 json。
    剔的是 ext 走相对路径的死壳 + 本地 py/js + 测活不过的 http。

    支持两种输入：
      --pkg <整包.json>   ← 全类型模式（推荐）：读 sites[].ext 做完整判据
      --in  <候选.json>   ← 直链模式（兼容旧）：只有 api，做 http 直链
    产出：{基础设施原样透传} + sites[保留] + 剔失清单（可还原）
    """
    from collections import Counter
    # ---- 优先整包模式 ----
    pkg = _lj_load_sites_from_package(getattr(a, 'pkg', None))
    if pkg is not None:
        _lj_lightjson_from_pkg(a, pkg)
        return
    # ---- 候选模式（旧直链逻辑）----
    inp = a.input or _latest_harvest()
    items = _lj_load_items(inp)
    if not items:
        print('ℹ️ 无候选（用 --pkg <整包.json> 走全类型，或先跑 harvest filter/export）')
        return
    print(f'═══ 候选·轻量 json 生成（直链模式）═══\n候选 {len(items)} 条')

    zhilian, dropped = [], []
    for it in items:
        api = (it.get('api接口') or '').strip()
        k = _lj_api_kind(api)
        if k == 'http':
            zhilian.append(it)
        elif k == 'csp':
            dropped.append((api, it.get('名称', ''), 'csp类线：候选模式无 ext，无法判内联；请用 --pkg 走全类型'))
        elif k == 'py':
            dropped.append((api, it.get('名称', ''), 'py线：请用 --pkg 模式（需测活+ext 判据）'))
        else:
            dropped.append((api, it.get('名称', ''), f'{k}线，本模式不产出（请用 --pkg）'))
    print(f'  🔎 http直链 {len(zhilian)} 条 ｜ 剔除 {len(dropped)} 条')
    for reason, n in Counter(r for _, _, r in dropped).most_common():
        print(f'     剔除 {reason}: {n}')

    need_probe = not getattr(a, 'no_probe', False)
    passed, unprobed = [], []
    if need_probe:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import livecheck

        def _one(it):
            api = it['api接口'].strip()
            kind = 'm3u8' if '.m3u8' in api.lower() else 'vod'
            v, ev, _k = livecheck.check_auto(api, kind=kind)
            return it, v, ev

        from concurrency import run_bounded, TIER_WEB, ConcurrencyError
        try:
            _rep = run_bounded(zhilian, _one, tier=TIER_WEB,
                               workers=getattr(a, 'workers', None),
                               label='lightjson-probe', keep_order=True, on_error='record')
        except ConcurrencyError as _e:
            print(f'❌ --workers 超限：{_e}')
            return 2
        res = [(it, '无法判定', '探测线程异常（并发报告已记录）') if _r is None else _r
               for it, _r in zip(zhilian, _rep.results)]
        for it, v, ev in res:
            (passed if v == '通过' else unprobed).append((it, v, ev))
        print(f'  🧪 真实测活：✅通过 {len(passed)} / ⚠️未通过 {len(unprobed)}')
    else:
        passed = [(it, '未测', '--no-probe：跳过测活') for it in zhilian]

    sites, seen_api, dup = [], set(), 0
    for it, v, ev in passed:
        api = it['api接口'].strip()
        if api in seen_api:
            dup += 1
            continue
        seen_api.add(api)
        name = (it.get('名称') or api).strip()
        sites.append({'key': _lj_key(api, name), 'name': name,
                      'type': _lj_site_type(api), 'api': api,
                      'searchable': 0, 'quickSearch': 0, 'recordable': 0})

    cfg = {'wallpaper': a.wallpaper or 'https://picsum.photos/1080/', 'sites': sites, 'lives': []}
    tag = a.tag or 'C'
    mmdd = time.strftime('%m%d')
    fn = f'{tag}{mmdd}_{a.desc or "轻量版"}.json'
    outp = a.out or os.path.join(a.outdir or os.getcwd(), fn)
    json.dump(cfg, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    drop_recs = [{'api接口': api, '名称': nm, '剔除原因': rs} for api, nm, rs in dropped]
    drop_recs += [{'api接口': it['api接口'].strip(), '名称': it.get('名称', ''),
                   '剔除原因': f'真实测活未通过({v})：{ev[:60]}'} for it, v, ev in unprobed]
    if drop_recs:
        json.dump(drop_recs, open(outp.replace('.json', '_剔失清单.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
    print(f'\n✅ 产出 {len(sites)} 站 → {outp}')


def _lj_lightjson_from_pkg(a, pkg):
    """全类型轻量化（金标准）：从整包 sites 按 ext 形态 + api 形态分诊。"""
    from collections import Counter
    sites_in = pkg.get('sites', []) or []
    print(f'═══ 全类型·轻量 json 生成 ═══\n整包 sites {len(sites_in)} 条')

    keep, dropped = [], []
    py_remote = []           # 待测活的远程 py
    base = getattr(a, 'base', '') or None   # S113 挂载决定论：候选 base 主机（不传=维持原剔除）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import mount_judge as _mj
    except Exception:
        _mj = None
    def _rel_judge(s, spider=None):
        """本地相对地址 → 挂载判定；无 base/模块缺失 → None（维持原剔除，向后兼容）。
        S113b：spider=包级 spider 地址，csp 判 jar 同名类用。"""
        if not base or _mj is None:
            return None
        return _mj.judge_site(s, base, spider)
    for s in sites_in:
        api = str(s.get('api', '')).strip()
        name = str(s.get('name', '')).strip()
        k = _lj_api_kind(api)
        if k == 'csp':
            # S113b 挂载决定论：ext 配置源（相对→base转绝对）+ spider jar 同名类
            d = _rel_judge(s, spider=pkg.get('spider'))
            if d is None:
                # 向后兼容：无 base/无 mount_judge → 原逻辑
                if _lj_ext_is_relative(s.get('ext')):
                    dropped.append((api, name, f"csp死壳：ext 走相对路径({str(s.get('ext'))[:40]})，远程壳加载不到"))
                else:
                    keep.append(s)      # csp_hit：ext 内联 → 保留
            elif d['verdict'] == 'csp_ok':
                s2 = dict(s)
                if d.get('new_ext'):
                    s2['ext'] = d['new_ext']
                keep.append(s2)          # jar 含同名类 + ext 通过 → 作者挂了，可用
                print(f"    ✅ csp挂载确认 {name}: {d['reason'][:90]}")
            elif d['verdict'] == 'csp_jar_unknown':
                keep.append(s)          # M-R60：无法判定≠死，保留待人工
                print(f"    ⚠️ csp无法判定 {name}: {d['reason'][:90]}")
            else:
                dropped.append((api, name, d['reason']))
        elif k == 'http':
            keep.append(s)          # http 直链：后续可测活（此处先留）
        elif k == 'py':
            if _lj_is_zhilian(api):
                py_remote.append(s)  # 远程 py → 测活
            else:
                d = _rel_judge(s)  # S113：本地相对 py → base 活体验证
                if d and d['verdict'] == 'converted':
                    s2 = dict(s); s2['api'] = d['new_api']
                    py_remote.append(s2)  # 转绝对后当远程 py 测活
                    print(f"    ↩ 相对→绝对[{d['code']}] {name}: {api} → {d['new_api']}")
                else:
                    dropped.append((api, name, (d or {}).get('reason') or 'py线：本地路径(./py/)，依赖本地文件'))
        elif k == 'js':
            if _lj_is_zhilian(api):
                keep.append(s)
            else:
                d = _rel_judge(s)  # S113：本地相对 js → base 活体验证
                if d and d['verdict'] == 'converted':
                    keep.append({**s, 'api': d['new_api']})
                    print(f"    ↩ 相对→绝对[{d['code']}] {name}: {api} → {d['new_api']}")
                else:
                    dropped.append((api, name, (d or {}).get('reason') or 'js线：本地路径(./js/)，依赖本地文件'))
        elif k == 'local':
            d = _rel_judge(s)  # S113：本地路径 → base 活体验证
            if d and d['verdict'] == 'converted':
                keep.append({**s, 'api': d['new_api']})
                print(f"    ↩ 相对→绝对[{d['code']}] {name}: {api} → {d['new_api']}")
            else:
                dropped.append((api, name, (d or {}).get('reason') or f'本地路径线({api[:30]})'))
        else:
            dropped.append((api, name, f'形态未知({api[:30]})'))

    # 远程 py 测活（gzip 已修）
    py_kept, py_drop = [], []
    if py_remote and not getattr(a, 'no_probe', False):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import livecheck
        def _one(s):
            v, ev = livecheck.check_py(str(s.get('api', '')).strip())
            return s, v, ev
        from concurrency import run_bounded, TIER_WEB, ConcurrencyError
        try:
            _rep = run_bounded(py_remote, _one, tier=TIER_WEB,
                               workers=getattr(a, 'workers', None),
                               label='lightjson-py-probe', keep_order=True, on_error='record')
        except ConcurrencyError as _e:
            print(f'❌ --workers 超限：{_e}')
            return 2
        res = [(s, '无法判定', '探测线程异常（并发报告已记录）') if _r is None else _r
               for s, _r in zip(py_remote, _rep.results)]
        for s, v, ev in res:
            if v == '通过':
                py_kept.append(s)
            else:
                py_drop.append((s.get('api', ''), s.get('name', ''), f'py测活未过({v})：{ev[:50]}'))
    else:
        py_kept = py_remote
    keep += py_kept
    dropped += py_drop

    print(f'  ✅ 保留 {len(keep)}（csp挂载 {sum(1 for s in keep if _lj_api_kind(str(s.get("api","")))=="csp")}'
          f' / http {sum(1 for s in keep if _lj_api_kind(str(s.get("api","")))=="http")}'
          f' / 远程py {len(py_kept)} / js {sum(1 for s in keep if _lj_api_kind(str(s.get("api","")))=="js")}）'
          f' ｜ ❌ 剔除 {len(dropped)}')
    for reason, n in Counter(r.split('：')[0] for _, _, r in dropped).most_common():
        print(f'     剔除 {reason}: {n}')

    # 基础设施原样透传（S144·P7 修正）：**全键透传**，只对已知键重排位置。
    #
    # 旧写法是白名单重建（`for k in order/tail: cfg[k]=pkg[k]`）——原包里凡不在
    # order/tail 里的键（作者的 `version`/`ijk`/`siteIcon`/自定义开关）**整体消失**，
    # 而 json.dump 成功、退出码 0、站点数也对，人完全看不出。P7 校验当场抓到了这条。
    # 铁律：**结构可以变，字段不能凭空没**——只做 reorder，不做 select。
    cfg = {}
    order = ('spider', 'wallpaper', 'logo', 'warningText', 'lives')
    tail = ('parses', 'flags', 'hosts', 'rules', 'ads')
    for k in order:
        if k in pkg:
            cfg[k] = pkg[k]
    if 'wallpaper' not in cfg:
        cfg['wallpaper'] = a.wallpaper or 'https://picsum.photos/1080/'
    if 'lives' not in cfg:
        cfg['lives'] = []
    cfg['sites'] = keep          # 站点顺序：保持原包顺序
    for k in tail:
        if k in pkg:
            cfg[k] = pkg[k]
    # ← P7：未列名的兄弟键一律补在最前（保持原包相对顺序），一个都不许丢
    _named = set(order) | set(tail) | {'sites'}
    _extra = {k: v for k, v in pkg.items() if k not in _named}
    if _extra:
        cfg = {**_extra, **cfg}

    # S113b：包级 spider 相对→绝对（csp 站点靠它加载 jar，远程配置必须可加载）
    _spider_verified = None
    if base and _mj is not None:
        sp = str(cfg.get('spider') or '').strip()
        if sp.startswith(('./', '../', '/')):
            abs_sp = _mj.to_abs(sp, base)
            code, alive = _mj._http_alive(abs_sp, timeout=20)
            if alive:
                cfg['spider'] = abs_sp
                _spider_verified = abs_sp
                print(f"  🕷  spider 相对→绝对[{code}]: {sp} → {abs_sp}")
            else:
                print(f"  ⚠️  spider {sp} 转绝对后探测 {code}（未通过活体校验），保留原值")

    tag = a.tag or 'C'
    mmdd = time.strftime('%m%d')
    desc = a.desc or '纯可用'
    outp = a.out or os.path.join(a.outdir or os.getcwd(), f'{tag}{mmdd}_{len(keep)}站_{desc}.json')
    os.makedirs(os.path.dirname(os.path.abspath(outp)), exist_ok=True)
    json.dump(cfg, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    drop_recs = [{'api接口': api, '名称': nm, '剔除原因': rs} for api, nm, rs in dropped]
    if drop_recs:
        json.dump(drop_recs, open(outp.replace('.json', '_剔失清单.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
    print(f'\n✅ 产出 {len(keep)} 站 → {outp}')
    print(f'   基础设施透传: {[k for k in cfg if k!="sites"]}')
    if drop_recs:
        print(f'   剔失清单（可还原 {len(drop_recs)} 条）：{outp.replace(".json", "_剔失清单.json")}')
    # S144·P7：落盘后立刻回读 + 原生态校验——「写成功」不等于「没丢字段」
    try:
        back = json.load(open(outp, encoding='utf-8'))
        # verified：S113b spider 相对→绝对是**已验证的 §4.2c 转换**（转换前活体探测过），
        # 区别于白名单重建的静默篡改——校验函数内会以 ⚠️ 记录而非报红。
        _ok, _reds, _warns = check_preserve(
            pkg, back, verified={'spider': _spider_verified} if _spider_verified else None)
        if not _ok:
            print(f'  ❌ 产出包已丢失原包字段（{outp}）——请勿直接下发，先修透传白名单')
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f'  ⚠️ 原生态校验读回失败（{type(e).__name__}）——产出包未验证')


def cmd_harvest_promote(a):
    """harvest promote：把**真实测活通过**的成品源写入「探源司·成品源库」（DB_PROD，S097）。

    与 DB_HARVEST（候选）的区别：DB_HARVEST 存"采集到的候选"（含未测/失败），
    DB_PROD 只存**通过真实可用测活**的成品——与源数据总库/备用库并列的第四库，
    拿来即可放进用户 json。

    输入：filter 后的候选文件（含"测活证据"字段者优先）；对无证据者现场补测。
    字段映射：名称/host/分组类目/api接口/体系/可用体系/源类型/真实测活/测活证据/
              来源仓库/状态——与源总库同构 + 测活扩展。
    """
    inp = a.input or _latest_harvest()
    if not inp or not os.path.exists(inp):
        print('ℹ️ 无候选文件（先跑 harvest scan/pull/filter）')
        return
    items = json.load(open(inp, encoding='utf-8'))
    if isinstance(items, dict):
        items = items.get('records') or items.get('items') or []
    if not items:
        print('ℹ️ 候选为空')
        return
    print(f'✅ 提升 {len(items)} 条候选 → 成品源库（真实测活通过者入）')

    # 现场补测（无"测活证据"的才测，省时）
    need = [it for it in items if not it.get('测活证据')]
    ev_map = {}
    if need:
        print(f'   🔎 对 {len(need)} 条补做真实测活...')
        from concurrency import run_bounded, TIER_WEB
        _rep = run_bounded(need, lambda x: _alive_line(x['api接口'], timeout=12),
                           tier=TIER_WEB, label='dispatch-reprobe',
                           keep_order=True, on_error='record')
        for it, _r in zip(need, _rep.results):
            # 线程异常 → 无法判定（不进「通过」、也不判死），M-R60 三态不许折叠
            ev_map[id(it)] = _r if _r is not None else (False, '无法判定：探测线程异常（并发报告已记录）')

    rows, n_pass, n_skip = [], 0, 0
    for it in items[:a.limit]:
        api = it.get('api接口') or ''
        if not api:
            continue
        if it.get('测活证据') and ('通过' in str(it.get('测活证据', '')) or 'vod_play_url' in str(it.get('测活证据', ''))
                                   or '分片' in str(it.get('测活证据', ''))):
            ok, ev = True, it['测活证据']
        elif id(it) in ev_map:
            ok, ev = ev_map[id(it)]
        else:
            ok, ev = False, '未测'
        if not ok:
            n_skip += 1
            continue
        n_pass += 1
        _, _, kind = _lc_kind(api)
        rows.append({
            '名称': it.get('名称', ''),
            'host': urlparse(api).netloc,
            '分组类目': it.get('分组类目', '其他'),
            'api接口': api,
            '体系': it.get('体系', '脚本系'),
            '源类型': {'vod': '点播', 'm3u8': '直播', 'py': 'py'}.get(kind, '点播'),
            '真实测活': '通过',
            '测活证据': str(ev)[:200],
            '来源仓库': it.get('来源仓库', ''),
            '状态': '正常',
            '生产时间': now_iso(),
        })
    if not rows:
        print(f'   ⚠️ 无一条通过真实测活（{n_skip} 条未通过）——不写入（禁把未测/失败折叠成成品）')
        return
    ok, n_add, n_upd = _cloud_upsert(rows, DB_PROD, 'api接口',
                                     select_cols=('分组类目', '体系', '源类型', '真实测活', '状态'))
    print(f'   ☁️ 成品源库写入：{"✅" if ok else "⚠️失败"}（新增 {n_add} / 更新 {n_upd}）'
          f'｜提交 {len(rows)} 条（跳过未通过 {n_skip} 条）')
    # S144·P4：写后**字段级回读**。旧版只报「新增 N / 更新 M」，
    # 而 S144 实测过「回执全绿但 7 个 select 字段全空」——写进去了但字段是空的，
    # 只看新增/更新条数永远发现不了（M-R99 同型：success ≠ 值对）。
    if ok and rows:
        try:
            invalidate(DB_PROD)
            after = {source_fp(r.get('api接口') or ''): r for r in rows_of(DB_PROD)}
            check_cols = ('分组类目', '源类型', '真实测活', '状态')
            bad = []
            for r in rows[:200]:                      # 采样上限 200，避免全库两遍
                got = after.get(source_fp(r.get('api接口') or ''))
                if not got:
                    bad.append((r.get('api接口'), '回读找不到该条'))
                    continue
                for col in check_cols:
                    want = str(r.get(col) or '')
                    have = str(got.get(col) or '')
                    if want and not have:
                        bad.append((r.get('api接口'), f'{col} 写入"{want}"但回读为空'))
                        break
            if bad:
                print(f'   ⚠️ 字段回读不符 {len(bad)} 条（写入报成功但字段没落对）：')
                for u, why in bad[:5]:
                    print(f'      {u[:56]} → {why}')
            else:
                print(f'   🔍 字段回读：采样 {min(len(rows), 200)} 条，'
                      f'{"/".join(check_cols)} 全部落对')
        except Exception as e:
            print(f'   ⚠️ 回读失败（{type(e).__name__}）——**无法判定**字段是否落对，勿当通过')
    print(f'   ℹ️ 成品源库 id：{DB_PROD}（与源总库/备用库并列，只存真实测活通过者）')


def _lc_kind(url):
    """判类（轻量，复用 livecheck 逻辑但不想 import 开销时用）。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import livecheck
        if livecheck._is_m3u8(url):
            return 'm3u8', True, 'm3u8'
        return 'vod', False, 'vod'
    except Exception:
        return 'vod', False, 'vod'


def cmd_harvest_addto(a):
    """harvest addto：把一条（或几条）线路加进指定包，**6 步一条命令走完**（S144·P2）。

    为什么必须有这一条：探源司 16 条路由全是单点命令（scan/pull/filter/promote/
    prune/repack 各一条），**没有「把 X 加进某包」**。于是加一次线要走 6 步，
    漏一步就出事——用户骂过的那次就是漏了测活那步；当年「松视两条加进 L 包」
    也是临时写脚本干的，至今没固化。现在再来一次还得临场写，这就是把
    **人的记忆当流程**。

    六步（每步都给结论，不静默跳过）：
      ① 抽取    —— 从来源（成品库 record_id / 候选文件 / 直接 api）取到条目
      ② 判平移  —— 形态判类 + 可进包判定（js/py/csp 需类包，不能单独进包）
      ③ 测活    —— 真实测活（不是 HTTP 码），用 livecheck.check_auto
      ④ 查冲突  —— 与目标包 sites/lives 按 api 指纹比对，重复则跳过
      ⑤ 判边界  —— 包结构合法性（sites 需 name+api，lives 需 name+url；
                   ext 相对路径=死壳，禁止写入）
      ⑥ 回读    —— 写回后重新读目标包，确认这条真的在里面、字段没丢

    `--dry` 只跑到第⑤步并打印将写入的完整片段，不落盘。
    """
    import copy
    from livecheck import classify_source, can_enter_pack, check_auto

    pkg_path = a.pkg
    if not os.path.exists(pkg_path):
        print(f'❌ 目标包不存在：{pkg_path}')
        return 1
    pkg = json.load(open(pkg_path, encoding='utf-8'))
    print(f'📦 目标包：{pkg_path}')
    print(f'   现有 sites {len(pkg.get("sites") or [])} / lives {len(pkg.get("lives") or [])} / '
          f'parses {len(pkg.get("parses") or [])}')

    # ── ① 抽取
    items = []
    if a.api:
        for x in str(a.api).split(','):
            x = x.strip()
            if x:
                items.append({'name': a.name or source_fp(x).split('/')[-1] or x[:20],
                              'api': x, 'group': a.group or '其他', 'type': a.type})
    if a.from_prod:
        rows = rows_of(DB_PROD)
        want = {x.strip() for x in str(a.from_prod).split(',') if x.strip()}
        hit = [r for r in rows
               if r.get('record_id') in want
               or source_fp(r.get('api接口', '')) in {source_fp(w) for w in want}]
        for r in hit:
            items.append({'name': r.get('名称') or '', 'api': r.get('api接口') or '',
                          'group': r.get('分组类目') or '其他', 'type': a.type,
                          '_rec': r.get('record_id')})
        miss = len(want) - len(hit)
        if miss:
            print(f'   ⚠️ 从成品库只找到 {len(hit)}/{len(want)} 条（{miss} 条未命中）')
    if a.file:
        cand = json.load(open(a.file, encoding='utf-8'))
        if isinstance(cand, dict):
            cand = cand.get('items') or cand.get('sites') or cand.get('lives') or []
        for it in cand:
            items.append({'name': it.get('name') or it.get('名称') or '',
                          'api': it.get('api') or it.get('api接口') or it.get('url') or '',
                          'group': it.get('group') or it.get('分组类目') or a.group or '其他',
                          'type': a.type})
    if not items:
        print('ℹ️ 没有待加条目（用 --api / --from-prod / --file 指定来源）')
        return 1
    print(f'\n① 抽取：{len(items)} 条')

    # ── ② 判平移
    print('② 判平移（形态 / 可进包）：')
    kept = []
    for it in items:
        k = classify_source(it['api'])
        ok_pack = can_enter_pack(k, '通过')
        it['_shape'] = k
        print(f'   · [{k:4s}] 可进包={ok_pack:3s}  {it["api"][:64]}')
        if ok_pack != '是' and not a.force:
            print(f'     ↳ 跳过（{k} 需类包，混进纯直链包会运行时崩；确要加用 --force）')
            continue
        kept.append(it)
    if not kept:
        print('\n⚠️ 无一条可平移——未写入任何内容')
        return 1

    # ── ③ 测活
    print(f'\n③ 真实测活（{len(kept)} 条）：')
    alive = []
    for it in kept:
        v, ev, k = check_auto(it['api'], kind={'采集': 'vod', '直链': 'm3u8'}.get(it['_shape'], None))
        print(f'   {"✅" if v == "通过" else "❌"} {v} [{k}] {ev[:70]}')
        if v != '通过' and not a.force:
            print('     ↳ 跳过（未通过真实测活，禁把未测/失败写进包）')
            continue
        it['_verdict'], it['_ev'] = v, ev
        alive.append(it)
    if not alive:
        print('\n⚠️ 无一条通过真实测活——未写入任何内容')
        return 1

    # ── ④ 查冲突
    # S144·P7 修 bug：旧写法 `'sites' if a.type=='sites' else ('lives' if ... else 'parses')`
    # 在 **a.type 为 None 时默认落到 `parses`**，而 parses 是**解析器**不是线路——
    # 一条采集站（api.php/provide/vod）就这么被写进了 parses，盒子会拿它当解析器调用，必然不可用。
    # 默认段必须按**形态**定：m3u8→lives，其余→sites；parses 只能由 `--type parses` 显式指定。
    if a.type in ('sites', 'lives', 'parses'):
        tgt_key = a.type
    else:
        tgt_key = 'sites'
    existing = pkg.get(tgt_key) or []
    seen = {source_fp(e.get('api') or e.get('url') or '') for e in existing}
    print(f'\n④ 查冲突（目标 {tgt_key} 已有 {len(existing)} 条）：')
    final = []
    for it in alive:
        f = source_fp(it['api'])
        if f in seen:
            print(f'   ⏭ 重复跳过：{it["api"][:64]}')
            continue
        seen.add(f)
        final.append(it)
    print(f'   净新增 {len(final)} 条')

    # ── ⑤ 判边界
    print('⑤ 判边界：')
    bad = []
    for it in final:
        # S144：直播形态（m3u8/m3u/live）自动落 lives，别硬按 --type 塞进 sites。
        # 否则 D 包换路线时会拿到一条 sites 里的 m3u8，type=0 被当采集接口解析 → 必然报错。
        eff = a.type if a.type in ('sites', 'lives', 'parses') else 'sites'
        if it.get('_shape') == '直链' and re.search(r'\.m3u8?($|\?)|/live', it['api'], re.I):
            eff = 'lives'
        it['_eff'] = eff
        if eff == 'lives':
            if not (it['name'] and it['api']):
                bad.append((it, '缺 name 或 url')); continue
            frag = {'name': it['name'], 'url': it['api'],
                    'group': it.get('group') or '其他'}
        else:
            if not (it['name'] and it['api']):
                bad.append((it, '缺 name 或 api')); continue
            if it['api'].startswith(('./', '../')) or not it['api'].startswith('http'):
                bad.append((it, 'api 非 http / 为相对路径=死壳')); continue
            frag = {'name': it['name'], 'api': it['api'],
                    'type': 1 if it.get('_shape') == '采集' else 0,
                    'group': it.get('group') or '其他'}
            if it.get('_shape') == '采集':
                frag['type'] = 1
        it['_frag'] = frag
    for it, why in bad:
        print(f'   ❌ 结构不合法（{why}）：{it["api"][:60]}')
    final = [it for it in final if it.get('_frag')]
    if not final:
        print('\n⚠️ 无一条通过边界校验——未写入任何内容')
        return 1
    # ⑤ 收口断言：段必须与形态一致（防"算出了 eff 却没落到 tgt_key"这类假绿）
    segs = {it['_eff'] for it in final}
    if len(segs) > 1:
        print(f'   ⚠️ 本批形态不统一 {sorted(segs)}——按形态拆分写入')
    elif segs and tgt_key not in segs and a.type not in ('sites', 'lives', 'parses'):
        tgt_key = segs.pop()          # 未显式指定段 → 跟随形态
        print(f'   目标段按形态定为 {tgt_key}')
    # 采集/直链绝不许落进 parses（parses 是解析器，不是线路）
    leak = [it for it in final if it['_eff'] != 'parses' and tgt_key == 'parses']
    if leak and a.type != 'parses':
        print(f'   ❌ {len(leak)} 条线路将被写进 parses（解析器段）——拒绝写入')
        return 1

    print(f'\n📝 将写入（段按形态自动分流）的片段：')
    for it in final:
        print(f'   [{it["_eff"]}] ' + json.dumps(it['_frag'], ensure_ascii=False))

    if a.dry:
        print('\n🔍 --dry：到此为止，未落盘。去掉 --dry 才真写。')
        return 0

    # ── 写入（备份先行）
    bak = f'{pkg_path}.bak_{time.strftime("%m%d_%H%M%S")}'
    shutil.copy2(pkg_path, bak)
    new_pkg = copy.deepcopy(pkg)
    for it in final:                       # 按实际形态分流（m3u8→lives，采集→sites）
        seg = 'lives' if it.get('_eff') == 'lives' else ('parses' if it.get('_eff') == 'parses' else 'sites')
        new_pkg.setdefault(seg, [])
        new_pkg[seg].append(it['_frag'])
    json.dump(new_pkg, open(pkg_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'\n💾 已写入（原文件备份 → {os.path.basename(bak)}）')

    # ── ⑥ 回读（分段核，别只核一个段——m3u8 落 lives、采集落 sites，核错段就是假绿）
    rb = json.load(open(pkg_path, encoding='utf-8'))
    allfps = set()
    for seg in ('sites', 'lives', 'parses'):
        for e in (rb.get(seg) or []):
            allfps.add(source_fp(e.get('api') or e.get('url') or ''))
    miss = [it['api'] for it in final if source_fp(it['api']) not in allfps]
    print(f'⑥ 回读：sites {len(pkg.get("sites") or [])}→{len(rb.get("sites") or [])} ｜ '
          f'lives {len(pkg.get("lives") or [])}→{len(rb.get("lives") or [])} ｜ '
          f'parses {len(pkg.get("parses") or [])}→{len(rb.get("parses") or [])}')
    if miss:
        print(f'   ❌ 回读缺 {len(miss)} 条（写入报成功但读不到，M-R99 同型）：')
        for m in miss[:5]:
            print('      ' + m)
        return 1
    print('   ✅ 回读一致，字段无丢失')
    # S144·P7：光核"这条在不在"不够——写回时若整包重建，顶层键会悄悄少几个。
    # 这里按原包（写前快照）对回读包做原生态比对，顶层键丢失即判失败。
    # `mutable`：本次要写入的段本来就会变（加一条必然改），允许值变、不许字段丢。
    try:
        _touched = {it['_eff'] for it in final} | {tgt_key}
        _ok, _reds, _ = check_preserve(pkg, rb, allow_drop=(), mutable=_touched)
        if not _ok:
            print(f'   ❌ 写回后原包字段丢失（加一条线不该动别的键）——已备份 {bak}，请勿下发')
            return 1
    except Exception as e:
        print(f'   ⚠️ 原生态校验异常（{type(e).__name__}）')

    # 顺带把成品库的「可进包」标为已入包，形成回路
    if not a.no_mark:
        recs = [it.get('_rec') for it in final if it.get('_rec')]
        if recs:
            print(f'   ℹ️ 成品库 {len(recs)} 条可回标「已入包」（本轮不改，避免串味）')
    return 0


def cmd_harvest_cloud(a):
    """harvest cloud：查看云端采集候选册（DB_HARVEST，S095）。"""
    if not DB_HARVEST:
        print('ℹ️ 未配置采集候选库（DB_HARVEST 为空）')
        return
    st, rows = _cloud_rows(DB_HARVEST)
    if st == 'fail':
        print('⚠️ 云端采集候选册不可达（未授权/网络异常）——**无法判定**，'
              '请稍后重试（勿据此认为"无候选"）')
        sys.exit(1)
    if not rows:
        print('ℹ️ 云端采集候选册为空（先跑 tvbox probe harvest scan/pull）')
        return
    lim = getattr(a, 'limit', 30) or 30
    print(f'☁️ 云端采集候选册（DB_HARVEST）共 {len(rows)} 条，显示前 {min(lim, len(rows))}：\n')
    print(f'   {"类目":<6}{"名称":<26}{"测活":<8}{"入库":<8}api接口')
    print('   ' + '─' * 72)
    for r in rows[:lim]:
        print(f'   {str(_cloud_txt(r.get("分组类目"))):<6}'
              f'{str(_cloud_txt(r.get("名称")))[:24]:<26}'
              f'{str(_cloud_txt(r.get("测活状态"))):<8}'
              f'{str(_cloud_txt(r.get("入库状态"))):<8}'
              f'{_cloud_txt(r.get("api接口"))[:40]}')
    print('\n   ℹ️ 这些是**跨会话持久**的采集候选（本地 harvest_candidates/ 会随沙箱重置丢失）')


def _cloud_txt(v):
    """云端字段取显示文本（select 回显为 {text,style}；text 可能是 None）。"""
    if isinstance(v, dict):
        return v.get('text') or ''
    return v if v is not None else ''


def _cloud_num(v):
    """云端数值字段取 number（回显为 {number:N}）。"""
    if isinstance(v, dict):
        v = v.get('number', v.get('text', 0))
    try:
        return float(v)
    except Exception:
        return 0.0


# ══════════════════ token / limits：GitHub 鉴权管理（S089） ══════════════════

def cmd_token(a):
    """token：GitHub 凭据状态 / 配置指引 / 写入 token 文件。

    子动作（--set/--clear 互斥；默认=查看状态）：
      · 默认     → 三态解析结果 + 遮罩显示 + **实时配额**（这一条是"看得见的收益"）
      · --set V  → 写入 token 文件（0600 权限，仅本用户可读）
      · --clear  → 删除 token 文件
    """
    fp = _token_file_path()

    if getattr(a, 'clear', False):
        if os.path.exists(fp):
            os.remove(fp)
            print(f'🗑 已删除 token 文件：{fp}')
            print('   （环境变量 GITHUB_TOKEN / gh CLI 若仍配置，依然生效——优先级更高）')
        else:
            print(f'ℹ️ token 文件不存在，无需删除：{fp}')
        return

    if getattr(a, 'set', None):
        tok = a.set.strip()
        if len(tok) < 20 or any(c.isspace() for c in tok):
            print('❌ token 形态不符（应 ≥20 位且无空白）——拒绝写入，防手误覆盖')
            sys.exit(1)
        try:
            with io.open(fp, 'w', encoding='utf-8') as f:
                f.write('# TVBox 探源司 · GitHub token（S089）\n')
                f.write('# 生成：https://github.com/settings/tokens  （只读 public_repo 即可）\n')
                f.write(tok + '\n')
            os.chmod(fp, 0o600)
            print(f'✅ 已写入 token 文件：{fp}（权限 0600）')
            print(f'   遮罩：{_mask(tok)}')
            print('   ℹ️ 生效于**下次进程**（本进程缓存已定）；可立即 `tvbox probe token` 验证')
        except Exception as e:
            print(f'❌ 写入失败：{type(e).__name__}：{e}')
            sys.exit(1)
        return

    # 默认：查看状态
    st, tok, src = _load_token(refresh=True)
    print('🔑 GitHub 凭据状态\n')
    if st == 'ok':
        print(f'   ✅ 已配置  {_mask(tok)}')
        print(f'   来源：{src}')
    elif st == 'none':
        print('   ⚪ 未配置（当前匿名调用，API 限额 60 req/h）')
    else:
        print(f'   ❌ 配置读取失败：{src}')
        print('      （**不是"未配置"**——请检查文件权限/编码，勿误判为"我没配"）')

    print('\n📊 实时配额：')
    lst, rl = _rate_limit()
    if lst == 'ok':
        lim = rl.get('limit', 0)
        rem = rl.get('remaining', 0)
        reset = rl.get('reset', 0)
        mins = max(0, int((reset - time.time()) / 60)) if reset else 0
        print(f'   限额 {lim}/h · 剩余 {rem} · 重置于 {mins} 分钟后'
              f'（{time.strftime("%H:%M", time.localtime(reset)) if reset else "—"}）')
        # ★ S181：**必须声明这个数字不可靠**。实测同一秒连查三次，0 与 45 交替、
        #   reset 相差 30 分钟——GitHub 侧有多个配额桶，落到哪个桶就报哪个值。
        #   不加这句，用户会拿它当准数：看到 58 就放心跑，结果全轮空转（真实发生过）。
        print('   ℹ️ 该数字**仅供参考**：GitHub 侧存在多个配额桶，同一时刻查询可能'
              '在不同桶间跳（实测 0 与 45 交替、reset 相差 30 分钟）。'
              '真实额度以 `monitor check` 的实测结论为准。')
        if rl.get('stale'):
            print('   ℹ️ 该数值疑似**上一时间窗的缓存快照**（reset 已过期）——'
                  '仅供参考，实际以调用返回的限流响应为准')
        if lim <= 60:
            # ★ S169：同样按 reset 是否过期分岔，不无脑说"等一会再来"。
            if rl.get('stale') or mins == 0:
                print('   ⚠️ 匿名额度（60/h）且已耗尽，reset 时刻已过——**等不会自动恢复**'
                      '（实测两次查询 reset 相差 2 分钟，是同 IP 不同配额桶的抖动，'
                      '不是同一窗口的重置承诺）。确定解法：配 token 升 5000/h。')
            else:
                print('   ⚠️ 匿名额度（60/h）——一次 monitor check 约耗 3 请求，几只仓库即触顶；'
                      '建议配 token')
    else:
        print('   ⚠️ 配额查询失败（网络/API 异常）——无法判定，非"额度用尽"')

    if st != 'ok':
        print('\n💡 配置方式（三选一，优先级从高到低）：')
        print('   ① 环境变量： export GITHUB_TOKEN=ghp_xxx')
        print('   ② token 文件： tvbox probe token --set ghp_xxx')
        print(f'       （写入 {fp}，权限 0600，随技能走）')
        print('   ③ gh CLI：   gh auth login（已登录则自动复用）')
        print('   配额提升：60/h（匿名）→ 5000/h（鉴权）；检索 API：10/min → 30/min')


def cmd_limits(a):
    """limits：只看配额（供自检/脚本消费；--json 出机器可读）。

    S089：**强制 refresh**——配额是"实时量"，用缓存会给出过期数字（实证：同进程内
    `token` 显示剩余 28、`limits` 显示 0，因后者读缓存）。凡展示"实时状态"的命令，
    必须绕缓存直取，否则用户看到的是"上次的真相"。
    """
    st, tok, src = _load_token(refresh=True)
    lst, rl = _rate_limit()
    if getattr(a, 'json', False):
        print(json.dumps({
            'token_state': st, 'token_source': src, 'token_masked': _mask(tok),
            'rate_state': lst, 'rate': rl,
        }, ensure_ascii=False))
        return
    print(f'🔑 token：{_mask(tok)}（{src or "未配置"}）')
    if lst == 'ok':
        _s = '（疑陈旧快照）' if rl.get('stale') else ''
        print(f'📊 配额：剩余 {rl.get("remaining")}/{rl.get("limit")}{_s}')
    else:
        print('📊 配额：查询失败——无法判定')


# ══════════════════ self-check：四监测器活体探针 + 假阳性基线 ══════════════════

def _sc_monitor(results):
    """self-check 组1：存活判定器/内容指纹/目录差集/活跃度判定/数据基座三态（C6 拆出，顺序不变）。"""
    # ① _alive_verdict：404→已封禁/删除（真阳性）；200→存活（清理后）；
    #    0→无法判定（**不是**"死"——假阳性基线）
    # ★ S177 步2.5：补齐异常隔离。原先是裸调用，被调函数一旦**抛异常**（不是返回错值），
    #   整个 self-check 就地崩成 traceback，33 条红绿一条都不打印、JSON 也没有。
    #   这正是 M-R63 的孪生形态：M-R63 是"坏了还报绿"，这个是"坏了连绿都不报"。
    #   写法照抄本文件后面 26 条探针**已经在用**的形态（预设 False + try + 打印原因 + 照常 append），
    #   不发明新结构：hit/clean/base 三态一律先落 False（= 判定不成立），
    #   异常原因必须打印出来（不许静默吞掉，M-R60）。
    hit_alive = clean_alive = base_alive = False
    try:
        hit_alive = _alive_verdict(404) == '已封禁/删除'
        clean_alive = _alive_verdict(200) == '存活'
        base_alive = _alive_verdict(0) == '无法判定'
    except Exception as e:
        print(f'  ⚠️ 存活判定器探针异常：{type(e).__name__}: {e}')
    results.append(('存活判定器', hit_alive, clean_alive, base_alive))

    # ② _content_sha：改 1 字节 → 指纹变（真阳性）；恢复 → 一致（清零）；
    #    同内容两次 → 必一致（假阳性基线：不得误报变更）
    hit_sha = clean_sha = base_sha = False
    try:
        base = b'{"sites":[{"name":"a","api":"http://x"}]}'
        sha1 = _content_sha(base)
        sha2 = _content_sha(base[:-1] + b']')     # 改尾字节
        hit_sha = sha1 != sha2
        clean_sha = _content_sha(base) == sha1
        base_sha = _content_sha(base) == _content_sha(base)
    except Exception as e:
        print(f'  ⚠️ 内容指纹探针异常：{type(e).__name__}: {e}')
    results.append(('内容指纹', hit_sha, clean_sha, base_sha))

    # ③ _dir_diff：新增目录 → 报 added（真阳性）；删注入 → 差集空（清零）；
    #    相同 → 双侧空（假阳性基线）
    hit_dir = clean_dir = base_dir = False
    guard_dir = False
    old = ['a', 'b']
    hit_dir = clean_dir = base_dir = False
    guard_dir = False
    # ★ S177 步2.5：这三行原先也是裸调，_dir_diff 一提异常整段就崩。
    try:
        added, removed = _dir_diff(old, ['a', 'b', 'c'])
        hit_dir = added == ['c'] and removed == []
        clean_dir = _dir_diff(old, old) == ([], [])
        base_dir = _dir_diff(old, ['a', 'b']) == ([], [])
    except Exception as e:
        print(f'  ⚠️ 目录差集探针异常（差集本身）：{type(e).__name__}: {e}')
    # S088：守卫断言——本期目录树缺失（n_files=None）时，_record_sample 必须标
    # 'dirs_undecidable' 而**不得**产出 removed（防"读失败"被谎报成"作者删光所有文件"）
    _fake_t = {'仓库URL': 'https://example.invalid/x'}
    _fake_rec_bad = {'alive': '无法判定', 'content_sha': '', 'n_files': None,
                     'n_commits_30d': None, 'pushed_at': None, 'dirs': []}
    global ALOG
    _bak_alog = open(ALOG, encoding='utf-8').read() if os.path.exists(ALOG) else None
    _alog_orig = ALOG
    try:
        _alog_tmp = {'latest_ts': '', 'repos': {'https://example.invalid/x': {'history': [
            {'ts': '', 'alive': '存活', 'content_sha': 'abc', 'n_files': 290,
             'n_commits_30d': 0, 'dirs': ['a.json', 'b.json']}]}}}
        json.dump(_alog_tmp, open(ALOG, 'w', encoding='utf-8'))
        _saved, _diff = _record_sample(_fake_t, _fake_rec_bad)
        guard_dir = bool(_diff.get('dirs_undecidable')) and not _diff.get('dirs_removed')
    except Exception as e:
        # ★ S177 步2.5：原实现只有 finally 没有 except —— 清理做完了，异常照旧往外冒，
        #   整个 self-check 崩掉。这里补一个 except，**finally 的清理逻辑一个字没动**。
        print(f'  ⚠️ 目录差集探针异常（守卫断言）：{type(e).__name__}: {e}')
    finally:
        ALOG = _alog_orig
        if _bak_alog is not None:
            open(ALOG, 'w', encoding='utf-8').write(_bak_alog)
        elif os.path.exists(ALOG):
            os.remove(ALOG)
    results.append(('目录差集', hit_dir and guard_dir, clean_dir, base_dir))

    # ④ _activity_verdict：S088 四档口径——两年无 push 且 0 提交 → 归档（真阳性）；
    #    近期有提交 → 活跃（清理后）；数据缺失 → 无法判定（假阳性基线：不得误报停滞）
    two_years_ago = time.time() - 2 * 365 * 86400
    half_year_ago = time.time() - 200 * 86400
    hit_act = clean_act = base_act = False
    mid_act = False
    try:
        hit_act = _activity_verdict(0, two_years_ago) == '归档'
        clean_act = _activity_verdict(5, time.time()) == '活跃'
        base_act = _activity_verdict(None, None) == '无法判定'
        # S088 新增：200 天未 push 应判'停滞'（非'归档'）——防阈值回退致误判废弃
        mid_act = _activity_verdict(0, half_year_ago) == '停滞'
    except Exception as e:
        print(f'  ⚠️ 活跃度判定探针异常：{type(e).__name__}: {e}')
    results.append(('活跃度判定', hit_act and mid_act, clean_act, base_act))

    # ⑤ 数据基座三态：损坏态必须被识别且拒绝回写（M-R60）
    #    注意：本探针**故意注入坏 JSON**，load_alog 会打告警——属预期噪声，
    #    故注入期静默 stdout，并逐字段断言（不止看 state，还看 save 拒写）
    bak = None
    had_file = os.path.exists(ALOG)
    if had_file:
        bak = open(ALOG, encoding='utf-8').read()
    hit_base = False
    _buf = io.StringIO()
    _old = sys.stdout
    try:
        open(ALOG, 'w', encoding='utf-8').write('{ 这不是合法 JSON')
        sys.stdout = _buf                       # 抑制注入期预期告警
        st, obj = load_alog()
        saved, _reason = save_alog({'latest_ts': '', 'repos': {}})
        hit_base = (st == 'corrupt' and saved is False
                    and isinstance(obj, dict) and saved is False)
    finally:
        sys.stdout = _old
        if had_file:
            open(ALOG, 'w', encoding='utf-8').write(bak)
        elif os.path.exists(ALOG):
            os.remove(ALOG)
    # 清理后必须还原为初始态：原无文件→现无文件；原有文件→内容一致
    clean_base = (os.path.exists(ALOG) == had_file
                  and (not had_file or open(ALOG, encoding='utf-8').read() == bak))
    results.append(('数据基座三态', hit_base, clean_base, True))
def _sc_fetch_mirror(results):
    """self-check 组2：HTTP 拉取三态（404/200/连接失败）（C6 拆出，顺序不变）。"""
    # ⑥ HTTP 拉取三态：S088 实证——GitHub raw 对不存在的文件返回 HTTP 404 +
    #    正文 "404: Not Found"（curl 退出码仍为 0）→ 曾被折叠成 ('ok', 错误页)。
    #
    #    设计要点（S088 二次修正）：**探针不得依赖不稳定的外部网络**。首版用真实
    #    raw.githubusercontent.com 驱动，结果受网络抖动/限流影响而"时绿时红"
    #    （实测 rc=28 超时 → 假红）——探针本身必须是**可控、离线可重复**的，
    #    否则它自己就成了新的噪声源。故改为**本地 http.server** 驱动：
    #      · 存在的路径 → 200 + 正文   → 'ok'（真阴性）
    #      · 不存在的路径 → 404 + 正文  → 'notfound'（真阳性：不得折叠成 ok）
    #      · 连接拒绝（关掉的端口）     → 'fail'（假阳性基线：网络失败≠不存在≠成功）
    hit_http = clean_http = base_http = False
    import http.server as _hs
    import threading as _th

    class _H(_hs.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.endswith('/exists.json'):
                body = b'{"sites":[{"name":"a"}]}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = b'404: Not Found'      # 复刻 GitHub raw 的 404 正文
                self.send_response(404)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    _g = globals()
    _real_hg = _g['http_get']
    try:
        # C1：底层已统一到 core.http_get（自带 SSRF 拦截），本地 127.0.0.1 探针
        # server 会被当成内网拦截，无法直接驱动。改为桩 http_get 模拟三种返回，
        # 既离线可控（符合 S088 二次修正"探针不得依赖网络"），又仍验证 _fetch 的
        # 状态码映射业务（404→notfound、200→ok、连接失败→fail）。
        def _fake_hg(_u, timeout=None, ua=None, return_status=False):
            if _u.endswith('/nope.json'):
                return ('ok', (404, '404: Not Found'), None)
            if _u.endswith('/exists.json'):
                return ('ok', (200, '{"sites":[{"name":"a"}]}'), None)
            if ':59999' in _u:
                return ('error', None, 'conn')
            return ('ok', (200, 'x'), None)
        _g['http_get'] = _fake_hg
        st_nf, _ = _fetch('http://127.0.0.1:9/nope.json', timeout=10)
        st_ok, txt_ok = _fetch('http://127.0.0.1:9/exists.json', timeout=10)
        st_bad, _ = _fetch('http://127.0.0.1:59999/x.json', timeout=5)
        hit_http = (st_nf == 'notfound')                 # 404 不得折叠成 ok
        clean_http = (st_ok == 'ok' and txt_ok and '404' not in txt_ok[:40])
        base_http = (st_bad == 'fail')                   # 连接失败不得冒充 notfound/ok
    except Exception as _e:
        # M-R60⑫：探针自身失败须显式告警，不得静默折叠成"未捕获"
        print(f'  ⚠️ HTTP 三态探针自身失败（{type(_e).__name__}: {_e}）——本次判定不可信')
    finally:
        _g['http_get'] = _real_hg
    results.append(('HTTP 拉取三态', hit_http, clean_http, base_http))
def _sc_platform(results):
    """self-check 组3：鉴权模块/多平台适配（C6 拆出，顺序不变）。"""
    # ⑦ 鉴权模块（S089 新增）：token 三态解析 + 遮罩 + 请求头注入。
    #
    #    为何必须探：鉴权是"看得见的收益"（60→5000 req/h），一旦解析逻辑回退
    #    （如把"读取失败"折叠成"无 token"），用户会以为"配了没用"而无从排查——M-R60
    #    的静默折叠。探针用**临时 token 文件 + 环境变量**双路注入，**不触网**：
    #      · 注入合法 token 到临时文件 → _load_token 必须 'ok' 且值正确（真阳性）
    #      · 清除注入 → 回落到环境变量/无（清理后回到基线）
    #      · token 文件为"存在但非法内容" → 必须 'none'（不是 'ok'，防手误当有效）
    #      · 遮罩：_mask 不得泄露中段
    _tok_bak = {}
    for _n in ENV_NAMES:
        if _n in os.environ:
            _tok_bak[_n] = os.environ[_n]
            del os.environ[_n]
    _fp = _token_file_path()
    _fp_existed = os.path.exists(_fp)
    _fp_bak = open(_fp, encoding='utf-8').read() if _fp_existed else None
    hit_auth = clean_auth = base_auth = False
    try:
        _fake = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'   # 40 位合法形态
        with io.open(_fp, 'w', encoding='utf-8') as f:
            f.write(_fake + '\n')
        _load_token(refresh=True)
        st_a, tok_a, _src = _load_token(refresh=True)
        hit_auth = (st_a == 'ok' and tok_a == _fake
                    and _mask(_fake) == 'ghp_' + '*' * (len(_fake) - 8) + _fake[-4:]
                    and 'Bearer' in ' '.join(_gh_headers()))
        # 清理：改写为"存在但非法" → 必须判 none（不得当有效）
        with io.open(_fp, 'w', encoding='utf-8') as f:
            f.write('# 注释行\nnot-a-token\n')
        _load_token(refresh=True)
        st_b, tok_b, _s2 = _load_token(refresh=True)
        clean_auth = (st_b == 'none' and tok_b == '')
        # 假阳性基线：无 token 时请求头不得含 Authorization
        base_auth = ('Authorization' not in ' '.join(_gh_headers()))
    except Exception as _e:
        print(f'  ⚠️ 鉴权探针自身失败（{type(_e).__name__}: {_e}）——本次判定不可信')
    finally:
        for _n, _v in _tok_bak.items():
            os.environ[_n] = _v
        if _fp_existed:
            with io.open(_fp, 'w', encoding='utf-8') as f:
                f.write(_fp_bak)
        elif os.path.exists(_fp):
            os.remove(_fp)
        _load_token(refresh=True)     # 清缓存，防止注入值残留污染后续命令
    results.append(('鉴权模块', hit_auth, clean_auth, base_auth))

    # ⑧ 多平台适配（S091 新增）：URL 解析 + 时间戳跨格式解析。
    #
    #    为何必须探：多平台监测的**共性缺陷**是把某平台的时间格式硬编码——GitHub 用
    #    `Z`（UTC），GitLab 用 `-04:00`（带偏移+毫秒），Gitee 用 `+08:00`。硬编码
    #    `%Y-%m-%dT%H:%M:%SZ` 会让后两者**静默解析失败** → pushed_at=None → 活跃度
    #    误判"无法判定"（S088 同型：格式不符被折叠成"无数据"）。故逐格式断言：
    #      · 三种平台格式各解析出**正确时间戳**（真阳性：跨格式不丢）
    #      · 垃圾输入 / 空值 → **必须返回 None**（不得折叠成 0=1970，否则误判"归档"）
    #      · URL 解析：GitHub/Gitee/GitLab 四元组正确；自建 host → 'Gitea' 族
    # ★ S177 步2.5：整段包一层。这三条探针内部调了 6 个生产函数（_parse_ts/_parse_repo 等），
    #   逐个包 try 会把二十行断言切得读不懂；统一预设 False + 一段 try 即可达到同样效果：
    #   任何一个抛异常 → 三条一起记 False + 打印原因 → 其余 30 条照常跑完。
    hit_plat = clean_plat = _base_plat = False
    try:
        _t_z = _parse_ts('2026-02-09T12:00:00Z')
        _t_gl = _parse_ts('2026-10-15T14:11:38.000-04:00')
        _t_ge = _parse_ts('2025-09-25T00:32:16+08:00')
        _t_day = _parse_ts('2025-09-25')
        hit_plat = (isinstance(_t_z, float) and isinstance(_t_gl, float)
                    and isinstance(_t_ge, float) and isinstance(_t_day, float)
                    and abs(_t_gl - _t_z) > 86400 * 200       # 不同日期，非同一值
                    and _parse_repo('https://gitlab.com/a/b')[:3] == ('a', 'b', 'GitLab')
                    and _parse_repo('https://gitee.com/a/b')[:3] == ('a', 'b', 'Gitee'))
        clean_plat = (abs(_t_z - 1770638400) < 2               # 2026-02-09T12:00:00Z
                      and _parse_ts(None) is None and _parse_ts('') is None
                      and _parse_ts('garbage') is None)
        # 假阳性基线：自建 host 归 Gitea 族（不误判为固定平台）、None 不得成 0
        _base_plat = (_parse_repo('https://git.code.tencent.com/a/b')[2] == 'Gitea'
                      and _parse_ts('garbage') != 0 and _parse_ts('garbage') is None)
    except Exception as e:
        print(f'  ⚠️ 多平台适配探针异常：{type(e).__name__}: {e}')
    results.append(('多平台适配', hit_plat, clean_plat, _base_plat))
def _sc_harvest(results):
    """self-check 组4：harvest判据一致性/活跃度预测（C6 拆出，顺序不变）。"""
    # ⑨ harvest 判据一致性（S092 新增）：harvest 类目必须与整包坊 split 同口径。
    #
    #    为何必须探：harvest 与 split 是**两条独立的抽取路径**，若各自实现类目判据，
    #    同一源会在探源司算「配置」、在整包坊算「采集」→ 下游 ingest 类目分叉
    #    （M-R38 单一事实源违例）。故断言：
    #      · harvest 判据**确实复用**整包坊 classify_line（同输入同输出，逐桶比对）
    #      · 整包坊不可用时**显式降级**（stderr 告警），绝不用错口径静默产出
    _clf = _classify_fn()
    _probe_cfg = {
        'sites': [
            {'name': 'A', 'type': 3, 'api': 'http://a.com/api.php/provide/vod/'},
            {'name': 'B', 'type': 0, 'api': 'http://b.com/live.m3u8'},
            {'name': 'C', 'type': 1, 'api': 'http://c.com/spider.py'},
            {'name': 'D', 'type': 1, 'api': 'http://d.com/drpy/x'},
            {'name': 'E', 'type': 1, 'api': 'http://e.com/x'},
        ],
        'lives': [{'name': 'L', 'url': 'http://l.com/1.m3u8'}],
        'parses': [{'name': 'P', 'url': 'http://p.com/jx', 'type': 1}],
    }
    hit_hv = False
    if _clf is not None:
        _hl = _extract_lines(_probe_cfg)
        _hit = (_clf.classify_line('sites', _probe_cfg['sites'][0]) == '采集'
                and _clf.classify_line('sites', _probe_cfg['sites'][2]) == 'py'
                and _clf.classify_line('lives', _probe_cfg['lives'][0]) == '直播')
        # 逐桶比对：harvest 输出类目 == 整包坊判据（真阳性·口径不分叉）
        ok_same = True
        for x in _hl:
            src = [c for c in _probe_cfg.get(x['_kind'], [])
                   if (c.get('api') or c.get('url')) == x['api接口']]
            if not src:
                continue
            if _clf.classify_line(x['_kind'], src[0]) != x['分组类目']:
                ok_same = False
        hit_hv = bool(_hit and ok_same and _hl)
    clean_hv = (hit_hv and
                # 降级路径可判定：_fallback_category 存在且 type=0→直链
                _fallback_category('sites', {'type': 0}) == '直链'
                and _fallback_category('sites', {'type': 9}) == '其他')
    # 假阳性基线：整包坊确实可达（否则"一致"是空比对的假通过）
    base_hv = (_clf is not None and hasattr(_clf, 'classify_line')
               and _clf.classify_line('parses', {}) == '其他')
    results.append(('harvest判据一致性', hit_hv, clean_hv, base_hv))

    # ⑩ 活跃度预测（S093 新增）：趋势外推的三态命门。
    #
    #    为何必须探：预测是**最容易编造**的判定——拿 2 期数据外推、或把 `None`
    #    （未取到）当 0 参与回归，都会产出"看似合理"的错误风险等级。故四路断言：
    #      · 真阳性：明显下降序列 → '升高'；上升 → '增长'（趋势方向不丢）
    #      · 命门·None 不填充：含 None 空洞的序列，有效点统计**跳过 None**（不当 0）
    #      · 命门·不足不预测：样本 < 下限 → '数据不足'（risk=None，绝不硬凑）
    #      · 假阳性基线：全 None → '数据不足'（不得报任何风险）
    def _mk_hist(vals, gap_days=7):
        import datetime as _dt
        _now = time.time()
        return [{'ts': _dt.datetime.utcfromtimestamp(
                    _now - (len(vals) - 1 - i) * gap_days * 86400).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 'n_commits_30d': v} for i, v in enumerate(vals)]
    # ★ S177 步2.5：同上，整段包一层（内部 6 处 _forecast_risk 调用）。
    hit_fc = clean_fc = base_fc = False
    try:
        _f_down = _forecast_risk(_mk_hist([30, 25, 20, 15, 10, 5, 2]))
        _f_up = _forecast_risk(_mk_hist([2, 5, 9, 14, 20, 28, 35]))
        hit_fc = (_f_down['state'] == 'ok' and _f_down['risk'] == '升高'
                  and _f_up['state'] == 'ok' and _f_up['risk'] == '增长'
                  and _f_down['n_pts'] == 7)
        # 命门：None 空洞 → 跳过不填充（有效点 = 非 None 数），且样本不足 → 明确不预测
        _f_hole = _forecast_risk(_mk_hist([20, None, 18, None, 15, None, 12, None, 10, None, 8, None, 7]))
        _f_few = _forecast_risk(_mk_hist([20, 18, 15]))
        clean_fc = (_f_hole['n_pts'] == 7 and _f_hole['state'] == 'ok'
                    and _f_few['state'] == '数据不足' and _f_few['risk'] is None)
        # 假阳性基线：全 None / 空序列 → 数据不足（绝不报风险）；跨度太短 → 不预测
        _f_none = _forecast_risk(_mk_hist([None] * 8))
        _f_short = _forecast_risk(_mk_hist([20, 19, 18, 17, 16, 15, 14], gap_days=0.1))
        base_fc = (_f_none['state'] == '数据不足' and _f_none['risk'] is None
                   and _forecast_risk([])['state'] == '数据不足'
                   and _f_short['state'] == '数据不足')
    except Exception as e:
        print(f'  ⚠️ 活跃度预测探针异常：{type(e).__name__}: {e}')
    results.append(('活跃度预测', hit_fc, clean_fc, base_fc))
def _sc_search_cloud(results):
    """self-check 组5：search三态/上云date列/upsert幂等/XML判活/评分分层/残留识别/回执判真（C6 拆出，顺序不变）。"""
    # ⑪ search 三态与写侧保护（S094 新增）：搜索模块的折叠与覆盖。
    #
    #    为何必须探：S094 真实跑 search 抓出三类缺陷（合成数据照不出）：
    #      · `_api_get` 只看正文不看状态码 → GitLab `{"message":"401 Unauthorized"}`
    #        被判成 'ok'（**错误响应被当成功**）→ 下游拿到 {} 静默产"0 结果"
    #      · `_save_search` 为覆盖写 → `search gitee` 空结果**整表覆盖** github 的 5 条
    #        （跨源覆盖 = 折叠的写侧形态，M-R60⑧ 变体）
    #      · verify 用裸探测主页判存活 → GitHub 返回 0 误判"无法判定"
    #    故断言：
    #      · 真阳性：`_save_search` **合并**（空列表不覆盖已有非空，同 url 去重）
    #      · 命门：401 语义在 `_api_get` 中被识别（离线注入不触网——用状态码解析分支）
    #      · 假阳性基线：正常保存后能读回、且坏旧文件不阻断本次写入
    _sfl = os.path.join(SELF_DIR, 'search_results.json')
    _bak = None
    if os.path.exists(_sfl):
        _bak = io.open(_sfl, encoding='utf-8').read()
        os.remove(_sfl)          # 探针须**隔离初始状态**（否则合并语义会叠加历史候选，
                                 # 断言 len==2 在有历史结果时必失败——探针自身不健壮）
    try:
        _save_search([{'url': 'https://x/a', 'stars': 1}], sync_cloud=False)
        _save_search([{'url': 'https://x/b', 'stars': 2}], sync_cloud=False)
        _after = json.load(io.open(_sfl, encoding='utf-8'))['repos']
        hit_sr = (len(_after) == 2                       # 合并：两条都在
                  and {r['url'] for r in _after} == {'https://x/a', 'https://x/b'})
        _save_search([], sync_cloud=False)               # 空结果
        _after2 = json.load(io.open(_sfl, encoding='utf-8'))['repos']
        clean_sr = len(_after2) == 2                     # 空列表不得覆盖已有非空
        # 假阳性基线：同 url 去重（再存 a → 仍 2 条，非 3）
        _save_search([{'url': 'https://x/a', 'stars': 9}], sync_cloud=False)
        _after3 = json.load(io.open(_sfl, encoding='utf-8'))['repos']
        base_sr = (len(_after3) == 2
                   and next(r for r in _after3 if r['url'] == 'https://x/a')['stars'] == 9)
    finally:
        if _bak is not None:
            io.open(_sfl, 'w', encoding='utf-8').write(_bak)
        elif os.path.exists(_sfl):
            os.remove(_sfl)
    results.append(('search三态与写保护', hit_sr, clean_sr, base_sr))

    # ⑫ 上云 upsert 与指纹幂等（S095 新增）：search/harvest 产出上云的三态命门。
    #
    #    为何必须探：上云是**最容易静默丢数据**的一环——
    #      · 稳定指纹若不幂等 → 每次搜索插重复行（同 S088 record_id 教训：一次 check 插 4 条）
    #      · 未配置库若被当"成功" → 用户以为已上云、实则只存本地（下次会话丢失）
    #      · 全空行若照写 → 污染云端（无用空记录）
    #      · select/number 若不显式包装 → 平台静默丢弃（S027 铁律）
    #    断言（全程离线，仅测「行构造 + 决策」，不触网）：
    #      · 真阳性：同一 url 两次 → 同一 候选id（幂等）；不同 url → 不同 id
    #      · 命门：db_id 为空 → (False,0,0)（未配置不得报成功）；全空行被 skip
    #      · 假阳性基线：select 列在 props 中被包成 {'select':...}、number 列被包成 {'number':...}
    # ★ S177 步2.5：包住开头的裸调用。注意 `clean_ck` 在下面「分批边界」还会被二次加工，
    #   所以这里不能整体 return 或跳过后续赋值 —— 只把可能抛异常的那几行关进 try。
    hit_ck = clean_ck = base_ck = False
    try:
        _id1 = _cand_id('GitHub', 'https://github.com/a/b')
        _id2 = _cand_id('GitHub', 'https://github.com/a/b')   # 同输入
        _id3 = _cand_id('Gitee', 'https://github.com/a/b')    # 平台不同 → 应不同
        _id4 = _cand_id('GitHub', 'https://github.com/a/c')   # url 不同 → 应不同
        hit_ck = (_id1 == _id2 and _id1 != _id3 and _id1 != _id4
                  and _id1.startswith('sr-') and len(_id1) == 15)
        # 命门：空库配置 → 不得报成功；全空行 → skip（不污染）
        _empty_ok, _ea, _eu = _cloud_upsert([{'候选id': 'x'}], '', '候选id')
        clean_ck = (_empty_ok is False and _ea == 0 and _eu == 0
                    and _cand_id('', '') != _cand_id('', ' ')
                    and _harvest_id('http://a') == _harvest_id('http://a')
                    and _harvest_id('http://a') != _harvest_id('http://b'))
        # 假阳性基线：指纹稳定（harvest 同 api 同 id）；空串 id 不相等（不误判同一候选）
        base_ck = (_harvest_id('http://x/y') .startswith('hv-')
                   and len(_harvest_id('http://x/y')) == 15)
    except Exception as e:
        print(f'  ⚠️ 上云幂等探针异常：{type(e).__name__}: {e}')
    # 分批边界（S095 修）：平台限 1–100 条/批——断言常量有效且分批区间无遗漏/不越界
    #   113 条应切成 [0:100]+[100:113] 两批；100 条恰好 1 批；0 条 0 批（不空写）。
    _nb = lambda n: (n + CLOUD_BATCH - 1) // CLOUD_BATCH if n > 0 else 0
    clean_ck = (clean_ck and CLOUD_BATCH <= 100 and CLOUD_BATCH >= 1
                and _nb(113) == 2 and _nb(100) == 1 and _nb(101) == 2
                and _nb(0) == 0 and _nb(1) == 1)
    # ★ S169 命门：date 列**不许**被包成 {'text':...}（会静默丢列，回执仍 success）。
    #   本段不打网络：直接调 _cloud_upsert 的字段包装分支（把 db_id 置空会提前 return，
    #   故改用「空库配置」以外的路径——用内存桩截获 props，见下）。
    _cap = {}
    _orig_batch_add = globals().get('_count_success')
    try:
        # 桩：把 _cloud_id_map 与写入接口换掉，只截获真正要发的 props
        _real_map, _real_api = _cloud_id_map, api

        def _stub_map(_db, _f):
            return {'r1': 'REC1'}

        def _stub_api(_script, *args, **kw):
            for i, a in enumerate(args):
                if a == '--records':
                    _cap['records'] = json.loads(args[i + 1])
            return '{"results":[{"index":0,"id":"REC1","success":true}]}'
        globals()['_cloud_id_map'] = _stub_map
        globals()['api'] = _stub_api
        try:
            _cloud_upsert(
                [{'id': 'r1', '上次检查': '2026-10-19T06:10:17Z',
                  '下次到期': '2026-10-26T06:10:17Z', '变更摘要': 'x',
                  '存活状态': '存活'}],
                'DB_STUB', 'id',
                select_cols=('存活状态',), date_cols=('上次检查', '下次到期'))
        finally:
            globals()['_cloud_id_map'] = _real_map
            globals()['api'] = _real_api
        _p = ((_cap.get('records') or [{}])[0] or {}).get('properties') or {}
        hit_date = (_p.get('上次检查') == {'date': '2026-10-19T06:10:17Z'}
                    and _p.get('下次到期') == {'date': '2026-10-26T06:10:17Z'})
        # 假阳性基线：同批的普通文本列**必须**仍是 text（别为了修 date 把 text 也改了）
        base_date = (_p.get('变更摘要') == {'text': 'x'}
                     and _p.get('存活状态') == {'select': '存活'})
        # 清理后清零：不传 date_cols 时退回 text（证明是 date_cols 在起作用，不是硬编码）
        _cap.clear()
        globals()['_cloud_id_map'] = _stub_map
        globals()['api'] = _stub_api
        try:
            _cloud_upsert([{'id': 'r1', '上次检查': 'T'}], 'DB_STUB', 'id')
        finally:
            globals()['_cloud_id_map'] = _real_map
            globals()['api'] = _real_api
        _p2 = ((_cap.get('records') or [{}])[0] or {}).get('properties') or {}
        clean_date = (_p2.get('上次检查') == {'text': 'T'})
    except Exception as e:
        print(f'  ⚠️ date 列包装探针异常：{type(e).__name__}: {e}')
        hit_date = clean_date = base_date = False
    results.append(('上云date列不被当text写丢', hit_date, clean_date, base_date))
    results.append(('上云upsert与指纹幂等', hit_ck, clean_ck, base_ck))

    # ⑬ XML 源判活通道（S142·离线样本，不打网络）
    #    病根：用户 10-14 手工把松视1/2（type=0 XML 源）加进 L 包，判据是"真拉页数+数video"，
    #    活得好好的；而 livecheck 写成引擎后只 re.search('vod_play_url')，XML 全文无此词
    #    → 三条活源全判"失败"，真拉分片却有 13~30 万字节。type=0 恰是 D 包唯一能吃形态。
    #    本组用**离线样本**锁死判据，避免回归。
    hit_xml = False
    clean_xml = False
    base_xml = False
    try:
        # S143 修：原写 `import livecheck`——在脚本目录直跑能过，但经 `tvbox` 入口
        # （以模块方式加载 probe，sys.path 只有 tvbox-source-hub/scripts）时
        # ModuleNotFoundError → 探针被 except 吞掉、判"捕获=否"，自检 15/16 假红。
        # 用同目录绝对路径加载，两种调用方式同源。
        import importlib.util as _ilu
        _lcp = os.path.join(SELF_DIR, 'livecheck.py')
        _spec = _ilu.spec_from_file_location('_lc_probe', _lcp)
        _lcx = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_lcx)
        # 真阳性：XML 形态样本必须能取出 <dd> 里的播放地址（这正是旧版漏掉的）
        _xml_sample = (
            '<?xml version="1.0" encoding="utf-8"?><rss version="5.1">'
            '<list page="1" pagecount="1207" pagesize="20" recordcount="24140">'
            '<video><id>256776</id><name><![CDATA[样例]]></name>'
            '<dl><dd flag="ckplayer"><![CDATA[https://video25.avnyg.com/video/20220522/'
            '748274beb8198d1694489770ff3a07c9/index.m3u8]]></dd></dl></video>'
            '<video><id>256774</id><name><![CDATA[样例2]]></name>'
            '<dl><dd flag="ckplayer"><![CDATA[https://video52.avnyg.com/video/20230427/'
            '55bdacfac5a00bff49793a942b0fbc2e/index.m3u8]]></dd></dl></video></list></rss>')
        _u = _lcx._xml_play_urls(_xml_sample)
        hit_xml = (len(_u) == 2 and all(x.endswith('.m3u8') for x in _u))
        # 真假阳性：JSON 空 vod_play_url 的样本不得被 XML 通道误救（防"假活"被放过）
        _json_empty = '{"list":[{"vod_play_url":""}]}'
        _u2 = _lcx._xml_play_urls(_json_empty)
        # 清理后基线：非 XML 正文 / 空正文 → 必须返回空（不得凭空造地址）
        clean_xml = (_u2 == [] and _lcx._xml_play_urls('') == []
                     and _lcx._xml_play_urls('{"class":[]}') == [])
        # 假阳性基线：XML 里 <dd> 取值为空 / 非 URL → 不得产出地址
        _bad = ('<rss><list pagecount="1"><video><name>x</name>'
                '<dl><dd flag="ckplayer"><![CDATA[]]></dd></dl></video></list></rss>')
        base_xml = (_lcx._xml_play_urls(_bad) == []
                    and _lcx._xml_play_urls('<rss><list></list></rss>') == [])
    except Exception as e:
        print(f'  ⚠️ XML 通道探针异常：{type(e).__name__}: {e}')
    results.append(('XML源判活通道', hit_xml, clean_xml, base_xml))

    # ⑭ 候选评分分层（S143 离线样本，不打网络）
    #     真阳性：同一批人手里"高星但无关" vs "低星但高度相关"，相关者必须排在前面。
    #     清理后基线：两条完全同构的仓库必须同分（确定性，不靠随机）。
    #     假阳性基线：毫无关系且零星的仓库必须拿到低分（不能因为新鲜就冲进前排）。
    hit_rank = clean_rank = base_rank = False
    try:
        now_t = time.time()
        fresh = datetime.fromtimestamp(now_t - 86400, timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ')
        # ① 分层有效：4240★ 的 Clash 节点仓 vs 14★ 的 tvbox 影视仓 → 后者在前
        a = _score_repo(4240, fresh, _rel_score('Jsnzkpg/Jsnzkpg',
                                                '免费节点订阅 clash 机场'))[0]
        b = _score_repo(14, fresh, _rel_score('songlees355-wq/okay',
                                              'tvbox，影视仓，直播源'))[0]
        hit_rank = b > a
        # ② 确定性：同输入两次必得同分
        clean_rank = _score_repo(3, fresh, 1.0)[0] == _score_repo(3, fresh, 1.0)[0]
        # ③ 反假阳性：零相关 + 零星的仓库不得高于 50（不能靠"新"混进前排）
        base_rank = _score_repo(0, fresh, _rel_score('foo/bar', 'nothing here'))[0] <= 50
    except Exception as e:
        print(f'  ⚠️ 评分分层探针异常：{type(e).__name__}: {e}')
    results.append(('候选评分分层', hit_rank, clean_rank, base_rank))

    # ⑮ 登记册测试残留识别（S143 离线样本）
    #     真阳性：test1 / qist/tvbox 必须被认出；清理后基线：真目标必须留下；
    #     假阳性基线：名字里带 test 的**真仓库**（如 latest-news）不得被误杀。
    hit_purge = clean_purge = base_purge = False
    try:
        hit_purge = bool(_purge_reason(
            {'id': 'test1', '仓库URL': 'https://gitee.com/easy999/test1'})) and \
            bool(_purge_reason({'id': 'tvbox',
                                '仓库URL': 'https://github.com/qist/tvbox'}))
        clean_purge = _purge_reason(
            {'id': 'iptv2', '仓库URL': 'https://github.com/lxcjy/IPTV2',
             '存活状态': '无法判定'}) == ''
        # 假阳性基线：id=latest-news、URL 含 /latest → 不该被当测试残留
        base_purge = _purge_reason(
            {'id': 'latest-news', '仓库URL': 'https://github.com/x/latest-news',
             '存活状态': '存活', '上次检查': '2026-10-01T00:00:00Z'}) == ''
    except Exception as e:
        print(f'  ⚠️ 登记册清理探针异常：{type(e).__name__}: {e}')
    results.append(('登记册残留识别', hit_purge, clean_purge, base_purge))

    # ⑯ 上云回执判真（S143 事故专项·离线样本）
    #     事故：`'error' not in out[:40]` 把"整批被拒"当成功——字段不存在时接口返回
    #     {"error": "records[0] 校验后为空..."}，前 40 字符恰好截断在 error 之后，
    #     且从不看 results.success → 界面连续三天报 ✅，实际一条都没写进去。
    #     真阳性：error 回执必须判失败；清理后：正常 results 必须判成功；
    #     假阳性基线：success=false 的逐条失败也必须判失败（不能只看没有 error 键）。
    hit_rcpt = clean_rcpt = base_rcpt = False
    try:
        def _probe_count(out, expect):
            import json as _j
            try:
                d = _j.loads(out)
            except Exception:
                return 0, 'parse'
            if isinstance(d, dict) and d.get('error'):
                return 0, str(d['error'])
            rs = (d or {}).get('results') if isinstance(d, dict) else None
            if not isinstance(rs, list):
                return 0, 'no-results'
            good = sum(1 for r in rs if isinstance(r, dict) and r.get('success'))
            return good, ('' if good == expect else 'partial')

        hit_rcpt = _probe_count('{"error": "records[0] 校验后为空，无有效字段"}', 1)[0] == 0
        clean_rcpt = _probe_count('{"results":[{"index":0,"success":true}]}', 1)[0] == 1
        base_rcpt = _probe_count(
            '{"results":[{"index":0,"success":false,"error":"bad"}]}', 1)[0] == 0
    except Exception as e:
        print(f'  ⚠️ 上云回执探针异常：{type(e).__name__}: {e}')
    results.append(('上云回执判真', hit_rcpt, clean_rcpt, base_rcpt))
def _sc_blacklist(results):
    """self-check 组6：黑名单三层三铁律/证书不判死/失败成因分开/元信息坏样本（C6 拆出，顺序不变）。"""
    # ⑰ 黑名单三层与三铁律（S144·M-R96 守护探针）
    #     为什么必须活体证明：三层分级的判据全在 judge_level/load_blacklist 里，
    #     写错一个字（比如把 R3 的 `fails>=2` 写成 `>=1`）不会有任何报错，
    #     只会静默把单次抖动的活源永久拉黑——这正是 L026 复发的方式。
    #     真阳性：单次失败必须落 L2；两次失败但间隔 0 天也必须落 L2；
    #     清理后：两次失败间隔 3 天必须落 L1；
    #     假阳性基线：①已平反不进拦截集 ②活源免疫（R2）必须生效 ③L3 不得出现在 L1 集合。
    hit_bl = clean_bl = base_bl = False
    try:
        # 真阳性①：R3 单次失败永不判 L1
        lv1, _ = judge_level(1, '2026-10-17', '2026-10-15')
        hit_bl = (lv1 == 'L2观察')
        # 清理后②：失败 2 次且间隔 ≥1 天 → L1
        lv2, _ = judge_level(2, '2026-10-17', '2026-10-14')
        clean_bl = (lv2 == 'L1精确黑')
        # 假阳性基线③：间隔 0 天（同一批采样）也不许判 L1；平反记录不进拦截集
        lv3, _ = judge_level(2, '2026-10-17', '2026-10-17')
        base_bl = (lv3 == 'L2观察')
        # 指纹归一化口径（原 M-R97）：同源多写法必须归一
        fps = {source_fp(u) for u in (
            'http://fhapi9.com/provide/vod',
            'https://FHAPI9.com/provide/vod/',
            'http://www.fhapi9.com:8080/provide/vod?ac=list#x',
        )}
        base_bl = base_bl and len(fps) == 1
        if not base_bl:
            print(f'  ⚠️ 黑名单假阳性基线不净：间隔0天判成 {lv3}；归一化得 {fps}')
    except Exception as e:
        print(f'  ⚠️ 黑名单探针异常：{type(e).__name__}: {e}')
    results.append(('黑名单三层三铁律', hit_bl, clean_bl, base_bl))

    # ⑰b 证书类失败不判死 + 脏 URL 不进黑名单（S169 守护探针）
    #     为什么必须活体证明：这两条都是"静默出错"型——
    #       · 证书过期让 curl 在校验阶段就失败，源被判死，**退出码 0、无任何报错**；
    #         实测 ryzy.tv / lz.118318.xyz / taopianapi.com 三条因此被拉黑，
    #         而它们内容完全可用（前者 8.7 万条、中者 30 万条、分片 92KB）。
    #       · 脏 URL 归一化出 `https/raw.githubusercontent.com/…`（host 位混进协议名），
    #         这条指纹永远匹配不到真实 api，却占着 L1 名额并骗过体检。
    #     捕获：证书类关键词 + 失败 5 次 + 间隔充分 → 必须仍是 L2（不许升 L1）
    #     清理后：同样的次数/间隔，原因若是 DNS 无记录 → 必须正常升 L1（别把规则做成"永不判死"）
    #     假阳性基线：脏 URL 必须被标出；代理型 URL 必须**不**被误标（39 条正常代理不能被冤）
    hit_cert = clean_cert = base_cert = False
    try:
        lv_c, why_c = judge_level(5, '2026-10-19', '2026-09-01',
                                  reason='整域全灭', detail='证书过期 cert_expired')
        hit_cert = (lv_c == 'L2观察' and '证书' in why_c)
        lv_d, _ = judge_level(5, '2026-10-19', '2026-09-01',
                              reason='整域全灭', detail='DNS 无 A 记录')
        clean_cert = (lv_d == 'L1精确黑')
        _dirty = source_fp('https://https://raw.githubusercontent.com/a/b/main/c.m3u')
        _proxy = source_fp('https://gh-proxy.com/https://raw.githubusercontent.com/a/b/main/c.m3u')
        base_cert = (_dirty.startswith('!脏URL')
                     and _proxy == 'raw.githubusercontent.com/a/b/main/c.m3u'
                     and not _proxy.startswith('!'))
        if not base_cert:
            print(f'  ⚠️ 脏 URL 基线不净：dirty={_dirty!r} proxy={_proxy!r}')
    except Exception as e:
        print(f'  ⚠️ 证书/脏URL探针异常：{type(e).__name__}: {e}')
    results.append(('证书类不判死·脏URL不入黑', hit_cert, clean_cert, base_cert))

    # ⑰c 失败成因必须分开（S193 守护探针）
    #     为什么必须活体证明：这条的病是**静默退化**——
    #     分类器一直在跑、永远返回一个值，看不出它是不是把不同失败塌成同一个。
    #     实测本机 curl 对连接失败统一吐 `Couldn't connect to server`，
    #     关键词判据（找 refused/timeout）把这类全判成 other，
    #     于是"端口关了"和"超时"在报告里长得一模一样，看的人还是分不清
    #     该重试还是该判死——分类等于白做。
    #     ★ 用 **curl 退出码** 做主判据（libcurl 稳定错误码），stderr 只兜底。
    #     这里测纯函数 `_classify_err`，不依赖网络——否则断网时探针自己失真。
    hit_ek = clean_ek = base_ek = False
    try:
        # 与同文件其他函数同款：先补脚本目录再 import（见 2683/3016/3951 行）。
        # 不补会 ModuleNotFoundError —— 经 `tvbox` 入口分发时 sys.path 不带本目录，
        # 这也是 `tvbox probe decrypt` 报 No module named 'armor' 的同一个病。
        import os as _os
        _d = _os.path.dirname(_os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import livecheck as _lc
        # 真阳性：四类失败必须给出**四个不同**的类别
        ks = (_lc._classify_err('', 6),      # DNS 解析不了
              _lc._classify_err('', 7),      # 连不上（端口关/被拒/路由不通）
              _lc._classify_err('', 28),     # 超时
              _lc._classify_err('certificate has expired', 60))
        hit_ek = (ks == ('dns', 'refused', 'timeout', 'cert_expired'))
        # 清理后：无成因时 err_tail 必须返回空串（不得给成功结果硬贴标签）
        _saved = _lc._last_err_kind
        _lc._last_err_kind = ''
        clean_ek = (_lc.err_tail() == '')
        _lc._last_err_kind = _saved
        # 假阳性基线：认不出的失败只许落 other，不许冒充已知类别
        base_ek = (_lc._classify_err('', 0) == 'other'
                   and _lc._classify_err('something weird', 99) == 'other')
        if not base_ek:
            print('  ⚠️ 成因分类假阳性基线不净：未知失败被判成已知类别')
    except Exception as e:
        print(f'  ⚠️ 失败成因探针异常：{type(e).__name__}: {e}')
    results.append(('失败成因分开·DNS/连不上/超时/证书', hit_ek, clean_ek, base_ek))

    # ⑰d 元信息函数不得有未定义变量（S194 守护探针）
    #     为什么必须活体证明：2026-10-21 实测 `monitor add` 五个新目标**全部失败**，
    #     报 `NameError: name 'rec' is not defined` → 修完又报 `_cfg_try`。
    #     病根是 S190 改动时把别处的变量名抄进 `_platform_meta`：
    #     · 平时**没人走这条路**（存量目标不需要再探测默认分支）→ 洞一直开着没人看见
    #     · 一走就**必然抛异常**，且是 NameError 不是逻辑错，日志里看着像"偶发"
    #     这类洞的特征：**语法合法、编译通过、lint 不报**，只有真跑才炸。
    #     所以探针不能只测"函数能不能 import"，要测**函数体内有没有读未绑定的名字**。
    #
    #     ★ S171/S172 铁律：探针必须能抓到"改坏的目标"，否则就是恒过的假绿。
    #       故三格里有一格是**反向验证**——拿一个故意写坏的函数喂给检查器，
    #       它必须报出来；报不出来说明检查器本身是瞎的。
    hit_un = clean_un = base_un = False
    try:
        # 真阳性①：真身必须干净（`monitor add` 走的正是这条路）
        real = _undefined_names(_platform_meta)
        # 反向验证：故意写坏一份（复刻 S194 的两个坏变量名），检查器必须抓到
        def _probe_bad_sample(platform, owner):
            out = {'why': {}}
            if not rec.get('content_sha'):
                out['why']['x'] = _cfg_try[:2]
            return out
        bad = _undefined_names(_probe_bad_sample)
        hit_un = (real == [] and 'rec' in bad and '_cfg_try' in bad)
        # 清理后：干净函数检查为空（不含上一个样本残留）
        clean_un = (_undefined_names(_undefined_names) == [])
        # 假阳性基线：正常用模块全局的函数不许被误报
        base_un = (_undefined_names(_platform_base) == []
                   and _undefined_names(_content_sha) == [])
        if not hit_un:
            print('  ⚠️ 未定义变量探针失效：真身=%s 坏样本检出=%s' % (real, bad))
    except Exception as e:
        print(f'  ⚠️ 未定义变量探针异常：{type(e).__name__}: {e}')
    results.append(('元信息函数无未定义变量·坏样本能抓到', hit_un, clean_un, base_un))
def _sc_harvest_payload(results):
    """self-check 组7：原生态保留/addto段随形态/批量测活认api接口/URL归一/jsm拆包（C6 拆出，顺序不变）。"""
    # ⑱ 原生态保留（S144·P7/P8 守护探针）
    #     为什么必须活体证明：lightjson/repack/prune 三条命令都是「读原包→改结构→写新包」，
    #     只要中间用白名单重建，原包里没被列名的键就整体消失——**退出码 0、条数对得上、
    #     没有任何报错**。S144 真跑时 P7 就是这么抓到 `version`/`customKeyNotInWhitelist`
    #     被吃掉的。若探针只测"函数返回 False"，把 check_preserve 改成永远 return True
    #     也照样过——所以三格都要活体构造。
    #     捕获：白名单重建丢顶层键 + 站点字段被压扁，两样都必须报红
    #     清理后：无损裁剪必须判绿（不然每个正常包都会被拦下，等于校验器报废）
    #     假阳性基线：透传键被篡改必须报红；显式 allow_drop 必须判绿（有意识删不算丢）
    hit_pv = clean_pv = base_pv = False
    try:
        _pi = {'spider': 'https://x/s.jar', 'version': '1', 'myCfg': {'a': 1},
               'sites': [{'key': 'k1', 'name': 'A', 'api': 'https://a/vod', 'jar': 'j', 'ua': 'u'},
                         {'key': 'k2', 'name': 'B', 'api': 'https://b/m.m3u8'}],
               'lives': [{'name': 'L', 'url': 'https://l.m3u8'}]}
        # 捕获：顶层丢 version/myCfg + 站点丢 jar/ua → 必须红
        _bad_out = {'spider': 'https://x/s.jar', 'lives': _pi['lives'],
                    'sites': [{'key': 'k1', 'name': 'A', 'api': 'https://a/vod'}]}
        _o1, _r1, _ = check_preserve(_pi, _bad_out, quiet=True)
        hit_pv = (not _o1) and len(_r1) >= 2
        # 清理后：无损裁剪（键全留、只少站）→ 必须绿
        _good_out = {**_pi, 'sites': [_pi['sites'][0]]}
        _o2, _r2, _ = check_preserve(_pi, _good_out, quiet=True)
        clean_pv = _o2 and not _r2
        # 假阳性基线①：透传键被改 → 必须红
        _tamper = {**_good_out, 'spider': 'https://evil/e.jar'}
        _o3, _r3, _ = check_preserve(_pi, _tamper, quiet=True)
        # 假阳性基线②：显式 allow_drop → 必须绿
        _decl = {k: v for k, v in _good_out.items() if k not in ('version', 'myCfg')}
        _o4, _r4, _ = check_preserve(_pi, _decl, allow_drop=['version', 'myCfg'], quiet=True)
        base_pv = (not _o3) and _o4
        if not (hit_pv and clean_pv and base_pv):
            print(f'  ⚠️ 原生态探针不达标：捕获={_o1}(应False,红{len(_r1)}) '
                  f'清理={_o2} 篡改={_o3} 声明删={_o4}')
    except Exception as e:
        print(f'  ⚠️ 原生态探针异常：{type(e).__name__}: {e}')
    results.append(('原生态保留·字段不凭空没', hit_pv, clean_pv, base_pv))

    # ⑲ addto 段落点与形态一致（S144·P2 守护探针）
    #     为什么必须活体证明：旧版 `tgt_key = 'sites' if ... else 'parses'` 在未指定类型时
    #     **默认落 parses**，一条采集站被写进解析器段——写入成功、回读也在、没有任何报错，
    #     只有装进盒子才发现点不开。探针必须真的"算一遍段落点"，不能只查源码字符串。
    hit_ad = clean_ad = base_ad = False
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import livecheck as _lc

        def _seg_of(api, typ=None):
            """复刻 addto ⑤ 的段落点推导（与实现同源，改实现不改这里就会红）。"""
            eff = typ if typ in ('sites', 'lives', 'parses') else 'sites'
            if re.search(r'\.m3u8?($|\?)|/live', api, re.I):
                eff = 'lives'
            return eff
        # 捕获：采集站未指定类型 → 必须落 sites（旧版落 parses）
        hit_ad = (_seg_of('https://a.example.com/api.php/provide/vod') == 'sites'
                  and _seg_of('https://a.example.com/api.php/provide/vod', 'parses') == 'parses')
        # 清理后：m3u8 必须落 lives
        clean_ad = _seg_of('https://a.example.com/live.m3u8') == 'lives'
        # 假阳性基线：普通直链不得被误判成 lives；显式 parses 不得被形态改写
        base_ad = (_seg_of('https://a.example.com/list.json') == 'sites'
                   and _seg_of('https://a.example.com/jx?url=', 'parses') == 'parses')
        # 形态判定本身也要对（classify_source 是 addto ② 的判据）
        base_ad = base_ad and _lc.classify_source('https://a/live.m3u8') == '直链'
        if not (hit_ad and clean_ad and base_ad):
            print(f'  ⚠️ addto 段探针不达标：采集={hit_ad} m3u8={clean_ad} 基线={base_ad}')
    except Exception as e:
        print(f'  ⚠️ addto 段探针异常：{type(e).__name__}: {e}')
    results.append(('addto段随形态·不落解析器', hit_ad, clean_ad, base_ad))

    # ⑳ 批量测活字段名兼容（S144·联网验证时抓到的假红守护探针）
    #     为什么必须活体证明：`cmd_batch` 旧版只读 `api`/`url`，而探源司全线（候选/剔失清单/
    #     repack 产物）用的是 **`api接口`** → 取到空串 → 拿空 URL 去测活 → 一律「无法判定（均超时/不可达）」。
    #     这条最毒的地方：**表现与"源真失效"完全一样**，会被当成网络问题甚至据此误删好源。
    #     S144 实测：6 条真实源修前 0 通过 / 修后 6 通过。
    hit_bf = clean_bf = base_bf = False
    try:
        import livecheck as _lc2
        _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'livecheck.py'),
                    encoding='utf-8').read()
        # 捕获：源码里必须认 `api接口`（探源司标准字段名）
        hit_bf = "'api接口'" in _src and 'api接口' in _src
        # 清理后：空地址必须显式报错，不许"照样测活"伪装成网络失败
        clean_bf = '条目无地址字段' in _src
        # 假阳性基线：`api`/`url` 仍须兼容（别的包/别的技能在用这两个名字）
        base_bf = ("'api'" in _src) and ("'url'" in _src)
        if not (hit_bf and clean_bf and base_bf):
            print(f'  ⚠️ 批量字段名探针不达标：api接口={hit_bf} 空址报错={clean_bf} 兼容={base_bf}')
    except Exception as e:
        print(f'  ⚠️ 批量字段名探针异常：{type(e).__name__}: {e}')
    results.append(('批量测活认api接口·空址不装网络错', hit_bf, clean_bf, base_bf))

    # ㉑ URL 解码 / 代理前缀剥离归一（S146·M-R96 ⑦⑧ 守护探针）
    #     为什么必须活体证明：⑥ 步口径把「同一文件的编码写法」和「裸 URL 外套一层代理」
    #     判成两条 → 同一条线路在库里存成两条（S146 实测 iptv365 一对、gh-proxy 一批）。
    #     ⑦ `unquote(path)`：%E5%A4%A9… 与 天天 必须归一。
    #     ⑧ 剥代理前缀：path 里出现第二个 http(s):// 时取内层递归归一。
    #     最毒的反例：第一版写成 `r'/https?://'` + `inner.lstrip('/')`，
    #     `path='/https://raw…'` 剥出 `https:/raw…`（**少一个斜杠**）→ 归一对不上且**不报错**。
    hit_fp = clean_fp = base_fp = False
    try:
        # 捕获真阳性：编码写法 ≡ 原文写法
        _a = 'http://play.iptv365.org/%E5%A4%A9%E5%A4%A9%E5%BC%80%E5%BF%83/api/drpy2.min.js'
        _b = 'http://play.iptv365.org/天天开心/api/drpy2.min.js'
        hit_fp = source_fp(_a) == source_fp(_b)
        # 反向：代理前缀剥掉后 ≡ 裸 URL
        _c = 'https://gh-proxy.com/https://raw.githubusercontent.com/IY-CPU/IY/main/lib/drpy2.min.js'
        _d = 'https://raw.githubusercontent.com/IY-CPU/IY/main/lib/drpy2.min.js'
        hit_fp = hit_fp and source_fp(_c) == source_fp(_d)
        # 清理后必须"不丢斜杠"——反例回归：`https:/raw…` 这种残缺 host 不许出现
        clean_fp = (source_fp(_c).startswith('raw.githubusercontent.com/')
                    and 'https:/' not in source_fp(_c) and 'https//' not in source_fp(_c))
        # 假阳性基线：**不同线路不许被归一成一条**（同 host 不同仓库的两个 js 必须能区分），
        # 同时 scheme / 尾斜杠差异**必须**归一（否则把同一条拆成两条，同样错）
        base_fp = (
            source_fp('http://raw.githubusercontent.com/IY-CPU/IY/main/lib/drpy2.min.js')
            != source_fp('http://raw.githubusercontent.com/takagen99/TVBox/main/lib/other.min.js')
            and source_fp('http://x.com/a/vod') == source_fp('https://x.com/a/vod/')
        )
        if not (hit_fp and clean_fp and base_fp):
            print(f'  ⚠️ 归一化探针不达标：解码/剥代理={hit_fp} 不丢斜杠={clean_fp} 区分基线={base_fp}')
            print(f'     fp("%E5%A4%A9…") = {source_fp(_a)}')
            print(f'     fp("天天…")      = {source_fp(_b)}')
            print(f'     fp(gh-proxy)    = {source_fp(_c)}')
    except Exception as e:
        print(f'  ⚠️ 归一化探针异常：{type(e).__name__}: {e}')
    results.append(('URL解码/代理剥离归一·不丢斜杠', hit_fp, clean_fp, base_fp))

    # ㉒ jsm 拆包分诊（S147·harvest splitjsm 守护探针）
    #     为什么必须活体证明：这条命令的核心不是"能拆出条数"，而是"**不把分母当答案**"——
    #     老的 split_jsm.py 把 164 条全当候选交出去，其中 150 条是 csp/py/js/本地相对路径，
    #     下游 `harvest promote` 拿这批去入库只会全数失败。所以探针必须证明：
    #       ① 拆包数对（sites+lives 去重后 = 3 条样张，不多不少）
    #       ② 分诊数对（可采集 1 / 非外链 2，**两边加起来等于总数**，不是各自拍脑袋）
    #       ③ 分组来自**正本** `split_jsm.group_of`，不是这里另抄一份表——
    #          探针用一个正本表里没有的关键词（'zz无关键词zz'）走兜底分支验证；
    #          若有人在本文件里复制了 RULES 且漏改，这条会漂。
    #       ④ 反例：非外链必须被判"不可采集"且**带原因**，不许原因是空串蒙混。
    hit_sj = clean_sj = base_sj = False
    try:
        _pkg = {'sites': [
            {'name': '样张A', 'api': 'http://example.com/api.php/provide/vod/'},
            {'name': '样张B', 'api': 'csp_Config'},                          # csp → 非外链
            {'name': '样张C', 'api': './lib/drpy.min.js'},                    # local → 非外链
            {'name': '样张A', 'api': 'http://example.com/api.php/provide/vod/'},  # 重复，须去重
        ], 'lives': []}
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import split_jsm as _sj
        _c = _sj.split_pkg(_pkg)
        _ok = [x for x in _c if _lj_api_kind(x['api接口']) == 'http']
        _ng = [x for x in _c if _lj_api_kind(x['api接口']) != 'http']
        # ① 去重后 3 条（样张A 重复一次已并）
        hit_sj = (len(_c) == 3 and len(_ok) == 1 and len(_ng) == 2
                  and len(_ok) + len(_ng) == len(_c))
        # ② 分组必须走正本：正本表里查不到的词 → 落到形态兜底，不该返回空
        _g1, _e1 = _jsm_group_of('吧无关键词吧', 'http://a.com/x')
        _g2, _e2 = _jsm_group_of('吧无关键词吧', 'csp_X')
        clean_sj = (not _e1 and not _e2 and _g1 == '外链采集' and _g2 == '集成解析(csp)')
        # ③ 假阳性基线：正本的名称关键词仍生效（'影视' 必须归 影视，不能一律兜底），
        #    且形态分诊**没把非外链错认成外链**（csp/local 必须落非 http）——
        #    这条盯的是 `_lj_api_kind` 本身，不依赖 cmd 层后填的"原因"字段。
        _g3, _ = _jsm_group_of('某某影视', 'csp_X')
        _kinds = {x['api接口']: _lj_api_kind(x['api接口']) for x in _ng}
        base_sj = (_g3 == '影视'
                   and _kinds.get('csp_Config') == 'csp'
                   and _kinds.get('./lib/drpy.min.js') == 'js')
        if not (hit_sj and clean_sj and base_sj):
            print(f'  ⚠️ jsm 拆包探针不达标：拆分={hit_sj} 正本分组={clean_sj} 基线={base_sj}')
            print(f'     拆分出 {len(_c)} 条（http {len(_ok)} / 非 http {len(_ng)}，应 3/1/2）')
            print(f'     group_of("吧无关键词吧",http) = {_g1!r} err={_e1!r}')
            print(f'     group_of("某某影视",csp)       = {_g3!r}（应 影视）')
            print(f'     形态分诊 = {_kinds}（csp_Config 应 csp，./lib/drpy.min.js 应 js）')
    except Exception as e:
        print(f'  ⚠️ jsm 拆包探针异常：{type(e).__name__}: {e}')
    results.append(('jsm拆包分诊·非外链不冒充候选', hit_sj, clean_sj, base_sj))
def _sc_pan_pak(results):
    """self-check 组8：网盘取料/412归因/MSITEPK2 key自取（C6 拆出，顺序不变）。"""
    # ㉓ 网盘分享取料：分享文案能认出 id + 412 不被当"文件坏"（S170）
    #     为什么必须活体证明：这条链路的坑全在**识别**上，不在下载上——
    #       ① 用户贴的是**整段分享文案**（含口令 `/~xxx~/`），不是干净 URL。
    #          口令长得很像 id，认错了会拿口令去查分享，报"分享不存在"，
    #          而真正的原因是解析错了。所以必须用真实文案喂进去证明抠对了。
    #       ② 412 的成因（机房 IP 段被 CDN 边缘拒，**不是** IPv6 也不是文件坏）必须与"文件损坏"分开报，
    #          探针用**字符串级证据**锁死：报错文案里必须出现"非文件问题"。
    hit_pan = clean_pan = base_pan = False
    try:
        _txt = ('我用夸克网盘给你分享了「夸克上传文件」，点击链接或复制整段内容，'
                '打开「夸克APP」即可获取。祄七另枫诶并闭页冉为喔汢 /~d9803bYsb2~:/\n'
                '链接：https://pan.quark.cn/s/9d5b78140523 查看链接中文件')
        hit_pan = (pan_parse_share(_txt) == '9d5b78140523')          # 从文案里抠对
        clean_pan = (pan_parse_share('https://pan.quark.cn/s/abc123') == 'abc123'
                     and pan_parse_share('随便一段没有链接的话') == '')   # 干净URL/无链接都不误判
        # 反例：口令片段不能被当成 id
        base_pan = (pan_parse_share('/~d9803bYsb2~/') == '')
    except Exception as e:
        print(f'  ⚠️ 网盘取料探针异常：{type(e).__name__}: {e}')
    results.append(('网盘取料·整段文案认出id且口令不误认', hit_pan, clean_pan, base_pan))

    # ㉓b 归因不能错：412 的处置建议里**不许**出现"换 IPv6"（S170 更正的固化）
    #     为什么值得单列一条：S170 初版把 412 判成"域名 IPv6-only"，处置写"换 IPv6 机器"，
    #     实测复查后证明**换过去还是 412**（A 记录 8 个，逐 IP 全 412；坏 token 也 412）。
    #     判据错法不会让程序报错、不会让 self-check 变红，只会**把人指向不会成功的动作**。
    #     所以这里用源码级检查：pull 的处置文案若又出现"换 IPv6"这类错误指引，直接判失败。
    hit_why = clean_why = base_why = False
    try:
        import ast as _ast
        _src = open(__file__, encoding='utf-8').read()
        _tree = _ast.parse(_src)
        _fn = next((n for n in _tree.body
                    if isinstance(n, _ast.FunctionDef) and n.name == 'cmd_pan_pull'), None)
        # ★只取**字符串字面量**，把注释排除掉——注释里提到旧错法是在说明它错，不是错法本身。
        #   （探针初版直接 grep 源码，被自己的"更正注释"判失败，属于同侧变量互比。）
        _lits = [n.value for n in _ast.walk(_fn)
                 if isinstance(n, _ast.Constant) and isinstance(n.value, str)] if _fn else []
        _text = '\n'.join(_lits)
        hit_why = ('机房' in _text and '412' in _text)      # 正确归因写明
        _bad = [k for k in ('在有 IPv6 的机器上', '换 IPv6 机器') if k in _text]
        clean_why = not _bad                                # 错误处置已清除
        base_why = ('坏' in _text)                          # 保留"坏 token 也 412"的证据
    except Exception as e:
        print(f'  ⚠️ 网盘归因探针异常：{type(e).__name__}: {e}')
    results.append(('网盘412归因·机房IP段而非IPv6', hit_why, clean_why, base_why))

    # ㉔ MSITEPK2 容器：**key 能自动取到** + **错 key/错文件不许折叠成成功**（S171）
    #     为什么必须测：`pak` 是本技能新增的第一个"离线解容器"能力。它最大的假绿风险是
    #     ——**解出来一堆 0 字节文件却报成功**。S171 实测就撞过：漏 import zlib 时，
    #     78 条全 NameError，而那时输出正要说"全部完成"。故探针锁三件事：
    #       ① 头部解析与声明条数一致（名字锚定，不靠猜偏移）
    #       ② 坏输入（非 magic / 错 key）**必须非 0 退出**
    #       ③ 源码里保留"声明/实解差额"的自证输出，不许只报成功数
    hit_pak = clean_pak = base_pak = _mc_ok = False
    try:
        import tempfile as _tf
        import struct as _st2
        d = _tf.mkdtemp()
        # 造一个最小合法 pak：2 条，内容 'A' / 'BB'
        k = bytes(range(32))
        iv1, iv2 = b'1' * 16, b'2' * 16
        payloads = []
        for txt, iv in ((b'A', iv1), (b'BB', iv2)):
            from Crypto.Cipher import AES as _A2
            comp = zlib.compress(txt)
            pad = 16 - len(comp) % 16
            comp += bytes([pad]) * pad
            payloads.append(_A2.new(k, _A2.MODE_CBC, iv).encrypt(comp))
        names = [('a.txt', payloads[0], iv1), ('b.txt', payloads[1], iv2)]
        head = PAK_MAGIC + _st2.pack('>I', 2)
        off = 0
        for nm, pl, iv in names:
            nb = nm.encode()
            head += _st2.pack('>H', len(nb)) + nb + _st2.pack('>II', off, len(pl)) + iv
            off += len(pl)
        pakbuf = head + b''.join(p[1] for p in names)
        fp = os.path.join(d, 't.pak')
        open(fp, 'wb').write(pakbuf)
        ents, ps = _pak_parse(pakbuf)
        hit_pak = (len(ents) == 2 and ps == len(head)
                   and _pak_read(pakbuf, ps, ents[0], k) == b'A')
        # 坏输入必须报错（不是返回空）
        bad1 = bad2 = False
        try:
            _pak_parse(b'NOTAPAK!' + b'\x00' * 16)
        except ValueError:
            bad1 = True
        try:
            _pak_read(pakbuf, ps, ents[0], b'\x00' * 32)
            bad2 = False
        except Exception:
            bad2 = True
        clean_pak = bad1 and bad2
        # 源码自证：差额必须报出来
        # ★这条探针栽过三次，是同一病的三次复发，记清楚：
        #   病的名字叫**同侧变量互比**——探针把「要检查的代码」和「检查用的判据」写在同一个文件里，
        #   于是判据字面量自己就满足了条件，无论怎么改都通过。
        #   ① 查整串 "声明 …/ 实解 …"→ 探针注释里也写着它；
        #   ② 查「同行含 差额 + len(entries)-ok + print」→ 判断行自身三者俱全；
        #   ③ 查 "别当成…" → 以为注释里没有，但**注入脚本第一处 find 命中的正是探针自己**。
        #   ★正解：像 ㉓b 那样用 AST，**只取 cmd_comb_pak 函数体内的字符串字面量**，
        #     把注释和探针自身的源码结构排除在外。判据再怎么改，也只在目标函数里生效。
        import ast as _ast3
        _t3 = _ast3.parse(open(__file__, encoding='utf-8').read())
        _fn3 = next((n for n in _t3.body
                     if isinstance(n, _ast3.FunctionDef) and n.name == 'cmd_comb_pak'), None)
        _lits3 = [n.value for n in _ast3.walk(_fn3)
                  if isinstance(n, _ast3.Constant) and isinstance(n.value, str)] if _fn3 else []
        _txt3 = '\n'.join(_lits3)
        # 「差额」二字必须出现在函数的**输出文案**里，且同处须有实际差额计算
        base_pak = ('差额' in _txt3)
        # ★扩展（S169 形态）：多候选 dex 时，自动取密钥必须靠"自证"选中正确 key，
        #   而非静默退化成只列清单。造含真 key + 2 个干扰 64-hex 的伪 dex 验证。
        try:
            _dex_fake = os.path.join(d, 'classes.dex')
            _real_hex = k.hex()
            _d1 = b'0123456789abcdef' * 4   # 64 hex 干扰串
            _d2 = b'fedcba9876543210' * 4   # 64 hex 干扰串
            open(_dex_fake, 'wb').write(
                b'xx' + _real_hex.encode() + b'yy'
                + b'zz' + _d1 + b'zz' + _d2 + b'zz')
            _mc = _pak_key_from_dex(_dex_fake)
            _picked = None
            for _c in _mc:
                if _pak_try_key(pakbuf, ps, ents, _c):
                    _picked = _c
                    break
            _mc_ok = (_picked == _real_hex)
        except Exception:
            _mc_ok = False
        shutil.rmtree(d, ignore_errors=True)
    except Exception as e:
        print(f'  ⚠️ MSITEPK2 探针异常：{type(e).__name__}: {e}')
    # ★多候选 dex 自证选 key 已并入「捕获」判据（S169 静默缺陷回归锁）：
    #   单候选能取 + 多候选能自证选中正确 key，二者任一失败即判不捕获。
    hit_pak = hit_pak and _mc_ok
    results.append(('MSITEPK2·key自取(含多候选自证)+坏输入不折叠', hit_pak, clean_pak, base_pak))
def _sc_eco_gate(results):
    """self-check 组9：多生态分诊闸/取数层镜像回退/破甲模块（C6 拆出，顺序不变）。"""
    # ㉕ 多生态分诊闸（点⑥·M-R212·M-R60 三态——越界留痕不静默丢弃）
    #     为什么必须活体证明：分诊闸是新增的"入库前过滤器"，最大假绿风险是
    #     越界项（漫画/书源/音源）被静默放行进 DB_HARVEST，或反过来误杀 tvbox 接口。
    #     探针用构造数据证伪（不打真网络），判据取行为结果而非源码字面量（避同侧变量互比）：
    #       ① tvbox 接口（含 m3u8）→ 进册 True
    #       ② 漫画书源（bookSourceGroup=漫画 且含 bookSourceUrl 键）→ 挡 False
    #       ③ 阅读书源（含 bookSourceUrl 键，无漫画分组）→ 挡 False
    #       ④ 音源（music 特征）→ 挡 False
    #       ⑤ 名字带"漫画"的 tvbox m3u8 → 仍进册（结构优先于关键词，不误杀）
    hit_gate = clean_gate = base_gate = False
    try:
        _g_tv = {'api接口': 'https://x.com/live.m3u8', '分组类目': '直播'}
        _g_comic = {'bookSourceGroup': '漫画', 'bookSourceUrl': 'https://c.com/a.json', 'name': '某某漫画'}
        _g_book = {'bookSourceUrl': 'https://b.com/a.json', 'name': '某某阅读'}
        _g_music = {'api接口': 'https://m.com/api', 'name': 'music网易云', 'url': 'https://m.com'}
        _g_tv_wc = {'api接口': 'https://x.com/live.m3u8', 'name': '漫画频道直播'}
        _r_tv = can_enter_harvest(dict(_g_tv))
        _r_comic = can_enter_harvest(dict(_g_comic))
        _r_book = can_enter_harvest(dict(_g_book))
        _r_music = can_enter_harvest(dict(_g_music))
        _r_tv_wc = can_enter_harvest(dict(_g_tv_wc))
        hit_gate = (_r_tv[0] is True
                    and _r_comic[0] is False and '漫画' in _r_comic[1]
                    and _r_book[0] is False and '阅读' in _r_book[1]
                    and _r_music[0] is False and '音源' in _r_music[1])
        clean_gate = (_r_tv_wc[0] is True)  # 结构优先，名字含漫画不误杀
        base_gate = (callable(can_enter_harvest)
                     and isinstance(_OUT_OF_SCOPE_ECO, tuple)
                     and len(_OUT_OF_SCOPE_ECO) == 3)
    except Exception as e:
        print(f'  ⚠️ 分诊闸探针异常：{type(e).__name__}: {e}')
    results.append(('多生态分诊闸·tvbox进/越界挡·不误杀', hit_gate, clean_gate, base_gate))

    # ㉓ 取数层镜像回退（S148·`_fetch` 守护探针）
    #     为什么必须活体证明：`_fetch` 是**所有采集/监测/目录树**的公共底，改它等于改全楼水管。
    #     S148 病根：raw.githubusercontent 直连实测 29/34/38/40s，而原 `-m 15` 必然超时 →
    #     存在的文件被判"拉取失败" → 同仓库一次采 129 条、一次采 55 条（**不可复现**）。
    #     修法是"慢则换镜像"，但这类"自适应"逻辑最容易假绿：不测就永远显示正常。
    #     故探针必须在**离线可复现**的前提下证明四件事（不打真网络，用桩替身）：
    #       ① 慢→标脏：直连成功但耗时>8s，必须把 `_RAW_SLOW['hit']` 置 True
    #       ② 脏→优先镜像：标记后下一次调用，**第一个尝试的 URL 必须是镜像**（不是直连）
    #       ③ 快→不标脏：直连 1s 就回，不许误标（否则每次都绕镜像，白丢一次直连）
    #       ④ 非 raw 域名不受影响：普通 URL 的尝试序列必须**只有它自己**，不许被塞镜像
    hit_fb = clean_fb = base_fb = False
    # 注意：本段要给 `_sh` / `time.time` 打桩，而**赋值即视为局部变量**会使读取失败，
    # 故一律通过 globals() 存取（避免遮蔽），绝不用裸名赋值。
    _g = globals()
    _real_httpget, _real_clock = _g['http_get'], _g['time'].time
    # ★ S167 修：必须**同时**固定 `_RAW_REACH`，否则本探针的结论由真实网络决定。
    #   实测（本网络）raw 直连 6 次里 3 次 3s 超时 → `_raw_direct_ok()` 返回 False 并被一次性缓存
    #   → `_fetch` 把镜像排到最前 → 直连不执行 → "直连成功但 >8s" 永不成立 → 慢标记恒 False
    #   → 本探针失败。同一命令连跑 20 次，10 次绿 10 次红，全程无人知道为什么。
    #   探针验的是"慢则标脏"这条**逻辑**，不是本机到 GitHub 的通路，
    #   所以两段都要把前提钉死：① 段=直连可达（否则走到不执行直连的分支）；
    #   ④ 段起=故意设为不可达，正好顺带证明"不通时不误标脏"。
    _real_reach = dict(_RAW_REACH)
    try:
        _calls = []

        def _mk():
            """伪造一次 `http_get`：记录被请求的 URL，并返回合法的 200 正文（status,payload,reason）。"""
            def _fake(_u, timeout=None, ua=None, return_status=False):
                _calls.append([_u])
                return ('ok', (200, '{"ok":1}'), None)
            return _fake

        def _clock(step):
            """返回一个假时钟：每次调用前进 step 秒（用来控制"本次调用耗时"）。"""
            _t = [1000.0]

            def _f():
                _t[0] += step
                return _t[0]
            return _f

        _url_raw = 'https://raw.githubusercontent.com/o/r/main/a.json'

        # ① 慢→标脏（直连 30s 才回，必须记下"慢"）
        #    前提：直连可达。否则 _fetch 会把镜像排前面、直连不跑，这条路径根本不可达。
        _RAW_REACH.clear()
        _RAW_REACH.update({'probed': True, 'ok': True})
        # ★ S167：**先验证前提真的生效**，再验结论。
        #   不验证的后果实测过：我一度在外面把 `_raw_direct_ok` 换成恒 False 的替身，
        #   探针照样按"可达"去断言，于是红——分不清是"探针逻辑错"还是"我的测法污染了被测对象"。
        #   探针必须自己证明「我现在确实处在直连可达态」，否则后面的断言没有意义。
        if not _raw_direct_ok():
            raise RuntimeError('探针前提未生效：已置 _RAW_REACH.ok=True，但 _raw_direct_ok() 仍返回假——'
                               '说明它没读缓存（或被替身替换），本段断言不可信')
        _RAW_SLOW['hit'] = False
        _calls.clear()
        _g['http_get'], _g['time'].time = _mk(), _clock(30)
        st1, _ = _fetch(_url_raw)
        got_slow = (_RAW_SLOW['hit'] is True)

        # ② 脏→优先镜像（同一进程内下一次必须第一个就试镜像）
        _calls.clear()
        _g['http_get'], _g['time'].time = _mk(), _clock(0.2)
        st2, _ = _fetch(_url_raw)
        _seq = _calls[0] if _calls else []
        got_mirror_first = bool(_seq) and ('gh-proxy.com' in _seq[0])

        # ③ 快→不标脏（干净状态下直连秒回，不许误标）
        _RAW_SLOW['hit'] = False
        _calls.clear()
        _g['http_get'], _g['time'].time = _mk(), _clock(0.2)
        st3, _ = _fetch(_url_raw)
        got_fast_clean = (_RAW_SLOW['hit'] is False)

        hit_fb = (st1 == 'ok' and got_slow and st2 == 'ok' and got_mirror_first)

        # ④ 非 raw 域名：即使脏标记开着，也不许被塞镜像（证明回退是**定向**的）
        _RAW_SLOW['hit'] = True
        _calls.clear()
        _g['http_get'], _g['time'].time = _mk(), _clock(0.2)
        st4, _ = _fetch('https://api.example.com/data.json')
        _seq4 = _calls[0] if _calls else []
        clean_fb = (st4 == 'ok' and len(_seq4) == 1 and 'gh-proxy' not in _seq4[0])
        base_fb = got_fast_clean
        if not (hit_fb and clean_fb and base_fb):
            print(f'  ⚠️ 取数层镜像回退探针不达标：慢标记={got_slow} 脏优先镜像={got_mirror_first} '
                  f'快不标脏={got_fast_clean} 非raw纯净={clean_fb}')
            print(f'     脏标记下首次尝试序列 = {_seq[:3]}（首个应含 gh-proxy.com）')
            print(f'     非 raw 域名尝试序列 = {_seq4}（应只有原始 URL 一条）')
    except RuntimeError as e:
        # ★ S167：**前提不成立 ≠ 逻辑错**，必须分开报（M-R60 三态，不许折叠）。
        #   如果探针自己没能把环境摆成想要的样子，它就没有资格对被测逻辑下任何结论；
        #   这时把它记成「捕获=否」是冤枉代码，记成「捕获=是」是自欺。
        #   两者都不对，正确做法是**说清是哪一种**并把三项判定全部置否（无法判定）。
        print(f'  ⚠️ 取数层探针**无法判定**（前提未成立）：{e}')
    except Exception as e:
        print(f'  ⚠️ 取数层探针异常：{type(e).__name__}: {e}')
    finally:
        _g['http_get'], _g['time'].time = _real_httpget, _real_clock
        _RAW_SLOW['hit'] = False        # 清理：探针不许留下脏状态影响真实采集
        # 清理：把 _RAW_REACH 还原成探针进来前的样子，不许把探针设的"可达"留给真实采集
        _RAW_REACH.clear()
        _RAW_REACH.update(_real_reach)
    results.append(('取数层镜像回退·慢则换路且只换raw', hit_fb, clean_fb, base_fb))

    # ㉔ 破甲模块（S148·`armor.py` 守护探针）
    #     为什么必须活体证明：这模块是从外部材料吸收进来的，自带 self_test **本身就是假绿的**
    #     （`except ImportError: pass` + 无条件 print ✅——没测过也报绿）。移植时实测出 5 处真缺陷：
    #     ① PKCS7 有"宽容兜底"把非法填充吃掉 ② self_test 假绿 ③ HTML 被当纯文本
    #     ④ Hex 被当 base64 ⑤ 变换链漏识 JS 侧 atob/JSON.parse。
    #     故探针不看它自己的 self_test，直接**用构造数据证伪**：
    #       ① 严格性：非法填充必须 None（原版会返回半成品）
    #       ② 语义判据：解得开的才算图，周期性数据不许凑成图片（原版靠猜 5 个固定 key）
    #       ③ 形态分诊：html-page / xml-doc / hex / base64-json 各归各位，不许串
    #       ④ 变换链：`atob+JSON.parse` 必须识别（原版返回 []）
    hit_ar = clean_ar = base_ar = False
    try:
        import armor as _am
        import io as _io

        # ① AES/PKCS7 严格性（NIST 向量 + 非法填充拒绝）
        _k = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
        _iv = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
        _ct = bytes.fromhex('7649abac8119b246cee98e9b12e9197d')
        _pt = _am.aes_cbc_decrypt(_k, _iv, _ct)
        _strict = (
            _pt is not None and _pt.hex() == '6bc1bee22e409f96e93d7e117393172a'
            and _am.pkcs7_unpad(b'AAAAAAAAAAA\x05') is None        # 非法填充必须拒绝
            and _am.aes_cbc_decrypt(_k, _iv, b'') is None             # 空输入必须拒绝
            and _am.aes_cbc_decrypt_and_unpad(_k, _iv, _ct) is None   # 填充非法→失败，不给半成品
        )
        # ★ 关键补强（S148）：上面的 `_ct` 解出来末字节是 0x2A（不在 1..16），
        #   所以它**触发不到"宽容兜底"那条分支**——只靠它断言是用弱数据测强逻辑，
        #   故障注入时曾因此漏报。补一个**专门构造**的密文：解密后末字节恰为 0x05
        #   但整段填充非法（`A*15 + \x05`），正确实现必须拒绝，宽容实现会返回 `A*11`。
        _bad_ct = bytes.fromhex('b3025aa12017459a499152d4a329d094')
        _bk = b'NHboMHZerxFQ401E'
        _biv = b'i7JeCEIMVrj2W9xN'
        _strict = _strict and (_am.aes_cbc_decrypt_and_unpad(_bk, _biv, _bad_ct) is None)
        hit_ar = _strict

        # ② XOR 图片：真图能还原，周期性数据不许凑
        _plain_ok = _fake_ok = False
        try:
            from PIL import Image as _IM
            _buf = _io.BytesIO()
            _IM.new('RGB', (16, 12), (7, 9, 11)).save(_buf, 'PNG')
            _png = _buf.getvalue()
            _m, _out, _was = _am.xor_decrypt_image(bytes(b ^ 0x5A for b in _png))
            _plain_ok = (_was is True and _out == _png and _m == 'image/png')
        except ImportError:
            _plain_ok = True        # 无 PIL 时该条不判（但不影响其它断言）
        _rnd = _am.xor_decrypt_image(bytes(range(256)) * 4)
        _txt = _am.xor_decrypt_image(b'hello world' * 20)
        _fake_ok = (_rnd[2] is False and _txt[2] is False)
        clean_ar = _plain_ok and _fake_ok

        def _cffi_ok():
            """实测 curl_cffi **类初始状态**下首次调用的行为（不许短路）。

            ★ 关键：必须用类定义时的**真实初值**，不许手工把它置成 'unknown'——
            手工置成 unknown 会让"坏实现"（初值 False/missing）也蒙混过关，
            探针就退化成恒真。这里直接读类属性在导入时的初值。
            """
            M = _am.CffiSessionManager
            _bak_s, _bak_st = M._session, M._state
            try:
                # 不猜初值：从类 dict 里取"第一次被定义成什么"。
                init_state = type.__getattribute__(M, '__dict__').get('_state', None)
                if init_state is None:
                    return False        # 老实现没有 _state（只有 _available）→ 直接判不合格
                M._session, M._state = None, init_state
                got = M.get_session()
                st = M._state
                if st == 'ok':
                    return got is not None
                # 依赖缺失时，状态必须**从初值变成 missing**（说明真的尝试过 import）
                return st == 'missing' and init_state != 'missing'
            finally:
                M._session, M._state = _bak_s, _bak_st

        # ③ 形态分诊（每条都是踩过的坑）
        _c = _am.classify_response_body
        base_ar = (
            _c(b'<!DOCTYPE html><html></html>')['shape'] == 'html-page'
            and _c(b'<?xml version="1.0"?><rss/>')['shape'] == 'xml-doc'
            and _c(b'89504e470d0a1a0a0000000d49484452')['shape'].startswith('hex-')
            and _c(b'eyJvayI6dHJ1ZX0=')['shape'] == 'base64-json'
            and _c(b'{"ok":1}')['shape'] == 'json-or-text'
            and _c(b'#EXTM3U')['shape'] == 'hls-manifest'
            and _c(b'hello plain text')['shape'] == 'text'
            and _c(b'hello world this is plain' * 2)['shape'] == 'text'   # 纯英文长串不许当 base64
            and _am.analyze_transform_chain('d=atob(x);j=JSON.parse(d)') == ['base64', 'json']
            # ★ curl_cffi 状态机三态（S148 审计）：原版 `_available=False` 初值 +
            #   `if _available is False: return None` → **首次调用永远返回 None**，
            #   装了 curl_cffi 也永远用不上，静默降级。
            #   证伪方式：**临时把状态重置成当初的坏初值**，看首次调用会不会直接返回 None。
            #   正确实现应把 unknown → 尝试 import；坏实现会因 False 短路。
            and _cffi_ok()
        )
        if not (hit_ar and clean_ar and base_ar):
            print(f'  ⚠️ 破甲模块探针不达标：严格性={hit_ar} 图片判据={clean_ar} 形态分诊={base_ar}')
            _bad_pad = b'AAAAAAAAAAA' + bytes([5])
            print(f'     PKCS7 非法填充返回: {_am.pkcs7_unpad(_bad_pad)!r}（应 None）')
            print(f'     随机二进制还原: {_rnd[0]} was={_rnd[2]}（应 False）')
            print(f'     HTML/uploads 分诊: {_c(b"<!DOCTYPE html><html></html>")["shape"]}')
    except Exception as e:
        print(f'  ⚠️ 破甲模块探针异常：{type(e).__name__}: {e}')
    results.append(('破甲模块·严格填充/语义判图/形态不串', hit_ar, clean_ar, base_ar))
def _sc_forge_shell(results):
    """self-check 组10：源验证器/触发词覆盖/写源留档/record_id三态/壳库readback/生态门面（C6 拆出，顺序不变）。"""
    # ── ㉕ py源5步验证器（spider_verify）：坏源必须判失败、不可达不许判失败 ──
    # 离线桩：把 verify_spider 的"跑真源"换成"看源码特征"，只验**判定逻辑**本身。
    # 为什么必须这么测（S148 踩过两次）：
    #   ① 第一版验证器"从源产出里找多集样本"，坏源自己把 84 集砍成 1 集，
    #      于是永远找不到多集样本 → 退化成单片分支 → 照样判通过。**验证器被被测对象牵着走**。
    #   ② 域名不存在的源，源自己 `_get` 把网络异常吞成空串，产出上与"源码写错"一模一样。
    #      不区分就判"失败"= 冤杀（M-R60：失败 ≠ 我方写错）。
    hit_sv = clean_sv = base_sv = False
    try:
        import spider_verify as _sv
        # 静态读取 host + 裸 DNS 探测，不执行被测代码（不让嫌疑人自己作证）
        _h = _sv._declared_host('self.host = "https://a.b.c"\n')
        _h2 = _sv._declared_host("self.host = 'https://x.y'\n")
        _h3 = _sv._declared_host('self.host = ""\n')
        _reach_bad = _sv._reachability('https://no-such-host-xyz-98765.invalid')
        _reach_none = _sv._reachability('')
        # 不可达 ≠ 失败：只有**确证**解析不了才算 unreachable，其它一律 unknown（不许猜）
        hit_sv = (_h == 'https://a.b.c' and _h2 == 'https://x.y'
                  and _h3 in ('', None) and _reach_bad == 'unreachable'
                  and _reach_none == 'unknown')
        # 三态判定：全项失败时，看能否区分「网络型」与「逻辑型」——这是本轮真撞出来的漏洞
        # 必须直接调**被测函数**，不许在探针里复制一份正则（两处各写各的，改了不同步＝假绿）
        _looks_net = _sv._looks_network_fault(
            'Connection refused | getaddrinfo failed | timeout')
        _looks_net2 = _sv._looks_network_fault('第1页 0 条 / 分类 0 个')
        # 假阳性基线：`Connection: keep-alive` / `sqlite3.Connection` 是正常写法，
        # 不许被当成网络故障证据（否则好源只要写了 header 就永远判"无法判定"，验证器被废掉）
        _fp_header = _sv._looks_network_fault(
            "headers={'Connection': 'keep-alive'} sqlite3.Connection")
        clean_sv = (_looks_net and not _looks_net2)
        base_sv = (not _fp_header)
    except Exception as e:
        print(f'  ⚠️ 源验证器探针异常：{type(e).__name__}: {e}')
        base_sv = False
    if not (hit_sv and clean_sv and base_sv):
        print(f'  ⚠️ 源验证器探针不达标：静态取host={hit_sv} 三态分诊={clean_sv} 基线={base_sv}')
    results.append(('源验证器·坏源判失败/不可达不冤杀', hit_sv, clean_sv, base_sv))

    # ── ㉖ 触发词覆盖（S148·M-R104）：技能写得再好，没被触发就等于不存在 ──
    # 病根实测：一次长会话 12884 次工具调用，`Skill` 只被调 3 次、探源司 0 次——
    # 用户说"哪个底包能加更多线路"，而 SKILL.md 只认"探源/采集线路"这类术语，匹配不上。
    # 这条探针守的是：**业务语言必须真在触发词里**，且业务语言与术语**两栏都要有**。
    hit_tw = clean_tw = base_tw = False
    try:
        _sk = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'SKILL.md')
        _src = open(_sk, encoding='utf-8').read()
        _desc = _src.split('---')[1] if _src.startswith('---') else _src[:3000]
        # 用户真实会说、但**不含任何术语**的句子（S148 原话抽样）
        # 词表必须与 SKILL.md description「触发词：」段**逐字一致**——
        # 探针自己另编一套就等于自测自绿（S148 教训）。
        _biz = ['哪个底包能加更多线路', '给D包换路线', '线路失效了点不开',
                '这个接口还能用吗', '这批源帮我验一遍', '帮我写个源']
        _terms = ['上游仓库', '采集线路', '探源司', '真实测活']
        # ★ 不许只查 description 自己——那样 description 怎么写都"通过"。
        #   必须与 **conductor 黄金用例**交叉：同一句话既要在触发词里，也要能被路由打分命中。
        #   两处都过才算真接得住（对应 M-R76「路由须验接得住」）。
        _hit_desc = all(k in _desc for k in _biz) and all(k in _desc for k in _terms)
        _route_ok = None
        try:
            import importlib.util as _iu
            _cp = os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))), 'ecosys-conductor', 'scripts',
                'conductor.py')
            if os.path.exists(_cp):
                _sp = _iu.spec_from_file_location('_cond_probe', _cp)
                _mod = _iu.module_from_spec(_sp)
                _sp.loader.exec_module(_mod)
                _t = _mod._skill_desc_terms('probe-hub')
                _blob = 'probe-hub ' + ' '.join(_t)
                _sc = [_mod._route_score(q, _blob) for q in _biz]
                _route_ok = all(x >= 2 for x in _sc)     # ROUTE_HIT_MIN=2
                # ★ 再验「两源一致」（M-R76 的核心主张）：description 声明的词
                #   必须**都**在 _SKILL_INTENTS 投影里。只改真源不改投影 = 静默失配，
                #   而单看路由打分是**看不出来**的（打分走的是 description 抽出来的词）。
                #
                # ★★ 必须**限定到本技能**（S148 自检撞出来的探针自身假红）：
                #   _sig_health() 返回的是**全生态**的告警列表。第一版只按
                #   `'话术两源缺口' in str(d)` 过滤，于是**别的 9 个技能**的缺口
                #   会全部被收进来，令 probe-hub 自己被判红——实测报出 9 条，
                #   没有一条是 probe-hub 的。这种"张冠李戴"比漏报更坏：
                #   它把红信号变成噪音，久而久之所有人都会忽略它。
                _gap = [d for d in (_mod._sig_health() or [])
                        if '话术两源缺口' in str(d) and 'probe-hub' in str(d)]
                _route_ok = _route_ok and not _gap
                if _gap:
                    print(f'  ↳ 本技能两源缺口：{_gap[0]}')
        except Exception as _e:
            print(f'  ⚠️ 路由交叉校验跳过（{type(_e).__name__}）——只验了 description 字面')
        hit_tw = _hit_desc and (_route_ok is not False)
        # 清理语义：§2.1 铁律段必须在（不许只补触发词而不写"不许绕过技能"）
        clean_tw = ('不许看见脚本就直接跑' in _src and 'M-R104' in _src)
        # 假阳性基线：description 不许**只**堆术语而不含业务语言——
        # 用"业务语言命中数 ≥3"当门槛，防止只加一句凑数
        base_tw = sum(1 for k in _biz if k in _desc) >= 3
    except Exception as e:
        print(f'  ⚠️ 触发词探针异常：{type(e).__name__}: {e}')
        base_tw = False
    if not (hit_tw and clean_tw and base_tw):
        print(f'  ⚠️ 触发词探针不达标：业务+术语齐备={hit_tw} 铁律段在={clean_tw} 基线={base_tw}')
    results.append(('触发词覆盖·业务语言能匹配上', hit_tw, clean_tw, base_tw))

    # ── ㉘ 写源留档闸门（S161·P018·`forge.py`）──
    # 守的是**「没验过的源不许进 sources/」**这条规矩。
    # 为什么必须活体证明：S148 跑通了写源，产物却在 `/tmp/forge/spider.py`——
    #   验完不留档，一次关机就蒸发；而"不留档"这件事**没有任何机制会报警**，
    #   它安静得跟正常没两样。这正是本探针要堵的那个方向：**失效时不是报错，是没发生**。
    # 判据（三条）：
    #   hit   注入一个必然失败的源 → verdict 必须不是「通过」，且 --save 后 sources/ 里**不许**多出它
    #   clean 三个坏源（逻辑空 / 语法错 / 域名不存在）跑完，索引条数必须**一条不变**
    #   base  反向基线：不加 --save 时，即使验过也不许往 sources/ 落任何文件（不许偷偷留档）
    hit_fg = clean_fg = base_fg = False
    _fg_tmp = []

    class _O:                       # 轻量 argparse 替身：只带 _cmd_verify 需要的两个字段
        def __init__(self, **kw):
            self.__dict__.update(kw)

    try:
        import forge as _fg
        import tempfile, shutil as _sh
        _tmpd = tempfile.mkdtemp(prefix='forgeprobe_')
        _fg_tmp.append(_tmpd)

        def _n_idx():
            _d = _fg._load_index()
            return -1 if _d is None else len(_d.get('sources') or [])

        def _mk(name, body):
            p = os.path.join(_tmpd, name)
            open(p, 'w', encoding='utf-8').write(body)
            return p

        # ★ S161 补：**注入前先清场**。上一轮若注入过缺陷（故障注入演练），
        #   坏源被错误留档后会**固化在索引里**，磁盘文件却早没了——
        #   于是变成「索引有、磁盘无」的孤儿账，而只看条数的判据发现不了。
        #   教训：**探针造的脏，探针必须自己擦**；清场放在注入之前，不靠"记得擦"。
        for _n in ('bad_syntax.py', 'bad_empty.py'):
            _fg._revoke(_n)
            _fp = os.path.join(_fg.SOURCES_DIR, _n)
            if os.path.exists(_fp):
                os.remove(_fp)

        # 坏源 A：语法错（最快、最不依赖网络，不会因外网波动而假红）
        _bad_syntax = _mk('bad_syntax.py', 'def (:\n    pass\n')
        # 坏源 B：能 import、但 homeContent 返回空 —— "逻辑全错"
        _bad_empty = _mk('bad_empty.py',
                         'class Spider(object):\n'
                         '    def getName(self): return "probe-bad"\n'
                         '    def init(self, e=""): self.host = "https://127.0.0.1:1"\n'
                         '    def homeContent(self, filter=False): return {"class": []}\n')
        _before = _n_idx()
        _r1 = _fg._cmd_verify(_O(file=_bad_syntax, save=True, quiet=True))
        _r2 = _fg._cmd_verify(_O(file=_bad_empty, save=True, quiet=True))
        # 关键断言：跑完两个坏源，索引**一条都不许多**
        hit_fg = (_r1 != 0 and _r2 != 0 and _n_idx() == _before
                  and not os.path.exists(os.path.join(_fg.SOURCES_DIR, 'bad_syntax.py'))
                  and not os.path.exists(os.path.join(_fg.SOURCES_DIR, 'bad_empty.py')))

        # clean（清理语义）：**必须真去核「索引里还有没有这两个名字」**，不能只比条数。
        #   只比条数会漏掉「一进一出」的情形（旧坏源被清、新坏源被加，条数不变=假绿）。
        #   这一条正是 S161 扎出来的：注入缺陷后索引里永久多出 bad_syntax.py，
        #   而那时判据只看条数回没回到 before —— 上一轮的脏早把 before 抬高了。
        clean_fg = (_n_idx() == _before
                    and _fg._revoke('bad_syntax.py') and _fg._revoke('bad_empty.py'))

        # base（反向基线）：不加 --save 时，即便验过也**不许**落文件
        #   偷懒用一个"通过"的源代价太大（要联网），此处退一步只断言**未通过**的情况：
        #   `_cmd_verify(file=坏源, save=False)` 之后 sources/ 目录的文件集合不变。
        _snap = set(os.listdir(_fg.SOURCES_DIR)) if os.path.isdir(_fg.SOURCES_DIR) else set()
        _fg._cmd_verify(_O(file=_bad_empty, save=False, quiet=True))
        _snap2 = set(os.listdir(_fg.SOURCES_DIR)) if os.path.isdir(_fg.SOURCES_DIR) else set()
        base_fg = (_snap == _snap2)
    except Exception as e:
        print(f'  ⚠️ 写源留档探针异常：{type(e).__name__}: {e}')
    finally:
        for _d in _fg_tmp:
            try:
                _sh.rmtree(_d, ignore_errors=True)
            except Exception:
                pass
    if not (hit_fg and clean_fg and base_fg):
        print(f'  ⚠️ 写源留档探针不达标：坏源被拦={hit_fg} 索引未污染={clean_fg} 不偷存={base_fg}')
    results.append(('写源留档·没验过的不许进 sources/ ', hit_fg, clean_fg, base_fg))


    # ㉗ 取 record_id 映射的三态（S156·M-R60）：这是"能不能安全写云端"的地基。
    #    旧实现 `except: return {}` 把"读不到"折成"没有已存行"→ 调用方按全新表走
    #    **全量新增**（docstring 自述曾因此一次插出 4 条重复行）。三态断言：
    #      · 读失败 → 必须返回 **None**（无法判定），且**不得**是 {}（会被当空表）
    #      · 真·空表 → 必须返回 {}（可初始化），且**不得**是 None（会被当失败）
    #      · 游标不前进 → 必须退出，不得死转
    #    用 monkeypatch 直接驱动真实函数，不复制逻辑（M-R76：验的必须是生产那条路）。
    _orig_run = subprocess.run
    class _R:
        def __init__(self, out, rc=0, err=''):
            self.stdout, self.returncode, self.stderr = out, rc, err
    def _paginate_replies(replies):
        it = iter(replies)
        def _fake(cmd, **kw):
            if any(str(x).endswith('query_database_record.py') for x in (cmd or [])):
                return _R(next(it))
            return _R('{}')
        return _fake
    try:
        # (a) 读失败 → None（不是 {}）
        def _boom(cmd, **kw):
            raise RuntimeError('模拟网络中断')
        subprocess.run = _boom
        hit_rid = (_cloud_id_map('FAKE_DB', 'id') is None)
        # (b) 真·空表 → {}（不是 None）
        import json as _json
        subprocess.run = _paginate_replies(
            [_json.dumps({'results': [], 'has_more': False})])
        clean_rid = (_cloud_id_map('FAKE_DB', 'id') == {})
        # (c) 游标恒不前进 → 必须只查 2 次就退出（第 1 次拿首页，第 2 次发现游标没变）
        _calls = {'n': 0}
        def _stuck(cmd, **kw):
            if any(str(x).endswith('query_database_record.py') for x in (cmd or [])):
                _calls['n'] += 1
                if _calls['n'] > 50:
                    raise AssertionError('游标不前进未被拦截——死循环（S110 坑复发）')
                return _R(_json.dumps({'results': [], 'has_more': True,
                                       'next_cursor': 'SAME'}))
            return _R('{}')
        subprocess.run = _stuck
        _cloud_id_map('FAKE_DB', 'id')
        base_rid = (_calls['n'] <= 3)
    finally:
        subprocess.run = _orig_run
    results.append(('record_id三态·读失败≠空表', hit_rid, clean_rid, base_rid))

    # C3（T4 治理·2026-1027）：壳源零探针 → 接入壳库 readback，呼应 S196 ⑤「入库必须 readback」
    # 实测：DB_SHELL=hPF6AUmWmOQa26tOg8Wjb5，good=True，rows=19；坏 id good=False（构造数据可证伪）
    hit_shell = clean_shell = base_shell = False
    try:
        import shellcrack  # probe.py 顶层未 import，此处按需导入（SSOT 取 DB_SHELL）
        sys.path.insert(0, '/root/.codebuddy/skills/eco-ledger/scripts')
        import core_ledger as _L
        _rows, _good = _L.rows_of(shellcrack.DB_SHELL)
        base_shell = bool(_good) and len(_rows) > 0            # 正常态：壳库可读且有数据
        _bad, _bad_good = _L.rows_of('__不存在的壳库id__')       # 构造数据：改坏目标
        hit_shell = (_bad_good is False) and base_shell         # 改坏仍红 + 正常态绿
        clean_shell = base_shell
    except Exception as e:
        print(f'  ⚠️ 壳库 readback 探针异常：{type(e).__name__}: {e}')
    results.append(('壳库readback', hit_shell, clean_shell, base_shell))

    # C4（T2 治理·2026-1027）：_eco_linked_probe 原为零调用休眠函数 → 接入自检验证生态联动门面可达
    # 实测：file_to_janitor/register_monitor 均可调用（core.py:279/317）
    # 呼应 S169 ⑤：构造数据——临时令门面不可达，探针必须变红（防探针自骗）
    hit_eco = clean_eco = base_eco = False
    try:
        import unittest.mock as _mock
        _janitor_ok, _monitor_ok = _eco_linked_probe()
        base_eco = bool(_janitor_ok and _monitor_ok)            # 正常态：两门面可达
        _mod = sys.modules[__name__]
        with _mock.patch.object(_mod, 'file_to_janitor', None):
            _janitor_bad, _ = _eco_linked_probe()
        hit_eco = (_janitor_bad is False) and base_eco          # 门面失效时仍红 + 正常态绿
        clean_eco = base_eco
    except Exception as e:
        print(f'  ⚠️ 生态联动门面探针异常：{type(e).__name__}: {e}')
    results.append(('生态联动门面可达', hit_eco, clean_eco, base_eco))


def cmd_self_check(a):
    """六组活体探针：注入已知异常 → 必须捕获 → 清理 → 必须清零；并验假阳性基线。"""
    # ★ S177 步2 踩到的真坑，记在这里当护栏：本函数 1300 多行，中途有一句
    #   `a = _score_repo(4240, ...)`（候选评分那条探针的局部变量）把**参数 a 覆盖成了 '59.8'**。
    #   于是末尾 `getattr(a,'as_json')` 永远拿不到命令行开关 —— 不报错、不告警、
    #   `--json` 静默无效，只有真去 trace 才看得见（实测：函数内 a 的值是字符串 '59.8'）。
    #   这正是 M-R63 那一类"检测器自己坏了却仍然报绿"的形态。
    #   根治办法不是去改那个局部变量，而是**参数一进门就锁进专用名字**：
    #   后面谁再覆盖 a 都伤不到它。
    _opt = a
    print('🔬 探源司·监测器活体探针（注入 → 捕获 → 清理）\n')
    results = []
    # C6：按探针组抽 10 个子函数（原 1580 行体内联拆出；顺序/行为不变）
    _sc_monitor(results)
    _sc_fetch_mirror(results)
    _sc_platform(results)
    _sc_harvest(results)
    _sc_search_cloud(results)
    _sc_blacklist(results)
    _sc_harvest_payload(results)
    _sc_pan_pak(results)
    _sc_eco_gate(results)
    _sc_forge_shell(results)

    # ── ㊱ 总调度路由可达（C8·T6）：入口分裂收口的机器判据 ──
    hit_rt = clean_rt = base_rt = False
    try:
        _need = [('quality', None), ('quality-batch', None), ('content-probe', None)]
        hit_rt = all(k in DISPATCH and callable(DISPATCH[k]) for k in _need)
        clean_rt = callable(DISPATCH.get(('livecheck',))) \
            and callable(DISPATCH.get(('forge', 'verify')))
        base_rt = build_parser() is not None      # 解析器可构建（假阳性基线）
    except Exception as e:
        print(f'  ⚠️ 路由可达探针异常：{type(e).__name__}: {e}')
    results.append(('总调度路由可达', hit_rt, clean_rt, base_rt))

    ok = 0
    for name, hit, clean, base in results:
        good = hit and clean and base
        ok += 1 if good else 0
        print(f'  {"✅" if good else "❌"} {name}：捕获={"是" if hit else "否"} '
              f'清理后清零={"是" if clean else "否"} 假阳性基线={"净" if base else "脏"}')
    print(f'\n探针结果: {ok}/{len(results)} 监测器逻辑有效'
          f'（"依赖可达≠逻辑对"，故需活体证明）')

    # S177 步2：--json 结构化输出。
    # 判定逻辑**完全没动**——上面那圈 ok/print 就是原样，这里只是额外吐一份机器可读的。
    # 为什么两个都留：人看的那几行是"眼睛的门"，删了没人能一眼看红绿；
    # JSON 是"外部程序的门"，没它别的技能只能去 grep 文字（脆、会漂）。
    # 下游契约：items[] 六项齐全；四元组有 None → good=False 且缺哪项写进 missing，
    # 不许把 None 折成 False（M-R60：不可判定 ≠ 判定为坏）。
    # ★ 坑（S177 步2 当场踩到，比上面那条更深）：**必须读 `_opt`，不能读 `a`**。
    #   本函数中途 `a` 被覆盖成候选评分的结果（'59.8'），读 `a` 永远拿不到开关。
    #   另外参数目的地也刻意叫 `as_json` 不叫 `json`——顶层 `import json`，
    #   `add_argument('--json')` 默认目的名就是 `json`，会与模块名撞车。
    #   这两个坑叠在一起时表现为：`--json` 不报错、也不输出，看起来"命令跑了"。
    #
    # ★★ 第二个坑（真注入坏样本才炸出来，务必留着这段顺序）：
    #   JSON 输出必须排在 `sys.exit(1)` **之前**。第一版把 exit 写在前面，结果是
    #   "33/33 绿 → 有 JSON；某条探针坏 → exit(1) 直接退，JSON 一行都不吐"。
    #   也就是：**越需要证据的时候越没有证据**（M-R63 的同型反面——报红是真的，
    #   但报红的原因全丢了，外部程序只能看到一个光秃秃的退出码）。
    #   现在的顺序：判红 → 先把证据吐出来 → 再 exit(1)。
    if getattr(_opt, 'as_json', False):
        items = []
        for _n, _h, _c, _b in results:
            _miss = [k for k, v in (('hit', _h), ('clean', _c), ('base', _b))
                     if v is None]
            items.append({'name': _n,
                          'hit': bool(_h) if _h is not None else None,
                          'clean': bool(_c) if _c is not None else None,
                          'base': bool(_b) if _b is not None else None,
                          'good': bool(_h and _c and _b),
                          'missing': _miss})
        print(json.dumps({
            'probe': 'probe-hub/self-check',
            'spec': 'self_probe_spec/v1',
            'ts': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'total': len(items),
            'passed': ok,
            'all_good': ok == len(items),
            'items': items,
        }, ensure_ascii=False, indent=1))

    # 证据先出、判红后走。顺序不许调（见上面 ★★ 那段）。
    if ok != len(results):
        sys.exit(1)


L1_MIN_FAILS = 2      # L1 精确黑：至少失败 2 次
L1_MIN_GAP_DAYS = 1   # 且两次间隔 ≥1 天（防单次抖动误杀）


def _parse_day(s):
    """解析 YYYY-MM-DD / ISO 时间为 date；失败返回 None。"""
    s = (s or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except Exception:
            continue
    return None


def judge_level(fails, last_fail, first_fail=None, reason='', detail=''):
    """按方案A定层。返回 (层级, 依据串)。

    规则（2026-10-17 用户选定 A 稳）：
      · fails >= 2 且 两次间隔 >= 1 天  → L1精确黑
      · fails >= 1 但未达上述           → L2观察（拦下强制测活，过则撤销）
      · 单次失败永不为 L1               ← 旧库当年就是错在"单次/严格采样即判死"
    域名级 L3 不在此判，由 load_blacklist() 按"同 host ≥3 个指纹全 L1"聚合。

    ★S169 **失败原因必须参与定层**：
      原实现只看次数与间隔，不看为什么失败。于是「证书过期」和「域名没了」走同一条路——
      实测已有 172 条 L1 里，17 条的失败原因是证书问题（其中 ryzy.tv、lz.118318.xyz
      加 -k 后返回 87594 / 302199 条**真实实时数据**）。证书是**运维问题、续期即恢复**，
      把它判「确认死」并整域丢弃，是拿一个可自愈的信号做了不可逆的决定。
      规则：
        · 证书类（cert_expired / cert_invalid / tls）→ **最高 L2**，封顶不升 L1，
          依据里写明「证书类·续期可恢复」。这类源仍然被拦下强制测活（L2 语义），
          但不会被当成永死；pardon 一次即可转正。
        · 其余原因（DNS 无记录/连接被拒/超时/4xx）→ 照旧按次数+间隔定层。
    """
    fails = int(fails or 0)
    _txt = f'{reason or ""} {detail or ""}'
    _cert = any(k in _txt for k in ('cert_expired', 'cert_invalid', '证书'))
    d_last, d_first = _parse_day(last_fail), _parse_day(first_fail)
    if _cert:
        return 'L2观察', (f'证书类失败（{fails}次）·续期可恢复，封顶 L2 不判死'
                          f'—原始原因：{_txt.strip()[:60]}')
    if fails >= L1_MIN_FAILS and d_last and d_first:
        gap = (d_last - d_first).days
        if gap >= L1_MIN_GAP_DAYS:
            return 'L1精确黑', f'失败{fails}次·间隔{gap}天(≥{L1_MIN_GAP_DAYS}天)'
        return 'L2观察', f'失败{fails}次但间隔{gap}天<{L1_MIN_GAP_DAYS}天（不足置信）'
    if fails >= L1_MIN_FAILS:
        return 'L2观察', f'失败{fails}次但无时间跨度证据（单批次采样不算）'
    return 'L2观察', f'仅失败{fails}次（单次失败永不判 L1）'


def _live_fps():
    """成品源库里"实测通过"的指纹集合 —— R2 活源免疫用。"""
    try:
        rows = rows_of(DB_PROD)
        return {source_fp(r.get('api接口') or '') for r in rows
                if (r.get('真实测活') or '') == '通过'}
    except Exception:
        return set()


def cmd_bl_audit(a):
    """体检 L1：抽样复验，找出「被判死但其实活着」的误杀。

    ★S169 为什么需要它：黑名单是**只进不出**的设计——`mark` 要 `--yes` 手写，
    自动链路没有；平反要人先起疑。于是误杀只能靠"有人偶然发现"。
    实测这一轮手工抽了 172 条里的 17 条证书类，就捞出 5 条误杀（其中 4 条
    cy 当时内容完全可用：ryzy.tv 8.7 万条、lz.118318.xyz 30 万条）。
    **没有体检，这些源就永远躺在 L1 里**。

    判据不新造：直接复用 `_alive_line`（= livecheck 真实可用口径 + 证书类复验），
    与 `pardon` 同一把尺子（M-R38）。本命令只做"抽样 + 报告 + 可选平反"。
    """
    from concurrency import run_bounded, TIER_WEB
    _HUB = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
    if _HUB not in sys.path:
        sys.path.insert(0, _HUB)

    bl = load_blacklist(verbose=False)
    l1rows = [r for r in bl['rows']
              if (r.get('拉黑层级') or '').startswith('L1')
              and r.get('判死状态') != '已平反'
              and (r.get('样例api') or '').strip()]
    if not l1rows:
        print('ℹ️ L1 无记录，无需体检')
        return
    # 抽样策略：**确定性**抽样（按指纹排序后等距取），不用随机——
    #   随机抽样两次跑出不同结果，就没法判断"误杀是变多了还是抽到的不同"。
    l1rows.sort(key=lambda r: str(r.get('源指纹') or ''))
    n = len(l1rows)
    if a.sample and a.sample < n:
        step = n / a.sample
        picked = [l1rows[int(i * step)] for i in range(a.sample)]
    else:
        picked = l1rows
    print(f'🩺 L1 体检：{n} 条中抽 {len(picked)} 条复验（确定性等距抽样，非随机）')

    rep = run_bounded(picked, lambda r: _alive_line((r.get('样例api') or '').strip(),
                                                    timeout=max(a.timeout, 12)),
                      tier=TIER_WEB, keep_order=True, label='bl-audit', on_error='record')
    alive, dead, unknown = [], 0, 0
    for r, res in zip(picked, rep.results):
        ok, ev = (res if res else (False, '探测线程异常（见并发报告）'))
        api = (r.get('样例api') or '').strip()
        if ok:
            alive.append((r, api, ev))
        # ★S169：三态分类要按**证据强度**分，不能按"消息里有没有'不可达'"分。
        #   DNS 无 A 记录 = 域名真没了 → **确认死**（公共 DNS 复核也查不到，实证 46 条全无记录）；
        #   超时/连接被拒 = 我这边到不了，**不等于**它死了 → 无法判定；
        #   4xx/无播放地址 = 有响应但内容不可用 → 确认仍失效。
        elif 'DNS 无 A 记录' in ev or '域名真没了' in ev:
            dead += 1
        elif ('超时' in ev or '无法判定' in ev or '不可达' in ev
              or '证书' in ev) and '4xx' not in ev and '无vod_play_url' not in ev:
            unknown += 1
        else:
            dead += 1
    print(f'\n   ✅ 复验通过（**误杀**）：{len(alive)}')
    print(f'   ❌ 确认仍失效        ：{dead}')
    print(f'   ⚠️ 无法判定          ：{unknown}')
    if alive:
        print('\n 误杀明细（这些源被判 L1「确认死」，但内容仍然可用）：')
        for r, api, ev in alive:
            print(f'   • {r.get("源指纹")}  [{r.get("失效原因")}]')
            print(f'     {ev[:110]}')
    if not a.yes:
        if alive:
            print(f'\n（预演。加 --yes 会对通过复验的条目执行平反；本次最多 {a.trials} 条）')
        return
    if not alive:
        print('\n无需平反')
        return
    done = 0
    for r, api, ev in alive:
        if done >= max(1, a.trials):
            print(f'\n（已达本次上限 {a.trials} 条，其余下轮再平反）')
            break
        print(f'\n── 平反 {api}')
        rc, _ = _sh([sys.executable, os.path.abspath(__file__), 'blacklist', 'pardon',
                     '--api', api, '--timeout', str(max(a.timeout, 12)), '--yes'], timeout=300)
        if rc == 0:
            done += 1
        else:
            print(f'   ⚠️ 平反返回 {rc}——该条跳过，下轮重试')
    print(f'\n✅ 本轮平反 {done} 条')


def cmd_bl_fp(a):
    print(source_fp(a.api))


def cmd_bl_judge(a):
    f = source_fp(a.api)
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    last = a.last or today
    first = a.first or last
    lvl, why = judge_level(a.fail, last, first, reason=a.detail, detail=a.detail)
    print(f'源指纹  : {f}')
    print(f'层级    : {lvl}')
    print(f'依据    : {why}  （首次 {first} / 末次 {last}）')
    lives = _live_fps()
    if f in lives:
        print('⚠️ R2 活源免疫：成品库存在实测通过的同一指纹，**禁止写入 L1**')
        return
    print('R2 活源免疫：未命中（可写）')


def cmd_bl_check(a):
    """检查某条 api 会不会被拦 —— 正负例验证用。"""
    f = source_fp(a.api)
    bl = load_blacklist(verbose=False)
    host = f.split('/')[0]
    if f in bl['l1']:
        print(f'🚫 L1拦截  {f}')
    elif f in bl['l2']:
        print(f'⚠️ L2观察  {f}（会被拦下强制测活，通过即放行）')
    elif host in bl['l3host']:
        print(f'✅ 放行(降权 L3)  {f}   host={host} 命中域名预警，收录但排队尾')
    else:
        print(f'✅ 放行  {f}')
    if f in _live_fps():
        print('   ↑ R2：该指纹在成品库中实测通过（活源免疫，不应被 L1 拦）')


def cmd_bl_list(a):
    bl = load_blacklist(verbose=False)
    want = (a.level or '').upper()
    if want in ('', 'L1'):
        print(f'=== L1 精确黑（{len(bl["l1"])} 个指纹，直接丢弃）')
        for f in sorted(bl['l1'])[:60]:
            print('   ', f)
        if len(bl['l1']) > 60:
            print(f'    … 其余 {len(bl["l1"])-60} 条')
    if want in ('', 'L2'):
        print(f'\n=== L2 观察（{len(bl["l2"])} 个指纹，拦下强制测活）')
        for f in sorted(bl['l2'])[:40]:
            print('   ', f)
        if len(bl['l2']) > 40:
            print(f'    … 其余 {len(bl["l2"])-40} 条')
    if want in ('', 'L3'):
        print(f'\n=== L3 域名预警（{len(bl["l3host"])} 个 host，仅降权不丢弃）')
        for h in sorted(bl['l3host']):
            print('   ', h)


def cmd_bl_pardon(a):
    """平反：**先实测，实测通过才撤**（S146·M-R96 铁律「活源免疫」的落地动作）。

    病根（2026-10-17 实证）：失信库有 `lbapi9.com/api.php/provide/vod/at/xml` 一条
    L1「确认死·仍失效」，但刚才 curl 该地址返回 **HTTP 200 + XML 正常**，
    recordcount 335045。而 S145 的指纹归一又会把 `/at/xml` 折叠掉 →
    折叠后正好等于整站标准指纹 → **一条活源把整站拉黑了**。

    这就是「单次/严格采样即判死」的老病复发。所以平反必须：
      ① 先按**原 URL**（不是指纹）真测活，通过才继续
      ② 撤的是那一条 L1 记录，同时写清证据
      ③ 不同网络环境下「我这能通不代表你那边能通」——所以备注要留证据，不写死结论
    """
    api = a.api
    f = source_fp(api)
    print(f'待平反 api : {api}')
    print(f'源指纹     : {f}')
    ok, ev = _alive_line(api, timeout=max(a.timeout, 12))
    print(f'实测       : {"✅ 通过" if ok else "❌ 未通过"} —— {ev[:120]}')
    if not ok:
        print('\n⛔ 实测未通过，拒绝平反（活源免疫只保护**真的活着**的源）')
        sys.exit(1)

    st, rows = _cloud_rows(DB_DEAD)
    if st != 'ok':
        print(f'❌ 失信库读不到（{st}）——不得当空库处理')
        sys.exit(1)
    # S146 修：`_cloud_rows` 走 get_database_content，**不返回 record_id**（S088 已知）。
    # 删记录必须用 query_database_record 拿真 ID，曾因此「命中 1 条但删 0 条」空转。
    rid_map = {}
    try:
        cur, guard = None, 0
        while True:
            c = ['python3', f'{LIB}/database/query_database_record.py',
                 '--database-id', DB_DEAD, '--page-size', '100']
            if cur:
                c += ['--start-cursor', cur]
            d = json.loads(subprocess.run(c, capture_output=True, text=True,
                                          timeout=30).stdout)
            for r in d.get('results', []):
                k = str(r.get('源指纹') or '').strip()
                if k and r.get('record_id'):
                    rid_map[k] = r['record_id']
            if not d.get('has_more'):
                break
            nc = d.get('next_cursor')
            if not nc or nc == cur:
                break
            cur, guard = nc, guard + 1
            if guard > 5:
                break
    except Exception as e:
        print(f'❌ 取 record_id 失败（{type(e).__name__}）——不敢删（无 ID 硬删会删错）')
        sys.exit(1)
    hits = [r for r in rows
            if str(r.get('源指纹') or '').strip() == f
            or str(r.get('样例api') or '').strip() == api.strip()]
    if not hits:
        print('ℹ️ 失信库里没有这条，无需平反')
        return
    for h in hits:
        rid = rid_map.get(str(h.get('源指纹') or '').strip()) or \
            rid_map.get(str(h.get('样例api') or '').strip())
        h['record_id'] = rid
        print(f'  命中：{h.get("源指纹")} | {h.get("拉黑层级")} | {h.get("判死状态")} | rid={rid}')
    if any(not h.get('record_id') for h in hits):
        print('❌ 有命中项取不到 record_id —— 中止（不敢盲删）')
        sys.exit(1)
    if not a.yes:
        print('\n（预演。加 --yes 才真正删除这条失信记录）')
        return
    note = (f'S146 平反：实测通过（{ev[:80]}）——原判 L1 为误杀，'
            f'按 M-R96 活源免疫撤销。注意 /at/xml 折叠后与标准指纹相同，'
            f'曾导致整站被拉黑')
    rids = [h.get('record_id') for h in hits if h.get('record_id')]
    # 先把证据写进备注（留痕），再删
    upd = [{'record_id': rid, '备注': note} for rid in rids]
    try:
        good, err = _cloud_update_ids(DB_DEAD, upd)
        print(f'   备注留痕：{good}/{len(upd)}' + (f'（{err}）' if err else ''))
    except Exception as e:
        print(f'   ⚠️ 备注写入失败（{type(e).__name__}），仍继续删除')
    good, err = _cloud_delete_ids(DB_DEAD, rids)
    print(f'   删除判真：{good}/{len(rids)}' + (f'（{err}）' if err else ''))
    invalidate(DB_DEAD)
    st2, after = _cloud_rows(DB_DEAD)
    print(f'   复核：失信库现 {len(after)} 条（原 {len(rows)}）')


def _cloud_update_ids(db_id, rows):
    """按 record_id 批量更新，以 results[].success 判真。返回 (成功数, 错误串)。

    S146 修（连续踩了两个坑）：
      坑1 `--stdin` + `{database_id, records}` 是**新增**接口的入参形态，
          更新接口虽然也吃 stdin，但 records 里要求 `properties` 子对象；
      坑2 直接把 `{record_id, 备注: ...}` 平铺传进去 → 报
          「records[0].properties 校验后为空，无有效字段」，且**一条都没写进去**，
          回执却是软错误（不抛异常、退出码 0）——典型的「写成功假象」。
    正确形态：`{'record_id': rid, 'properties': {...字段...}}`（见 batch_update 源码
    validate_properties(record.get('properties'))）。
    """
    if not rows:
        return 0, ''
    # 类型推断：更新接口的 properties 要求 `{字段名: {类型键: 值}}`，
    # 平铺传字符串会被 validate_properties 整条拒掉（S146 实测）。
    _SEL = ('拉黑层级', '判死状态', '复测结果', '失效原因', '拉黑范围')
    _NUM = ('连续失败次数', '失效源数', '拦截次数')

    def _wrap(name, v):
        if name in _SEL:
            return {'select': v}
        if name in _NUM:
            try:
                return {'number': int(v)}
            except Exception:
                return {'text': str(v)}
        if isinstance(v, bool):
            return {'checkbox': v}
        if isinstance(v, (int, float)):
            return {'number': v}
        return {'text': '' if v is None else str(v)}

    good, err = 0, ''
    for i in range(0, len(rows), 40):
        chunk = []
        for r in rows[i:i + 40]:
            r = dict(r)
            rid = r.pop('record_id', None)
            if not rid:
                continue
            props = r.pop('properties', None)
            if props is None:
                props = {k: _wrap(k, v) for k, v in r.items()}
            chunk.append({'record_id': rid, 'properties': props})
        if not chunk:
            continue
        out = api('database/batch_update_database_records.py',
                  '--database-id', db_id,
                  '--records', json.dumps(chunk, ensure_ascii=False))
        g, e = _count_success(out, len(chunk))
        good += g
        if e:
            err = (err + ' ' + e).strip()
    return good, err


def cmd_bl_mark(a):
    f = source_fp(a.api)
    # ★S169：脏 URL（协议头重复等）归一化不出有效指纹——指纹里带 `!脏URL` 标记。
    #   这种记录写进去既匹配不到真实 api，又会骗过体检（复验时永远"不可达"被算作正常），
    #   实测失信库已有一条 `https://https://raw.githubusercontent.com/…` 占着 L1。
    #   在写入这一关直接拒，别让脏数据再进来。
    if f.startswith('!脏URL'):
        print(f'⛔ 拒绝写入：URL 形态异常（协议头重复等）——{a.api[:90]}')
        print('   归一化得到的指纹无效，写进去永远匹配不到任何真实 api。请先修正源地址。')
        sys.exit(2)
    today = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    lvl, why = judge_level(a.fails, a.last or today, a.last or today,
                            reason=a.reason, detail=a.detail)
    print(f'源指纹  : {f}')
    print(f'拟写入  : {lvl}  依据：{why}')
    if f in _live_fps():
        print('⛔ R2 活源免疫：该指纹在成品源库实测通过，拒绝写入')
        return
    if not a.yes:
        print('\n（预演。加 --yes 才真正写入）')
        return
    host = f.split('/')[0]
    row = {
        'host': {'text': host},
        '源指纹': {'text': f},
        '拉黑范围': {'select': '单条' if lvl.startswith('L2') else '整域'},
        '拉黑层级': {'select': lvl},
        '层级依据': {'text': why},
        '样例api': {'text': a.api},
        '失效原因': {'select': a.reason},
        '判死状态': {'select': '确认死' if lvl.startswith('L1') else '暂判死'},
        '连续失败次数': {'number': int(a.fails)},
        '首次失败时间': {'text': a.last or today},
        '末次失败时间': {'text': today},
        '复测结果': {'select': '仍失效'},
        '备注': {'text': 'probe blacklist mark'},
    }
    ok, err = _cloud_insert(DB_DEAD, [row])
    print(f'{"✅ 已写入" if ok else "❌ 写入失败"} {err}')


def _cloud_insert(db_id, records):
    """写一批记录 → (是否全成功, 错误串)。复用 _count_success 判真，不看退出码。"""
    body = {'database_id': db_id, 'records': records}
    p = subprocess.run(
        ['python3', f'{LIB}/database/batch_add_database_records.py', '--stdin'],
        input=json.dumps(body, ensure_ascii=False), capture_output=True, text=True)
    good, err = _count_success(p.stdout, len(records))
    try:
        invalidate(db_id)
    except Exception:
        pass
    return (good == len(records)), err


# ══════════════════ 网盘分享取料（S170） ══════════════════
# 场景：用户丢一个网盘分享链接说"看看有啥可借鉴的"。网盘是**外部分发**渠道，
# 上游作者把整包/源配置传这儿，跟 GitHub 仓库是并列的料源。
#
# ★**为什么必须分两步而不是一步**：分享链接本身只有文件清单，**内容要去 CDN 拿**。
#   而 CDN 的下载直链对**本机房的出口 IP 段**一律 412 Precondition Failed。
#
#   ⚠️ S170 更正（重要，别再把这条记错）：
#     上一版这里写的"直链域名 IPv6-only、无 A 记录"是**错的**。实测复查：
#     `socket.getaddrinfo(..., AF_INET)` 返回 220.181.141.99~106 共 8 个 **IPv4**（也有 AAAA），
#     逐 IP 用 `--resolve` 直连**全部 412**。所以 412 与协议族**无关**。
#
#   412 的真正成因（对照实验得出）：
#     · **正确 token → 412，故意改坏的 token → 也 412**（两者响应体完全一致）；
#     · 同一出口访问 `pan.quark.cn` 官网 200、`image.quark.cn` 图片 200；
#     · 响应头 `Server: Tengine` + `Via: ens-cache2.<节点>`，262 字节固定 HTML。
#   → 拦截发生在**校验签名之前**，依据是**请求来自哪段网络**（本沙箱出口 175.24.185.125，
#     腾讯云上海机房）。即：**数据中心/云机房 IP 段被 CDN 边缘节点策略性拒绝**。
#
#   为什么必须说清成因：处置办法与成因一一对应——
#     成分因是"IPv6 不通"→ 处置是"换 IPv6"（**错的，换了过去还是 412**）；
#     成分因是"机房 IP 被拒"→ 处置是"换个出口网络"或"手动下"（对的）。
#   归因错了，处置就会把人引到死路上——这正是本技能 §3.13 反复在防的那种病。


PAN_API = 'https://drive-pc.quark.cn'
# S197 扩 UC 网盘（drive.uc.cn）：与夸克同 API 体系（阿里系），接口路径一致，
# 差异只在域名 / pr 参数 / Referer。UC 的 pr 值实测验证，不对再按三态调整。
PAN_PLATFORMS = {
    'quark': {'api': 'https://drive-pc.quark.cn', 'referer': 'https://pan.quark.cn/', 'pr': 'ucpro'},
    'uc':    {'api': 'https://pc-api.uc.cn',     'referer': 'https://drive.uc.cn/', 'pr': 'UCBrowser'},
    'xunlei':{'api': 'https://api-pan.xunlei.com', 'referer': 'https://pan.xunlei.com/', 'pr': ''},
}
#   ⚠️ UC 下载 403 坏法清单（S197 实测，真实分享 4bb62a7a8e794）：
#     · API 全通：token/detail/直链签发都成功（pc-api.uc.cn 不拒机房 IP）；
#     · 下载 CDN（dl-uf-sz.pds.uc.cn）→ 18/18 全 403；
#     · 对照实验 5 组头全 403（无Referer/UC Referer/api来源页/默认UA/Range）→ 拦截与请求头无关；
#     · 结论：与夸克 412 同构——**下载 CDN 按出口 IP 段拒绝机房网络**，归「无法判定·出口被拒」，
#       不是文件坏、不是 token 错。处置同夸克：换出口网络或用户手动下载，不许记「源失效」。
PAN_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def _pan_req(path, data=None, timeout=30, platform='quark', cookie=None):
    """调分享 API（夸克/UC）。返回 (ok, json|err)。**不吞异常**——网络问题要显式分类。

    cookie：网盘登录 cookie。实测含 __pus/__puus/__kps 组即有效，**不必含 ck 字段**。
    save 类接口 require login 必须传；info/pull 留空即 guest。
    """
    import urllib.request
    import urllib.error
    pf = PAN_PLATFORMS.get(platform) or PAN_PLATFORMS['quark']
    path = path.replace('pr=ucpro', f'pr={pf["pr"]}')
    url = f'{pf["api"]}{path}'
    hdr = {'User-Agent': PAN_UA, 'Referer': pf['referer']}
    if cookie:
        hdr['Cookie'] = cookie
    body = None
    if data is not None:
        hdr['Content-Type'] = 'application/json'
        body = json.dumps(data).encode()
    # C1b：底层取数统一走 core.http_get（err_body 保留服务端错误正文，排障必需）
    try:
        _st, _pl, _rs = http_get(url, timeout=timeout, headers=hdr,
                                 method='POST' if data is not None else 'GET',
                                 data=body, err_body=True)
        if _st == 'ok':
            return True, json.loads(_pl)
        if isinstance(_pl, tuple):
            _code, _b = _pl
            return False, f'HTTP {_code} {(_b or "")[:300]}'.strip()
        return False, f'HTTP错误 {_rs}' if _rs and _rs.startswith('http_') else f'网络错误 {_rs}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def pan_parse_share(text):
    """从分享文本里抠出 pwd_id。用户常见贴法是**整段分享文案**，不是干净 URL。

    实测样本：
      「…打开「夸克APP」即可获取。祄七另枫诶并闭页冉为喔汢 /~d9803bYsb2~:/
       链接：https://pan.quark.cn/s/9d5b78140523 …」
    注意 `/~xxx~/` 是**口令**不是 pwd_id，真正的 id 在 /s/ 后面。
    """
    import re as _re
    if not text:
        return ''
    m = _re.search(r'pan\.quark\.cn/s/([0-9a-zA-Z]+)', text)
    if m:
        return m.group(1)
    # S197：UC 网盘分享（drive.uc.cn/s/<id>）
    m = _re.search(r'drive\.uc\.cn/s/([0-9a-zA-Z]+)', text)
    return m.group(1) if m else ''


def pan_parse_share_ex(text):
    """四平台版：返回 (id, platform)。platform ∈ {'quark','uc','xunlei','baidu'}；认不出返回 ('', '')。"""
    import re as _re
    if not text:
        return '', ''
    m = _re.search(r'pan\.quark\.cn/s/([0-9a-zA-Z_]+)', text)
    if m:
        return m.group(1), 'quark'
    m = _re.search(r'drive\.uc\.cn/s/([0-9a-zA-Z_]+)', text)
    if m:
        return m.group(1), 'uc'
    m = _re.search(r'pan\.xunlei\.com/s/([0-9a-zA-Z_-]+)', text)
    if m:
        return m.group(1), 'xunlei'
    m = _re.search(r'pan\.baidu\.com/s/([0-9a-zA-Z_-]+)', text)
    if m:
        return m.group(1), 'baidu'
    return '', ''


def cmd_pan_info(a):
    """摸清分享里有什么：文件树 + 大小 + 格式。**不下载**。

    这一步在 IPv4-only 环境下也能完全成功——它走的是 API，不是 CDN。
    """
    pwd_id, plat = pan_parse_share_ex(a.url)
    if not pwd_id:
        print('❌ 认不出分享 id：需要夸克 https://pan.quark.cn/s/<id> 或 UC https://drive.uc.cn/s/<id> 或 迅雷 https://pan.xunlei.com/s/<id> 或 百度 https://pan.baidu.com/s/<id>（或含它们的整段分享文案）')
        sys.exit(2)
    if plat == 'xunlei':
        return cmd_pan_info_xunlei(a, pwd_id)
    if plat == 'baidu':
        return cmd_pan_info_baidu(a, pwd_id)

    ok, r = _pan_req(f'/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc&uc_param_str=',
                     {'pwd_id': pwd_id, 'passcode': a.passcode or ''}, platform=plat)
    if not ok:
        print(f'❌ 取 stoken 失败：{r}')
        sys.exit(1)
    if r.get('status') != 200:
        # 三态：有明确业务码就照实报，不折叠成"网络失败"
        print(f"❌ 分享不可用：code={r.get('code')} {r.get('message')}")
        sys.exit(1)
    st = r['data']['stoken']
    meta = r['data']

    # BFS 遍历：分享通常有外层文件夹包着，不能只看第一层
    queue = [('0', '')]
    rows, seen = [], 0
    while queue and seen < (a.limit or 500):
        pdir, prefix = queue.pop(0)
        q = (f'/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc'
             f'&pwd_id={pwd_id}&stoken={quote(st, safe="")}&pdir_fid={pdir}'
             f'&force=0&_page=1&_size=200&_fetch_total=1')
        ok, d = _pan_req(q, platform=plat)
        if not ok:
            print(f'  ⚠️ 目录 {prefix or "/"} 读取失败：{d}（无法判定，不当作空目录）')
            continue
        for f in ((d.get('data') or {}).get('list') or []):
            seen += 1
            name = f.get('file_name') or ''
            full = f'{prefix}{name}'
            if f.get('dir'):
                rows.append({'path': full + '/', 'kind': 'dir', 'size': 0})
                queue.append((f['fid'], full + '/'))
            else:
                rows.append({'path': full, 'kind': 'file',
                             'size': int(f.get('size') or 0),
                             'fmt': f.get('format_type') or '',
                             'fid': f.get('fid'),
                             'token': f.get('share_fid_token') or ''})

    print(f"📦 {'UC' if plat == 'uc' else '夸克'}分享 {pwd_id}")
    print(f"   标题：{meta.get('title')}｜作者：{(meta.get('author') or {}).get('nick_name')}"
          f"｜类型：{meta.get('share_type')}")
    print(f"   本期：{'需提取码' if meta.get('expired_type') == 2 else '无需提取码'}"
          f"｜条目 {len(rows)}\n")
    if a.json:
        print(json.dumps({'pwd_id': pwd_id, 'stoken': st, 'items': rows},
                         ensure_ascii=False, indent=1))
        return

    files = [r for r in rows if r['kind'] == 'file']
    if files:
        print(f"   {'大小':>10}  格式                 路径")
        for r in sorted(files, key=lambda x: -x['size']):
            print(f"   {_fmt_size(r['size']):>10}  {(r.get('fmt') or '-'):<20} {r['path']}")

        # ★这是个**有用的旁证**，不是装饰：正常 apk 不可能几十 KB，
        #   而 format_type=application/zip 说明它是个包。两者一起看就能判断"是不是被改名了"。
        susp = [r for r in files
                if r['path'].lower().endswith('.apk') and r['size'] < 100 * 1024]
        if susp:
            print(f"\n   ⚠️ {len(susp)} 个 .apk 小于 100KB —— 正常 apk 不会这么小，"
                  f"多半是改了后缀的 zip/文本。取回后先 `file` 一下再当安装包看待。")
        zips = [r for r in files
                if 'zip' in (r.get('fmt') or '') and r['path'].lower().endswith('.apk')]
        if zips:
            print(f"   ℹ️ {len(zips)} 个 .apk 的 format_type 是 application/zip（容器是 zip，后缀是 apk）")
    print(f"\n   下一步：tvbox probe harvest pan pull --url <同一链接> --outdir <目录>"
          f"\n   （下载受 CDN 出口策略限制：本类机房 IP 段会被 412 拒绝，与 IPv6 无关）")


def _fmt_size(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f}{u}' if u == 'B' else f'{n:.1f}{u}'
        n /= 1024
    return f'{n:.1f}TB'


def cmd_pan_pull(a):
    """取料：拿直链 → 下载。**412 的成因是出口网络被 CDN 拒（非 IPv6、非文件坏）**，必须说清楚。"""
    import urllib.request
    import urllib.error
    pwd_id, plat = pan_parse_share_ex(a.url)
    if not pwd_id:
        print('❌ 认不出分享 id')
        sys.exit(2)

    ok, r = _pan_req(f'/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc&uc_param_str=',
                     {'pwd_id': pwd_id, 'passcode': a.passcode or ''}, platform=plat)
    if not ok or r.get('status') != 200:
        print(f'❌ 分享不可用：{r if not ok else r.get("message")}')
        sys.exit(1)
    st = r['data']['stoken']

    # 收齐所有文件（含子目录）
    queue, files = [('0', '')], []
    while queue:
        pdir, prefix = queue.pop(0)
        q = (f'/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc'
             f'&pwd_id={pwd_id}&stoken={quote(st, safe="")}&pdir_fid={pdir}'
             f'&force=0&_page=1&_size=200&_fetch_total=1')
        ok, d = _pan_req(q, platform=plat)
        if not ok:
            print(f'  ⚠️ 目录读取失败：{d}'); continue
        for f in ((d.get('data') or {}).get('list') or []):
            if f.get('dir'):
                queue.append((f['fid'], f"{prefix}{f.get('file_name')}/"))
            else:
                files.append({'name': f"{prefix}{f.get('file_name')}",
                              'fid': f['fid'], 'size': int(f.get('size') or 0),
                              'token': f.get('share_fid_token') or '',
                              'pdir': f.get('pdir_fid') or pdir})
    if a.match:
        files = [f for f in files if a.match in f['name']]
    if not files:
        print('❌ 没有匹配的文件'); sys.exit(1)

    outdir = a.outdir or '.'
    os.makedirs(outdir, exist_ok=True)

    ok_n, fail_n, cdn_reject = 0, 0, 0
    for f in files:
        # 按 pdir 分组取直链（接口一次能要多条同目录的）
        ok, d = _pan_req('/1/clouddrive/file/download?pr=ucpro&fr=pc&uc_param_str=',
                         {'fids': [f['fid']], 'pwd_id': pwd_id, 'stoken': st,
                          'fids_token': [f['token']], 'pdir_fid': f['pdir']}, platform=plat)
        if not ok or not (d.get('data') or []):
            print(f'  ❌ {f["name"]}：取直链失败（{d}）'); fail_n += 1; continue
        url = d['data'][0].get('download_url') or ''
        if not url:
            print(f'  ❌ {f["name"]}：接口未返回 download_url'); fail_n += 1; continue

        dst = os.path.join(outdir, os.path.basename(f['name']))
        try:
            rq = urllib.request.Request(url, headers={'User-Agent': PAN_UA,
                                                     'Referer': (PAN_PLATFORMS.get(plat) or PAN_PLATFORMS['quark'])['referer']})
            with urllib.request.urlopen(rq, timeout=a.timeout) as resp, open(dst, 'wb') as fh:
                shutil.copyfileobj(resp, fh)
            got = os.path.getsize(dst)
            if got != f['size']:
                print(f'  ⚠️ {f["name"]}：大小不符 {got}≠{f["size"]}（可能被截断）')
            print(f'  ✅ {f["name"]}  {_fmt_size(got)}')
            ok_n += 1
        except urllib.error.HTTPError as e:
            if e.code == 412:
                cdn_reject += 1
                print(f'  🚫 {f["name"]}：412 Precondition Failed —— **CDN 拒绝本出口，非文件问题**')
            else:
                print(f'  ❌ {f["name"]}：HTTP {e.code}')
            fail_n += 1
            try:
                os.remove(dst)
            except Exception:
                pass
        except Exception as e:
            print(f'  ❌ {f["name"]}：{type(e).__name__}: {e}'); fail_n += 1

    print(f'\n   取料小结：成功 {ok_n}｜失败 {fail_n}｜保存到 {os.path.abspath(outdir)}')
    if cdn_reject:
        # ★这是**无法判定**，不是"下载失败"。412 的成因是出口网络被拒，重试无用。
        print(f"""
   🚫 其中 {cdn_reject} 个被 CDN 以 412 拒绝。**不是文件坏了，也不是签名过期**：
      · **正确 token 与故意改坏的 token 都是 412**（响应体一模一样）→ 拦截在校验签名**之前**；
      · 同一出口访问 `pan.quark.cn` 200、`image.quark.cn` 200 → 不是断网、不是全站封禁；
      · 响应头 `Server: Tengine` + `Via: ens-cache2.<节点>`，262 字节固定 HTML。
      → 成因是**请求来自机房/云 IP 段，被 CDN 边缘节点策略性拒绝**（与 IPv4/IPv6 无关）。
   处置（按代价从低到高）：
      ① 换一个**家宽/移动网络**出口跑同一条命令（机房 IP 换机房 IP 没用）；
      ② 用夸克 App/网页版手动下载后放进指定目录；
      ③ 让分享者换网盘（直链可公网访问的）。
      **清单已拿到（`pan info`），别把"取不到"当成"里面没东西"。**""")
        sys.exit(0 if ok_n else 1)
    sys.exit(0 if ok_n else 1)


def _load_pan_cookie(plat='quark'):
    """读网盘登录态（按平台分文件/环境变量，同 S209 单一事实源风格）。
    夸克 → .quark_cookie / QUARK_COOKIE；UC → .uc_cookie / UC_COOKIE；
    迅雷 → .xunlei_cookie / XUNLEI_COOKIE；百度 → .baidu_cookie / BAIDU_COOKIE。
    缺则返回 ''，由调用方按需报错（转存必须登录）。"""
    import os
    if plat == 'uc':
        fpath, env = '/root/.codebuddy/.uc_cookie', 'UC_COOKIE'
    elif plat == 'xunlei':
        fpath, env = '/root/.codebuddy/.xunlei_cookie', 'XUNLEI_COOKIE'
    elif plat == 'baidu':
        fpath, env = '/root/.codebuddy/.baidu_cookie', 'BAIDU_COOKIE'
    else:
        fpath, env = '/root/.codebuddy/.quark_cookie', 'QUARK_COOKIE'
    try:
        if os.path.exists(fpath):
            return open(fpath, encoding='utf-8').read().strip()
    except Exception:
        pass
    return (os.environ.get(env) or '').strip()


# ── 迅雷网盘（S218 续·2026-10-24 本轮回补）──────────────────────────────
# 迅雷分享转存接口与夸克/UC **不同构**：独立验证码（X-Captcha-Token）+ 独立鉴权
# （Authorization: x_token）+ 独立列文件（/share + /share/detail）+ 独立转存
# （POST /share/restore，无需 users/me）。x_token 在网页 localStorage，不在 Cookie 头。
# captcha_sign 为动态签名（12 轮 md5 加盐），来自 Gopeed 开源迅雷扩展（适配 1.92.89），2026-10-24 实测有效。
XUNLEI_CLIENT_ID = 'Xqp0kJBXWhwaTpB6'
XUNLEI_CLIENT_VERSION = '1.92.89'
XUNLEI_CAPTCHA_SALTS = [
    'q1FLg', 'OECaw0higFYs7qCdOWvMEe', '1UQvF/CWht+RATnmBnyakvJFHL1jaAb8MRYvdVHQLZ',
    'OV+xWBsbnAehPNmDEUnjcVT', 'bbuvyCYHdJwBOtJ8Cdeg', '9ROgxnyRfQInpATpkchBTZW',
    'vXA2EpRk8', 'oEd', 'QCBQwX/', 'P5NlYzyVtGjJUI/dIzi+SOL+mf0Wl17',
    '7GRbxu7OmpasI841c66J', 'PEVQN9w+A4wRbGoX', 'CJ3yz0fu1kqhPDOyL438W', 'tD75Q',
]

def _xunlei_captcha_sign(ts, device_id):
    """迅雷验证码签名：CLIENT_ID+版本+域名+device_id+时间戳 经 12 轮 md5 加盐。"""
    cur = XUNLEI_CLIENT_ID + XUNLEI_CLIENT_VERSION + 'pan.xunlei.com' + device_id + ts
    for s in XUNLEI_CAPTCHA_SALTS:
        cur = hashlib.md5((cur + s).encode()).hexdigest()
    return '1.' + cur

def _xunlei_device_id(cookie):
    """迅雷 device_id：取 cookie 里 wdi10. 后的 32 位 hex（与 captcha 签名一致）。"""
    import re as _re
    m = _re.search(r'deviceid=wdi10\.([a-f0-9]+)', cookie or '')
    return (m.group(1)[:32] if m else '0' * 32)

def _xunlei_pwd_from_url(url):
    """从迅雷分享链接 query 取访问密码（pwd=）；取不到返回空串。"""
    import urllib.parse as _up
    try:
        return (_up.parse_qs(_up.urlparse(url or '').query).get('pwd') or [''])[0]
    except Exception:
        return ''

def _xunlei_captcha_token(device_id):
    """POST xluser-ssl.xunlei.com/v1/shield/captcha/init 拿验证码令牌。"""
    import time as _t
    ts = str(int(_t.time() * 1000))
    sign = _xunlei_captcha_sign(ts, device_id)
    body = {'client_id': XUNLEI_CLIENT_ID, 'action': 'get:/drive/v1/share',
            'device_id': device_id,
            'meta': {'username': '', 'phone_number': '', 'email': '',
                     'package_name': 'pan.xunlei.com', 'client_version': XUNLEI_CLIENT_VERSION,
                     'captcha_sign': sign, 'timestamp': ts, 'user_id': '0'}}
    hdr = {'User-Agent': PAN_UA, 'Content-Type': 'application/json',
           'X-Client-Id': XUNLEI_CLIENT_ID, 'X-Client-Version': XUNLEI_CLIENT_VERSION,
           'X-Device-Id': device_id, 'Referer': 'https://pan.xunlei.com/'}
    # C1b：底层取数统一走 core.http_get（err_body 保留服务端错误正文）
    try:
        _st, _pl, _rs = http_get('https://xluser-ssl.xunlei.com/v1/shield/captcha/init',
                                 timeout=30, headers=hdr, method='POST',
                                 data=json.dumps(body).encode(), err_body=True)
        if _st == 'ok':
            d = json.loads(_pl)
        elif isinstance(_pl, tuple):
            return False, 'captcha/init HTTP %d %s' % (_pl[0], (_pl[1] or '')[:120])
        else:
            return False, 'captcha/init %s' % _rs
        if not d.get('captcha_token'):
            return False, 'captcha/init 未返回 captcha_token：%s' % str(d)[:120]
        return True, d['captcha_token']
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)

def _xunlei_headers(captcha_token, cookie=None, token=None, device_id=''):
    """迅雷请求头：验证码令牌 + Cookie + Authorization(登录态) + 设备标识。"""
    hdr = {'User-Agent': PAN_UA, 'X-Client-Id': XUNLEI_CLIENT_ID,
           'X-Client-Version': XUNLEI_CLIENT_VERSION, 'X-Device-Id': device_id,
           'X-Captcha-Token': captcha_token, 'Referer': 'https://pan.xunlei.com/'}
    if cookie:
        hdr['Cookie'] = cookie
    if token:
        hdr['Authorization'] = token
    return hdr

def _xunlei_get(url, hdr):
    """迅雷 GET，返回解析后的 json。（C1b：走 core.http_get）"""
    _st, _pl, _rs = http_get(url, timeout=30, headers=hdr)
    if _st != 'ok':
        raise RuntimeError('迅雷 GET 失败: %s' % _rs)
    return json.loads(_pl)

def _xunlei_post(url, hdr, body):
    """迅雷 POST（JSON），返回解析后的 json。（C1b：走 core.http_get）"""
    h = dict(hdr)
    h['Content-Type'] = 'application/json'
    _st, _pl, _rs = http_get(url, timeout=30, headers=h, method='POST',
                             data=json.dumps(body).encode())
    if _st != 'ok':
        raise RuntimeError('迅雷 POST 失败: %s' % _rs)
    return json.loads(_pl)

def _xunlei_token(cookie):
    """迅雷登录态 x_token（JWT）。优先从 cookie 文本解析 x_token=；取不到则回退到
    独立文件 .xunlei_token 或环境变量 XUNLEI_TOKEN
    （迅雷 x_token 在网页 localStorage，不在请求 Cookie 头，故单独存放）。"""
    import os, re as _re
    if cookie:
        m = _re.search(r'(?:^|;\s*)x_token=([^;]+)', cookie)
        if m:
            return m.group(1).strip()
        m = _re.search(r'(?:^|;\s*)(?:authorization|token)=([^;]+)', cookie, _re.I)
        if m:
            return m.group(1).strip()
    p = '/root/.codebuddy/.xunlei_token'
    if os.path.exists(p):
        return open(p, encoding='utf-8').read().strip()
    return (os.environ.get('XUNLEI_TOKEN') or '').strip()


def _xunlei_share_files(share_id, passcode, cookie=None, token=None, device_id=''):
    """递归列出迅雷分享内全部文件。返回 (ok, rows, pass_code_token, root_ids)。
    根层 /share?share_id=&pass_code=；folder 递归 /share/detail?parent_id=&pass_code_token=。
    文件在响应顶层 files（非 data.files）；folder kind=drive#folder。参数必须 urlencode。
    root_ids 为分享顶层条目 id（转存 file_ids 用）。"""
    import urllib.parse as _up
    ok, ct = _xunlei_captcha_token(device_id)
    if not ok:
        return False, ct, '', []
    hdr = _xunlei_headers(ct, cookie=cookie, token=token, device_id=device_id)
    q = _up.urlencode({'share_id': share_id, 'pass_code': passcode or '',
                       'limit': '100', 'space': '', 'thumbnail_size': 'SIZE_MEDIUM', 'scene': 'NORMAL'})
    try:
        d = _xunlei_get('https://api-pan.xunlei.com/drive/v1/share?' + q, hdr)
    except Exception as e:
        return False, '根层列文件失败：%s' % e, '', []
    pct = (d.get('pass_code_token') or '').strip()
    root = d.get('files') or []
    out, root_ids = [], []
    for f in root:
        out.append(f)
        root_ids.append(f.get('id'))
        if f.get('kind') == 'drive#folder':
            _xunlei_walk(share_id, f['id'], pct, hdr, out)
    return True, out, pct, root_ids

def _xunlei_walk(share_id, folder_id, pct, hdr, out, _depth=0):
    """递归展开 folder（/share/detail?parent_id=&pass_code_token=，必须 urlencode）。"""
    import urllib.parse as _up
    if _depth > 20:
        return
    q = _up.urlencode({'share_id': share_id, 'parent_id': folder_id, 'pass_code_token': pct,
                       'limit': '100', 'space': '', 'thumbnail_size': 'SIZE_MEDIUM', 'scene': 'NORMAL'})
    try:
        d = _xunlei_get('https://api-pan.xunlei.com/drive/v1/share/detail?' + q, hdr)
    except Exception:
        return
    for f in (d.get('files') or []):
        out.append(f)
        if f.get('kind') == 'drive#folder':
            _xunlei_walk(share_id, f['id'], pct, hdr, out, _depth + 1)


def _xunlei_restore_status(d):
    """转存任务状态：优先读取 restore_status/phase_type/task_status/status/phase。"""
    for k in ('restore_status', 'phase_type', 'task_status', 'status', 'phase'):
        v = d.get(k)
        if v:
            return str(v).upper()
    return ''

def _xunlei_restore_task_id(d):
    """转存任务 id：优先读取 restore_task_id/task_id/id。"""
    for k in ('restore_task_id', 'task_id', 'id'):
        v = d.get(k)
        if v:
            return str(v)
    return ''

def _xunlei_restore_done(d, file_ids):
    """转存是否已同步完成。"""
    return _xunlei_restore_status(d) in ('RESTORE_COMPLETE', 'PHASE_TYPE_COMPLETE', 'COMPLETE')

def _xunlei_restore_error(d):
    """转存是否已失败。"""
    return _xunlei_restore_status(d) in ('RESTORE_ERROR', 'PHASE_TYPE_ERROR', 'ERROR', 'FAILED')

def _xunlei_wait_restore(task_id, file_ids, hdr, timeout=60):
    """轮询转存任务（GET /drive/v1/tasks/{id}），最多 timeout 秒。返回 (done, msg)。
    参考 Gopeed 开源迅雷扩展 waitRestoreTask（MAX_RESTORE_POLL=120，1s 间隔）。"""
    url = 'https://api-pan.xunlei.com/drive/v1/tasks/' + quote(task_id, safe='')
    for _ in range(timeout):
        try:
            d = _xunlei_get(url, hdr)
        except Exception as e:
            return False, '轮询异常：%s' % e
        st = _xunlei_restore_status(d)
        if st in ('RESTORE_COMPLETE', 'PHASE_TYPE_COMPLETE', 'COMPLETE'):
            return True, '任务完成'
        if st in ('RESTORE_ERROR', 'PHASE_TYPE_ERROR', 'ERROR', 'FAILED'):
            return False, '任务失败：%s' % (d.get('message') or st)
        time.sleep(1)
    return False, '超时未结束'


def cmd_pan_info_xunlei(a, pwd_id):
    """迅雷分享清单（不下载，走 API，IPv4 可成功）。"""
    ck = _load_pan_cookie('xunlei')
    token = _xunlei_token(ck) if ck else ''
    did = _xunlei_device_id(ck) if ck else ''
    passcode = a.passcode or _xunlei_pwd_from_url(a.url)
    ok, rows, _pct, _root = _xunlei_share_files(pwd_id, passcode, cookie=ck, token=token, device_id=did)
    if not ok:
        print(f'❌ 读取迅雷分享失败：{rows}')
        sys.exit(1)
    files = [r for r in rows if r.get('kind') != 'drive#folder']
    print(f"📦 迅雷分享 {pwd_id}｜条目 {len(rows)}（文件 {len(files)}）\n")
    if a.json:
        print(json.dumps({'share_id': pwd_id, 'items': rows}, ensure_ascii=False, indent=1))
        return
    if files:
        print(f"   {'大小':>10}  路径")
        for r in sorted(files, key=lambda x: -int(x.get('size') or 0)):
            print(f"   {_fmt_size(int(r.get('size') or 0)):>10}  {r.get('name')}")
    print(f"\n   下一步：tvbox probe harvest pan save --url https://pan.xunlei.com/s/{pwd_id}")


def cmd_pan_save_xunlei(a, pwd_id):
    """★迅雷网盘分享转存（S218 续）。

    链路：列分享文件（/share + /share/detail，验证码令牌即可，无需登录态）
    → 转存（POST /drive/v1/share/restore，需 Authorization: x_token 登录态）。
    转存 body（下划线风格）：parent_id(空=我的云盘根)、share_id、pass_code_token、
    ancestor_ids=[]、file_ids=[顶层条目id]、specify_parent_id=true。
    异步任务则轮询 GET /drive/v1/tasks/{id}。
    参考 Gopeed 开源迅雷扩展（1.92.89），2026-10-24 实测列文件有效。"""
    import os
    ck = _load_pan_cookie('xunlei')
    if not ck:
        print("""❌ 缺少迅雷网盘 Cookie，无法读取分享/转存。
   迅雷凭证分两部分（实测确认）：
     · Cookie 部分（sessionid/deviceid）→ 写入 `/root/.codebuddy/.xunlei_cookie`
       （登录 pan.xunlei.com 后复制任意请求的 Cookie 头整串；你已给过）
     · **x_token（JWT，Authorization 用）不在 Cookie 头，在网页 localStorage**
       → 写入 `/root/.codebuddy/.xunlei_token`（单行，权限 600）
         或环境变量 `export XUNLEI_TOKEN='eyJ...'`
   获取 x_token：F12 → 应用/Application → 本地存储/Local Storage → 选 pan.xunlei.com
     → 找键 `x_token`（值是一长串 eyJ...）→ 复制完整值
   注：仅复制 Cookie 头（sessionid）不够——转存报 401 帐号认证失败；必须有 x_token。""")
        sys.exit(1)
    did = _xunlei_device_id(ck)

    # 1) 列文件（验证码令牌即可，不需要 x_token）
    passcode = a.passcode or _xunlei_pwd_from_url(a.url)
    ok, rows, pct, root_ids = _xunlei_share_files(pwd_id, passcode, cookie=ck, device_id=did)
    if not ok:
        print(f'❌ 读取迅雷分享失败：{rows}')
        sys.exit(1)
    files = [r for r in rows if r.get('kind') != 'drive#folder']
    if a.match:
        files = [f for f in files if a.match in f.get('name', '')]
    if not files:
        print('❌ 没有可转存的文件'); sys.exit(1)

    # 2) 校验登录态（转存必须有 x_token）
    token = _xunlei_token(ck)
    if not token:
        print(f'✅ 分享可读（共 {len(files)} 个文件），但**缺少 x_token 登录态，无法转存**。\n'
              f'   请按上方提示把 x_token 写入 /root/.codebuddy/.xunlei_token 或环境变量 XUNLEI_TOKEN 后重试。')
        sys.exit(1)

    # 3) 计算要转存的顶层 file_ids（默认整分享；--match 时取其所在根条目）
    root_set = set(root_ids)
    if a.match:
        by_id = {r.get('id'): r for r in rows}
        sel = set()
        for f in files:
            fid = f.get('id')
            if fid in root_set:
                sel.add(fid); continue
            cur = by_id.get(f.get('parent_id'))
            while cur and cur.get('id') not in root_set:
                cur = by_id.get(cur.get('parent_id'))
            if cur:
                sel.add(cur.get('id'))
        file_ids = list(sel)
    else:
        file_ids = list(root_ids)
    if not file_ids:
        print('❌ 没有可转存的顶层条目'); sys.exit(1)

    # 4) 转存：POST /drive/v1/share/restore（需 x_token 登录态）
    ok2, ct = _xunlei_captcha_token(did)
    if not ok2:
        print(f'❌ 获取验证码令牌失败：{ct}'); sys.exit(1)
    hdr = _xunlei_headers(ct, cookie=ck, token=token, device_id=did)
    to_parent = '' if (a.to in ('', '0', None)) else a.to  # 迅雷根目录是空串，非 '0'
    body = {
        'parent_id': to_parent,
        'share_id': pwd_id,
        'pass_code_token': pct,
        'ancestor_ids': [],
        'file_ids': file_ids,
        'specify_parent_id': True,
    }
    try:
        d = _xunlei_post('https://api-pan.xunlei.com/drive/v1/share/restore', hdr, body)
    except urllib.error.HTTPError as e:
        print(f'❌ 转存请求被拒 HTTP {e.code}：{e.read()[:240].decode("utf-8", "replace")}')
        sys.exit(1)
    except Exception as e:
        print(f'❌ 转存请求失败：{type(e).__name__} {e}'); sys.exit(1)

    # 5) 处理响应：同步完成 / 异步任务轮询 / 失败
    if _xunlei_restore_done(d, file_ids):
        print(f'  ✅ 转存完成：{len(file_ids)} 个顶层条目（含 {len(files)} 个文件）'
              f'→ 网盘（parent_id={to_parent or "我的云盘根"}）')
        sys.exit(0)
    task_id = _xunlei_restore_task_id(d)
    if task_id:
        done, msg = _xunlei_wait_restore(task_id, file_ids, hdr)
        if done:
            print(f'  ✅ 转存任务完成：{msg}｜{len(file_ids)} 个顶层条目'
                  f'→ 网盘（parent_id={to_parent or "我的云盘根"}）')
            sys.exit(0)
        print(f'  ❌ 转存任务未完成：{msg}'); sys.exit(1)
    if _xunlei_restore_error(d):
        print(f'  ❌ 转存被拒：{d.get("message") or d}'); sys.exit(1)
    print(f'  ⚠️ 转存已提交（无明确完成状态）：task={task_id}'
          f'｜file_ids={len(file_ids)} 个顶层条目。请稍后到网盘确认。')
    sys.exit(0)


def cmd_pan_save(a):
    """★直送二期（S170 延续）：把分享里的文件**转存到用户自己的夸克网盘**。

    链路：token → detail 取清单 → sharepage/save（**fid 参数必须是数组**，
    逗号串会 500 inner error，2026-10-24 实测）→ task 轮询确认（2=完成 3=失败）。
    save 接口 `require login [guest]`——**必须有你的夸克登录态**，否则被拒。
    与 pull 不同，save **走 API 域（drive-pc.quark.cn），不经过 CDN 下载域**，
    所以本机房出口可达（同 token/detail），唯一卡点是登录态。
    """
    import os
    from collections import defaultdict
    pwd_id, plat = pan_parse_share_ex(a.url)
    if not pwd_id:
        print('❌ 认不出分享 id：需要夸克 https://pan.quark.cn/s/<id> 或 UC https://drive.uc.cn/s/<id> 或 迅雷 https://pan.xunlei.com/s/<id> 或 百度 https://pan.baidu.com/s/<id>')
        sys.exit(2)
    if plat == 'xunlei':
        return cmd_pan_save_xunlei(a, pwd_id)
    if plat == 'baidu':
        return cmd_pan_save_baidu(a, pwd_id)

    ok, r = _pan_req(f'/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc&uc_param_str=',
                     {'pwd_id': pwd_id, 'passcode': a.passcode or ''}, platform=plat)
    if not ok or r.get('status') != 200:
        print(f'❌ 分享不可用：{r if not ok else r.get("message")}')
        sys.exit(1)
    st = r['data']['stoken']

    # 收齐文件（含子目录），记录 fid + share_fid_token + pdir
    queue, files = [('0', '')], []
    while queue:
        pdir, prefix = queue.pop(0)
        q = (f'/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc'
             f'&pwd_id={pwd_id}&stoken={quote(st, safe="")}&pdir_fid={pdir}'
             f'&force=0&_page=1&_size=200&_fetch_total=1')
        ok, d = _pan_req(q, platform=plat)
        if not ok:
            print(f'  ⚠️ 目录读取失败：{d}'); continue
        for f in ((d.get('data') or {}).get('list') or []):
            if f.get('dir'):
                queue.append((f['fid'], f"{prefix}{f.get('file_name')}/"))
            else:
                files.append({'name': f"{prefix}{f.get('file_name')}",
                              'fid': f['fid'],
                              'token': f.get('share_fid_token') or '',
                              'pdir': f.get('pdir_fid') or pdir})

    if a.fid:
        files = [f for f in files if f['fid'] == a.fid]
    if a.match:
        files = [f for f in files if a.match in f['name']]
    if not files:
        print('❌ 没有可转存的文件'); sys.exit(1)

    # ★硬前置：登录态（save 必须，按平台选凭证文件）
    ck = _load_pan_cookie(plat)
    if not ck:
        if plat == 'uc':
            print("""❌ 缺少 UC 网盘登录态，无法转存。
   转存接口 sharepage/save 要求登录。请提供你的 UC 网盘登录 cookie：
     · 方式①：写入文件 `/root/.codebuddy/.uc_cookie`（单行，cookie 原文，权限 600）
     · 方式②：环境变量 `export UC_COOKIE='...'`
   获取方法：登录 drive.uc.cn 后复制任意请求的 Cookie 头**整串**即可——
   实测含 __pus/__puus 组就有效，不需要专门找 ck 字段。""")
        else:
            print("""❌ 缺少夸克登录态，无法转存。
   转存接口 sharepage/save 要求登录。请提供你的夸克网盘登录 cookie：
     · 方式①：写入文件 `/root/.codebuddy/.quark_cookie`（单行，cookie 原文，权限 600）
     · 方式②：环境变量 `export QUARK_COOKIE='...'`
   获取方法：登录 pan.quark.cn 后复制任意请求的 Cookie 头**整串**即可——
   实测含 __pus/__puus/__kps 组就有效，**不需要专门找 ck 字段**。""")
        sys.exit(1)

    to = a.to or '0'
    by_pdir = defaultdict(list)
    for f in files:
        by_pdir[f['pdir']].append(f)

    ok_n, fail_n = 0, 0
    for pdir, grp in by_pdir.items():
        ok, d = _pan_req('/1/clouddrive/share/sharepage/save?pr=ucpro&fr=pc&uc_param_str=',
                         {'pwd_id': pwd_id, 'stoken': st,
                          'fid_list': [x['fid'] for x in grp],          # ★必须是数组，逗号串会 500
                          'fid_token_list': [x['token'] for x in grp],  # ★同上
                          'to_pdir_fid': to, 'pdir_fid': pdir, 'scene': 'link'},
                         platform=plat, cookie=ck)
        if not ok:
            print(f'  ❌ 目录 {pdir} 转存请求失败：{d}'); fail_n += len(grp); continue
        code = d.get('status') or d.get('code')
        if code != 200:
            print(f'  ❌ 目录 {pdir} 转存被拒：code={code} {d.get("message")}'); fail_n += len(grp); continue
        # 轮询任务状态：2=完成 3=失败（实测约 2 秒一轮内完成）
        task_id = (d.get('data') or {}).get('task_id') or ''
        done = False
        if task_id:
            import time as _t
            for _i in range(15):
                _t.sleep(2)
                ok2, t2 = _pan_req(f'/1/clouddrive/task?pr=ucpro&fr=pc&task_id={quote(task_id, safe="")}&retry_index={_i}',
                                   platform=plat, cookie=ck)
                td = (t2.get('data') or {}) if ok2 and isinstance(t2, dict) else {}
                if td.get('status') == 2:
                    done = True; break
                if td.get('status') == 3:
                    break
        else:
            done = True  # 无 task_id 时以 200 受理为准
        if done:
            ok_n += len(grp)
            print(f'  ✅ 转存 {len(grp)} 个文件到网盘（to_pdir_fid={to}，任务已完成）')
        else:
            print(f'  ⚠️ 目录 {pdir} 转存任务未确认完成（task_id={task_id}）'); fail_n += len(grp)

    if plat == 'uc':
        src = '文件 .uc_cookie' if os.path.exists('/root/.codebuddy/.uc_cookie') else '环境变量 UC_COOKIE'
    else:
        src = '文件 .quark_cookie' if os.path.exists('/root/.codebuddy/.quark_cookie') else '环境变量 QUARK_COOKIE'
    print(f'\n   转存小结：成功 {ok_n}｜失败 {fail_n}｜分享 {pwd_id}｜登录态来源：{src}')
    if ok_n:
        sid = _pan_snapshot_save(plat, pwd_id, to, [f['name'] for f in files])
        if sid:
            print(f'   快照已存 id={sid}｜撤销用：tvbox probe harvest pan rm --undo {sid} --platform {plat}')
    sys.exit(0 if ok_n and not fail_n else 1)


def _parse_since(s):
    """--since 时间解析：'30'→最近30分钟；13位数字→毫秒时间戳；否则试 ISO。

    为什么要它：按文件名关键词猜"哪些是刚转存的"很脆弱（漏掉不含关键词的、
    误伤同名的）。接口每个文件都返回 created_at，按时间筛才稳。
    """
    import time as _t
    import datetime as _dt
    s = (s or '').strip()
    if not s:
        return 0
    if s.isdigit():
        n = int(s)
        if n < 100000:          # 小于十万视为"分钟数"
            return int((_t.time() - n * 60) * 1000)
        return n                # 13 位＝毫秒时间戳
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return int(_dt.datetime.strptime(s, fmt).timestamp() * 1000)
        except ValueError:
            continue
    print(f'❌ 认不出时间：{s}（用分钟数如 30，或 2026-10-25 00:00）')
    sys.exit(2)


def _fmt_ts(ms):
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(ms / 1000).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(ms)


def _pan_snapshot_save(plat, pwd_id, to, names):
    """转存快照：记下本次转存了哪些文件（供 --undo 精确撤销）。

    为什么必须有：转存接口只回 task_id，**不回"落盘后的新 fid"**，
    所以事后只能按文件名反推。把当时转存的文件名清单存下来，
    撤销时才能精确匹配，而不是拿关键词瞎猜（本轮 42 个误转就是这么猜的）。
    """
    import time as _t
    import hashlib as _h
    fp = '/root/.codebuddy/.pan_snapshots.json'
    try:
        d = json.load(open(fp, encoding='utf-8')) if os.path.exists(fp) else {'snapshots': []}
    except Exception:
        d = {'snapshots': []}
    sid = _h.sha1(('%s|%s|%s' % (plat, pwd_id, _t.time())).encode('utf-8')).hexdigest()[:10]
    d.setdefault('snapshots', []).append({
        'id': sid, 'time': int(_t.time() * 1000), 'platform': plat,
        'pwd_id': pwd_id, 'to': to, 'names': list(names)})
    d['snapshots'] = d['snapshots'][-20:]      # 只留最近 20 次，防无限膨胀
    try:
        json.dump(d, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.chmod(fp, 0o600)
        return sid
    except Exception:
        return ''


def _pan_snapshot_load(sid):
    """读快照：sid='last' 取最近一次，否则按 id 精确取。"""
    fp = '/root/.codebuddy/.pan_snapshots.json'
    try:
        d = json.load(open(fp, encoding='utf-8'))
    except Exception:
        return None
    snaps = d.get('snapshots') or []
    if not snaps:
        return None
    if sid == 'last':
        return snaps[-1]
    for s in snaps:
        if s.get('id') == sid:
            return s
    return None


def _write_backup_manifest(sel, plat):
    """破坏性操作门禁第 2 步：删前把待删清单落盘备份（可据此核对/恢复）。"""
    import time as _t
    fp = '/root/.codebuddy/pan_rm_backup_%s.json' % _t.strftime('%m%d_%H%M%S')
    try:
        json.dump({'time': int(_t.time() * 1000), 'platform': plat,
                   'items': [{'fid': x['fid'], 'name': x['name'],
                              'dir': x.get('dir'), 'size': x.get('size', 0)} for x in sel]},
                  open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'   💾 删前备份清单已落盘：{fp}')
        return fp
    except Exception as e:
        print(f'   ⚠️ 备份清单写入失败：{e}')
        return ''


def cmd_pan_rm(a):
    """★删除你自己网盘里的文件/目录。默认**预演**（只列将删的），--yes 才真删。

    删除走 `/1/clouddrive/file/delete`（夸克/UC 同构），body 传 filelist（fid 数组）。
    删目录会递归清掉里面所有内容。写操作，需登录态（与 save 同套 cookie）。
    筛选：--fid 精确（逗号分隔，优先）｜--match 多关键词（逗号分隔，任一命中即选）｜都不给则删整个 --pdir 范围。
    """
    import urllib.parse as _up
    plat = a.platform
    ck = _load_pan_cookie(plat)
    if not ck:
        print('❌ 缺少 %s 网盘登录态，无法删除。请提供 cookie 写入 /root/.codebuddy/.%s_cookie（权限600）'
              % ('夸克' if plat == 'quark' else 'UC', plat))
        sys.exit(1)

    # 1) 列出目标范围内全部条目（支持递归进子目录）
    items = []
    queue = [a.pdir or '0']
    while queue:
        pdir = queue.pop(0)
        q = (f'/1/clouddrive/file?pr=ucpro&fr=pc&pdir_fid={_up.quote(pdir, safe="")}'
             f'&_page=1&_size=200&_fetch_total=1&_fetch_sub_count=1')
        ok, d = _pan_req(q, platform=plat, cookie=ck)
        if not ok:
            print(f'  ⚠️ 目录 {pdir} 读取失败：{d}'); continue
        for f in ((d.get('data') or {}).get('list') or []):
            items.append({'fid': f.get('fid'), 'name': f.get('file_name') or '',
                          'dir': bool(f.get('dir')), 'size': int(f.get('size') or 0),
                          'ct': int(f.get('created_at') or 0)})   # ★创建时间，供 --since 用
            if a.recursive and f.get('dir'):
                queue.append(f['fid'])

    # 2) 筛选（--undo 优先：按转存快照精确撤销，不用文件名猜）
    if getattr(a, 'undo', ''):
        snap = _pan_snapshot_load(a.undo)
        if not snap:
            print(f'❌ 找不到快照：{a.undo}（用 last 取最近一次）'); sys.exit(1)
        want = set(snap.get('names') or [])
        sel = [x for x in items if x['name'] in want]      # 精确名匹配，不是子串
        print(f'   ↩️  按快照 {snap.get("id")} 撤销（快照 {len(want)} 个文件名，精确匹配到 {len(sel)} 项）')
    elif a.fid:
        want = set(x for x in a.fid.split(',') if x.strip())
        sel = [x for x in items if x['fid'] in want]
    elif a.match:
        keys = [k.strip() for k in a.match.split(',') if k.strip()]
        sel = [x for x in items if any(k in x['name'] for k in keys)]
    else:
        sel = items  # 没指定则整个 --pdir 范围

    # 2.5) --since：只保留该时间点之后新增的（按转存/创建时间筛，不靠文件名猜）
    if getattr(a, 'since', ''):
        th = _parse_since(a.since)
        before = len(sel)
        sel = [x for x in sel if x['ct'] >= th]
        print(f'   ⏱️  按创建时间筛（{_fmt_ts(th)} 之后）：{before} → {len(sel)} 项')

    if not sel:
        print('❌ 没有匹配到要删的文件'); sys.exit(1)

    # 3) 预演 / 执行
    print(f"🗑️  将删除 {len(sel)} 项（{'【执行】' if a.yes else '【预演】加 --yes 才真删'}）：")
    for x in sel:
        print(f"   [{'目录' if x['dir'] else '文件'}] {x['name']}  (fid={x['fid']})")

    if not a.yes:
        print('\n   预演完毕，未删除任何东西。确认无误后加 --yes 执行。')
        sys.exit(0)

    # 破坏性门禁第 2 步：执行前必须落备份清单（清单→备份→确认→执行，缺一即中止）
    _bk = _write_backup_manifest(sel, plat)
    if not _bk:
        print('❌ 备份清单未生成，按破坏性操作门禁中止删除')
        sys.exit(1)

    fids = [x['fid'] for x in sel]
    ok_n = 0
    for i in range(0, len(fids), 50):
        chunk = fids[i:i + 50]
        ok, d = _pan_req('/1/clouddrive/file/delete?pr=ucpro&fr=pc&uc_param_str=',
                         {'actoin_type': 2, 'exclude_fids': [], 'filelist': chunk},
                         platform=plat, cookie=ck)
        if not ok:
            print(f'  ❌ 删除请求失败：{d}'); continue
        code = d.get('status') or d.get('code')
        if code not in (200, 0, 'ok', None):
            print(f'  ❌ 删除被拒：code={code} {d.get("message")}'); continue
        ok_n += len(chunk)
        print(f'  ✅ 已删除 {len(chunk)} 项')
    print(f'\n   删除小结：成功 {ok_n}｜失败 {len(fids) - ok_n}')
    sys.exit(0 if ok_n == len(fids) else 1)


# ── 百度网盘（S218 续·2026-10-24 本轮回补）──────────────────────────────
# 百度与夸克/UC/迅雷**不同构**：独立 cookie（BDUSS/STOKEN/PANPSC）+
# 独立分享解析（surl→shareid+uk）+ 独立列文件（share/list）+
# 独立转存（share/transfer，fsidlist 明文 JSON 数组，配 shareid+share_uk+bdstoken，
#   无需 RSA 加密——disk-share 前端 JS 无 RSA 公钥，契约来自 BaiduPCS-Go 逆向佐证）。
BAIDU_REF = 'https://pan.baidu.com/'
BAIDU_APP_ID = '250528'

def _baidu_req(url, ck, data=None, timeout=20):
    """百度 GET/POST（form）。data 有则 POST(form-urlencoded)，无则 GET。返回解析 json。"""
    hdr = {'User-Agent': PAN_UA, 'Cookie': ck, 'Referer': BAIDU_REF}
    if data is not None:
        import urllib.parse as _up
        _st, _pl, _rs = http_get(url, timeout=timeout,
                                 headers={**hdr, 'Content-Type': 'application/x-www-form-urlencoded'},
                                 method='POST', data=_up.urlencode(data).encode('utf-8'))
    else:
        _st, _pl, _rs = http_get(url, timeout=timeout, headers=hdr)
    if _st != 'ok':
        raise RuntimeError('百度请求失败: %s' % _rs)
    return json.loads(_pl)

def _baidu_fetch_raw(url, ck, timeout=20):
    """抓原始 HTML/JS（用于从分享页 JS 提取 RSA 公钥）。（C1b：走 core.http_get）"""
    _st, _pl, _rs = http_get(url, timeout=timeout,
                             headers={'User-Agent': PAN_UA, 'Cookie': ck, 'Referer': BAIDU_REF})
    if _st != 'ok':
        raise RuntimeError('百度页面抓取失败: %s' % _rs)
    return _pl

def _baidu_parse_surl(text):
    """从分享文本抠出 surl + 提取码。返回 (surl, pwd)。"""
    import re as _re
    m = _re.search(r'pan\.baidu\.com/s/([0-9a-zA-Z_-]+)', text or '')
    if not m:
        return '', ''
    surl = m.group(1)
    pm = _re.search(r'[?&]pwd=([0-9a-zA-Z]+)', text or '')
    return surl, (pm.group(1) if pm else '')

def _baidu_shareinfo(surl, pwd, ck):
    """从分享页内联 locals 提取 shareid + share_uk（分享者 uk）+ bdstoken。返回 (ok, shareid, share_uk, bdstoken, err)。

    实测确证：百度 `api/shareinfo` 接口已废弃（404），而页面 locals 内联了 shareid/share_uk/bdstoken；
    `uk`（当前登录用户）≠ `share_uk`（分享者），分享列表/转存必须用 share_uk。故直接抓页提取。"""
    import re as _re
    try:
        html = _baidu_fetch_raw('https://pan.baidu.com/s/' + surl + ('?pwd=' + pwd if pwd else ''), ck)
    except Exception as e:
        return False, '', '', '', '抓分享页失败：%s' % e
    sid = _re.search(r'share_uk["\']?\s*[:=]\s*["\']?(\d+)', html)
    uk = _re.search(r'shareid["\']?\s*[:=]\s*["\']?(\d+)', html)
    bt = _re.search(r'bdstoken["\']?\s*[:=]\s*["\']?([0-9a-f]+)', html)
    if not sid or not uk:
        return False, '', '', '', '分享页未含 shareid/share_uk（链接无效、需登录查看，或 cookie 失效）'
    return True, uk.group(1), sid.group(1), (bt.group(1) if bt else ''), ''

def _baidu_verify(surl, pwd, ck, shareid, share_uk):
    """验证提取码，返回 (ok, stoken, err)。百度要求先 verify 拿 stoken，再带 stoken 列文件/转存。"""
    import urllib.parse as _up
    data = {'shareid': shareid, 'uk': share_uk, 'pwd': pwd,
            'clienttype': '0', 'channel': 'chunlei', 'web': '1', 'app_id': BAIDU_APP_ID}
    try:
        d = _baidu_req('https://pan.baidu.com/share/verify', ck, data)
    except Exception as e:
        return False, '', 'share/verify 失败：%s' % e
    if d.get('errno') != 0:
        return False, '', 'share/verify 错误：errno=%s %s（提取码错误，或 cookie 登录态失效需重登重发）' % (d.get('errno'), d.get('errmsg'))
    return True, (d.get('stoken') or ''), ''

def _baidu_bduss_alive(ck):
    """轻量探活：BDUSS 登录态是否有效（仅依赖 BDUSS，不依赖分享专用 STOKEN）。返回 bool。"""
    try:
        d = _baidu_req('https://pan.baidu.com/api/quota?check_free=1&checkexpire=1&web=1', ck)
        return d.get('errno') == 0
    except Exception:
        return False

def _baidu_verify_hint(ck, err_v):
    """verify 失败时的精准指引：区分「服务端风控(出口IP)」与「登录态整体失效」。"""
    if 'errno=-12' in err_v:
        if _baidu_bduss_alive(ck):
            return ('❌ 验证提取码失败（errno=-12，err_msg 为空）：账号 BDUSS 登录态有效、'
                    'quota 正常，但分享验证被百度服务端静默拒绝。\n'
                    '   实测「所有参数/UA/请求方式组合都 -12 且空 err_msg、连无效 shareid 也 -12」，'
                    '而 quota/list 接口正常——这种表现通常是当前运行出口'
                    '（数据中心 / 云服务器 IP）被百度对分享操作风控。\n'
                    '   → 请在你的本机（家庭宽带、已登录该账号的浏览器网络）运行同样命令，'
                    '用此 cookie 重试；或让请求走非机房出口 IP。cookie 本身无需重发。')
        return ('❌ 验证提取码失败（errno=-12）：登录态整体失效。\n'
                '   → 重新登录 pan.baidu.com，F12 复制整串 Cookie 覆盖写入 '
                '/root/.codebuddy/.baidu_cookie（权限 600），再重跑。')
    return '❌ 验证提取码失败：%s' % err_v


def _baidu_resolve_stoken(surl, pwd, ck, shareid, share_uk, force_verify=False):
    """决定列文件/转存所需的 stoken。百度契约：无密码分享直接 list（errno=0）；
    有密码分享 list 返回 -9，需先 verify 拿 stoken。返回 (stoken, err)，err='' 即成功。

    force_verify：转存(save)场景必须为 True——公开分享虽能直接 list，但 transfer
    仍需要 verify 拿到的 stoken。列文件(info)场景用默认 False（公开分享跳过 verify 即可）。

    实测价值：沙箱环境 share/verify 被百度静默风控（errno=-12 空 err_msg），但 share/list
    对公开分享完全可用——故「先无密码试 list」既能让公开分享端到端跑通列文件，
    又能把转存所需的 verify/transfer 风控精准隔离出来。"""
    import urllib.parse as _up
    short = surl[1:] if surl.startswith('1') else surl
    q = {'web': '5', 'app_id': BAIDU_APP_ID, 'desc': '1', 'showempty': '0',
         'page': '1', 'num': '100', 'order': 'time',
         'shorturl': short, 'root': '1', 'view_mode': '1'}
    try:
        d = _baidu_req('https://pan.baidu.com/share/list?' + _up.urlencode(q), ck)
    except Exception as e:
        return '', 'share/list 失败：%s' % e
    errno = d.get('errno')
    if errno == 0:
        if not force_verify:
            return '', ''  # 列文件(info)：公开无密码，无需 stoken
        # 转存(save)：公开分享也需 verify 拿 stoken 供 transfer
        ok_v, stoken, err_v = _baidu_verify(surl, pwd, ck, shareid, share_uk)
        if not ok_v:
            return '', err_v
        return stoken, ''
    if errno == -9:
        if not pwd:
            return '', '此分享需要提取码，请加 ?pwd= 或在命令后加 --passcode <提取码>'
        ok_v, stoken, err_v = _baidu_verify(surl, pwd, ck, shareid, share_uk)
        if not ok_v:
            return '', err_v  # 命令函数会用 hint 美化 -12
        return stoken, ''
    return '', 'share/list 错误：errno=%s %s' % (errno, d.get('errmsg'))


def _baidu_collect(surl, pwd, ck, shareid, share_uk, out, stoken='', dir_fsid='', prefix='', _depth=0):
    """递归列文件，写入 out（含子目录）。返回 err 或 ''。
    实测契约（来自 disk-share 前端 JS getShareList）：GET /share/list?
      web=5&app_id=250528&desc=1&showempty=0&page=N&num=100&order=time
      &shorturl=<surl去开头1>&root=1&view_mode=1[&pwd=..][&stoken=..][&dir=<fs_id>]
    shorturl 必须把 URL 里 surl 开头的 '1' 去掉（JS: _surl.split('').slice(1).join('')）。"""
    import urllib.parse as _up
    if _depth > 20:
        return ''
    short = surl[1:] if surl.startswith('1') else surl
    page = 1
    while True:
        q = {'web': '5', 'app_id': BAIDU_APP_ID, 'desc': '1', 'showempty': '0',
             'page': str(page), 'num': '100', 'order': 'time',
             'shorturl': short, 'root': '1', 'view_mode': '1'}
        if pwd:
            q['pwd'] = pwd
        if stoken:
            q['stoken'] = stoken
        if dir_fsid:
            q['dir'] = dir_fsid
        try:
            d = _baidu_req('https://pan.baidu.com/share/list?' + _up.urlencode(q), ck)
        except Exception as e:
            return 'share/list 失败：%s' % e
        if d.get('errno') != 0:
            return 'share/list 错误：errno=%s %s' % (d.get('errno'), d.get('errmsg'))
        lst = d.get('list') or []
        if not lst:
            break
        for f in lst:
            isdir = (f.get('isdir') == 1)
            name = f.get('server_filename') or f.get('filename') or ''
            full = prefix + name
            out.append({'fs_id': f.get('fs_id'), 'name': full, 'isdir': isdir,
                        'size': int(f.get('size') or 0), 'md5': f.get('md5')})
            if isdir:
                e2 = _baidu_collect(surl, pwd, ck, shareid, share_uk, out, stoken,
                                    dir_fsid=str(f.get('fs_id')), prefix=full + '/', _depth=_depth + 1)
                if e2:
                    return e2
        if not d.get('more') or len(lst) < 100:
            break
        page += 1
    return ''

# 注：百度网盘网页端分享转存（/share/transfer）实测用 fsidlist 明文 JSON 数组（非 RSA filelist），
# disk-share 前端 JS（common-chunks-all）中亦无 RSA 公钥，故无需 RSA 加密与公钥提取。
# 转存见 _baidu_transfer（fsidlist 方式，契约来自 BaiduPCS-Go 逆向 + 网页端佐证）。

def _baidu_transfer(surl, pwd, ck, shareid, share_uk, bdstoken, items, to_path='/'):
    """转存。items=[(fs_id, server_filename), ...]。返回 (ok, err)。
    实测契约（BaiduPCS-Go 逆向 + 网页端 disk-share JS 佐证）：POST /share/transfer，
    fsidlist 为明文 JSON 数组字符串，配 shareid + share_uk + bdstoken，无需 RSA 加密 filelist。"""
    fsidlist = json.dumps([str(f) for f, _ in items], ensure_ascii=False)
    data = {'shareid': shareid, 'uk': share_uk, 'fsidlist': fsidlist,
            'path': to_path, 'bdstoken': bdstoken,
            'clienttype': '0', 'channel': 'chunlei', 'web': '1', 'app_id': BAIDU_APP_ID}
    try:
        d = _baidu_req('https://pan.baidu.com/share/transfer', ck, data)
    except Exception as ex:
        return False, 'share/transfer 失败：%s' % ex
    if d.get('errno') != 0:
        return False, 'share/transfer 错误：errno=%s %s' % (d.get('errno'), d.get('errmsg'))
    return True, ''

def cmd_pan_info_baidu(a, surl):
    """百度分享清单（列文件，不下载）。"""
    ck = _load_pan_cookie('baidu')
    if not ck:
        print('❌ 缺少百度网盘登录态（/root/.codebuddy/.baidu_cookie）。F12 复制 pan.baidu.com 的 Cookie 整串写入该文件（权限600）。')
        sys.exit(1)
    pwd = a.passcode or _baidu_parse_surl(a.url)[1]
    ok, shareid, share_uk, bdstoken, err = _baidu_shareinfo(surl, pwd, ck)
    if not ok:
        print('❌ 读取百度分享失败：%s' % err); sys.exit(1)
    stoken, e = _baidu_resolve_stoken(surl, pwd, ck, shareid, share_uk)
    if e:
        if 'errno=-12' in e:
            print(_baidu_verify_hint(ck, e)); sys.exit(1)
        print('❌ ' + e); sys.exit(1)
    out = []
    e2 = _baidu_collect(surl, pwd, ck, shareid, share_uk, out, stoken=stoken)
    if e2:
        print('❌ 列文件失败：%s' % e2); sys.exit(1)
    files = [r for r in out if not r['isdir']]
    print(f"📦 百度分享 {surl}｜条目 {len(out)}（文件 {len(files)}）\n")
    if files:
        print(f"   {'大小':>10}  路径")
        for r in sorted(files, key=lambda x: -x['size']):
            print(f"   {_fmt_size(r['size']):>10}  {r['name']}")
    print(f"\n   下一步：tvbox probe harvest pan save --url https://pan.baidu.com/s/{surl}")

def cmd_pan_save_baidu(a, surl):
    """★百度网盘分享转存（S218 续）。"""
    ck = _load_pan_cookie('baidu')
    if not ck:
        print('❌ 缺少百度网盘登录态（/root/.codebuddy/.baidu_cookie）。F12 复制 pan.baidu.com 的 Cookie 整串写入该文件（权限600）。')
        sys.exit(1)
    pwd = a.passcode or _baidu_parse_surl(a.url)[1]
    ok, shareid, share_uk, bdstoken, err = _baidu_shareinfo(surl, pwd, ck)
    if not ok:
        print('❌ 读取百度分享失败：%s' % err); sys.exit(1)
    stoken, e = _baidu_resolve_stoken(surl, pwd, ck, shareid, share_uk, force_verify=True)
    if e:
        if 'errno=-12' in e:
            print(_baidu_verify_hint(ck, e)); sys.exit(1)
        print('❌ ' + e); sys.exit(1)
    out = []
    e2 = _baidu_collect(surl, pwd, ck, shareid, share_uk, out, stoken=stoken)
    if e2:
        print('❌ 列文件失败：%s' % e2); sys.exit(1)
    files = [r for r in out if not r['isdir']]
    if a.match:
        files = [f for f in files if a.match in f['name']]
    if not files:
        print('❌ 没有可转存的文件'); sys.exit(1)
    items = [(f['fs_id'], f['name']) for f in files]
    ok2, err2 = _baidu_transfer(surl, pwd, ck, shareid, share_uk, bdstoken, items, to_path=(a.to or '/'))
    if not ok2:
        print(f'❌ 转存失败：{err2}'); sys.exit(1)
    print(f'✅ 转存完成：{len(items)} 个文件 → 网盘（{a.to or "我的网盘根"}）')
    sys.exit(0)


# ══════════════════ MSITEPK2 单文件资源容器（S171） ══════════════════
# ★为什么值得单独支持：**一个 2MB 的 site.pak 装了 78 个资源**（源清单 + m3u + 图标 + 健康数据
#   + 整个前端页面）。对"整包分发"这件事，单文件容器比目录树强得多——一次请求拿全站，
#   不会被按文件粒度 CDN 缓存/拦截，也难被逐文件扒。
#
# ★格式（反编译 `com.moliys.tvbox.SitePak` 得到，非猜测）：
#   magic(8)='MSITEPK2' + count(4 BE)
#   每条 24B：name_len(2BE) + name(UTF-8) + off(4BE 相对 payloadStart) + len(4BE) + iv(16B)
#   头部读完的位置即 payloadStart。数据区 spec:
#     AES-256-CBC(PKCS5) 解密 → zlib inflate（标准头 78 da）
#   ★key 是**独立字段** `SiteKeys.SITE_KEY_HEX`（32 hex 字符×2=64 hex = 32 字节 → AES-256）。
#     `create([B pak, [B key)` 里断言 `key.length == 32`，所以是 **AES-256**，别写成 128。
#
# ⚠️ 应用**自带 key**，所以这是"防扒"不是"加密"：key 就在 dex 里，任何人装了 apk 都能取。
#   对生态的意义：**这类包可以完全打开**，不存在"打不开就算了"。
#   但也说明：**它保护不了数据，只提高门槛**——我们在做分发时别指望这层能挡人。

PAK_MAGIC = b'MSITEPK2'


def _pak_key_from_dex(dex_path):
    """从 classes.dex 里抠 `SiteKeys.SITE_KEY_HEX`。

    ★为什么这么取而不是猜：`SiteKeys` 是**手写 Java 类**（不是 Kotlin），
      只有 `<init>` 没有 `<clinit>`，说明是编译期常量内联——真正的 hex 串
      以普通字符串常量躺在 dex 字符串池里，长度 64 且全 hex。
      **用这个特征找，比 grep "key" 稳**：dex 里 "key" 出现上千次。
    """
    import re as _re
    raw = open(dex_path, 'rb').read()
    cands = _re.findall(rb'(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])', raw)
    # 去重保序
    seen, out = set(), []
    for c in cands:
        s = c.decode()
        if s not in seen:
            seen.add(s); out.append(s)
    return out


def _pak_parse(buf):
    """解析头部，返回 (entries, payloadStart)。**任何一步对不上就单报，不硬猜。**"""
    import struct as _st
    if not buf.startswith(PAK_MAGIC):
        raise ValueError(f'不是 MSITEPK2：头部是 {buf[:8]!r}')
    n = _st.unpack('>I', buf[8:12])[0]
    if not (0 < n <= 100000):
        raise ValueError(f'条目数不合理：{n}（头部可能不是本格式）')
    p, entries = 12, []
    for i in range(n):
        if p + 2 > len(buf):
            raise ValueError(f'第 {i} 条越界（读 name_len）')
        nl = _st.unpack('>H', buf[p:p + 2])[0]; p += 2
        if not (0 < nl <= 4096) or p + nl > len(buf):
            raise ValueError(f'第 {i} 条名字长度异常：{nl}')
        name = buf[p:p + nl].decode('utf-8', 'replace'); p += nl
        if p + 8 + 16 > len(buf):
            raise ValueError(f'第 {i} 条（{name}）越界（读 off/len/iv）')
        off, ln = _st.unpack('>II', buf[p:p + 8]); p += 8
        iv = buf[p:p + 16]; p += 16
        entries.append({'name': name, 'off': off, 'len': ln, 'iv': iv})
    return entries, p


def _pak_read(buf, payload_start, ent, key):
    """取一条：AES-CBC 解密 → zlib inflate。"""
    from Crypto.Cipher import AES as _AES
    seg = buf[payload_start + ent['off']: payload_start + ent['off'] + ent['len']]
    if len(seg) != ent['len']:
        raise ValueError(f"数据不足：要 {ent['len']} 得 {len(seg)}")
    dec = _AES.new(key, _AES.MODE_CBC, ent['iv']).decrypt(seg)
    # PKCS5 去填充：**先判合法再剥**，不合法就原样交给 zlib（让它报真错，别掩盖）
    if dec and 1 <= dec[-1] <= 16 and dec[-dec[-1]:] == bytes([dec[-1]]) * dec[-1]:
        dec = dec[:-dec[-1]]
    return zlib.decompress(dec)


def _pak_try_key(buf, pstart, entries, hexkey):
    """自证：用候选 hex 当 AES-256 key 试解**前两条**记录，zlib 能 inflate 出正长度即采用。

    ★为什么自证而非盲选：真实 dex 里 64-hex 串往往不止一个（sha/uuid/常量），
      原实现靠"恰好 1 个候选"的前提太脆——多候选就静默退化成只列清单。
      改成"谁能真解开就用谁"，把"取密钥"从猜变成验（呼应 S169 测活归因思路）。
    """
    try:
        k = bytes.fromhex(hexkey)
    except ValueError:
        return False
    if len(k) != 32 or not entries:
        return False
    for e in entries[:2]:
        try:
            _pak_read(buf, pstart, e, k)
            return True
        except Exception:
            continue
    return False


def cmd_comb_pak(a):
    """MSITEPK2 解包：列清单 / 全解 / 取单条。"""
    path = a.file
    if not os.path.exists(path):
        print(f'❌ 文件不存在：{path}'); sys.exit(2)
    buf = open(path, 'rb').read()
    try:
        entries, pstart = _pak_parse(buf)
    except ValueError as e:
        print(f'❌ {e}'); sys.exit(1)

    print(f'   容器：{path}  {_fmt_size(len(buf))}')
    print(f'   条目：{len(entries)}   头部 {pstart}B   数据区 {_fmt_size(len(buf) - pstart)}')

    # 找 key：显式 --key 优先；否则从同名目录的 classes.dex 里抠
    # ★--list 不取 key：清单在**明文头部**，不需要解密。取 key 会打日志、也误导人以为要解密。
    key = None
    if not a.list_only:
        if a.key:
            key = bytes.fromhex(a.key)
        else:
            dex = a.dex or os.path.join(os.path.dirname(os.path.abspath(path)), 'classes.dex')
            if os.path.exists(dex):
                cands = _pak_key_from_dex(dex)
                if len(cands) == 1:
                    key = bytes.fromhex(cands[0])
                    print(f'   key：从 {os.path.basename(dex)} 取到（{cands[0][:8]}… 共 32B → AES-256）')
                elif not cands:
                    print(f'   ⚠️ {os.path.basename(dex)} 里没找到 64 hex 的串')
                else:
                    # ★多候选不再静默放弃：逐候选自证，选中能真解开的那个
                    for c in cands:
                        if _pak_try_key(buf, pstart, entries, c):
                            key = bytes.fromhex(c)
                            print(f'   key：{os.path.basename(dex)} 有 {len(cands)} 个候选，'
                                  f'自证选中 {c[:8]}…（其余 {len(cands) - 1} 个解不开）')
                            break
                    else:
                        print(f'   ⚠️ {os.path.basename(dex)} 里有 {len(cands)} 个候选，'
                              f'逐个自证都没解开，用 --key 指定：')
                        [print('      ', c) for c in cands[:8]]

    if a.list_only or key is None:
        print(f"\n   {'大小':>10}  名字")
        for e in sorted(entries, key=lambda x: -x['len']):
            print(f"   {_fmt_size(e['len']):>10}  {e['name']}")
        if key is None:
            print('\n   ℹ️ 没 key，只列了清单。给 --key <64hex> 或把 classes.dex 放在同目录即可解开。')
        return

    if len(key) != 32:
        print(f'❌ key 长度 {len(key)}，本格式固定 32 字节（AES-256）'); sys.exit(1)

    # 单条导出
    if a.get:
        hit = [e for e in entries if e['name'] == a.get]
        if not hit:
            hit = [e for e in entries if a.get in e['name']]
        if not hit:
            print(f'❌ 没有匹配 {a.get} 的条目'); sys.exit(1)
        e = hit[0]
        try:
            data = _pak_read(buf, pstart, e, key)
        except Exception as ex:
            print(f"❌ {e['name']}：{type(ex).__name__}: {ex}"); sys.exit(1)
        # ★--out 优先；其次 --outdir/名；都没有才吐 stdout（兼容老用法）
        if a.out:
            outp = a.out
        elif a.outdir:
            os.makedirs(a.outdir, exist_ok=True)
            outp = os.path.join(a.outdir, e['name'])
        else:
            outp = None
        if outp:
            os.makedirs(os.path.dirname(os.path.abspath(outp)) or '.', exist_ok=True)
            open(outp, 'wb').write(data)
            print(f"   ✅ {e['name']} → {outp}  {_fmt_size(len(data))}")
        else:
            sys.stdout.buffer.write(data)
        return

    # 全解
    outdir = a.outdir or 'pak_out'
    ok, bad = 0, []
    for e in entries:
        try:
            data = _pak_read(buf, pstart, e, key)
        except Exception as ex:
            bad.append((e['name'], f'{type(ex).__name__}: {ex}')); continue
        fp = os.path.join(outdir, e['name'])
        d = os.path.dirname(fp)
        if d:
            os.makedirs(d, exist_ok=True)
        open(fp, 'wb').write(data)
        ok += 1
    total = 0
    for root, _, fs in os.walk(outdir):
        for f in fs:
            total += os.path.getsize(os.path.join(root, f))
    print(f'\n   ✅ 解出 {ok}/{len(entries)} 条，共 {_fmt_size(total)} → {os.path.abspath(outdir)}')
    if bad:
        print(f'   ❌ {len(bad)} 条失败（key 错或条目损坏，逐条列不折叠）：')
        for n, why in bad[:20]:
            print(f'      {n}: {why}')
    # ★解出来和头部声明的总长要对得上；对不上说明有漏，不许报"成功"
    declared = sum(e['len'] for e in entries)
    if ok != len(entries):
        print(f'   声明 {len(entries)} 条 / 实解 {ok} 条 —— 差额 {len(entries) - ok}，'
              f'上面已逐条列出，别当成"全都好了"。')
    sys.exit(0 if not bad else 1)


# ══════════════════ 入口：argparse + dispatch ══════════════════

def build_parser():
    ap = argparse.ArgumentParser(prog='probe.py', description='探源司·上游侦察')
    sub = ap.add_subparsers(dest='mod', required=True)

    m = sub.add_parser('monitor', help='监测上游仓库')
    ms = m.add_subparsers(dest='cmd', required=True)
    c = ms.add_parser('check'); c.add_argument('--repo'); c.add_argument('--all', action='store_true'); c.add_argument('--json', action='store_true')
    d = ms.add_parser('diff'); d.add_argument('--repo')
    fc = ms.add_parser('forecast'); fc.add_argument('--repo'); fc.add_argument('--json', action='store_true')
    ms.add_parser('status')
    ad = ms.add_parser('add'); ad.add_argument('--url', required=True); ad.add_argument('--author'); ad.add_argument('--cadence', default='每周'); ad.add_argument('--branch', default='main'); ad.add_argument('--config-path'); ad.add_argument('--related')
    im = ms.add_parser('import', help='从 seed JSON 批量登记监测目标（离线种子基线）')
    im.add_argument('--file', required=True, help='seed JSON：[{name, urls:[...], kind, seed_baseline}]')
    rm = ms.add_parser('remove', help='撤登记（按 id 或 URL 片段；须 --yes 确认）')
    rm.add_argument('target', help='目标 id 或 URL 片段')
    rm.add_argument('--yes', action='store_true', help='确认执行（缺省为预演 dry-run）')
    pt = ms.add_parser('purge-test', help='清理测试残留目标（S143：test1/tvbox 等自检写入）')
    pt.add_argument('--yes', action='store_true', help='确认执行（缺省为预演）')
    ms.add_parser('due')

    dl = sub.add_parser('deliver', help='大集成消费优选投递（影视/音乐/直播/小说·一期）')
    dl.add_argument('--type', default='影视', help='类目：影视/音乐/直播/小说')
    dl.add_argument('--top', type=int, default=3, help='挑前 N 优')
    dl.add_argument('--json', action='store_true', help='输出 json（上机实测优先）')
    s = sub.add_parser('search', help='搜索更多仓库')
    ss = s.add_subparsers(dest='cmd', required=True)
    sg = ss.add_parser('github'); sg.add_argument('--topic', default='tvbox'); sg.add_argument('--limit', type=int, default=20)
    sge = ss.add_parser('gitee'); sge.add_argument('--keyword', default='tvbox'); sge.add_argument('--limit', type=int, default=20)
    sgl = ss.add_parser('gitlab'); sgl.add_argument('--keyword', default='tvbox'); sgl.add_argument('--limit', type=int, default=20)
    sr = ss.add_parser('rank'); sr.add_argument('--in', dest='input'); sr.add_argument('--why', action='store_true', help='显示前 5 条分数的分项拆解（S143）')
    sv = ss.add_parser('verify'); sv.add_argument('--repo', required=True)
    sc = ss.add_parser('cloud'); sc.add_argument('--limit', type=int, default=30)

    h = sub.add_parser('harvest', help='采集高质量线路')
    hs = h.add_subparsers(dest='cmd', required=True)
    hc = hs.add_parser('scan'); hc.add_argument('--repo', required=True); hc.add_argument('--path'); hc.add_argument('--branch')
    hp = hs.add_parser('pull'); hp.add_argument('--url', required=True); hp.add_argument('--kind', default='')
    hf = hs.add_parser('filter'); hf.add_argument('--in', dest='input')
    he = hs.add_parser('export'); he.add_argument('--in', dest='input'); he.add_argument('--out')
    hd = hs.add_parser('dispatch'); hd.add_argument('--in', dest='input'); hd.add_argument('--dry', action='store_true')
    hcl = hs.add_parser('cloud'); hcl.add_argument('--limit', type=int, default=30)
    hpr = hs.add_parser('promote'); hpr.add_argument('--in', dest='input'); hpr.add_argument('--limit', type=int, default=100)
    hdd = hs.add_parser('dedup', help='★分组唯一性收敛：一条线路只留一条（S146·M-R103）')
    hdd.add_argument('--db', nargs='*', help='读云端库（缺省即成品源库）')
    hdd.add_argument('--in', dest='input', help='读本地 json（数组）')
    hdd.add_argument('--pkg', help='读 TVBox 包的 sites[]')
    hdd.add_argument('--out', help='计划产物路径')
    hdd.add_argument('--timeout', type=int, default=15)
    hdd.add_argument('--apply', action='store_true',
                     help='真删（缺省 dry-run 只出计划）——删除以 results[].success 判真')
    hpn = hs.add_parser('prune'); hpn.add_argument('--url', required=True); hpn.add_argument('--outdir')
    hto = hs.add_parser('addto', help='★把线路加进指定包：6 步一条命令走完（S144·P2）')
    hto.add_argument('--pkg', required=True, help='目标包 json 路径')
    hto.add_argument('--api', help='逗号分隔的 api/url（直接给）')
    hto.add_argument('--from-prod', dest='from_prod', help='成品库 record_id 或 api（逗号分隔）')
    hto.add_argument('--file', help='候选 json 文件（items/sites/lives）')
    hto.add_argument('--name', help='单条时的名称')
    hto.add_argument('--group', help='分组')
    hto.add_argument('--type', default='sites', choices=['sites', 'lives', 'parses'],
                     help='写进哪一段（默认 sites）')
    hto.add_argument('--dry', action='store_true', help='只跑到边界校验，不落盘')
    hto.add_argument('--force', action='store_true', help='强加：跳过可进包/测活拦截（慎用）')
    hto.add_argument('--no-mark', action='store_true', help='不回标成品库')
    # ★ S173：解析接口采集（解析库的上游）。只抽+真验，不入库。
    hpa = hs.add_parser('parse', help='★从别人包里采解析接口 → 真实出流闸门（S173）')
    hpa.add_argument('--url', help='配置 json 直链')
    hpa.add_argument('--repo', help='仓库 URL（自动找配置，取 parses 最多的那份）')
    hpa.add_argument('--branch', help='默认分支（不给则自动探测）')
    hpa.add_argument('--wait', type=int, default=12,
                     help='等 JS 出流秒数（实测 9s 才出 blob，低于 8 会误判死）')
    hrp = hs.add_parser('repack', help='★从已有 TVBox 包抽线路+真测活+判能否跨包平移（S142）')
    hrp.add_argument('--pkg', required=True, help='源包 json 路径（含 sites/lives/parses）')
    hrp.add_argument('--kind', default='', choices=['', 'sites', 'lives', 'parses'],
                     help='只抽某一类（默认三类全抽）')
    hrp.add_argument('--target', help='目标包 json（给了就做 key/api 冲突检查）')
    hrp.add_argument('--only-live', action='store_true', help='只测可平移的（省时间）')
    hrp.add_argument('--out', help='产物路径')
    hrp.add_argument('--workers', type=int, default=12)
    hlj = hs.add_parser('lightjson', help='★全类型：生成 TVBox 轻量可编辑 json（csp必留/相对转绝对/死壳判死）')
    hlj.add_argument('--in', dest='input')
    hlj.add_argument('--pkg', help='★整包 json 路径（全类型模式·推荐）：读 sites[].ext 做完整判据')
    hlj.add_argument('--out')
    hlj.add_argument('--outdir')
    hlj.add_argument('--tag', default='C', help='命名体系标签（默认 C=无jar纯直链；S114：整包带 spider/jar 的应传 --tag A——体系随 jar，不因没用 csp 掉 C）')
    hlj.add_argument('--desc', default='', help='命名描述（默认 直链轻量版）')
    hlj.add_argument('--wallpaper', default='')
    hlj.add_argument('--no-probe', action='store_true', help='跳过真实测活（仅结构转换）')
    hlj.add_argument('--workers', type=int, default=12)
    hlj.add_argument('--base', default='', help='★S113挂载决定论：本地相对地址(./py/*.py、./js/*.js)的候选base主机；给了则先活体探测，200才转绝对保留(§4.2c)，不通不硬编。例:https://clun.top')
    hsj = hs.add_parser('splitjsm', help='★jsm 聚合配置拆包+形态分诊（S147）')
    hsj.add_argument('--pkg', required=True, help='jsm 配置 json 路径（含 sites/lives）')
    hsj.add_argument('--out', help='产物路径（默认 harvest_candidates/splitjsm_MMDD_HHMM.json）')
    hsj.add_argument('--no-probe', action='store_true', help='跳过真实测活（只拆不测）')
    hsj.add_argument('--keep-nonhttp', action='store_true',
                     help='产物里保留非外链条目（默认只留可采集外链，非外链仅留结论）')
    hsj.add_argument('--push', action='store_true', help='同步候选进 DB_HARVEST')
    hsj.add_argument('--workers', type=int, default=12)

    # ★S170 网盘分享取料：网盘是**外部分发**渠道，与 GitHub 仓库并列的料源
    hpi = hs.add_parser('pan', help='★网盘分享取料：摸清单/下载（S170）')
    hps = hpi.add_subparsers(dest='pan_cmd', required=True)
    p1 = hps.add_parser('info', help='列分享清单（只走 API，IPv4 环境也能成功）')
    p1.add_argument('--url', required=True, help='分享链接或整段分享文案')
    p1.add_argument('--passcode', default='', help='提取码（无则留空）')
    p1.add_argument('--limit', type=int, default=500, help='最多遍历条目数')
    p1.add_argument('--json', action='store_true')
    p2 = hps.add_parser('pull', help='下载文件（机房 IP 段可能被 CDN 以 412 拒绝，会如实报）')
    p2.add_argument('--url', required=True)
    p2.add_argument('--passcode', default='')
    p2.add_argument('--outdir', default='.')
    p2.add_argument('--match', default='', help='只下文件名含该子串的')
    p2.add_argument('--timeout', type=int, default=120)
    p3 = hps.add_parser('save', help='★直送二期：转存分享到你的夸克网盘（需登录态 ck）')
    p3.add_argument('--url', required=True)
    p3.add_argument('--passcode', default='')
    p3.add_argument('--fid', default='', help='只转存该 fid（默认全部）')
    p3.add_argument('--match', default='', help='只转存文件名含该子串的')
    p3.add_argument('--to', default='0', help='目标目录 fid，0=根目录')
    p3.add_argument('--json', action='store_true')
    p4 = hps.add_parser('rm', help='★删除你自己网盘里的文件/目录（先预演，--yes 才真删）')
    p4.add_argument('--pdir', default='0', help='从哪个目录开始列，0=根目录')
    p4.add_argument('--recursive', action='store_true', help='递归进入子目录')
    p4.add_argument('--match', default='', help='只删文件名含该子串的')
    p4.add_argument('--fid', default='', help='直接指定 fid（逗号分隔），优先于 --match')
    p4.add_argument('--platform', default='quark', choices=['quark', 'uc'])
    p4.add_argument('--yes', action='store_true', help='确认执行删除（缺省仅预演列出）')
    p4.add_argument('--since', default='', help='只删创建时间晚于该点的：数字=最近N分钟；13位=毫秒时间戳；或 2026-10-25 00:00')
    p4.add_argument('--undo', default='', help='按转存快照精确撤销：last=最近一次转存，或快照 id')

    # ★C8（T6）入口分裂收口：S110 质量评测 + 内容层探查 接入总调度（不删、不降级）
    qy = sub.add_parser('quality', help='S110 单条质量评测（画质/速度/广告/质量分）')
    qy.add_argument('url', help='m3u8 播放地址')
    qb = sub.add_parser('quality-batch', help='S110 批量质量评测并回填成品源库/线路源库')
    qb.add_argument('--db', choices=['成品源库', '线路源库', 'both'], default='成品源库')
    qb.add_argument('--limit', type=int, default=0, help='只测前 N 条（0=全量）')
    qb.add_argument('--only-pass', action='store_true', help='只测真实测活=通过的')
    qb.add_argument('--rounds', type=int, default=2)
    qb.add_argument('--timeout', type=int, default=12)
    qb.add_argument('--workers', type=int, default=6, help='并发数（M-R114 B档上限 6）')
    qb.add_argument('--dry', action='store_true', help='只打印不写库')
    cpb = sub.add_parser('content-probe', help='内容层探查：读原仓库配置逐站探内容层（需 TVBOX_ORIG_DIR）')

    # ★S171 MSITEPK2 单文件资源容器（一个 pak 装 78 个资源）
    pk = sub.add_parser('pak', help='★MSITEPK2 单文件资源容器解包（S171）')
    pk.add_argument('file', help='site.pak 之类')
    pk.add_argument('--key', default='', help='64 hex 的 AES key；缺省从同目录 classes.dex 自动取')
    pk.add_argument('--dex', default='', help='指定 classes.dex 路径（缺省同目录）')
    pk.add_argument('--list', dest='list_only', action='store_true', help='只列清单不解')
    pk.add_argument('--get', default='', help='只取一条（名字或子串），输出到 stdout 或 --out')
    pk.add_argument('--out', default='', help='配合 --get 指定输出文件')
    pk.add_argument('--outdir', default='', help='全解输出目录（缺省 pak_out）')

    dc = sub.add_parser('decrypt', help='★破甲：响应体形态分诊/变换链/AES/XOR图片/m3u8（S148）')
    dc.add_argument('--file', help='本地文件（与 --url 二选一）')
    dc.add_argument('--url', help='直接拉取（与 --file 二选一）')
    dc.add_argument('--content-type', default='', help='已知 content-type（提升分诊准确度）')
    dc.add_argument('--chain', action='store_true', help='把输入当源码，分析变换链信号')
    dc.add_argument('--selftest', action='store_true', help='跑模块自检（向量自证）')
    dc.add_argument('--aes-key', help='AES 密钥（16/24/32 字节字符串）')
    dc.add_argument('--aes-iv', help='AES IV（16 字节字符串）')
    dc.add_argument('--aes-data', help='十六进制密文（缺省用 --file/--url 的原始字节）')
    dc.add_argument('--img', action='store_true', help='按魔数反推 XOR 密钥并还原图片')
    dc.add_argument('--m3u8', action='store_true', help='重写 m3u8 为代理地址')
    dc.add_argument('--base', help='m3u8 原始基地址（--m3u8 需要）')
    dc.add_argument('--proxy', default='', help='代理前缀（缺省 127.0.0.1:9978/proxy?url=）')
    dc.add_argument('--out', help='输出文件（--img 用）')
    dc.add_argument('--timeout', type=int, default=20)

    lc = sub.add_parser('livecheck', help='★真实可用测活（点播拉片/直播拉流，非仅HTTP码）')
    lc.add_argument('url', nargs='?')
    lc.add_argument('--kind', default='auto',
                    choices=['auto', 'vod', 'm3u8', 'py', 'js', 'class'],
                    help='js/class 为 S144 补（此前 SKILL.md 写了 class 而 argparse 没有）')
    lc.add_argument('--batch')
    lc.add_argument('--workers', type=int, default=6)
    lc.add_argument('--json', action='store_true')
    lc.add_argument('--spider', help='★跑一个 py 源的代码：逐接口 5 步验证（S148，非仅"像不像源码"）')

    # 模块 F：写源（S161·P018）——「验完的源留在哪」这件事原来没人管，产物落 /tmp 就蒸发
    fg = sub.add_parser('forge', help='★写源模块：验源 + 过则留档到 sources/ + 盘点（S161）')
    fg_sub = fg.add_subparsers(dest='cmd')
    fg_v = fg_sub.add_parser('verify', help='跑一个 py 源；--save 则「通过」后留档到 sources/')
    fg_v.add_argument('file', help='源文件路径')
    fg_v.add_argument('--save', action='store_true',
                      help='验证通过则复制进 sources/ 并更新索引（未通过一定不留档）')
    fg_sub.add_parser('list', help='列出留档的源 + 最近验证结果 + 索引/磁盘不一致项')
    # S162：源类型实测判定（判据＝响应行为，不是 url 路径——按路径猜 5 条错了 4 条）
    fg_c = fg_sub.add_parser('classify', help='★实测判定源类型（CMS采集/m3u8直链/JSON配置/无法判定）')
    fg_c.add_argument('api', nargs='?', help='单条接口地址')
    fg_c.add_argument('--in', dest='infile', help='批量清单 json：[{"api":...,"name":...}]')
    fg_c.add_argument('--out', help='把结果写到 json（便于后续写库）')
    # S194：借鉴道长 spider-craft 落地——留档审计 + 站点画像
    fg_sub.add_parser('audit', help='★留档审计：七项闸门查 sources/ 账实一致性（索引↔磁盘）')
    fg_p = fg_sub.add_parser('profile', help='★站点画像：43 项特征扫描→防御等级 0-4→推荐探源司命令')
    fg_p.add_argument('url', help='待画像的站点地址')
    # S204：本地 py/js 源清单生成器（借鉴用户 1026 提供的 auto_json.py 分发形态）
    fg_pl = fg_sub.add_parser('packlocal', help='★本地 py/js 目录扫描→验源（py真跑/js静态）→出 auto_config.json')
    fg_pl.add_argument('--dir', dest='dir', help='待扫描目录（递归）')
    fg_pl.add_argument('--out', dest='out', help='输出配置路径（缺省 <目录>/auto_config.json）')
    fg_pl.add_argument('--keep-unverified', dest='keep_unverified', action='store_true',
                       help='「无法判定」也进清单并打标（默认只收「通过」）')

    # 模块 G：壳源侦察与抠链（S196 固化）——第三类料源「播放页壳」的发现与变现
    sh = sub.add_parser('shellcrack', help='★壳源侦察+抠链：CMS站形态分诊→三模板金律正则→urljoin→真拉分片验活→入播放页壳库（S196）')
    sh_sub = sh.add_subparsers(dest='cmd', required=True)
    sh_c = sh_sub.add_parser('crack', help='抠单个播放页壳')
    sh_c.add_argument('page', help='壳页面 URL')
    sh_c.add_argument('--name', help='名称（缺省取 host）')
    sh_c.add_argument('--save', action='store_true', help='结果写入播放页壳库')
    sh_p2 = sh_sub.add_parser('probe', help='★形态分诊：ac=detail 判 直链站/壳/API死，是壳则当场抠')
    sh_p2.add_argument('api', help='CMS api.php/provide/vod 接口')
    sh_p2.add_argument('--name', required=True)
    sh_p2.add_argument('--save', action='store_true')
    sh_s = sh_sub.add_parser('scan', help='★批量：对候选清单逐站分诊+抠链')
    sh_s.add_argument('--in', dest='infile', help='候选清单 json：[[名称,接口],...]（缺省用内置 shellcrack_cands.json）')
    sh_s.add_argument('--save', action='store_true', help='抠出的壳入库')
    sh_sub.add_parser('status', help='壳库 readback（入库后必须回读，不许只看回执）')

    tk = sub.add_parser('token', help='GitHub 凭据：查看状态/配置/写入/清除')
    tk.add_argument('--set', metavar='TOKEN', help='写入 token 文件（0600）')
    tk.add_argument('--clear', action='store_true', help='删除 token 文件')

    lm = sub.add_parser('limits', help='只看 GitHub 剩余配额')
    lm.add_argument('--json', action='store_true')

    bl = sub.add_parser('blacklist', help='★失信源三层黑名单（L1精确黑/L2观察/L3域名预警）')
    bs = bl.add_subparsers(dest='cmd', required=True)
    bls = bs.add_parser('list', help='按层级列出黑名单')
    bls.add_argument('--level', default='', help='L1/L2/L3，缺省全列')
    bld = bs.add_parser('judge', help='按方案A判定某条 api 该进哪层')
    bld.add_argument('api', help='待判定的 api 接口')
    bld.add_argument('--fail', type=int, default=1, help='累计失败次数')
    bld.add_argument('--first', default='', help='首次失败时间 YYYY-MM-DD（判间隔用）')
    bld.add_argument('--last', default='', help='末次失败时间 YYYY-MM-DD（缺省=今天）')
    # ★S169：失败原因必须能传进来，否则"证书类封顶 L2"这条规则没有输入。
    bld.add_argument('--detail', default='',
                     help='失败原因明细（含 cert_expired/cert_invalid/证书 等关键词即判为证书类）')
    blm = bs.add_parser('mark', help='写入黑名单（按指纹，自动判层；须 --yes）')
    blm.add_argument('--api', required=True)
    blm.add_argument('--fails', type=int, default=1)
    blm.add_argument('--last', default='')
    blm.add_argument('--reason', default='单源失效')
    blm.add_argument('--detail', default='',
                     help='实测明细（同 judge --detail；写了"证书"就不会被判死）')
    blm.add_argument('--yes', action='store_true', help='确认写入（缺省为预演）')
    blp = bs.add_parser('pardon', help='★平反误杀：先实测通过才撤销失信记录（S146·活源免疫）')
    blp.add_argument('--api', required=True, help='被误判的 api（按**原 URL** 实测，不按指纹）')
    blp.add_argument('--timeout', type=int, default=15)
    blp.add_argument('--yes', action='store_true', help='确认撤销（缺省为预演）')
    blc = bs.add_parser('check', help='检查某条 api 是否会被拦（正负例验证用）')
    blc.add_argument('api')
    blf = bs.add_parser('fp', help='打印源指纹（调试归一化）')
    blf.add_argument('api')
    bls = bs.add_parser('audit', help='★体检：抽样复验 L1，找出「被判死但其实活着」的误杀（S169）')
    bls.add_argument('--sample', type=int, default=30, help='抽样条数（0=全量，慎用）')
    bls.add_argument('--trials', type=int, default=1, help='首批自动平反的最大条数（默认 1）')
    bls.add_argument('--timeout', type=int, default=15)
    bls.add_argument('--yes', action='store_true', help='确认平反通过复验的条目（缺省仅报告）')

    _sc = sub.add_parser('self-check', help='监测器活体探针（含鉴权模块）')
    # S177 步2：加 --json。**只加输出格式，不改任何一条判定逻辑**。
    # 起因：探源司现在的结果是"给人看的文字"，复刻到其他技能需要"给机器看的数据"。
    # 契约见 references/self_probe_spec.md；消费方按 items[].good 判，不解析文字。
    # ★ 目的地变量刻意叫 `as_json` 不叫 `json`：本模块顶层 `import json`，
    #   `add_argument('--json')` 默认目的名就是 `json`，`getattr(a,'json')` 会拿到**模块对象**
    #   （恒真），于是开关形同虚设、且不报错——"看起来在跑"的死代码。
    #   这不是命名洁癖：命令行开关撞上模块名，是 Python 里最安静的一类失效。
    _sc.add_argument('--json', dest='as_json', action='store_true',
                     help='结构化输出（33 条四元组 JSON），供外部程序调用')
    return ap


AP = build_parser()
DISPATCH = {
    ('monitor', 'check'): cmd_monitor_check,
    ('monitor', 'diff'): cmd_monitor_diff,
    ('monitor', 'forecast'): cmd_monitor_forecast,
    ('monitor', 'status'): cmd_monitor_status,
    ('monitor', 'add'): cmd_monitor_add,
    ('monitor', 'import'): cmd_monitor_import,
    ('monitor', 'remove'): cmd_monitor_remove,
    ('monitor', 'purge-test'): cmd_monitor_purge_test,
    ('monitor', 'due'): cmd_monitor_due,
    ('search', 'github'): cmd_search_github,
    ('search', 'gitee'): cmd_search_gitee,
    ('search', 'gitlab'): cmd_search_gitlab,
    ('search', 'rank'): cmd_search_rank,
    ('search', 'verify'): cmd_search_verify,
    ('search', 'cloud'): cmd_search_cloud,
    ('harvest', 'scan'): cmd_harvest_scan,
    ('harvest', 'pull'): cmd_harvest_pull,
    ('harvest', 'parse'): cmd_harvest_parse,     # S173 解析接口采集
    ('harvest', 'filter'): cmd_harvest_filter,
    ('harvest', 'export'): cmd_harvest_export,
    ('harvest', 'dispatch'): cmd_harvest_dispatch,
    ('harvest', 'cloud'): cmd_harvest_cloud,
    ('harvest', 'promote'): cmd_harvest_promote,
    ('harvest', 'addto'): cmd_harvest_addto,
    ('harvest', 'prune'): cmd_harvest_prune,
    ('harvest', 'dedup'): cmd_harvest_dedup,
    ('harvest', 'repack'): cmd_harvest_repack,
    ('harvest', 'lightjson'): cmd_harvest_lightjson,
    ('decrypt', None): cmd_decrypt,
    ('forge', 'verify'): cmd_forge,
    ('forge', 'classify'): cmd_forge,   # S162
    ('forge', 'list'): cmd_forge,
    ('forge', 'audit'): cmd_forge,      # S194 七项闸门
    ('forge', 'profile'): cmd_forge,    # S194 站点画像
    ('forge', 'packlocal'): cmd_forge,  # S204 本地 py/js 清单生成器
    ('shellcrack', 'crack'): cmd_shellcrack,    # S196 模块 G
    ('shellcrack', 'probe'): cmd_shellcrack,
    ('shellcrack', 'scan'): cmd_shellcrack,
    ('shellcrack', 'status'): cmd_shellcrack,
    ('harvest', 'splitjsm'): cmd_harvest_splitjsm,
    ('harvest', 'pan'): lambda a: (cmd_pan_info(a) if a.pan_cmd == 'info'
                                   else cmd_pan_pull(a) if a.pan_cmd == 'pull'
                                   else cmd_pan_save(a) if a.pan_cmd == 'save'
                                   else cmd_pan_rm(a)),
    ('quality', None): cmd_quality_one,             # C8 S110 单条质量评测
    ('quality-batch', None): cmd_quality_batch_run,  # C8 S110 批量评测回填
    ('content-probe', None): cmd_content_probe_run,  # C8 内容层探查
    'pak': cmd_comb_pak,
    ('livecheck',): cmd_livecheck,
    ('blacklist', 'list'): cmd_bl_list,
    ('blacklist', 'judge'): cmd_bl_judge,
    ('blacklist', 'mark'): cmd_bl_mark,
    ('blacklist', 'pardon'): cmd_bl_pardon,
    ('blacklist', 'check'): cmd_bl_check,
    ('blacklist', 'fp'): cmd_bl_fp,
    ('blacklist', 'audit'): cmd_bl_audit,
}


def dispatch(argv=None):
    """供调度器 tvbox 在同进程内直接调用，共享 core 缓存与并行池。"""
    # ★ 横幅挂这里，不挂 main()。
    #   实测教训（S189b，两次改错）：
    #     ① 第一版挂在 main()，且用 sys.stdout.isatty() 判断 → tvbox 走 dispatch，
    #        main() 根本不是入口，横幅一次都没打印；
    #     ② 第二版去掉 isatty 但仍挂 main() → 只有直跑脚本才显示，
    #        而真实使用里 `tvbox probe ...` 占绝大多数（135 次 vs 直跑 7 次）。
    #   两次都是**没在真实路径上验证**。现在挂到 dispatch —— 直跑和经 tvbox 都必过此处。
    _criteria_banner(argv)
    a = AP.parse_args(argv)
    if a.mod == 'self-check':
        cmd_self_check(a)
        return
    if a.mod == 'token':
        cmd_token(a)
        return
    if a.mod == 'limits':
        cmd_limits(a)
        return
    if a.mod == 'livecheck':
        cmd_livecheck(a)
        return
    if a.mod == 'pak':                    # S171 单文件资源容器（无子命令，直接执行）
        cmd_comb_pak(a)
        return

    if a.mod == 'deliver':                # 大集成消费优选投递（一期）
        sys.path.insert(0, '/root/.codebuddy/skills/probe-hub/scripts')
        from deliver import run as _deliver_run
        _deliver_run(a)
        return

    fn = DISPATCH.get((a.mod, getattr(a, 'cmd', None)))
    if fn is None:
        print(f'未知子命令: {a.mod} {getattr(a, "cmd", "")}')
        sys.exit(2)
    # 子命令可返回退出码；非 0 必须传出去（否则脚本里"解密失败"也叫成功，
    # 与 M-R60「不许把失败折叠成通过」同理）
    rc = fn(a)
    if isinstance(rc, int) and rc != 0:
        sys.exit(rc)


def _criteria_banner(argv=None):
    """跑脚本时把「该守的规矩」直接印在屏幕上。

    ## 为什么放在这里（不是放在 SKILL.md 里）

    实测（40MB 真实会话转录）：
        脚本被执行 219 次 ｜ SKILL.md 被加载 2 次  → 109 : 1
    本技能：脚本跑过 7 次，SKILL.md **一次没被加载**。

    也就是说：**判据写在文档里，而执行走的是脚本，两者从来没接上。**
    活是干完了，但口径是我自拟的、DB_UPSTREAM 没回写、快照没生成。
    这是结构缺陷，不是"忘了触发"——再怎么改触发词都治不好。

    ## 为什么印横幅，而不是硬拦

    硬拦需要判断"该不该拦"，实测四种判据全部证伪（词表 36% 且误伤、
    whoami 相关度不可靠、lanes 是给人看的句子、路径判据只覆盖 28% 且
    拦在"已经知道自己干什么"的地方）。
    横幅不需要判断：**脚本一跑就一定出现**，我照不照做你一眼能看出。

    ## 内容从哪来（防脱钩）

    不写死文案，每次从本技能 SKILL.md 的 §2.1 硬约束段实时读。
    文档改了横幅就跟着改，不会出现"横幅还在说旧规矩"（M-R38 单一事实源）。

    ## 跳过条件

      · 环境变量 PROBE_HUB_NO_BANNER=1 / PROBE_HUB_BANNER=0
      · 本次 argv 带 --json（机读输出，横幅会破坏 JSON 解析）
    """
    if os.environ.get('PROBE_HUB_NO_BANNER') == '1':
        return
    if os.environ.get('PROBE_HUB_BANNER') == '0':
        return
    args = list(argv) if argv is not None else sys.argv[1:]
    if '--json' in args:
        return

    items = []
    try:
        doc = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'SKILL.md')
        text = open(doc, encoding='utf-8').read()
        m = re.search(r'\*\*硬约束\*\*（违反即返工）：(.+?)(?=\n\n|\n>|\n###)', text, re.S)
        if m:
            for line in m.group(1).split('\n'):
                line = line.strip()
                mm = re.match(r'^\d+\.\s*(.+)$', line)
                if mm:
                    items.append(mm.group(1).strip())
    except Exception:
        pass

    if not items:
        return

    print('┌─ 探源司判据（本技能 SKILL.md §2.1 硬约束，跑脚本即须遵守）', file=sys.stderr)
    for i, it in enumerate(items, 1):
        s = it if len(it) <= 46 else it[:44] + '…'
        print(f'│ {i}. {s}', file=sys.stderr)
    print('│ 另：结论须按 §3.5/§3.7/§3.5.3 判据给；上游状态须写 DB_UPSTREAM', file=sys.stderr)
    print('│     跳过横幅：PROBE_HUB_NO_BANNER=1 或带 --json', file=sys.stderr)
    print('└' + '─' * 60, file=sys.stderr)


def main():
    # 横幅由 dispatch() 内部打印（直跑与经 tvbox 都必经那里），此处不再重复
    dispatch()


if __name__ == '__main__':
    main()


