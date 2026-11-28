import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const ui = new URL('../desktop/ui/', import.meta.url);
const html = readFileSync(new URL('index.html', ui), 'utf8');
const source = readFileSync(new URL('app.js', ui), 'utf8');

// An isolated DOM is enough to exercise the shipped event handlers and bridge
// protocol without launching the desktop app, a browser, or a microphone.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.classes = new Set();
    this.classList = {
      toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name),
      add: (name) => this.classes.add(name),
      remove: (name) => this.classes.delete(name),
    };
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.value = '';
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this._text = '';
  }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) {
    this._text = '';
    this.children = children.flatMap((child) => child.tagName === 'FRAGMENT' ? child.children : [child]);
  }
  get lastChild() {
    if (!this.children.length) this.children.push(new Element('text'));
    return this.children.at(-1);
  }
  get childElementCount() { return this.children.length; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }
  dispatch(name, extra = {}) {
    const event = { target: this, preventDefault() {}, ...extra };
    for (const handler of this.listeners.get(name) || []) handler(event);
  }
  querySelectorAll(selector) {
    const all = this.children.flatMap((child) => [child, ...child.querySelectorAll(selector)]);
    return all.filter((child) => selector === 'button' && child.tagName === 'BUTTON');
  }
  focus() {}
  select() {}
  scrollIntoView() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  reset() {}
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const detail = (recording, status = null, other = {}) => ({
  id: 'm', title: 'Audio test', mode: 'live', segments: [], summary: '', minutes: null,
  recording, ...(status ? { recordingStatus: status } : {}), ...other,
});
const standard = { preset: 'default', asrThreads: 2, pauseMs: 500, stalled: false, secondsWithoutAudio: 0, warning: '' };
const lowLatency = { ...standard, preset: 'low-latency', asrThreads: 4, pauseMs: 300 };

async function application() {
  const nodes = new Map();
  for (const match of html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const node = new Element(match[1]);
    node.hidden = /\bhidden\b/.test(match[0]);
    nodes.set(match[2], node);
  }
  const requests = [];
  const responses = new Map([
    ['bootstrap', { meetings: [], meeting: null }],
    ['devices', { devices: [{ id: 'mic', name: 'Test microphone', isDefault: true }] }],
    ['questionHistory', { turns: [] }],
    ['skills', { skills: [] }],
    ['settings', null],
  ]);
  let timerWrites = 0;
  const window = {
    setInterval() {}, clearTimeout() {},
    setTimeout() { return ++timerWrites; },
    webkit: { messageHandlers: { copilot: { postMessage(request) {
      requests.push(request);
      assert.ok(responses.has(request.action), `Unexpected bridge request: ${request.action}`);
      const result = responses.get(request.action);
      queueMicrotask(() => window.copilotReceive({ id: request.id, ok: true, result }));
    } } } },
  };
  const document = {
    activeElement: null,
    getElementById(id) {
      assert.ok(nodes.has(id), `App requested an element absent from index.html: ${id}`);
      return nodes.get(id);
    },
    createElement: (tag) => new Element(tag),
    createTextNode(text) { const node = new Element('text'); node.textContent = text; return node; },
    createDocumentFragment: () => new Element('fragment'),
    querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
  };
  const context = vm.createContext({ window, document, console });
  vm.runInContext(source, context, { filename: fileURLToPath(new URL('app.js', ui)) });
  await flush();
  return {
    nodes, requests, responses, context,
    get timerWrites() { return timerWrites; },
    evaluate: (expression) => vm.runInContext(expression, context),
    install(value) {
      context.nextDetail = value;
      vm.runInContext('state.meetings = [{ id: nextDetail.id, title: nextDetail.title, mode: nextDetail.mode }]; state.meeting = nextDetail; state.selectedId = nextDetail.id; state.loading = false; updateRecording(nextDetail); render();', context);
    },
  };
}

