# 外置正文 · 做JSON标准流程SOP（自 SKILL.md §8.15 外置）

> 权威源仍是 `scripts/sop_makejson.json`（禁手改）；md 投影由 `sync_sop.py render` 生成。本文件只是 SKILL.md 的**说明性外置**，便于主文件瘦身（M-R77⑥）。

### 8.15 ★ 做JSON标准流程SOP（可溯源·可成长 · v1.0.11）
> 场景：任何「做 json」任务（整包/轻量化/对比表/拆解入库）**开工前与每一步先查本 SOP**；症状复发**禁止从头试错**，直达已验证解法。三件套：权威源 `scripts/sop_makejson.json`（禁手改）+ 同步器 `scripts/sync_sop.py` + md 投影《标准1015_做JSON标准流程SOP.md》（禁手改，由 render 生成）。

**三级路由铁律（顺序不可颠倒）**：
```
① 症状直达：sync_sop.py route --symptom "<现象>" 命中 → 直接执行「已验证解法」（出处=台账/经验库编号，可溯源）
       ↓ 不通
② 标准主流程：S1 需求判定→S2 抓取解析→S3 体系识别→S4 体检拆解→S5 测活→S6 去重拦截→S7 轻量化→S8 入库归枝→S9 交付四项→S10 复盘闭环；每步先读「坑与解法」再按「备选路径」按序尝试
       ↓ 仍不通
③ 未探索路径：记现象→类比第3节历史案例排查思路→解决后必须 add-case 登记（成长协议）
```

**成长协议（文档自动同步最新做json过程的机制）**：新问题/新解法出现时 `sync_sop.py add-case` → 自动去重（已覆盖→解法增强 / 全新→pending_queue 待分级）→ 自动重渲染 md；`promote SYxx` 把复发稳定的症状提升为正式索引；`check` 查欠账与高频出处。**复盘闭环（S10）必须跑一次 add-case**，否则经验散落等于没做。

**命令**：`sync_sop.py route --symptom "现象"`｜`check`｜`add-case`｜`promote`｜`render`（参数见脚本 `--help`；改内容只改 JSON，禁手改 md）。

**校准与绑定**：route 阈值 0.45 经校准集定标（symptom/root_cause/alias 三路；中文子串≥5/ASCII≥7 强命中；top2 分差<0.15 出 rival）。promote 按**症状关键词**匹配 pending（pending 无 ID，勿传 SY 编号）。flow.json 11 步已加 `sop_ref` 对齐 S1-S10，**冲突以 SOP 权威源为准**并回修 flow.json。
