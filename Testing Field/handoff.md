# Meeting Copilot v0.2 交接

独立审查已把 v0.1 的主要功能缺口补成可运行的 v0.2：长会议/长文件检索、精确原始行号、带证据的模型综合、跨快照私有会话、并发录音与后台摘要、流式协议和只读工具调用、可运行的原生助手回放评测入口。默认 demo 仍是明确标注的模拟转录与确定性摘录，不冒充真实模型。最终提交、标签、测试和产物以构建根目录 `DELIVERY.json` 为准。[source: src/meeting_copilot; docs/INDEPENDENT_REVIEW.md]

## 同学上手

安装与完整命令见 [README.md](README.md)。Windows 用 `py -3.12 -m venv .venv`，之后调用 `.\.venv\Scripts\python.exe`；macOS/Linux 用 `python3.12 -m venv .venv`，之后调用 `.venv/bin/python`。新环境安装 `-e ".[dev]"`，依次执行 `init`、`demo`、`evaluate --output results/local-report.json --stress`。`init` 不覆盖已有配置；升级时保留配置和数据即可，新增表会自动创建，v0.1 私有答案会迁移为历史记录。[source: README.md; src/meeting_copilot/store.py]

默认配置和数据相对 `config.json` 所在目录解析；所有终端使用同一配置。不要把运行数据、模型或凭据提交到 Git。demo 项目在 `data/` 旁的 `demo-project/`，避免重新索引自己的私有数据库、导出和 trace。批准项目根目录不能与实际运行数据目录互相包含；不要批准整个应用父目录。[source: src/meeting_copilot/config.py; src/meeting_copilot/replay.py; src/meeting_copilot/store.py]

无密钥的可重复流程：

```bash
.venv/bin/python -m meeting_copilot create design --title "Design review"
.venv/bin/python -m meeting_copilot replay design examples/replay.jsonl
.venv/bin/python -m meeting_copilot approve-folder design examples/project
.venv/bin/python -m meeting_copilot refresh-folder FOLDER_ID
.venv/bin/python -m meeting_copilot ask design "Compare SQLite and Kafka deployment" --folder FOLDER_ID --skill compare_options --conversation notes
.venv/bin/python -m meeting_copilot ask design "Why was it chosen?" --folder FOLDER_ID --conversation notes
.venv/bin/python -m meeting_copilot history SNAPSHOT_ID --conversation notes --across-snapshots
.venv/bin/python -m meeting_copilot export SNAPSHOT_ID data/answer.json --conversation notes
```

把 `FOLDER_ID`、`SNAPSHOT_ID` 替换为前一步输出的真实 ID。`ask` 每次提交时固定转录/文件版本与私有历史水位；`--snapshot` 用于明确重读旧证据。历史答案保留各自快照；撤权、未选中的会议/文件夹和提问后产生的私有轮次都不能通过历史进入新回答。`history` 不带 `--across-snapshots` 时只显示该快照的答案。导出包含前 100 条证据及所有实际被答案/摘要引用的原文，额外截断会明确标记。[source: src/meeting_copilot/store.py; src/meeting_copilot/cli.py]

## 真实模式与同时提问

配置 `provider=openai` 或 `anthropic`、供应商模型 ID、`base_url`、`send_evidence=true`，在当前 shell 设置自己的 `MEETING_COPILOT_API_KEY`。OpenAI-compatible URL 包含 `/v1`；Anthropic 使用站点根 URL。不要把 Codex 登录凭据当作应用 API key。当前问题、选定证据片段和符合范围的历史私有上下文会发送给配置的供应商。[source: src/meeting_copilot/provider.py]

`streaming=true` 可接收两种 SSE 协议，但应用等引用校验完成后才显示答案；并非逐字显示未经检查的内容。`tool_rounds=1` 或 `2` 允许模型请求只读搜索/读取，身份、快照、权限和预算仍由主机固定；默认 `0`。没有 shell、写文件、授权或修改工具。两种协议都经过本地假 HTTP 端点测试，未使用付费密钥，也没有声称已验证任意供应商。[source: tests/test_protocols.py; src/meeting_copilot/model_tools.py]

真实采集需要安装 `.[audio]`，执行 `mic-list` 并选取实际设备 ID。终端 A：

```bash
.venv/bin/python -m meeting_copilot create live
.venv/bin/python -m meeting_copilot listen live --source DEVICE_ID --seconds 0 --consent --download-model --summary-interval 15
```

终端 B 使用同一配置：

```bash
.venv/bin/python -m meeting_copilot ask live "Catch me up" --skill catch_up --conversation mine
.venv/bin/python -m meeting_copilot ask live "Why was it chosen?" --conversation mine
.venv/bin/python -m meeting_copilot live-status live
```

