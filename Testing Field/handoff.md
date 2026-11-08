# Meeting Copilot 使用与交接

## B｜交付

本地应用位于 `D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\Testing Field`，运行时版本 **0.2.0**。这份指南使用 **DeepSeek V4.1-Flash-1017**，启动步骤按平台完整分开：

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
| `tool_rounds` | `0` | 程序上限为 2；DeepSeek 默认 thinking 的工具续接还需回传 `reasoning_content`，现有适配器尚未处理，先使用无额外工具轮次的路径 |

`tool_rounds=0` **仍会检索会议与批准的项目资料，并执行三种技能、生成引用**；只关闭模型在回答中追加的工具调用。`base.en`、4096 输出上限、流式响应和 `API_KEY` 环境变量均可保留。

模型名称核对自 [DeepSeek 官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)；`/v1/chat/completions` 用法见[官方接入示例](https://api-docs.deepseek.com/quick_start/agent_integrations/workbuddy/)。当前模型别名对应 1017，日后官方可能更新其路由。工具续接要求见[官方 thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

下面两个平台都给出完整 JSON。**真实 key 留在终端设置，`api_key_env` 填变量名 `API_KEY`。**

## T｜启动流程

### Windows

#### W1｜选择入口：1A 或 1B，完成后都接 W2

**1A．使用本机已经准备好的版本**

打开 PowerShell，运行：

```powershell
Set-Location 'D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\Testing Field'
$env:PYTHONUTF8 = '1'
$appDir = (Get-Location).Path
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
```

**接 W2，不需要重新安装或执行 `init`。**

**1B．第一次 clean clone**

在准备存放项目的目录打开 PowerShell，运行：

```powershell
git clone --branch meeting-copilot/prototype-20261108 --single-branch https://github.com/cyudaddy/5405.git meeting-copilot-dev
Set-Location 'meeting-copilot-dev\Testing Field'
$env:PYTHONUTF8 = '1'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,audio]'
$appDir = (Get-Location).Path
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $py -m meeting_copilot init
```

**接 W2。**

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

#### W5｜真实会议：终端 A 录音，终端 B 私问

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
$env:API_KEY = ""
$liveMeeting = Read-Host '粘贴终端A显示的会议ID'
& $py -m meeting_copilot ask $liveMeeting 'Catch me up' --skill catch_up --conversation mine
& $py -m meeting_copilot live-status $liveMeeting
```

之后在 B 继续用 `ask` 提问，A 保持录音。

*首次下载 `base.en` 需要等待。1A 本机版本已缓存 `tiny.en`，快速试录可改用它；1B clean clone 仍需首次下载所选模型。麦克风流程需在本机实测；若设备列表为空，先检查系统麦克风权限。*

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

*项目文件夹应与应用的 `data/` 分开；若选的是包含 data 的整个工程根目录，先把配置中的 `data_dir` 移到工程之外。文件改动后，再执行 `refresh-folder` 导入新版本。*

</details>

#### W6｜结束、测试与可选清理

在 **终端 A 按 Ctrl+C**，等命令返回提示符后，录音会话即结束。

<details>
<summary>可选：运行无付费 API 的评测和完整测试</summary>

```powershell
& $py -m meeting_copilot evaluate --output results/local-report.json --stress
& $py -m pytest --basetemp data/test-temp -q
```

*现有 Windows 基线为 84 通过、1 项权限跳过；完整测试约七分钟。*

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
git clone --branch meeting-copilot/prototype-20261108 --single-branch https://github.com/cyudaddy/5405.git meeting-copilot-dev
cd 'meeting-copilot-dev/Testing Field'
export PYTHONUTF8=1
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,audio]'
app_dir="$PWD"
py="$app_dir/.venv/bin/python"
"$py" -m meeting_copilot init
```

**接 M2。**

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

#### M5｜真实会议：终端 A 录音，终端 B 私问

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
export API_KEY=""
printf '粘贴终端A显示的会议ID：'
read -r live_meeting
"$py" -m meeting_copilot ask "$live_meeting" 'Catch me up' --skill catch_up --conversation mine
"$py" -m meeting_copilot live-status "$live_meeting"
```

之后在 B 继续用 `ask` 提问，A 保持录音。

*按系统提示允许 Terminal/Python 使用麦克风。第一次下载 `base.en` 需要等待；快速试录可把 `asr_model` 改为 `tiny.en`。上面写法可用于 bash/zsh，不需要切换 shell。*

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

*项目目录与应用实际 `data/` 分开；若要批准包含 data 的整个工程根，先把 `data_dir` 配到工程之外。文件改动后用 `refresh-folder` 导入新版本。*

</details>

#### M6｜结束、测试与可选清理

在 **终端 A 按 Ctrl+C**，等命令返回提示符后，录音会话即结束。

<details>
<summary>可选：运行无付费 API 的评测和完整测试</summary>

```bash
"$py" -m meeting_copilot evaluate --output results/local-report.json --stress
"$py" -m pytest --basetemp data/test-temp -q
```

*macOS 尚未实机验证，测试结果以本机输出为准。*

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
| Windows | 84 通过、1 项 symlink 权限跳过；[完整记录](results/windows-validation.json)；另有[原生 junction 检查](results/windows-junction.json) |
| Linux | 85 项通过；[验证记录](results/windows-repair3-validation.json) |
| 本地 ASR | Windows 公开音频使用 `tiny.en` 得到 2 段；[记录](results/windows-prepared.json)。本指南改用的 `base.en` 尚未单独实测 |
| 20 分钟稳定性 | Linux 预录音频实际 ASR 399 段、79 次摘要、119 次模拟慢问答，零丢弃/错误；[记录](results/audit-soak-final.json) |
| Meet2Notes 复用 | 固定提交 `b75077e588f5`，24 个实现/SQL 文件逐字节核验；[来源与许可](docs/UPSTREAM_SOURCE.json) |
| 交付 | [代码分支](https://github.com/cyudaddy/5405/tree/meeting-copilot/prototype-20261108)、[历史交付标签](https://github.com/cyudaddy/5405/tree/v0.2.0-handoff)、[交付索引](results/final-delivery.json)、[服务器已清理回执](results/server-cleanup.json) |

本次更新是指南和配置检查，未使用真实 DeepSeek key 发起付费请求，也未修改当前本地 `config.json`。默认配置、示例语法和命令参数已核对；macOS、真实麦克风和 DeepSeek 付费端点仍需实机验证。

## R｜开发 TODO

| 优先级 | 后续工作 | 建议负责人 |
|---|---|---|
| P0 | 各平台真实麦克风、拔插、20 分钟技术会议，以及 `base.en` CPU 吞吐/术语识别 | Zimo Li / Qiyan Zhai |
| P0 | DeepSeek 实际端点与人工答案质量；补 `reasoning_content` 工具回传或可配置的 non-thinking，再启用额外工具轮次 | Zimo Li / Jiaxuan Yan |
| P1 | 真实模型与 native baseline 的同数据 A/B、延迟、tokens 和成本 | Qiyan Zhai |
| P1 | graph 中间节点崩溃恢复、重放与幂等 | Xiaolong Lin / Zimo Li |
| P1 | revisions/录音/导出/trace 的保留期、磁盘配额、隐私生命周期 | Cai Yong / Xiaolong Lin |
| P2 | 更多路径/权限边界；按评测收益选择 embeddings、Langfuse、diarization、GUI/打包 | Cai Yong / Jiaxuan Yan / 相关模块负责人 |

技术细节留在[当前需求映射](results/requirements-current.json)、[独立审查](docs/INDEPENDENT_REVIEW.md)和[Windows 兼容记录](docs/WINDOWS_COMPATIBILITY.md)。提问快照/私有隔离已实现；引文匹配不等于语义正确，重要技术结论仍由团队复核。
