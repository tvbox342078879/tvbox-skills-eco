#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tvbus —— 生态唯一需求入口（S184 · S178 ②期落地）

用户原话（本文件唯一依据）：
    「做好联动和分工接入」/「文字上闭环了，可执行层肯定还没做到闭环」
    「跨对话/跨任务/跨场景/跨生态的调用机制闭环，合并进账房，做统一调用入口」
    「不靠手动干预，生态自己转一圈并留账」

## 它解决的是哪一步没接上

生态里已经有两层入口，各管一段，中间**恰好断了一次**：

    `tvbox conductor ask "<需求>"`   翻译 + 路由 + 历史呼应 → **只给建议，不动手**
    `tvbox ledger call <动词>`       执行 + 记账           → **要求你已经知道敲哪个动词**

于是每次真实需求的形状是：先问 ask 拿建议，人读完、判断、改成动词，
再敲 ledger call。这一段"人肉翻译 + 复制粘贴"就是没闭环的地方——
用户说的"调用麻烦"，麻烦的全在这里。

`tvbus` 做的是**把这两步焊成一步**，并且只做这一件事：

    tvbus "<随便怎么说>"  →  conductor ask 出候选  →  选一个可执行的  →  ledger call 执行并记账

## 三条纪律

① **不新建路由表**（M-R38）。候选一律来自 `conductor ask` 与 `ledger verbs`，
   本文件里**没有任何一个技能名或命令字面量**。改路由只改那两处，这里自动跟着变。
   （写死一份"需求→命令"表当然更简单，但那会是生态里第三份路由，
     而前两份已经在 S148/S158 各错过一次——错的那份永远是没人记得改的那份。）

② **拿不准就调 `ask` 给人看，不猜着执行**。`ask` 自己会算分差；
   分差不够或命中很弱时，本文件**不选候选**，而是把 ask 的完整输出交给用户。
   理由：执行错一条命令的代价，远大于多看一眼。这里绝不"为了闭环而闭环"。

③ **每一笔都留账**。执行走 `ledger call`（它内部 `_book` 落分录），
   所以"调过去了"和"调成功了"必然有区别——这是 S178 ③期「账驱动」的地基。

## 为什么不叫 `tvbox bus`

`tvbox` 的 ROUTE 是**态射表**（原子动作），本文件是**函数**（组合动作 + 记账）。
混进去会重演 S149 `tvbox size` 那次"路由通了但参数错位"（命令执行了，只是执行错了）。
独立可执行文件名，边界最清楚。

用法：
    tvbus "帮我看看有没有红字"            # 自动选最合适的候选执行并记账
    tvbus "查一下这个技能名下的欠账" probe-hub
    tvbus --dry "…"                       # 只看它会怎么走，不执行
    tvbus --list                          # 看可用动词（= ledger verbs）
    tvbus --self-check                    # 自检：路由/台账/记账三处是否一致
