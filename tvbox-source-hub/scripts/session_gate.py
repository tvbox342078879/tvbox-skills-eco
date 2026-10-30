#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话闸门（S153）——把「开工先查历史」从**提示**升级成**机制**。

背景（本模块存在的全部理由）：
    生态早就有提速入口（`tvbox conductor ask` 会自动预载历史经验、`tvbox brief`
    会列出已知雷区），但这些入口靠 AI 记得去调。实测证明**靠不住**——
    把三条快捷命令打印在 brief 输出里，照样被绕过去手写脚本：
      · 同一件事手写分页拉库比走缓存慢 15.7 倍；
      · 历史已固化的招式（T-I「写后必回读」）没检索就被重新推导一遍。

设计判据（不靠枚举）：
    **所有写库最终都汇流到 library/database/batch_{add,update}_database_records.py
    这两个脚本**。无论调用方是生态命令还是 AI 手搓的 subprocess / heredoc，
    最后一步都是它。在此设卡 = 一处拦住全部写路径；在别处设卡 = 留一个后门。

零依赖约束：本模块不许 import 生态内的任何东西（会被 library/database 反向引用），
只用 stdlib —— 否则会出现循环依赖，且平台层脚本不该依赖业务层技能。

三态（M-R60）：状态文件读不到 / 损坏 → 显式「无法判定」并**拒绝写入**，
绝不当「已开工」放行。宁可挡住一次合法写入，也不放过一个没查历史的写入。
"""
import os
import json
import time

GATE_FILE = '/root/.codebuddy/skills/tvbox-source-hub/.session_gate.json'
GATE_TTL_SEC = 12 * 3600       # 开工登记有效期：防「一次开工、永久放行」（P087②延长：覆盖多轮长会话，本会话实测 7h 被拦）


def _read(p):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write(p, obj):
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def open_session(intent='', _file=None):
    """登记开工。正门只有一处：`tvbox conductor ask`（=登记 + 预载历史经验）。"""
    st = {'opened_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
          'pid': os.getpid(), 'intent': str(intent)[:200],
          'writes': 0, 'bypassed': 0}
    return _write(_file or GATE_FILE, st)


def close_session(_file=None):
    st = _read(_file or GATE_FILE)
    if st is None:
        return False
    st['closed_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    return _write(_file or GATE_FILE, st)


def state(_file=None):
    """闸门三态。ok=None 表示**无法判定**（读不到/损坏），调用方必须拒绝。"""
    p = _file or GATE_FILE
    st = _read(p)
    if st is None:
        return {'ok': None, 'state': 'unknown',
                'reason': f'闸门状态文件缺失或不可读（{p}）——**无法判定**，'
                          f'未开工登记前拒绝写入（M-R60）'}
    try:
        age = time.time() - os.path.getmtime(p)
    except Exception:
        return {'ok': None, 'state': 'unknown',
                'reason': '闸门状态文件无法取时间——**无法判定**，拒绝写入'}
    if age > GATE_TTL_SEC:
        return {'ok': False, 'state': 'expired', 'age': int(age),
                'reason': f'开工登记已过期（距今 {int(age)}s > TTL {GATE_TTL_SEC}s）——'
                          f'换任务就须重新 `tvbox conductor ask`，防止一次开工无限放行'}
    if st.get('closed_at'):
        return {'ok': False, 'state': 'closed',
                'reason': f"本次会话已收尾（{st.get('closed_at')}）——"
                          f'新任务须重新 `tvbox conductor ask`'}
    return {'ok': True, 'state': 'open', 'intent': st.get('intent', ''),
            'opened_at': st.get('opened_at', ''), 'writes': st.get('writes', 0),
            'bypassed': st.get('bypassed', 0)}


def _bump(key, _file=None):
    """计数。**此处不许静默返回**——S153 实测漏洞：

    逃逸舱（TVBOX_GATE_BYPASS=1）在状态文件**不存在**时，旧版这里 `_read` 拿到
    None 就 return 了，于是「绕过去写成功 + 没有任何痕迹」。旁路的价值全在于
    **事后追得到**，写不下痕迹的旁路＝白给的永久后门。
    故：文件不存在时**先建一个旁路档案**再计数，让每次取巧都留下记录。
    """
    p = _file or GATE_FILE
    st = _read(p)
    if st is None:
        st = {'opened_at': None, 'pid': os.getpid(),
              'intent': '(未登记开工·旁路写入)',
              'writes': 0, 'bypassed': 0, 'origin': 'bypass'}
    st[key] = int(st.get(key, 0)) + 1
    ok = _write(p, st)
    if not ok:
        # 计数失败必须显式告警：留痕写不下去的旁路＝追不到责的后门，
        # 与其 silent，不如把「这次绕行没记上」摆到明面。
        print(f'   ⚠️ 闸门计数落盘失败（{p}）——本次旁路**未能留痕**')
    return ok


def enforce(action='写入', _file=None, _bypass=False):
    """写库前调用。返回 (放行?, reason)。

    `_bypass` / 环境变量 TVBOX_GATE_BYPASS=1 是逃逸舱：不否认它有正当用途
    （平台层脚本被其他合规流程调用、紧急修复等），但**一律计数留痕**，
    事后 `tvbox conductor gate` 可查这次会话绕了几次。
    """
    if _bypass or os.environ.get('TVBOX_GATE_BYPASS') == '1':
        _bump('bypassed', _file)
        return True, '闸门旁路（已计数留痕）'
    st = state(_file)
    if st['ok'] is True:
        _bump('writes', _file)
        return True, f"会话已开工登记（{st.get('opened_at','')}）"
    return False, st.get('reason', '未知原因')


def banner(why, action='写入'):
    """被拦截时打印的统一文案（含放行命令，不只是报错）。"""
    return [
        f'🚫 会话闸门拦截：本次{action}被拒绝',
        f'   原因：{why}',
        '   放行方式：tvbox conductor ask "<你的需求>"  （登记开工 + 自动预载历史经验）',
        '   紧急旁路：TVBOX_GATE_BYPASS=1 （会被计数留痕，事后可追）',
    ]
