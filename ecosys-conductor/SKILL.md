---
name: ecosys-conductor
version: "1.79.13"
description: "TVBox 生态指挥家——**需求管家（单一入口：术语翻译+历史呼应+技能路由+需求拆解，读术语库把黑话 G/L/D包翻译成标准，读事件库告诉你"以前做过什么"）**+会话提速（并行分道/sprint）+技能分工（意图路由/分工地图/仲裁）/**规则纳管（132 条规则单一事实源 rules_registry：归谁守/落地了没/探针是什么）+执行层门禁（~/.codebuddy/hooks/ 下 9 个会话钩子的判据设计与自测，M-R134 判据必须测目标本身）**+全局进化（五维评估/证据驱动方向/留痕对账）。 【何时必须用·业务语言】用户说下列任一即触发（不必出现术语）：之前做过什么/我以前是不是弄过/这活归谁管/该找哪个技能/这条怎么又犯了/上次怎么解决的/按老规矩来/先做什么后做什么/帮我排个顺序/这事怎么拆/有没有现成流程/这次别又白做/该不该继续/是不是该换方向了。 触发词：该找谁 / 这活归谁 / 我以前弄过吗 / 之前做过什么 / 上次怎么解决的 / 按老规矩来 / 先做哪个 / 帮我排个顺序 / 这事怎么拆 / 有没有现成流程 / 别又白做 / 该不该继续 / 是不是该换方向 / 下一步做什么 / 需求 / 你该找谁 / 我以前做过什么 / 历史 / 这条规则归谁 / 规则落地了吗 / 孤岛规则 / 规则纳管 / 生态指挥 / 并行推进 / 任务提速 / 技能分工 / 协调技能 / 生态评估 / 进化方向 / 全局调度 / 联动 / 备份 / 三层备份 / 恢复 / 还原 / 回滚 / 白做 / 全系联动 / 生态库 / 资料库 / 云端备份 / 归谁管 / conductor / 提速 / 并行 / 分工 / 协调 / 调度 / 找谁 / 哪个技能 / 白做账本 / 备份区 / 瘦身 / 精简 / 体积 / 超40KB / SKILL太大 / 规则太多 / 规则精简 / 钩子 / 门禁 / 拦截 / 误伤 / 判据 / 这个钩子没生效 / 又被拦了 / 怎么没拦住 / transcript / hook / 任务状态 / 任务进程 / 到哪里了 / 未完成 / 经验池 / 增加规则 / 全局规则 / 硬性规则 / 规则固化 / 规则册 / 规则总册 / 开工必读 / 入册 / 治理闭环 / 纳管 / M-R。"
---

# ecosys-conductor（生态指挥家）

> 不做事，只让所有事更快、更准、更协同。
> 四层能力：**L0 需求管家（单一入口）** / L1 会话提速 / L2 技能协调 / L3 全局进化。

## 0. 需求管家（单一入口·S096）——**用户只需对它说话**

