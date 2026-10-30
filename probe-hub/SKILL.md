---
name: probe-hub
description: "探源司·TVBox 上游侦察（可升级·规则54三件套）。能力：①监测作者仓库动态（存活/内容变更/新增文件/活跃度）；②搜更多有效仓库（多平台→rank→verify）；③采线路（sites/lives/parses→测活→去伪→入库）；④GitHub 凭据与配额；⑤多平台适配（GitHub/GitLab/Gitee/工蜂）；⑥类目判据复用整包坊 classify_line；⑦活跃度预测；⑧search 三态取数+合并写；⑨--dry 演示；⑩候选上云；⑪真实测活（点播真拉 vod_play_url / 直播真拉分片，非仅 HTTP 码）；⑫promote 写成品源库 DB_PROD；⑬prune 作者原包 SOP；⑭lightjson 全类型轻量 json；⑮采料去向锚定 D 包；⑯网盘取料 `pan`+单文件容器 `pak`（S170/S171）；⑰壳源侦察与抠链 `shellcrack`（S196）。★写源 `forge`：`verify <源.py> [--save]` 过验才留档（没验过不许进）、`list` 盘点，见 §3.12。★写源自验 `livecheck --spider` 逐接口 5 步真跑，坏源判失败、域名挂判无法判定。★破甲 `decrypt`。 【边界】手上没源/没仓库要去上游找 → 本技能；已有接口 URL 要入库 → tvbox-interface；要判断某条源该不该拉黑 → tvbox-source-blacklist。 触发词：哪个底包能加更多线路 / 哪种线路能进我的包 / 给D包换路线 / 线路失效了点不开 / 某个线路转圈 / 还有哪些站能用 / 这个接口还能用吗 / 这批源帮我验一遍 / 作者仓库还在吗 / 他又更新了什么 / 有没有新的源 / 我的库不够用了 / 这个站能不能做成源 / 帮我写个源 / 这个源好没好 / 复现一下之前的采集 / 上游仓库 / 作者仓库 / 原作者 / 跑路 / 被封 / 删库 / 仓库监测 / 找仓库 / 找源 / 搜索源 / 采集线路 / 采集源 / 扩充库容量 / 探源 / 探源司 / 真实测活 / 成品源库 / 剔除失效 / py线路源 / 换路线 / D包 / DmaxPro908 / 新源 / 上游仓库监测 / 找点播源 / 找直播源 / 扩库容 / 新线路 / 合格线路源 / 监测仓库 / 仓库动态 / 上游侦察 / 剔除失效线路 / 直链轻量版 / 原仓库转json / DmaxPro / 播放页壳 / 壳源 / 抠链 / 抠直链 / 壳库 / 从别的站找壳。"
version: "1.20.8"
---

> **🗣 语言铁律（M-R110 · 全模型适用）**：本技能所有输出——正文、思考过程、进度播报、
> 工具调用说明、报错与澄清提问——**一律简体中文**。切换模型（快速/Hy3/深度思考等）、
> 新会话、恢复会话、压缩上下文后**均不得回退英文**。用户明确要求中文时尤须遵守。
> 完整习惯快照见 `ecosys-conductor/references/user_habit_brief.md`。

# probe-hub（探源司 · 上游侦察）

> **定位**：生态的**上游侦察司**——底包 json 是从原作者仓库改来的，只有掌握上游现状，
> 才能在作者跑路 / 被封 / 发新线路 / 更新好东西时**第一时间做出调整**。
> 三模块：**监测（monitor）/ 搜索（search）/ 采集（harvest）**，对外侦察、对内供料。

**成员中文名**：探源司（A 风格·官署器具风，与 源枢库曹 / 质检闸 / 整包坊 / 遗迹哨 / 进化舵 / 总指挥 同构）。
**建制位次**：位于**对外一侧**——与"质检闸"形成**进料—把关**的呼应（探源司进货，质检闸验货）。

---

## 1. 用途

| 模块 | 职责 | 输出 |
|---|---|---|
| **monitor** | 监测底包库原作者的第三方仓库/发布源动态：**存活状态**（跑路/被封/删库/下架）/ **配置内容变更** / **新增文件目录** / **更新频率活跃度** / **发布源直连包探活（pub-package）** / **生命周期声明正交交叉** | 变更摘要 + 快照对比（DB_UPSTREAM 状态回写） |
| **search** | 搜索更多**有效可用**的第三方仓库，扩充"库容量"，方便加工找调用 | `search_results.json` + 高分仓库转监测目标 |
| **harvest** | 采集**高质量线路**（直连/采集源），**首要供直链系线路池换路线调用**（三系口径 1025；D 包为历史别名） | 候选 json（contracts `candidate-list` 格式）→ 交 `tvbox ingest` 入库 |

**核心命题**：用户原话——"我底包库 json 是根据作者原仓库改过来的，只有我知道作者仓库现状，才能做出相应调整"。本技能把"作者仓库现状"从**人脑记忆**变成**可查、可比、可告警的基线**。

> **★ 采料去向（业务链·S085·三系口径 1025）**：采集到的 http(s) 直链天然归**直链系**——
> 全 `type=1` 直链、零 jar 零 spider，稳、跨壳通用，代价是直链会失效。
> 调用链 `harvest scan/pull` → `livecheck` → `harvest promote` → **直链系线路池** → 用户按三系给任何直链型配置换路线。
> （「D 包/DmaxPro908」自 1025 起为**历史别名**，语义指向直链系。）
> 完整说明与硬约束见 §3.9 / [`references/S109_采料去向与D包换路线.md`](references/S109_采料去向与D包换路线.md)。


## 2. 触发词

