"""Upstream capture/ASR with independent bounded chunk and ingestion queues."""
import asyncio
import queue
import threading
import time
import uuid
import wave
from dataclasses import asdict
from pathlib import Path
from local_meeting_ai.adapters.audio_capture.factory import create_audio_capture_backend
from local_meeting_ai.infrastructure.storage import MeetingStorage
from local_meeting_ai.paths import AppPaths

class Pipeline:
    def __init__(self,transcribe,ingest,trace=None,capacity=4):
        self.transcribe=transcribe;self.ingest=ingest;self.trace=trace
        self.audio=queue.Queue(capacity);self.segments=queue.Queue(32);self.dropped=0;self.errors=0;self.processed=0;self.stop_event=threading.Event();self.error=None
        self.threads=[threading.Thread(target=self._asr,name='local-asr'),threading.Thread(target=self._ingest,name='evidence-ingest')]
        for t in self.threads:t.start()
    def submit(self,item):
        try:self.audio.put_nowait(item);return True
        except queue.Full:
            self.dropped+=1
            if self.trace:self.trace.emit('audio_drop',dropped_segments=self.dropped,queue_depth=self.audio.qsize())
            return False
    def _asr(self):
        while True:
            item=self.audio.get()
            try:
                if item is None:break
                try:
                    for segment in self.transcribe(item):
                        try:self.segments.put(segment,timeout=2)
                        except queue.Full:self.dropped+=1
                except Exception as e:self.errors+=1;self.error=type(e).__name__
                finally:
                    if isinstance(item,dict) and item.get('temporary'):Path(item['path']).unlink(missing_ok=True)
            finally:self.audio.task_done()
        self.segments.put(None)
    def _ingest(self):
        while True:
            item=self.segments.get()
            try:
                if item is None:break
                try:self.ingest(item);self.processed+=1
                except Exception as e:self.errors+=1;self.error=type(e).__name__
            finally:self.segments.task_done()
    def close(self):
        self.audio.put(None)
        for t in self.threads:t.join()
        if self.trace:self.trace.emit('capture_complete',dropped_segments=self.dropped,reason=self.error or 'ok')
        return {'processed':self.processed,'dropped_segments':self.dropped,'errors':self.errors,'last_error_type':self.error}

class LocalASR:
    def __init__(self,root,model='tiny.en',allow_download=False):
        from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
        self.engine=FasterWhisperEngine(Path(root)/'models');self.model=model;self.allow_download=allow_download
    def transcribe(self,path):
        from local_meeting_ai.domain.entities import TranscriptionEngineRequest
        req=TranscriptionEngineRequest(audio_path=Path(path),model=self.model,device='cpu',compute_type='int8',language='en',task='transcribe',beam_size=1,vad_filter=False,allow_model_download=self.allow_download,cpu_threads=2,num_workers=1)
        return asyncio.run(self.engine.transcribe(req,lambda *a:None,lambda:False,lambda s:None)).segments
    def close(self):self.engine.shutdown()

def microphones():return [asdict(x) for x in create_audio_capture_backend().list_sources()]

def listen(store,user,meeting,root,source,seconds,consent,model='tiny.en',download=False,trace=None):
    if not consent:raise ValueError('recording requires explicit participant consent (--consent)')
    if not 1<=seconds<=7200:raise ValueError('duration limit 1..7200 seconds')
    store.metadata(user,meeting)
    # Owner-only ingestion is checked before opening a microphone.
    with store.lock:store._check(user,meeting,True)
    storage=MeetingStorage(AppPaths(Path(root)),128*1024*1024)
    asr=LocalASR(root,model,download);backend=create_audio_capture_backend();stream=uuid.uuid4().hex;seq=0;last_end=0
    def transcribe(item):
        nonlocal seq
        for s in asr.transcribe(item['path']):
            seq+=1;yield {'event_id':stream+str(seq),'stream':stream,'seq':seq,'segment_id':stream+str(seq),'revision':1,'start_ms':item['start_ms']+s.start_ms,'end_ms':item['start_ms']+s.end_ms,'text':s.text,'final':True}
    pipe=None
    try:
        pipe=Pipeline(transcribe,lambda e:store.ingest(user,meeting,e),trace)
        backend.start(session_id=meeting,source_id=source,destination=storage.new_live_capture_path(meeting))
        start=time.monotonic();pcm=bytearray();chunk_start=None;rate=channels=0
        def flush():
            nonlocal pcm,chunk_start
            if not pcm:return
            path=storage.new_realtime_chunk_path(meeting)
            with wave.open(str(path),'wb') as f:f.setnchannels(channels);f.setsampwidth(2);f.setframerate(rate);f.writeframes(bytes(pcm))
            if not pipe.submit({'path':path,'start_ms':round(chunk_start*1000/rate),'temporary':True}):path.unlink(missing_ok=True)
            pcm=bytearray();chunk_start=None
        while time.monotonic()-start<seconds:
            time.sleep(.1);batch=backend.drain_frames()
            if batch:
                rate=batch.sample_rate;channels=batch.channels
                if batch.start_frame>last_end:
                    pipe.dropped+=1
                    if trace:trace.emit('capture_gap',dropped_segments=pipe.dropped)
                last_end=batch.end_frame
                if chunk_start is None:chunk_start=batch.start_frame
                pcm.extend(batch.pcm_s16le)
                if len(pcm)>=rate*channels*2*3:flush()
        flush()
    finally:
        backend.shutdown()
        result=pipe.close() if pipe else {'errors':1}
        asr.close()
    return result
