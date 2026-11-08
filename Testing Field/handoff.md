# Meeting Copilot 原型交接（2026-11-08）

已交付可运行的 Python CLI：回放/导入 → 保留转录与文件版本 → 提问时快照 → 私有技能问答 → 精确引用 → 导出。默认模式明确为 **SIMULATED / deterministic extractive**，不调用付费 API、不执行 ASR；真实 API 模式目前让模型选择证据原句并验证引用，不提供已经验证的自由综合推理质量。[source: src/meeting_copilot/cli.py; src/meeting_copilot/assistant.py]

仓库分支 `meeting-copilot/prototype-20261108`；首个功能提交 `b42ddcf8a22217ca5757fc5e577c77d42a8d2fb0`；交付提交/标签以构建根目录 `DELIVERY.json` 及 `git rev-parse HEAD`、`git rev-list -n 1 v0.1.0-prototype` 为准。团队原有 main、文档、骨架和历史均保留；没有 force push。上游固定 `b75077e588f5623d48d9c9bac05aa428b4257c52`。[source: docs/UPSTREAM_SOURCE.json; git history]

## 对照提案的完成情况

| 提案工作流 | 本次实际交付 | 验证/限制 |
|---|---|---|
| 跨平台 CLI、单配置、无 GPU/Docker | Python 3.12 venv，`config.json`，安装包可独立运行 | Linux aarch64 新 venv/wheel 实测；Windows/macOS 未运行 |
| 上游复用 | 原始采集后端、FasterWhisperEngine、MeetingStorage、实体/协议、证据提示、LiteLLM 参数过滤；MCP 注册结构适配 | MIT 与逐文件 SHA256 保留；不依赖临时 sibling clone |
| 实时音频 | 设备枚举/选择、`--consent`、3 秒块、4 个音频队列槽、32 个转录槽、独立入库 | 慢 API 并发测试通过；服务器缺 PortAudio，真实麦克风未验证 |
| 本地 ASR | tiny.en / CPU int8 / 2 threads，显式首次下载，文件 ASR 与实时路径 | 11 秒 JFK 录音真实运行成功，2 段；不是技术术语准确率或持续麦克风测试 |
| Memory | SQLite/FTS5、主机 principal/ACL、event 去重/序列缺口、observed_at/入库水位、全部修订、快照固定 ID | 测试迟到语音、更正、final rewrite、旧快照与撤权 |
| 私有 QA | meeting/user/conversation/snapshot 键隔离；最终图状态 checkpoint 与答案缓存；手动 promotion 标 user supplement | 每次读取重新检查权限；不向共享摘要输入私有问答 |
| 文件连接器 | 明确批准根目录；文本/Markdown/源码；有界 list/search/read；显式 refresh；保留 manifest/内容/hash/行号 | traversal、symlink、敏感文件、二进制、超限、旧版保留和原文件 hash 测试；Windows junction 待实机 |
| LangGraph/skills | QA 图与共享摘要图、独立 worker、catch_up / trace_decision / compare_options 合同与字段输出 | 有引用/空证据弃答；共享摘要为转录摘录；无第二个上游答复生成器 |
| MCP | 真实 stdio 初始化、tools/list、tools/call；10 个只读工具 | 主机绑定 principal/snapshot，无 shell/write/root/user 参数；真实 transport 测试通过 |
| API | LiteLLM OpenAI Chat Completions / Anthropic Messages；非流式、超时/取消、用量/错误归一 | 两协议本地 HTTP 假端点实测；未调用真实付费 API；不执行模型自主工具调用 |
| Traces | 本地 allowlist NDJSON，snapshot/source/policy/latency/drop/tokens/nullable cost；导出再次裁剪 | 禁用自动 LangSmith tracing；Langfuse 实际上传/托管待做 |
| Eval | 冻结 22 QA、10 隔离攻击、skills/direct 相同证据、snapshot/folder/policy ablation、队列压力、前后报告 | 全为模拟/摘录评测；native baseline 未测；不宣称泛化提升 |

## Windows 从零运行（PowerShell）

前提：已安装 Git 和 Python 3.12；仓库 SSH 权限可用。下面命令从你选定的工作目录执行；若协调者已同步应用文件夹，直接进入其 `Testing Field` 后从创建缓存/venv 开始即可。