> ### ⚡ 会话开工三件事（M-R120 开工门禁 · 原 M-R112 已于 T27 退役并入本条）
>
> ```bash
> cd /root/.codebuddy/skills/ecosys-conductor
> python3 scripts/habit_brief.py show      # ①读习惯摘要：中文铁律/**超1分钟命令必后台**/短代号/工作偏好/模型约定
> python3 scripts/habit_brief.py check     # ②体检摘要是否与源数据一致（过期则 gen 刷新）
> cat references/_handoff_latest.md 2>/dev/null   # ③若有交接摘要，先接住上文任务与未决问题
> ls /root/.codebuddy/skills/eco-ledger 2>/dev/null && echo "[技能已就绪]" \
>   || { echo "[技能缺失·自动按教程部署]"; cat /root/.codebuddy/skills_deploy_guide.md 2>/dev/null \
>        || sed -n '/技能部署教程/,/^## /p' /root/.codebuddy/memory_core.md; }   # ④技能自检+按教程自助部署
> ```
>
> **④ 技能自检（干净沙箱/新会话报"找不到 eco-ledger / 技能 / 执行开工必读"时触发）**：
> 探测 `/root/.codebuddy/skills/eco-ledger`，缺失则**自动**按 `skills_deploy_guide.md`（或 `memory_core.md` 第六节）
> 把技能部署到位，**不问用户、不造通道**。三来源任选、自动试错：①公开仓库 `git clone --depth 1
> https://github.com/tvbox342078879/tvbox-skills-eco.git`（免令牌，先试）②克隆超时改 `curl -fsSL
> https://cdn.jsdelivr.net/gh/tvbox342078879/tvbox-skills-eco@master/skills_lean.tar.gz`（jsDelivr 备选）
> ③私有换机备份 `DmaxPro908`（需 `.gh_token`）④用户经 App 上传 `/root/uploads` 手动解压。
> 部署完跑 `python3 /root/.codebuddy/skills/ecosys-conductor/scripts/habit_brief.py` 验证。
> 全网络不通才提示用户手动上传——这是"让他自己部署"机制化，与记忆/规则是两条独立恢复链。
>
> **为什么**：语言要求已复发 5 次（`pref_zh_chat`），根因是"规则写在册子里、模型看不见"。
> 本摘要是**模型无关**的习惯载体——换快速/Hy3/深度思考/未来新模型，读它即对齐，不必用户重教。
>
> ⏱️ **第 2 节 M-R120 后台门禁**：预估 >60s 的命令一律 `run_in_background` + 输出落盘，
> 启动后立刻回报「跑什么/多久/我接着做什么」。前台长跑会让用户既看不见进度也插不进话（S176b 实证）。
> **输出纪律**：全程简体中文（含思考面板、进度播报、报错），不得因换模型回退英文。
>
> 📄 **第 0 节 M-R128 交付报告七段闭环**（摘要里排在语言铁律之前，这里同步一份，防摘要过期未刷新）：
> 任何交付报告／复盘／验收报告／治理回溯，一律按 **①原因 ②过程 ③过程坑 ④解决方案
> ⑤后续带出的问题 ⑥后续问题解决过程 ⑦完工经验总结** 七段写，顺序固定。
> 硬性四条：③ 必须含自己造成的坑（不许写"一切顺利"）；⑤ 确实没有也要明写「无」（不许省略该节）；
> 未解决标 `open`（不许写成已完成）；⑦ 写成判断句「X 情况下必须 Y，因为 Z」（不许写感想）。
> 文件名须含 `MMDD`（规则51）。模板：`references/交付报告_七段模板.md`｜探针：`rule_probe.py M-R128`
>
> **M-R128 已登记 `rules_registry.json`（第 141 条，`tier=enforced`）**，可查：
> `python3 scripts/conductor.py rules M-R128` ｜ 按用户口语找：`rules --ask "交付报告要写哪些部分"`
> **痛点（S096 复盘）**：技能各有触发词、无"用户业务记忆"，用户黑话（G/L/D包）无处安放，
> 用户被迫"先知道找哪个技能"。**本层是总服务台**：用户说什么都行，它负责翻译+路由+拆解。

```bash
tvbox conductor ask "<你的需求，随便怎么问>"     # ★单一入口
```

它做三件事：
1. **术语翻译**：读云端「用户术语库」（DB_TERM `MrJdROzysspCyWo0L3F32z`），把用户黑话
   （G包/L包/D包/点播/直播…）翻译成系统标准实体——**跨会话持久，不再压缩即丢**。
2. **历史呼应**：读云端「需求事件库」（DB_EVENT `kqNrwHd53K9qr1DFBUPglZ`），把当前需求和你
   **以前做过的事**对上——"这个我做过吗、当时交付了啥"，避免重复劳动、避免"新建对话就失忆"。
3. **技能路由 + 需求拆解**：技能级（谁负责）+ 命令级（跑什么）+"该找谁 / 可直接跑的命令 / 下一步"。

**查历史**（"我以前做过什么"的专用入口）：
```bash
tvbox conductor history                 # 全部成功清单（按主题分组）
tvbox conductor history "做json"         # 按关键词过滤
```

**术语库维护**（发现新黑话时写入，这是"记住用户说的话"的唯一入口）：
```bash
python3 skills/library/database/batch_add_database_records.py --database-id MrJdROzysspCyWo0L3F32z \
  --records '[{"术语名":{"text":"X包"},"别名":{"text":"x包"},"标准定义":{"text":"…"}, \
               "对应实体":{"text":"…"},"类别":{"select":"底包体系"},"来源":{"text":"用户原话"}, \
               "更新时间":{"text":"YYYY-MM-DD"}}]'
```

## 1. 使用入口

```bash
tvbox conductor <命令>            # 统一入口（tvbox CLI 已注册 ROUTE）
python3 scripts/conductor.py <命令>   # 直跑
```

## 2. 命令集（三层 12 个）