async function openRecorder(app) {
  app.nodes.get('record-button').dispatch('click');
  await flush();
  assert.equal(app.nodes.get('record-dialog').open, true);
  app.nodes.get('device-select').value = 'mic';
  app.nodes.get('record-consent').checked = true;
}

test('desktop submits a private question to the background and polls its state', async () => {
  const app = await application();
  app.install(detail(false, null, { segments: [{ id: 1, text: 'SQLite is local.', startMs: 0, endMs: 1000 }] }));
  app.responses.set('beginQuestion', { id: 'snapshot-1' });
  app.responses.set('questionHistory', { turns: [{ id: 'snapshot-1', question: 'Why SQLite?', pending: true }] });
  await app.evaluate('ask("Why SQLite?")');
  assert.equal(app.requests.find((request) => request.action === 'beginQuestion').payload.question, 'Why SQLite?');
  assert.equal(app.nodes.get('question').disabled, true);
  app.responses.set('questionHistory', { turns: [{
    id: 'snapshot-1', question: 'Why SQLite?', answer: { text: 'SQLite is local.', citations: [] },
  }] });
  await app.evaluate('refreshQuestions("m")');
  assert.equal(app.nodes.get('question').disabled, false);
  assert.equal(app.evaluate('currentTurns()[0].answer.text'), 'SQLite is local.');
});

test('resuming sends the original question ID and a fresh history can remove an inaccessible turn', async () => {
  const app = await application();
  app.install(detail(false, null, { segments: [{ id: 1, text: 'SQLite is local.', startMs: 0, endMs: 1000 }] }));
  app.responses.set('questionHistory', { turns: [{ id: 'saved-snapshot', question: 'Unfinished question', resumable: true }] });
  await app.evaluate('refreshQuestions("m")');
  const resume = app.nodes.get('conversation').querySelectorAll('button')[0];
  assert.equal(resume.textContent, 'Continue question');
  app.evaluate('showSource({ kind: "file", id: 1, label: "Private file", quote: "Private excerpt" })');
  app.responses.set('resumeQuestion', { id: 'saved-snapshot' });
  app.responses.set('questionHistory', { turns: [] });
  resume.dispatch('click');
  await flush();
  assert.equal(app.requests.find((request) => request.action === 'resumeQuestion').payload.questionId, 'saved-snapshot');
  assert.equal(app.nodes.get('conversation').children.length, 0);
  assert.equal(app.nodes.get('source-dialog').open, false);
  assert.equal(app.nodes.get('source-quote').textContent, '');
});

for (const enabled of [false, true]) {
  test(`recording submits lowLatency=${enabled} and reflects actual capture settings`, async () => {
    const app = await application();
    app.install(detail(false));
    app.nodes.get('record-low-latency').checked = true;
    await openRecorder(app);
    assert.equal(app.nodes.get('record-low-latency').checked, false, 'a new dialog must default to standard mode');
    app.nodes.get('record-low-latency').checked = enabled;
    app.responses.set('startRecording', detail(true, enabled ? lowLatency : standard));
    app.nodes.get('record-form').dispatch('submit');
    await flush();
    const request = app.requests.find((item) => item.action === 'startRecording');
    assert.equal(request.payload.lowLatency, enabled);
    assert.equal(request.payload.meetingId, 'm');
    assert.equal(request.payload.deviceId, 'mic');
    assert.equal(app.nodes.get('record-low-latency').disabled, true);
    assert.equal(app.nodes.get('record-button').disabled, false, 'Stop must remain available');
    assert.equal(app.nodes.get('record-label').textContent, 'Stop recording');
    assert.equal(app.nodes.get('recording-settings').hidden, false);
    assert.equal(app.nodes.get('recording-settings').title, enabled ? 'preset: low-latency' : 'preset: default');
  });
}

