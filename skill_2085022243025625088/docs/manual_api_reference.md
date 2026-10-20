# 秒哒 API 命令参考（list-apps / app-detail / get-context-id / conversation-history /
# chat / fetch-trajectory / generate-app / publish / publish-status / download-*）

> 外置自 SKILL.md（S179 体积治理，M-R77⑥ 法③：论证型长节外置 + 索引行）。
> 本文件是这些命令的**唯一正本**；SKILL.md 只留索引与调用要点。

## list-apps

List all applications belonging to the authenticated user.

**Usage:**

```bash
python scripts/miaoda_api.py list-apps [--brief]
```

**Optional Parameters:**
- `--brief`: Output only key fields: `appId`, `name`, `type`, `appFocus`, `host`, `updatedAt`. Recommended for agents to reduce token usage.
- `--name NAME`: Filter by app name (substring)
- `--page PAGE`: Page number (default: 1)
- `--size SIZE`: Page size (default: 12)

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"

# Brief mode (recommended for agents)
python scripts/miaoda_api.py list-apps --brief

# Full mode
python scripts/miaoda_api.py list-apps
```

**Returns:** JSON array of applications with `appId`, `name`, `type`, etc.

---

## app-detail

Get detailed information about a specific application. **Automatically injects `conversationId`** into the response by default — no need to call `get-context-id` separately.

**Usage:**

```bash
python scripts/miaoda_api.py app-detail --app-id <app_id> [--no-context]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--no-context`: Skip auto-fetching `conversationId` from trajectory (faster, but response will not contain `conversationId`)

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py app-detail --app-id app-abc123xyz
```

**Returns:** JSON object with application details, configuration, and status. `data.conversationId` is automatically populated.

**appFocus — Publish Readiness Guide:**

After calling `app-detail`, check `data.appFocus` to determine whether publishing is allowed:

| `appFocus` | Meaning | Can Publish? |
|------------|---------|--------------|
| `NOT_GENERATE` | Not yet generated | No — call `generate-app` first |
| `WAITING` | Queued for generation | No — wait for generation to complete |
| `UNDER_CREATING` | Generation in progress | No — wait for generation to complete |
| `CREATE_FAILED` | Generation failed | No — retry `generate-app` |
| `DESIGNING` | Generated, ready to edit/deploy | **Yes** |
| `RELEASED` | Already published | **Yes** (only for an intentional new deployment after changes; not for retrying a successful publish) |
| `RELEASE_FAILED` | Last publish explicitly failed | **Yes** (retry allowed only after confirming the failed deployment) |
| `UNDER_RELEASE` | Publish currently in progress | Wait — do not call publish again |

**Rule:** Only proceed with `publish` when `appFocus` is `DESIGNING`, `RELEASED`, or `RELEASE_FAILED`.
When `appFocus` is `RELEASED`, do not publish again merely because downstream
UI output or frontend events are missing; check the existing publish result and
the event/rendering path first.

---

## get-context-id

Recover the `conversationId` for an existing app by reading its trajectory. Useful when the `conversationId` has been lost after a session reset.

**Usage:**

```bash
python scripts/miaoda_api.py get-context-id --app-id <app_id>
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--fetch-timeout SECONDS`: Request timeout in seconds (default: 10)

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py get-context-id --app-id app-abc123xyz
```

**Returns:** `{"appId": "app-abc123xyz", "conversationId": "conv-def456uvw"}`

**Use Cases:**
- Need to modify a previously created app in a new session but `conversationId` is lost
- Use the returned `conversationId` with `chat --app-id --context-id` to resume modification

---

## conversation-history

Show a human/agent-readable summary of past interactions for an app. More convenient than `trajectory` or `fetch-trajectory` for quickly understanding what happened in previous sessions.

**Usage:**

```bash
python scripts/miaoda_api.py conversation-history --app-id <app_id> [options]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--full`: Show full content instead of truncated summaries (default: truncate at 200 chars)
- `--limit N`: Only show the last N conversation turns
- `--fetch-timeout SECONDS`: Request timeout in seconds (default: 10)

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"

# View conversation history summary
python scripts/miaoda_api.py conversation-history --app-id app-abc123xyz

# Only show the last 3 turns
python scripts/miaoda_api.py conversation-history --app-id app-abc123xyz --limit 3

# Show full content without truncation
python scripts/miaoda_api.py conversation-history --app-id app-abc123xyz --full
```