```bash
# L0 需求管家层（S096·单一入口）
tvbox conductor ask "<需求>"        # ★术语翻译（读云端术语库）+ 历史呼应（读事件库）+ 技能路由 + 需求拆解
tvbox conductor history ["<关键词>"] # ★需求成功清单：查"用户做过什么、当时交付啥"（读事件库）

# L1 会话提速层
tvbox conductor plan "tvbox brief" "tvbox watchdog scan --upload" ...  # 并行分道规划（依赖图+提速比）
tvbox conductor sprint "任务1" "任务2" ...   # 冲刺执行：产出可粘贴的 tvbox p / run 串
tvbox conductor speed <串行当量> <实际耗时>   # 提速度量（<1.5x 提速不足）

# L2 协调调度层
tvbox conductor whoami "<意图>"    # 意图→技能路由（查 manifest，弱匹配）
tvbox conductor lanes              # 生态分工地图（管什么/不管什么/重叠检测）

# L3 全局进化层
tvbox conductor assess             # 五维评估（契约/版本/三件套/业务面/活跃度）
tvbox conductor direction          # 进化方向（证据驱动：backlog+生态空白，留痕对账）

# 第二批（v1.1.0）
tvbox conductor conflict "任务1" ...  # 写冲突仲裁（同目标多写→定序建议）
tvbox conductor handoff <A> <B>       # 交接契约检查（contracts 对读）
tvbox conductor expand-eco            # 生态空白雷达（负载过载/地图外实体）
tvbox conductor retro [要点]          # 会话复盘四问（内环：教训→经验库）
tvbox conductor evo-check             # 采纳率对账（兑现才算进化）
tvbox conductor waste                 # 白做账本（S124：投入错配/返工率/闭环命中，常设度量）
tvbox conductor threads list          # ★任务线看板（M-R140·S196）：多线并行必登记，问「完成没」先对线
                                      #   open <摘要> 开线报号 ｜ fork <父线> <摘要> 分叉挂父
                                      #   done <线号> <证据> 收口 ｜ list 一屏全景（在途/完成/父子溯源）
tvbox conductor backup [--dry]        # 三层联动备份（生态→资料→云端，manifest sha256 对账）
tvbox conductor restore [--apply]     # 云端→生态恢复通道（校验+云端拉回，--apply 真恢复）
tvbox conductor linkmap               # 三库全系联动对账（两两+整体，一屏健康）
tvbox conductor issues                # 问题总账（复盘遗留零漂没：add/close/wontfix，M-R73）
tvbox conductor sync-version <ver>    # 版本四面一键同步（M-R71 工具化，CHANGELOG 缺条目拒升）
tvbox conductor eco-sync              # 把本地 SKILL.md 版本批量回写云端生态库（S162）
                                     #   写前反向核对 id→技能名，对不上整批拒写（防"写错行"）
                                     #   写后必须回读；回读前须 invalidate（core 有 rows/api 两层缓存）
tvbox conductor drift-fix [--dry]     # 【版本漂移一条命令闭环】体检→需修才修→复检，以复检定 rc
                                     #   为什么不直接用 eco-sync：那条只管"回写"，
                                     #   人要记得 体检→回写→再体检 三步，忘一步就停在半路且无人报警
                                     #   本地两列自相矛盾时不修（那是写错，改了会掩盖），只点名并 rc=1
tvbox conductor onboard <技能名>      # 【新增技能必跑】八面对账（M-R78+M-R84）：清单/话术/黄金用例/身份/manifest/派生消费方/三件套/收尾绑定 一次补齐
                                     #   --dry 只检查不写 ｜ --words "词1/词2" description 抽不到时手工给
                                     #   漏任一面都不报错，直到发现它从没被用上
tvbox conductor slim <技能名>         # 技能瘦身（M-R77 三法）：--diagnose 看节清单｜--apply "<关键字>" 外置并留索引｜--verify 校验索引
tvbox conductor size [技能名|all]     # 【改完 SKILL.md 立刻跑】体积写后即测（M-R77 拦截点前移）：
                                     #   硬上限 40960B / 缓冲线 35840B，🟡=余量<512B 该先外置再改；超标 exit=1
tvbox conductor field [--all]         # 【改完路由词表跑】第三方口语集真实泛化度量（S157）：
                                     #   top1(单选)/top3(候选前三)两个指标，--all 看逐条。
                                     #   ⚠️ 指标不是判据：掉分不许改表、不许加词迁就（改表换绿=自测自绿）
tvbox close                           # 【收尾必跑】九技能共用统一收尾入口（M-R79·M-R81，finish 同义别名）
                                     #   六源采集→signature 指纹对账→分诊派单(owner/next/due)→落账→四向联动→交监察官
                                     #   子命令：status/mine/done/wontfix/defer/verify/report/promise/link ｜ --dry 只报不写
```

