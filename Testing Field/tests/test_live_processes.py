"""Independent SQLite/CLI processes; simulated capture is explicitly not mic validation."""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
import pytest
from test_protocols import fake_http
from meeting_copilot.store import Store
from meeting_copilot.audio import listen


def assert_capture_identity(identity,launcher_pid,parent_pid,nonce):
    """Bind a direct interpreter or its redirector child to this test launch."""
    assert identity['nonce']==nonce
    pid,ppid=identity['pid'],identity['ppid']
    assert type(pid) is int and type(ppid) is int and pid>0 and ppid>0
    assert pid!=parent_pid and pid!=ppid
    assert ((pid==launcher_pid and ppid==parent_pid) or
            (pid!=launcher_pid and ppid==launcher_pid))


@pytest.mark.parametrize('pid,ppid,nonce,valid',[
    (9048,1000,'expected',True),       # Direct Unix interpreter.
    (20584,9048,'expected',True),      # Windows venv redirector -> interpreter.
    (20584,7777,'expected',False),     # Unrelated process.
    (9048,7777,'expected',False),      # Correct PID, wrong parent.
    (20584,1000,'expected',False),     # Sibling, not the launcher's child.
    (1000,9048,'expected',False),      # Parent cannot be its own capture child.
    (20584,9048,'stale',False),        # Readiness from another launch.
])
def test_capture_identity_ancestry_and_nonce(pid,ppid,nonce,valid):
    identity={'pid':pid,'ppid':ppid,'nonce':nonce}
    if valid:assert_capture_identity(identity,9048,1000,'expected')
    else:
        with pytest.raises(AssertionError):assert_capture_identity(identity,9048,1000,'expected')


def test_listen_selected_source_final_partial_and_cleanup(monkeypatch,store,event,tmp_path):
    import meeting_copilot.audio as audio
    from types import SimpleNamespace
    calls=[]
    class Backend:
        def start(self,**kwargs):calls.append(('start',kwargs));self.remaining=True
        def drain_frames(self):
            if not self.remaining:return None
            self.remaining=False
            return SimpleNamespace(sample_rate=16000,channels=1,start_frame=0,end_frame=8000,pcm_s16le=b'\0\0'*8000)
        def stop(self):calls.append(('stop',))
        def shutdown(self):calls.append(('shutdown',))
    class ASR:
        def __init__(self,*args):pass
        def transcribe(self,path):
            import wave
            with wave.open(str(path)) as f:assert f.getnframes()==8000
            return [SimpleNamespace(text='SQLite partial chunk.',start_ms=0,end_ms=500)]
        def close(self):calls.append(('asr_closed',))
    monkeypatch.setattr(audio,'create_audio_capture_backend',Backend)
    monkeypatch.setattr(audio,'LocalASR',ASR)
    def interrupt(_):raise KeyboardInterrupt()
    monkeypatch.setattr(audio.time,'sleep',interrupt)
    result=listen(store,'alice','m',store.root,'sounddevice:7',0,True)
    assert result['interrupted'] and result['processed']==1
    assert calls[0][1]['source_id']=='sounddevice:7'
    assert ('stop',) in calls and ('shutdown',) in calls and ('asr_closed',) in calls
    assert not list(store.root.rglob('realtime/*.wav'))