test('cancelled dialogs and subsequent recordings reset the opt-in setting', async () => {
  const app = await application();
  app.install(detail(false));
  await openRecorder(app);
  app.nodes.get('record-low-latency').checked = true;
  app.nodes.get('record-dialog').close();
  await openRecorder(app);
  assert.equal(app.nodes.get('record-low-latency').checked, false);
  app.install(detail(true, lowLatency));
  app.install(detail(false));
  await openRecorder(app);
  assert.equal(app.nodes.get('record-low-latency').checked, false);
  assert.equal(app.nodes.get('record-low-latency').disabled, false);
});

test('stall and resume update the visible UI without a new transcript segment', async () => {
  const app = await application();
  app.install(detail(true, standard));
  app.responses.set('meeting', detail(true, { ...standard, stalled: true, secondsWithoutAudio: 3 }));
  await app.evaluate('refreshRecording()');
  assert.equal(app.nodes.get('recording-warning').hidden, false);
  assert.match(app.nodes.get('meeting-meta').textContent, /Waiting for microphone audio/);
  assert.match(app.nodes.get('transcript-empty').textContent, /Waiting for microphone audio/);
  assert.match(app.nodes.get('meeting-list').textContent, /Waiting for audio/);
  assert.equal(app.nodes.get('record-button').disabled, false);
  assert.equal(app.nodes.get('record-label').textContent, 'Stop recording');
  app.responses.set('meeting', detail(true, standard));
  await app.evaluate('refreshRecording()');
  assert.equal(app.nodes.get('recording-warning').hidden, true);
  assert.match(app.nodes.get('meeting-meta').textContent, /^Recording/);
  assert.equal(app.nodes.get('toast-text').textContent, 'Microphone audio resumed.');
  const afterResume = app.timerWrites;
  await app.evaluate('refreshRecording()');
  assert.equal(app.timerWrites, afterResume, 'unchanged polls must not repeat the resume toast');
});

test('stopping clears the warning and preset without announcing a false recovery', async () => {
  const app = await application();
  app.install(detail(true, { ...lowLatency, stalled: true }));
  app.responses.set('meeting', detail(false));
  await app.evaluate('refreshRecording()');
  assert.equal(app.nodes.get('recording-warning').hidden, true);
  assert.equal(app.nodes.get('recording-settings').hidden, true);
  assert.equal(app.nodes.get('recording-settings').textContent, '');
  assert.equal(app.nodes.get('record-low-latency').disabled, false);
  assert.equal(app.evaluate('state.recordingStatuses.has("m")'), false);
  assert.notEqual(app.nodes.get('toast-text').textContent, 'Microphone audio resumed.');
});

test('effective parameters come from the capture status, including environment overrides', async () => {
  const app = await application();
  app.install(detail(true, { ...lowLatency, asrThreads: 6, pauseMs: 425 }));
  assert.equal(app.nodes.get('recording-settings').textContent, 'Low latency · 6 threads · 425 ms pauses');
  assert.equal(app.nodes.get('recording-settings').title, 'preset: low-latency');
});

test('finished session displays only allowlisted health diagnostics', async () => {
  const app = await application();
  app.install(detail(false, null, {
    sessionHealth: {
      preset: 'low-latency', asrThreads: 4, pauseMs: 300,
      processed: 12, dropped_segments: 1, errors: 0,
      speech_to_ready_p95_s: 1.357, capture_missing_seconds: 2.5,
      capture_stalls: 2, input_overflows: 0,
      rawTranscript: 'must never be rendered', arbitrary: 'also hidden',
    },
  }));

  const health = app.nodes.get('summary-content').textContent;
  assert.match(health, /Session health/);
  assert.match(health, /Low latency · 4 threads · 300 ms pauses/);
  assert.match(health, /Segments processed12/);
  assert.match(health, /Speech-to-ready p951\.36 s/);
  assert.match(health, /Missing audio2\.5 s/);
  assert.doesNotMatch(health, /rawTranscript|must never be rendered|arbitrary/);
});

