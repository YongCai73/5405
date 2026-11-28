"""Exercise the transport the macOS app uses without an API key or microphone."""

import json
from pathlib import Path
import subprocess
import sys

from meeting_copilot.store import Store


BRIDGE = Path(__file__).resolve().parents[1] / 'desktop' / 'bridge.py'


def run_bridge(data, operations, *extra_args):
    requests = ''.join(json.dumps({'id': number, 'action': action, 'payload': payload}) + '\n'
                       for number, (action, payload) in enumerate(operations))
    process = subprocess.run([sys.executable, '-u', str(BRIDGE), '--data-dir', str(data), *extra_args],
                             input=requests, capture_output=True, text=True, timeout=30, check=True)
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert [reply['id'] for reply in replies] == list(range(len(operations)))
    return replies


def test_desktop_meetings_citations_and_private_export(tmp_path):
    data = tmp_path / 'Meeting Copilot'
    export = tmp_path / 'Meeting notes.json'
    operations = [
        ('bootstrap', {}),
        ('ask', {'meetingId': 'demo', 'question': 'Compare SQLite and Kafka deployment', 'skill': 'compare_options'}),
        ('summary', {'meetingId': 'demo'}),
        ('export', {'meetingId': 'demo', 'path': str(export)}),
        ('meeting', {'meetingId': 'unknown'}),
        ('create', {'title': 'Next meeting'}),
    ]
    replies = run_bridge(data, operations)
    assert [reply['ok'] for reply in replies] == [True, True, True, True, False, True]
    sample = replies[0]['result']['meeting']
    assert sample['answerMode'] == 'local'
    segment_ids = {row['id'] for row in sample['segments']}
    citations = replies[1]['result']['citations']
    assert {citation['kind'] for citation in citations} == {'segment', 'file'}
    assert all(citation['id'] in segment_ids for citation in citations if citation['kind'] == 'segment')
    assert any('architecture.md' in citation['label'] for citation in citations)
    assert replies[2]['result']['text']
    assert export.exists()
    exported = json.loads(export.read_text(encoding='utf-8'))
    assert exported['summary'] and exported['evidence']
    assert not exported['private']
    assert replies[-1]['result']['meeting']['title'] == 'Next meeting'