def test_separate_cli_process_asks_while_ingestion_and_summaries_continue(fake_http,tmp_path):
    config=tmp_path/'config.json';runtime=tmp_path/'runtime'
    config.write_text(json.dumps({'principal':'host','data_dir':str(runtime),'provider':'openai','model':'gpt-4o-mini',
        'base_url':fake_http['url']+'/v1','send_evidence':True,'timeout_seconds':120}))
    nonce=secrets.token_hex(32)
    env=dict(os.environ,MEETING_COPILOT_API_KEY='fake-contract-key',LITELLM_LOCAL_MODEL_COST_MAP='True')
    db=Store(runtime/'evidence.db');db.create('host','live','Synthetic capture')
    db.ingest('host','live',{'event_id':'first','segment_id':'s1','seq':1,'revision':1,'start_ms':0,'end_ms':500,'text':'SQLite is local.'})
    script=tmp_path/'capture.py'
    script.write_text('''import json,os,sys,time
from pathlib import Path
from meeting_copilot.store import Store
from meeting_copilot.assistant import Assistant,Workers,LiveSummaries
from meeting_copilot.provider import Provider
from meeting_copilot.config import Config
from meeting_copilot.trace import Trace
from meeting_copilot.audio import Pipeline
root=Path(sys.argv[1]);store=Store(root/'evidence.db')
identity={'pid':os.getpid(),'ppid':os.getppid(),'nonce':sys.argv[2]}
a=Assistant(store,'host',Provider(Config()),Trace(root/'trace.jsonl'))
# Functional concurrency load: 2 events/sec and 1 summary/sec leave WAL headroom.
# Production accumulates 3-second audio chunks and defaults to 15-second summaries.
# Deliberate queue saturation belongs in test_pipeline_bounded_drop_accounting.
w=Workers(a);bg=LiveSummaries(w,'live',1)
p=Pipeline(lambda x:[x],lambda x:store.ingest('host','live',x))
try:
    i=2
    while not (root/'stop-capture').exists():
        p.submit({'event_id':f'e{i}','segment_id':f's{i}','seq':i,'revision':1,'start_ms':i*1000,'end_ms':i*1000+500,'text':f'Concurrent ingestion marker {i}.'})
        i+=1
        # Ready means committed ingestion AND successful shared summaries, not imports alone.
        if not (root/'capture-ready.json').exists() and p.processed>=10 and bg.completed>5:
            ready=root/'capture-ready.tmp'
            ready.write_text(json.dumps({**identity,'processed':p.processed,'summaries':bg.completed}))
            ready.replace(root/'capture-ready.json')
        time.sleep(.5)
finally:
    stats=p.close();bg.close();w.close();store.close()
    (root/'capture-result.json').write_text(json.dumps({**identity,**stats,'summaries':bg.completed,'summary_errors':bg.failures}))
''',encoding='utf-8')
    capture=ask=None
    response_gate=Event();fake_http['response_gate']=response_gate

    def wait_for(description,condition,children,timeout=120):
        # Windows cold imports need their own bounded startup budget. Fail fast on exit.
        deadline=time.monotonic()+timeout
        while not condition():
            for name,proc in children:
                if proc.poll() is not None:
                    stdout,stderr=proc.communicate(timeout=5)
                    pytest.fail(f'{name} exited ({proc.returncode}) while {description}: {stdout} {stderr}')
            if time.monotonic()>=deadline:
                pytest.fail(f'Timed out after {timeout}s while {description}')
            time.sleep(.02)

    def progress():
        return (db.gaps('host','live')[0]['received'],
                db.db.execute('SELECT COUNT(*) FROM summaries WHERE meeting=?',('live',)).fetchone()[0])

    try:
        capture=subprocess.Popen([sys.executable,str(script),str(runtime),nonce],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        wait_for('waiting for capture readiness',lambda:(runtime/'capture-ready.json').exists(),[('capture',capture)])
        ready=json.loads((runtime/'capture-ready.json').read_text())
        assert_capture_identity(ready,capture.pid,os.getpid(),nonce)
        assert ready['processed']>=10 and ready['summaries']>5
        assert progress()[0]>=11 and db.latest_summary('host','live')['text']
        ask=subprocess.Popen([sys.executable,'-m','meeting_copilot','--config',str(config),'ask','live','SQLite'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        # Receipt at the loopback endpoint is the real ask-child readiness handshake.
        wait_for('waiting for ask provider request',lambda:bool(fake_http['requests']),[('ask',ask),('capture',capture)])
        before=progress()
        before_watermark=db.db.execute('SELECT MAX(id) FROM evidence').fetchone()[0]
        def advanced():
            after=progress()
            if not (after[0]>before[0] and after[1]>before[1]):return False
            # A completed summary must include post-request ingestion before release.
            # Count advancement alone can finish an older, already queued snapshot.
            summary=db.latest_summary('host','live')
            return db.snapshot_info('host',summary['snapshot_id'])['watermark']>before_watermark
        wait_for('checking ingestion and summaries during pending ask',advanced,[('ask',ask),('capture',capture)],timeout=30)
        assert ask.poll() is None and capture.poll() is None
        response_gate.set()
        stdout,stderr=ask.communicate(timeout=30);assert ask.returncode==0,stderr
        answer=json.loads(stdout);sid=answer['snapshot_id'];snapshot=db.snapshot_info('host',sid);watermark=snapshot['watermark']
        assert answer['claims'] and 'SQLite is local.' in answer['text']
        assert all(c['source_id']<=watermark and c['source_id'] in json.loads(snapshot['refs']) for c in answer['claims'])
        assert db.db.execute('SELECT MAX(id) FROM evidence').fetchone()[0]>watermark
        assert all(after>prior for after,prior in zip(progress(),before))
        summary=db.latest_summary('host','live')
        assert summary['text'] and db.snapshot_info('host',summary['snapshot_id'])['watermark']>watermark
        (runtime/'stop-capture').touch()
        capture_stdout,capture_stderr=capture.communicate(timeout=30);assert capture.returncode==0,capture_stderr
        stats=json.loads((runtime/'capture-result.json').read_text())
        assert {k:stats[k] for k in ('pid','ppid','nonce')}=={k:ready[k] for k in ('pid','ppid','nonce')}
        assert stats['processed']>=10 and stats['errors']==0 and stats['dropped_segments']==0
        assert stats['summaries']>ready['summaries'] and stats['summary_errors']==0
        gaps=db.gaps('host','live')[0]
        assert gaps['missing']==0 and gaps['received']==stats['processed']+1
        assert ask.poll() is not None and capture.poll() is not None
    finally:
        response_gate.set()
        (runtime/'stop-capture').touch()
        for proc in (ask,capture):
            if proc is None:continue
            try:
                proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name=='nt':
                    # A venv launcher can own the actual interpreter. Kill the
                    # still-live owned tree before reaping its launcher.
                    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
                proc.kill();proc.communicate(timeout=10)
        db.close()
