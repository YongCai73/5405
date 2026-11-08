"""Independent SQLite/CLI processes; simulated capture is explicitly not mic validation."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from test_protocols import fake_http
from meeting_copilot.store import Store
from meeting_copilot.audio import listen


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
        'base_url':fake_http['url']+'/v1','send_evidence':True,'timeout_seconds':10}))
    env=dict(os.environ,MEETING_COPILOT_API_KEY='fake-contract-key',LITELLM_LOCAL_MODEL_COST_MAP='True')
    db=Store(runtime/'evidence.db');db.create('host','live','Synthetic capture')
    db.ingest('host','live',{'event_id':'first','segment_id':'s1','seq':1,'revision':1,'start_ms':0,'end_ms':500,'text':'SQLite is local.'})
    script=tmp_path/'capture.py'
    script.write_text('''import json,sys,time
from pathlib import Path
from meeting_copilot.store import Store
from meeting_copilot.assistant import Assistant,Workers,LiveSummaries
from meeting_copilot.provider import Provider
from meeting_copilot.config import Config
from meeting_copilot.trace import Trace
from meeting_copilot.audio import Pipeline
root=Path(sys.argv[1]);store=Store(root/'evidence.db')
a=Assistant(store,'host',Provider(Config()),Trace(root/'trace.jsonl'))
w=Workers(a);bg=LiveSummaries(w,'live',.1)
p=Pipeline(lambda x:[x],lambda x:store.ingest('host','live',x))
try:
    for i in range(2,402):
        if (root/'stop-capture').exists():break
        p.submit({'event_id':f'e{i}','segment_id':f's{i}','seq':i,'revision':1,'start_ms':i*1000,'end_ms':i*1000+500,'text':f'Concurrent ingestion marker {i}.'})
        time.sleep(.05)
finally:
    stats=p.close();bg.close();w.close();store.close()
    (root/'capture-result.json').write_text(json.dumps({**stats,'summaries':bg.completed,'summary_errors':bg.failures}))
''')
    capture=subprocess.Popen([sys.executable,str(script),str(runtime)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    fake_http['delay']=1.3
    ask=subprocess.Popen([sys.executable,'-m','meeting_copilot','--config',str(config),'ask','live','SQLite'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+12
        while not fake_http['requests'] and time.monotonic()<deadline:time.sleep(.02)
        assert fake_http['requests']
        before=db.gaps('host','live')[0]['received'];time.sleep(.5)
        after=db.gaps('host','live')[0]['received'];assert after>before and ask.poll() is None
        stdout,stderr=ask.communicate(timeout=15);assert ask.returncode==0,stderr
        answer=json.loads(stdout);sid=answer['snapshot_id'];watermark=db.snapshot_info('host',sid)['watermark']
        assert answer['claims'] and all(c['source_id']<=watermark for c in answer['claims'])
        assert db.db.execute('SELECT MAX(id) FROM evidence').fetchone()[0]>watermark
        (runtime/'stop-capture').touch()
        capture_stdout,capture_stderr=capture.communicate(timeout=15);assert capture.returncode==0,capture_stderr
        stats=json.loads((runtime/'capture-result.json').read_text())
        assert stats['processed']>=10 and stats['errors']==0 and stats['dropped_segments']==0
        assert stats['summaries']>5 and stats['summary_errors']==0
        assert db.latest_summary('host','live')['text']
    finally:
        for proc in (ask,capture):
            if proc.poll() is None:proc.terminate();proc.communicate(timeout=5)
        db.close()