"""
import os
import re
import subprocess
import sys

TVBOX = '/usr/local/bin/tvbox'
SKILLS = '/root/.codebuddy/skills'

# 判定"够不够确定"的阈值。这两个数是**从 ask 自己的输出里读出来的口径**，
# 不是我另定一套：
#   ask 在 `top - second <= 1` 时自己打印「⚠️ 前两名只差 N 分，我拿不准」；
#   ask 在 `top < ROUTE_HIT_MIN + 1` 时打印「命中很弱」。
# 这里用同一套阈值决定"要不要交给人"，避免出现 ask 说拿不准、tvbus 却照跑的荒唐。
MIN_TOP_SCORE = 5          # 低于此分一律不自动执行（只看不跑）
MIN_GAP = 2                # 第一名与第二名分差低于此值一律不自动执行


def _run(cmd, timeout=240):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or ''), (r.stderr or '')
    except subprocess.TimeoutExpired:
        return -1, '', f'超时（{timeout}s）'
    except Exception as e:
        return -1, '', f'{type(e).__name__}: {e}'


def _ask(intent):
    """调 conductor ask，拿它的**原文**（我们只解析其中两段，其余原样展示）。"""
    rc, out, err = _run([TVBOX, 'conductor', 'ask', intent], timeout=180)
    return rc, out + (('\n' + err) if err else '')


def _parse_ask(text):
    """从 ask 输出里抽两样东西：① 路由候选（技能 + 相关度）② 可直接跑的命令行。

    为什么解析文本而不是让 ask 吐 JSON：改 ask 的输出格式会连带影响
    所有读它的人（用户、定时任务、别的技能）。这里宁可解析，也不动公共出口。
    ★ 但解析别人的输出是最脆的一环（M-R37）：所以本函数**必须容错**——
      抽不到就返回空，由调用方退回"把原文给人看"，而不是猜。
    """
    cands, cmds = [], []
    for ln in text.splitlines():
        s = ln.strip()
        # 候选行形如：`① [tvbox-interface] 手上已有接口URL… ← 仅字面沾边`
        if s[:1] in '①②③' and '[' in s and ']' in s:
            try:
                sk = s.split('[', 1)[1].split(']', 1)[0].strip()
                rest = s.split(']', 1)[1]
                score = ''
                # ★ S184：分在**下一行**（`依据：…｜强度 13`），不在候选行本身。
                #   第一版只在候选行里找「强度」，于是所有候选分都读成 0，
                #   入口把所有需求都判成"拿不准"——**永远不敢执行**，
                #   看起来像保守，实际是把入口废掉了。
                #   改为：候选行只认 marked 标志，分从紧随的 `依据：` 行取。
                cands.append({'skill': sk, 'score': '', 'weak': ('仅字面沾边' in rest
                                                                 or '弱信号' in rest),
                              'raw': s, 'pending_score': True})
            except Exception:
                continue
        # 「依据：…｜强度 13」——给最近一条还没拿到分的候选
        if s.startswith('依据') and cands and cands[-1].get('pending_score'):
            m = re.search(r'强度\s*(\d+(?:\.\d+)?)', s)
            if m:
                cands[-1]['score'] = m.group(1)
            if '弱信号' in s:
                cands[-1]['weak'] = True
            cands[-1]['pending_score'] = False
        # 命令行形如：`   [eco-ledger] tvbox ledger verbs`（下一行是 ↳ 说明）
        if s.startswith('[') and ']' in s and ' tvbox ' in s:
            cmd = s.split(']', 1)[1].strip()
            if cmd.startswith('tvbox '):
                cmds.append(cmd)
    return cands, cmds


def _score_of(c):
    try:
        return float(str(c.get('score') or '0').strip('()（） '))
    except Exception:
        return 0.0


def _cmd_to_ledger_call(cmd):
    """把一条 `tvbox xxx` 命令映射成 `ledger call` 的参数。

    ★ 这里**不做语义翻译**，只做动词表里已有的映射（`ledger verbs` 的 skill/cmd 字段）。
      映射不到就返回 None——**返回 None 表示"这条命令只能直跑、不能经总线记账"**，
      调用方会如实告诉用户"这条命令不在总线里，直跑不记账"，而不是偷偷直跑。
      （偷偷直跑 = 用户以为记了账，实际没有；那比不提供入口更坏。）
    """
    try:
        sys.path.insert(0, os.path.join(SKILLS, 'eco-ledger', 'scripts'))
        sys.path.insert(0, os.path.join(SKILLS, 'tvbox-source-hub', 'scripts'))
        from call_bus import VERBS           # noqa: E402  唯一动词表，不复制
    except Exception:
        return None
    if not cmd.startswith('tvbox '):
        return None
    body = cmd[len('tvbox '):].strip()
    # verb 表里 cmd 是"tvbox 之后的部分"，逐条前缀匹配最长者
    best = None
    for verb, spec in VERBS.items():
        sub = str(spec.get('cmd') or '')
        if not sub:
            continue
        if body == sub or body.startswith(sub + ' '):
            if best is None or len(sub) > len(best[1]):
                best = (verb, sub)
    if not best:
        return None
    verb, sub = best
    tail = body[len(sub):].strip()
    return verb, (tail.split() if tail else [])


def _gate_of(intent):
    """返回 (是不是在问账, 点名的技能或'')。

    ★ 抽成纯函数是为了**能被自检直接验**（M-R76：验的必须是生产跑的那一个）。
      这不是为了好看——闸门是本文件里唯一带语义判断的地方，
      也正是本轮四次答错里三次出事的所在。它必须可被单独注入式验证。

    为什么"问账"要两个条件同时成立（既有「欠…」又有疑问/盘点词）：
      只见「欠」会误伤「这个源欠稳定」（在说质量）；
      只见「什么」会误伤一切问句。两个一起才收敛到"在问账目"。
    """
    q = intent or ''
    # ★ S184 自检当场抓到：`eco-ledger 名下的欠账` 被判"不是在问账"——
    #   它没有「什么/哪些/查/看」这类疑问或动作词，只有"名下的欠账"这个**名词短语**。
    #   而这恰恰是最常见的一种问法：**直接说出要的账单名**。
    #   补这一支之前，这类句子会掉进"点名某技能"那一层，被当成"以该技能为主语"，
    #   于是"谁的账单"变成"那个技能能干点什么"——语义整个翻掉。
    #
    #   判据：句子**以账目词结尾或紧邻其后无谓语**（"…的欠账"/"…的待办"）。
    #   为什么用"欠账/欠款/待办/未完成"这组名词而不是任何含"欠"的字：
    #     「这个源欠稳定」里的"欠"是副词（欠=不够），是质量描述，不是账目。
    #     名词形态（欠账/欠款/待办/未完成事项）不会有这个歧义。
    if re.search(r'(的|的?本|的?全|的?总)?\s*(欠账|欠款|待办|未完成|没做完|欠的账)\s*$', q.strip()) \
       or re.search(r'^\S*的\s*(欠账|欠款|待办|未完成)', q.strip()):
        return True, (_skill_named_in_intent(q, _known_skills())
                      if _skill_named_in_intent(q, _known_skills()) != 'unfinished-audit' else '')
    is_ledger_q = bool(re.search(r'(欠|未完成|没做完|待办|没收尾)', q)) and \
                  bool(re.search(r'(什么|哪些|多少|几|啥|吗|呢|进度|状态|查|看|列|盘点|清|捋|谁的)', q))
    named = _skill_named_in_intent(q, _known_skills())
    if named == 'unfinished-audit':
        named = ''      # 点名清账司自己 = 要它干活，不是"以它为宾语"
    return is_ledger_q, named


def _skill_of_verb(verb):
    """从动词表反查：这个动词归哪个技能。查不到就如实说"未知"，不猜。"""
    try:
        sys.path.insert(0, os.path.join(SKILLS, 'eco-ledger', 'scripts'))
        sys.path.insert(0, os.path.join(SKILLS, 'tvbox-source-hub', 'scripts'))
        from call_bus import VERBS           # noqa: E402
        return str((VERBS.get(verb) or {}).get('skill') or '（未登记归口）')
    except Exception:
        return '（动词表读不出）'


def _readonly_verb_of(skill, intent=''):
    """某技能名下**无参数、只读**的动词（动词表 `args == ''` 的那些）。

    为什么只认 `args == ''`：带参数的动词必须由人决定填什么（技能名、文件、sid），
    替用户瞎填 = 执行一条语法对但语义错的命令 —— 比不执行更坏（M-R105 幽灵命令的变体：
    命令确实跑了，只是跑错了对象）。

    ★ S184 自审（`--dry "查一下 probe-hub 名下的欠账"` 当场抓到）：
      第一版**只按动词表顺序取第一个只读动词**，于是"查某技能名下的欠账"
      → probe-hub 名下第一个只读动词 = `harvest`（看**采集候选册**）。
      技能认得对，动词选得完全不相干——用户问欠账，入口去翻源册。
      这不是小事：它会**真跑**（harvest 是只读的，所以不会写坏东西），
      但会记账成一笔"成功"，而用户的问题一个字都没被回答。
      **只读 ≠ 对题**。只读只保证"跑错了也不留伤"，不保证"跑对了"。

    所以：只读是**必要条件**，再加一条——该动词的 `desc` 要与需求**有字面交集**。
    取交集最大者；全都无交集就返回 None（宁可交给人，也不猜）。
    为什么用 desc 字面交集而不是又造一张"需求→动词"表：那会是第三份路由（M-R38），
    而 desc 本来就是给用户看的一句话说明，它和用户说的话用同一批词。
    """
    try:
        sys.path.insert(0, os.path.join(SKILLS, 'eco-ledger', 'scripts'))
        sys.path.insert(0, os.path.join(SKILLS, 'tvbox-source-hub', 'scripts'))
        from call_bus import VERBS           # noqa: E402
    except Exception:
        return None
    ro = []
    for verb, spec in VERBS.items():          # dict 按书写顺序遍历 = 动词表自己的优先级
        if str(spec.get('skill') or '') != skill:
            continue
        if spec.get('cmd') is None:
            continue
        if str(spec.get('args') or '').strip():
            continue
        ro.append((verb, spec.get('desc', '')))
    if not ro:
        return None

    # 无需求文本时（自检等）退回旧口径：取表内第一个（顺序即优先级）
    q = _tokens(intent)
    if not q:
        return ro[0]

    best, best_hit = None, 0
    for verb, desc in ro:
        d = _tokens(f'{verb} {desc}')
        hit = len(q & d)
        if hit > best_hit:                 # 严格大于 → 平分时保留表内靠前的那个
            best, best_hit = (verb, desc), hit
    return best if best_hit > 0 else None


# 中文按 2-gram 切，英文/数字按词切。
# 为什么中文用 2-gram 而不是分词：这里没有词典也不该引词典（引了就是第四份口径）。
# 2-gram 粗糙但**可解释**——命中了哪两个字，人一眼能看出来，
# 比"某模型认为相关"更适合放进一条会**真的去执行**的判据里。
_STOP = set('的了呢吗吧啊是有没把给和与或就在这那个一 　')


def _tokens(s):
    s = (s or '').lower()
    out, buf = set(), ''
    for ch in s:
        if '\u4e00' <= ch <= '\u9fff':
            if buf:
                out.add(buf)
                buf = ''
            # 收集连续汉字串
            out.add(ch)
        else:
            buf += ch
    # 连续汉字串再做 2-gram：上一步只拿到了单字，单字太松（"看"会命中一切）
    runs = re.findall(r'[\u4e00-\u9fff]{2,}', s)
    for r in runs:
        for i in range(len(r) - 1):
            out.add(r[i:i + 2])
    # 单字贡献删掉（太松），保留英文词
    out = {t for t in out if not (len(t) == 1 and '\u4e00' <= t <= '\u9fff')}
    for w in re.findall(r'[a-z][a-z\-]{1,}', s):
        out.add(w)
    return out - _STOP


def _known_skills():
    """生态里已知的技能名（读目录，不写死清单）。

    ★ 为什么不写死：写死的清单会在装了新技能之后**静默过期**——
      症状是"点名了新技能，入口却说没听出你在说哪个技能"，且不报错。
      这正是 M-R105「幽灵命令」的镜像：清单里有的东西已经不在，
      而清单外的东西明明在却没人认得。
    """
    try:
        return [d for d in os.listdir(SKILLS)
                if os.path.isdir(os.path.join(SKILLS, d)) and not d.startswith('.')]
    except Exception:
        return []


def _skill_named_in_intent(intent, known):
    """用户这句话里**明确说出了某个技能名**吗（整词、区分大小写部分）？

    ★ S184 自测抓到的一次"看似可用、实则答错"：
        `tvbus --dry "查一下 eco-ledger 名下的欠账"`
        → ask 判 eco-ledger 强度 **15**（命中「ecoledger」），unfinished-audit 强度 7（命中「欠账」）
        → 入口跟着 15 分走，去了账房 `post`（对账终验）。
      但用户说得很清楚：**eco-ledger 名下的欠账**——他要的是"那个技能欠了谁"，不是"账房体检"。
      ask 把技能名当成了"要办的事"，而技能名在这里其实是**宾语**。

    为什么不能靠分数解决：技能名命中天然高分（整词匹配 15），任何"欠账/查一下"之类的
    意图词都超不过它。**分数排序在这里系统性失准**，不是阈值调一调能好的。
    所以单独加一道闸：只要句子里出现了某个已知技能名，就**跳过分数**，
    直接在该技能名下找动词；该技能名下没有对得上的动词就如实说"需要你指定动作"。
    宁可多问一句，也不要把"谁的欠账"办成"账房体检"。

    判"说出"的口径：技能名按**连字符/下划线切开的片段全都要在句子里**。
    （`eco-ledger` → 要求同时出现 `eco` 和 `ledger`；这样可以容忍用户
     写成 `ecoledger` / `eco ledger`，又不会被 `eco` 单独命中而误判。）
    """
    s = (intent or '').lower()
    hits = []
    for sk in known:
        parts = [p for p in re.split(r'[-_\s]+', sk.lower()) if len(p) >= 3]
        if parts and all(p in s for p in parts):
            hits.append(sk)
    # 多个都命中时取最长的（`tvbox-inspect` 与 `tvbox-inspect-x` 同时命中时选更具体的）
    hits.sort(key=len, reverse=True)
    return hits[0] if hits else ''


def main(argv):
    args = list(argv)
    dry = False
    if '--list' in args:
        # ★ S184 实测：`tvbus --list` 原样返回 rc 却**一个字都不打印**。
        #   根因是 `_run(...)[0]` —— 只取了退出码，把 stdout 丢了。
        #   于是"看可用动词"这个最基本的自解释动作**静默空转**：
        #   用户敲完，屏幕上什么都没有，退出码还 0（看着像成功了）。
        #   这类 bug 特别毒：命令没报错、退出码正常、就是没效果——
        #   与"查了但没查到"分不开，也与"根本没查"分不开（M-R57 三态在这里全塌）。
        rc, out, err = _run([TVBOX, 'ledger', 'verbs'])
        if out:
            print(out.rstrip())
        if err:
            print(err.rstrip(), file=sys.stderr)
        return rc
    if '--self-check' in args:
        return _self_check()
    if '--dry' in args:
        dry = True
        args.remove('--dry')
    if not args:
        print('用法: tvbus "<随便怎么说>"        # 翻译→路由→执行→记账，一步')
        print('      tvbus --dry "<需求>"          # 只看它会怎么走')
        print('      tvbus --list                 # 可用动词')
        print('      tvbus --self-check           # 自检三处一致')
        return 2

    intent = ' '.join(args).strip()
    print(f'🚏 tvbus｜需求：{intent}')
    print('─' * 66)

    rc, text = _ask(intent)
    cands, cmds = _parse_ask(text)
    if not cands and not cmds:
        # 抽不到 → 不猜。把 ask 的原文完整交给用户（这是三态里的"无法判定"）
        print('⚠️ 没能从需求管家的输出里解析出候选（可能是它自己也在说"没听懂"）。')
        print('   下面是它的原始输出，据此再说一句更具体的话即可：\n')
        print(text)
        return 0

    top = _score_of(cands[0]) if cands else 0.0
    second = _score_of(cands[1]) if len(cands) > 1 else 0.0
    gap = top - second
    weak = bool(cands and cands[0].get('weak'))

    # ══ 闸门：**先看这句在问什么，再看它点了谁的名** ═══════════════════════
    #
    #   这一段的来历（四次实测，每次都是"入口看起来能用、结果答错"）：
    #
    #   ① `体检一下生态，把红字都列出来` → ask 技能级 tvbox-inspect 13 分领先 11，
    #      但命令级给了 `inspect balance`（看强弱）→ 第一版跟着命令走，答"短板"。
    #      修：技能级确定时以归口动词为准，命令级只在技能级不确定时才代表信息量。
    #
    #   ② `现在还欠着什么` → ask 主分 3（排序第一就是清账司），但它被标成 `weak`，
    #      于是入口判"拿不准"，把 ask 原文整段丢给用户。
    #      根因：ask 的整词命中要字面完全相等，用户说「还欠**着**什么」、
    #      词表写「还欠什么」，差一个语气助词就是"弱信号"。
    #      这不能靠补词解决——中文口语必然带助词（着/了/呢/吧），补不完。
    #
    #   ③ `查一下 eco-ledger 名下的欠账` → ask 给 eco-ledger 15 分（技能名整词命中）、
    #      清账司 7 分，于是"谁的欠账"被办成"账房体检"。
    #      根因：技能名在这句里是**宾语**，ask 却当成了"要办的事"。
    #
    #   ④ `probe-hub 名下还欠着哪些没做完的` → 点名了、也在问欠账，两个信号都有。
    #
    #   这四次的共同点：**句子结构决定该谁办，分数只看词重合，看不出结构。**
    #   所以这里按结构分层判，而不是给分数调阈值——阈值调不出一双看得懂语法的眼睛。
    #
    #   分层（从具体到笼统，先命中先返回）：
    #     层1 问欠账 + 点名某技能  → 那个技能的欠账（清账司 `mine <技能>`，带参数，不自动跑）
    #     层2 问欠账（未点名）      → 全生态总账（清账司 `backlog`）
    #     层3 点名某技能（不问账）  → 该技能名下对得上的动作
    #     层4 其余                  → 原有分数逻辑
    is_ledger_q, named = _gate_of(intent)

    if is_ledger_q and named:
        # 层1：谁的欠账。**带参数的动词不自动执行**（`mine <技能名>` 要人确认目标），
        # 所以这里只把命令摆出来，不替用户跑——跑错的话，记的是一笔别人的账。
        print(f'📌 这句在问 `{named}` 名下的欠账——清账司按 owner 反查，不是查总账')
        print(f'   要执行：tvbox ledger call mine {named}')
        print(f'   （带技能名参数的动词不自动跑：跑错对象会把别人的账记到你头上）')
        if dry:
            print('（--dry：未执行）')
        return 0

    if is_ledger_q:
        # 层2：总账。这是自转圈第三环（接单前先看清账）的人工入口。
        print('📌 这句在问「还欠着什么」——归清账司（自转圈第三环：接单前先看清账）')
        v = _readonly_verb_of('unfinished-audit', intent)
        if v:
            print(f'🎯 路由：unfinished-audit → 归口动词 `{v[0]}`（{v[1][:28]}）')
            call_argv = [TVBOX, 'ledger', 'call', v[0]]
            print(f'🔗 经总线执行：{" ".join(call_argv[1:])}')
            print('─' * 66)
            if dry:
                print('（--dry：未执行）')
                return 0
            rc_d, out_d, err_d = _run(call_argv, timeout=300)
            if out_d:
                print(out_d.rstrip())
            if err_d:
                print(err_d.rstrip(), file=sys.stderr)
            return rc_d
        print('❌ 清账司名下没有无参只读动作——这是入口自身的缺口（不是你的话有问题）')
        return 1

    if named:
        # 层3：点名了技能。技能名在这里是**宾语**，不是要办的事。
        print(f'📌 你点名了 `{named}`——按点名走，不看分数排序（技能名这里是宾语，不是动词）')
        v = _readonly_verb_of(named, intent)
        if not v:
            print(f'   `{named}` 名下没有与这句话对得上的无参只读动作。')
            print(f'   看它有哪些动作：tvbox ledger verbs')
            return 0
        print(f'🎯 路由：{named} → 归口动词 `{v[0]}`（{v[1][:30]}）')
        call_argv = [TVBOX, 'ledger', 'call', v[0]]
        print(f'🔗 经总线执行：{" ".join(call_argv[1:])}')
        print('─' * 66)
        if dry:
            print('（--dry：未执行）')
            return 0
        rc_n, out_n, err_n = _run(call_argv, timeout=300)
        if out_n:
            print(out_n.rstrip())
        if err_n:
            print(err_n.rstrip(), file=sys.stderr)
        return rc_n

    # ★ S184 实测第二坑：**技能级分差为 0 时不该直接判"拿不准"**。
    #   实测 `体检一下生态，把红字都列出来`：① tvbox-inspect 强度 13，
    #   ② probe-hub 强度 3 —— 分差 10，明显是前者。但第一版算出的 gap 是 0.0，
    #   根因：`top` 被后面的"命令级覆盖"逻辑改成了 cmd_best（0）——
    #   于是"没有任何可跑命令"这个事实，被误当成"技能候选也不可信"。
    #   两件事必须分开判：
    #     · 命令级没分 → 只是"没有现成命令"，不代表技能没认出来；
    #     · 技能级有高分且领先 → 应当走归口动词（下面 via_skill 就是为此写的）。
    #   故先算技能级的确定度，再单独算命令级的确定度，任一成立即可执行。
    skill_confident = (not weak) and top >= MIN_TOP_SCORE and gap >= MIN_GAP

    # 从原始输出里抽命令行自带的相关度（ask 把分数写在 ↳ 那一行）
    cmd_best = 0.0
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith('↳') and '相关度' in s:
            m = re.search(r'相关度\s*(\d+(?:\.\d+)?)', s)
            if m:
                cmd_best = max(cmd_best, float(m.group(1)))
    cmd_confident = cmd_best >= MIN_TOP_SCORE

    if not skill_confident and not cmd_confident:
        print(f'🤔 拿不准（技能首选 {top} 分 / 次选 {second} 分 / 差 {gap} 分'
              f'{f" / 命令最高 {cmd_best} 分" if cmd_best else ""}'
              f'{" / 首选是弱信号" if weak else ""}）——**不自动执行**。')
        print('   需求管家的完整判断如下，看完再说一句更具体的即可：\n')
        print(text)
        return 0
    if cmd_confident and not skill_confident:
        top, gap = cmd_best, cmd_best

    # ★ S184 自审（`--dry` 连跑两次当场发现的两处，都是同一条病：判据写了两遍）：
    #
    #   病灶一：连打两行 `🎯 路由`。上面先打"技能几分"，下面归口分支又打"几分 + 走哪个动词"。
    #   病灶二：路由行**在决定走哪条路之前**就打出来了，于是它是猜的，不是结论。
    #
    #   修法：**先定后说**。先把 target / via_skill 两个出口都定下来，再打一行路由。
    #   这不是显示问题——先打后算的日志，人读到的是"它将要去哪"，出事时无法据它复盘。
    #
    #   出口共三个，必须先各自定下来：
    #     A 归口动词（技能级确定）  B 命令级命中（能进总线）  C 都进不去（如实说，不跑）
    target = None
    for c in cmds:
        m = _cmd_to_ledger_call(c)
        if m:
            target = (c, m)
            break

    via_skill = None
    if cands and skill_confident:
        # ★ 需求文本要一起传进去：只读动词有好几个，光靠"表内顺序"会选错。
        #   实测 `查一下 probe-hub 名下的欠账` → probe-hub 名下第一个只读动词是
        #   `harvest`（看**采集候选册**），于是"查欠账"变成"翻源册"。
        #   见 `_readonly_verb_of` 里那段说明。
        via_skill = _readonly_verb_of(cands[0]['skill'], intent)
        if via_skill:
            target = None      # 技能级已确定：命令级那条只是字面噪声，让位给归口动词

    if target:
        # ★ S184：这里曾打 `{cands[0]["skill"]}`（技能榜第一名）。
        #   实测 `看看哪个技能平衡性最差` → 走的其实是 `balance`（tvbox-inspect 的动词），
        #   但技能榜第一名是 ecosys-conductor（"技能/平衡性"这些词更像总指挥的话），
        #   于是路由行写「路由：ecosys-conductor → 命令级命中」，**跟实际執行的是两个技能**。
        #   一条日志要是连"到底谁在干活"都指错，复盘时只会把人带偏（比不记还坏）。
        #   改成**从动词表反查归口技能**——真正被执行的那个技能，唯一的答案。
        _owner_sk = _skill_of_verb(target[1][0])
        print(f'🎯 路由：{_owner_sk} → 动词 `{target[1][0]}`（命令级命中，'
              f'ask 首推 {cands[0]["skill"]} {top} 分，但那条命令不进总线）')
    elif via_skill:
        print(f'🎯 路由：{cands[0]["skill"]}（技能 {top} 分，领先 {gap} 分）'
              f'→ 归口动词 `{via_skill[0]}`')
    else:
        # 技能认出来了，但它名下没有一个"既不收参数、又和这句话对得上"的动词。
        # **不猜**：这种情形下任何自动执行都是拿用户的账去试。
        print(f'🎯 路由：{cands[0]["skill"]}（技能 {top} 分，领先 {gap} 分）'
              f'→ 但它名下没有与这句话对得上的无参只读动词')
        print(f'   该找 {cands[0]["skill"]}，只是这一步需要你指定具体动作。')
        print(f'   看它有哪些动作：tvbox ledger verbs')
        return 0

    # ── 出口 A：走归口动词 ────────────────────────────────────────
    if not target and via_skill:
        verb, _desc = via_skill
        call_argv = [TVBOX, 'ledger', 'call', verb]
        print(f'🔗 经总线执行：{" ".join(call_argv[1:])}')
        print('─' * 66)
        if dry:
            print('（--dry：未执行）')
            return 0
        rc3, out3, err3 = _run(call_argv, timeout=300)
        if out3:
            print(out3.rstrip())
        if err3:
            print(err3.rstrip(), file=sys.stderr)
        return rc3

    # ── 出口 C：命令段给了命令，但一条都进不了总线 ──────────────
    if not target:
        print(f'   可跑的命令有 {len(cmds)} 条，但都不在统一总线的动词表里——')
        print('   **直跑不会记账**，所以这里不替你跑。要记账请用 `tvbox ledger call <动词>`；')
        print('   下面是需求管家给的可直接执行串：')
        for c in cmds[:6]:
            print(f'     · {c}')
        return 0

    cmd, (verb, rest) = target
    call_argv = [TVBOX, 'ledger', 'call', verb] + rest
    print(f'🔗 经总线执行：{" ".join(call_argv[1:])}')
    print('─' * 66)
    if dry:
        print('（--dry：未执行）')
        return 0
    rc2, out2, err2 = _run(call_argv, timeout=300)
    if out2:
        print(out2.rstrip())
    if err2:
        print(err2.rstrip(), file=sys.stderr)
    return rc2


def _self_check():
    """自检：本文件的三个假设是否仍成立。

    ★ 为什么要自检而不是"写完跑一次就行"：本文件全靠**别人的输出格式**活着
      （ask 的候选行、verbs 的 cmd 字段）。那两处一改，本文件会静默变成哑巴——
      抽不到候选 → 走"没解析出来"分支 → 打印原文 → 看起来一切正常。
      这正是 M-R37「解析别人的输出是最脆的一环」说的情况。
    """
    print('🔎 tvbus 自检')
    ok = True

    rc, text = _ask('帮我看看有没有红字')
    cands, cmds = _parse_ask(text)
    print(f'  ① 解析需求管家输出：rc={rc} 候选 {len(cands)} 条 / 命令 {len(cmds)} 条')
    if not cands and not cmds:
        print('     ❌ 一条都解析不出来——格式可能变了，本入口会恒走"无法判定"分支')
        ok = False

    n_map = 0
    for c in cmds:
        if _cmd_to_ledger_call(c):
            n_map += 1
    print(f'  ② 总线映射：{len(cmds)} 条命令中 {n_map} 条能进总线')
    if cmds and n_map == 0:
        print('     ⚠️ 全部映射不到——入口只能干看不能执行（不算错，但要知情）')

    # ③ 注入判红：故意改坏一个候选行的格式，必须解析不出"看似有效"的候选
    bad = '① [tvbox-interface 手上已有接口URL ← 仅字面沾边'    # 少了右括号
    bc, _ = _parse_ask(bad)
    print(f'  ③ 注入（去掉右括号）：解析出 {len(bc)} 条 → '
          + ('✅ 未被骗' if not bc else '❌ 把坏格式当成了候选'))
    if bc:
        ok = False

    # ══ ④ 决策自检（S184 补）══════════════════════════════════════
    # ①②③ 只验"解析得对不对"，不验"选得对不对"。而 S184 实测出错的全是后者：
    #   选错动词（watch↔balance）、选错出口（欠账→harvest）、打两行路由。
    #   格式没坏、日志也不报错，**结果就是错的**——只验解析的自检对这类错完全失明。
    # 所以这里给动词选择加探针：**注入式**，不依赖真环境的分数波动。
    print('  ④ 动词选择（判据本身，注入式）:')
    cases = [
        # (技能, 需求, 期望动词, 说明)
        ('tvbox-inspect', '体检一下生态，把红字都列出来', 'watch', '看红字 vs 看短板'),
        ('tvbox-inspect', '看看哪个技能平衡性最差、短板在哪', 'balance', '反向：该选 balance'),
        ('probe-hub', '查一下 probe-hub 名下的欠账', None, '不对题时必须拒选'),
        ('probe-hub', '去看看采集候选册里有什么', 'harvest', '正向：该选 harvest'),
    ]
    for sk, q, want, why in cases:
        got = _readonly_verb_of(sk, q)
        gv = got[0] if got else None
        good = (gv == want)
        print(f'     {sk:<15} {q[:22]:<24} → {str(gv):<9} 期望 {str(want):<9} '
              f'{"✅" if good else "❌"}　({why})')
        if not good:
            ok = False

    # ══ ⑤ 闸门自检（S184 补）══════════════════════════════════════
    # ④ 只验"给某个技能 + 某句话，选中哪个动词"，验不到**该不该归这个技能**。
    # 本轮四个坑里有三个是后者：
    #   · 「还欠着什么」被判 weak 而整句交回给人（该归清账司）
    #   · 「eco-ledger 名下的欠账」被当成"账房体检"（技能名是宾语）
    #   · 路由行写错技能（显示的是 ask 第一名，执行的却是别家的动词）
    # 抽出来做成小而纯的函数再验——**验的就是生产用的那一个**（M-R76），不复制判据。
    print('  ⑤ 闸门（这句话该归谁，注入式）:')
    gate_cases = [
        ('现在还欠着什么', True, '', '问欠账、未点名 → 清账司总账'),
        ('还欠着哪些活', True, '', '助词变体（"欠着"）也必须认'),
        ('eco-ledger 名下的欠账', True, 'eco-ledger', '点名了 → 反查那个技能'),
        ('probe-hub 名下还欠着哪些', True, 'probe-hub', '点名 + 问账，两层都要中'),
        ('体检一下生态，把红字都列出来', False, '', '不是问账，别抢清账司的活'),
        ('看看哪个技能平衡性最差', False, '', '同上'),
    ]
    for q, want_is_ledger, want_named, why in gate_cases:
        got_l, got_n = _gate_of(q)
        good = (got_l == want_is_ledger) and (got_n == want_named)
        print(f'     {q[:24]:<26} 问账={str(got_l):<5} 点名={got_n or "-":<14} '
              f'{"✅" if good else "❌"}　({why})')
        if not good:
            ok = False
    # 防恒绿：若 `_gate_of` 退化成"永远 True"，第 5/6 条会红；
    # 若退化成"永远 False"，第 1~4 条会红。六条合起来是双向卡口。
    # 防恒绿：`_readonly_verb_of` 若退化成"永远返回第一个"，上面第 2/3 条会红；
    # 若退化成"永远返回 None"，第 1/2/4 条会红。四条合起来即双向卡口。

    print('\n结论：' + ('✅ 自检通过' if ok else '❌ 自检未通过'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
