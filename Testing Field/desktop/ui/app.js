"use strict";

const byId = (id) => document.getElementById(id);
const pending = new Map();
let nextRequest = 0;

window.copilotReceive = (reply) => {
  const request = pending.get(reply.id);
  if (!request) return;
  pending.delete(reply.id);
  if (reply.ok) request.resolve(reply.result);
  else request.reject(new Error(reply.error || "Something went wrong. Please try again."));
};

function bridge(action, payload = {}) {
  return new Promise((resolve, reject) => {
    const handler = window.webkit?.messageHandlers?.copilot;
    if (!handler) {
      reject(new Error("Open Meeting Copilot to connect to the local workspace."));
      return;
    }
    const id = String(++nextRequest);
    pending.set(id, { resolve, reject });
    try {
      handler.postMessage({ id, action, payload });
    } catch (error) {
      pending.delete(id);
      reject(error);
    }
  });
}

const state = {
  meetings: [],
  meeting: null,
  selectedId: null,
  loading: true,
  busy: null,
  selection: 0,
  tab: "transcript",
  query: "",
  conversations: new Map(),
  highlighted: null,
  recordingId: null,
  recordingErrors: new Map(),
  endingMeetings: new Set(),
  summaryRequests: new Map(),
  initialError: null,
};

const prompts = [
  { question: "Catch me up on the discussion.", label: "Catch me up", skill: "catch_up" },
  { question: "Why did we choose SQLite?", label: "Why did we choose SQLite?", skill: "trace_decision" },
  { question: "Compare SQLite and Kafka deployment.", label: "Compare SQLite and Kafka", skill: "compare_options" },
];
const localPrompts = [
  prompts[0],
  { question: "What did we decide?", label: "Find a decision", skill: "trace_decision" },
  { question: "What options did we compare?", label: "Compare options", skill: "compare_options" },
];
const minuteSections = [
  ["overview", "Overview", "No overview available."],
  ["topics", "Topics", "No topics identified."],
  ["decisions", "Decisions", "No decisions recorded."],
  ["actionItems", "Action items", "No action items recorded."],
  ["openQuestions", "Open questions", "No open questions recorded."],
];

let toastTimer;
function toast(message, error = false) {
  const view = byId("toast");
  byId("toast-text").textContent = message;
  view.classList.toggle("error", error);
  view.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { view.hidden = true; }, error ? 9000 : 4500);
}

function make(tag, className, value) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (value !== undefined) el.textContent = value;
  return el;
}

function duration(milliseconds) {
  const seconds = Math.max(0, Math.floor(Number(milliseconds) / 1000) || 0);
  const mins = Math.floor(seconds / 60);
  const secs = String(seconds % 60).padStart(2, "0");
  return mins < 60
    ? String(mins).padStart(2, "0") + ":" + secs
    : Math.floor(mins / 60) + ":" + String(mins % 60).padStart(2, "0") + ":" + secs;
}

function titleFor(meeting) {
  if (!meeting) return "Meetings";
  return meeting.mode === "sample" ? meeting.title.replace(/\s*\(SIMULATED REPLAY\)\s*$/, "") : meeting.title;
}

function updateRecording(detail) {
  if (detail.recording) {
    state.recordingId = detail.id;
    state.recordingErrors.delete(detail.id);
  } else if (state.recordingId === detail.id) {
    state.recordingId = null;
  }
  if (detail.recordingError && state.recordingErrors.get(detail.id) !== detail.recordingError) {
    state.recordingErrors.set(detail.id, detail.recordingError);
    toast(detail.recordingError, true);
  }
}

function applyCollection(result) {
  state.meetings = result.meetings || [];
  state.meeting = result.meeting || null;
  state.selectedId = state.meeting?.id || null;
  state.loading = false;
  state.initialError = null;
  if (state.meeting) updateRecording(state.meeting);
  resetContent();
  render();
}

function resetContent() {
  state.tab = "transcript";
  state.query = "";
  state.highlighted = null;
  byId("transcript-search").value = "";
  byId("transcript-scroll").scrollTop = 0;
  byId("summary-scroll").scrollTop = 0;
  byId("question").value = "";
}

function showEmpty(node, heading, message) {
  node.replaceChildren(make("h3", "", heading), make("p", "", message));
  node.hidden = false;
}