```powershell
git clone --branch meeting-copilot/prototype-20261108 git@github.com:cyudaddy/5405.git 5405-prototype
Set-Location '.\5405-prototype\Testing Field'
New-Item -ItemType Directory -Force '.cache\pip', 'data\tmp' | Out-Null
$env:TEMP = (Resolve-Path '.\data\tmp').Path
$env:TMP = $env:TEMP
py -3.12 -m venv .venv
$mcPy = '.\.venv\Scripts\python.exe'
& $mcPy -m pip install --cache-dir .cache/pip -e '.[dev]'
& $mcPy -m meeting_copilot init
$demo = & $mcPy -m meeting_copilot demo | ConvertFrom-Json
$demo.notice
$demo.result.text
$demo.export
$fid = $demo.folder_id
$sid = $demo.result.snapshot_id
& $mcPy -m meeting_copilot ask demo 'What FTS5 retrieval baseline is implemented?' --folder $fid --skill trace_decision
& $mcPy -m meeting_copilot ask demo 'Compare SQLite and Kafka deployment' --folder $fid --skill compare_options
& $mcPy -m meeting_copilot ask demo 'Catch up on the discussion' --skill catch_up
& $mcPy -m meeting_copilot export $sid 'data\my-export.json' --conversation demo
& $mcPy -m meeting_copilot history $sid --conversation demo
& $mcPy -m meeting_copilot checkpoint $sid --conversation demo
& $mcPy -m meeting_copilot evaluate --output results/local-report.json --stress
& $mcPy -m pytest --basetemp data/test-temp -q
```

预期：demo 提示 SIMULATED；答案带 `[E数字]`，JSON citations 包含修订、SHA256、语音时间或文件路径/行号；导出保存在 data；当前服务器完整测试为 32 passed（不是 Windows 已通过）。`init` 只执行一次，重复执行会拒绝覆盖配置。[source: results/tests.xml; src/meeting_copilot/cli.py]

## macOS 从零运行（Terminal）

```bash
git clone --branch meeting-copilot/prototype-20261108 git@github.com:cyudaddy/5405.git 5405-prototype
cd '5405-prototype/Testing Field'
mkdir -p data/tmp .cache/pip
export TMPDIR="$PWD/data/tmp"
export XDG_CACHE_HOME="$PWD/.cache"
python3.12 -m venv .venv
.venv/bin/python -m pip install --cache-dir .cache/pip -e '.[dev]'
.venv/bin/python -m meeting_copilot init
.venv/bin/python -m meeting_copilot demo
.venv/bin/python -m meeting_copilot create review --title 'Technical review'
.venv/bin/python -m meeting_copilot replay review examples/replay.jsonl
.venv/bin/python -m meeting_copilot approve-folder review examples/project
```

记录返回的 folder_id，把以下 `FOLDER_ID` 替换为它；问题返回的 snapshot_id 同理替换 `SNAPSHOT_ID`。ID 是运行时值，不能直接输入这些占位符。

```bash
.venv/bin/python -m meeting_copilot refresh-folder FOLDER_ID
.venv/bin/python -m meeting_copilot ask review 'Compare SQLite and Kafka deployment' --folder FOLDER_ID --skill compare_options
.venv/bin/python -m meeting_copilot export SNAPSHOT_ID 'data/review-export.json'
.venv/bin/python -m meeting_copilot evaluate --output results/local-report.json --stress
.venv/bin/python -m pytest --basetemp data/test-temp -q
```

## 配置、真实 API 与数据出境选择

`config.json` 是可信主机配置，principal 不由模型选择；不是抵抗恶意本机 OS 用户的登录系统。不要把主机文件写权限授予不可信模型。`data_dir` 相对于 config 所在目录解析；密钥只从 `api_key_env` 指定的环境变量读取。可复制 `config.example.json`；不要提交密钥或运行 DB。[source: src/meeting_copilot/config.py; src/meeting_copilot/provider.py]

真实服务需要编辑同一个配置文件：

```json
{
  "principal": "local-host",
  "data_dir": "data",
  "provider": "openai",
  "model": "YOUR_MODEL_ID",
  "base_url": "https://YOUR_PROVIDER_HOST/v1",
  "api_key_env": "MEETING_COPILOT_API_KEY",
  "send_evidence": true,
  "timeout_seconds": 20,
  "max_tokens": 800,
  "input_cost_per_million": null,
  "output_cost_per_million": null,
  "asr_model": "tiny.en"
}
```