**Returns:** JSON Lines, one entry per meaningful turn:

```json
{"eventId": 5, "role": "user", "type": "message", "content": "创建一个待办事项应用..."}
{"eventId": 865, "role": "agent", "type": "file", "content": "[file: 需求文档.md]"}
{"eventId": 880, "role": "user", "type": "message", "content": "生成应用"}
```

**Use Cases:**
- Quickly understand what happened in previous sessions before resuming work
- Determine which phase the app is in (PRD refinement / generated / modified)
- Check for unfinished modifications or cancelled tasks

---

## chat

Start or continue a conversation to create or modify an application.

**Usage:**

```bash
python scripts/miaoda_api.py chat --text "description" [options]
```

**Required Parameters:**
- `--text TEXT`: The message/instruction to send

**Optional Parameters:**
- `--context-id CONTEXT_ID`: Conversation ID of an existing app.
- `--app-id APP_ID`: Application ID of an existing app.
- `--query-mode QUERY_MODE`: Query mode (default: deep_mode)
- `--input-field-type INPUT_FIELD_TYPE`: Input field type (default: web)
- `--poll-interval SECONDS`: Seconds between trajectory polls (default: 2.0)
- `--fetch-timeout SECONDS`: Per-request timeout for each trajectory fetch (default: 10)
- `--no-stream`: Return raw chat POST response without trajectory polling
- `--prompt-generate`: After polling, interactively ask whether to submit app generation if text was returned
- `--node-json JSON`: **LGUI mode** — pass a JSON string describing the currently-selected UI node (file path, line range, tag, current styles, etc.). When set, an extra `kind: "data"` part is appended to the request so the Agent can pinpoint the exact node instead of searching the codebase. See the **LGUI (Live-GUI) Node-Scoped Editing** section below.

> **⚠️ IMPORTANT — `--app-id` and `--context-id` must always be used together.**
>
> | Intent | `--app-id` | `--context-id` |
> |--------|-----------|----------------|
> | Create a brand-new app | omit | omit |
> | Continue / modify an existing app | required | required (conversationId) |
>
> Passing `--app-id` **without** `--context-id` will **NOT** modify the existing app.
> The platform will silently create a **new** app every time. The CLI will now raise an error in this case to prevent accidental app proliferation.

**Examples:**

**1. Create a new application:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py chat --text "创建一个待办事项管理应用"
```

Response includes `appId` and `contextId` for subsequent calls.

**2. Continue conversation (refine PRD):**

```bash
python scripts/miaoda_api.py chat \
  --text "添加优先级标记功能" \
  --app-id app-abc123xyz \
  --context-id conv-def456uvw
```

**3. Modify existing generated app:**

```bash
python scripts/miaoda_api.py chat \
  --text "把按钮颜色改成蓝色" \
  --app-id app-abc123xyz \
  --context-id conv-def456uvw
```

**4. LGUI mode — modify one specific UI node:**

```bash
python scripts/miaoda_api.py chat \
  --text "改成蓝底黑字" \
  --app-id app-abc123xyz \
  --context-id conv-def456uvw \
  --node-json '{"type":"node","node":{"id":"src/pages/HomePage.tsx:30:8","path":"src/pages/HomePage.tsx","line":"30","endLine":"35","tag":"H1","content":{"text":"HelloWorld"},"style":{"color":"rgb(46, 26, 15)","backgroundColor":"rgba(0, 0, 0, 0)"}}}'
