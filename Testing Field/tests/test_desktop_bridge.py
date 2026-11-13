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