**术语类**：上游仓库 · 作者仓库 · 原作者 · 作者跑路 · 被封 · 删库 · 仓库监测 · 监测仓库 · 找仓库 · 搜索源 · 找新仓库 · 采集线路 · 采集源 · 直链 · 扩充库容量 · 探源 · 探源司

**业务语言类（S148 补·用户不会说术语，说的是需求）**：哪个底包能加更多线路 · 哪种线路能进我的包 · 给 D 包换路线 · 线路失效了点不开 · 某个线路转圈 · 还有哪些站能用 · 这个接口还能用吗 · 这批源帮我验一遍 · 作者仓库还在吗 · 他又更新了什么 · 有没有新的源 · 我的库不够用了 · 这个站能不能做成源 · 帮我写个源 · 这个源好没好 · 复现一下之前的采集

> 当用户提到以上任一，或**底包库线路失效怀疑源头出问题**时触发。

### 2.1 ★ 铁律：不许看见脚本就直接跑（S148·M-R104）

**病根（S148 实测）**：一次长会话 12884 次工具调用里 `Skill` 只被调用 **3 次**，探源司 **0 次**。
后果不是"慢"，是 **SKILL.md 的流程约束根本没被读**——§3.7 分类空壳坑、§3.5.1 黑名单三铁律、
M-R60 三态，全靠"碰巧记得"才遵守，**不是被技能强制的**。

> 正本（S148 全量实测 + S189 二次复发现场 + 没走流程欠下的三笔账）：
> [`docs/外置_不许看见脚本就直接跑.md`](docs/外置_不许看见脚本就直接跑.md)

**判据：知道脚本路径 ≠ 走过流程。** 直接跑脚本只得"命令结果"；走技能得"结果+判据+验收+留痕"。前者出错时没人告诉你错在哪。

**硬约束**（违反即返工）：
1. TVBox 业务请求**先加载技能**再跑命令，不许凭记忆直接敲脚本；
2. 命令输出只算**证据**，结论必须按对应判据（§3.5 / §3.7 / §3.5.3）给，不许自创判据；
3. 写库/判死/剔除必须走对应章节的 readback 与三态校验。

> **已补执行层**：`~/.codebuddy/hooks/skill_route_guard.py`（M-R133）——命中真触发词且本轮未加载本技能时，
> PreToolUse `exit 2` 强制先 `Skill`。判据见 `ecosys-conductor/SKILL.md §8.8`。


### 2.2 ★ 铁律：用户编号选项引用约定（S118·M-R109·全局）

**约定（用户明确要求固化）**：当你用简短代号回应——单个数字 `1`/`2`/`3`…、单个字母 `a`/`b`/`c`…、
或复合 `1.2`/`a.b`/`2.1`——该代号**默认指代我（agent）在最近一次回复中给出或推荐的可选项**
（如「两件事」「或者」列出的编号项、AskUserQuestion 的字母/数字选项、列表序号等）。

**硬约束**：
1. 直接将代号映射到最近回复的对应选项并**立即执行**，无须再次澄清；
2. 复合代号 `X.Y` = 「第 X 组第 Y 项」或「主选项 X 下的子项 Y」；
3. **例外须先澄清**：代号存在合理歧义（可能映射到多条不同回复的选项），或该选项会导致
   不可逆 / 破坏性操作（删库、覆盖、发布、改线上配置等）时，先确认再执行；
4. 代号无法唯一解析时回退澄清，**不臆测**。

**解析优先级**：最近一次含编号 / 字母选项的回复 ＞ 更早回复。

## 3. 用法（引擎 `scripts/probe.py`）

> 📌 **S199 两类新型监测目标**（填补只认 GitHub 仓库的黑洞）：① **发布源直连包（pub-package）**——HTTP 轻量探活对象存储直连包（无 git 历史）；② **生命周期声明**维度——mark.json 自报 × 实测活跃度正交交叉（冲突/风险/提示）。登记见 [`references/S199_发布源与生命周期声明.md`](references/S199_发布源与生命周期声明.md)。

