# 技能生态与统一入口（SKILL.md §8 外置索引层）

> **为什么有这个文件**：M-R77 规定 SKILL.md 主体硬上限 40KB。§8 原本 19.4KB（占全文近一半），
> 已整体外置到本文件；SKILL.md 内只保留小节索引表。
> **本层职责 = 导航 + 落地要点**。不再按"§8 全文"维护，长论证与实证过程一律在**唯一正本**里。

**§8 正本分布（改内容只改左列文件，本文件不用动）**

| 小节 | 唯一正本位置 |
|---|---|
| 8.3b 内容分级三档 · 8.14 在线包对比 · 8.16 写后验证 · 8.6 联动并行 · 8.10 端到端 · 8.17 四化 | `docs/manual_外置细则.md` |
| 8.15 做JSON标准流程SOP | `docs/外置_做JSON标准流程SOP.md`（权威源 `scripts/sop_makejson.json`）|
| 8.10 端到端十一步（机器可读）| `scripts/flow.json` |
| 8.13 底包细分类 | `/workspace/标准1015_底包细分类体系.md` |
| 8.17 规则 54 四化 | 总册 + `scripts/rules_registry.json` |

> 技能不是孤岛。本体系把分散的技能连成**可自升级的技能生态**：统一登记、互相发现、联动触发。

---

## 8.1 技能生态登记库（互相发现的底座）

- database `T4kODtANe7rSrtZTu4sIND`，11 字段（技能名/路径/触发词/输入/输出/依赖/version/末次升级/状态/可升级/简介）
- 查看：`tvbox eco`；**注册后才能被其他技能"发现"**，联动不再靠人记路径。`skill-evolution` 的 `create`/`upgrade` 自动 `register_eco`（已存在则更新 version）。
- 生态成员（6）：library（平台层）/ skill-evolution（进化引擎）/ tvbox-interface（整包流水线）/ tvbox-source-blacklist（质量闸门）/ **tvbox-source-hub**（线路中枢，本技能）/ legacy-watchdog（遗留监测，管未闭环+时效项，与 evolve 的「管复发」互补）
- `evolve scan` 全集 = **生态库登记 ∪ 本地自建**（防前缀硬编码漏技能），检查项 = 三件套 + 触发词

## 8.2 统一入口 `tvbox`

```bash
tvbox <brief|status|learn|log|sync|audit|snapshot|ingest|eco|lib-learn|lib-log|sizes>
tvbox block <mark|check|guard|list|stats|review>
tvbox evolve <recurring|create|scan|upgrade|review|note|mine|lend|review-lend>
tvbox run  <v1> <v2> ...   # 同进程串联，共享缓存（最快单线程）
tvbox p    "<v1>" "<v2>"   # 并行多命令（子进程隔离，各带标题）
tvbox chain <名称>          # 一键任务链
tvbox manifest | contracts   # 联动清单 / 联动契约
```

> 不必记脚本路径，`tvbox` 自动分发。已软链 `/usr/local/bin/tvbox`（S022 修复过"脚本在但 PATH 不可达、文档命令全落空"）。

## 8.3 L7 统一入库管线 `link.py ingest`（联动闭环的龙头）

```bash
link.py ingest --candidates /tmp/to_add.json --dry   # 演示
link.py ingest --candidates /tmp/to_add.json         # 真实入库
```
- 流向：候选 → `blk.py guard` 黑名单拦截 → 放行则分类（后缀即体系）→ 入源总库 → `snapshot`；拦截则直接归档失效库（**不污染源总库**）
- 拦截是主动触达的：失效 host 在新源检测时自动命中
- 幂等：已归档失效源自动跳过（`--dry` 不写）

## 8.3b 内容分级三档 → 正本 `docs/manual_外置细则.md#8.3b`

落地要点：分档字段 = 源库「内容分级」；产出 json **按档分别出，不混档**；`未知` 档 ≠ 干净。

## 8.3c 线路总库·四档 + 产出 json 归档监测