function renderMeetings() {
  const list = byId("meeting-list");
  const scroll = list.scrollTop;
  const focused = document.activeElement?.dataset?.meetingId;
  const fragment = document.createDocumentFragment();
  for (const meeting of state.meetings) {
    const item = make("button", "meeting-item");
    item.type = "button";
    item.dataset.meetingId = meeting.id;
    item.setAttribute("aria-current", String(meeting.id === state.selectedId));
    item.title = titleFor(meeting);
    const icon = make("span", "meeting-icon");
    icon.setAttribute("aria-hidden", "true");
    icon.append(make("span", "icon icon-note"));
    const info = make("span", "meeting-item-info");
    const status = state.recordingId === meeting.id ? "Recording" : (meeting.mode === "sample" ? "Sample meeting" : "Local meeting");
    info.append(make("span", "meeting-item-name", titleFor(meeting)), make("span", "meeting-item-type", status));
    item.append(icon, info);
    fragment.append(item);
  }
  list.replaceChildren(fragment);
  list.scrollTop = scroll;
  if (focused) {
    Array.from(list.children).find((item) => item.dataset.meetingId === focused)?.focus({ preventScroll: true });
  }
  byId("meeting-count").textContent = state.meetings.length || "";
}

function renderControls() {
  const meeting = state.meeting;
  const recording = meeting && state.recordingId === meeting.id;
  const busy = state.busy !== null;
  byId("new-meeting").disabled = busy || state.loading;
  byId("load-sample").disabled = busy || state.loading;
  byId("record-button").disabled = !meeting || meeting.mode === "sample" || busy || (state.recordingId !== null && !recording);
  byId("record-button").title = meeting?.mode === "sample" ? "Create a meeting to record audio" : "";
  byId("record-button").classList.toggle("is-recording", Boolean(recording));
  byId("record-label").textContent = state.busy === "record" ? "Starting…" : state.busy === "stop" ? "Stopping…" : recording ? "Stop recording" : "Record";
  byId("import-button").disabled = !meeting || meeting.mode === "sample" || busy || state.recordingId !== null;
  byId("import-button").title = meeting?.mode === "sample" ? "Create a meeting to import audio" : "";
  byId("import-button").lastChild.textContent = state.busy === "import" ? " Importing…" : " Import audio";
  byId("export-button").disabled = !meeting || busy;
  const summaryRequest = state.summaryRequests.get(meeting?.id);
  const generate = byId("generate-summary");
  generate.hidden = !recording && Boolean(meeting?.minutes) && !summaryRequest;
  generate.disabled = !meeting?.segments?.length || busy || Boolean(summaryRequest?.pending);
  generate.lastChild.textContent = summaryRequest?.pending ? " Generating…" : summaryRequest?.error ? " Retry" : " Generate summary";
  byId("transcript-search").disabled = !meeting?.segments?.length;
  updateComposer();
}

function renderHeader() {
  const meeting = state.meeting;
  const segments = meeting?.segments || [];
  byId("meeting-title").textContent = state.loading ? "Loading meetings…" : meeting ? titleFor(meeting) : "Your meetings";
  byId("meeting-context").textContent = meeting?.mode === "sample" ? "Sample meeting" : "Local workspace";
  byId("sample-label").hidden = meeting?.mode !== "sample";
  let meta = "Create a meeting or open the sample to get started.";
  if (state.initialError) meta = state.initialError;
  if (meeting) {
    const count = segments.length;
    const segmentLabel = count + (count === 1 ? " transcript segment" : " transcript segments");
    const detail = count ? duration(segments[segments.length - 1].endMs) + " · " + segmentLabel : "No transcript yet";
    meta = (state.recordingId === meeting.id ? "Recording · " : "") + detail;
  }
  byId("meeting-meta").textContent = state.loading ? "" : meta;
}

function renderTabs() {
  for (const name of ["transcript", "summary"]) {
    const active = state.tab === name;
    const tab = byId(name + "-tab");
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    byId(name + "-panel").hidden = !active;
  }
}

function appendMatched(text, target) {
  const search = state.query.toLocaleLowerCase();
  const folded = text.toLocaleLowerCase();
  if (!search) {
    target.textContent = text;
    return;
  }
  // Preserve character positions for Unicode case mappings that change string length.
  if (folded.length !== text.length) {
    target.textContent = text;
    return;
  }
  let cursor = 0;
  let index = folded.indexOf(search);
  while (index !== -1) {
    target.append(document.createTextNode(text.slice(cursor, index)), make("mark", "", text.slice(index, index + search.length)));
    cursor = index + search.length;
    index = folded.indexOf(search, cursor);
  }
  target.append(document.createTextNode(text.slice(cursor)));
}