```bash
# 模块 A：监测上游仓库
tvbox probe monitor check [--repo <url>] [--all] [--json]   # 四类信号逐条三态
tvbox probe monitor diff  [--repo <url>]                    # 快照对比（本次 vs 上次）
tvbox probe monitor status                                  # 监测目标总览表
tvbox probe monitor add --url <repo_url> [--author X] [--cadence 每周] [--branch main]
tvbox probe monitor import --file <seed.json>   # 从 seed JSON 批量登记（接口清单/离线种子基线，kind=tvbox-iface/repo/pub-package）
# ★S199① 聚合分发站监测：盯一个聚合发布源的清单文件（tvboxapilinks.txt + api/live.json），
#   其 sha12 变更即=全行业接口增删信号（monitor_pubsources.json 的 pub-moliys-666 即此模式）。
tvbox probe monitor due                                     # 列出到期未检查的目标

# 监测三态分诊细化（借鉴 learn-hub M-L03 · S203 后移植）
> 监测作者仓库存活，必须逐条给**三态结论**，不许折叠成「成功/失败」两分（细化 M-R60，生态规则 M-R203）：
> - **取到**：拿到内容且可解析 → 进下一步；
> - **确定取不到**：404 / 已删 / 明确拒绝 → 记死因，可换源；
> - **无法判定**：超时 / 412 / 登录墙 / 编码异常 → **单列「待复测」，不许当「失败」**。
>
> 412（机房 IP 被 CDN 拒）≠ 文件坏了；超时 ≠ 作者跑路。归因错，处置就全错（S171 实测网盘 412 被误当下载失败）。

# 模块 B：搜索更多仓库
tvbox probe search github --topic tvbox [--limit N]
tvbox probe search gitee  --keyword tvbox [--limit N]
tvbox probe search rank   --in <raw_results.json>
tvbox probe search verify --repo <url>

# 模块 D：破甲（响应体看不懂时先过一遍）
tvbox probe decrypt --file <p>|--url <u>    # 形态分诊（json/hls/html-page/xml/hex/base64*/binary）
tvbox probe decrypt --file <p> --chain      # 当源码看，列变换链信号（按出现顺序）
tvbox probe decrypt --aes-key K --aes-iv IV --aes-data <hex>   # AES 严格解密
tvbox probe decrypt --file <p> --img [--out o.png]             # XOR 图片（魔数反推密钥）
tvbox probe decrypt --file <m3u8> --m3u8 --base <url>          # m3u8 转绝对代理

# 模块 C：采集线路
tvbox probe harvest scan  --repo <url> [--path <子目录>]
tvbox probe harvest pull  --url <file_url> --kind <sites|lives|parses>
tvbox probe harvest filter [--in <candidates.json>]
tvbox probe harvest export [--out scripts/harvest_candidates/harvest_MMDD.json]
tvbox probe harvest dispatch                                # 交 tvbox ingest 入库
tvbox probe harvest promote [--in <候选.json>] [--limit N]  # ★把真实测活通过者写入「成品源库」（S097）
tvbox probe harvest prune --url <作者原包URL> [--outdir 目录] # ★仓库活体校验→逐条真实测活→剔除失效→产备用版（S097）
tvbox probe harvest lightjson [--in <候选.json>] [--outdir 目录] [--desc 描述] [--no-probe]
tvbox probe harvest repack --pkg <整包.json> [--kind sites|lives|parses] [--target <目标包>] [--only-live] [--out] # ★包内抽线路→测活→判能否跨包平移（S142）
tvbox probe harvest splitjsm --pkg <jsm配置.json> [--out] [--no-probe] [--keep-nonhttp] [--push] # ★jsm拆包+形态分诊（S147）

# 写源（P018·S161）：把「写一个 py 源」从临时手搓变成一条命令（验→留档→列→判→画像）
python3 skills/probe-hub/scripts/forge.py verify <源.py> [--save]   # 验源；--save 通过则留档 sources/（不再随 /tmp 蒸发）
python3 skills/probe-hub/scripts/forge.py list                      # 列出已留档源与最近验证结果
python3 skills/probe-hub/scripts/forge.py classify <api> [--in 清单.json] [--out 结论.json]   # 实测判定源类型（不写库）
python3 skills/probe-hub/scripts/forge.py profile <url>             # 站点画像→推荐探源司命令
python3 skills/probe-hub/scripts/forge.py audit                     # 源库审计
（注：forge 已接入总调度，可直接 `tvbox probe forge verify/list/classify/audit/profile`；上列直跑方式仍等效保留）
# ★C8（T6·2026-1028）入口分裂收口：总调度已接入 quality / quality-batch / content-probe
tvbox probe quality <m3u8地址>                                   # S110 单条质量评测（画质/速度/广告/质量分）
tvbox probe quality-batch [--db 成品源库|线路源库|both] [--limit N] [--only-pass] [--dry]  # S110 批量评测并回填两库
tvbox probe content-probe                                        # 内容层探查（需 TVBOX_ORIG_DIR 指向原仓库配置）
# 纯工具/内部能力（未进总调度，独立直跑；C8 评估：一次性抽取或被 probe.py 内部引用，无独立 CLI 需求）
python3 skills/probe-hub/scripts/mount_judge.py <sites.json> [base] [spider]  # jar 挂载判定（harvest prune 的 csp 系判据底层）
python3 skills/probe-hub/scripts/danmu_pick.py <url...> [--demo]              # 弹幕挑选
python3 skills/probe-hub/scripts/parse_apilinks.py <tvboxapilinks.txt> [out]  # 聚合分发站清单→监测 seed（S199 配套）
python3 skills/probe-hub/scripts/armor.py                        # 破甲底层库（decrypt 的实现体，仅 self_test 入口）
python3 skills/probe-hub/scripts/extract_live_urls.py            # live.json→测活 batch json（§3.5.0）
# ★网盘分享取料（S170）：网盘与 GitHub 仓库并列的料源
tvbox probe harvest pan info --url <分享链接或整段文案> [--passcode X] [--json]  # 列清单（只走API，IPv4可成功）
tvbox probe harvest pan pull --url <同上> [--outdir 目录] [--match 子串]         # 下载（机房IP段会被412拒，与IPv6无关）
# ★MSITEPK2 单文件资源容器（S171）：一个 pak 装整站（源清单+图标+前端页面）
tvbox probe pak <site.pak> [--list] [--get 名字 [--out 单文件|--outdir 目录]] [--key 64hex]  # key 缺省从同目录 classes.dex 自动取（多候选时逐键自证选中能解开的）

# 模块 E：真实可用测活（S097，非仅HTTP码）
tvbox probe livecheck <url> [--kind vod|m3u8|py|class|auto]     # 单条真实测活（class=分类完整性检测·S101）
tvbox probe livecheck --batch <file.json> [--json]           # 批量真实测活
# ★自己写的 py 源，交卷前先自验（S148）：逐接口 5 步真跑，不是"看像不像源码"
tvbox probe livecheck --spider <源.py>

# 候选重排（S143：按四分量重算，非按旧分数重排）
tvbox probe search rank [--in <候选.json>] [--why]           # --why 显示前 5 条分数拆解

# 登记册维护（S143：缺省均只预演，加 --yes 才写；均含云端真删除）
tvbox probe monitor remove <id|URL片段> [--yes]
tvbox probe monitor purge-test [--yes]                       # 清理 test1/tvbox 等自检写入的假目标

# 黑名单（S169）：判死要能自证清白
tvbox probe blacklist audit [--sample 30] [--trials 1] [--yes]   # ★等距抽样复验 L1，找误杀
tvbox probe blacklist judge <api> --fail N [--first D] [--last D] [--detail 证书过期]  # 试算定层

# 自检（16 组活体探针 + 假阳性基线）
tvbox probe self-check

# 模块 D：GitHub 凭据（S089 新增）
tvbox probe token                       # 三态状态 + 实时配额 + 配置指引
tvbox probe token --set <TOKEN>         # 写入 token 文件（0600）
tvbox probe token --clear               # 清除 token 文件
tvbox probe limits [--json]             # 只查配额（供脚本消费）
```