def test_trusted_config_and_local_sample_export(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    config = workspace / 'config.json'
    config.write_text(json.dumps({
        'data_dir': 'configured-data', 'principal': 'desktop-user', 'provider': 'openai',
        'api_key_env': 'TEST_DESKTOP_MISSING_KEY',
    }), encoding='utf-8')
    monkeypatch.delenv('TEST_DESKTOP_MISSING_KEY', raising=False)
    data = workspace / 'configured-data'
    store = Store(data / 'evidence.db')
    store.create('desktop-user', 'team', 'Real team meeting')
    store.ingest('desktop-user', 'team', {
        'event_id': 'event', 'segment_id': 'segment', 'seq': 1, 'revision': 1,
        'start_ms': 0, 'end_ms': 1000, 'text': 'SQLite runs locally.', 'final': True,
    })
    store.close()
    export = tmp_path / 'real.json'
    replies = run_bridge(workspace, [
        ('bootstrap', {}),
        ('ask', {'meetingId': 'team', 'question': 'Why SQLite?'}),
        ('demo', {}),
        ('ask', {'meetingId': 'demo', 'question': 'Compare SQLite and Kafka deployment', 'skill': 'compare_options'}),
        ('summary', {'meetingId': 'demo'}),
        ('export', {'meetingId': 'team', 'path': str(export)}),
    ])
    assert [reply['ok'] for reply in replies] == [True, False, True, True, True, True]
    assert replies[0]['result']['meeting']['providerLabel'] == 'OpenAI-compatible'
    assert replies[0]['result']['meeting']['answerMode'] == 'api'
    assert 'send_evidence' in replies[1]['error']
    assert replies[2]['result']['meeting']['answerMode'] == 'local'
    assert replies[3]['result']['citations']
    assert replies[4]['result']['text']
    assert 'SQLite runs locally.' in json.loads(export.read_text(encoding='utf-8'))['summary']['text']
    assert not (workspace / 'data' / 'evidence.db').exists()

    config.write_text(json.dumps({
        'data_dir': 'configured-data', 'principal': 'desktop-user', 'provider': 'openai',
        'send_evidence': True, 'api_key_env': 'TEST_DESKTOP_MISSING_KEY',
    }), encoding='utf-8')
    reply, = run_bridge(tmp_path / 'unused', [
        ('ask', {'meetingId': 'team', 'question': 'Why SQLite?'}),
    ], '--config', str(config))
    assert not reply['ok'] and 'API key' in reply['error']
    assert 'TEST_DESKTOP_MISSING_KEY' not in reply['error']


def test_export_includes_allowlisted_session_health_without_content(tmp_path):
    workspace = tmp_path / 'Meeting Copilot'
    data = workspace / 'data'
    export = tmp_path / 'health.json'
    store = Store(data / 'evidence.db')
    store.create('local-host', 'team', 'Team meeting')
    with store.tx() as db:
        db.execute('CREATE TABLE desktop_session_health (meeting TEXT PRIMARY KEY, content TEXT NOT NULL)')
        db.execute('INSERT INTO desktop_session_health VALUES (?, ?)', ('team', json.dumps({
            'processed': 5, 'errors': 0, 'capture_missing_seconds': 1.25,
            'preset': 'default', 'asrThreads': 2, 'pauseMs': 500,
            'rawTranscript': 'must not be exported',
        })))
    store.close()

    reply, = run_bridge(workspace, [('export', {'meetingId': 'team', 'path': str(export)})])

    assert reply['ok']
    assert json.loads(export.read_text(encoding='utf-8'))['sessionHealth'] == {
        'processed': 5, 'errors': 0, 'capture_missing_seconds': 1.25,
        'preset': 'default', 'asrThreads': 2, 'pauseMs': 500,
    }


def test_real_meeting_scope_is_explicit_and_sample_id_collision_is_safe(tmp_path):
    workspace = tmp_path / 'workspace'
    store = Store(workspace / 'data' / 'evidence.db')
    store.create('local-host', 'current', 'Current meeting')
    store.create('local-host', 'demo', 'Previous real meeting')
    store.ingest('local-host', 'demo', {
        'event_id': 'event', 'segment_id': 'segment', 'seq': 1, 'revision': 1,
        'start_ms': 0, 'end_ms': 1000, 'text': 'The Aurora roadmap is approved.', 'final': True,
    })
    approved = tmp_path / 'team project'
    approved.mkdir()
    (approved / 'plan.md').write_text('Aurora runs SQLite locally.\n', encoding='utf-8')
    folder_id = store.approve('local-host', 'current', approved)
    store.refresh('local-host', folder_id)
    store.close()
    question = {'meetingId': 'current', 'question': 'What was decided about Aurora?'}
    replies = run_bridge(workspace, [
        ('meeting', {'meetingId': 'current'}),
        ('ask', question),
        ('ask', {**question, 'previous': ['demo'], 'folders': [folder_id]}),
        ('demo', {}),
        ('meeting', {'meetingId': 'demo'}),
    ])
    assert all(reply['ok'] for reply in replies)
    assert not replies[1]['result']['citations']
    assert {citation['meetingId'] for citation in replies[2]['result']['citations']} == {'current', 'demo'}
    assert replies[3]['result']['meeting']['id'] != 'demo'
    assert replies[4]['result']['title'] == 'Previous real meeting'
    assert len(replies[4]['result']['segments']) == 1


def test_final_minutes_persist_with_sources_and_follow_transcript_revisions(tmp_path):
    workspace = tmp_path / 'workspace'
    export = tmp_path / 'minutes.json'
    event = {
        'event_id': 'closing', 'segment_id': 'closing', 'seq': 1, 'revision': 1,
        'start_ms': 1_190_000, 'end_ms': 1_200_000,
        'text': 'We decided to ship the final desktop build.', 'final': True,
    }
    store = Store(workspace / 'data' / 'evidence.db')
    store.create('local-host', 'team', 'Team meeting')
    source_id = store.ingest('local-host', 'team', event)
    store.close()

    request = {'meetingId': 'team', 'final': True}
    replies = run_bridge(workspace, [('summary', request), ('summary', request)])
    assert all(reply['ok'] for reply in replies)
    final = replies[0]['result']
    assert replies[1]['result'] == final
    assert final['minutes']['mode'] == 'local'
    sources = [source for key in ('overview', 'topics', 'decisions', 'actionItems', 'openQuestions')
               for item in final['minutes'][key] for source in item['sources']]
    assert sources and {source['id'] for source in sources} == {source_id}

    loaded, saved = run_bridge(workspace, [('meeting', request), ('export', {**request, 'path': str(export)})])
    assert loaded['ok'] and saved['ok']
    assert loaded['result']['minutes'] == final['minutes']
    data = json.loads(export.read_text(encoding='utf-8'))
    assert data['minutes'] == final['minutes']
    assert source_id in {row['id'] for row in data['evidence']}
    assert not data['private']

    store = Store(workspace / 'data' / 'evidence.db')
    store.ingest('local-host', 'team', {**event, 'event_id': 'correction', 'seq': 2, 'revision': 2,
                                        'text': 'We decided to delay the final desktop build.'})
    store.close()
    loaded, refreshed = run_bridge(workspace, [('meeting', request), ('summary', request)])
    assert loaded['result']['minutes'] is None and 'ship' not in loaded['result']['summary']
    assert refreshed['ok'] and 'delay the final desktop build' in refreshed['result']['text']


def test_skills_come_from_contracts_and_new_skills_route_through_the_desktop(tmp_path):
    from meeting_copilot.skill_runtime import MANUAL

    replies = run_bridge(tmp_path / 'data', [
        ('bootstrap', {}),
        ('skills', {}),
        ('ask', {'meetingId': 'demo', 'question': 'Grill me on the SQLite decision', 'skill': 'auto'}),
        ('ask', {'meetingId': 'demo', 'question': 'Because it needs no database service.', 'skill': 'auto'}),
        ('ask', {'meetingId': 'demo', 'question': 'Explain FTS5 in plain language', 'skill': 'clarify'}),
        ('ask', {'meetingId': 'demo', 'question': 'Why SQLite?', 'skill': 'not_a_skill'}),
    ])
    assert [reply['ok'] for reply in replies] == [True, True, True, True, True, False]
    offered = replies[1]['result']['skills']
    assert [skill['name'] for skill in offered] == ['auto', *MANUAL]
    assert next(skill for skill in offered if skill['name'] == 'grill_me')['rounds'] == 5
    first, second, manual = (reply['result'] for reply in replies[2:5])
    assert (first['skill'], first['skillRoute']) == ('grill_me', 'matched')
    assert 'Challenge 1 of 5' in first['text']
    # The second message has no trigger phrase; auto routing stays in the open session.
    assert second['skill'] == 'grill_me' and 'Challenge 2 of 5' in second['text']
    assert (manual['skill'], manual['skillRoute']) == ('clarify', 'manual')


def test_feedback_preferences_and_suggest_actions(tmp_path):
    data = tmp_path / 'data'
    question = 'Why did we choose SQLite?'
    replies = run_bridge(data, [
        ('bootstrap', {}),
        ('ask', {'meetingId': 'demo', 'question': question, 'skill': 'trace_decision'}),
        ('questionHistory', {'meetingId': 'demo'}),
    ])
    snapshot = replies[2]['result']['turns'][0]['id']
    replies = run_bridge(data, [
        ('feedback', {'meetingId': 'demo', 'questionId': snapshot, 'rating': 'down'}),
        ('feedback', {'meetingId': 'demo', 'questionId': 'missing', 'rating': 'up'}),
        ('preferences', {'action': 'add', 'value': 'Keep answers brief.'}),
        ('preferences', {'action': 'add', 'value': 'two\nlines'}),
        ('preferences', {'action': 'list'}),
        ('preferences', {'action': 'remove', 'index': 0}),
        ('preferences', {'action': 'remove', 'index': 0}),
        ('suggest', {'meetingId': 'demo'}),
        ('suggest', {'meetingId': 'demo', 'useModel': True}),
    ])
    assert [reply['ok'] for reply in replies] == [True, False, True, False, True, True, False, True, False]
    assert replies[4]['result'] == {'preferences': ['Keep answers brief.'], 'limit': 5}
    assert replies[5]['result']['preferences'] == []
    report = replies[7]['result']
    assert report['suggestions'] and 'Nothing was changed' in report['activation']
    assert 'command' not in report['activation'] + report['suggestions'][0]['action']
    assert 'sample' in replies[8]['error']


def test_rating_a_background_answer_appears_in_history(tmp_path):
    import time

    data = tmp_path / 'data'
    bridge = subprocess.Popen([sys.executable, '-u', str(BRIDGE), '--data-dir', str(data)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def call(number, action, payload):
        bridge.stdin.write(json.dumps({'id': number, 'action': action, 'payload': payload}) + '\n')
        bridge.stdin.flush()
        reply = json.loads(bridge.stdout.readline())
        assert reply['id'] == number
        return reply

    try:
        assert call(0, 'bootstrap', {})['ok']
        started = call(1, 'beginQuestion', {'meetingId': 'demo', 'question': 'Why did we choose SQLite?', 'skill': 'auto'})
        question_id = started['result']['id']
        deadline = time.monotonic() + 30
        while True:
            turn = call(2, 'questionHistory', {'meetingId': 'demo'})['result']['turns'][0]
            if 'answer' in turn or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        assert turn['answer']['skill'] == 'trace_decision' and 'rating' not in turn
        assert call(3, 'feedback', {'meetingId': 'demo', 'questionId': question_id, 'rating': 'down', 'note': 'too long'})['ok']
        assert call(4, 'feedback', {'meetingId': 'demo', 'questionId': question_id, 'rating': 'sideways'})['ok'] is False
        assert call(5, 'questionHistory', {'meetingId': 'demo'})['result']['turns'][0]['rating'] == 'down'
    finally:
        bridge.stdin.close()
        bridge.wait(timeout=30)


def test_settings_report_key_status_without_exposing_the_key(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'config.json').write_text(json.dumps({
        'provider': 'openai', 'model': 'test-model', 'base_url': 'https://example.invalid/v1',
        'send_evidence': True, 'api_key_env': 'TEST_DESKTOP_SETTINGS_KEY',
    }), encoding='utf-8')
    secret = 'sk-test-environment-secret-value'
    monkeypatch.setenv('TEST_DESKTOP_SETTINGS_KEY', secret)
    model = {'preset': 'custom', 'model': 'test-model', 'baseUrl': 'https://example.invalid/v1', 'sendEvidence': True}
    replies = run_bridge(workspace, [
        ('settings', {}),
        ('saveModelSettings', {**model, 'key': 'short'}),
        ('saveModelSettings', {**model, 'key': 'has a space inside it'}),
        ('saveModelSettings', {**model, 'key': 'sk-test-another-secret-value'}),
        ('deleteApiKey', {}),
    ])
    settings = replies[0]['result']
    assert settings['provider'] == 'openai' and settings['model'] == 'test-model' and settings['preset'] == 'custom'
    assert settings['keyName'] == 'TEST_DESKTOP_SETTINGS_KEY' and settings['keyInEnvironment'] is True
    assert settings['ready'] is True
    # The test suite disables the OS credential store, so saving a key is refused with guidance.
    assert settings['keyStoreAvailable'] is False and settings['keySaved'] is False
    assert [reply['ok'] for reply in replies[1:]] == [False, False, False, True]
    assert 'valid API key' in replies[1]['error'] and 'valid API key' in replies[2]['error']
    assert 'credential store is unavailable' in replies[3]['error']
    assert secret not in json.dumps(replies) and 'another-secret' not in json.dumps(replies)


def test_model_is_set_up_once_in_the_app_and_used_without_a_restart(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    config = workspace / 'config.json'
    monkeypatch.delenv('MEETING_COPILOT_API_KEY', raising=False)
    deepseek = {'preset': 'deepseek', 'model': 'deepseek-flash', 'baseUrl': 'https://api.deepseek.com/v1'}
    replies = run_bridge(workspace, [
        ('bootstrap', {}),
        ('settings', {}),
        ('saveModelSettings', {**deepseek, 'sendEvidence': False}),
        ('meeting', {'meetingId': 'demo'}),
        ('saveModelSettings', {**deepseek, 'sendEvidence': True}),
        ('meeting', {'meetingId': 'demo'}),
        ('ask', {'meetingId': 'demo', 'question': 'Why did we choose SQLite?'}),
        ('testModel', {}),
        ('saveModelSettings', {**deepseek, 'baseUrl': 'ftp://example.invalid', 'sendEvidence': True}),
        ('saveModelSettings', {**deepseek, 'model': '', 'sendEvidence': True}),
        ('saveModelSettings', {**deepseek, 'baseUrl': ''}),
        ('saveModelSettings', {'preset': 'unknown'}),
    ])
    assert [reply['ok'] for reply in replies] == [True, True, True, True, True, True, False, False, False, False, False, False]
    first = replies[1]['result']
    assert (first['preset'], first['provider'], first['ready']) == ('local', 'demo', False)
    assert [row['id'] for row in first['presets']] == ['local', 'deepseek', 'openai', 'anthropic', 'custom']
    # Without permission to send excerpts the sample stays on local answers.
    assert replies[2]['result']['sendEvidence'] is False and replies[3]['result']['answerMode'] == 'local'
    saved = replies[4]['result']
    assert (saved['preset'], saved['provider'], saved['model']) == ('deepseek', 'openai', 'deepseek-flash')
    assert saved['configFile'] == str(config) and saved['ready'] is False
    # The running backend switched: the sample now goes to the model, which has no key yet.
    assert replies[5]['result']['answerMode'] == 'api'
    assert 'API key' in replies[6]['error'] and 'API key' in replies[7]['error']
    written = json.loads(config.read_text(encoding='utf-8'))
    assert written['provider'] == 'openai' and written['base_url'] == 'https://api.deepseek.com/v1'
    assert written['send_evidence'] is True and written['streaming'] is True
    assert (written['timeout_seconds'], written['max_tokens']) == (120, 4096)
    assert written['data_dir'] == 'data' and written['api_key_env'] == 'MEETING_COPILOT_API_KEY'
    assert not list(workspace.glob('config.json.*'))

    # The next launch reads the file: nothing to set up again, and the data is where it was.
    replies = run_bridge(workspace, [
        ('settings', {}),
        ('bootstrap', {}),
        ('saveModelSettings', {'preset': 'local'}),
        ('meeting', {'meetingId': 'demo'}),
        ('ask', {'meetingId': 'demo', 'question': 'Why did we choose SQLite?'}),
    ])
    assert all(reply['ok'] for reply in replies)
    assert replies[0]['result']['preset'] == 'deepseek'
    assert replies[1]['result']['meeting']['answerMode'] == 'api'
    assert replies[2]['result']['provider'] == 'demo' and replies[3]['result']['answerMode'] == 'local'
    assert replies[4]['result']['citations']
    assert json.loads(config.read_text(encoding='utf-8'))['provider'] == 'demo'


def test_saving_the_model_keeps_other_configuration(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    custom = tmp_path / 'elsewhere' / 'custom.json'
    custom.parent.mkdir()
    custom.write_text(json.dumps({
        'data_dir': 'kept-data', 'principal': 'kept-user', 'api_key_env': 'KEPT_KEY_NAME', 'asr_model': 'base.en',
        'tool_rounds': 1,
    }), encoding='utf-8')
    reply, = run_bridge(workspace, [
        ('saveModelSettings', {'preset': 'anthropic', 'model': 'some-model', 'baseUrl': '', 'sendEvidence': True}),
    ], '--config', str(custom))
    assert reply['ok'] and reply['result']['configFile'] == str(custom) and reply['result']['keyName'] == 'KEPT_KEY_NAME'
    written = json.loads(custom.read_text(encoding='utf-8'))
    assert (written['provider'], written['model'], written['base_url']) == ('anthropic', 'some-model', None)
    assert (written['data_dir'], written['principal'], written['asr_model'], written['tool_rounds']) == (
        'kept-data', 'kept-user', 'base.en', 1)
    assert not (workspace / 'config.json').exists()
    assert (custom.parent / 'kept-data' / 'evidence.db').exists()


def test_save_and_delete_key_through_the_bridge(tmp_path, monkeypatch):
    """Drive the handlers in process with an in-memory stand-in for the OS credential store."""
    import importlib.util
    import types

    import pytest

    from meeting_copilot import credentials
    from meeting_copilot.config import Config
    from meeting_copilot.provider import ProviderError

    saved = {}
    fake = types.ModuleType('keyring')
    fake.get_password = lambda service, name: saved.get((service, name))
    fake.set_password = lambda service, name, value: saved.__setitem__((service, name), value)
    fake.delete_password = lambda service, name: saved.pop((service, name))
    monkeypatch.setitem(sys.modules, 'keyring', fake)
    monkeypatch.setenv('MEETING_COPILOT_KEYRING', '1')
    monkeypatch.delenv('MEETING_COPILOT_API_KEY', raising=False)
    monkeypatch.setattr(credentials, '_found', {})
    monkeypatch.syspath_prepend(str(BRIDGE.parent))
    spec = importlib.util.spec_from_file_location('desktop_bridge_under_test', BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # A key saved by an earlier version carries no host. It must never be used, and is cleaned up.
    saved[(credentials.SERVICE, 'MEETING_COPILOT_API_KEY')] = 'sk-legacy-key-without-a-host'
    bridge = module.DesktopBridge(Config(_base=tmp_path))
    try:
        before = bridge.run('settings', {})
        assert (before['keySaved'], before['ready'], before['keyStoreAvailable']) == (False, False, True)
        after = bridge.run('saveModelSettings', {
            'preset': 'custom', 'model': 'm', 'baseUrl': 'https://example.invalid/v1', 'sendEvidence': True,
            'key': '  sk-test-saved-through-bridge  ',
        })
        assert after['keySaved'] is True and after['ready'] is True and 'sk-test' not in json.dumps(after)
        assert after['keyHost'] == 'example.invalid'
        assert saved == {(credentials.SERVICE, 'MEETING_COPILOT_API_KEY@example.invalid'): 'sk-test-saved-through-bridge'}
        # The key goes to the credential store only, never into the configuration file.
        assert 'sk-test' not in (tmp_path / 'config.json').read_text(encoding='utf-8')
        # The running backend uses the new provider and key without a restart.
        assert bridge.assistant.provider.config.model == 'm' and bridge.workers.a is bridge.assistant
        assert bridge.assistant.provider._client()[1] == 'sk-test-saved-through-bridge'
        # Saving again without typing a key keeps the saved one.
        assert bridge.run('saveModelSettings', {
            'preset': 'custom', 'model': 'm2', 'baseUrl': 'https://example.invalid/v1', 'sendEvidence': True,
        })['keySaved'] is True
        # Choosing another provider does not carry the key over: that host has none until one is entered.
        switched = bridge.run('saveModelSettings', {'preset': 'anthropic', 'model': 'some-model', 'baseUrl': '', 'sendEvidence': True})
        assert (switched['keyHost'], switched['keySaved'], switched['ready']) == ('api.anthropic.com', False, False)
        with pytest.raises(ProviderError, match='missing_api_key'):
            bridge.assistant.provider._key()
        # Going back finds the key that was saved for that host.
        back = bridge.run('saveModelSettings', {
            'preset': 'custom', 'model': 'm2', 'baseUrl': 'https://example.invalid/v1', 'sendEvidence': True})
        assert back['keySaved'] is True and bridge.assistant.provider._key() == 'sk-test-saved-through-bridge'
        assert bridge.run('deleteApiKey', {})['keySaved'] is False
        assert saved == {} and credentials.api_key('MEETING_COPILOT_API_KEY', 'example.invalid') is None
    finally:
        bridge.close()


def test_both_hosts_allow_exactly_the_bridge_actions():
    """A bridge action missing from a host allowlist is silently dropped on that platform."""
    import re

    desktop = BRIDGE.parent
    handlers = re.search(r'handlers = \{(.*?)\n        \}', BRIDGE.read_text(encoding='utf-8'), re.S).group(1)
    bridge_actions = set(re.findall(r"'(\w+)': self\.", handlers))
    swift = re.search(r'private let actions = Set\(\[(.*?)\]\)', (desktop / 'main.swift').read_text(encoding='utf-8'), re.S)
    windows = re.search(r'Actions = new\(StringComparer\.Ordinal\)\s*\{(.*?)\};',
                        (desktop / 'windows' / 'MainWindow.xaml.cs').read_text(encoding='utf-8'), re.S)
    assert len(bridge_actions) > 15
    assert set(re.findall(r'"(\w+)"', swift.group(1))) == bridge_actions
    assert set(re.findall(r'"(\w+)"', windows.group(1))) == bridge_actions
