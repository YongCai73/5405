"""Exercise the transport the macOS app uses without an API key or microphone."""

import json
from pathlib import Path
import subprocess
import sys


BRIDGE = Path(__file__).resolve().parents[1] / 'desktop' / 'bridge.py'


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
    requests = ''.join(json.dumps({'id': number, 'action': action, 'payload': payload}) + '\n'
                       for number, (action, payload) in enumerate(operations))
    process = subprocess.run([sys.executable, '-u', str(BRIDGE), '--data-dir', str(data)],
                             input=requests, capture_output=True, text=True, timeout=30, check=True)
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert [reply['id'] for reply in replies] == list(range(len(operations)))
    assert [reply['ok'] for reply in replies] == [True, True, True, True, False, True]
    sample = replies[0]['result']['meeting']
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