```

**Important Notes:**
- Extract `appId` and `contextId` from the response and save them
- Use these IDs for all subsequent operations on the same app
- The first `chat` creates the app and starts PRD refinement
- After generation, `chat` directly modifies the app (no `generate-app` needed)

---

# LGUI (Live-GUI) Node-Scoped Editing

LGUI mode lets a `chat` message carry **the exact UI node the user is pointing
at**, so the platform edits that node directly instead of inferring the target
from natural language alone.

## Difference from a plain chat

A plain `chat` sends only text:

```json
"parts": [
  {"kind": "text", "text": "把整体配色换成暖色调的橙粉渐变风格"}
]
```

The Agent must search the codebase itself to work out what to change.

LGUI appends one extra `kind: "data"` part alongside the text:

```json
"parts": [
  {"kind": "text", "text": "改成蓝底黑字"},
  {
    "kind": "data",
    "data": {
      "type": "node",
      "node": {
        "id": "src/pages/HomePage.tsx:30:8",
        "path": "src/pages/HomePage.tsx",
        "line": "30",
        "endLine": "35",
        "isRoot": false,
        "tag": "H1",
        "content": {
          "text": "HelloWorld",
          "className": "gradient-text text-5xl font-bold",
          "allowLink": true
        },
        "customAttr": {"href": null, "target": null},
        "style": {
          "color": "rgb(46, 26, 15)",
          "fontSize": "72px",
          "fontFamily": "\"Space Grotesk\"",
          "margin": "0px",
          "radius": "0px",
          "backgroundColor": "rgba(0, 0, 0, 0)",
          "backgroundImage": "linear-gradient(135deg, rgb(249, 115, 22), rgb(236, 72, 153))",
          "borderColor": "rgb(245, 230, 214)",
          "borderWidth": "0px",
          "borderStyle": "solid"
        }
      }
    }
  }
]
```

Everything else in the request — `contextId`, `metadata`, `queryMode`, the
lifecycle, the trajectory polling — is identical to a plain `chat`. LGUI is
purely additive.

## Node fields

| Field | Meaning |
|-------|---------|
| `id` | Node identifier, conventionally `<path>:<line>:<column>` |
| `path` | Source file containing the node |
| `line` / `endLine` | Line range of the node in that file |
| `isRoot` | Whether this is the root node of the page |
| `tag` | HTML/JSX tag name (e.g. `H1`, `DIV`, `BUTTON`) |
| `content.text` | Current visible text |
| `content.className` | Current class list |
| `content.allowLink` | Whether the node may carry a link |
| `customAttr` | Element attributes such as `href` / `target` |
| `style` | Current computed styles (color, font, spacing, border, background…) |

Only `path` plus a line range is strictly needed to locate a node; the more
fields you supply, the less the Agent has to re-derive, and the more reliably a
relative instruction ("改成蓝底黑字", "字号再小一点") resolves.

## Usage

```bash
python scripts/miaoda_api.py chat \
  --text "改成蓝底黑字" \
  --app-id <app_id> \
  --context-id <conversation_id> \
  --node-json '<json>'
```

`--node-json` accepts either form:

* the wrapper — `{"type":"node","node":{...}}`
* the bare node object — `{"id":"...","path":"...","line":"30",...}` (the CLI
  wraps it automatically)

Invalid JSON, or JSON that is not an object, exits with an error before any
request is sent.

## When to use LGUI vs plain chat

| Situation | Use |
|-----------|-----|
| User selected/clicked a specific element and you have its node data | `--node-json` |
| Change is scoped to one element ("这个标题改成蓝底黑字") | `--node-json` |
| Global change ("整体配色换成暖色调") | plain `chat` |
| Change spans multiple pages or components | plain `chat` |
| Adding a feature or new page | plain `chat` |
| You do not have real node data | plain `chat` — **never fabricate a node** |

**Do not invent node data.** A wrong `path` or line range points the Agent at
the wrong code. Node data must come from an actual selection source (the Miaoda
editor's element picker, or a caller that captured it). Without it, send a plain
`chat` and let the platform locate the target.

**Also note:**
- LGUI applies to **generated** apps — it edits existing UI code, so there must
  already be code to point at. It is not for the PRD stage.
- `--node-json` carries **one** node. For several elements, send one `chat` per
  node, updating `conversationId` from each response, or describe the change in
  plain text.

---

## trajectory

Poll trajectory events until the task reaches a terminal state.

**Usage:**

```bash
python scripts/miaoda_api.py trajectory --app-id <app_id> [options]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--last-event-id EVENT_ID`: Start eventId; -1 = all events from beginning (default: -1)
- `--poll-interval SECONDS`: Seconds between polls (default: 2.0)
- `--fetch-timeout SECONDS`: Per-request timeout in seconds (default: 10)
- `--sse`: Use legacy SSE streaming instead of polling

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py trajectory --app-id app-abc123xyz
```