test('an already active capture cannot submit a second recording from the dialog', async () => {
  const app = await application();
  app.install(detail(true, standard));
  app.nodes.get('record-dialog').dataset.meetingId = 'm';
  app.nodes.get('device-select').value = 'mic';
  app.nodes.get('record-consent').checked = true;
  app.nodes.get('record-low-latency').checked = true;
  app.nodes.get('record-form').dispatch('submit');
  await flush();
  assert.equal(app.requests.some((item) => item.action === 'startRecording'), false);
});

// Objects created inside the isolated context have a different Object prototype.
const plain = (value) => JSON.parse(JSON.stringify(value));
const answered = (extra = {}) => ({ turns: [{
  id: 'snapshot-1', question: 'Why SQLite?',
  answer: { text: 'SQLite is local.', citations: [], skill: 'trace_decision', skillRoute: 'matched' }, ...extra,
}] });
const withSegments = () => detail(false, null, { segments: [{ id: 1, text: 'SQLite is local.', startMs: 0, endMs: 1000 }] });
const rateButtons = (app) => app.nodes.get('conversation').querySelectorAll('button')
  .filter((button) => String(button.className).startsWith('rate-button'));

test('the question type menu is filled from the backend and sent with a typed question', async () => {
  const app = await application();
  app.install(withSegments());
  app.responses.set('skills', { skills: [
    { name: 'auto', label: 'Auto', triggers: [] },
    { name: 'grill_me', label: 'Grill me', triggers: ['grill me'], rounds: 5 },
    { name: 'future_skill', label: 'Future skill', triggers: [] },
  ] });
  await app.evaluate('loadSkills()');
  const select = app.nodes.get('skill-select');
  assert.deepEqual(select.children.map((option) => option.value), ['auto', 'grill_me', 'future_skill']);
  assert.equal(select.value, 'auto');

  app.responses.set('beginQuestion', { id: 'snapshot-1' });
  select.value = 'future_skill';
  select.dispatch('change');
  app.nodes.get('question').value = 'Challenge the SQLite decision';
  app.nodes.get('ask-form').dispatch('submit');
  await flush();
  assert.equal(app.requests.find((request) => request.action === 'beginQuestion').payload.skill, 'future_skill');

  // A value the backend did not offer falls back to automatic routing.
  select.value = 'made_up';
  select.dispatch('change');
  assert.equal(app.evaluate('state.skill'), 'auto');
});

test('an answer shows its skill and can be rated, with a note for a negative rating', async () => {
  const app = await application();
  app.install(withSegments());
  app.responses.set('questionHistory', answered());
  app.responses.set('feedback', { id: 'snapshot-1', rating: 'up' });
  await app.evaluate('refreshQuestions("m")');
  assert.match(app.nodes.get('conversation').textContent, /Trace decision · auto/);
  assert.doesNotMatch(app.nodes.get('conversation').textContent, /searched again/);
  const [up, down] = rateButtons(app);
  up.dispatch('click');
  await flush();
  assert.deepEqual(plain(app.requests.find((request) => request.action === 'feedback').payload),
    { meetingId: 'm', questionId: 'snapshot-1', rating: 'up', note: '' });

  down.dispatch('click');
  assert.equal(app.nodes.get('feedback-dialog').open, true);
  app.nodes.get('feedback-note').value = ' too long ';
  app.responses.set('questionHistory', answered({ rating: 'down' }));
  app.nodes.get('feedback-form').dispatch('submit');
  await flush();
  assert.deepEqual(plain(app.requests.filter((request) => request.action === 'feedback').at(-1).payload),
    { meetingId: 'm', questionId: 'snapshot-1', rating: 'down', note: 'too long' });
  assert.equal(app.nodes.get('feedback-dialog').open, false);
  await flush();
  assert.equal(rateButtons(app)[1].attributes['aria-pressed'], 'true');
});