**GitHub 凭据（S089）**：未鉴权 API 限额 **60 req/h**，一次 `monitor check` 约耗 3 请求
（repos/commits/tree），几只仓库即触顶 → 近30天提交/目录树**全部"无法判定"**。配 token 后
升至 **5000 req/h**。三选一（优先级从高到低）：

```bash
export GITHUB_TOKEN=ghp_xxx          # ① 环境变量（临时覆盖，最高优先）
tvbox probe token --set ghp_xxx      # ② token 文件（0600，随技能走）
gh auth login                        # ③ gh CLI（零配置兜底）
```

> 凭据**只落本地、不上云**（秘钥性质与共享库相反）；`_mask()` 遮罩显示，绝不外泄。
> 无凭据**不报错**，静默降级匿名，只是受限——**"能测的照测，测不了的明说无法判定"**。

**退出码约定**：`0`=完成（哪怕有变更）；`1`=整体无法判定（网络/鉴权全域失败，不写空结果）；`2`=致命错（登记册损坏）。

## 3.5 ★ 合格线路源标准（S097·规则 55）

> 用户 2026-10-16 要求：生产的是"能直接放进 json 的线路源"，必须**真实测活**——不许 HTTP 可达就当可用。

**判据一：真实可用（硬性）**——只有第一级算合格：

| 源类型 | 合格判据 | 实现 |
|---|---|---|
| 点播（苹果CMS **JSON 版**） | 真请求 `?ac=detail`，返回体含 `vod_play_url` | `livecheck.check_vod`(JSON) |
| 点播（苹果CMS **XML 版**） | 真请求 `?ac=detail`，正文 `<rss>/<list>/<video>`，播放地址在 `<dd flag=".."><![CDATA[..m3u8]]></dd>`——**全文无 `vod_play_url` 字样**；取到后再真拉确认可播 | `livecheck._xml_play_urls` + `_probe_playable` |
| 直播（m3u8） | 真拉清单 → **真拉一个分片**拿到 >0 字节 | `livecheck.check_m3u8` |
| py/spider | 真请求 api，返回体非空且非错误页 | `livecheck.check_py` |

> **★ S141 事故**：旧 `check_vod` 只 `re.search('vod_play_url')`，XML 版全文该词出现 0 次 →
> **所有 XML 直链源一律判"失败"**，而 `sites` 里 `type=0` 恰是这种形态（D 包唯一能吃的）。
> 修前三条实测源全死、修后全通（分片 2013/2023/2013 字节）。
> 教训：判据写"响应里有没有某个词"而非"里面有没有货"，会被格式变体一枪打死。

**三态契约（M-R60）**：`通过` / `失败` / `无法判定`。**"无法判定"≠"死"**——抖动/超时/限流不算失败，
单列"待复测"，不误删好源、也不折叠成"通过"。

> **慢 ≠ 失败（S148）**：raw.githubusercontent 实测 **22–40s** > 原超时 15s → 存在文件被判失败，
> 同仓库一次采 129 一次 55（**不可复现**）。现行：慢一次置 `_RAW_SLOW` → 后续优先镜像；**回退定向**。`self-check` ㉓ 组守护。

**判据二：字段规范**（与源数据总库同构）——成品源必含：
`名称 / host / 分组类目(规则49) / api接口 / 体系 / 可用体系 / 源类型 / 真实测活 / 测活证据`

**判据三：命名（规则 51）**——`<体系|类别><MMDD>_<描述>`；禁无体系无日期懒命名。

**判据四：落库**——写入「**探源司·成品源库**」（DB_PROD `8JBrhSwsJ3pnPYtoGLo9XV`，S160 前叫成品源库），
与源数据总库、备用库**并列的第四库**；只存真实测活通过者，**禁把未测/失败写进去**。

**判据五：溯源**——每条成品源记 `来源仓库`（作者原仓库），可回溯。

## 3.5.0 ★ 候选溯源字段范本（点③·借鉴 live.json 结构）

借鉴 666 聚合包 `api/live.json` 的字段结构，候选与成品源统一携带四溯源字段，从源头保留
「原始上游直链 / 镜像 / UA / 包大小」，供跨包平移（S142）与回溯源（M-R101 原生态保留）使用。

| 候选字段 | 含义 | live.json 来源键 | 备注 |
|---|---|---|---|
| `sourceUrl` | 原始上游直链 | `sourceUrl` | 探源核心：上游活不活看这个 |
| `mirror` | 镜像站地址 | `url` | 聚合分发站的镜像副本（如 `tvbox.moliys.icu`） |
| `ua` | 请求 UA | `ua` | `null` 表示用默认 UA |
| `size` | 包大小 | `size` | 原文字符串（如 `21.4K`） |

> **透传链路**：候选 row builder（`_cloud_sync_harvest`）→ filter 保留（`cmd_harvest_filter`）
> → export（`cmd_harvest_export`）全程携带上述四字段，不因格式转换丢字段
> （呼应 M-R101「只 reorder 不 select」）。
> **抽取脚本**：`scripts/extract_live_urls.py`（live.json → `harvest_candidates/livecheck_batch_666.json`）。
> 字段映射：`url←sourceUrl`（测活目标=上游直链）、`mirror←url`、`ua←ua`、`size←size`、`source←source`（来源分组）、`date←date`（上游更新日期）。


