# WorkBuddy 宿主（课件技能 · Windows）

本技能只写课型与大纲。真正出 `.pptx` 的是本机 **zagens-office（要求 ≥ 0.14.2）**。新验收能力（`schema capabilities` / `inspect_slide` / batch 含 `preflight`）以探测结果为准，不单看版本号。

## 用哪个工具跑脚本（硬）

WorkBuddy 的 **Bash 沙箱会拦截** `powershell.exe -File ...`。第一次执行被拒时不要重试同一条 Bash 命令。

| 要跑什么 | 用哪个宿主工具 | 怎么写 |
|----------|----------------|--------|
| `ensure-workbuddy-sandbox.ps1` | **PowerShell 工具** | 把 `.ps1` 路径交给 PowerShell，不要套进 Bash |
| `zagens-office` | **Bash**（优先） | 先 `zagens-office --version`；无则 `~/.zagens-pro/bin/`，再无则 npm 全局 `vendor/zagens-office.exe`。stdout 应是一行 JSON |

禁止在 Bash 里把 PowerShell 可执行文件套一层再 `-File` 跑 `.ps1`。第一次被拒不要用同一条 Bash 重试。

## 沙箱

探测或出件前，用 **PowerShell 工具**执行一次（`init` 已做过可跳过）。脚本写入 `~/.zagens-pro`，并在 npm 可用时写入全局 prefix 与 cache：

```
skills/office-kejian/scripts/ensure-workbuddy-sandbox.ps1
```

若该路径不存在，改试技能目录下的 `scripts/ensure-workbuddy-sandbox.ps1`。否则写不了 `~/.zagens-pro`。非 Windows 先告知再停。

## 优先用已上架的 Office 连接器

用户若已在 WorkBuddy 连接 **Office 文档引擎**（连接器装到 `~\.zagens-pro\bin\`），或已用 npm 装过（命令在 PATH），先探测；有版本号就不要再装一遍。

**不要**跑 `zagens-office integrate`：会覆盖办公技能/连接器捆绑的技能文件。

stdout 需 ≥ **0.14.2**（公式、自动建父目录、写入期 assert）。`0.0.0-stage` 或更低按根技能「首次使用」升级后再继续。本文件不写安装命令。

## 能力探测（出件前）

有版本号后，Bash 再跑：

```
zagens-office schema capabilities
```

| 结果 | 验收怎么走 |
|------|------------|
| 成功且含 `inspect_slide` | 逐页 upsert 后对该页 `edit --op inspect_slide`；整份可用 `batch`+`preflight` |
| 命令失败 / 无该字段 | **不当失败**；退回 `preflight` + `view` `mode:svg` |

`path` / 配图须在 `--workspace` 内（路径狱）；`path_denied` 先改路径再重试。

## 本仓 dogfood（试 Unreleased 引擎）

npm 上的 `0.14.2` 可能还没有 `inspect_slide`。要在 WorkBuddy 测本仓迭代能力：

1. 本仓 `cargo build -p zagens-office-cli --release`
2. 把 `target/release/zagens-office.exe`（或包内二进制名）拷到 `%USERPROFILE%\.zagens-pro\bin\zagens-office.exe`（覆盖前可备份）
3. Bash 再 `zagens-office --version`（可能仍显示 `0.14.2`）+ `schema capabilities` 确认新字段
4. 用本技能包出课件，优先走档 A0

不要从裸链接下 exe；只换本机构建产物。

## 探测

| 工具 | 命令 |
|------|------|
| PowerShell | 先 `zagens-office --version`；无则 `& "$env:USERPROFILE\.zagens-pro\bin\zagens-office.exe" --version` |
| Git Bash | 先 `zagens-office --version`；无则 `"$HOME/.zagens-pro/bin/zagens-office.exe" --version` |
| npm 未进 PATH | PowerShell `& "$(npm prefix -g)\node_modules\zagens-office\vendor\zagens-office.exe" --version`；Git Bash `"$(npm prefix -g)/node_modules/zagens-office/vendor/zagens-office.exe" --version` |
| PATH 已含 bin | `zagens-office --version` |

以 stdout 版本号为准。Bash **不要**写 `%USERPROFILE%`。当前发行以 **Windows x64** 为主。

## 未装引擎时

安装见本技能根 `SKILL.md`「首次使用」（本文件不复述命令）。已在连接器 `init` 则等装完再探测，不要重复安装。**不要**跑 `zagens-office integrate`。

出件纪律在本包 @references/pptx-kejian.md。**不要** `load_skill office-pptx` 做课件。

## 边界

- 可以写大纲、出课件 PPT；**不能**售课、直播、招生报名。
- 学生名册、成绩、照片只用用户本机路径，不上传。
- 成绩单 / 奖状 / 试卷题库不走本技能，改 `zagens-office`。
