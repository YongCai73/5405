# Meeting Copilot 使用与交接

## B｜交付

这台电脑上有两份代码，请先分清：

- **开发仓库（本指南使用）**：`D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\5405\Testing Field`，是 Git 仓库 `5405` 里的应用目录，分支 `jiaxuan/mcp-skills`，包含技能自动选择、受控工具调用和关联 trace。
- **旧的独立运行副本**：`D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\Testing Field`，没有 `.git`，代码较早、没有上述功能。它的 `config.json`、数据库和 `.venv` 都保持原样，本次没有改动；不要把它当开发仓库，也不要用新代码覆盖它。

运行时版本 **0.2.0**。这份指南使用 **DeepSeek V4.1-Flash-1017**，启动步骤按平台完整分开：

- [Windows：从这里开始](#windows)
- [macOS：从这里开始](#macos)

## G｜跑通什么

先用样例完成“会议转录 → 项目检索 → DeepSeek 回答 → 引用与导出”，再用两个终端运行真实录音和会中私问。已有本机环境可直接进入对应平台的 **1A**；第一次 clone 从 **1B** 开始。

## C｜已检查的 DeepSeek 配置

你给出的 JSON 需要调整以下四项，其余主要字段保留：

| 字段 | 指南采用值 | 原因 |
|---|---|---|
| `provider` | `openai` | 当前程序通过 OpenAI-compatible 接口连接 DeepSeek；不接受 `deepseek` 作为配置值 |
| `model` | `deepseek-flash` | 官方当前将此 API 名称路由到 V4.1-Flash-1017 |
| `timeout_seconds` | `120` | 当前程序允许的最大值为 120，不接受 240 |
| `tool_rounds` | `0` | 程序上限为 2。适配器现在会回传 `reasoning_content`，也支持新配置项 `"thinking": "disabled"`，但这两条路径**只通过了本地模拟协议测试，没有在真实 DeepSeek 端点上验证**，所以真实会议先保持 0；验证方法见 W6 / M6 |

`tool_rounds=0` **仍会检索会议与批准的项目资料，并执行三种技能（可自动选择）、生成引用**；只关闭模型在回答中追加的工具调用。`base.en`、4096 输出上限、流式响应和 `API_KEY` 环境变量均可保留。

模型名称核对自 [DeepSeek 官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)；`/v1/chat/completions` 用法见[官方接入示例](https://api-docs.deepseek.com/quick_start/agent_integrations/workbuddy/)。当前模型别名对应 1017，日后官方可能更新其路由。工具续接要求见[官方 thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

下面两个平台都给出完整 JSON。**真实 key 留在终端设置，`api_key_env` 填变量名 `API_KEY`。**

## T｜启动流程

### Windows

#### W1｜选择入口：1A 或 1B，完成后都接 W2

**1A．使用本机已经准备好的版本**

打开 PowerShell，运行：

```powershell
Set-Location 'D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\5405\Testing Field'
git status --short --branch
$env:PYTHONUTF8 = '1'
$appDir = (Get-Location).Path
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
if (-not (Test-Path config.json)) { & $py -m meeting_copilot init }
```

预期第二行显示 `## jiaxuan/mcp-skills...`。**接 W2，不需要重新安装。**

*这个 `.venv` 是 2026-11-27 用 `uv` 建的 Python 3.12 环境，已装 `.[dev,audio]`。仓库副本里原本没有 `config.json`（它被 `.gitignore` 排除），上面最后一行会在缺失时生成默认配置。旧运行副本里的配置不会被读取或改动；如果想沿用，自己把那份 `config.json` 复制过来即可，其中只有环境变量名、没有 key。*

**1B．第一次 clean clone**

在准备存放项目的目录打开 PowerShell，运行：

```powershell
git clone --branch jiaxuan/mcp-skills --single-branch https://github.com/cyudaddy/5405.git meeting-copilot-dev
Set-Location 'meeting-copilot-dev\Testing Field'
$env:PYTHONUTF8 = '1'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,audio]'
$appDir = (Get-Location).Path
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $py -m meeting_copilot init
```

**接 W2。** *`jiaxuan/mcp-skills` 合并进 `main` 之后，把上面的分支名换成 `main`。*

<details>
<summary>备用：没有 py / Python 3.12，但已安装 uv</summary>

用以下三行替代上面的创建环境和安装命令，然后设置 `$appDir`、`$py` 并执行 `init`：

```powershell
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -e '.[dev,audio]'
```

*如果只有源码、没有本机可用的 `.venv`，先进入现有 `Testing Field`，执行安装部分即可；已有 `config.json` 时跳过 `init`。*

</details>

#### W2｜保存 DeepSeek 配置

打开当前目录的 `config.json`，整份替换为下面的内容并保存为 UTF-8：

```json
{
  "principal": "local-host",
  "data_dir": "data",
  "provider": "openai",
  "model": "deepseek-flash",
  "base_url": "https://api.deepseek.com/v1",
  "api_key_env": "API_KEY",
  "send_evidence": true,
  "timeout_seconds": 120,
  "max_tokens": 4096,
  "streaming": true,
  "tool_rounds": 0,
  "input_cost_per_million": null,
  "output_cost_per_million": null,
  "asr_model": "base.en"
}
```

*费率留 `null` 即可启动；它只影响估算成本。`base.en` 首次录音时会下载，本机之前缓存的是 `tiny.en`。*

#### W3｜填 key，检查配置

先在第一行的空引号内填入自己的 DeepSeek key，再执行整个代码块：

```powershell
$env:API_KEY = ""
& $py -c "from meeting_copilot.config import Config; c=Config.load('config.json'); print('Config OK:', c.model)"
```

预期输出：`Config OK: deepseek-flash`。然后接 W4。

*`api_key_env` 是变量名，不是 key 本身。环境变量只需在每个使用 API 的终端设置一次；新开终端要重新设置。*

#### W4｜先用样例完成一次问答与导出

在同一个 PowerShell 继续运行：

```powershell
$sampleMeeting = 'sample-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $py -m meeting_copilot create $sampleMeeting --title 'Sample design review'
& $py -m meeting_copilot replay $sampleMeeting examples/replay.jsonl
$approved = & $py -m meeting_copilot approve-folder $sampleMeeting examples/project | Out-String | ConvertFrom-Json
$folderId = $approved.folder_id
& $py -m meeting_copilot refresh-folder $folderId
$answer = & $py -m meeting_copilot ask $sampleMeeting 'Compare SQLite and Kafka deployment' --folder $folderId --skill compare_options --conversation notes | Out-String | ConvertFrom-Json
$answer.text
& $py -m meeting_copilot export $answer.snapshot_id data/sample-export.json --conversation notes
```

预期：终端显示 DeepSeek 的回答及 `[E…]` 引用，结果导出到 `data/sample-export.json`。到这里，样例启动流程已完成。

*每次自动生成新会议 ID，重跑时不用先清库。当前流式响应会在收齐并检查引用后显示，等待期间可能暂时没有文字。*

#### W4b｜自动选技能、查看 trace、演示非法调用被拒

*这一步不需要 key，也不改 `config.json` 和 `data/`。*

```powershell
& $py -m meeting_copilot route 'Why was Kafka rejected?'
& $py -m meeting_copilot skills
& $py scripts/skills_demo.py
```

第一条只解释路由：给出每个技能的得分、命中的触发词和最终选择，不读证据也不调用模型。第二条列出各技能的版本、允许的工具和上限。第三条在临时目录里用本地抽取模式跑完整演示：自动选技能 → 受控取证 → 带引用回答 → 按 `task_id` 查看关联 trace，以及三种非法调用（绑定 `catch_up` 的 MCP 会话调用检索工具、模型侧调用未知工具、伪造身份参数）被拒绝并留痕。

在自己的会议里也一样：`ask` 省略 `--skill` 即为自动选择，回答 JSON 里的 `skill`、`routing`、`task_id` 说明选了什么、为什么；stderr 会打印一行 `skill_selected`。

```powershell
$auto = & $py -m meeting_copilot ask $sampleMeeting 'Why was Kafka rejected?' --folder $folderId --conversation auto-notes | Out-String | ConvertFrom-Json
$auto.skill; $auto.routing.status; $auto.text
& $py -m meeting_copilot trace-show --task $auto.task_id
```

*路由状态：`matched` 用命中的技能；`ambiguous`（并列）和 `not_applicable`（没有触发词）都退回 `direct` 关键词检索；`manual` 是你用 `--skill` 指定的。同一会话里同一个问题换技能再问，会返回第一次的回答并附带 `skill_conflict` 说明，换一个 `--conversation` 即可重新回答。细节见 [docs/MCP_SKILLS.md](docs/MCP_SKILLS.md)。*

#### W5｜真实会议：终端 A 录音，终端 B 私问

*安装和配置不用每场重做；每场新会议只需新建会议 ID、启动一次录音。同一录音可反复提问或开多个 B 窗口，共用会议 ID。新终端要各自准备目录、Python 路径和 API key。*

**终端 A：**沿用当前 PowerShell，在取得参与者同意后运行：

```powershell
$liveMeeting = 'live-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $py -m meeting_copilot create $liveMeeting --title 'Live discussion'
Write-Host "终端B使用的目录：$appDir"
Write-Host "终端B使用的会议ID：$liveMeeting"
& $py -m meeting_copilot mic-list
$deviceId = Read-Host '复制上方实际 source ID 后粘贴到这里'
& $py -m meeting_copilot listen $liveMeeting --source $deviceId --seconds 0 --consent --download-model --summary-interval 15
```

预期：A 持续运行，并定期显示队列/摘要状态。

**终端 B：**新开 PowerShell，按提示粘贴 A 显示的目录和会议 ID，并填入 key：

```powershell
$appDir = Read-Host '粘贴终端A显示的应用目录'
Set-Location $appDir
$env:PYTHONUTF8 = '1'
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$env:API_KEY = "" # 在空引号中填 key；每个新终端都要单独设置
$liveMeeting = Read-Host '粘贴终端A显示的会议ID'
& $py -m meeting_copilot live-status $liveMeeting
```

*`$env:API_KEY` 只对当前 PowerShell 及其子进程有效，A、B、C 等终端需各自设置。`config.json` 只保留 `"api_key_env": "API_KEY"`，不把真实 key 填进去。*

**常用：概括近期讨论，只显示回答正文**

```powershell
$resultJson = & $py -m meeting_copilot ask $liveMeeting 'Summarize the recent discussion in at most 3 sentences.' --skill catch_up --conversation mine
if ($LASTEXITCODE -ne 0) { throw 'ask 失败，请先处理上方错误，再继续问答或导出' }
$result = $resultJson | Out-String | ConvertFrom-Json
$result.text
```

*省略 `--skill` 时现在会自动选择技能：问题里有 summarize、recap、catch up 等触发词会走 `catch_up`；一个触发词都没命中时退回 `direct` 关键词检索，这时通用总结问题仍可能检索不到证据，即使全英文也会发生。所以概括近期内容时，建议像上面这样显式写 `--skill catch_up`。`catch_up` 读取提交时最近 8 段已入库转写，概括近期内容。*

*先看 `$result.text`；仅看 `$result.inferences.text` 可能是空的。若正文仍为 `Insufficient evidence...`，表示没有形成可用引文，需继续排查转写质量、模型输出或校验，不能只凭它断定原因。若出现 `missing_api_key` 等错误，先在当前终端设置 key、重跑 `ask`，成功后再导出。*

之后在 B 继续用 `ask` 提问，A 保持录音。连续追问保留 `--conversation mine`；独立话题换成其他名称，如 `design`、`tasks`。录音停止后，只要没有清理会议，也可继续按原会议 ID 提问。

*当前 `base.en` 是英语专用模型，代码也固定 `language='en'`；中英混说可能误转。它与技能选择是独立限制，只改模型名还不能解除固定英语参数。首次下载 `base.en` 需要等待；1A 已缓存 `tiny.en`，1B 则需首次下载。设备列表为空时先检查麦克风权限。*

<details>
<summary>可选：让这场真实会议也读取自己的项目，并导出回答</summary>

在终端 B 执行：

```powershell
$projectDir = Read-Host '输入项目文件夹完整路径'
$approved = & $py -m meeting_copilot approve-folder $liveMeeting $projectDir | Out-String | ConvertFrom-Json
$liveFolderId = $approved.folder_id
& $py -m meeting_copilot refresh-folder $liveFolderId
$liveAnswer = & $py -m meeting_copilot ask $liveMeeting 'How does the project implement the current decision?' --folder $liveFolderId --conversation mine | Out-String | ConvertFrom-Json
$liveAnswer.text
& $py -m meeting_copilot export $liveAnswer.snapshot_id data/live-export.json --conversation mine
```

*优先选择不含应用 `data/` 的源码目录，例如工程的 `src`。若需要改 `data_dir`，先停止录音，回 W2 配置工程外路径，再从 W5 新开会议。文件改动后用 `refresh-folder` 导入新版本。*

</details>

#### W6｜结束、测试与可选清理

在 **终端 A 按 Ctrl+C**，等命令返回提示符后，录音会话即结束。

<details>
<summary>可选：运行无付费 API 的评测和完整测试</summary>

```powershell
& $py -m meeting_copilot evaluate --output results/local-report.json --stress --strict
& $py -m pytest --basetemp data/test-temp -q
```

*`jiaxuan/mcp-skills` 在这台 Windows 电脑上 2026-11-27 的结果：275 通过、10 项平台相关跳过、0 失败，完整测试约五分半钟；严格评测门禁 12 项全部通过。较早的 84 通过、1 项跳过是旧提交的历史记录。个别桌面问答测试对机器负载敏感，与其他重任务同时跑时可能超时，单独重跑即可。*

</details>

<details>
<summary>可选（会产生少量 DeepSeek 费用）：实测工具轮次是否被真实端点接受</summary>

*这一步**尚未有人在真实端点上跑过**。适配器的 `reasoning_content` 回传和 `thinking` 开关目前只通过了本地模拟协议测试（`tests/test_deepseek_protocol.py`）。*

先把 `config.json` 另存一份备份，再把其中两项改为 `"tool_rounds": 1`，并新增一行 `"thinking": null`（保持 DeepSeek 默认的 thinking 模式）。在已设置 `$env:API_KEY` 的终端运行：

```powershell
$probe = & $py -m meeting_copilot ask $sampleMeeting 'Why was Kafka rejected?' --folder $folderId --conversation probe-thinking | Out-String | ConvertFrom-Json
$probe.tool_events; $probe.abstained; $probe.usage
& $py -m meeting_copilot trace-show --task $probe.task_id
```

- 正常返回且 `abstained` 为 `False`：thinking 模式下的工具回传可用。`tool_events` 为空只说明模型这次没有调用工具，换一个需要查文件的问题再试。
- 报 `badrequest`：把 `"thinking"` 改成 `"disabled"`，换一个 `--conversation` 名称重跑。若这样可以，说明 non-thinking 路径可用、thinking 回传仍有问题。
- 两种都失败：恢复备份（`tool_rounds: 0`），把 `trace-show` 输出交给 Jiaxuan Yan。

无论结果如何，请把结论补进本文件的 D 表，并注明日期和模型名。


</details>

<details>
<summary>可选：删除刚创建的测试会议</summary>

保留的会议可供以后继续查询；只在不再需要时运行对应命令：

```powershell
& $py -m meeting_copilot cleanup $sampleMeeting --confirm $sampleMeeting
& $py -m meeting_copilot cleanup $liveMeeting --confirm $liveMeeting
```

*分别在仍保存对应变量的终端执行。已有导出文件会保留。*

</details>

### macOS

#### M1｜选择入口：1A 或 1B，完成后都接 M2

**1A．这台 Mac 上已经有可用的源码和 `.venv`**

打开 Terminal，把第一行路径改为本机实际目录后运行：

```bash
cd "/你的项目路径/Testing Field"
export PYTHONUTF8=1
app_dir="$PWD"
py="$app_dir/.venv/bin/python"
```

**接 M2，不需要重新安装或执行 `init`。**

**1B．第一次 clean clone**

在准备存放项目的目录打开 Terminal，运行：

```bash
git clone --branch jiaxuan/mcp-skills --single-branch https://github.com/cyudaddy/5405.git meeting-copilot-dev
cd 'meeting-copilot-dev/Testing Field'
export PYTHONUTF8=1
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,audio]'
app_dir="$PWD"
py="$app_dir/.venv/bin/python"
"$py" -m meeting_copilot init
```

**接 M2。** *`jiaxuan/mcp-skills` 合并进 `main` 之后，把上面的分支名换成 `main`。*

<details>
<summary>备用：没有 python3.12，但已安装 uv</summary>

用以下三行替代上面的创建环境和安装命令，然后设置 `app_dir`、`py` 并执行 `init`：

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev,audio]'
```

*只有源码时，在现有目录执行安装部分；不要沿用从 Windows 复制过来的 `.venv`。已有配置时跳过 `init`。*

</details>

#### M2｜保存 DeepSeek 配置

打开当前目录的 `config.json`，整份替换为下面的内容并保存为 UTF-8：

```json
{
  "principal": "local-host",
  "data_dir": "data",
  "provider": "openai",
  "model": "deepseek-flash",
  "base_url": "https://api.deepseek.com/v1",
  "api_key_env": "API_KEY",
  "send_evidence": true,
  "timeout_seconds": 120,
  "max_tokens": 4096,
  "streaming": true,
  "tool_rounds": 0,
  "input_cost_per_million": null,
  "output_cost_per_million": null,
  "asr_model": "base.en"
}
```

*费率可继续留 `null`。`base.en` 会在首次录音时下载。*

#### M3｜填 key，检查配置

先在第一行的空引号内填入自己的 DeepSeek key，再执行：

```bash
export API_KEY=""
"$py" -c "from meeting_copilot.config import Config; c=Config.load('config.json'); print('Config OK:', c.model)"
```

预期输出：`Config OK: deepseek-flash`。然后接 M4。

*`api_key_env` 填 `API_KEY`，不填真实 key。新开终端时，重新执行环境变量设置。*

#### M4｜先用样例完成一次问答与导出

在同一个 Terminal 继续运行：

```bash
sample_meeting="sample-$(date +%Y%m%d-%H%M%S)"
"$py" -m meeting_copilot create "$sample_meeting" --title 'Sample design review'
"$py" -m meeting_copilot replay "$sample_meeting" examples/replay.jsonl
approved=$("$py" -m meeting_copilot approve-folder "$sample_meeting" examples/project)
folder_id=$(printf '%s' "$approved" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["folder_id"])')
"$py" -m meeting_copilot refresh-folder "$folder_id"
answer=$("$py" -m meeting_copilot ask "$sample_meeting" 'Compare SQLite and Kafka deployment' --folder "$folder_id" --skill compare_options --conversation notes)
snapshot_id=$(printf '%s' "$answer" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["snapshot_id"])')
printf '%s' "$answer" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["text"])'
"$py" -m meeting_copilot export "$snapshot_id" data/sample-export.json --conversation notes
```

预期：终端显示 DeepSeek 的回答及 `[E…]` 引用，结果导出到 `data/sample-export.json`。到这里，样例启动流程已完成。

*流式响应收齐并检查引用后才显示，不是逐字打印；新会议 ID 自动生成，可重跑。*

#### M4b｜自动选技能、查看 trace、演示非法调用被拒

*这一步不需要 key，也不改 `config.json` 和 `data/`。*

```bash
"$py" -m meeting_copilot route 'Why was Kafka rejected?'
"$py" -m meeting_copilot skills
"$py" scripts/skills_demo.py
```

第一条只解释路由：给出每个技能的得分、命中的触发词和最终选择，不读证据也不调用模型。第二条列出各技能的版本、允许的工具和上限。第三条在临时目录里用本地抽取模式跑完整演示：自动选技能 → 受控取证 → 带引用回答 → 按 `task_id` 查看关联 trace，以及三种非法调用（绑定 `catch_up` 的 MCP 会话调用检索工具、模型侧调用未知工具、伪造身份参数）被拒绝并留痕。

在自己的会议里也一样：`ask` 省略 `--skill` 即为自动选择，回答 JSON 里的 `skill`、`routing`、`task_id` 说明选了什么、为什么；stderr 会打印一行 `skill_selected`。

```bash
auto=$("$py" -m meeting_copilot ask "$sample_meeting" 'Why was Kafka rejected?' --folder "$folder_id" --conversation auto-notes)
task_id=$(printf '%s' "$auto" | "$py" -c 'import json,sys; r=json.load(sys.stdin); print(r["skill"], r["routing"]["status"], file=sys.stderr); print(r["task_id"])')
"$py" -m meeting_copilot trace-show --task "$task_id"
```

*路由状态：`matched` 用命中的技能；`ambiguous`（并列）和 `not_applicable`（没有触发词）都退回 `direct` 关键词检索；`manual` 是你用 `--skill` 指定的。同一会话里同一个问题换技能再问，会返回第一次的回答并附带 `skill_conflict` 说明，换一个 `--conversation` 即可重新回答。细节见 [docs/MCP_SKILLS.md](docs/MCP_SKILLS.md)。*

#### M5｜真实会议：终端 A 录音，终端 B 私问

*安装和配置不用每场重做；每场新会议只需新建会议 ID、启动一次录音。同一录音可反复提问或开多个 B 窗口，共用会议 ID。新终端要各自准备目录、Python 路径和 API key。*

**终端 A：**沿用当前 Terminal，在取得参与者同意后运行：

```bash
live_meeting="live-$(date +%Y%m%d-%H%M%S)"
"$py" -m meeting_copilot create "$live_meeting" --title 'Live discussion'
printf '终端B使用的目录：%s\n会议ID：%s\n' "$app_dir" "$live_meeting"
"$py" -m meeting_copilot mic-list
printf '复制上方实际 source ID 后粘贴到这里：'
read -r device_id
"$py" -m meeting_copilot listen "$live_meeting" --source "$device_id" --seconds 0 --consent --download-model --summary-interval 15
```

预期：A 持续运行，并定期显示队列/摘要状态。

**终端 B：**新开 Terminal，按提示粘贴 A 显示的目录和会议 ID，并填入 key：

```bash
printf '粘贴终端A显示的应用目录：'
read -r app_dir
cd "$app_dir"
export PYTHONUTF8=1
py="$app_dir/.venv/bin/python"
export API_KEY="" # 在空引号中填 key；每个新终端都要单独设置
printf '粘贴终端A显示的会议ID：'
read -r live_meeting
"$py" -m meeting_copilot live-status "$live_meeting"
```

*`export API_KEY` 只对当前 shell 及其子进程有效，A、B、C 等终端需各自设置。`config.json` 只保留 `"api_key_env": "API_KEY"`，不把真实 key 填进去。*

**常用：概括近期讨论，只显示回答正文**

```bash
result=$("$py" -m meeting_copilot ask "$live_meeting" 'Summarize the recent discussion in at most 3 sentences.' --skill catch_up --conversation mine) &&
printf '%s' "$result" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["text"])'
```

*省略 `--skill` 时现在会自动选择技能：问题里有 summarize、recap、catch up 等触发词会走 `catch_up`；一个触发词都没命中时退回 `direct` 关键词检索，这时通用总结问题仍可能检索不到证据，即使全英文也会发生。所以概括近期内容时，建议像上面这样显式写 `--skill catch_up`。`catch_up` 读取提交时最近 8 段已入库转写，概括近期内容。*

*先看 JSON 的 `text`；只看 `inferences` 可能是空的。若正文仍为 `Insufficient evidence...`，表示没有形成可用引文，需继续排查转写质量、模型输出或校验，不能只凭它断定原因。若出现 `missing_api_key` 等错误，先在当前终端设置 key、重跑 `ask`，成功后再导出。*

之后在 B 继续用 `ask` 提问，A 保持录音。连续追问保留 `--conversation mine`；独立话题换成其他名称，如 `design`、`tasks`。录音停止后，只要没有清理会议，也可继续按原会议 ID 提问。

*当前 `base.en` 是英语专用模型，代码也固定 `language='en'`；中英混说可能误转。它与技能选择是独立限制，只改模型名还不能解除固定英语参数。按系统提示允许麦克风权限；首次下载模型需要等待。上面写法可用于 bash/zsh。*

<details>
<summary>可选：让这场真实会议也读取自己的项目，并导出回答</summary>

在终端 B 执行：

```bash
printf '输入项目文件夹完整路径：'
read -r project_dir
approved=$("$py" -m meeting_copilot approve-folder "$live_meeting" "$project_dir")
live_folder_id=$(printf '%s' "$approved" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["folder_id"])')
"$py" -m meeting_copilot refresh-folder "$live_folder_id"
live_answer=$("$py" -m meeting_copilot ask "$live_meeting" 'How does the project implement the current decision?' --folder "$live_folder_id" --conversation mine)
live_snapshot_id=$(printf '%s' "$live_answer" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["snapshot_id"])')
printf '%s' "$live_answer" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["text"])'
"$py" -m meeting_copilot export "$live_snapshot_id" data/live-export.json --conversation mine
```

*优先选择不含应用 `data/` 的源码目录，例如工程的 `src`。若需要改 `data_dir`，先停止录音，回 M2 配置工程外路径，再从 M5 新开会议。文件改动后用 `refresh-folder` 导入新版本。*

</details>

#### M6｜结束、测试与可选清理

在 **终端 A 按 Ctrl+C**，等命令返回提示符后，录音会话即结束。

<details>
<summary>可选：运行无付费 API 的评测和完整测试</summary>

```bash
"$py" -m meeting_copilot evaluate --output results/local-report.json --stress --strict
"$py" -m pytest --basetemp data/test-temp -q
```

*macOS 尚未实机验证，测试结果以本机输出为准。*

</details>

<details>
<summary>可选（会产生少量 DeepSeek 费用）：实测工具轮次是否被真实端点接受</summary>

*这一步**尚未有人在真实端点上跑过**。适配器的 `reasoning_content` 回传和 `thinking` 开关目前只通过了本地模拟协议测试（`tests/test_deepseek_protocol.py`）。*

先把 `config.json` 另存一份备份，再把其中两项改为 `"tool_rounds": 1`，并新增一行 `"thinking": null`（保持 DeepSeek 默认的 thinking 模式）。在已 `export API_KEY` 的终端运行：

```bash
probe=$("$py" -m meeting_copilot ask "$sample_meeting" 'Why was Kafka rejected?' --folder "$folder_id" --conversation probe-thinking)
printf '%s' "$probe" | "$py" -c 'import json,sys; r=json.load(sys.stdin); print(r["tool_events"], r["abstained"], r["usage"]); print(r["task_id"])'
"$py" -m meeting_copilot trace-show --task 上一行输出的task_id
```

- 正常返回且 `abstained` 为 `False`：thinking 模式下的工具回传可用。`tool_events` 为空只说明模型这次没有调用工具，换一个需要查文件的问题再试。
- 报 `badrequest`：把 `"thinking"` 改成 `"disabled"`，换一个 `--conversation` 名称重跑。若这样可以，说明 non-thinking 路径可用、thinking 回传仍有问题。
- 两种都失败：恢复备份（`tool_rounds: 0`），把 `trace-show` 输出交给 Jiaxuan Yan。

无论结果如何，请把结论补进本文件的 D 表，并注明日期和模型名。


</details>

<details>
<summary>可选：删除刚创建的测试会议</summary>

只在不再需要时，分别在保存对应变量的终端执行：

```bash
"$py" -m meeting_copilot cleanup "$sample_meeting" --confirm "$sample_meeting"
"$py" -m meeting_copilot cleanup "$live_meeting" --confirm "$live_meeting"
```

*已有导出文件会保留。*

</details>

## D｜已有验证与交付记录

| 项目 | 结果 / 记录 |
|---|---|
| MCP＋Skills（本分支，Windows，2026-11-27） | 275 通过、10 项平台相关跳过、0 失败；严格评测门禁 12/12；路由回归 24/24、技能控制 13/13，均为本地确定性结果。DeepSeek 工具轮次**仅模拟协议测试**，真实端点未测。[报告](results/mcp-skills-validation-2026-11-27.md)、[设计说明](docs/MCP_SKILLS.md) |
| Windows（历史，旧提交） | 84 通过、1 项 symlink 权限跳过；[完整记录](results/windows-validation.json)；另有[原生 junction 检查](results/windows-junction.json) |
| Linux | 85 项通过；[验证记录](results/windows-repair3-validation.json) |
| 本地 ASR | Windows 公开音频使用 `tiny.en` 得到 2 段；[记录](results/windows-prepared.json)。本指南改用的 `base.en` 尚未单独实测 |
| 20 分钟稳定性 | Linux 预录音频实际 ASR 399 段、79 次摘要、119 次模拟慢问答，零丢弃/错误；[记录](results/audit-soak-final.json) |
| Meet2Notes 复用 | 固定提交 `b75077e588f5`，24 个实现/SQL 文件逐字节核验；[来源与许可](docs/UPSTREAM_SOURCE.json) |
| 交付 | [MCP＋Skills 分支](https://github.com/cyudaddy/5405/tree/jiaxuan/mcp-skills)（待审查，未合并 main）、[早期代码分支](https://github.com/cyudaddy/5405/tree/meeting-copilot/prototype-20261108)、[历史交付标签](https://github.com/cyudaddy/5405/tree/v0.2.0-handoff)、[交付索引](results/final-delivery.json)、[服务器已清理回执](results/server-cleanup.json) |

2026-11-27 的 MCP＋Skills 更新没有使用真实 DeepSeek key、没有发起付费请求，也没有读取或修改任何现有 `config.json` 和 key 文件。新增功能的验证全部是本地确定性测试和模拟协议测试：它们说明路由、工具限制、恢复和 trace 的代码按约定执行，**不说明**真实模型在技能下回答更好，也不说明真实 DeepSeek 端点接受工具轮次。macOS、真实麦克风和 DeepSeek 付费端点仍需实机验证。

另外核实了一点：`ask` 的回答并不经过 MCP。MCP 是给外部客户端用的 stdio 只读入口；模型的工具调用是进程内的函数调用循环。两者现在共用同一道授权、参数和技能白名单检查，但不能把内部调用说成 MCP 端到端验证。

## R｜开发 TODO

| 优先级 | 后续工作 | 建议负责人 |
|---|---|---|
| P0 | 各平台真实麦克风、拔插、20 分钟技术会议，以及 `base.en` CPU 吞吐/术语识别 | Zimo Li / Qiyan Zhai |
| P0 | 在真实 DeepSeek 端点上跑 W6 / M6 的工具轮次实测（`reasoning_content` 回传和 `thinking: disabled` 已实现，仅模拟验证），通过后再把 `tool_rounds` 调高；人工检查答案质量 | Zimo Li / Jiaxuan Yan |
| P1 | 用真实模型跑 `evaluate --api`，对比 `skills` / `direct` / `auto` 的质量、引用、延迟、tokens 和成本，并做人工语义复核；现有对照只在模拟模式下跑过，不能说明技能有无收益 | Jiaxuan Yan / Qiyan Zhai |
| P2 | 路由只认英文触发词，并列时退回 `direct` 而不是询问用户；按真实提问补触发词或加交互式消歧。若需要模型工具也真正经过 MCP 客户端，需另做设计 | Jiaxuan Yan |
| P1 | 真实模型与 native baseline 的同数据 A/B、延迟、tokens 和成本 | Qiyan Zhai |
| P1 | graph 中间节点崩溃恢复、重放与幂等 | Xiaolong Lin / Zimo Li |
| P1 | revisions/录音/导出/trace 的保留期、磁盘配额、隐私生命周期 | Cai Yong / Xiaolong Lin |
| P2 | 更多路径/权限边界；按评测收益选择 embeddings、Langfuse、diarization、GUI/打包 | Cai Yong / Jiaxuan Yan / 相关模块负责人 |

技术细节留在[当前需求映射](results/requirements-current.json)、[独立审查](docs/INDEPENDENT_REVIEW.md)和[Windows 兼容记录](docs/WINDOWS_COMPATIBILITY.md)。提问快照/私有隔离已实现；引文匹配不等于语义正确，重要技术结论仍由团队复核。