## 3.5.1 ★ 黑名单主键与三层分级（S144·**M-R96**·含并入的 M-R97/M-R99）

> **正文外置** → [`docs/外置_黑名单主键与三层分级.md`](docs/外置_黑名单主键与三层分级.md)
> （完整推演 + 三处实测硬伤 + 三层分级字段口径）。
>
> **判据主干（必记）**：① 主键必须是**源指纹**，不是 host（按 host 拉黑会连坐活源）；
> ② 指纹归一化**唯一实现** = `probe.py::source_fp()`，禁各技能另写一套；
> ③ 写库判真必须**读回字段值**，`success=true` 不等于写进去了；
> ④ `select` 的枚举值**先读 schema 再写**，禁凭记忆写选项名。
> ⑤ **M-R103** 一条线路只允许存在于一个分组（指纹唯一 ≠ 线路唯一）——推演见外置文件「分组唯一性」节。
> ⑥ **S169 失败原因参与定层**：证书类（过期/校验失败/TLS）封顶 L2 不判死——续期即恢复（实测 3 条因此被误杀，含 30 万条的真源）。
> ⑦ **S169 黑名单要能自证清白**：`blacklist audit` 确定性等距抽样复验 L1，找出「被判死但其实活着」的。
>    根因与实测见 [`references/S169_黑名单误杀三根因.md`](references/S169_黑名单误杀三根因.md)。

## 3.5.2 ★ 原生态保留：写包不许让字段凭空没（S144·P7/P8·**M-R101**）

> **正文外置** → [`docs/外置_原生态保留与段位落点.md`](docs/外置_原生态保留与段位落点.md)
> （完整推演 + `check_preserve` 判据表 + 正确写法代码 + `addto` 段落点表）。

`lightjson` / `repack` / `prune` / `addto` 四条命令都是同一个危险动作：**读原包 → 改结构 → 写新包**。
中间只要用「白名单重建」写代码，没列名的键就整体消失——`json.dump` 照样成功、退出码照样 0、
站点数照样对得上，**人完全看不出来**。S144 实跑就吃掉了作者包的 `version` / `customKeyNotInWhitelist`。

> **铁律（必记）**：结构可以变，字段不能凭空没。**只做 reorder，不做 select。**
> ① 写完先跑 `check_preserve`（顶层键 / 站点字段 / 透传值三项，不通过即 `exit 1`）；
> ② 未列名兄弟键经 `_extra` 原样补回——白名单只决定**顺序**，不决定**准入**；
> ③ `prune` 保留 = **通过 ∪ 无法判定**，只有明确失效才删（抖动一次就误删，不可逆）；
> ④ `addto` 段落点**由形态定**：采集/直链→`sites`、m3u8/m3u//live→`lives`、
>    `parses` 仅显式 `--type` 才落（它是解析器不是线路，绝不自动落）。

## 3.5.3 ★ 分母不是答案：空输入/非外链都不许装成结论（S144 M-R102 · S147 扩）

`livecheck --batch` 旧版只读 `api` / `url`，而全线用的是 **`api接口`**。字段读不到 → 空串 → 拿空 URL 测活
→ **一律「无法判定」**。实测：6 条真源修前 **0 通过**，同批 URL 直连 **HTTP 200**，修后 **6/6 通过**。
最毒的是表现与「源真失效」一样——好源被自己的代码判死。

> **判据（必记）**：**取不到 ≠ 目标不可达**；空输入必须单报错，不许装成网络失败。
> 兼容键 `api接口|api|url|link|地址`；**拆得出 ≠ 采得到**（jsm 164 条里 150 条非外链，
> 只有 `http` 进候选，其余逐类给原因且**总数守恒**）；`--no-probe` 报「未测」而非「通过 0 条」。

## 3.6 ★ 作者原仓库取用 SOP（S097）

> 用作者原仓库做自己的 json——先验仓库活、可远程拉取，再验线路活，剔除失效留备用。
```bash
# ① 仓库活体 + 可远程拉取（search verify / monitor check）
tvbox probe search verify --repo https://github.com/<owner>/<repo>
tvbox probe monitor check  --repo https://github.com/<owner>/<repo>

# ② 拉原包 → 逐条真实测活 → 剔除失效 → 产「备用」版（一条命令）
tvbox probe harvest prune --url <作者原包URL> --outdir <目录>
#   产出：<原包名>_备用_<MMDD>.json      ← 失效已剔除，可直接用
#        <原包名>_剔除清单_<MMDD>.json  ← 逐条结论（明确失效 / 待复测）

# ③ 把通过者写入成品源库
tvbox probe harvest promote --in <候选.json>
```

铁律：**先验仓库活 → 再验线路活 → 才剔除**；剔除逐条给结论（不静默丢弃，M-R32）。

## 3.7 ★ 分类完整性检测（S101 — "点分类是空的"两坑之一）

> 正本已外置：[`references/S101_分类完整性检测.md`](references/S101_分类完整性检测.md)（M-R77 法①）。

**两坑**：①父分类空壳（`ac=detail&t=1` 返 `total:0`，子分类才有数据，且 API 不返 `type_pid`）
②分类树拿不到就谎报"无分类"。

**判据**：`livecheck --kind class` 真请求 `?ac=class`；空壳**必须靠实测探针判**，不许靠字段推断；
拿不到分类 = 无法判定，不许折叠成"没有分类"（M-R60）。

## 3.8 ★「原仓库 → 轻量 json」链路（S109·S113 挂载决定论·S113b）

> **核心判据（挂载决定论）**：能不能用作者的线路，取决于**作者把对应资源挂没挂出来**（相对/csp_/绝对三类判定、SPA 假活坑、五步流水见正本）。
> 📄 完整推演见 [`docs/外置_原仓库转轻量json链路.md`](docs/外置_原仓库转轻量json链路.md)（M-R77⑥ 法①：同源抄本已外置，主文件只留判据主干+索引）。