function renderTranscript() {
  const list = byId("transcript-list");
  const empty = byId("transcript-empty");
  const scroll = byId("transcript-scroll");
  const position = scroll.scrollTop;
  const segments = state.meeting?.segments || [];
  const query = state.query.toLocaleLowerCase();
  const visible = query ? segments.filter((item) => String(item.text).toLocaleLowerCase().includes(query)) : segments;
  const rows = document.createDocumentFragment();
  for (const segment of visible) {
    const id = String(segment.id);
    const row = make("li", "transcript-row");
    row.id = "segment-" + id;
    row.dataset.sourceId = id;
    row.classList.toggle("highlighted", id === state.highlighted);
    const timestamp = make("time", "transcript-time", duration(segment.startMs));
    const body = make("p", "transcript-text");
    appendMatched(String(segment.text), body);
    row.append(timestamp, body);
    rows.append(row);
  }
  list.replaceChildren(rows);
  byId("transcript-count").textContent = query ? visible.length + " of " + segments.length + " segments" : "";
  if (visible.length) empty.hidden = true;
  else if (state.loading) showEmpty(empty, "Loading transcript…", "");
  else if (query) showEmpty(empty, "No matching segments", "Try a different search.");
  else if (state.meeting) {
    if (state.busy === "import") showEmpty(empty, "Transcribing audio…", "The text will appear here when transcription finishes.");
    else if (state.recordingId === state.meeting.id) showEmpty(empty, "Listening for speech…", "Transcript segments appear here as they’re ready.");
    else showEmpty(empty, "No transcript yet", "Record this meeting or import an audio file to get started.");
  } else showEmpty(empty, "No meeting selected", "Open a meeting to view its transcript.");
  scroll.scrollTop = position;
}

function appendAnswer(line, paragraph, citations, sourceNumbers) {
  let cursor = 0;
  for (const match of line.matchAll(/\[E(\d+)\]/g)) {
    paragraph.append(document.createTextNode(line.slice(cursor, match.index)));
    const citation = citations.get(match[1]);
    if (citation) {
      const button = make("button", "inline-citation", sourceNumbers?.get(match[1]) || match[1]);
      button.type = "button";
      button.setAttribute("aria-label", "Open source " + citation.label);
      button.addEventListener("click", () => showSource(citation));
      paragraph.append(button);
    } else {
      paragraph.append(document.createTextNode(match[0]));
    }
    cursor = match.index + match[0].length;
  }
  paragraph.append(document.createTextNode(line.slice(cursor)));
}

function renderSummary() {
  const detail = state.meeting;
  const summary = byId("summary-content");
  const empty = byId("summary-empty");
  const request = state.summaryRequests.get(detail?.id);
  summary.replaceChildren();
  byId("summary-panel").setAttribute("aria-busy", String(Boolean(request?.pending)));
  if (request?.pending) {
    showEmpty(empty, request.final ? "Preparing meeting minutes…" : "Preparing a summary…", "");
    return;
  }
  if (request?.error) {
    showEmpty(empty, request.final ? "Couldn’t generate minutes" : "Couldn’t generate a summary", request.error);
    return;
  }
  if (!detail?.segments?.length) {
    showEmpty(empty, "No summary yet", "A transcript is needed before you can generate a summary.");
    return;
  }
  if (!detail.recording && detail.minutes) {
    empty.hidden = true;
    renderMinutes(detail.minutes, summary);
    return;
  }
  if (!detail?.summary) {
    showEmpty(empty, "No summary yet", "Generate a recap from this meeting’s transcript.");
    return;
  }
  empty.hidden = true;
  const segmentCitations = new Map(detail.segments.map((segment) => [
    String(segment.id),
    { id: segment.id, label: duration(segment.startMs), quote: segment.text, kind: "segment" },
  ]));
  for (const line of String(detail.summary).split(/\r?\n/).filter((part) => part.trim())) {
    const p = make("p", "summary-line");
    appendAnswer(line, p, segmentCitations);
    summary.append(p);
  }
}