- 用户需求：线路按**全量/纯净/18 三档各一份文档**，产出 json **单独一档**并列，并**与监测联动**（作者仓库跑路 → 我的 json 自动标失效）
- 四档并列（根 `ftHhGQ4c6EbEN62WVPMdTh`）：全量 `Gx2pP8c3rTYIb3TYwgi1n1` ｜ 纯净 `BJCCeJPkCifYdhs0QwklKp` ｜ 18 `53htuXyjvtm9YAjxLl2obP` ｜ 产出json `GVP5OkLnHCgTke3nVdNcNF`
- 命令：`newlib.py build｜migrate｜archive --file F.json [--repo URL] [--level 档]｜relink`（细节查 docstring）

**四条实测坑（必记）**

| 坑 | 后果 | 做法 |
|---|---|---|
| `可用体系` 源库是逗号连接整体串 | 直塞 multi_select 报 `invalid fields` | 按 `,` 拆数组再写 |
| `rows_of()` 不含 record_id | `relink` 取不到 id 无法回写 | 必须分页查询读归档表 |
| 来源非 git 托管平台（自建域名/直链 jar）| 监测技能巡检不了 | **不登记监测**，失效标记=`待核` |
| `未知` 档当干净放纯净版 | 漏网 18+ | 只放**能确认档位**者，纯净版仅收 `内容分级=='纯净'` |

联动闭环：`archive` → 写 DB_UPSTREAM 监测册 → 探源司巡检标 `已封禁/删除` → `relink` 反查回写 `失效标记=失效`。

## 8.5 五向闭环自检

| 方向 | 落点 |
|---|---|
| 新技能形成 | 新技能补 SKILL.md + CHANGELOG，登记入生态 |
| 生态 | 🧩 生态登记库 + 注册函数 + `tvbox eco` 视图 |
| 联动 | L7 ingest 管线（黑名单→源库→快照）+ 跨技能调用 |
| 技能升级 | `evolve scan/upgrade` 真正做版本提升与契约补齐 |
| 高效 | `tvbox` 统一入口 + 幂等 ingest + 共享核心 `core.py` + 并行读/调度 |

## 8.6 技能↔脚本联动 + 并行加速 → 正本 `docs/manual_外置细则.md#8.6`

一句话：多脚本共享同一 `core.py`（读缓存 + `fanout` 并发读 + 限流写）；跨命令并行用 `tvbox p`（子进程隔离，避开 stdout 串扰）。
**并发数怎么定 → 一律走 `concurrency.run_bounded(tier=...)`，见 SKILL.md §4.5 与 M-R114。**

## 8.7 交付铁律 + 复盘四问（规则 49 / 50 — 进化发动机的燃料）

> 用户连续追问暴露的硬伤：体系类目没写进规则、轻量可编辑 JSON 没交、交付物缺 JSON、复盘环节缺失。已固化进云 RULES 第 J 章。

**规则 49 · 源分组类目标准（强制复用，不得临时另立）**
- 唯一权威 = 云 RULES 规则 49；机器可读载体 `/workspace/tvbox.源分组类目标准.json`
- 复用 `split_jsm.py` 的 `group_of()`，判定顺序：名称关键词优先 → 兜底按 api 形态
- **`split_jsm.py` 是这口径的正本且必须保持可 import**：`RULES`/`group_of`/`split_pkg` 为纯函数，副作用只在 `main()`。下游只调用不复制，复制必漂移
- ⚠️ **分组类目（内容品类）≠ 体系（A/B/C/D 技术血缘）**，两者独立标记，不可混用

**规则 50 · 交付物四项强制（缺项 = 交付不完整）**
1. **结构化 JSON**（候选/结果/分类映射，不能只有脚本 + md）
2. **轻量化可编辑 JSON**（配置接口类额外产出 `xxx.轻量化可编辑版.json`）
3. **体系/类目标注**（落库，类目遵循规则 49）
4. **复盘报告**（含下方四问，驱动 ③台账 + ④经验库 + `evolve upgrade`）

