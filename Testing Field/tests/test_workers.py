import asyncio
import threading
import time
import pytest
from meeting_copilot.audio import Pipeline
from meeting_copilot.assistant import Workers,Assistant
from meeting_copilot.provider import Provider,Completion
from meeting_copilot.config import Config
from meeting_copilot.store import Denied

class Slow(Provider):
    def __init__(self):super().__init__(Config(provider='openai'));self.started=threading.Event();self.release=threading.Event()
    async def complete(self,q,e,c):
        self.started.set()
        await asyncio.to_thread(self.release.wait,3)
        return Completion('{"claims":[]}')


def test_slow_provider_capture_continuity_and_question_cutoff(store,event,tmp_path,assistant):
    store.ingest('alice','m',event);provider=Slow();a=Assistant(store,'alice',provider,assistant.trace);workers=Workers(a)
    output=[];pipe=Pipeline(lambda n:[n],output.append)
    try:
        sid,f=workers.submit('m','SQLite')
        assert provider.started.wait(2)
        store.ingest('alice','m',{**event,'event_id':'late','segment_id':'late','seq':2,'text':'Future text'})
        for n in range(30):pipe.submit(n);time.sleep(.002)
        summary=workers.summary(sid).result(2)
        assert summary['result']['source_ids']==[1] and not f.done()
        assert len(store.evidence('alice',sid))==1
    finally:provider.release.set();workers.close();stats=pipe.close()
    assert output==list(range(30)) and stats['dropped_segments']==0


def test_pipeline_bounded_drop_accounting():
    release=threading.Event();started=threading.Event();output=[]
    def slow(n):started.set();release.wait(2);return [n]
    pipe=Pipeline(slow,output.append,capacity=2)
    try:
        pipe.submit(0);assert started.wait(1)
        accepted=sum(pipe.submit(n) for n in range(1,21));assert accepted==2 and pipe.dropped==18 and pipe.audio.qsize()==2
    finally:release.set();stats=pipe.close()
    assert stats['processed']==3


def test_revocation_during_generation(store,event,assistant):
    store.ingest('alice','m',event);store.grant('alice','m','bob');p=Slow();a=Assistant(store,'bob',p,assistant.trace);w=Workers(a)
    try:
        sid,f=w.submit('m','SQLite');assert p.started.wait(2);store.grant('alice','m','bob',False);p.release.set()
        with pytest.raises(Denied):f.result(3)
    finally:p.release.set();w.close()


def test_worker_pending_requests_bounded(store,event,assistant):
    store.ingest('alice','m',event);p=Slow();a=Assistant(store,'alice',p,assistant.trace);w=Workers(a)
    try:
        for _ in range(4):w.submit('m','SQLite')
        with pytest.raises(RuntimeError):w.submit('m','SQLite')
    finally:p.release.set();w.close()
