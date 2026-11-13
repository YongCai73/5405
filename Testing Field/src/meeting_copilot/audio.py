"""Upstream capture/ASR with independent bounded chunk and ingestion queues."""
import asyncio
import os
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
        req=TranscriptionEngineRequest(audio_path=Path(path),model=self.model,device='cpu',compute_type='int8',language='en',task='transcribe',beam_size=1,vad_filter=True,allow_model_download=self.allow_download,cpu_threads=_env_number('MC_ASR_THREADS',2,1,16,int),num_workers=1)
        return asyncio.run(self.engine.transcribe(req,lambda *a:None,lambda:False,lambda s:None)).segments
    def close(self):self.engine.shutdown()

def _env_number(name,default,low,high,kind=float):
    """Experiment knob read from an environment variable, e.g. MC_CHUNK_SECONDS=5."""
    raw=os.environ.get(name)
    if raw is None or not raw.strip():return default
    value=kind(raw)
    if not low<=value<=high:raise ValueError(f'{name} must be between {low} and {high}')
    return value

class PauseCutter:
    """Ends a chunk at a pause in speech instead of a fixed length.

    Audio is checked in 20 ms frames. A frame counts as speech when its loudness (RMS)
    is above max(speech_rms, 3 x background noise). Once speech has been heard, a pause of
    pause_ms ends the chunk, as long as the chunk is at least min_seconds long.

    Background noise is tracked as a running minimum: it drops quickly to quiet frames and
    creeps up slowly otherwise, so a noisy room still gets a sensible threshold.
    """
    def __init__(self,pause_ms=500,min_seconds=1.0,speech_rms=100.0):
        self.pause_ms=pause_ms;self.min_ms=min_seconds*1000;self.speech_rms=speech_rms
        self.noise=None;self.frames=0;self.speech_frames=0;self.reset()
    def reset(self):
        self.speech=False;self.silence_ms=0.0;self.chunk_ms=0.0
    def diagnostics(self):
        return {'noise_rms':round(self.noise or 0,1),'speech_threshold_rms':round(max(self.speech_rms,3*(self.noise or 0)),1),
                'speech_frame_ratio':round(self.speech_frames/self.frames,2) if self.frames else None}
    def update(self,pcm,rate,channels):
        """Feed new 16-bit audio; returns True when the chunk should be sent now."""
        import numpy as np
        samples=np.frombuffer(bytes(pcm),dtype='<i2').astype(np.float32)
        step=max(1,int(rate*channels*.02))
        for i in range(0,len(samples),step):
            frame=samples[i:i+step]
            rms=float(np.sqrt(np.mean(frame*frame)))
            if self.noise is None:self.noise=rms
            elif rms<self.noise:self.noise=.7*self.noise+.3*rms
            else:self.noise=self.noise*1.001+.01
            self.chunk_ms+=len(frame)*1000/(rate*channels);self.frames+=1
            if rms>=max(self.speech_rms,3*self.noise):
                self.speech=True;self.silence_ms=0.0;self.speech_frames+=1
            else:
                self.silence_ms+=len(frame)*1000/(rate*channels)
        return self.speech and self.silence_ms>=self.pause_ms and self.chunk_ms>=self.min_ms

def _latency_summary(values):
    """Speech end -> segment committed (seconds): mean, p50, p95, max."""
    v=sorted(values)
    if not v:return {'speech_to_ready_mean_s':None,'speech_to_ready_p50_s':None,'speech_to_ready_p95_s':None,'speech_to_ready_max_s':None,'speech_to_ready_count':0}
    pick=lambda q:round(v[min(len(v)-1,int(len(v)*q))],2)
    return {'speech_to_ready_mean_s':round(sum(v)/len(v),2),'speech_to_ready_p50_s':pick(.5),'speech_to_ready_p95_s':pick(.95),'speech_to_ready_max_s':round(v[-1],2),'speech_to_ready_count':len(v)}

def microphones():return [asdict(x) for x in create_audio_capture_backend().list_sources()]