function renderMinutes(minutes, container) {
  const header = make("div", "minutes-header");
  header.append(make("h2", "", "Meeting minutes"));
  container.append(header);
  for (const [key, label, emptyText] of minuteSections) {
    const section = make("section", "minutes-section");
    section.append(make("h3", "", label));
    const entries = minutes[key] || [];
    if (!entries.length) {
      section.append(make("p", "minutes-empty", emptyText));
    } else {
      const list = make("ul", "minutes-list");
      for (const entry of entries) {
        const item = make("li", "minutes-item");
        item.append(make("p", "minutes-text", entry.text));
        const details = make("div", "minutes-details");
        if (entry.owner) details.append(make("span", "minutes-meta", "Owner · " + entry.owner));
        if (entry.due) details.append(make("span", "minutes-meta", "Due · " + entry.due));
        for (const source of entry.sources || []) {
          const button = make("button", "citation minutes-source", source.label);
          button.type = "button";
          button.setAttribute("aria-label", "Open source " + source.label);
          button.addEventListener("click", () => showSource(source));
          details.append(button);
        }
        if (details.childElementCount) item.append(details);
        list.append(item);
      }
      section.append(list);
    }
    container.append(section);
  }
}

async function requestSummary(meetingId, final) {
  const previous = state.summaryRequests.get(meetingId);
  if (previous?.pending && (!final || previous.final)) return;
  const request = { pending: true, final };
  state.summaryRequests.set(meetingId, request);
  if (state.meeting?.id === meetingId) {
    renderSummary();
    renderControls();
  }
  try {
    const result = await bridge("summary", { meetingId, final });
    if (state.summaryRequests.get(meetingId) !== request) return;
    state.summaryRequests.delete(meetingId);
    if (state.meeting?.id === meetingId) {
      state.meeting.summary = result.text;
      if (final) state.meeting.minutes = result.minutes ?? null;
    }
  } catch (error) {
    request.pending = false;
    request.error = error.message;
  } finally {
    if (state.meeting?.id === meetingId) {
      renderSummary();
      renderControls();
    }
  }
}

function finishRecording(detail) {
  if (detail.recording || !state.endingMeetings.delete(detail.id)) return;
  if (state.meeting?.id === detail.id) selectTab("summary", true);
  if (detail.segments.length) void requestSummary(detail.id, true);
}

function currentTurns(meetingId = state.meeting?.id) {
  return state.conversations.get(meetingId) || [];
}

function renderConversation(scrollToEnd = false) {
  const turns = currentTurns();
  const scroll = byId("assistant-scroll");
  const position = scroll.scrollTop;
  const container = byId("conversation");
  const welcome = byId("assistant-welcome");
  welcome.hidden = turns.length > 0;
  const list = byId("prompt-list");
  list.replaceChildren();
  const examples = state.meeting?.mode === "sample" ? prompts : localPrompts;
  for (const prompt of examples) {
    const button = make("button", "prompt", prompt.label);
    button.type = "button";
    button.dataset.skill = prompt.skill;
    const arrow = make("span", "", "↗");
    arrow.setAttribute("aria-hidden", "true");
    button.append(arrow);
    button.addEventListener("click", () => { void ask(prompt.question, prompt.skill); });
    list.append(button);
  }
  container.replaceChildren();
  for (const turn of turns) {
    const section = make("section", "turn");
    section.append(make("p", "question-bubble", turn.question));
    const answer = make("div", "answer-bubble");
    answer.append(make("p", "answer-title", "Copilot"));
    if (turn.pending) {
      answer.append(make("p", "answer-pending", "Finding meeting evidence…"));
    } else if (turn.error) {
      answer.append(make("p", "answer-error", turn.error));
    } else if (turn.answer) {
      const citations = new Map((turn.answer.citations || []).map((citation) => [String(citation.id), citation]));
      const sourceNumbers = new Map(Array.from(citations.keys(), (id, index) => [id, String(index + 1)]));
      for (const line of String(turn.answer.text).split(/\r?\n/).filter((part) => part.trim())) {
        const p = make("p", "answer-line");
        appendAnswer(line, p, citations, sourceNumbers);
        answer.append(p);
      }
      if (citations.size) {
        const sources = make("div", "citations");
        sources.setAttribute("aria-label", "Answer sources");
        for (const citation of citations.values()) {
          const button = make("button", "citation");
          button.type = "button";
          button.title = citation.label;
          const icon = make("span", "", citation.kind === "segment" ? "◷" : "▤");
          icon.setAttribute("aria-hidden", "true");
          button.append(icon, make("span", "", citation.label));
          button.addEventListener("click", () => showSource(citation));
          sources.append(button);
        }
        answer.append(sources);
      }
    }
    section.append(answer);
    container.append(section);
  }
  scroll.scrollTop = scrollToEnd ? scroll.scrollHeight : position;
  updateComposer();
}