Anthropic 协议改 `provider` 为 `anthropic`、填对应模型、`base_url` 填服务 origin（适配器追加 `/v1/messages`）；不要仅修改 OpenAI URL 后宣称协议相同。价格由用户填写两个每百万 token 费率，否则 cost 为 null。API 会发送问题和选中的证据摘录；需先取得参与者对这种使用方式的同意。没有真实 API 密钥时保持 demo；**Codex CLI OAuth 不能作为应用凭据**。[source: src/meeting_copilot/provider.py; tests/test_protocols.py]

PowerShell 不把密钥直接写进命令历史：

```powershell
$env:MEETING_COPILOT_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Provider API key' -AsSecureString)).Password
& $mcPy -m meeting_copilot ask demo 'Why was Kafka rejected?' --skill trace_decision
Remove-Item Env:MEETING_COPILOT_API_KEY
```

macOS 当前 shell 输入（输入后回车，不会回显）：

```bash
read -r -s MEETING_COPILOT_API_KEY
export MEETING_COPILOT_API_KEY
.venv/bin/python -m meeting_copilot ask demo 'Why was Kafka rejected?' --skill trace_decision
unset MEETING_COPILOT_API_KEY
```

成功时 mode 为 `API / validated extractive quotations`，usage 来自提供方；失败只暴露归一错误码。实际付费端点/实际账单尚未测试；不要把 fake HTTP 的 17/11 token 当真实服务质量。[source: tests/test_protocols.py; src/meeting_copilot/provider.py]

## 麦克风、本地 ASR、持续问答

Windows：

```powershell
& $mcPy -m pip install --cache-dir .cache/pip -e '.[audio]'
& $mcPy -m meeting_copilot mic-list
& $mcPy -m meeting_copilot create live --title 'Consented microphone meeting'
# DEVICE_ID 替换为枚举返回值；已取得所有参与者同意后再使用 --consent：
& $mcPy -m meeting_copilot listen live --source DEVICE_ID --seconds 30 --consent --download-model
# 或导入自己的录音，FILE.wav 替换为真实路径：
& $mcPy -m meeting_copilot asr live 'FILE.wav' --download-model
```

macOS：

```bash
.venv/bin/python -m pip install --cache-dir .cache/pip -e '.[audio]'
.venv/bin/python -m meeting_copilot mic-list
.venv/bin/python -m meeting_copilot create live --title 'Consented microphone meeting'
.venv/bin/python -m meeting_copilot listen live --source DEVICE_ID --seconds 30 --consent --download-model
.venv/bin/python -m meeting_copilot asr live 'FILE.wav' --download-model
```

macOS 需要给终端麦克风权限；Windows 要选择实际 WASAPI 设备。若缺原生 PortAudio/设备，先使用回放路径并交 Zimo 排查，不要安装 GPU 栈解决麦克风问题。首次下载 tiny.en 约 76 MB 级模型，保存在 data/models；再次运行可省去 `--download-model`。当前仅明确配置英文 ASR；语言扩展、系统音频/diarization 持续质量待办。[source: src/local_meeting_ai/adapters/audio_capture; src/meeting_copilot/audio.py; results/asr-smoke.json]

录音进行时，可在第二个终端进入同一应用目录、使用同一配置和 venv，执行 `ask live ...`。SQLite/WAL 负责跨进程读写，问题提交时固定水位，采集不等待提供方；程序内 Workers 同样分离 QA/共享摘要；listen 每 15 秒由独立调度线程提交共享摘要，停止时返回最后的 background_summary_snapshot。`--seconds` 范围 1..7200，音频队列满会计数丢弃而不会无限增长；原始录音保留在 data/media。Ctrl+C 停止后等待当前 ASR 块与队列完成，模型关闭；不要直接关闭窗口后宣称完成长时间验证。[source: src/meeting_copilot/audio.py; src/meeting_copilot/assistant.py; tests/test_workers.py]

## 更正、项目刷新、权限、摘要与 MCP

以下示例使用 `design`；先 `create design` 和 `replay design examples/replay.jsonl`。Windows 把 `.venv/bin/python` 换成 `& $mcPy`。