def listen(store,user,meeting,root,source,seconds,consent,model='tiny.en',download=False,trace=None,on_status=None,status_interval=5):
    if not consent:raise ValueError('recording requires explicit participant consent (--consent)')
    if not 0<=seconds<=28800:raise ValueError('duration limit 0 (until Ctrl+C) or 1..28800 seconds')
    with store.lock:store._check(user,meeting,True)
    storage=MeetingStorage(AppPaths(Path(root)),128*1024*1024)
    backend=create_audio_capture_backend()
    asr=None;pipe=None;stream=uuid.uuid4().hex;seq=0;last_end=0
    pcm=bytearray();chunk_start=None;rate=channels=0;interrupted=False;started=False
    chunk_seconds=_env_number('MC_CHUNK_SECONDS',10,1,30)
    # 'pause' = cut at pauses (chunk_seconds is then the maximum); 'fixed' = cut every chunk_seconds.
    cut_mode=os.environ.get('MC_CUT_MODE','pause').strip() or 'pause'
    if cut_mode not in ('pause','fixed'):raise ValueError("MC_CUT_MODE must be 'pause' or 'fixed'")
    cutter=PauseCutter(_env_number('MC_PAUSE_MS',500,100,3000),1.0,_env_number('MC_SPEECH_RMS',100,1,30000))
    silent_skipped=0;cuts={'pause_cuts':0,'max_length_cuts':0}
    def cut_stats():
        # captured_audio_seconds should track elapsed recording time; a gap means clock drift.
        audio_s=round(last_end/rate,2) if rate else None
        wall_s=round((capture_stop or time.monotonic())-capture_t0,2) if capture_t0 is not None else None
        return {'silent_chunks_skipped':silent_skipped,**cuts,**cutter.diagnostics(),'captured_audio_seconds':audio_s,'capture_wall_seconds':wall_s,
                'capture_missing_seconds':round(wall_s-audio_s,2) if audio_s is not None and wall_s is not None else None,
                'input_overflows':getattr(backend,'input_overflows',getattr(getattr(backend,'native',None),'input_overflows',None))}
    # capture_t0 = clock time when recording began; segment end_ms is relative to it.
    capture_t0=None;capture_stop=None;speech_lags=deque(maxlen=10000);lag_lock=threading.Lock()
    def ingest(e):
        store.ingest(user,meeting,e)
        if capture_t0 is not None:
            with lag_lock:speech_lags.append(time.monotonic()-(capture_t0+e['end_ms']/1000))
    def latency():
        with lag_lock:values=list(speech_lags)
        return _latency_summary(values)
    def transcribe(item):
        nonlocal seq
        for s in asr.transcribe(item['path']):
            seq+=1;yield {'event_id':stream+str(seq),'stream':stream,'seq':seq,'segment_id':stream+str(seq),'revision':1,'start_ms':item['start_ms']+s.start_ms,'end_ms':item['start_ms']+s.end_ms,'text':s.text,'final':True}
    def flush(final=False):
        nonlocal pcm,chunk_start,silent_skipped
        if not pcm:return
        heard_speech=cutter.speech;cutter.reset()
        if cut_mode=='pause' and not heard_speech and not final:
            # Only background noise in this chunk: skip ASR (saves time, avoids hallucinations).
            silent_skipped+=1;pcm=bytearray();chunk_start=None;return
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
            pause=cutter.update(batch.pcm_s16le,rate,channels)
            if cut_mode=='pause' and pause:cuts['pause_cuts']+=1;flush()
            elif len(pcm)>=rate*channels*2*chunk_seconds:cuts['max_length_cuts']+=1;flush()
    try:
        asr=LocalASR(root,model,download)
        pipe=Pipeline(transcribe,ingest,trace)
        backend.start(session_id=meeting,source_id=source,destination=storage.new_live_capture_path(meeting));started=True
        start=time.monotonic();last_status=start;capture_t0=start
        if on_status:on_status({'event':'recording_started','source':source,'meeting':meeting,'chunk_seconds':chunk_seconds,'cut_mode':cut_mode})
        try:
            while not seconds or time.monotonic()-start<seconds:
                time.sleep(.1);drain()
                if on_status and time.monotonic()-last_status>=status_interval:
                    on_status({'event':'recording_status',**pipe.stats(),**latency(),**cut_stats()});last_status=time.monotonic()
        except KeyboardInterrupt:
            interrupted=True
        finally:
            # Stop callbacks first, then ingest the final partial chunk as well.
            backend.stop();started=False;capture_stop=time.monotonic()
            drain();flush(final=True)
    finally:
        try:backend.shutdown()
        finally:
            try:result=pipe.close() if pipe else {'errors':1}
            finally:
                if asr:asr.close()
    return {**result,**latency(),'chunk_seconds':chunk_seconds,'cut_mode':cut_mode,**cut_stats(),'interrupted':interrupted,'source':source,'mode':'microphone / local ASR'}