test('a model is set up once in Settings: provider defaults, key cleared at once, hint goes away', async () => {
  const app = await application();
  const presets = [
    { id: 'local', label: 'Local only (no model)', provider: 'demo', model: '', baseUrl: '' },
    { id: 'deepseek', label: 'DeepSeek', provider: 'openai', model: 'deepseek-flash', baseUrl: 'https://api.deepseek.com/v1' },
    { id: 'custom', label: 'Other OpenAI-compatible', provider: 'openai', model: '', baseUrl: '' },
  ];
  const unset = {
    provider: 'demo', providerLabel: 'Local extractive', model: '', baseUrl: '', sendEvidence: false, preset: 'local',
    presets, keyName: 'MEETING_COPILOT_API_KEY', keyHost: '', keyInEnvironment: false, keySaved: false, keyStoreAvailable: true,
    configFile: '/workspace/config.json', ready: false, researchRetries: 1, researchRetriesLimit: 5, toolRounds: 1, toolRoundsLimit: 2,
  };
  const ready = { ...unset, provider: 'openai', providerLabel: 'OpenAI-compatible', model: 'deepseek-flash',
                  baseUrl: 'https://api.deepseek.com/v1', sendEvidence: true, preset: 'deepseek', keyHost: 'api.deepseek.com',
                  keySaved: true, ready: true };
  app.install(withSegments());
  app.responses.set('settings', unset);
  app.responses.set('preferences', { preferences: ['Keep answers brief.'], limit: 5 });
  await app.evaluate('loadSettings()');
  const hint = app.nodes.get('setup-model');
  assert.equal(hint.hidden, false);
  assert.match(hint.textContent, /Set up a model/);

  hint.dispatch('click');
  await flush();
  assert.equal(app.nodes.get('settings-dialog').open, true);
  assert.match(app.nodes.get('settings-provider').textContent, /You only do this once/);
  assert.match(app.nodes.get('preference-list').textContent, /Keep answers brief\./);
  assert.equal(app.nodes.get('model-name').disabled, true);
  assert.equal(app.nodes.get('test-model').disabled, true);

  // Choosing a provider fills in its model and address and enables the fields.
  const select = app.nodes.get('model-preset');
  select.value = 'deepseek';
  select.dispatch('change');
  assert.equal(app.nodes.get('model-name').value, 'deepseek-flash');
  assert.equal(app.nodes.get('model-base-url').value, 'https://api.deepseek.com/v1');
  assert.equal(app.nodes.get('model-name').disabled, false);
  assert.equal(app.nodes.get('model-consent').checked, false);
  assert.match(app.nodes.get('settings-key-status').textContent, /kept per provider and never reused/);

  app.responses.set('saveModelSettings', ready);
  app.responses.set('meeting', withSegments());
  const field = app.nodes.get('api-key-input');
  field.value = '  sk-test-typed-in-the-dialog  ';
  app.nodes.get('model-consent').checked = true;
  // The two loop settings show their saved values and are clamped to their limits when sent.
  assert.equal(app.nodes.get('research-retries').value, '1');
  assert.equal(app.nodes.get('tool-rounds').value, '1');
  app.nodes.get('research-retries').value = '9';
  app.nodes.get('tool-rounds').value = '2';
  app.nodes.get('model-form').dispatch('submit');
  assert.equal(field.value, '');
  await flush();
  assert.deepEqual(plain(app.requests.find((request) => request.action === 'saveModelSettings').payload), {
    preset: 'deepseek', model: 'deepseek-flash', baseUrl: 'https://api.deepseek.com/v1', sendEvidence: true,
    researchRetries: 5, toolRounds: 2, key: 'sk-test-typed-in-the-dialog',
  });
  assert.match(app.nodes.get('settings-provider').textContent, /Ready\. Questions use deepseek-flash/);
  assert.match(app.nodes.get('settings-key-status').textContent, /A key for api\.deepseek\.com is saved/);
  assert.equal(app.nodes.get('remove-key').disabled, false);
  assert.equal(app.nodes.get('test-model').disabled, false);
  assert.equal(hint.hidden, true);

  app.responses.set('testModel', { ok: true, model: 'deepseek-flash' });
  app.nodes.get('test-model').dispatch('click');
  await flush();
  assert.match(app.nodes.get('model-test-status').textContent, /Connected\. deepseek-flash answered/);

  // Each remaining gap has its own hint; an environment variable counts as a key.
  assert.match(app.evaluate('setupGap')({ ...ready, keySaved: false }), /needs an API key/);
  assert.equal(app.evaluate('setupGap')({ ...ready, keySaved: false, keyInEnvironment: true }), '');
  assert.match(app.evaluate('setupGap')({ ...ready, sendEvidence: false }), /allow sending excerpts/);
  // Without a credential store the key cannot be typed here.
  app.evaluate('renderSettings')({ ...ready, keyStoreAvailable: false });
  assert.equal(field.disabled, true);
});

