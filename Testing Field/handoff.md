# Meeting Copilot 交接（B / G / C / T / D / R）

最终交付标签：[v0.2.0-handoff](https://github.com/cyudaddy/5405/tree/v0.2.0-handoff)。服务器专用开发目录已删除，并经独立 SSH 检查确认不存在；[清理回执](results/server-cleanup.json)和[最终交付索引](results/final-delivery.json)已保存。

## B — 结论与交付物

交付的是 **Meeting Copilot CLI 原型 0.2.0**：本地会议/项目证据库、提问时快照、带引用的私有问答、三种任务技能、后台共享摘要、只读 MCP、可复现评测。Python 建议 **3.12**；实际 Windows 使用 **3.12.14**，Linux 使用 **3.12.3 / aarch64**。默认 `demo` 无密钥、无录音、无 API，输出明确标为模拟；真实 API 模式已实现，尚未用付费端点验证。

Windows 本地应用副本已经准备 `.venv`（dev+audio）、默认 demo 配置（`timeout_seconds=120`）、演示数据和缓存 `tiny.en`。**直接在 PowerShell 运行，无需激活环境：**

```powershell
Set-Location 'D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\Testing Field'
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m meeting_copilot demo
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
```

预期是包含 `SIMULATED`、`folder_id`、`result.snapshot_id` 的 JSON，导出位置为 `data/demo-export.json`。此副本**没有完整 `.git`**；后续开发请使用 T 节的完整仓库 clone，不要在这里执行 `git pull`。准备状态和公开录音 ASR 实测见 [Windows 准备报告](results/windows-prepared.json)。`PYTHONUTF8` 是 Python shell/消费者的 UTF-8 编码设置，避免中文设备名和 JSON 被传统中文代码页误解码，不代表核心 ASR 有缺陷。

源代码已冻结：54 个 runtime 文件与 Windows 被测清单一致，版本仍为 0.2.0。本轮只整理文档/结果，不修改源码、测试、依赖或 vendored 文件。工作分支是 [meeting-copilot/prototype-20261108](https://github.com/cyudaddy/5405/tree/meeting-copilot/prototype-20261108)；Windows 被测源码/测试提交是 [c763c1489c66b826251cf0ae93712a42a0edfa81](https://github.com/cyudaddy/5405/commit/c763c1489c66b826251cf0ae93712a42a0edfa81)。历史标签 [v0.1.0-prototype](https://github.com/cyudaddy/5405/tree/v0.1.0-prototype) 和 [v0.2.0-prototype](https://github.com/cyudaddy/5405/tree/v0.2.0-prototype) 保留；后者早于三轮测试修复，不能代替当前分支测试集。可随应用带走的交付摘要见 [server-delivery.json](results/server-delivery.json)；构建根 `DELIVERY.json` 的旧 head 不是最终文档 head，最终精确文档提交由协调者另记。

## G — 对照提案的范围

当前逐项状态以 [requirements-current.json](results/requirements-current.json) 为准；[旧 checklist](results/requirements-checklist.json) 是修复前审查快照，不再用作当前平台状态。

| 提案能力 | 已实现 | 尚需完成/验证 |
|---|---|---|
| Windows/macOS CLI、单一配置、API-first | CLI、环境隔离、OpenAI-compatible Chat Completions / Anthropic Messages；Windows 源码/CLI 已实测 | macOS 实机；每类真实付费端点 |
| 音频持续采集、ASR、并发私有 QA/摘要 | 设备选择/同意门、独立有界队列、停止时尾块、状态报告；Windows 枚举和公开音频本地 ASR | 真麦克风、拔插、真实 20 分钟技术会议 |
| Memory / snapshots | SQLite/FTS5、修订/事件去重/缺口、观察水位、保留文件版本、长文本检索、精确行号/哈希、导出 | 中间节点崩溃恢复、长期生命周期 |
| 私有连续会话与安全 | 跨快照 continuity；meeting/principal/conversation 隔离、重新授权检查、撤权、注入不授予权限 | 外部登录/多用户会话集成；更广安全评估 |
| MCP / skills / synthesis | 10 个只读 MCP 工具、catch_up / trace_decision / compare_options、模型综合+独立引文、最多两轮工具 | 人工语义质量评判 |
| Streaming / observability | 两种 SSE 协议内部组装、超时/取消/usage；本地脱敏 trace | 非逐 token UI；可选 Langfuse |
| 评测与原生 A/B | 冻结 22 题、消融/安全用例、88 请求 API evaluator、22 请求原生 service runner | 真实模型质量/成本、人工标注、术语 WER |
| 提案可选项 | 轻量词法检索和 CLI 已交付 | embeddings/Chroma、diarization/系统音频、GUI、安装包 |

## C — 架构、复用和关键假设

```text
麦克风/公开录音 → 上游采集与 Faster Whisper → 有界 ingestion → SQLite WAL 证据/修订
                                                         ├→ 独立共享摘要 worker
私有问题 → 主机身份/范围/快照 → LangGraph → FTS5 / 只读 MCP → provider → 引用校验 → 私有回答
```

复用 Meet2Notes 固定提交 [b75077e588f5623d48d9c9bac05aa428b4257c52](https://github.com/estebanstifli/Meet2Notes/tree/b75077e588f5623d48d9c9bac05aa428b4257c52) 的采集后端、ASR、媒体存储及原生 assistant/service/SQL 等；24 个实现/SQL 文件经逐字节核验。MCP guarded wrapper、CPU/path seam、native transport bridge 属于明确记录的适配；并未运行整个上游桌面/native hybrid-RAG。见 [来源](docs/UPSTREAM_SOURCE.json)、[MIT 许可](docs/UPSTREAM_LICENSE.txt)、[复用核验](results/audit-upstream.json)。

主机固定身份、会议、显式选择的历史会议/文件夹、conversation 和 snapshot。快照固定**已观察到的**转录/文件修订和私有历史水位；讲话时间不是唯一截止依据。后到 ASR/refresh 不改变旧答案；快照本身不授予权限，旧快照、缓存、checkpoint、导出和工具读均重查 ACL。共享摘要只用当前会议转录，私有问题和项目文件不会自动成为共享事实。模型工具不能运行 shell、写文件或授予权限。

数据目录相对 `config.json` 解析，默认在应用内的 `data/`。**要批准包含该 data 的祖先项目根，必须先把 `data_dir` 配到目标项目之外**；已有数据不会自动搬迁，先备份并决定迁移或新建库，再使用新配置。若确实不与实际 data 重叠，`examples/project`、`src` 等源文件子目录可以批准。refresh 显式导入版本；不在问答时静默读取新磁盘内容。

可信边界是本机用户/主机配置，不隔离恶意 OS 管理员。API 会发送当前问题、选定片段和符合范围的私有上下文；并非完全离线。引文成员关系/行号/哈希校验不能证明模型推理的语义正确，技术结论仍须人工复核。实现与边界详见 [独立审查](docs/INDEPENDENT_REVIEW.md)。

## T — 从零安装、使用与停止

### 1. 同学安装与后续开发（完整仓库）

已准备的本地副本直接用 B 节；以下命令用于**另选空目录**建立可开发的完整 checkout，不覆盖该副本。需要 Git、网络和 Python 3.12；安装 audio 额外包不等于验证麦克风。

Windows PowerShell：

```powershell
git clone --branch meeting-copilot/prototype-20261108 --single-branch https://github.com/cyudaddy/5405.git meeting-copilot-dev
Set-Location 'meeting-copilot-dev\Testing Field'
$env:PYTHONUTF8 = '1'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,audio]'
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
# 已有 config.json 则保留；init 不覆盖已有文件。
if (-not (Test-Path config.json)) { & $py -m meeting_copilot init }
# 仅调整本地等待预算，不改变 provider/上传同意。
$cfg = Get-Content -Raw -Encoding UTF8 config.json | ConvertFrom-Json
$cfg.timeout_seconds = 120
[System.IO.File]::WriteAllText((Join-Path $PWD 'config.json'), ($cfg | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))
& $py -m meeting_copilot demo
```

macOS Terminal（说明命令，尚未实机验证）：

```bash
git clone --branch meeting-copilot/prototype-20261108 --single-branch https://github.com/cyudaddy/5405.git meeting-copilot-dev
cd 'meeting-copilot-dev/Testing Field'
export PYTHONUTF8=1
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,audio]'
if [ ! -f config.json ]; then .venv/bin/python -m meeting_copilot init; fi
.venv/bin/python - <<'PYCONFIG'
import json
from pathlib import Path
p = Path('config.json')
c = json.loads(p.read_text(encoding='utf-8'))
c['timeout_seconds'] = 120
p.write_text(json.dumps(c, indent=2), encoding='utf-8')
PYCONFIG
.venv/bin/python -m meeting_copilot demo
```

若没有 `py` / `python3.12`，可用已经安装的用户级 **uv** 替换上面创建/安装环境的两行（保留相应平台后续步骤，无需 activation）：

```powershell
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -e '.[dev,audio]'
```

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev,audio]'
```

版本核对：Windows `& $py -c "import meeting_copilot; print(meeting_copilot.__version__)"`；macOS `.venv/bin/python -c 'import meeting_copilot; print(meeting_copilot.__version__)'`，均应为 `0.2.0`。只做 replay/demo 可安装 `.[dev]`，无需 ASR 模型。Linux lock 文件仅记录当时服务器环境，不要把它当作跨平台约束强制安装。之后只在完整 clone 的仓库中 `git switch meeting-copilot/prototype-20261108`、`git pull --ff-only`，再建自己的开发分支。

### 2. 无密钥项目 QA、真实 ID、历史与导出

所有命令从应用目录运行，两个终端使用同一 `config.json`；自选配置写在子命令**前**：`-m meeting_copilot --config path/to/config.json ask ...`。以下使用独立会议 `design`；第一次执行即可，重复演示可换新 meeting ID。

PowerShell 不用手抄占位 ID，直接解析实际 JSON：

```powershell
& $py -m meeting_copilot create design --title 'Design review'
& $py -m meeting_copilot replay design examples/replay.jsonl
$approved = & $py -m meeting_copilot approve-folder design examples/project | Out-String | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Folder approval failed' }
$folderId = $approved.folder_id
if (-not $folderId) { throw 'No folder_id returned' }
& $py -m meeting_copilot refresh-folder $folderId
$answer = & $py -m meeting_copilot ask design 'Compare SQLite and Kafka deployment' --folder $folderId --skill compare_options --conversation notes | Out-String | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Ask failed' }
$snapshotId = $answer.snapshot_id
if (-not $snapshotId) { throw 'No snapshot_id returned' }
$answer | ConvertTo-Json -Depth 30
& $py -m meeting_copilot ask design 'Why was it chosen?' --folder $folderId --conversation notes
& $py -m meeting_copilot summary $snapshotId
& $py -m meeting_copilot history $snapshotId --conversation notes --across-snapshots
& $py -m meeting_copilot checkpoint $snapshotId --conversation notes
& $py -m meeting_copilot export $snapshotId data/answer.json --conversation notes
```

macOS bash 同一流程（自动提取 ID）：

```bash
.venv/bin/python -m meeting_copilot create design --title 'Design review'
.venv/bin/python -m meeting_copilot replay design examples/replay.jsonl
approved=$(.venv/bin/python -m meeting_copilot approve-folder design examples/project)
folder_id=$(printf '%s' "$approved" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["folder_id"])')
.venv/bin/python -m meeting_copilot refresh-folder "$folder_id"
answer=$(.venv/bin/python -m meeting_copilot ask design 'Compare SQLite and Kafka deployment' --folder "$folder_id" --skill compare_options --conversation notes)
snapshot_id=$(printf '%s' "$answer" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["snapshot_id"])')
printf '%s\n' "$answer"
.venv/bin/python -m meeting_copilot ask design 'Why was it chosen?' --folder "$folder_id" --conversation notes
.venv/bin/python -m meeting_copilot summary "$snapshot_id"
.venv/bin/python -m meeting_copilot history "$snapshot_id" --conversation notes --across-snapshots
.venv/bin/python -m meeting_copilot checkpoint "$snapshot_id" --conversation notes
.venv/bin/python -m meeting_copilot export "$snapshot_id" data/answer.json --conversation notes
```

预期 replay 返回事件数/缺口，refresh 返回索引结果，ask 返回模式、文本、citations、snapshot_id 和 usage，export 返回路径。相同 conversation 的新 ask 会产生新快照并继承仍有权限且在范围内的历史；查询旧 `$snapshotId` 不会看到未来那一轮。`--previous MEETING_ID` 显式选择已授权历史会议；`--snapshot SNAPSHOT_ID` 重读指定旧证据。bash 中 `MEETING_ID` / `SNAPSHOT_ID` / `DEVICE_ID` / `MODEL_ID` 是需要替换的说明占位符，不要原样运行；`"$folder_id"`、`"$snapshot_id"` 是上面已赋值的真实变量。

导出含前 100 项证据及答案/摘要实际引用的其他来源，仍有截断时会标记。需要只读 MCP 客户端时，Windows `& $py -m meeting_copilot mcp $snapshotId`，macOS `.venv/bin/python -m meeting_copilot mcp "$snapshot_id"`；它通过 stdio 等待客户端，不是普通聊天窗口，Ctrl+C 停止。

### 3. 实际 API 模式（自行提供凭据）

停止正在使用配置的进程后编辑 `config.json`。以下是可用字段示例，**先把 `YOUR_PROVIDER_MODEL_ID` 换成供应商实际模型 ID**，不带 LiteLLM provider 前缀；不含任何真实 key：

```json
{
  "principal": "local-host",
  "data_dir": "data",
  "provider": "openai",
  "model": "YOUR_PROVIDER_MODEL_ID",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "MEETING_COPILOT_API_KEY",
  "send_evidence": true,
  "timeout_seconds": 120,
  "max_tokens": 800,
  "streaming": true,
  "tool_rounds": 1,
  "input_cost_per_million": null,
  "output_cost_per_million": null,
  "asr_model": "tiny.en"
}
```

Anthropic 则设 `provider: "anthropic"`、实际模型 ID、`base_url: "https://api.anthropic.com"`（适配器追加 `/v1/messages`）。OpenAI-compatible 地址须包含 `/v1`，兼容性不能据本地合同测试推广到所有厂商。只有明确愿意将选定证据发给该端点时才设 `send_evidence: true`。

Windows 当前 shell：

```powershell
$env:MEETING_COPILOT_API_KEY = '<your-app-api-key>'
& $py -m meeting_copilot ask design 'Why was SQLite chosen?' --folder $folderId --conversation api-notes
# 本次使用后清除 shell 环境变量：
Remove-Item Env:MEETING_COPILOT_API_KEY
```

macOS 当前 shell：

```bash
export MEETING_COPILOT_API_KEY='<your-app-api-key>'
.venv/bin/python -m meeting_copilot ask design 'Why was SQLite chosen?' --folder "$folder_id" --conversation api-notes
unset MEETING_COPILOT_API_KEY
```

示例中的 key 占位符必须在本机替换；不要把真实 key 写进源码、报告或截图。**Codex 订阅登录/服务器 Codex quota 与本应用的 API 凭据和计费分开**。有 Codex 登录不等于可以调用应用配置的付费端点。

`streaming=true` 会内部接收并组装 SSE，**完成引用校验后一次显示**，不是 UI 逐 token 打字。`tool_rounds` 为 0–2，工具仅限主机范围内的 search/read。一次 ask 在加载依赖、检索和端点响应期间可能安静等待：Windows 已测 CLI 冷启动约数秒至十几秒，首次 API 初始化可能更久；首次用 120 秒预算，只有测量本机启动后才考虑降到 60。源码默认仍为 20，本地准备配置已设 120。配置预算覆盖 provider 的异步操作（包括其工具轮次），但进程启动、检索/持久化另计，同步 import 也不能强行抢占，所以整条命令不保证在 120 秒内结束；这里没有真实付费延迟保证。需要时 Ctrl+C 取消，普通命令显示 `Stopped.` 并可返回 130。usage 或价格未知时成本为 null；只有填入真实费率才有意义。切回无密钥演示须恢复 `provider=demo`、`model=simulated-extractive`、`send_evidence=false`。

### 4. Live 双终端（实验流程，真实麦克风尚未验证）

先取得参与者同意，再进行目标机器实验；Windows 已枚举设备不等于已经录音。缺少设备/同意时用 replay 或下方公开录音 ASR。两个终端各设置 Python 路径/UTF-8；若选 API，两个终端都需设置自己的环境 key，否则保留 demo，只模拟回答而实际尝试采集/ASR。

Windows 终端 A：

```powershell
Set-Location 'D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\Testing Field'
$env:PYTHONUTF8 = '1'
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $py -m meeting_copilot mic-list
$deviceId = Read-Host 'Copy the actual source ID from mic-list'
& $py -m meeting_copilot create live --title 'Consented live discussion'
& $py -m meeting_copilot listen live --source $deviceId --seconds 0 --consent --download-model --summary-interval 15
```

Windows 终端 B（与 A 同目录、配置）：

```powershell
Set-Location 'D:\Yan\Cornell\Semester 1\ENMGT 5405 Applied AI for Teams\Final Project\Testing Field'
$env:PYTHONUTF8 = '1'
$py = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $py -m meeting_copilot ask live 'Catch me up' --skill catch_up --conversation mine
& $py -m meeting_copilot ask live 'Why was it chosen?' --conversation mine
& $py -m meeting_copilot live-status live
```

macOS 终端 A/B 各自 `cd` 到同一 clone 的 `Testing Field`，按系统提示允许 Terminal/Python 使用麦克风。A：

```bash
export PYTHONUTF8=1
.venv/bin/python -m meeting_copilot mic-list
read -r -p 'Actual source ID: ' device_id
.venv/bin/python -m meeting_copilot create live --title 'Consented live discussion'
.venv/bin/python -m meeting_copilot listen live --source "$device_id" --seconds 0 --consent --download-model --summary-interval 15
```

macOS B：

```bash
.venv/bin/python -m meeting_copilot ask live 'Catch me up' --skill catch_up --conversation mine
.venv/bin/python -m meeting_copilot ask live 'Why was it chosen?' --conversation mine
.venv/bin/python -m meeting_copilot live-status live
```

macOS 默认 shell 常为 zsh；上述交互 `read -r -p` 是 bash 写法，可先输入 `bash`，或直接 `device_id='实际枚举 ID'`。`--seconds 0` 表示持续至 Ctrl+C，改为 1200 可安排 20 分钟实验。首次未缓存模型时 `--download-model` 需要网络；缓存跟随实际 data_dir。每五秒 stderr 显示队列/丢弃/摘要失败，B 可检查共享摘要是否推进；证据不足应弃答，不能据空会话制造结论。A 中 Ctrl+C 后会尝试提交最后不足三秒的块并排空已接受任务，等待摘要/API 结束可能较久，请等命令返回后再清理会议。无需后台 daemon 或全局停止命令。

公开录音本地 ASR 复现（不使用麦克风或付费 API）：

```powershell
Invoke-WebRequest 'https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/tests/data/jfk.flac' -OutFile data/jfk.flac
& $py -m meeting_copilot create asr-smoke
& $py -m meeting_copilot asr asr-smoke data/jfk.flac --download-model
& $py -m meeting_copilot cleanup asr-smoke --confirm asr-smoke
```

```bash
curl -L 'https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/tests/data/jfk.flac' -o data/jfk.flac
.venv/bin/python -m meeting_copilot create asr-smoke
.venv/bin/python -m meeting_copilot asr asr-smoke data/jfk.flac --download-model
.venv/bin/python -m meeting_copilot cleanup asr-smoke --confirm asr-smoke
```

先跑过 demo 会创建 `data/`；Windows 已测该录音用 tiny.en 得到 2 个 segment，文本含 “ask not what your country can do for you”。这是英文公共片段，不代表技术术语、多语言或真实房间的识别质量。

### 5. 评测、测试、停止和清理

无付费 API 的本地验收：

```powershell
& $py -m meeting_copilot evaluate --output results/local-report.json --stress
& $py -m pytest --basetemp data/test-temp -q
```

```bash
.venv/bin/python -m meeting_copilot evaluate --output results/local-report.json --stress
.venv/bin/python -m pytest --basetemp data/test-temp -q
```

evaluate 默认为模拟冻结题；预期 22 题和安全/消融结果，不是模型质量证明。pytest 当前收集 85 项；实际 Windows 为 84 通过/1 symlink 权限跳过，Linux 为 85 通过，macOS 数量/结果须以实机为准。Windows 完整运行约七分钟，不要因首次依赖加载安静就认定挂死。本轮文档整理不重复运行该套件。

仅在自行配置并同意计费后运行（API 字段/环境 key 同上）：

```powershell
& $py -m meeting_copilot evaluate --api --output results/local-api-eval.json
& $py -m meeting_copilot native-baseline --output results/local-native.json
```

```bash
.venv/bin/python -m meeting_copilot evaluate --api --output results/local-api-eval.json
.venv/bin/python -m meeting_copilot native-baseline --output results/local-native.json
```

前者 88 请求，后者同一冻结题 22 次原生 `LiveAssistantService.ask`；应保持 provider/model/硬件一致。原生 runner 使用共同传输 bridge/合成父表，不是完整桌面 RAG 基线；没有付费实测分数。安全消融仅在 evaluator 的隔离合成库执行，生产无关闭政策开关。

所有 listen/MCP/ask 停止并返回后，只删除自己创建的测试会议：

```powershell
& $py -m meeting_copilot cleanup design --confirm design
# 如果上面确实创建了 live：
& $py -m meeting_copilot cleanup live --confirm live
```

```bash
.venv/bin/python -m meeting_copilot cleanup design --confirm design
# 如果确实创建了 live：
.venv/bin/python -m meeting_copilot cleanup live --confirm live
```

cleanup 返回 `deleted`，逻辑删除会议/依赖快照/私有状态和应用媒体；**不会删除批准的项目源码**。已有 exports、traces、备份、手动下载录音/缓存另行保留，需按自己的保留政策处理，不提供安全擦除。不要为了重试 demo 清空整个 data 或删除准备好的 `.venv`。

## D — 已测完成度与证据

| 实测环境/工作负载 | 实际结果 | 可带走的证据 |
|---|---|---|
| Windows 11 / Python 3.12.14，repair3 源码/测试 | 85 项：84 passed、1 skipped、0 failure/error；symlink 权限不足跳过；独立并发复测 1 passed / 72.25s | [Windows 总结](results/windows-validation.json)、[完整 JUnit](results/tests-windows.xml)、[并发 JUnit](results/tests-windows-concurrency.xml)、[64 文件哈希清单](results/windows-source-manifest.json)（54 runtime + 10 test） |
| Windows CLI/设备/本地 ASR | init/demo/evaluate/mic-list 成功，枚举 3 个源；公开 JFK 录音 tiny.en CPU ASR 2 段；没有真实录音/付费 API | [准备与 ASR](results/windows-prepared.json)、[环境](results/windows-environment.txt) |
| Windows 原生 NTFS junction | 扫描逃逸阻止、junction 根拒绝、非递归移除、外部目标未改；本次结果对应 c763c14 | [junction 结果](results/windows-junction.json)；不能把 junction 通过当成 symlink 权限通过 |
| Linux 最新 repair3 源码套件 | 85/85，62.597s；focused 14/14，17.245s；延迟启动 1/1，50.583s | [repair3 验证](results/windows-repair3-validation.json) |
| Linux repair2 合同扩展 | 完整 85/85；redirector 1/1、cold-start 1/1、provider cold-start 1/1；真实 ancestry+nonce、短超时/取消断言 | [repair2 验证](results/windows-repair2-validation.json) |
| 历史 v0.2 审查：source / 独立 wheel 无 audio / README 环境 | 当时各 73/73；22 CLI 操作、24 fake-wire 合同。**不是最新 85 项 wheel 重测** | [audit-validation](results/audit-validation.json) |
| 最终 Linux 真实墙钟稳定性 | 1,200.336s；实际 tiny.en CPU int8/2 threads，重复公开 11s 录音；399 段、79 摘要、119 次五秒延迟模拟 QA；丢弃/错误/观察到的未来证据违规均 0 | [最终完整 soak](results/audit-soak-final.json)；入库 p95 1.115s、max 1.445s（均不含 3s 采集累积），峰值 RSS 319,311,872 bytes，两个队列峰值均 1 |
| 合成评测与规模 | 22 题 skills 22/22、定义的安全例 10/10、未授权披露 0；三例 evaluator 政策消融；1,200 段/300 文件/目标行 3,001 | [评测](results/audit-report.json)、[规模](results/audit-scale.json)；不是人工语义质量或真实一小时会议 |

以上稳定性只采用最终完成的 soak，不用中断/未完成运行。运行时自最终 soak 以来未变，三次修复调整测试与说明，未重跑 ASR 长测。历史 `test-summary.json` 的 32 项属于 v0.1；各报告时间/范围见 [results 索引](results/README.md)。

第三轮修复前，Windows 人工 fixture 每秒 20 事件、10 次摘要导致本机文件系统上的有界队列饱和：处理 782、丢弃 184、错误 0；有引用回答、等待中双重推进及快照检查已通过。repair3 将**普通功能场景**改为最多每秒 2 事件、1 次摘要，保留进度/截止/零缺口/零丢弃/零错误和清理断言；故意过载的专门测试继续验证计数和上限。这些是特定合成工作负载观察，既不说明真麦克风/ASR 失败，也不保证任意负载零丢弃。详见 [Windows 兼容记录](docs/WINDOWS_COMPATIBILITY.md)。

macOS 实机、真实麦克风/拔插/20 分钟会议、付费端点、人工术语/多语言质量均**未验证**。模拟评测的毫秒延迟、关键词正确率和原文引文匹配不能当作真实模型性能提升或语义正确率。

## R — 限制、后续责任与收集清理

建议分工沿用提案，不代表已替队友承诺排期；工作量为粗估，依赖硬件/keys/人类标注的工作不能仅在当前服务器执行。

| 优先级 | 具体任务与验收 | 建议负责人 | 粗估/前提 |
|---|---|---|---|
| P0 | Windows/macOS 真麦克风、设备拔插、20 分钟技术会议；记录 lag/drop/RSS、并发 QA/摘要、Ctrl+C 与设备错误 | Zimo Li（orchestration/audio），Qiyan Zhai（evaluation） | 1–2 人日；两平台硬件、参与者同意和代表性会议 |
| P0 | OpenAI-compatible + Anthropic 各一个实际付费端点；同模型/硬件 native A/B；人工标注技术题/弃答/语义引用、tokens/cost/p50/p95 | Qiyan Zhai，Zimo Li | 2–3 人日；授权 API keys/预算、人类标注，当前不能无凭据执行 |
| P1 | 比较 tiny.en 与多语言/更大 ASR；技术术语、否定、口音/中英混说 WER 和 CPU 吞吐，确定可接受模型 | Zimo Li，Qiyan Zhai | 1–2 人日；同意录音与人工逐字稿 |
| P1 | 中间 graph 节点 crash-resume；provider/tool 边界持久化、重放/幂等、故障注入、重复成本处理 | Xiaolong Lin（memory），Zimo Li | 2–4 人日；当前仅最终 guarded checkpoints，不是 durable mid-node resume |
| P1 | revisions/媒体/exports/traces 的 retention、磁盘 quota、备份/删除/隐私生命周期，撤权后清理策略 | Cai Yong（security），Xiaolong Lin | 2–3 人日；先由团队确定保留政策，逻辑删除非安全擦除 |
| P2 | 实机 case/UNC/更多 reparse 边界、host session、项目敏感内容审查及 false blocking | Cai Yong，Jiaxuan Yan（MCP/skills） | 1–2 人日；Windows 目标环境；不把现有 junction 结果扩大化 |
| P2 | 按评测收益选择 embeddings/Chroma、脱敏 Langfuse；可选 diarization/系统音频、GUI/便携包 | Xiaolong Lin / Qiyan Zhai / Zimo Li；Jiaxuan Yan 维护工具技能 | 每项约 1–3 人日，包装约 2–5；需目标平台/资源与隐私决定 |

剩余项来自硬件、凭据、标注或产品生命周期需求，不是因为已撤回的一小时时限而搁置。当前 streaming、模型 synthesis、跨快照 private continuity 均已实现，不应重复列为缺失功能。

**收集与清理已完成：**已收集并核验最终源码、文档、结果和 `FINALIZE.json`；本地 64 个源码/测试文件与实际 Windows 被测提交一致。服务器专用目录在确认无遗留任务进程、Git 工作树干净后删除，随后另开 SSH 验证目录及同名链接均不存在。未修改服务器全局包或配置；本地 `.venv`、配置、演示、公开音频和模型缓存保留可用。见[实际清理回执](results/server-cleanup.json)。本轮未调用 usage reset。

最终分支和旧标签保留，包含本地验证及清理回执的交付版本以 [v0.2.0-handoff](https://github.com/cyudaddy/5405/tree/v0.2.0-handoff) 为准；该标签可解析最终精确 Git commit。本地 `dist/` 另有原审查 wheel 和最终源码 ZIP；源代码 clone 按 T 节重新建环境，勿分发机器相关的 `.venv`。
