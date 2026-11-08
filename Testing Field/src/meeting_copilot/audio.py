"""Upstream capture/ASR with independent bounded chunk and ingestion queues."""
import asyncio
import queue
import threading
import time
import uuid
import wave
from collections import deque
from dataclasses import asdict
from pathlib import Path
from local_meeting_ai.adapters.audio_capture.factory import create_audio_capture_backend
from local_meeting_ai.infrastructure.storage import MeetingStorage
from local_meeting_ai.paths import AppPaths

class Pipeline:
    def __init__(self,transcribe,ingest,trace=None,capacity=4):
        self.transcribe=transcribe;self.ingest=ingest;self.trace=trace
        self.audio=queue.Queue(capacity);self.segments=queue.Queue(32);self.dropped=0;self.errors=0;self.processed=0;self.stop_event=threading.Event();self.error=None
        self.started=time.monotonic();self.peak_audio=0;self.peak_segments=0;self.lags=deque(maxlen=10000);self.closed=False
        self.threads=[threading.Thread(target=self._asr,name='local-asr'),threading.Thread(target=self._ingest,name='evidence-ingest')]
        for t in self.threads:t.start()
    def submit(self,item):
        if self.closed:raise RuntimeError('pipeline closed')
        try:
            self.audio.put_nowait((item,time.monotonic()));self.peak_audio=max(self.peak_audio,self.audio.qsize());return True
        except queue.Full:
            self.dropped+=1
            if self.trace:self.trace.emit('audio_drop',dropped_segments=self.dropped,queue_depth=self.audio.qsize())
            return False
    def _asr(self):
        while True:
            packet=self.audio.get()
            try:
                if packet is None:break
                item,submitted=packet
                try:
                    for segment in self.transcribe(item):
                        try:
                            self.segments.put((segment,submitted),timeout=2);self.peak_segments=max(self.peak_segments,self.segments.qsize())
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
                try:
                    segment,submitted=item
                    self.ingest(segment);self.processed+=1;self.lags.append(time.monotonic()-submitted)
                except Exception as e:self.errors+=1;self.error=type(e).__name__
            finally:self.segments.task_done()
    def close(self):
        if self.closed:return self.stats()
        self.closed=True
        self.audio.put(None)
        for t in self.threads:t.join()
        if self.trace:self.trace.emit('capture_complete',dropped_segments=self.dropped,reason=self.error or 'ok')
        return self.stats()
    def stats(self):
        lags=sorted(self.lags)
        return {'processed':self.processed,'dropped_segments':self.dropped,'errors':self.errors,'last_error_type':self.error,
                'audio_queue_depth':self.audio.qsize(),'segment_queue_depth':self.segments.qsize(),'peak_audio_queue':self.peak_audio,
                'peak_segment_queue':self.peak_segments,'audio_queue_capacity':self.audio.maxsize,'segment_queue_capacity':self.segments.maxsize,
                'ingestion_lag_p95_seconds':lags[max(0,int(len(lags)*.95)-1)] if lags else None,
                'ingestion_lag_max_seconds':max(lags) if lags else None,'wall_seconds':time.monotonic()-self.started,
                'lag_definition':'chunk submission to committed segment; last 10000 segments; excludes chunk accumulation'}

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

def listen(store,user,meeting,root,source,seconds,consent,model='tiny.en',download=False,trace=None,on_status=None,status_interval=5):
    if not consent:raise ValueError('recording requires explicit participant consent (--consent)')
    if not 0<=seconds<=28800:raise ValueError('duration limit 0 (until Ctrl+C) or 1..28800 seconds')
    with store.lock:store._check(user,meeting,True)
    storage=MeetingStorage(AppPaths(Path(root)),128*1024*1024)
    backend=create_audio_capture_backend()
    asr=None;pipe=None;stream=uuid.uuid4().hex;seq=0;last_end=0
    pcm=bytearray();chunk_start=None;rate=channels=0;interrupted=False;started=False
    def transcribe(item):
        nonlocal seq
        for s in asr.transcribe(item['path']):
            seq+=1;yield {'event_id':stream+str(seq),'stream':stream,'seq':seq,'segment_id':stream+str(seq),'revision':1,'start_ms':item['start_ms']+s.start_ms,'end_ms':item['start_ms']+s.end_ms,'text':s.text,'final':True}
    def flush():
        nonlocal pcm,chunk_start
        if not pcm:return
        path=storage.new_realtime_chunk_path(meeting)
        with wave.open(str(path),'wb') as f:
            f.setnchannels(channels);f.setsampwidth(2);f.setframerate(rate);f.writeframes(bytes(pcm))
        if not pipe.submit({'path':path,'start_ms':round(chunk_start*1000/rate),'temporary':True}):path.unlink(missing_ok=True)
        pcm=bytearray();chunk_start=None
    def drain():
        nonlocal rate,channels,last_end,chunk_start
        batch=backend.drain_frames()
        if batch:
            if pcm and (batch.sample_rate!=rate or batch.channels!=channels or batch.start_frame!=last_end):flush()
            rate=batch.sample_rate;channels=batch.channels
            if batch.start_frame>last_end:
                pipe.dropped+=1
                if trace:trace.emit('capture_gap',dropped_segments=pipe.dropped)
            last_end=batch.end_frame
            if chunk_start is None:chunk_start=batch.start_frame
            pcm.extend(batch.pcm_s16le)
            if len(pcm)>=rate*channels*2*3:flush()
    try:
        asr=LocalASR(root,model,download)
        pipe=Pipeline(transcribe,lambda e:store.ingest(user,meeting,e),trace)
        backend.start(session_id=meeting,source_id=source,destination=storage.new_live_capture_path(meeting));started=True
        start=time.monotonic();last_status=start
        if on_status:on_status({'event':'recording_started','source':source,'meeting':meeting})
        try:
            while not seconds or time.monotonic()-start<seconds:
                time.sleep(.1);drain()
                if on_status and time.monotonic()-last_status>=status_interval:
                    on_status({'event':'recording_status',**pipe.stats()});last_status=time.monotonic()
        except KeyboardInterrupt:
            interrupted=True
        finally:
            # Stop callbacks first, then ingest the final partial chunk as well.
            backend.stop();started=False
            drain();flush()
    finally:
        try:backend.shutdown()
        finally:
            try:result=pipe.close() if pipe else {'errors':1}
            finally:
                if asr:asr.close()
    return {**result,'interrupted':interrupted,'source':source,'mode':'microphone / local ASR'}
