# M-R125 附件不可见 · 证据登记（S179）

## 一句话

**附件不占目录树槽位。任何"列子节点"的接口都看不见附件，所以"子节点数为 0"不能推出"没有内容"。**

## 事故

20261019，治理 `conductor backup` 时，`space.workspace.list-nodes` 对 13 个名为
`20261020_生态备份` 的夹全部返回 0 个子节点。据此刻定"空夹，可删"，
把它们连同 `20261018_生态备份` 一起推进待删收件箱。
**判据不成立**：这些夹里存的是 81 个备份文件，全部以 attachment 形式挂在节点上。
后已全部回退到备份区（收件箱回到 63 项）。

## 实测事实（可复验）

### 1. 节点元数据字段全集只有 16 个，无附件字段

```
space.workspace.node-info --node-id <任意夹>
createdAt createdBy enabled id kind nodes parentId parentTable
spaceId table title type updatedAt updatedBy url version
```

对 `zEYBoLq5G58Rdw7g3ADBnn`（备份区根，下辖 14 夹）与
`tBlgSL4SPEFMrsyTBTg03c`（收件箱，下辖 63 项）分别取 info，字段集合**完全一致**，
且都不含 `attachments` / `attachmentCount` / `fileCount` / `size`。

### 2. 14 个备份夹的子节点数一律为 0

```
T7Bd0ukQ4dnnTAYMy2ktLq  20261018_生态备份  子节点=0
tmjTFPQKkh2kQETEn3JocK  20261020_生态备份  子节点=0
gZy0myTDRxp5mR8aZl5oGU  20261020_生态备份  子节点=0
…（其余 11 个同）
```

而 `backup_state.json` 记录 `uploaded_by_day` 三天各 77/81/81 个文件、
`last.ok=81 fail=0`。**"0 子节点"与"81 个文件已上传"同时成立**——
这就是"子节点数无判定力"的直接证明，不是推理。

### 3. 平台没有附件枚举接口

`library/api-manifest.json` 共 41 个接口，`list` 类只有 4 个：
`list-nodes` / `list-user-spaces` / `list-channel-spaces` / `list-page-publish-artifacts`。
**没有任何 attachment-list**。`attachment.py` 只有 `upload` / `download`，
`download` 必须传 `--attachment-id`——不先知道 id 就取不到。

### 4. 正文也读不出附件

`space.workspace.get-doc-review` 对当日备份夹返回
`"---\ntitle: 20261020_生态备份\n---\n\n"`，与备份区根空夹返回的形态相同。
**正文不含附件清单**，故不能用正文非空来反推。

## 结论：什么能当判据，什么不能

| 判据 | 能不能用 | 说明 |
|---|---|---|
| 子节点数 == 0 → 空夹 | ❌ **不成立** | 附件不占槽位，0 与"有 81 个文件"并存 |
| node-info 有无附件字段 | ❌ 不存在该字段 | 16 个字段里没有 |
| get-doc-review 正文为空 | ❌ 不成立 | 有附件时正文同样为空 |
| 平台 attach-list 接口 | ❌ 平台未提供 | 41 接口中无 |
| `backup_state.json` 的 `last.ok` + `items[].sha256` | ✅ **本生态唯一可用** | 上传侧自记账，含逐件 sha256 |
| `uploaded_by_day[folder_id]` 文件名清单 | ✅ 辅助 | 能回答"这个夹该有几个文件" |
| `attachment.py upload` 返回 `已作为附件上传完成` | ✅ 单次上传的成功判据 | 现有 `backup` 用的就是这个，正确 |
| 用户/人工在客户端看到附件列表 | ✅ 终裁 | AI 侧无法自证时交人工 |

## 因此这条规则要求两件事

1. **禁止**：任何脚本或判断用"子节点数 / 子节点列表为空"推断备份是否落地、
   附件是否存在、某个夹是否可删。探针：`/root/.codebuddy/hooks/attach_guard.py`。
2. **必须**：涉及"这个夹里有没有东西"的结论，一律走
   `backup_state.json`（`last.ok`、`items[].sha256`、`uploaded_by_day`），
   或显式声明"无法从接口判定，请人工核对"（M-R60 三态），**不得折叠成"空"**。

## 连带修正

`tvbox-janitor` 的空夹判定原来靠 `EMPTY_KEEP` 关键词（`备份/backup/归档/库/…`）
豁免——14 个备份夹确实都命中"备份"，所以没被误投。但**这层保护挂在名字上**：
换个命名（如 `20261020_eco`）就会失守，而 interfaces 侧依然无法分辨空与非空。
故 janitor 侧须改为"结构性容器一律不按空夹投递 + 显式声明无法判定"，
不依赖标题关键词。
