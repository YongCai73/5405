"""Security boundaries use real files; all contents are synthetic fixtures."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from meeting_copilot.store import Store
from meeting_copilot.trace import Trace


@pytest.fixture
def permissive_umask():
    # Production must not depend on the operator's umask. Tests run serially.
    previous = os.umask(0)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions are not Windows DACLs')
def test_database_sidecars_and_new_runtime_are_owner_only(tmp_path, permissive_umask):
    path = tmp_path / 'runtime' / 'evidence.db'
    store = Store(path)
    try:
        store.create('alice', 'm', 'Synthetic meeting')
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        for file in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm')):
            assert file.is_file()
            assert stat.S_IMODE(file.stat().st_mode) == 0o600
    finally:
        store.close()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions are not Windows DACLs')
def test_all_new_runtime_ancestors_are_private(tmp_path, permissive_umask):
    parent = tmp_path / 'new-parent'
    runtime = parent / 'runtime'
    store = Store(runtime / 'evidence.db')
    try:
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    finally:
        store.close()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions are not Windows DACLs')
def test_private_runtime_does_not_change_preexisting_parent_permissions(tmp_path):
    parent = tmp_path / 'existing'
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    store = Store(parent / 'runtime' / 'evidence.db')
    try:
        assert stat.S_IMODE(parent.stat().st_mode) == 0o755
        assert stat.S_IMODE((parent / 'runtime').stat().st_mode) == 0o700
    finally:
        store.close()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions are not Windows DACLs')
def test_reopen_tightens_existing_database_and_open_sidecars(tmp_path):
    path = tmp_path / 'runtime' / 'evidence.db'
    first = Store(path)
    second = None
    try:
        first.create('alice', 'm', 'Keep this meeting')
        files = [path, Path(str(path) + '-wal'), Path(str(path) + '-shm')]
        for file in files:
            file.chmod(0o666)
        second = Store(path)
        assert second.metadata('alice', 'm')['title'] == 'Keep this meeting'
        assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in files)
    finally:
        if second:
            second.close()
        first.close()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions are not Windows DACLs')
def test_trace_and_redacted_export_are_owner_only(tmp_path, permissive_umask):
    trace = Trace(tmp_path / 'runtime' / 'trace.jsonl')
    trace.emit('fixture', policy='allow', question='must not be logged')
    target = tmp_path / 'trace-export.jsonl'
    assert trace.redacted_export(target) == 1
    assert 'must not be logged' not in target.read_text()
    assert stat.S_IMODE(trace.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions are not Windows DACLs')
def test_cli_creates_private_config_and_snapshot_export(tmp_path, permissive_umask):
    config = tmp_path / 'config.json'

    def cli(*args):
        result = subprocess.run(
            [sys.executable, '-m', 'meeting_copilot', '--config', str(config), *args],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    cli('init')
    cli('create', 'm')
    sid = cli('snapshot', 'm')['snapshot_id']
    target = tmp_path / 'meeting.json'
    cli('export', sid, str(target))
    assert json.loads(target.read_text())['snapshot']['meeting'] == 'm'
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize('link_type', ['symlink', 'hardlink'])
def test_database_refuses_linked_files_without_changing_target(tmp_path, link_type):
    original = tmp_path / 'original.db'
    store = Store(original)
    store.create('alice', 'm', 'Keep')
    store.close()
    before = original.read_bytes()
    mode = original.stat().st_mode
    alias = tmp_path / 'linked.db'
    try:
        if link_type == 'symlink':
            alias.symlink_to(original)
        else:
            os.link(original, alias)
    except OSError:
        pytest.skip('platform does not permit link fixture')
    with pytest.raises((ValueError, OSError)):
        unexpected = Store(alias)
        unexpected.close()
    assert original.read_bytes() == before
    assert original.stat().st_mode == mode


@pytest.mark.parametrize('link_type', ['symlink', 'hardlink'])
def test_trace_refuses_linked_destination_without_overwriting_target(tmp_path, link_type):
    original = tmp_path / 'keep.txt'
    original.write_text('KEEP SYNTHETIC CONTENT')
    alias = tmp_path / 'trace.jsonl'
    try:
        if link_type == 'symlink':
            alias.symlink_to(original)
        else:
            os.link(original, alias)
    except OSError:
        pytest.skip('platform does not permit link fixture')
    with pytest.raises((ValueError, OSError)):
        Trace(alias).emit('fixture')
    assert original.read_text() == 'KEEP SYNTHETIC CONTENT'


def test_failed_atomic_trace_export_preserves_previous_file(tmp_path, monkeypatch):
    trace = Trace(tmp_path / 'trace.jsonl')
    trace.emit('fixture', policy='allow')
    target = tmp_path / 'export.jsonl'
    target.write_text('PREVIOUS EXPORT')
    before = set(tmp_path.iterdir())

    def fail_replace(*args, **kwargs):
        raise OSError('synthetic disk failure')

    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError, match='synthetic disk failure'):
        trace.redacted_export(target)
    assert target.read_text() == 'PREVIOUS EXPORT'
    assert set(tmp_path.iterdir()) == before


@pytest.mark.skipif(os.name != 'posix', reason='Regression concerns POSIX process-owned file locks')
def test_opening_another_store_cannot_release_active_sqlite_write_lock(store, monkeypatch):
    import meeting_copilot.store as store_module

    path = store.root / 'evidence.db'
    child = '''
import sqlite3, sys
db = sqlite3.connect(sys.argv[1], timeout=0.1)
try:
    db.execute("INSERT INTO meetings VALUES('competing','Competing writer','alice')")
    db.commit()
    print('committed')
except sqlite3.OperationalError as exc:
    if 'locked' not in str(exc):
        raise
    print('locked')
finally:
    db.close()
'''

    def competing_write():
        completed = subprocess.run([sys.executable, '-c', child, str(path)],
                                   capture_output=True, text=True, timeout=5)
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    ready = threading.Event()
    proceed = threading.Event()
    connect = store_module.sqlite3.connect

    def pause_before_sqlite_connect(*args, **kwargs):
        # Real Store initialization has completed its file-permission operations.
        ready.set()
        if not proceed.wait(timeout=10):
            raise RuntimeError('test synchronization timed out')
        return connect(*args, **kwargs)

    other = None
    with ThreadPoolExecutor(1) as pool:
        try:
            with store.tx() as db:
                db.execute("INSERT INTO meetings VALUES('held','Uncommitted writer','alice')")
                db.execute("INSERT INTO acl VALUES('held','alice')")
                assert competing_write() == 'locked'
                monkeypatch.setattr(store_module.sqlite3, 'connect', pause_before_sqlite_connect)
                future = pool.submit(Store, path)
                assert ready.wait(timeout=5)
                assert competing_write() == 'locked'
            proceed.set()
            other = future.result(timeout=10)
            assert other.metadata('alice', 'held')['title'] == 'Uncommitted writer'
        finally:
            proceed.set()
            if other is not None:
                other.close()
            elif 'future' in locals():
                future.result(timeout=10).close()
