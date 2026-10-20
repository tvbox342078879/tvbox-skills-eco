---
name: miaoda-app-builder
display_name: 秒哒应用搭建
display_name_en: Miaoda app builder
description: "Based on Baidu Miaoda (秒哒), users can create, preview, modify, and publish websites, WeChat Mini Programs, and mobile apps through natural-language conversations—enabling intelligent, automated, and scalable application development."
description_zh: "基于秒哒平台，通过自然语言对话，即可完成网页、微信小程序及移动 App 的创建、预览、修改与发布上线，实现智能化、自动化、规模化的应用开发。"
description_en: "Based on Baidu Miaoda (秒哒), users can create, preview, modify, and publish websites, WeChat Mini Programs, and mobile apps through natural-language conversations—enabling intelligent, automated, and scalable application development."
category: Development Tools
version: 1.0.23
author: 百度秒哒
metadata:
  openclaw:
    requires:
      bins:
        - python3
      env:
        - MIAODA_API_KEY
    primaryEnv: MIAODA_API_KEY
---

# Miaoda (秒哒) App Builder

Miaoda (秒哒) is a **chat-driven full-stack application builder**.
Official website: https://www.miaoda.cn

Users describe what they want in natural language and Miaoda generates a **production-ready web product**, including:

- frontend UI
- backend services
- database schema
- integrations
- deployable hosting

Typical outputs include:

- websites
- web applications
- dashboards
- SaaS products
- admin panels
- internal tools
- landing pages
- interactive web pages
- browser games and mini games
- Weixin mini programs
- native iOS apps
- native Android apps
- research reports
- data analysis documents
- PPT / presentations
- general content documents

This skill enables AI agents to interact with the **Miaoda(秒哒) platform** to create, iterate, generate, and deploy applications.

All platform operations must be executed through the packaged CLI script:

```bash
python scripts/miaoda_api.py <command> [options]
```

Do **not** call platform APIs directly. Always use the CLI commands provided by this skill.

# When to Use This Skill

Use this skill whenever the user wants to:

* create a **website**
* create a **webpage**
* build a **web application**
* build a **dashboard**
* create a **SaaS product**
* build an **admin panel**
* build an **internal tool**
* create a **landing page**
* build an **interactive web page**
* create a **browser game**
* create a **mini game**
* build a **native iOS app**
* build a **native Android app**
* build a **mobile app** (iOS / Android)
* generate an **MVP web product**
* modify an existing **Miaoda project**
* publish or deploy a **Miaoda application**
* generate a **research report**
* create a **PPT or presentation**
* produce a **data analysis document**
* create any **general content output** (not a deployed app)

Do **not** use this skill for unrelated programming tasks.

---

# Routing Keywords

Trigger this skill if the request includes concepts such as:

* build a website
* create a webpage
* build a web app
* create a SaaS
* build a dashboard
* create an admin panel
* build an internal tool
* create a landing page
* build a browser game
* create a mini game
* build a native iOS app
* build a native Android app
* build a mobile app
* generate a web product
* make a snake game webpage
* build a todo web app
* create a blog site

---

# Example Requests

Examples that should route to this skill:

* "Create a todo list web app"
* "Build a personal blog website"
* "Make a dashboard for sales analytics"
* "Create a SaaS landing page"
* "Build an admin panel"
* "Write a snake game webpage"
* "Create a browser game"
* "Build a mini web game"
* "Build a native iOS habit-tracking app"
* "Make an Android app for expense tracking"
* "I want a mobile app that works on both iPhone and Android"
* "Modify my Miaoda project"
* "Publish this Miaoda app"

---

# Native Mobile Apps (iOS / Android)

Miaoda can generate **native iOS and Android mobile applications**, not just web
products. The output is a real mobile app project rather than a mobile-adapted
web page.

**How to request it:** state the target platform explicitly in the `chat` text.
There is no separate CLI command or flag — the platform decides the output form
from the natural-language description.