`--seconds 0` 持续至 Ctrl+C；也可指定有限秒数。停止时会提交最后不足三秒的块并等待已接受任务结束。每五秒 stderr 输出队列、丢弃和摘要错误状态；`live-status` 可查询已完成的共享摘要。慢 API 不阻塞独立采集/入库；真实麦克风仍须在目标机器验证。共享摘要只消费当前会议转录，不消费私有问题、项目文件或用户推广的补充内容。[source: src/meeting_copilot/audio.py; src/meeting_copilot/assistant.py; tests/test_live_processes.py]

手工纠正已有片段可用 `correct MEETING SEGMENT_ID "corrected text" --revision N`。N 必须大于当前修订；省略 `--start-ms/--end-ms` 时保留原始讲话时间，旧快照仍保留旧文本。[source: src/meeting_copilot/cli.py; tests/test_cli_storage.py]

## 安全与结果解释

API 答案分为模型综合和逐字引文。程序验证引文确实出自当前授权快照、原文哈希和行号正确、综合引用了已验证的来源；**这些检查不能证明模型综合在语义上成立**。未找到证据时弃答；没有证据的理由、取舍或负责人必须由人检查。主机配置/操作系统用户属于可信边界，不提供对恶意本机管理员的隔离。[source: src/meeting_copilot/provider.py; src/meeting_copilot/assistant.py]

会议授权和撤权：`grant MEETING PRINCIPAL`、`revoke MEETING PRINCIPAL`；文件夹撤权：`revoke-folder FOLDER_ID`。这些都是显式主机命令，不接受模型授权。`mcp SNAPSHOT_ID` 启动 stdio 服务，只接受 MCP 客户端；Ctrl+C 停止。十个只读工具具有同样的 ACL/快照检查，转录和项目列表可分页。旧快照、缓存、checkpoint、导出、历史上下文和工具调用都会重新检查权限。[source: src/meeting_copilot/cli.py; src/meeting_copilot/mcp_server.py; tests/test_evidence.py; tests/test_mcp.py]

## 评测与确实未验证的部分

`evaluate` 默认不调用 API；22 道冻结题的“correct”是预期关键词与弃答检查，“citation_supported”仅表示引文属于原文。政策消融实际执行同一隔离合成数据库中的跨会议、未选文件夹和私有线程三个攻击；生产路径没有关闭政策的开关。毫秒级 demo 延迟不能当作真实模型延迟，模拟技能得分差异不能宣传为模型能力提升。[source: src/meeting_copilot/evaluation.py]

配置真实端点后，`evaluate --api --output results/local-api-eval.json` 会执行 88 次请求；`native-baseline --output results/local-native.json` 会对同一冻结题运行 22 次原生 `LiveAssistantService.ask`。原生服务、仓库和 SQL 为未改上游代码；评测桥接器共用本项目传输适配器，使用合成父表，不启动桌面应用、原生 RAG 或 OS keyring。这两个命令可能计费；本次只执行假端点合同测试，不报告付费模型质量或完整原生桌面基线得分。[source: src/meeting_copilot/native_baseline.py; tests/test_protocols.py]

真实 20 分钟稳定性证据见独立审查报告：重复公开 JFK 录音，经实际 tiny.en CPU ASR、周期摘要和 5 秒延迟的模拟提供方 QA；记录真实墙钟、入库延迟、队列、丢弃和 RSS。它不是麦克风、技术讨论 WER/术语识别或真实 LLM 质量测试。[source: scripts/prerecorded_soak.py; docs/INDEPENDENT_REVIEW.md]

仍需目标机器/凭据/人工评判的工作：Windows/macOS 安装、设备与 junction/UNC 测试；连续真实麦克风；技术术语/人工质量；真实供应商成本和原生对照。持久化目前保存最终 graph checkpoint，不支持进程崩溃后从中间节点恢复。长期保留/磁盘配额、加密与安全擦除、系统音频/说话人分离、嵌入、Langfuse 托管、GUI/安装包均未宣称完成；后几项是提案可选扩展。[source: docs/INDEPENDENT_REVIEW.md; inputs proposal scope]

## 文件、版本与清理

24 个复用上游实现/SQL 文件逐字节验证通过，MCP 适配、CPU/path seam 和 native transport bridge 单独注明。来源提交与 MIT 许可见 `docs/UPSTREAM_SOURCE.json`、`docs/UPSTREAM_LICENSE.txt`，验证结果见 `results/audit-upstream.json`。原始 `v0.1.0-prototype` 标签保留；新版本以 DELIVERY.json 为准。修改清单见 `docs/AUDIT_MODIFIED_FILES.txt`。[source: docs/UPSTREAM_SOURCE.json; results/audit-upstream.json]

清理一个会议：

```bash
.venv/bin/python -m meeting_copilot cleanup design --confirm design
```

这会删除该会议及依赖快照/私有状态和应用媒体，不修改批准的项目源文件。已生成的导出、trace、备份仍是独立文件；逻辑删除不等于安全擦除。所有本次启动的测试/ASR/MCP 进程在交付前结束；构建根目录保留，等待协调者收集并在 Windows 独立测试，禁止现在删除。[source: src/meeting_copilot/cli.py; DELIVERY.json; user authorization]