test('suggestions are requested on demand and a suggested preference can be saved', async () => {
  const app = await application();
  app.install(withSegments());
  app.nodes.get('open-suggest').dispatch('click');
  assert.equal(app.nodes.get('suggest-dialog').open, true);
  assert.equal(app.requests.some((request) => request.action === 'suggest'), false);
  app.responses.set('suggest', {
    mode: 'rules only; no model call', turnsAnalysed: 3, feedbackRows: 1, activation: 'Nothing was changed.',
    suggestions: [{ kind: 'preference', title: 'Save a wording preference', observed: '2 questions ask for this',
                    action: 'Save it as a wording preference.', support: 2, preference: 'Keep answers brief.' }],
  });
  app.responses.set('preferences', { preferences: ['Keep answers brief.'], limit: 5 });
  app.nodes.get('run-suggest').dispatch('click');
  await flush();
  assert.deepEqual(plain(app.requests.find((request) => request.action === 'suggest').payload), { meetingId: 'm', useModel: false });
  assert.match(app.nodes.get('suggest-status').textContent, /3 question\(s\) and 1 rating\(s\) analysed/);
  const [save] = app.nodes.get('suggest-results').querySelectorAll('button');
  save.dispatch('click');
  await flush();
  assert.deepEqual(plain(app.requests.find((request) => request.action === 'preferences').payload),
    { action: 'add', value: 'Keep answers brief.' });
  assert.equal(save.disabled, true);
});

test('a transcript correction sends the selected segment and marks older answers', async () => {
  const app = await application();
  const original = detail(false, null, { segments: [{ id: 7, text: 'SQLite is local.', startMs: 12_000, endMs: 15_000 }] });
  const corrected = detail(false, null, { segments: [{ id: 8, text: 'SQLite is preferred for deployment simplicity.', startMs: 12_000, endMs: 15_000 }] });
  app.install(original);
  const edit = app.nodes.get('transcript-list').querySelectorAll('button')[0];
  edit.dispatch('click');
  assert.equal(app.nodes.get('transcript-edit-dialog').open, true);
  assert.equal(app.nodes.get('transcript-edit-text').value, 'SQLite is local.');
  assert.match(app.nodes.get('transcript-edit-time').textContent, /00:12/);

  app.responses.set('correctTranscript', corrected);
  app.nodes.get('transcript-edit-text').value = ' SQLite is preferred for deployment simplicity. ';
  app.nodes.get('transcript-edit-form').dispatch('submit');
  await flush();
  const request = app.requests.find((item) => item.action === 'correctTranscript');
  assert.deepEqual(JSON.parse(JSON.stringify(request.payload)), {
    meetingId: 'm', segmentId: 7, text: 'SQLite is preferred for deployment simplicity.',
  });
  assert.match(app.nodes.get('transcript-list').textContent, /deployment simplicity/);
  assert.match(app.nodes.get('toast-text').textContent, /Transcript corrected/);

  app.responses.set('questionHistory', { turns: [{
    id: 'old-snapshot', question: 'Why SQLite?', transcriptChanged: true,
    answer: { text: 'SQLite is local.', citations: [] },
  }] });
  await app.evaluate('refreshQuestions("m")');
  assert.match(app.nodes.get('conversation').textContent, /Transcript updated after this answer/);
});