```bash
.venv/bin/python -m meeting_copilot correct design s9 'Correction: agreed timeout is ten seconds.' --revision 3 --start-ms 210000 --end-ms 214000
.venv/bin/python -m meeting_copilot gaps design
.venv/bin/python -m meeting_copilot approve-folder design examples/project
.venv/bin/python -m meeting_copilot refresh-folder FOLDER_ID
.venv/bin/python -m meeting_copilot snapshot design --folder FOLDER_ID
.venv/bin/python -m meeting_copilot ask design 'What queue capacity is configured?' --snapshot SNAPSHOT_ID --conversation private-1
.venv/bin/python -m meeting_copilot summary SNAPSHOT_ID
.venv/bin/python -m meeting_copilot history SNAPSHOT_ID --conversation private-1
.venv/bin/python -m meeting_copilot checkpoint SNAPSHOT_ID --conversation private-1
.venv/bin/python -m meeting_copilot promote design SNAPSHOT_ID 'What queue capacity is configured?' --conversation private-1
.venv/bin/python -m meeting_copilot grant design classmate-host
.venv/bin/python -m meeting_copilot revoke design classmate-host
.venv/bin/python -m meeting_copilot revoke-folder FOLDER_ID
.venv/bin/python -m meeting_copilot trace-export data/redacted-traces.jsonl
```

旧 snapshot 继续读保留的修订/文件；撤销其中任一 meeting/folder 的权限后，整个旧 snapshot 读操作都会拒绝。私有最终图 checkpoint 只支持查看/复用最终结果，不做中途节点断点恢复。历史会议只有同时获得 ACL 并在 `snapshot/ask --previous PREVIOUS_MEETING_ID` 显式选择时进入证据。共享摘要只读取当前会议转录，不读私有问答、文件或 promotion 补充。[source: src/meeting_copilot/store.py; src/meeting_copilot/assistant.py]

MCP 客户端命令配置（cwd 设本应用目录，替换实际 Python 路径和 snapshot）：

```json
{"mcpServers":{"meeting-copilot":{"command":"ABSOLUTE_PATH_TO_VENV_PYTHON","args":["-m","meeting_copilot","--config","ABSOLUTE_PATH_TO_CONFIG_JSON","mcp","SNAPSHOT_ID"]}}}
```

直接执行 `python -m meeting_copilot mcp SNAPSHOT_ID` 会等待协议输入；使用 Ctrl+C 停止。工具只有 meet2notes_status、list_meetings、get_meeting、get_transcript、find_in_transcripts、get_summary、read_evidence、list_project_files、search_project、read_project_file。模型不能用工具参数扩大身份/路径；project tool 返回存储内容而不是即时磁盘内容。未运行 native Meet2Notes 全栈，因此不能把适配后的 transport 测试写成 native baseline 质量结果。[source: src/meeting_copilot/mcp_server.py; tests/test_mcp.py]

## 测试与评测实测

运行命令见上。服务器完整结果以 `results/tests.xml`、`results/test-summary.json` 为准；包含修订/迟到/幂等/缺口、ACL 与私有隔离、metadata/list/get/search/raw/summary/cache/checkpoint、文件保留/撤权/路径/秘密文件、注入惰性、慢 API 采集连续性、HTTP 两协议、MCP stdio、CLI 空格路径、导出和 cleanup。独立 venv 安装 wheel 后未安装 audio extra，demo/replay/项目问答/导出/eval 仍可运行，见 `results/install-smoke.json`。[source: tests; results]

冻结 fixture 为 `src/meeting_copilot/fixtures.json`，22 问/20 事件；转录逻辑时间覆盖 20 分钟，但压力测试只用了 400 个模拟块并加速运行，绝非 20 分钟真实麦克风或 ASR 准确率。22/22 是词项标注匹配与原句引用支持，不能等价于开放式问题正确率。10/10 是定义的隔离攻击集合，不能等价于完整安全证明。[source: src/meeting_copilot/evaluation.py; results/report.json]