## 2.5 收尾纪律（M-R79~M-R84）

**会话/技能结束时必跑 `tvbox close`**——「没做完的事」只活在上下文里，会话一结束就蒸发。
**收尾不等于只清欠账**：经验必须用 `tvbox close learn biz/plat` 落双池（见 §8）。

- **六源全采**：问题总账 / 会话承诺 / flow.backlog / CHANGELOG 敞口 / 监测项未实证 / 云端待跟进
- **每项三件**：`owner` + `next`（下一步命令）+ `due`。缺一判红，不许无主悬空
- **指纹对账**：`sha1(source|skill|title[:40])[:16]`；上期在册本期采不到的标「消失」并要求显式收口
- **派单真送达**：真写进对方 `flow.backlog`（写后回读），不是打印一行"已派给谁"
- **硬闭环**：`verify` 判据⑧ 直读本账，无主项/未说明消失/未兑现承诺 → 验收不过

## 3. 提速三铁律

- **T1 读写分离并行**：只读打包 `tvbox p` 无限并行，写库串行防锁，上云收尾统一
- **T2 同进程共享缓存**：能用 `tvbox run` 串联的绝不 `p` 并发（避免重复建连）
- **T3 先 plan 后 sprint**：禁止未规划直接开跑（防依赖撞车返工）

## 3.5 大生态 / 小生态：三层一缝（M-R116 · S158）

> **正本**：[`references/eco_layers.md`](references/eco_layers.md)（唯一权威定义，别处不许另立一套）

用户反复强调「大生态、小生态、联动」，这三个词以前只活在对话里——没有定义、没有判定式、
没有归属表。结果每次说要"联动"，落到的都是各技能自己那一块的内部优化。

| 层 | 定义 | 实测（S158） |
|---|---|---|
| **L0 大生态** | 空间全部节点 | 439 |
| **L1 小生态** | 各技能 SKILL.md **声明拥有**的子树 | 124（28%） |
| **L2 技能内部** | 技能自己的库/文件/SOP（计入 L1） | — |
| **缝** | **L0 − L1 的差额** | **315（71%）** |

**判定式只有一条**：节点不在任何技能的 `TERRITORY` 里 → 它就是缝。
**缝不是"乱"，缝是"无主"**——乱可以清，无主必须先派人。

缝的五类型（治理抓手）：S1 无主区 / S2 重复堆积 / S3 孤儿产出 / S4 产出堆场 / S5 死坐标。

三条强制用法：
1. **一条命令查归属**：`tvbox janitor census`（人读）/ `census --json`（机读）
2. **新技能 onboard 必填责任田**：不填 = 它的产出天然成为缝
3. **每轮治理前先看分区排名**，按 zone_registry 的 `why` 分类动手，不逐条打补丁

> 归属表正本：`scripts/zone_registry.json`（14 区覆盖全部 315 个缝节点，11 已指派 / 3 待用户裁定）。

## 4. 与邻居的边界（防职责重叠）

- **vs `tvbox-inspect`（监察司·S105 新增）**：**监测/自检入口已统一到监察司** ——
  `tvbox inspect signal-check|verify|assess|scan`。conductor 保留 `verify`/`signal-check`/`assess`
  作为**总指挥对生态的终检**（属调度层职责，且深度耦合评分器）；对用户侧，**监测意图统一路由监察司**。
  分工：监察司=**发现层**（盯）｜遗迹哨=**处置层**（修）｜总指挥=**调度层**（派）。
- vs `legacy-watchdog`（遗迹哨）：watchdog 监测**业务遗留**并评「技能自身质量」，conductor 评「生态整体与方向」
- vs `skill-evolution`：evolution 处理**触发式单技能进化**（同类问题≥3次），conductor 做**周期性全局评估与方向规划**
- vs `tvbox chain`：chain 是**静态步骤链**，conductor 是**动态调度决策层**（调用 chain/run/p 作为执行原语）
- **自有写目标仅一个**：`plans/evolution_plan.json`（进化留痕），其余只读——不越权

## 5. 自我进化机制（三环）

1. **内环（每次会话）**：提速/协调教训写入经验库（`tvbox lib-log`），同类 ≥3 次由 skill-evolution 触发升级
2. **中环（每轮扩展）**：flow.json backlog 登记扩展点，下一轮兑现
3. **外环（全局周期）**：`direction` 建议留痕 → 下期 `assess` 回验兑现率——**建议没有闭环对账不算进化**