**复盘四问（每次交付收尾必跑）**
```
① 成果：这次做成了什么？
② 不足：哪里没做到位（用户反馈 / 自检）？
③ 改进：当场改了什么、沉淀成什么规则？
④ 进化：是否触发 evolve upgrade / 新技能立项 / 规则47快照刷新？
```
落库：②+③ → `lib-log` 记 ④经验库；整单 → `log` 记 ③台账；进化 → `evolve upgrade`。

## 8.8 交付物命名约定 + 必须进底包库（规则 51）

**规则 51 · 命名 = 体系/类别标签 + 日期(MMDD) + 描述**
- 同形范例：`A1015_156站_含黄蚂蚁11组` / `B1014_46站_cainisi` / `C1014_黄蚂蚁_11分组` / `🔞1014_lives_zoho25路`
- 标签：底包库源包用 `A/B/C/D/🔞成人`；配置接口类**能判技术血缘就用体系字母**（FongMi/CatVod→A、moji Guard→B、零 jar 纯直链→C、Amns→D、成人直链→🔞），判不出才退类别标签
- 日期取任务完成日；**禁止**无体系无日期的懒命名

**配套铁律：交付物必须进底包库 + 显式交代**
- 除落 `/workspace` 外，**必须同步上传底包库根**（`parent-id = GCLJWbFGS1BmYbj6JCX9it`，space `8w5cnqCqQJ0wNoF2Hb6efH`）
- 通道：JSON → drive 节点；MD → doc 节点
- 交付结果**必须显式列出**每个交付物的「是否进底包库 + node id」

## 8.9 坑→解法固化（规则 52 · execution 层）

**四动作（缺一不可）**
1. **记**：坑 + 失败旧方式 + 正确途径写进 ④经验库；「解法/可复用点」必填且**写成可执行步骤**
2. **固化（三级取高）**：可代码化 → 技能脚本可 import 函数；可判定化 → SKILL.md/云 RULES 成规则；仅操作型 → ④库复用点 + ③台账 reuse 字段
3. **直达**：`evolve recurring` 命中聚类后，动手前先查已固化途径，**禁止重新试错**
4. **改进**：同一问题再次踩坑 = 途径不够好 → 三处同步更新途径本身，version +1，CHANGELOG + evolution_log 留痕

## 8.10 端到端完整流程 → 正本 `scripts/flow.json` + `docs/manual_外置细则.md#8.10`

**只能背下来的三条**：
1. **顺序不可乱**：触发 → 体系识别 → 拆解 → 黑名单拦截 → 去重 → 测活 → 入库 → 交付四项 → 复盘四问（黑名单必须先于去重/入库；复盘必须当场）
2. **快照刷新判据**：对源库/失效库的**写操作**收工必 `tvbox snapshot`；纯规则/命名/文档变更**不刷**，须在复盘说明原因
3. **体系判不出 → 标「待确认」**，交付时显式列清单请用户拍板，**禁自动猜**

## 8.11 云端读写预检（高频问题 TOP1）

> 云端读写坑占 ④经验库 13/16，4 个阻断级坑全集中于此。共同结构：**服务端校验与本地直觉不一致，且失败信息不直白**。

**写前（强制）**
1. `scripts/preflight.py --database-id <id> --records-file x.json` —— 拦未知字段/裸值/类型不符/选项外取值/空值
2. 字段名以 `get_database_schema.py` 为准，**禁止凭记忆写 `r.get`**
3. 批量 ≤100 条/批 + sleep 防限流；走 `core.add_records`（自带写后 invalidate）

**写后（强制）**
4. 不信 `results.success=true` —— **读回核对行数/内容**
5. 同进程读回未查到 ≠ 没写入 —— 换新进程确认
6. 本地路径白名单：/workspace 与 artifact 都可能被拒，上传前先试

## 8.12 可用性判定快查（用户提问 TOP1 环节）

