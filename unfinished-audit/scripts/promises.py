#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清账司·会话承诺登记（S136 · M-R79 的「承诺必有账」延伸）

要解决的是最隐蔽的一种「没做完」：**AI 在会话里答应了什么，但那句话只活在上下文里**。
会话一结束上下文就没了，承诺随之蒸发——用户以为有人在做，实际没有任何人/任何账记着它。

做法：
  · 会话中做出「我接下来会做 X」的承诺 → `tvbox close promise "<承诺>" [--due 日期]` 立刻落账
  · 承诺登记后**进入同一本欠账单**（source=promise），与六源采集的欠账同等对待
  · 收尾时若承诺未收口 → verify 判据⑧ 报红（未落账不得验收通过）

为什么不用「自动解析对话」：上下文解析不可靠且不可复现。
登记是**显式动作**——不做这个动作就说明没打算真做，那正好应该被标出来。
"""

import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROMISE_FP = os.path.join(os.path.dirname(HERE), 'session_promises.json')


def _today():
    return datetime.date.today()


def _load():
    """三态：不存在=空；损坏=显式报（不折叠成空——承诺账本丢了当没承诺，是最坏结果）。"""
    try:
        return json.load(open(PROMISE_FP, encoding='utf-8'))
    except FileNotFoundError:
        return {'promises': []}
    except Exception as e:
        raise RuntimeError(f'session_promises.json 损坏：{type(e).__name__}: {e}')


def _save(db):
    tmp = PROMISE_FP + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as _f:      # 同款：不关就 os.replace + 回读，会读到半截
        json.dump(db, _f, ensure_ascii=False, indent=1)
    os.replace(tmp, PROMISE_FP)
    back = json.load(open(PROMISE_FP, encoding='utf-8'))
    if len(back.get('promises', [])) != len(db.get('promises', [])):
        raise RuntimeError('承诺落账回读失败')
    return back


def add(text, due=None, owner='ecosys-conductor'):
    if not (text or '').strip():
        raise RuntimeError('承诺内容不能为空')
    db = _load()
    pid = 'C%03d' % (len(db['promises']) + 1)
    due = due or (_today() + datetime.timedelta(days=3)).isoformat()
    db['promises'].append({
        'id': pid, 'text': text.strip(), 'owner': owner, 'due': due,
        'status': 'open', 'opened': _today().isoformat(), 'closed': '', 'evidence': '',
    })
    _save(db)
    return pid


def as_items():
    """把未收口的承诺转成统一 Item（并入欠账单，与六源同等待遇）。"""
    import collect
    out = []
    try:
        db = _load()
    except RuntimeError:
        return out
    for p in db['promises']:
        if p.get('status') in ('done', 'wontfix'):
            continue
        it = collect._item('promise', p.get('owner', 'ecosys-conductor'), p.get('text', ''),
                           collect._guess_kind(p.get('text', '')),
                           f"{PROMISE_FP} {p.get('id')} 承诺于 {p.get('opened')}")
        it['due'] = p.get('due') or it['due']
        it['owner'] = p.get('owner') or 'ecosys-conductor'
        out.append(it)
    return out


def close(pid, evidence):
    if not (evidence or '').strip():
        raise RuntimeError('收口承诺必须带证据')
    db = _load()
    hit = next((p for p in db['promises'] if p['id'] == pid), None)
    if hit is None:
        raise RuntimeError(f'找不到承诺 {pid}')
    hit['status'] = 'done'
    hit['closed'] = _today().isoformat()
    hit['evidence'] = evidence.strip()
    _save(db)
    return hit


def stale():
    """超期未收口的承诺（供 verify 判红）。"""
    out = []
    try:
        db = _load()
    except RuntimeError as e:
        return [('promise', f'承诺账本无法判定：{e}')]
    for p in db['promises']:
        if p.get('status') in ('done', 'wontfix'):
            continue
        try:
            if datetime.date.fromisoformat(p.get('due', '')) < _today():
                out.append((p.get('id'), f"承诺超期：「{p.get('text', '')[:40]}」（应 {p.get('due')}）"))
        except Exception:
            continue
    return out


if __name__ == '__main__':
    print('未收口承诺：')
    for it in as_items():
        print(f"  · {it['title'][:70]} due {it['due']}")
    print('超期：', stale())