## 6. 数据契约

- 上游只读：`evolve scan`（契约）、`manifest.json`（路由）、`watchdog scan/expand`（质量/空白）、`snapshot`（活跃度）
- 下游输出：`plans/evolution_plan.json`（进化建议，含日期/技能/类型/证据）


## 7. 铁律坐标（M-R / U-R / P-R · S108）

> **本技能守护哪些铁律**：明细表已外置 → [`references/rules_铁律坐标明细.md`](references/rules_铁律坐标明细.md)
> （共 72 条落地说明）。机器可读版在 `scripts/rules_registry.json`，那是**权威源**；
> 外置表是"本技能怎么落地"的注释，两者由 `rules check` 做三方一致校验。
> 
> **最容易踩的四条（正文保留，其余外置）**：
> · **M-R38 单一事实源**——同一判据只写一次，其余派生；发现两处各维护即属违规。
> · **M-R60 禁折叠**——读失败必须是"无法判定"，不得 `except: return []` 装成"没有"。
> · **M-R76 触发词单一真源**——`_SKILL_INTENTS` 是 description 的派生投影，禁两处各写。
> · **M-R77 SKILL 体积 40KB 硬上限**——超限须外置长条，本节的搬迁就是这条的执行。


## 7.5 规则纳管（单一事实源 · S109）

> **正本**：[`references/rules_纳管细则.md`](references/rules_纳管细则.md)（含 ask 写法、打分口径、验证教训）。
> 单一事实源 = `scripts/rules_registry.json`。三问：**归谁守/落地了没/探针是什么**。
>
> ```bash
> tvbox conductor rules [编号] [--orphan|--owner X|--dup]
> tvbox conductor rules check | relapse | --ask "用户原话" | --ask-coverage
> ```

## 8. 会话经验沉淀（规则 58 · 每次会话强制 · S108）

> **规则58**：**每次会话收尾必须**把「坑/解法/可复用点」写入双经验池；无坑也记
> 「本轮无新坑」，**禁止静默收工**。

| 池 | 库 | 编号 | 记什么 | **写入口** |
|---|---|---|---|---|
| ③ 会话台账 | `TO4LwsjVVdR8JRAUlkejk3` | `S0xx` | 业务经验（本轮做了什么/产出什么） | `tvbox close learn biz` |
| ④ 资料库经验库 | `XTuuj8mKtqnZSolDdeDxkq` | `L0xx` | 平台经验（踩的平台坑及绕法） | `tvbox close learn plat` |

> **写入器一直都在**：`tvbox log` / `tvbox lib-log`（`tvbox-source-hub/scripts/link.py`），
> 内部是 读库续号 → 类型按 schema → **写后回读**。`tvbox close learn` **只做转接**，不复制实现（M-R38）。
> ⚠️ S148 曾误判"全生态 0 处代码写经验池"（grep 判据取错地方），并据此误建 `learn.py`（已删）——
> 教训见 M-R105：判"有没有执行器"要去跑命令，不要 grep 变量名。
> 守它的：信号源 22（留痕缺口）+ 34（防命令变幽灵）。

**四条判据**：① 收尾必写（③ 记业务、④ 记平台坑）② 编号续接（读库取最大号续接，不重号）
③ 无坑也留痕（无新坑 → ③ 记「本轮无新坑」）④ 写后回读（T-I 验证条目已落库）。

**守护**：`conductor signal-check` 信号源22「会话经验留痕缺口」——双池最新时间落后工作锚点
超 **4 小时**即报遗留；要求「每次会话」而非「每月别漏」。
另有**空时间单列**：条目在但 `时间` 为空 → 单独报「已沉淀但无法计时」（空时间 ≠ 及时）。

> S158 十补：阈值原为 1 天 = 24h。一轮工作约 2h，于是同一天里漏多少轮都静默——
> 实测锚点 14:21、③停 13:56、④停 11:21，两轮没进池，检测器返回空。
> 它只能抓"S108 那种漏 30 轮"的灾难，抓不到"今天又漏了"。现收到 4h。
>
> 让规则58 **从人自觉转为机制守护**（S108 实证：曾静默遗漏近 30 轮）。

## 8.5 会话提炼（M-R117 · 每次会话强制）

> **M-R117｜会话不产出则不许结束**：每一轮会话收尾必须交代**三样实物**——
> ① **落点**：哪些文件 / 库 / 规则被改了，**路径写全**（`skills/xxx/scripts/yyy.py:123`）
> ② **证据**：跑了什么命令、看到什么输出。**引用要能与记录逐字对上**
> ③ **没做什么**：明确排除项 + 原因（"这条查了，只记账不修"）
> 三样缺一即判「**本轮无产出**」，禁止用「已完成 / 已处理 / 搞定了」收尾。

