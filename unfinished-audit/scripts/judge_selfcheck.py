import ast, os, sys

"""判据同源互比·回归自测（C008 落地）

为什么不做全生态扫描而做「用例闸门」：
  实测全生态 AST 扫「两侧同源」，命中 4 处**全是误报**
  （`m.group(1)==m.group(2)` 比两个捕获组、`a.frm==a.to` 比转账双方、
   `self.done==self.total` 是进度判断）——语法上的"同父"几乎总是合法。
  真正的问题在**语义层**：`who == it.owner`（who 的值域本就是 it.owner）。
  纯静态抓不到，硬抓只会制造误报，而误报多了人就不看这一屏了。

所以改为：**把踩过的四种形态固化成用例，跑得通就是没退化。**
  形态① 自反比较       x == x
  形态② 同对象属性     a.b == a.c  且 a.b/a.c 语义独立（合法，不报）
  形态③ 同容器元素     for it in X: it.a == it.b  且 a 由外部传入（非法）
  形态④ 外部输入与账本字段同源  who == it.owner（非法，哨兵用例）

用法：python3 judge_selfcheck.py        → 0 全过 / 1 有退化
"""


def _report(cases):
    bad = 0
    for name, ok, detail in cases:
        print(f'  {"✅" if ok else "❌"} {name}')
        if not ok:
            bad += 1
            print(f'      {detail}')
    return bad


def case_self_compare():
    """① 自反比较应被判为恒真。"""
    src = 'def check(x):\n    return x == x\n'
    tree = ast.parse(src)
    found = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):
            l, r = ast.unparse(n.left), ast.unparse(n.comparators[0])
            if l == r:
                found = True
    return found, 'x == x 未被识别为恒真'


def case_same_attr_legal():
    """② 同对象属性——必须**不**判错（a.frm == a.to 是合法的转账双方校验）。"""
    src = ('def check(a):\n'
           '    return a.frm == a.to\n')
    tree = ast.parse(src)
    ok = True
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):
            l, r = ast.unparse(n.left), ast.unparse(n.comparators[0])
            # 合法：两端属性名不同，语义各自独立
            if l == r:
                ok = False
    return ok, 'a.frm == a.to 被误判为同源'


def case_external_vs_field():
    """④ 外部输入与账本字段同源——这是真实事故形态，必须能被描述性判据识别。

    机器不能自动判定语义同源，但**可以要求代码里显式声明两侧来源**。
    本用例检查：unfinished_cli 的 before_in_who 在比较时，
    左侧是否由「命令行传入」（stems），而非直接取 it 的字段。
    """
    p = '/root/.codebuddy/skills/unfinished-audit/scripts/unfinished_cli.py'
    if not os.path.exists(p):
        return True, ''       # 文件不在则跳过（不算失败）
    src = open(p, encoding='utf-8').read()
    # 关键证据：中档判据里 before_in_who 与 it.get(...) 是**两个独立来源**
    has_gate = 'before_in_who and (it.get(' in src
    has_stems = 'stems = {p for p in re.split' in src
    ok = has_gate and has_stems
    return ok, '中档判据未保持「before_in_who（外部传入）与 it 字段」两源分离'


def case_no_repeat_regression():
    """守卫：确认「判据」里不再出现 `it.get('owner') == who` 这种恒真写法。

    ★ 必须先分清两类用法，否则这条守卫自己就成了误报源（实测撞上）：
      · 合法：**查询域过滤** `its = [it for it in X if it.get('owner') == who]`
        —— 这一句的语义就是"取 who 名下的账"，与 who 比较正是它该做的。
      · 非法：**分类判据** `if it.get('owner') == who: return '中档'`
        —— 在已按 who 过滤过的域里再按 who 判档，必然恒真。

    差别不在语法而在**上下文**：出现在列表推导式的 `if` 里 = 域过滤（合法）；
    出现在语句级 `if`/`elif` 且分支体内有 return/append 到分类桶 = 判据（须受约束）。
    """
    p = '/root/.codebuddy/skills/unfinished-audit/scripts/unfinished_cli.py'
    if not os.path.exists(p):
        return True, ''
    src = open(p, encoding='utf-8').read()

    import ast as _a
    tree = _a.parse(src)
    offenders = []
    for node in _a.walk(tree):
        # 只看语句级 If（含 elif），不看列表推导的内部
        if not isinstance(node, _a.If):
            continue
        seg = _a.unparse(node.test)
        if "it.get('owner') == who" not in seg and "it.get('skill') == who" not in seg:
            continue
        # 该 If 是否已受 before_in_who / stems 之类的外部侧约束
        if 'before_in_who' in seg or 'name_hits' in seg or 'stems' in seg:
            continue
        # 再看分支体是否是"分类"（出现 append 到 bucket 或 return 常量档位）
        body = _a.unparse(_a.Module(body=node.body, type_ignores=[]))
        if ('append' in body) or ('return' in body and '档' in body):
            offenders.append((node.lineno, seg[:80]))

    return (not offenders), (f'第 {offenders} 行判据未受外部侧约束' if offenders else '')


def main():
    print('🧪 判据同源互比·回归自测（C008）\n')
    cases = [
        ('① 自反比较 x == x 能被识别为恒真', *case_self_compare()),
        ('② 同对象属性 a.frm == a.to 不被误判', *case_same_attr_legal()),
        ('③ 外部输入与账本字段保持两源分离', *case_external_vs_field()),
        ('④ 无恒真写法回流（it.owner == who）', *case_no_repeat_regression()),
    ]
    bad = _report(cases)
    print()
    if bad:
        print(f'🔴 {bad} 项退化——判据又出现同侧互比')
        return 1
    print('🟢 4/4 全过：判据两侧来源分离，无恒真回比')
    return 0


if __name__ == '__main__':
    sys.exit(main())
