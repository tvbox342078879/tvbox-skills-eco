"""S184 A4：把定时巡检从「报告器」改成「派单器」。

## 现状（改之前）

任务 12854499「S165·跨界调用每日巡检」每天 09:15 跑：
    tvbox ledger call audit-call --quiet   # 体检
    tvbox ledger call debt --quiet         # 看账
然后**按格式回一段话**给用户。

它产出的是一份**给人读的报告**。问题不在报告写得不好，在于：
  报告读完了，**没有任何东西被写下来**。第二天同一份报告再来一遍，
  同一批问题还在，但"这件事今天被发现了"这个事实不存在于任何地方——
  没有账龄、没有主人、没有期限，也就没有"该谁、什么时候、做到没做到"。

用户原话（判据）：**不靠手动干预，生态自己转一圈并留账。**
"留账"这一环，报告器天然做不到：报告是一次性的，账是持续的。

## 改法

指令改成三件事，按顺序，任何一步不吞错：

  1. `tvbox inspect scan`                    —— ① 看红字（自转第一环）
  2. `tvbox close`                           —— ② 派单落账（八源采集，含监察司红字）
  3. `tvbox ledger call post`                —— ⑤ 对账终验（五门）

第 2 步是新增的关键：它才是"把发现变成账"的动作。
不用 `--quiet`：quiet 会吞掉落账行（新增/存续/消失），而**落账行是这一圈的唯一证据**
——没有它，日志里看不出这一圈到底往账上写了什么，等于又回到"看起来跑了"。

三步各自记退出码（不 `&&` 串联）：派单失败不能让对账也不跑——
**对账恰恰是最该在出问题时跑的**。串联会把"账没派上"和"账对不平"两件事
压成一个 exit 1，人无法分辨是哪个环节坏了。

## 输出纪律

回一段固定格式的话，四个数必报：红字几条、新增几条、未结几条、五门过几门。
**这四行是给人看的**，但它们全部来自上面三步的**真实输出**，不是复述意图。
如果某步失败，写"该步失败 + 退出码"，不写"已完成"。
"""
import subprocess
import sys

TASKS = '/root/.codebuddy/skills/automation-task-manager/scripts/scheduler-api.sh'


def sh(cmd, timeout=600):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def main():
    print('🔄 生态自转·每日派单（A4）\n' + '─' * 60)

    # ① 看红字
    rc1, o1 = sh('tvbox inspect scan')
    reds = [l.strip() for l in o1.splitlines() if l.startswith('    遗留: ')]
    total = ''
    for l in o1.splitlines():
        if l.startswith('📊 合计'):
            total = l.strip()
    print(f'① 红字：rc={rc1}｜{total or "（未解析到合计行）"}')

    # ② 派单落账
    rc2, o2 = sh('tvbox close')
    land = ''
    for l in o2.splitlines():
        if '落账' in l and '新增' in l:
            land = l.strip()
            break
    print(f'② 派单：rc={rc2}｜{land or "（未解析到落账行）"}')

    # ⑤ 对账
    rc3, o3 = sh('tvbox ledger call post')
    gates = [l.strip() for l in o3.splitlines() if l.strip().startswith(('✅', '❌'))
             and '①' in l or l.strip().startswith(('✅ ②', '✅ ③', '✅ ④', '✅ ⑤'))]
    passed = len(gates)
    print(f'⑤ 对账：rc={rc3}｜五门通过 {passed}/5')

    print('\n── 回话模板（照此回给用户）──')
    print(f'生态自转·每日圈 {__import__("datetime").date.today().isoformat()}')
    print(f'  红字 {len(reds)} 条：{total or "见 scan 输出"}')
    print(f'  派单 {land or "（未解析）"}')
    print(f'  对账 五门 {passed}/5（{"放行" if rc3 == 0 else "拦截"}）')
    if rc1 or rc2 or rc3:
        print(f'  ⚠️ 非零退出码：scan={rc1} / close={rc2} / post={rc3}'
              f'（各自独立记录，不串联——派单失败也要跑对账）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
