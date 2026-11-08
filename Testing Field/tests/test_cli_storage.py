import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from local_meeting_ai.infrastructure.storage import MeetingStorage
from local_meeting_ai.paths import AppPaths
from local_meeting_ai.domain.errors import ValidationError
from meeting_copilot.audio import listen


def test_upstream_media_layout_and_scoped_cleanup(tmp_path):
    storage=MeetingStorage(AppPaths(tmp_path),1024)
    original=storage.new_live_capture_path('m');original.write_bytes(b'fixture')
    chunk=storage.new_realtime_chunk_path('m');chunk.write_bytes(b'chunk')
    other=storage.new_live_capture_path('other');other.write_bytes(b'keep')
    assert storage.delete_meeting_audio('m',[])==12
    assert other.read_bytes()==b'keep'
    with pytest.raises(ValidationError):storage.delete_meeting('../escape')
    storage.delete_meeting('m');assert not original.parent.parent.exists()


def test_consent_before_any_microphone(store,tmp_path):
    with pytest.raises(ValueError,match='consent'):listen(store,'alice','m',tmp_path,'no-device',1,False)


def test_cli_space_paths_replay_export_cleanup(tmp_path):
    root=tmp_path/'Testing Field';root.mkdir();config=root/'config.json'
    def cli(*args,ok=True):
        p=subprocess.run([sys.executable,'-m','meeting_copilot','--config',str(config),*args],capture_output=True,text=True,timeout=30)
        assert (p.returncode==0)==ok,p.stderr
        return json.loads(p.stdout) if ok else p.stderr
    cli('init');cli('create','m')
    replay=root/'replay.jsonl';replay.write_text(json.dumps({'event_id':'e1','segment_id':'s1','seq':1,'revision':1,'start_ms':0,'end_ms':10,'text':'SQLite is local.'})+'\n',encoding='utf-8')
    assert cli('replay','m',str(replay))['events']==1
    result=cli('ask','m','Why SQLite?');sid=result['snapshot_id'];assert result['claims']
    target=root/'export file.json';cli('export',sid,str(target));assert json.loads(target.read_text())['private']
    assert cli('checkpoint',sid)['result']['claims']
    cli('cleanup','m','--confirm','m');assert cli('list')==[]
    cli('export',sid,str(root/'denied.json'),ok=False)
    assert not (root/'denied.json').exists()
