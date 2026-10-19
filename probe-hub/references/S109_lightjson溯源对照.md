# 与历史经验的对应（复刻溯源 · 索引自 SKILL.md §3.8）

> 外置副本（M-R77 法①）：SKILL.md §3.8 只留索引行，对照表全文在此。


| 历史经验（出处） | 本命令落实 |
|---|---|
| 直链类分叉判据 `api.startswith('http')`（`split_box.py` / `split_jsm.py`） | `_lj_is_zhilian()` |
| 真实测活判据（`livecheck.py`·S097） | 直接 import 复用，**不重写**（M-R38 单一事实源） |
| sites 条目结构 + `type` 取值（`探源司1016_直链5条_TVBox配置.json`） | `_lj_site_type()` |
| 保留率必记、被删项完整留档（SOP §S7 "禁静默裁剪"） | 剔失清单 + 构成统计输出 |
| 规则51 命名（体系+MMDD+描述） | `--tag/--desc` + 自动日期 |
| `key` 稳定可复现（避免重建后指针失效·SY08） | md5(api\|name) 幂等 key |