**Use Cases:**
- Monitor conversation / generation progress
- Detect when PRD refinement is complete (look for Generate App button in `result.artifact.parts[].data.actions[]`)
- Verify generation has completed

---

## fetch-trajectory

Fetch one batch of trajectory events (single request, no polling loop).

**Usage:**

```bash
python scripts/miaoda_api.py fetch-trajectory --app-id <app_id> [options]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--last-event-id EVENT_ID`: Fetch events after this eventId; -1 = all (default: -1)
- `--fetch-timeout SECONDS`: Request timeout in seconds (default: 10)

**Example:**

```bash
# First call — get all events (note maxEventId from stderr)
python scripts/miaoda_api.py fetch-trajectory --app-id app-abc123xyz

# Subsequent calls — incremental fetch
python scripts/miaoda_api.py fetch-trajectory --app-id app-abc123xyz --last-event-id 345
```

Events are printed to stdout as JSON lines; `{"maxEventId": N, "isTerminal": bool}` is printed to stderr.

**Generate App readiness:** Inspect `result.artifact.parts[].data.actions[]` for an action with `"type":"button"` and `"event":{"name":"generateApp"}`. Example:

```json
{"type":"button","label":"Generate App","value":"Generate App","event":{"name":"generateApp"}}
```

When this button appears, PRD is ready and `generate-app` may be called.

---

## generate-app

Submit app-generation confirmation and return immediately with `appId`/`conversationId`.

**Usage:**

```bash
python scripts/miaoda_api.py generate-app --app-id <app_id> --context-id <context_id> [options]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--context-id CONTEXT_ID`: Conversation ID (default: "")
- `--query-mode QUERY_MODE`: Query mode (default: deep_mode)
- `--watch`: Block and poll trajectory until generation completes **(recommended)** (default: return immediately)
- `--poll-interval SECONDS`: Seconds between polls when `--watch` is set (default: 2.0)
- `--fetch-timeout SECONDS`: Per-request timeout in seconds when `--watch` is set (default: 10)

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
# Recommended: submit and block until generation finishes
python scripts/miaoda_api.py generate-app \
  --app-id app-abc123xyz \
  --context-id conv-def456uvw \
  --watch

# Alternative: submit and return immediately — check status later with fetch-trajectory
python scripts/miaoda_api.py generate-app \
  --app-id app-abc123xyz \
  --context-id conv-def456uvw
```

**Important:**
- Only call this **once** during initial creation
- Call **only** when trajectory contains a Generate App button in `result.artifact.parts[].data.actions[]`: an action with `"type":"button"` and `"event":{"name":"generateApp"}` (label may vary by locale; use event name for the check). This indicates PRD is ready.
- Do **not** call again for modifications after the first generation
- After this command, use `chat` to modify the generated app

> * **Never** call this command when `chat` returns `"needGenerateApp": false`.
>   General tasks (reports, PPT, analysis) complete in the `chat` step and
>   do not have a generation stage.

---

## publish

Trigger deployment to production.

**Usage:**

```bash
python scripts/miaoda_api.py publish --app-id <app_id> [options]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID to publish

**Optional Parameters:**
- `--env ENV`: Target environment (default: PRODUCE)
- `--wait`: Auto-poll publish status until SUCCESS or FAILED

**Examples:**

**1. Publish and manually check status:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py publish --app-id app-abc123xyz
```

Returns `releaseId` immediately. Then poll with `publish-status`.

**2. Publish and auto-wait for completion (recommended):**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py publish --app-id app-abc123xyz --wait
```

This polls automatically and exits when deployment succeeds or fails.

**Important:**
- Application must be generated at least once before publishing
- After successful publish, use the public URL returned by the platform. Do not
  construct it from `appId`.
- A final `SUCCESS` status is terminal for this publish attempt. Do not invoke
  `publish` again because downstream UI output or events were not observed.
  Retry only after a command error or an explicit final `FAILED` status.

---

## publish-status

Check the status of a deployment.

**Usage:**

```bash
python scripts/miaoda_api.py publish-status --release-id <release_id>
```

