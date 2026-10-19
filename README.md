# Sandbox Skills

CodeBuddy Skills 集合，提供多平台 API 集成和沙箱环境能力。

## Skills 列表

| Skill | 描述 | 主要功能 |
|-------|------|----------|
| **computer-use** | 沙箱桌面交互 | 截图、鼠标点击、键盘输入、滚动、GUI 应用操作 |
| **figma-connector** | Figma 设计平台集成 | 设计稿还原、代码生成、Design Token 提取 |
| **github-connector** | GitHub 平台集成 | 仓库操作、PR 管理、Issues |
| **cnb-cli-connector** | 腾讯 CNB 代码平台集成（CLI 版） | cnb.woa.com 仓库/Issue/PR/流水线/制品库（cnb CLI） |
| **gongfeng-connector** | 工蜂代码平台集成 | git.woa.com 仓库操作、PR 管理 |
| **sites** | 发布为在线应用 | 把沙箱项目发布为在线应用并生成可访问的分享链接 |

## 目录结构

```
skills/
├── computer-use/         # Computer Use 桌面交互
│   ├── SKILL.md          # Skill 定义和使用说明
│   ├── docs/             # 设计方案文档
│   └── scripts/
│       ├── install.sh        # 依赖安装脚本
│       ├── uninstall.sh      # 依赖卸载脚本
│       ├── start_desktop.sh  # 桌面启动
│       ├── stop_desktop.sh   # 桌面停止
│       ├── health_check.sh   # 健康检查
│       └── computer_tool.py  # Computer Use 核心工具
│
├── figma-connector/      # Figma API 集成
│   ├── SKILL.md          # Skill 定义和使用说明
│   └── scripts/
│       └── get_token.sh  # OAuth Token 获取脚本
│
├── github-connector/     # GitHub API 集成
│   ├── SKILL.md
│   └── scripts/
│       └── get_token.sh
│
├── cnb-cli-connector/    # CNB 平台集成（CLI 版，cnb 命令行工具）
│   ├── SKILL.md
│   └── scripts/
│       └── get_token.sh
│
├── gongfeng-connector/   # 工蜂平台集成
│   ├── SKILL.md
│   └── scripts/
│       └── get_token.sh
│
└── sites/                # 发布为在线应用
    ├── SKILL.md
    └── scripts/
        ├── lib.js
        ├── package.json
        ├── publish.js       # 发布脚本
        └── unpublish.js     # 取消发布脚本
```

## 使用方式

### Computer Use（桌面交互）

Computer Use 通过虚拟桌面让 Agent 能够截图、操作鼠标和键盘：

```bash
# 1. 安装依赖（首次，需要 root 权限）
sudo bash skills/computer-use/scripts/install.sh --force

# 2. 启动桌面
bash skills/computer-use/scripts/start_desktop.sh

# 3. 截图
python3 skills/computer-use/scripts/computer_tool.py '{"action": "screenshot"}'

# 4. 点击坐标 (512, 384)
python3 skills/computer-use/scripts/computer_tool.py '{"action": "left_click", "x": 512, "y": 384}'

# 5. 输入文本
python3 skills/computer-use/scripts/computer_tool.py '{"action": "type", "text": "Hello"}'

# 6. 停止桌面
bash skills/computer-use/scripts/stop_desktop.sh
```

详细用法参见 `skills/computer-use/SKILL.md`。

### Connector Skills（API 集成）

#### 1. 安装 Skill

将对应的 connector 目录复制到项目的 `.codebuddy/skills/` 目录下：

```bash
cp -r skills/figma-connector /path/to/project/.codebuddy/skills/
```

#### 2. 获取 Token

每个 Connector Skill 都内置了 `get_token.sh` 脚本，用于获取 OAuth Token：

```bash
# Figma
./scripts/get_token.sh figma

# GitHub
./scripts/get_token.sh github

# CNB（CLI 版 / enterprise_cnb-apikey 连接器）
./scripts/get_token.sh enterprise_cnb-apikey

# 工蜂
./scripts/get_token.sh gongfeng
```

Token 会自动设置为对应的环境变量（如 `FIGMA_TOKEN`、`GITHUB_TOKEN` 等）。

#### 3. 使用 Skill

在 CodeBuddy 对话中，相关 Skill 会根据上下文自动激活，提供 API 调用能力。

## 认证方式

所有 Skill 使用 OAuth 2.0 认证，Token 通过 `Authorization: Bearer` header 传递：

```bash
curl -H "Authorization: Bearer $TOKEN" https://api.example.com/...
```

## License

MIT