## 3.9 ★ 采料去向：直链系换路线业务链（S109·三系口径 1025）

> 正本已外置：[`references/S109_采料去向与D包换路线.md`](references/S109_采料去向与D包换路线.md)（M-R77 法①）。
> 采料不是为了"库里有数"，而是为了**给直链系换掉失效线路**。
> **三系口径（1025 用户拍板，废弃三包 L/G/D 设定）**：

| 系 | 判据（机读可判） | 靠什么活 | 测活闸 |
|---|---|---|---|
| **csp 系** | `api` 为 `csp_*`，或包/站级 `spider` 指向 jar/dex | **jar 真身**（同枝才可平移，枝=jar md5） | 下载 jar→解 dex→确认含 `csp_` 后缀同名类 |
| **直链系** | `type=1` 且 `api` 以 `http(s)://` 开头，零 jar 零 spider（含 CMS 采集站与 m3u8 直链） | **直链本身** | 真拉 `vod_play_url` / XML 确认可播 / m3u8 分片 >0 字节 |
| **脚本系** | py 源、drpy js 源、播放页壳、解析器（parses）、判不出者 | 各自宿主环境 | py 五步真跑 / 壳抠链分片>1KB / 解析出流 rounds=3 |

> 三系与 A-E **配置形态**（A=CatVod/B=加壳/C=无jar/D=Amns/E=待建）**正交不互替**：
> A-E 说「配置文件长什么样」，三系说「每条线路靠什么活」。判不出者落**脚本系**标「待观察」，不硬塞（M-R60）。

**硬约束**（判据，改这里）：

- **不拉流验证，不交付**（S097 用户铁律）；
- 直链判据 `api.startswith('http(s)://')`，与 `split_box.py`/`split_jsm.py` 同口径（M-R38）；
- 进直链系的直链**必过测活闸**——失效直链进包就是直接"转圈"。

> 承载铁律：`M-R66`（采料去向锚定）+ `P-R1`（合格源判据）+ `M-R65`（真实数据驱动）。

## 3.10 ★ 三条"假绿"缺陷定案（S143）→ 已外置

> 详见 [`references/S143_三处假绿定案.md`](references/S143_三处假绿定案.md)（M-R77 法①）。
> 判据错法各一：评分恒 60=有公式没验分层；上云=看回执不看字段；删除=看本地不看云端。
> 病根同：拿"看起来对"代替"验过是对的"（S169「上云」栽了第二次，见 §3.13 ①）。

## 3.11 ★ 质量评测：画质 / 速度 / 广告（S110）

> 用户原话：「数据源质量如何把控，比如播放速度，广告，画质，能把这个做进去闭环么」
> **正本**：[`references/S110_质量评测.md`](references/S110_质量评测.md)（M-R77⑥ 法③）含评分模型 v1→v3 两次校准矩阵、三条硬限制论证、四个解析坑症状表。

`livecheck` 只答**能不能拉到播放地址**，不答**拉到之后好不好用**。「假通过」大量存在：
接口 200、地址非空，真拉 m3u8 却 403/超时；或能拉但只有 480p、首片 8 秒、中间插广告。

| 脚本 | 作用 |
|---|---|
| `scripts/quality.py` | 单条：拉主清单 → 读 RESOLUTION → 拉首分片测耗时与速率 → 扫广告特征 → 合成质量分 |
| `scripts/quality_batch.py` | 接两库：读库 → 取播放地址 → 8 并发评测 → 回填 6 字段 |

**回填字段**（两库同名）：`最高画质`/`首片耗时`/`下载速率`/`广告判定`/`质量分`/`质量证据`。
**评分 v3**：画质是门槛、速度只加减分。三条硬限制与四解析坑见正本——**它们都会让「好源被判死」**。

## 3.12 ★ 写源模块（S161·P018 · `probe forge`）

> 正本已外置：[`references/S161_写源模块.md`](references/S161_写源模块.md)（M-R77 法①）。

第 6 个业务模块：前五个管**别人做好的源**，这个管**自己要造的源**。

```bash
tvbox probe forge verify <源.py> [--save]   # 验源；--save 则「通过」后留档到 sources/
tvbox probe forge list                      # 留档清单 + 索引/磁盘不一致项
tvbox probe forge classify <api>            # ★实测判源类型（CMS采集/m3u8直链/JSON配置/无法判定）
tvbox probe forge audit                     # ★留档审计：七项闸门查账实一致性（S194）
tvbox probe forge profile <url>             # ★站点画像：特征扫描→防御等级 0-4→推荐探源司命令（S194）
```

**四条铁律**：①没验过的不许进 `sources/`（`失败`/`无法判定` 一律拒）；②正、负两面账都要清；
③不做「配置驱动自动生成源」（遮天法 v3.1：10 项能力 7 项无调用点——**诚实边界不是缺口**）；
④**判源类型只看响应行为不看 url 路径**（按路径猜 5 条错 4 条）。详判据与留档格式见正本。

**⑤ 审计不过先修账（S194）**：`forge audit` 七项闸门——索引可读（坏=致命 exit 2）/
file 唯一 / 四要素齐全 / 双向对账（孤儿=WARN、幽灵=FAIL）/ 内部计数自洽 /
在册必须「通过」（铁律①的机器判据）/ verified_at 可解析。**FAIL→exit 1、WARN→exit 0**。
`forge profile` 判据移植自道长 39 项特征表，`modules_for` 改造成 **next_steps**（推荐探源司自己命令，
画像才接得上执行链）；画像只答"好不好采"，**能不能采一律以实测为准**（M-R60）。