初次 21/22 → 代码标识符分词改进 → 同冻结集 22/22；前后报告均保留。skills/direct 对照使用相同检索上下文；direct 的 catch-up 弃答差异来自确定性规则，不能声称真实 LLM skill 提升。no_snapshots 使用最终证据回答早期问题，确实观察到未来泄漏；no_folder 去掉项目证据；no_policy 仅在新建的合成评测 DB/graph 中绕过检索政策，生产运行没有关闭开关。native assistant baseline、真实提供方质量/成本、Windows/macOS smoke 都明确未测。[source: results/initial-eval.json; results/report.json; src/meeting_copilot/evaluation.py]

## 文件地图与上游许可

- `cli.py/config.py/replay.py/audio.py`：Zimo 集成/录音/回放入口。
- `store.py`：Xiaolong 的版本证据、快照、私有状态、共享摘要与 checkpoint 接口。
- `project.py/service.py/mcp_server.py/skills/*.json`：Jiaxuan 的保留文件与 MCP/技能。
- `provider.py/assistant.py`：LiteLLM 协议边界、LangGraph、worker、引用检查；Cai 复核策略。
- `evaluation.py/trace.py/tests/results`：Qiyan 的冻结评测/攻击/延迟/用量证据。
- `src/local_meeting_ai`：真实上游源码与少量 CPU/路径兼容 seam；`docs/UPSTREAM_SOURCE.json` 逐文件记录精确来源；MIT 在 `docs/UPSTREAM_LICENSE.txt`，wheel 也打包许可。

兼容性三种实际方法及选择见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)，ASR 原始结果和 native MCP 探针分别见 `results/asr-smoke.json`、`results/mcp-compatibility.json`。没有用“重写”冒充上游复用。[source: docs/UPSTREAM_SOURCE.json]

## 优先后续工作

| 优先级 | 负责人 | 内容 | 复杂度/建议时间 |
|---|---|---|---|
| P0 | Zimo | Windows/macOS clean install + 枚举/30 秒/20 分钟真实麦克风；技术词 ASR 标注；设备热拔/ASR 取消 | 中，1–2 天含实机 |
| P0 | Cai | Windows junction/大小写/网络盘测试；跨进程撤权竞态；更细粒度 host session 身份接入 | 中，1 天 |
| P0 | Qiyan | 用同提供方/同证据/同硬件运行真实 LLM 与 native baseline，人工判分引用/弃答；费用校准 | 中，1–2 天，需授权 API |
| P1 | Xiaolong | 中途 LangGraph checkpoint 恢复、按需长会议分页/长期保留预算、迁移版本与加密/删除策略 | 中高，1–2 天 |
| P1 | Jiaxuan | 扩展技能的综合输出验证、真正模型 tool-call 轮次与 streaming；更丰富源码定位 | 中，1–2 天 |
| P2 | Qiyan | 可选 Langfuse 红acted exporter 对接（现有只导出本地 NDJSON）、现场资源/转录 lag | 低中，半天–1 天 |
| P2 | Zimo/Jiaxuan | 系统音频/diarization、平台打包、最小 UI、embeddings（需先有评测收益） | 高，后续迭代 |

以上是预计工作量，不是已完成验证。提案中的“双平台 smoke、20 分钟现场讨论质量、native baseline 对比”尚不能勾选完成。[source: results; general inference for estimates]

## 停止与清理

普通 CLI 完成即退出；MCP/listen 使用 Ctrl+C，等待 worker 收尾。没有安装服务，也没有后台自动启动项。开发测试 HTTP 服务仅绑定 127.0.0.1，测试结束由 finally/上下文关闭；模型和下载位于本构建目录内。[source: tests/test_protocols.py; tests/test_mcp.py; src/meeting_copilot/audio.py]

```bash
.venv/bin/python -m meeting_copilot cleanup design --confirm design
```

这个命令删除对应 DB 记录、依赖它的快照/私有缓存/checkpoint 与 app-owned media，不写或删除批准的项目源目录。已有导出、trace、备份为独立文件，需另外管理；SQLite 逻辑删除不承诺磁盘安全擦除。完全移除本机运行数据时，先备份需要的导出、停止程序，再仅删除此应用的 `data`、`.cache`、`.venv`；不要删除批准的外部项目。服务器构建目录 **本代理不会删除**，由协调者收集/核验/同步 Windows 后统一删除。[source: src/meeting_copilot/store.py; src/meeting_copilot/cli.py; authorized workflow]