```bash
python scripts/miaoda_api.py chat --text "创建一个原生 iOS 记账 App，支持分类统计和月度报表"
python scripts/miaoda_api.py chat --text "创建一个安卓原生 App，用于扫码记录仓库出入库"
python scripts/miaoda_api.py chat --text "创建一个 iOS 和 Android 双端原生 App，功能是每日习惯打卡"
```

**Guidance for agents:**

* Say **原生 App / native app** plus the platform (**iOS**, **Android**, or both)
  in the first `chat` message. Vague wording like "做个 App" is often interpreted
  as a web app.
* Mention mobile-specific needs in the PRD stage — push notifications, camera /
  scanning, offline storage, location, biometric login — so they land in the
  generated project.
* If the user only says "手机上能用" or "移动端", that usually means a
  mobile-friendly **web** page. Confirm which one they want before committing:
  a native app and a responsive web app are different deliverables.
* The lifecycle is unchanged: `chat` → PRD refinement → `generate-app --watch` →
  `publish --wait`.
* Distribution differs from web. For a native app, check `app-detail` and the
  post-publish trajectory output for the download / distribution entry the
  platform returns; do not assume or construct a web URL.

---

# Stateless Execution Model

The CLI script is **stateless**.

It does not store workflow state between calls.

Application workflow state is maintained by the Miaoda platform and must be inferred from:

* `appId`
* `conversationId`
* application detail
* conversation trajectory events

Agents must pass the appropriate identifiers when continuing conversations or modifying applications.

---

# Application Lifecycle Rules

Miaoda applications follow a strict lifecycle.

Agents must follow these rules.

---

## Initial Creation

For a new application:

1. Start with a `chat` request describing the product.
2. The application enters the **PRD refinement stage**.
3. Continue chatting to refine the specification.
4. When the trajectory contains a **Generate App** button (`type":"button"` and `event":{"name":"generateApp"}` in `result.artifact.parts[].data.actions[]`), trigger application generation using `generate-app`.

Generation is required **only once** during the initial creation.

---

## Multi-Round Modification

After an application has already been generated:

* **Do not call `generate-app` again.**
* Continue using `chat` with the same `appId` and `conversationId`.

Normal chat messages modify the existing application.

---

## Publishing

Publishing is allowed **after the application has been generated at least once**.

Rules:

* Publishing does **not require another generation step**
* Publishing may happen anytime after the first generation
* Publishing must be followed by **status polling** (or use `--wait` flag)
* **Do not retry or repeat `publish` after a successful execution.** A publish is
  successful when the CLI exits successfully and the final status is `SUCCESS`.
  Missing or delayed downstream output, missing frontend events, an incomplete
  conversation reply, or any other display/transport issue is **not** a publish
  failure; investigate that separately instead of publishing again.
* Retry `publish` only when the command itself fails or the final deployment
  status is explicitly `FAILED`. Do not retry while the status is
  `PROCESSING`, `RUNNING`, or `UNDER_RELEASE`; keep polling.

Typical deployment flow:

```
publish → publish-status polling
```

Or use the `--wait` flag to auto-poll:

```
publish --wait
```

Stop polling when the status becomes:

* `SUCCESS`
* `FAILED`

---

## General Task (No Generation Required)

For general tasks such as research reports, PPT, or analysis documents, the
platform processes the request entirely in the chat stage.

Rules:

* There is **no PRD refinement stage**
* There is **no `generate-app` step**
* There is **no `publish` step**
* The task is complete when `chat` returns `{"needGenerateApp": false, "appType": "TASK"}`

Do **not** call `generate-app` for general tasks.

---

# Application URLs

Publishing returns the application's public URL in the platform response. Use
the value returned by the CLI; do not infer a URL from `appId`, because test and
production environments use different domains.

For a deployment that should wait for completion, use:

```bash
python scripts/miaoda_api.py publish --app-id <app_id> --wait
```

# Standard Workflow

## Create New Application

```
chat → PRD refinement → generate-app --watch → publish --wait
```

---

## Modify Existing Generated Application

```
chat → chat → chat
```

(no additional generation step required)

---

