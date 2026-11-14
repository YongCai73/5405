# Meeting Copilot — Secure, Memory-Augmented Multi-Agent Meeting Assistant

**ENMGT 5405 Final Project | Team planning repository**

A privacy-conscious meeting assistant that combines continuous transcription with private, evidence-grounded Q&A, meeting memory, controlled tools, and reproducible evaluation.

## Xiaolong 的 macOS 桌面版更新

这个分支把现有 Meeting Copilot 原型接入 macOS 桌面应用。开会时可以录音、看转录和提问，结束后可以直接阅读纪要并核对原文。

| 更新 | 用途 |
|---|---|
| **桌面工作区** | 在同一个窗口新建会议、选择麦克风录音或导入音频，并按关键词搜索带时间戳的转录。语音转写由本机的 Faster Whisper 处理。界面用正红标出主要操作和选中状态，阅读区采用浅色底。 |
| **会后自动生成纪要** | 停止录音后，应用等转写收尾，再打开 **Summary**。有转录时会生成概览、讨论议题、决定、待办和待确认问题中有原文支持的内容。点击时间戳可以核对原文。导入音频后可在 **Summary** 点 **Generate summary**。 |
| **带引用的会议问答和导出** | 在 **Ask Copilot** 里追问会议决定或讨论内容，点引用可查看依据。**Export** 可把会议转录和已生成的纪要保存为 JSON。 |
| **合并 Zimo 的实时转写改进** | 录音按语音停顿切片，结合语音活动检测（VAD）识别语音，跳过纯静音切片，并记录延迟和音频丢失情况，便于排查漏转和延迟。Zimo 的 WASAPI 缓冲区调整和实验数据适用于 Windows。 |

### 在 Mac 上试用

检出 `xiaolong/macos-desktop` 后，按 [桌面版构建指南](Testing%20Field/desktop/README.md#build-and-open) 在本机生成 `Meeting Copilot.app`，需要 macOS、Python 3.11 及以上和 Xcode Command Line Tools。构建后可以在 Finder 双击打开。点 **Open sample meeting** 可查看样例转录和纪要，并试用带引用的问答，样例无需 API key。实际录音需要授予麦克风权限，首次转写可能会下载英语模型。

相关资料 · [原型与 CLI](Testing%20Field/README.md) · [交接文档](Testing%20Field/handoff.md) · [需求状态](Testing%20Field/results/requirements-current.json) · [Zimo 的 ASR 实验记录](Testing%20Field/results/asr-chunking-experiment.md)

> The sections below preserve the team's original project plan and proposed architecture.

## Product scenario

During a small in-person technical meeting, a user can privately ask questions such as *“Why did we reject Option A?”* or *“How does this compare with last week's decision?”* while transcription continues uninterrupted. Answers should cite timestamped meeting segments or approved project documents, and should not incorporate meeting content that occurred after the question was asked.

## MVP scope

1. Capture microphone audio on one computer and generate timestamped transcript segments.
2. Keep recording/transcribing while a private question is being answered.
3. Retrieve relevant meeting segments and approved project documents with source citations.
4. Generate a concise meeting summary and export it locally.
5. Enforce meeting/user access boundaries and treat transcript content as untrusted data.
6. Record reproducible traces and test task quality, latency, and privacy/security cases.

**Out of scope for the first version:** Zoom/Teams integration, accurate multi-speaker diarization in noisy rooms, automatic outbound emails, and uncontrolled third-party actions.

## Workstreams

| Workstream | Initial responsibility | Possible technologies (under evaluation) |
|---|---|---|
| Orchestration & Integration | Transcription ingestion, concurrent summary/private Q&A, routing, bounded retries | LangGraph |
| Memory & Context | Timestamped transcript store, meeting-scoped RAG, document retrieval, provenance | SQLite, Chroma |
| MCP & Skills | Read-only project-document tools, skill routing, schema/contracts | MCP, Agent Skills |
| Security & Guardrails | Authorization, private/shared separation, prompt-injection defense, approval policy | Deterministic execution checks; research Invariant/AgentDojo |
| Evaluation & Observability | Replayable tests, trace IDs, baseline comparison, metrics | Langfuse, custom scorers, optional DeepEval |

This original **five-workstream plan** is preserved. The current proposal assignments and developer TODO owners are recorded in the prototype handoff.

## Architecture (proposed)

```text
Microphone -> ASR adapter -> Timestamped transcript events -> Meeting store
                                                    |         |
                                                    v         v
                                           Background summary  Private Q&A request
                                                              |
                                                     Orchestrator (LangGraph)
                                                       /          \
                                          Meeting RAG / Memory   Read-only MCP tools
                                                       \          /
                                                 Evidence + citations
                                                        |
                                               Security and access checks
                                                        |
                                                Private answer to user

All modules -> structured traces -> evaluation runner
```

**Critical security rule:** Meeting speech and retrieved documents are evidence, **not instructions** that authorize tool actions. Authorization must be validated server-side before any tool execution. Private Q&A does not enter shared notes automatically.

## Getting started (planning stage)

- Read [project scope](docs/PROJECT_SCOPE.md).
- Agree on [interfaces](docs/INTERFACES.md) before independent implementation.
- Use [milestones](docs/MILESTONES.md) as a draft timeline; adapt to the professor's deadline.
- Record research and benchmark references in [research notes](docs/RESEARCH.md).

The `src/` directory is intentionally a lightweight placeholder. Add a prototype only after the audio feasibility test and team architecture review.

## Open-source provenance

We are evaluating [Meet2Notes](https://github.com/estebanstifli/Meet2Notes) as a reference or potential base. The original planning repository did not include copied Meet2Notes code. This branch now includes attributed prototype reuse under `Testing Field/`; see its [upstream provenance](Testing%20Field/docs/UPSTREAM_SOURCE.json) and [MIT notice](Testing%20Field/docs/UPSTREAM_LICENSE.txt). Preserve license, notices and attribution, document changes, and distinguish upstream features from original contributions.

## Research/evaluation principles

- Compare a fixed baseline against each enhancement on the same test cases.
- Report legitimate answer accuracy, citation support, private-data leakage, injection success, P50/P95 latency, and audio queue lag.
- Use synthetic or consented recordings; never upload private meeting audio to a third party without explicit consent and disclosure.

## License

No license has been chosen for the team's original code yet. Decide this before making the project public or accepting external contributions. Third-party components retain their respective licenses.