function updateComposer() {
  const field = byId("question");
  const mode = state.meeting?.answerMode;
  const modeLabel = mode === "api" ? (state.meeting.providerLabel || "Configured model") + " API" : mode === "local" ? "Local excerpt answers" : "";
  byId("composer-mode-label").textContent = modeLabel;
  byId("composer-mode").hidden = !modeLabel;
  const enabled = Boolean(state.meeting?.segments?.length) && !currentTurns().some((turn) => turn.pending);
  field.disabled = !enabled;
  byId("send-question").disabled = !enabled || !field.value.trim();
  byId("prompt-list").querySelectorAll("button").forEach((button) => { button.disabled = !enabled; });
}

function render() {
  renderMeetings();
  renderHeader();
  renderTabs();
  renderTranscript();
  renderSummary();
  renderConversation();
  renderControls();
}

function showSource(citation) {
  if (citation.kind === "segment" && state.meeting?.segments.some((segment) => String(segment.id) === String(citation.id))) {
    state.tab = "transcript";
    state.query = "";
    state.highlighted = String(citation.id);
    byId("transcript-search").value = "";
    renderTabs();
    renderTranscript();
    const row = byId("segment-" + citation.id);
    if (row) {
      row.tabIndex = -1;
      row.scrollIntoView({ block: "center", behavior: "auto" });
      row.focus({ preventScroll: true });
    }
    return;
  }
  byId("source-label").textContent = citation.label;
  byId("source-quote").textContent = citation.quote;
  byId("source-dialog").showModal();
}

async function selectMeeting(meetingId) {
  if (state.selectedId === meetingId && !state.loading) return;
  const selection = ++state.selection;
  state.selectedId = meetingId;
  state.meeting = null;
  state.loading = true;
  resetContent();
  render();
  try {
    const detail = await bridge("meeting", { meetingId });
    if (selection !== state.selection) return;
    state.meeting = detail;
    updateRecording(detail);
  } catch (error) {
    if (selection !== state.selection) return;
    state.selectedId = null;
    toast(error.message, true);
  } finally {
    if (selection === state.selection) {
      state.loading = false;
      render();
    }
  }
}

async function action(name, work) {
  if (state.busy) return;
  state.busy = name;
  renderControls();
  renderTranscript();
  try {
    await work();
  } catch (error) {
    if (!/^(cancelled|canceled|cancelled\.|canceled\.)$/i.test(error.message)) toast(error.message, true);
  } finally {
    state.busy = null;
    renderControls();
    renderTranscript();
  }
}

async function ask(question, skill = "trace_decision") {
  const meetingId = state.meeting?.id;
  if (!question || !meetingId || !state.meeting.segments.length || currentTurns().some((turn) => turn.pending)) return;
  const turns = currentTurns();
  state.conversations.set(meetingId, turns);
  const turn = { question, pending: true };
  turns.push(turn);
  byId("question").value = "";
  renderConversation(true);
  try {
    turn.answer = await bridge("ask", { meetingId, question, skill });
  } catch (error) {
    turn.error = error.message;
  } finally {
    turn.pending = false;
    if (state.meeting?.id === meetingId) {
      renderConversation(true);
      byId("question").focus();
    }
  }
}

async function refreshRecording() {
  const meetingId = state.recordingId;
  if (!meetingId || state.busy || refreshRecording.pending) return;
  refreshRecording.pending = true;
  try {
    const detail = await bridge("meeting", { meetingId });
    if (state.recordingId !== meetingId) return;
    const wasRecording = state.recordingId === meetingId;
    const visible = state.meeting?.id === meetingId;
    updateRecording(detail);
    if (wasRecording && !detail.recording) {
      if (!detail.recordingError) state.endingMeetings.add(meetingId);
      renderMeetings();
    }
    if (visible) {
      const scroller = byId("transcript-scroll");
      const follow = state.tab === "transcript" && !state.query && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 28;
      const changed = JSON.stringify(state.meeting.segments) !== JSON.stringify(detail.segments);
      const summaryChanged = state.meeting.summary !== detail.summary || (!detail.recording && JSON.stringify(state.meeting.minutes) !== JSON.stringify(detail.minutes));
      state.meeting = detail;
      if (changed || !detail.recording) {
        renderHeader();
        renderTranscript();
        if (changed && follow) scroller.scrollTop = scroller.scrollHeight;
      }
      if (summaryChanged) renderSummary();
      renderControls();
    }
    finishRecording(detail);
  } catch (error) {
    toast(error.message, true);
  } finally {
    refreshRecording.pending = false;
  }
}