### 3.12.1 ★ 写源规范正本（S195·从 DsPlayer 壳内提取）

> 正本：[`docs/外置_写源规范正本.md`](docs/外置_写源规范正本.md)
> （含三份作者亲写规范的用途对照、未拆完部分、`forge` 三条硬判据、drpy3 与 py 源的边界声明）。

**三条硬判据（速查，详版见正本）**：① 能声明的全部声明——`rule` 覆盖五件事，
只有真需写代码才上钩子；② 钩子优先、缺省回退；③ `js:` 片段内是 drpy2 老写法（`request()`）。

> **边界**：正本描述的是 drpy3 宿主环境（QuickJS）；`forge verify` 只验 py 源，**不验 drpy3 js 源**。

## 3.13 ★ 静默缺陷六例（S169/S170/S171/S172）

> 正本：监测台 → [`references/S169_监测台静默缺陷.md`](references/S169_监测台静默缺陷.md)；
> 黑名单误杀 → [`references/S169_黑名单误杀三根因.md`](references/S169_黑名单误杀三根因.md)；
> 网盘取料 → [`docs/外置_网盘分享取料.md`](docs/外置_网盘分享取料.md)（四判据全推演 + 412 对照组 + 接口存活表）。

**共同形态：不报错、退出码 0、结果一直不变**——不是"功能坏了"，是"空转"或"归因错位"。

| # | 缺陷（证据与推演见正本） |
|---|---|
| ① | `上次检查` 写不进云端：列是 `date`、代码按 `text` 写，回执照样 `success: true` |
| ② | 存活只看仓库主页 → **监测面缺口伪装成健康度** |
| ③ | 黑名单按"失败几次"判死，不问"为什么" → 证书过期也被判死 |
| ④ | 网盘 412 并进「下载失败」 → **坏 token 也是 412**＝拦截在验签之前 |
| ⑤ | 探针**自己判自己通过**（连犯三次）→ 判据字面量写在探针同文件里 |
| ⑥ | **判据没落在执行路径上**（15 探针首版 6 条是假的） |
| ⑦ | 外部材料自带的 `self_test` 不能当证据（`except ImportError: pass` + 无条件 ✅） |

> 七例的实测数字、对照组、逐条推演 → [`docs/外置_静默缺陷七例.md`](docs/外置_静默缺陷七例.md)

判据：存活按 `API ok`→存活、`notfound`→已封禁、`限流/失败`→才退回主页；时间列走
`_cloud_upsert(date_cols=...)`；证书类封顶 L2；**不可达类失败单列、带成因、给处置阶梯**。
`self-check` 五条命门探针锁死。**验收：回读 ≠ 回执；判定要说清凭什么；归因错位比不报错更毒。**

> **⑤ 比前四条更阴**：前四条是代码空转，⑤ 是**校验空转**。**写探针先问：把目标改坏，它还亮吗？**
> **⑥ 是 ⑤ 的续集**：六变种逐条正解见 [`docs/外置_判据必须落在执行路径.md`](docs/外置_判据必须落在执行路径.md)。
> **⑦ 上手第一件事**是用构造数据证伪，不是跑它的自检看绿灯——
> 判定与处置见 [`references/S195_吸收材料结案.md`](references/S195_吸收材料结案.md)。

> **三条硬要求**：①取自**执行路径**不取自说明书（报错文案里的字不算）；②能行为测就行为测；
> ③不许查**一个自己会说的话**（提示语里写着"不许报归因 0 条"，那串字本身会命中探针）。

**`pan` 另两条判据**：① 只认 `/s/<id>`（用户贴的整段文案含口令 `/~xxx~/` 极像 id，口令单独输入返回空）；
② **`format_type` 判改名包**——实测 4 个 `.apk` 里 2 个原生是 `application/zip`，`.apk` <100KB 必预警。

## 3.14 ★ 网盘分享取料（S170·`probe harvest pan`）

> 判据见上表 ④；正本 [`docs/外置_网盘分享取料.md`](docs/外置_网盘分享取料.md)（含 S171 MSITEPK2 全解 + 密钥取法 + 三类分发形态）。
> `pan info` 只走 API，`pan pull` 才碰 CDN；`pak` 解整站容器。

## 3.15 ★ 解析接口：采集与真实出流（S173·`harvest parse` / `livecheck --kind parse`）

> **解析库的原料不从天上掉，从别人已发布的包里捞。** 但捞出来能不能用，得真跑。
> 断点实测：两库 732 条**解析 0 条**——`scan.py:55`/`run.py:104` 把 `parses` 塞进 rows 后就没了下文。
> 四条判据被实测推翻过的全过程见 [`docs/外置_解析接口采集与出流.md`](docs/外置_解析接口采集与出流.md)。

```bash
tvbox probe harvest parse --repo <仓库URL>    # 抽 parses → 去重 → 真实出流闸门 → 候选落盘（不入库）
tvbox probe harvest parse --url <配置直链>
tvbox livecheck --kind parse <解析地址> [--video 测试影片页] [--wait 秒]
```

**判据归属**：出流＝`livecheck.check_parse`（唯一实现），`harvest parse` 只调不复写（M-R38）。
**三态**：无流＝**失败**（确定结论）；无浏览器/超时＝**无法判定**（不算它死）。
**稳定性**：`check_parse_stable(rounds=3)`。实测「闪电」4 测 3 过＝抖动型，单测会骗人；
⚠️ **3 轮抓不住全部抖动**（有过三轮全过其实仍抖）——能力边界，别当已解决。

## 3.16 ★ 壳源侦察与抠链（S196·`probe shellcrack`）

> **正本已外置**：[`references/S196_壳源侦察与抠链.md`](references/S196_壳源侦察与抠链.md)（M-R77 法①）。
> **第三类料源**：`ac=detail` 给的是**播放页壳**而非 m3u8——壳内 JS 变量藏真直链。

