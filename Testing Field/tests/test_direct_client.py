"""The direct chat-completions client used for OpenAI-compatible addresses (no LiteLLM import)."""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meeting_copilot.config import Config
from meeting_copilot.provider import Provider, ProviderError


@pytest.fixture
def endpoint():
    """A local endpoint whose next reply (status, body) the test chooses."""
    state = {'status': 200, 'body': json.dumps({'choices': [{'message': {'content': 'OK'}}],
                                                 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}), 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append({'path': self.path, 'authorization': self.headers.get('Authorization'), 'body': body})
            payload = state['body'].encode()
            self.send_response(state['status'])
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state['url'] = f'http://127.0.0.1:{server.server_port}/v1'
    yield state
    server.shutdown()
    server.server_close()


def provider(url, **extra):
    return Provider(Config(provider='openai', model='some-model', base_url=url, send_evidence=True,
                           api_key_env='TEST_DIRECT_KEY', timeout_seconds=10, **extra))


def test_only_openai_compatible_addresses_use_the_direct_client():
    assert provider('https://example.invalid/v1')._direct()
    assert not Provider(Config(provider='openai', model='m', send_evidence=True))._direct()
    assert not Provider(Config(provider='anthropic', model='m', base_url='https://example.invalid', send_evidence=True))._direct()


async def test_request_shape_and_reply(endpoint, monkeypatch):
    monkeypatch.setenv('TEST_DIRECT_KEY', 'test-key-value')
    reply = await provider(endpoint['url'] + '/', thinking='disabled', max_tokens=77).chat([{'role': 'user', 'content': 'hi'}])
    assert (reply.content, reply.input_tokens, reply.output_tokens) == ('OK', 3, 1)
    request, = endpoint['requests']
    assert request['path'] == '/v1/chat/completions' and request['authorization'] == 'Bearer test-key-value'
    assert request['body'] == {'model': 'some-model', 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 77,
                               'temperature': 0, 'stream': False, 'thinking': {'type': 'disabled'}}


@pytest.mark.parametrize('status,body,code', [
    (401, '{"error": {"message": "bad key test-key-value"}}', 'authentication'),
    (403, '{}', 'authentication'),
    (404, '{}', 'badrequest'),
    (400, '{}', 'badrequest'),
    (429, '{}', 'ratelimit'),
    (500, 'upstream exploded', 'provider_error'),
    (200, 'not json at all', 'provider_error'),
    (200, '{"choices": []}', 'provider_error'),
])
async def test_failures_become_fixed_codes_without_the_provider_text(endpoint, monkeypatch, status, body, code):
    monkeypatch.setenv('TEST_DIRECT_KEY', 'test-key-value')
    endpoint.update(status=status, body=body)
    with pytest.raises(ProviderError) as raised:
        await provider(endpoint['url']).chat([{'role': 'user', 'content': 'hi'}])
    assert raised.value.code == code and str(raised.value) == code


async def test_unreachable_address_is_a_connection_error(monkeypatch):
    monkeypatch.setenv('TEST_DIRECT_KEY', 'test-key-value')
    with pytest.raises(ProviderError, match='connection'):
        await provider('http://127.0.0.1:9/v1').chat([{'role': 'user', 'content': 'hi'}])


def test_a_request_never_loads_litellm(endpoint, monkeypatch):
    """The point of the direct client: the first question does not wait for a three-thousand-module import."""
    monkeypatch.setenv('TEST_DIRECT_KEY', 'test-key-value')
    script = ('import asyncio, sys\n'
              'from meeting_copilot.config import Config\n'
              'from meeting_copilot.provider import Provider\n'
              f'p = Provider(Config(provider="openai", model="m", base_url={endpoint["url"]!r}, send_evidence=True, api_key_env="TEST_DIRECT_KEY"))\n'
              'p.warm_up()\n'
              'print(asyncio.run(p.chat([{"role": "user", "content": "hi"}])).content, "litellm" in sys.modules)\n')
    done = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=120, check=True)
    assert done.stdout.split() == ['OK', 'False']