function useDetail(detail) {
  updateRecording(detail);
  if (state.meeting?.id === detail.id) {
    state.meeting = detail;
    renderHeader();
    renderTranscript();
    renderSummary();
    renderControls();
  }
  renderMeetings();
}

byId("meeting-list").addEventListener("click", (event) => {
  const item = event.target.closest("button[data-meeting-id]");
  if (item && !state.busy) void selectMeeting(item.dataset.meetingId);
});
byId("load-sample").addEventListener("click", () => {
  void action("demo", async () => { applyCollection(await bridge("demo")); });
});
byId("new-meeting").addEventListener("click", () => {
  byId("new-form").reset();
  byId("new-dialog").showModal();
});
byId("new-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const title = byId("new-name").value.trim();
  if (!title) {
    toast("Enter a meeting name.", true);
    return;
  }
  byId("new-dialog").close();
  void action("create", async () => { applyCollection(await bridge("create", { title })); });
});
document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => byId(button.dataset.close).close());
});
byId("dismiss-toast").addEventListener("click", () => { byId("toast").hidden = true; });

function selectTab(tab, focus = false) {
  state.tab = tab;
  renderTabs();
  if (focus) byId(tab + "-tab").focus();
}

for (const tab of ["transcript", "summary"]) {
  const node = byId(tab + "-tab");
  node.addEventListener("click", () => selectTab(tab));
  node.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const target = event.key === "Home" ? "transcript" : event.key === "End" ? "summary" : tab === "summary" ? "transcript" : "summary";
    selectTab(target, true);
  });
}
byId("transcript-search").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  state.highlighted = null;
  byId("transcript-scroll").scrollTop = 0;
  renderTranscript();
});
document.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() === "f" && (event.metaKey || event.ctrlKey) && !document.querySelector("dialog[open]") && state.meeting?.segments?.length) {
    event.preventDefault();
    selectTab("transcript");
    byId("transcript-search").focus();
    byId("transcript-search").select();
  }
});
byId("generate-summary").addEventListener("click", () => {
  const meeting = state.meeting;
  if (meeting?.segments.length) void requestSummary(meeting.id, !meeting.recording);
});
byId("import-button").addEventListener("click", () => {
  const meetingId = state.meeting?.id;
  if (!meetingId) return;
  void action("import", async () => {
    const detail = await bridge("importAudio", { meetingId });
    if (!detail || detail.cancelled) return;
    useDetail(detail);
    toast("Audio import completed.");
  });
});
byId("export-button").addEventListener("click", () => {
  const meetingId = state.meeting?.id;
  if (!meetingId) return;
  void action("export", async () => {
    const result = await bridge("export", { meetingId });
    if (result?.path) toast("Meeting exported.");
  });
});
byId("record-button").addEventListener("click", () => {
  const meetingId = state.meeting?.id;
  if (!meetingId) return;
  if (state.recordingId === meetingId) {
    void action("stop", async () => {
      state.endingMeetings.add(meetingId);
      const detail = await bridge("stopRecording", { meetingId });
      useDetail(detail);
      finishRecording(detail);
      if (!detail.recording && !detail.recordingError) toast("Recording saved.");
    });
    return;
  }
  void action("devices", async () => {
    const { devices } = await bridge("devices");
    if (!devices.length) {
      toast("No microphone was found. Connect a microphone and try again.", true);
      return;
    }
    const dialog = byId("record-dialog");
    dialog.dataset.meetingId = meetingId;
    byId("record-consent").checked = false;
    const select = byId("device-select");
    select.replaceChildren(...devices.map((device) => {
      const option = make("option", "", device.name);
      option.value = device.id;
      option.selected = device.isDefault;
      return option;
    }));
    dialog.showModal();
  });
});
byId("record-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const dialog = byId("record-dialog");
  const meetingId = dialog.dataset.meetingId;
  const deviceId = byId("device-select").value;
  if (!meetingId || !deviceId || !byId("record-consent").checked) return;
  dialog.close();
  void action("record", async () => { useDetail(await bridge("startRecording", { meetingId, deviceId })); });
});
byId("question").addEventListener("input", updateComposer);
byId("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    byId("ask-form").requestSubmit();
  }
});
byId("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void ask(byId("question").value.trim());
});

async function boot() {
  render();
  try {
    applyCollection(await bridge("bootstrap"));
  } catch (error) {
    state.initialError = error.message;
    state.loading = false;
    render();
    toast(error.message, true);
  }
}
void boot();
window.setInterval(() => { void refreshRecording(); }, 1500);