**四条禁止**：

| # | 禁止 | 为什么单列 |
|---|---|---|
| 1 | 把「分析过 / 看明白了 / 确认了一下」算产出 | 没有命令、没有输出 → 回查时**无法复现** |
| 2 | 只写结论不写路径 | "已修复"查不到改在哪一行 |
| 3 | 用估计数值冒充实测 | M-R26 要求实测，估算混进来就分不清了 |
| 4 | 把上游作者 / 外部文档的结论算成自己的产出 | 那是**引用**，不是**产出** |

**与规则58 的分工（关键）**：

```
规则58：经验池写了吗？      → 判「记账动作」
M-R117：这一轮到底做了什么？ → 判「账目内容」
```

两者缺一都会让台账变成**有字段没价值的假账**。实测出现过：③ 台账 110 条，抽任意一条都答不出
「改了哪个文件的哪一行、跑命令看到了什么」。

## 8.6 可对话铁律（M-R120 / M-R121 · 执行层硬拦）

> **M-R120｜超 1 分钟的命令必须后台**。用户原话：「超过一分钟，必须后台跑，不然我都找不到人，
> 并且我无法插入对话」。做法：`run_in_background` + **输出必须落盘**（句柄会被清掉，
> 实测两次 `No task found` 结果全丢）+ 启动后立刻回报「跑什么 / 多久 / 我接着做什么」。

> **M-R121｜可对话靠钩子拦，不靠自觉**。S176 实证：M-R120 写下来当晚**仍两次前台长跑**，
> 第二次明知规则在手边。故装了 `PreToolUse` + `Bash` 钩子：
> `~/.codebuddy/hooks/bg_guard.py`，命中重命令且非后台即 `exit 2` 阻断，stderr 回填改法。

| 判据 | 口径 |
|---|---|
| 拦截面 | `verify` / `assess` / 无参 `signal-check` / `rules-probe` / `watchdog scan` / `inspect scan` |
| 放行面 | 已带 `run_in_background`、`&`、`nohup`、或 `timeout ≤55` |
| 防误伤 | 只看每段管道**首个可执行词**——heredoc 正文、`grep`、`echo` 里出现关键词不算 |
| 探针 | `python3 ~/.codebuddy/hooks/bg_guard_selftest.py` → 11/11 |

**两条为什么必须拆开**：120 是行为要求，121 是执行层保证。只有 120 没有 121 =
S176 那种明知故犯；只有 121 没有 120 = 模型不知道被拦后该怎么改。

**三条自检（收尾前自问）**：

1. 我这一轮**改**了什么？路径呢？
2. 我跑的哪条命令能证明它生效了？输出是什么？
3. 哪些事我**没做**？为什么没做？

> 出自 S158/S111 实证：曾把「我确认了一下」写进交付，回查时无法复现——
> 那一步没有命令、没有输出。**"我说做完了"不是证据，"这条命令输出这个"才是。**

> **M-R135｜自测必须跑真实样本**：用例不能由自己臆想的写法构成。
> S192 实证：`bg_guard` 旧自测 **11/11 全绿**，用例写成 `tvbox inspect scan`；
> 而真实历史 53 次是 `python3 /root/.../inspect.py scan`，**一次没拦**。
> 判据：正例必须从 transcript / 日志里**抄录原文**；反例必须覆盖
> 「已收窄 / 已后台 / 局部化」三类放行路径。
> **用例抄错 = 假绿，比没有测试更糟**——它让人以为已经守住。

## 8.7 规则执行层分级（enforce_level · S188）

> **`tier: enforced` 不等于"做事时会拦"。** 这是 S188 查出来的最大认知偏差。
> 原先 141 条规则里有 37 条标着 `enforced`，但它们的 `probe.command` 全是
> `tvbox conductor signal-check` 这类**全量自检**——只在有人主动跑检查时才生效。
> 用户感觉"规则都写了但没用"，根源就在这里。

**四档（字段 `enforce_level`，已写入全部 145 条）**：