> 用户提问最多的环节永远是「能不能用、怎么才能用」。遇症状直接查表，不重新试错。

| 症状 | 判据 | 处置（已验证范式） |
|---|---|---|
| 站点无数据/空白 | api 失效或依赖本地 jar | 剥离失效 csp 生成纯可用版（zoho 范式）|
| 解析坏/播放失败 | 解析地址失效或需登录 | 换有效解析；保留原生态改远程 URL（lytvs+drpy2）|
| App 限次/广告 | 配置带限次逻辑 | 去限次纯净版（easytv 范式）|
| 整体依赖 spider jar | spider 指向 `./jar/xx.jar` | 保留 jar 依赖并明示；或改远程 jar（md5 核对）|
| 域名整体失效 | host 命中失效库 | `tvbox block mark` 整域拉黑 |
| 采集类无数据 | 需带 `?ac=list` 等参数 | `probe_src.py` 自动补参探测 |
| 直播源无画面 | 返回体 <500B 或非 200 | 判停用；livekey/streamid 失效则拆线单测 |
| 体系判不出 | 后缀与技术血缘都判不了 | 标「待确认」+ 交付时显式列出（§8.10 第 3 条）|

## 8.13 底包细分类体系 → 正本 `/workspace/标准1015_底包细分类体系.md`

**不查正本也必须记死的四条**
1. **枝判定只看 spider/jar 地址或 md5，不看类名重合** —— easy999↔bbm116 类名 Jaccard 0.69 但 jar 不同，不可平移
2. **业务别名 ≠ 技术体系字母**：`D包 = DmaxPro908`（零 jar 直链）= 技术体系 **C** 主干包，**不是** D·Amns。三包先查术语库 DB_TERM 再下结论
3. **lives/http 直链/parses/drpy 远程 js = 全系可平移**；**csp 类线只在同枝可平移**（跨枝须端测）
4. **归位断言（入库收尾必跑）**：包名前缀体系字母 == 所在文件夹体系；18+ 必归 🔞 且带前缀；md5 重复标 `_重复可删`

## 8.14 在线包对比分析套路 → 正本 `docs/manual_外置细则.md#8.14`

用途：拿到用户新包与已有包做轻量 diff，判断"新增/改版/换源"。含在线 JSON 解析三连坑、集合比对失真坑、轻量化对比五步法。

## 8.15 做JSON标准流程SOP → 正本 `docs/外置_做JSON标准流程SOP.md`

> **开工前与每一步先查** `sync_sop.py route --symptom "现象"`，命中即走已验证解法；不通再按 S1–S10 主流程；仍不通记台账并 `add-case`（成长协议）。**症状复发禁止从头试错**。权威源 `scripts/sop_makejson.json`（禁手改）。

## 8.16 写后验证双保险 → 正本 `docs/manual_外置细则.md#8.16`

铁律摘要：写完必读回；读回分三级（进程内 → 短重试 → 独立进程）；**禁信单一进程内信号**。

## 8.17 全局联动生态·四化（规则 54）→ 正本 `docs/manual_外置细则.md#8.17`

| 四化 | 判定（不满足即不合格） |
|---|---|
| 流程标准化 | 自建技能 = `version` + `CHANGELOG` + `flow.json` 三件套，缺一 evolve scan 不过 |
| 标准流程化 | flow.json 机器可读，**步骤 cmd 必须是真实命令**，禁臆造 |
| 任务效率化 | 跨技能任务链写进 manifest `chains`；tvbox 在 PATH |
| 联动生态化 | 上下游格式契约化（`contracts.json`），**引用契约不复制数字**（唯一权威=快照）|

**验收（evolve scan 增补项）**：① 三件套齐全 ② chains 非占位步骤可执行、占位有替换指引（`<候选文件>` 类占位直接跑必失败，**禁静默失败**）③ contracts.json 与 ingest 实消字段一致（dry-run 验）④ tvbox 在 PATH ⑤ evolve scan 全生态就绪。
