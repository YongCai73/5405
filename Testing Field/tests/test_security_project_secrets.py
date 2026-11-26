"""Recognized credential canaries must never become retrievable project evidence."""
import hashlib
import json

import pytest

from meeting_copilot.project import scan
from meeting_copilot.service import EvidenceService


@pytest.mark.parametrize('content', [
    'const key = "ghp_' + 'a' * 36 + '";',
    'github_pat_' + 'b' * 64,
    'sk-proj-' + 'c' * 48,
    'sk-ant-api03-' + 'd' * 48,
    'AWS_ACCESS_KEY_ID = "AKIA' + 'E' * 16 + '"',
    '-----BEGIN OPENSSH PRIVATE KEY-----\nSYNTHETIC TEST ONLY\n-----END OPENSSH PRIVATE KEY-----',
    'api_key = "synthetic-credential-value-123"',
    '{"client_secret": "synthetic-private-value-123"}',
    'API_TOKEN=synthetic_unquoted_token_123456',
    'Authorization: Bearer synthetic-bearer-value-123456',
    'url = "postgresql://alice:syntheticpassword@localhost/database"',
])
def test_secret_in_ordinary_filename_is_excluded_before_indexing(tmp_path, content):
    project = tmp_path / 'approved'
    project.mkdir()
    sensitive = project / 'settings.py'
    sensitive.write_text(content, encoding='utf-8')
    (project / 'architecture.md').write_text('SQLite stores the meeting history.')
    before = sensitive.read_bytes()
    assert [name for name, _ in scan(project)] == ['architecture.md']
    assert sensitive.read_bytes() == before


@pytest.mark.parametrize('content', [
    'api_key = os.getenv("SERVICE_API_KEY")\n',
    'password = config.get("password")\n',
    'API_TOKEN=None\n',
    'def get_token():\n    return runtime_credentials()\n',
    'SQLite was chosen because it requires no separate database service.\n',
    '-----BEGIN PUBLIC KEY-----\nSYNTHETIC PUBLIC MATERIAL\n-----END PUBLIC KEY-----\n',
])
def test_benign_code_and_public_evidence_remain_indexable(tmp_path, content):
    project = tmp_path / 'approved'
    project.mkdir()
    (project / 'architecture.py').write_bytes(content.encode('utf-8'))
    assert scan(project) == [('architecture.py', content)]


def test_excluded_credentials_still_count_toward_scan_budget(tmp_path):
    project = tmp_path / 'approved'
    project.mkdir()
    # 35 * 120,000 = 4,200,000 bytes, beyond the existing 4 MiB bound.
    content = 'sk-proj-' + 'a' * 119992
    for number in range(35):
        (project / f'file-{number:02}.txt').write_text(content)
    with pytest.raises(ValueError, match='manifest size limit'):
        scan(project)


async def test_refresh_cannot_leak_project_credential_into_qa_or_export(store, assistant, tmp_path):
    project = tmp_path / 'approved'
    project.mkdir()
    canary = 'ghp_' + 'q' * 36
    unsafe = project / 'settings.py'
    unsafe.write_text('GITHUB_TOKEN = "' + canary + '"\n')
    (project / 'architecture.md').write_text('SQLite was chosen for deployment simplicity.')
    original_hash = hashlib.sha256(unsafe.read_bytes()).hexdigest()
    folder = store.approve('alice', 'm', project)
    store.refresh('alice', folder)
    sid = store.snapshot('alice', 'm', folders=[folder])
    service = EvidenceService(store, 'alice', sid)
    assert [row['path'] for row in service.list_project_files()] == ['architecture.md']
    assert service.search_project('GITHUB_TOKEN') == []
    result = await assistant.ask(sid, 'Why SQLite?', 'security-check')
    assert result['claims'] and canary not in json.dumps(result)
    assert canary not in json.dumps(store.export('alice', sid, 'security-check'))
    assert store.db.execute("SELECT COUNT(*) FROM evidence WHERE path='settings.py'").fetchone()[0] == 0
    assert hashlib.sha256(unsafe.read_bytes()).hexdigest() == original_hash