**Required Parameters:**
- `--release-id RELEASE_ID`: Release ID from `publish` command

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py publish-status --release-id app_release_record-xyz789abc
```

**Returns:** JSON with status: `PROCESSING`, `RUNNING`, `SUCCESS`, or `FAILED`

**Usage Pattern:**

```bash
# Get release ID from publish
RELEASE_ID=$(python scripts/miaoda_api.py publish --app-id app-abc123xyz | jq -r '.releaseId')

# Poll status
while true; do
  STATUS=$(python scripts/miaoda_api.py publish-status --release-id $RELEASE_ID | jq -r '.status')
  echo "Status: $STATUS"
  if [[ "$STATUS" == "SUCCESS" || "$STATUS" == "FAILED" ]]; then
    break
  fi
  sleep 5
done
```

**Tip:** Use `publish --wait` to avoid manual polling.

---

## download-source

Download the full generated application source ZIP.

Use this command when the user asks for the complete source code of a Miaoda application, a source ZIP, or a local copy of the generated app project.

**Usage:**

```bash
python scripts/miaoda_api.py download-source --app-id <app_id> [options]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--env ENV`: Source environment (default: DEV)
- `--output PATH`: Save the ZIP to a specific local path
- `--print-url`: Print the download URL without saving the file

**Examples:**

```bash
export MIAODA_API_KEY="sk_xxxxx"

# Download to an inferred local filename
python scripts/miaoda_api.py download-source --app-id app-abc123xyz

# Download to a specific local path
python scripts/miaoda_api.py download-source --app-id app-abc123xyz --output ./source.zip

# Only print the source ZIP download URL
python scripts/miaoda_api.py download-source --app-id app-abc123xyz --print-url
```

**Returns:** JSON containing `appId`, `name`, `downloadUrl`, and `localPath` when a file is saved.

**Rule:** Use `download-source` for complete application source code. Do not use Agent workspace file commands for this purpose.

---

## list-task-files

List files in the Agent workspace task directory for an application.

Use this command before downloading a specific Agent task artifact, especially when the exact filename is unknown.

**Usage:**

```bash
python scripts/miaoda_api.py list-task-files --app-id <app_id> [--include-docs-dir]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID

**Optional Parameters:**
- `--include-docs-dir`: Include docs directory files in the listing

**Example:**

```bash
export MIAODA_API_KEY="sk_xxxxx"
python scripts/miaoda_api.py list-task-files --app-id app-abc123xyz
```

**Returns:** JSON containing Agent workspace files, paths, sizes, update times, and source labels.

---

## download-task-file

Download one file from the Agent workspace task directory.

Use this command when the user asks for a specific Agent-generated task artifact, such as a report, image, document, JSON file, or other file listed by `list-task-files`.

**Usage:**

```bash
python scripts/miaoda_api.py download-task-file --app-id <app_id> --filename <filename> [options]
```

**Required Parameters:**
- `--app-id APP_ID`: Application ID
- `--filename FILENAME`: File name or `/workspace/{appId}/tasks/...` path

**Optional Parameters:**
- `--output PATH`: Save the file to a specific local path
- `--print-url`: Print the download URL without saving the file

**Examples:**

```bash
export MIAODA_API_KEY="sk_xxxxx"

# Download by filename
python scripts/miaoda_api.py download-task-file --app-id app-abc123xyz --filename report.pdf --output ./report.pdf

# Download by task path returned from list-task-files
python scripts/miaoda_api.py download-task-file \
  --app-id app-abc123xyz \
  --filename /workspace/app-abc123xyz/tasks/report.pdf
```

**Returns:** JSON containing `appId`, `filename`, `downloadUrl`, `thumbnailUrl` for images, and `localPath` when a file is saved.

**Rule:** Use `list-task-files` first when the user asks for Agent workspace artifacts but does not provide an exact filename.

---

# Download Generated Artifacts

Agents must use these commands for generated artifact downloads:

- Use `download-source` when the user wants the complete generated application source ZIP.
- Use `list-task-files` and `download-task-file` when the user wants a specific Agent workspace artifact.
- Do not call platform HTTP APIs directly.
- Do not invent local paths. Only report a local file path after the CLI successfully saves the file and returns `localPath`.
- If a generation or modification is still running, wait for completion before downloading the latest source ZIP.

---

# Complete Workflow Examples