```bash
tvbox probe shellcrack probe <api> --name X [--save]   # 形态分诊：直链站/壳/API死，是壳当场抠
tvbox probe shellcrack crack <页面URL> [--save]         # 抠单壳 ｜ scan 批量 ｜ status 壳库回读
```
**判据主干（必记）**：①金律正则顺序不可换（`const url` 前置，防 vid 抢匹配）；②壳内/master/分片
一律 `urljoin`（`base+` 拼接 404）；③「已抠」必须真拉分片 >1KB；④三态含「待抠＝非CMS模板」不硬凑；
⑤入库播放页壳库后必须 readback；⑥「接口给壳」混合形态（新浪 xlyun）进不了 type=1 json，不硬进。

## 4. 扩展点

- [x] 更多平台（GitLab/Gitea/工蜂）；变更通知进 watchdog（M18）；活跃度预测 `monitor forecast`；
      `splitjsm` 复源枢库曹正本（S147，164→外链14、非外链150不进候选）；写源自验 `livecheck --spider`（S148）；破甲 `probe decrypt`；网盘 `harvest pan`（S170）；单文件容器 `pak`（S171）


## 5. 联动（数据流）

```
monitor  存活异常 ──► 状态回写 ──► (若派生源已死) 复用 blk 口径写 DB_DEAD
         内容/目录变更 ──► 变更摘要 + activity_log.json（数据基座·三态守护）
search   有效仓库 ──► search_results.json ──► 高分转 monitor add
harvest  候选 ──► harvest_candidates/harvest_MMDD.json ──► tvbox ingest
                └─► blk.py guard 拦截(命中→DB_DEAD) → 放行→link.py classify→DB_SRC → snapshot
         ★ 采料去向：http(s) 直链 ──► livecheck ──► harvest promote → DB_PROD（成品源库）
                                       └─► 用户据成品源库给 **直链系线路池**换路线（三系口径 1025；D 包=历史别名）
```

- **上游**：`/root/.codebuddy/skills/library`（云读写）+ `tvbox-source-hub/scripts/core.py`（共享效率核心）
- **下游**：`tvbox-source-blacklist`（质检闸·guard 拦截）、`tvbox-source-hub`（源枢库曹·ingest 入库）
- **横向**：`legacy-watchdog`（遗迹哨·监测上游遗留）、`ecosys-conductor`（总指挥·信号源18 守护）

## 6. 升级契约（规则 48 + 54 三件套）

- 顶部 `version` + 本 `CHANGELOG.md` + `scripts/flow.json`（机器可读流程）三件套齐备。
- 版本提升：`tvbox evolve upgrade probe-hub`（自动 bump + 同步存在性登记 + 追加 CHANGELOG）。
- ⚠️ **本技能非 `tvbox-` 前缀** → `evolve scan` 靠目录扫描**扫不到**，**必须** `register_eco` 登记到生态库（DB_ECO）才会被进化引擎发现。

## 7. 关联

| 项 | 值 |
|---|---|
| 共享核心 | `/root/.codebuddy/skills/tvbox-source-hub/scripts/core.py`（同 §5 上游） |
| 源库 DB_SRC | 已收口至 `core.SRC_DBS`（**本技能不读源库**；线路源库 `pNwZ9uBOTz6FzRuvu8nryI` / 成品源库 `8JBrhSwsJ3pnPYtoGLo9XV`） |
| 成品源库 DB_PROD | `8JBrhSwsJ3pnPYtoGLo9XV`（S160 由「成品源库」更名；取自 core，勿写死） |
| 失效源库 DB_DEAD | `92dSuG6uUbxitGuyx4Saaf` |
| 生态登记库 DB_ECO | `T4kODtANe7rSrtZTu4sIND` |
| 上游监测库 DB_UPSTREAM | （下轮建：用真实数据验证 18 字段 schema 后创建） |
| 监测目标登记册 | `scripts/monitor_targets.json`（本轮承载；下轮迁 DB_UPSTREAM） |
| 采样史数据基座 | `scripts/activity_log.json`（三态守护，损坏拒绝回写） |
| 闸门 / 入库 | `tvbox-source-blacklist/scripts/blk.py` · `tvbox-source-hub/scripts/link.py`（cmd_ingest）（同 §5 下游） |
| 测活口径 | `tvbox-interface/scripts/pipeline.py`（`_alive` / `ALIVE_CODES`） |
| 云规则 | 生态1015_TVBox生态全局规则（M-R55~M-R60 监测器铁律） |

## 8. 历史专项脚本归档（C5 · 2026-1027 生态对齐治理）

以下脚本经 grep 确认**零运行时依赖**（不被任何运行代码 `import`），属 S117/S173 历史专项遗留，
已移至 `scripts/_legacy/` 归档，**保留可回溯、不删除**（T8 处置，不影响现有功能）：

| 脚本 | 来源 | 用途 | 失效/遗留原因 |
|---|---|---|---|
| `retry_lives_parses.py` | S117 | 复测直播 parse 出流 | 硬编码已失效路径 `/workspace/原仓库库/cluntop_box原文_1018.json` |
| `rebuild_b1018_s116.py` | S116 重建 | 以作者原配置为基线分层重建 | 同上硬编码 + 硬编码本次会话 artifact 路径（换会话即失效） |
| `rebuild_from_origin.py` | — | 以作者原配置为唯一基线重建 | 同上 |
| `probe_parse_inject.py` | S173 | parse 档注入回归测试 | 无 `__main__` 守卫，专项回归用，非运行入口 |

> 路径由 `scripts/xxx.py` 变为 `scripts/_legacy/xxx.py`；如需回溯运行，须将其依赖的 `livecheck` 等模块加入 `sys.path` 或调整 import。