| 档 | 条数 | 含义 | 代表性载体 |
|---|---|---|---|
| `GATE` | **15** | **动作时拦截**，命中即 `exit 2`，真的拦得住 | `bg_guard` `chain_guard` `announce_guard` `parallel_guard` `post_probe` |
| `PROBE` | 11 | **可双向验证**，注入异常必捕获 + 正例必静默 | `rule_probe.py M-Rxx` |
| `SCAN` | 40 | **静态体检**，跑一次查一次，不跑就没事 | `signal-check` / `verify` / `rules check` |
| `DOC` | 79 | **文档锚点**，设计原则型，本就不该有命令 | `landing` 指向技能文档 |

> ★ **什么样的规则才配 GATE**（判据只有一条）：
> **存在「改了 A → 就必须做 B」的动作依赖。**
> 例：M-R17「改 pre_exempt/白名单 → 必须跑 `watchdog regress`」、
> M-R20「改检查器逻辑 → 必须跑 `watchdog replay`」——
> 改动文件可识别、后续动作可指明、缺了会怎样可描述。`post_probe.py` 按文件路径实拦。
>
> **反例（为什么另 26 条业务规则不升 GATE）**：
> `tvbox-inspect` 的 M-R57（输出→判定三态）、M-R60（读取三态化）同样重要，
> 但它们没有动作依赖结构，硬配钩子只能造出**假门禁**——
> 那是比没门禁更坏的事，因为报表上会显示"已守护"。
> 它们的正确载体是 SCAN（每期体检跑一遍）。
> `tvbox-interface` 的 2 条、`probe-hub` 的 6 条同理，全部如实留在 DOC/SCAN。

> ★ **为什么 GATE 只有 13 条（9%）**：这不是缺陷，是**如实记账**。
> 第一版脚本用「钩子文本里出现过规则号」反推，得到 21 条，但里面有 6 条是
> 前缀误命中（`M-R7` 命中了 `M-R71` 的前缀），另有 2 条（如 M-R121）因门禁词
> 不在同一行被误降。修了两次才对——**从文本反推守护关系本身就是错的方法**，
> 文本是给人看的，措辞会变。
> 最终改为**钩子显式认领**：`hooks_integrity.py` 里的 `CLAIM` 表写死
> 「哪个钩子守护哪几条规则」，并做**双向校验**（认领的必须在册且标 GATE；
> 标 GATE 的必须有人认领）。任一侧对不上就报红。

**怎么用**：

```bash
python3 ecosys-conductor/scripts/fix_tier_report.py      # 分布 + GATE 清单
python3 ecosys-conductor/scripts/fix_tier_report.py --by-owner   # 按技能看谁在守
python3 ecosys-conductor/scripts/regrade_gate.py         # 按 CLAIM 表重定 GATE（钩子变动后跑）
python3 ~/.codebuddy/hooks/hooks_integrity.py            # 双向校验认领契约
```

**判档口径**（写给后来人，防止再虚标）：
- 能在**动作发生的那一刻**拒绝执行 → `GATE`，否则不许写 enforced。
- 只能事后查 → 老实标 `SCAN`，不要写成 `enforced`。
- 设计约束（如"判重必须与库内快照比"）→ `DOC`，**不要为了凑数编假命令**
  （L023 的 `carrier_note` 已明说：逼人编假命令比空着更坏）。

**S188 补登脱册 4 条**：`M-R129`（规则复发须升执行层）、`M-R130`（并行）、
`M-R131`（会话编号权威）、`M-R132`（统计先验翻页）——此前只活在
**S209 立册 M-R208｜令牌单一事实源**：GitHub 令牌以 `/root/.codebuddy/.gh_token` 为唯一事实源，落地于 `probe.py` 回退全局 + `clone_restore.sh` 自动落位，符号链接已删。
`user_habit_brief.md`，规则册里查无此条，`rules check` 也报"三方一致"
（因为三处都缺，检查器没有可比较的对象）。**检查器不知道它漏了什么，
是 M-R63 的现场版。**

**M-R136｜业务规则配门禁须过三关**：给业务技能配钩子前，逐条过——
① 触发点是**单点可观测**的（某个工具调用的具体形态）；
② 判据能用**纯文本/结构**判真假（不靠会话意图、不靠联网）；
③ 误拦代价**远小于**放过代价。
三条缺一即降 `SCAN` / `DOC`。**不许为了凑数把文档规则硬升成门禁——
假门禁比没门禁更坏，因为报表会显示"已守护"。**

> S193 现场：19 条业务候选规则初判 15 条"可拦"，逐条回代码核对后
> 砍到 7 条、再砍到 **4 条**（`~/.codebuddy/hooks/biz_gate.py`）。
> 被砍的里有"检查器必须配套 `_selftest.py`"——真实自测是 `self-check`
> 的活体探针，**根本不存在这个命名约定**，判据不成立，装上去就是恒不触发的假门禁。