## Deploy Application

```
publish → publish-status polling
```

Or:

```
publish --wait
```

---

## General Task (Direct Completion)

```
chat → state:completed (needGenerateApp: false)
```

No generate-app or publish step required.

---

# Available Commands

All commands are executed via the CLI script.

**Authentication**:

* If `MIAODA_API_KEY` is set, the CLI uses it for direct Miaoda API access.
* When running inside qianfan-desk without a key, the CLI automatically uses
  the session-bound Miaoda proxy when `DUMATE_SESSION_ID` and the desktop
  gateway/scheduler environment are available.
* Outside qianfan-desk, set `MIAODA_API_KEY` explicitly before running commands.

Examples below show the explicit-key form for clarity.

```bash
export MIAODA_API_KEY="your_api_key_here"
```

---

## API 命令参考（外置）

各命令的参数、返回、错误码详见 **docs/manual_api_reference.md**（S179 外置，本文件→外置索引；包含 list-apps / app-detail / get-context-id / conversation-history / chat / fetch-trajectory / generate-app / publish / publish-status / download-source / list-task-files / download-task-file）。

调用铁律（正文保留）：任何 API 调用前先看本节外置文档的「鉴权」小节；publish 前必须 generate-app 成功；失败先查 publish-status 再重试。

## Example 1: Create and Deploy a New App

```bash
export MIAODA_API_KEY="sk_xxxxx"
cd ~/.openclaw/skills/miaoda-app-builder

# Step 1: Create app via chat (returns appId + conversationId on first line)
FIRST=$(python scripts/miaoda_api.py chat --text "创建一个简单的计数器应用" | head -1)
APP_ID=$(echo $FIRST | jq -r '.appId')
CONTEXT_ID=$(echo $FIRST | jq -r '.conversationId')

# Step 2: Generate and wait for completion (recommended: --watch auto-polls until done)
python scripts/miaoda_api.py generate-app \
  --app-id $APP_ID \
  --context-id $CONTEXT_ID \
  --watch

# Step 3: Publish (with auto-wait)
python scripts/miaoda_api.py publish --app-id $APP_ID --wait

```

---

## Example 2: Modify an Existing App

> **IMPORTANT — Always update `CONTEXT_ID` from each `chat` response.**
>
> Each `chat` call prints a JSON header line `{"appId": "...", "conversationId": "..."}`.
> The platform may return an updated `conversationId` in this response.
> **Always capture it and use it for the next `chat` call**, or the next round will create a brand-new app.

```bash
export MIAODA_API_KEY="sk_xxxxx"
cd ~/.openclaw/skills/miaoda-app-builder

APP_ID="app-abc123xyz"

# Step 1: Get app detail — conversationId is auto-injected
DETAIL=$(python scripts/miaoda_api.py app-detail --app-id $APP_ID)
CONTEXT_ID=$(echo $DETAIL | jq -r '.data.conversationId')

# Abort early if conversationId is missing (app may have no history yet)
if [ -z "$CONTEXT_ID" ] || [ "$CONTEXT_ID" = "null" ]; then
  echo "Error: could not retrieve conversationId. Try: get-context-id --app-id $APP_ID"
  exit 1
fi

# Step 2 (optional): Review conversation history to understand previous work
python scripts/miaoda_api.py conversation-history --app-id $APP_ID

# Step 3a: First modification — capture the UPDATED conversationId from the response header
CHAT_RESULT=$(python scripts/miaoda_api.py chat \
  --text "把背景颜色改成深色模式" \
  --app-id $APP_ID \
  --context-id $CONTEXT_ID | head -1)
CONTEXT_ID=$(echo $CHAT_RESULT | jq -r '.conversationId')   # ← UPDATE for next round

# Step 3b: Second modification — uses the updated CONTEXT_ID
CHAT_RESULT=$(python scripts/miaoda_api.py chat \
  --text "添加暗黑模式切换按钮" \
  --app-id $APP_ID \
  --context-id $CONTEXT_ID | head -1)
CONTEXT_ID=$(echo $CHAT_RESULT | jq -r '.conversationId')   # ← UPDATE again

# Step 4: Re-publish
python scripts/miaoda_api.py publish --app-id $APP_ID --wait
```

