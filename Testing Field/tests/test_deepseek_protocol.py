"""MOCK protocol tests for a DeepSeek-style thinking endpoint. No real DeepSeek request is made.

The loopback server below imitates the two documented rules this adapter depends on
(https://api-docs.deepseek.com/guides/thinking_mode/, read 2026-11-27):
  * thinking is on unless the body carries {"thinking": {"type": "disabled"}};
  * in thinking mode, a request that carries assistant tool calls must send each
    call's reasoning_content back unchanged, otherwise the API answers HTTP 400.
Passing here shows the request shape matches that description. It does not show the
live endpoint accepts it.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from meeting_copilot.assistant import Assistant
from meeting_copilot.config import Config
from meeting_copilot.model_tools import EvidenceTools
from meeting_copilot.provider import Provider, ProviderError
from meeting_copilot.trace import Trace

REASONING = 'PRIVATE-REASONING-CANARY step by step'
QUOTE = 'SQLite was chosen for simplicity.'


@pytest.fixture
def deepseek():
    state = {'requests': [], 'issued': {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['content-length'])))
            state['requests'].append(body)
            thinking = (body.get('thinking') or {}).get('type', 'enabled') == 'enabled'
            turns = [m for m in body['messages'] if m.get('role') == 'assistant' and m.get('tool_calls')]
            missing = thinking and any(m.get('reasoning_content') != state['issued'].get(m['tool_calls'][0]['id']) for m in turns)
            if missing:
                raw = json.dumps({'error': {'message': 'reasoning_content must be passed back', 'type': 'invalid_request_error'}}).encode()
                self.send_response(400); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            wants_tool = len(turns) < state.get('tool_turns', 1) and body.get('tools')
            call_id = f'call-{len(turns)+1}'
            message = {'role': 'assistant', 'content': None if wants_tool else json.dumps({'claims': [{'source_id': state['source_id'], 'quote': QUOTE}]})}
            if thinking:
                message['reasoning_content'] = f'{REASONING} {call_id}'
            if wants_tool:
                message['tool_calls'] = [{'id': call_id, 'type': 'function', 'function': {'name': 'search_evidence', 'arguments': json.dumps({'query': 'SQLite'})}}]
                if thinking:
                    state['issued'][call_id] = message['reasoning_content']
            usage = {'prompt_tokens': 10, 'completion_tokens': 6, 'total_tokens': 16}
            base = {'id': 'x', 'created': 1, 'model': body['model']}
            if body.get('stream'):
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                def emit(delta, finish=None, extra=None):
                    chunk = {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}], **(extra or {})}
                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode()); self.wfile.flush()
                emit({'role': 'assistant', 'content': ''})
                if thinking:
                    text = message['reasoning_content']
                    emit({'reasoning_content': text[:9]}); emit({'reasoning_content': text[9:]})
                if wants_tool:
                    call = message['tool_calls'][0]
                    emit({'tool_calls': [{'index': 0, **call}]})
                else:
                    emit({'content': message['content']})
                # DeepSeek puts usage on the last chunk; there is no separate usage-only chunk.
                emit({}, 'tool_calls' if wants_tool else 'stop', {'usage': usage})
                self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush(); return
            raw = json.dumps({**base, 'object': 'chat.completion', 'usage': usage,
                              'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if wants_tool else 'stop'}]}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name='mock-deepseek'); thread.start()
    state['url'] = f'http://127.0.0.1:{server.server_port}/v1'
    yield state
    server.shutdown(); server.server_close(); thread.join()


def config(state, monkeypatch, **kw):
    monkeypatch.setenv('API_KEY', 'mock-key-not-a-secret')
    return Config(provider='openai', model='deepseek-flash', base_url=state['url'], api_key_env='API_KEY', send_evidence=True,
                  timeout_seconds=120, **kw)


@pytest.fixture
def sid(store, event, deepseek):
    deepseek['source_id'] = store.ingest('alice', 'm', {**event, 'final': True})
    return store.snapshot('alice', 'm')


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('tool_turns', [1, 2])
async def test_mock_thinking_tool_rounds_pass_reasoning_back(deepseek, monkeypatch, store, sid, streaming, tool_turns):
    deepseek['tool_turns'] = tool_turns
    provider = Provider(config(deepseek, monkeypatch, streaming=streaming, tool_rounds=2))
    reply = await provider.complete('Why SQLite?', [], {'name': 'direct', 'output_fields': ['evidence']}, tool_context=EvidenceTools(store, 'alice', sid))
    assert len(deepseek['requests']) == tool_turns+1
    assert reply.tool_events == [{'name': 'search_evidence', 'status': 'ok'}]*tool_turns
    assert json.loads(reply.content)['claims'][0]['quote'] == QUOTE
    assert reply.input_tokens == 10*(tool_turns+1) and reply.output_tokens == 6*(tool_turns+1)
    last = deepseek['requests'][-1]
    sent_back = [m['reasoning_content'] for m in last['messages'] if m.get('tool_calls')]
    assert sent_back == [f'{REASONING} call-{n+1}' for n in range(tool_turns)]
    assert 'thinking' not in deepseek['requests'][0]  # null config leaves the provider default


async def test_mock_endpoint_really_rejects_a_missing_passback(deepseek, monkeypatch):
    """Negative control: the mock would fail the adapter if it dropped reasoning_content."""
    deepseek['source_id'] = 1
    provider = Provider(config(deepseek, monkeypatch))
    first = await provider.chat([{'role': 'user', 'content': 'q'}], EvidenceTools.all_schemas)
    assert first.tool_calls and first.reasoning_content.startswith(REASONING)
    without = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': None, 'tool_calls': first.tool_calls},
               {'role': 'tool', 'tool_call_id': first.tool_calls[0]['id'], 'content': '{}'}]
    with pytest.raises(ProviderError) as error:
        await provider.chat(without, EvidenceTools.all_schemas)
    assert error.value.code in ('badrequest', 'provider_error') and 'passed back' not in str(error.value)


@pytest.mark.parametrize('streaming', [False, True])
async def test_mock_non_thinking_switch_is_sent_and_needs_no_passback(deepseek, monkeypatch, store, sid, streaming):
    provider = Provider(config(deepseek, monkeypatch, streaming=streaming, tool_rounds=1, thinking='disabled'))
    reply = await provider.complete('Why SQLite?', [], {'name': 'direct', 'output_fields': ['evidence']}, tool_context=EvidenceTools(store, 'alice', sid))
    assert all(r['thinking'] == {'type': 'disabled'} for r in deepseek['requests']) and len(deepseek['requests']) == 2
    assert reply.reasoning_content == '' and not any('reasoning_content' in m for m in deepseek['requests'][-1]['messages'])


async def test_mock_follow_up_with_tools_has_no_fabricated_assistant_turn(deepseek, monkeypatch, store, sid):
    provider = Provider(config(deepseek, monkeypatch, tool_rounds=1))
    history = [{'snapshot_id': 's', 'question': 'earlier', 'prior_answer': 'earlier answer'}]
    await provider.complete('And why?', [], {'name': 'direct', 'output_fields': ['evidence'], 'private_conversation': history},
                            tool_context=EvidenceTools(store, 'alice', sid))
    first = deepseek['requests'][0]['messages']
    assert [m['role'] for m in first] == ['system', 'user'] and 'earlier answer' in first[1]['content'] and 'CURRENT QUESTION' in first[1]['content']


async def test_mock_reasoning_never_reaches_answer_trace_or_checkpoint(deepseek, monkeypatch, store, sid, tmp_path):
    trace = Trace(tmp_path/'trace.jsonl')
    assistant = Assistant(store, 'alice', Provider(config(deepseek, monkeypatch, tool_rounds=1)), trace)
    result = await assistant.ask(sid, 'Why SQLite?', 'c', 'auto')
    assert result['skill'] == 'trace_decision' and result['citations'] and result['tool_events'] == [{'name': 'search_evidence', 'status': 'ok'}]
    assert result['usage']['input_tokens'] == 20
    stored = json.dumps([result, store.checkpoint_get('alice', sid, 'c'), store.private_get('alice', sid, 'c')])
    assert 'PRIVATE-REASONING-CANARY' not in stored
    assert 'PRIVATE-REASONING-CANARY' not in (tmp_path/'trace.jsonl').read_text(encoding='utf-8')
    assert [r['event'] for r in trace.rows(task_id=result['task_id'])] == ['skill_route', 'retrieval', 'tool_call', 'answer']


def test_thinking_configuration_is_validated(tmp_path):
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'thinking': 'disabled'})); assert Config.load(path).thinking == 'disabled'
    path.write_text(json.dumps({})); assert Config.load(path).thinking is None
    path.write_text(json.dumps({'thinking': 'off'}))
    with pytest.raises(ValueError, match='thinking'):
        Config.load(path)