## 8.8 技能路由闸（M-R133 · S189）

> **「知道脚本路径 ≠ 走过流程」。** 该走的技能没走，是最贵的一种省事——
> 省下的是 6 秒的 `Skill` 调用，赔上的是整个流程的判据、验收与留痕。

**现场（用户原话：「探源司怎么没看到触发」）**：用户说「追踪原仓库」，
`probe-hub` 的触发词里明写有「上游仓库 / 作者仓库 / 原作者 / 原仓库」，
`conductor whoami` 实测也能正确路由——**但没人调用它**。我直接手写 curl
＋自拟判据把整条链路跑完了。而 `probe-hub/SKILL.md §2.1` 自己就写着
「12884 次调用里 Skill 只被调用 3 次，探源司 0 次」。

**结论：路由引擎是好的，缺的是「工具调用前强制问一次路由」这道闸。**

### 钩子判据（三道全中才拦）

| # | 判据 | 实现 |
|---|---|---|
| ① | **真触发词子串命中** | 从各技能 `SKILL.md` description 抽词（触发词段 + 「」引号词 + 复合词切核），要求词作为连续子串出现在用户话里 |
| ② | **本轮未加载该技能** | 从会话转录倒读，看有没有 `Skill(skill=<该技能>)` 调用 |
| ③ | **是动作不是讨论** | 消息含「这个词 / 规则 / 门禁 / 触发词 / 为什么没触发」等 → 放行 |

**为什么不用 `conductor whoami` 当判据（实测）**：

```
「追踪原仓库」          → 相关度 2（近义）
「帮我查一下今天天气」  → 相关度 2（近义）
```

两者**完全同档**。`whoami` 是弱匹配，只做分词部分重合，无法区分"真的要探源"
与"只是说了个查字"。拿它当门禁 = 每天被天气句拦一次，我很快学会无视它（M-R113 噪音化）。
改用真触发词子串匹配后，同一批句子区分度 **100%**。

**为什么词表要「两路取词 + 复合词切核」**：`原仓库` 在 `probe-hub` 的 description 里
位于触发词段末尾，写作 `原仓库转json` 这个复合词。只按顿号切、只存整词，
会漏掉用户实际使用的核心词「原仓库」——**这是我第一版自测 A1 失败的真实原因**。

### 实现与验证

```bash
python3 ~/.codebuddy/hooks/skill_route_guard.py            # 钩子本体（PreToolUse · Bash/Write/Edit/MultiEdit）
python3 ~/.codebuddy/hooks/skill_route_guard_selftest.py   # 双向自测 12/12
python3 ~/.codebuddy/hooks/skill_route_guard_verify.py     # 真实语料独立验证（防自测自绿）
```

**三条硬约束**（照抄 `parallel_guard` 的血泪教训）：
1. **钩子永不允许阻塞调用方**——只用只读快操作，转录只读尾部 2MB（全文已 363MB）
2. **必须双向自测**——正例不拦 = 白装；反例误拦 = 噪音化
3. **只拦一次**——同一 (会话, 技能, 消息时间) 提醒过就放行，否则模型一旦决定不加载
   该技能，就会陷入死循环无法干活

**放行面**：`SKILL_ROUTE_OFF=1` / automation 会话 / 已加载总指挥 / 只读工具（Read·Glob·Grep）。

### 这次的三处代价（补门禁前欠下的）

1. **没写 DB_UPSTREAM**——上游状态没入库，下次还得从头查
2. **没有快照对比**——答不出"这仓库比上次更新了什么"
3. **判据自拟**——用了自己的"通/不通/无法判定"，没走 `monitor check` 的四类信号判据

## 8.9 正本入口优先（M-R137 · S196）

> **知道入口存在 ≠ 走过入口**（M-R133 的兄弟条）。做动作前先查**正本入口**，不自造通道：
> ①经验沉淀走 `tvbox close learn biz/plat`（正本转接器），禁自拼 link.py 参数、禁自写写入器；
> ②后台收结果禁 block=true（M-R122 已 GATE），等 task-notification 自动推进；
> ③同一件事试错 ≥2 次 → 停，先查正本入口，不许第 3 次改脚本。
> S196 现场：`tvbox close learn` 一直存在，我却自拼参数三连改 /tmp 脚本（首次还静默返回 None）。
> 习惯档：`user_standing_instructions.json pref_canonical_entry`。定级 SCAN（试错计数静态钩子判不了，M-R136 不造假门禁）。