---

# Command Semantics

## chat

Used to:

* create a new application
* refine PRD specifications
* modify an existing generated application

For a new application, `chat` creates the project and begins the PRD stage.

For an existing application, `chat` performs iterative modifications.

---

## trajectory

Streams conversation progress and system events.

Use this to determine:

* whether PRD generation is ongoing
* whether the app is ready for generation (see generate-app condition below)
* whether generation has completed

---

## generate-app

Triggers application generation.

Call **only** when trajectory contains a Generate App button in `result.artifact.parts[].data.actions[]`: an action with `"type":"button"` and `"event":{"name":"generateApp"}`. This indicates PRD is ready. Do **not** rely on text alone—the button structure is the authoritative signal.

Call **once** during initial creation. Do not call again for modifications.

---

## publish

Triggers application deployment.

Use `--wait` flag to auto-poll status (recommended).

Treat `SUCCESS` as terminal. Missing or malformed downstream output is not
evidence that deployment failed and must not trigger another publish.

---

## publish-status

Polls deployment progress until the release completes.

Not needed if using `publish --wait`.

---

# Error Handling

## IAM AccessKey Validation Failed

**Symptom:**

```
IAM access key validation failed.
```

**Cause:**

The configured `MIAODA_API_KEY` is invalid or missing.

**Resolution:**

1. Go to the Miaoda(秒哒) official website:

   https://www.miaoda.cn

2. In the left navigation panel, apply for an available access key.

3. Set the key as the environment variable:

   ```bash
   export MIAODA_API_KEY="sk_xxxxx"
   ```

4. Or create a `.env` file in the skill directory:

   ```bash
   echo "MIAODA_API_KEY=sk_xxxxx" > ~/.openclaw/skills/miaoda-app-builder/.env
   ```

---

## NotOpenSSLWarning

**Symptom:**

```
NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+,
currently the 'ssl' module is compiled with 'LibreSSL 2.8.3'
```

**Cause:**

macOS uses LibreSSL by default, but urllib3 v2 recommends OpenSSL.

**Impact:**

This is a **warning only**. API calls still work correctly.

**Resolution (optional):**

If you want to suppress the warning:

```bash
pip3 install 'urllib3<2'
```

Or ignore it - it doesn't affect functionality.

---

## 秒点不足 (Insufficient Credits)

**Symptom:**

```
[秒点不足] 您的秒点余额不足，无法继续操作。
请前往秒哒官网充值：https://www.miaoda.cn
```

Or within an error message:

```
秒点不足，您可明日再来，或邀请新人立即获得秒点奖励。
```

**Cause:**

Your Miaoda account does not have enough 秒点 (Credits) to perform this operation (e.g., generating or publishing an app).

**Resolution:**

Visit the official Miaoda website to recharge your credits:

**https://www.miaoda.cn**

Log in → navigate to the recharge page → complete payment. Credits are credited immediately.

---

# Error Handling Guidance

Agents should handle the following situations:

* generation requested before the platform indicates readiness
* publish requested before generation has completed
* missing `appId` or `conversationId` (use `get-context-id` to recover a lost `conversationId`)
* interrupted trajectory stream
* failed deployment status
* incorrect or missing parameter values

After a publish command returns successfully, do not republish solely because
downstream UI output or a frontend event is missing. Only a command-level
failure or an explicit final `FAILED` deployment status permits a retry.

If workflow state is unclear, inspect the trajectory or application detail before taking the next action.

**Pro Tips:**
- Always check command `--help` when unsure about parameters
- Use `--wait` flag with `publish` to simplify deployment
- Save `appId` and `contextId` from first `chat` response
- Only call `generate-app` once during initial creation
- After generation, use `chat` directly for modifications

The final response rules are defined in **Final Output Contract** above. Do not
add a second sharing rule or expose editor/production URLs in ordinary text.
