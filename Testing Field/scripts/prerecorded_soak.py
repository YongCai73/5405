"""Wall-clock prerecorded replay through actual local ASR plus slow simulated QA.

No microphone or paid model is used. Requires the audio extra and cached ASR model.
"""
import argparse
import asyncio
import hashlib
import json
import math
import platform
import resource
import time
import wave
from pathlib import Path
import numpy as np
from faster_whisper.audio import decode_audio
from meeting_copilot.audio import LocalASR, Pipeline
from meeting_copilot.assistant import Assistant, Workers, LiveSummaries
from meeting_copilot.config import Config
from meeting_copilot.provider import Provider, Completion
from meeting_copilot.store import Store
from meeting_copilot.trace import Trace


class SlowFakeProvider(Provider):
    def __init__(self):
        super().__init__(Config(provider='openai',model='SIMULATED-slow-provider'))
    async def complete(self,question,evidence,contract):
        await asyncio.sleep(5)
        claims=[{'source_id':e['id'],'quote':e['text'][:1200]} for e in evidence[-2:] if len(e['text'])>=3]
        return Completion(json.dumps({'claims':claims,'inferences':[]}),0,0,0.0)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio',type=Path,required=True);p.add_argument('--model-root',type=Path,required=True)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=1200)
    a=p.parse_args()
    if not 3<=a.seconds<=7200:raise ValueError('duration must be 3..7200 seconds')
    a.work.mkdir(parents=True,exist_ok=False)
    audio=decode_audio(str(a.audio),sampling_rate=16000)
    source_hashes={str(f.relative_to(Path(__file__).resolve().parents[1])):hashlib.sha256(f.read_bytes()).hexdigest()
                   for f in (Path(__file__).resolve().parents[1]/'src/meeting_copilot').glob('*.py')}
    store=Store(a.work/'runtime/evidence.db');store.create('soak','live','PRERECORDED REPLAY / SYNTHETIC QA')
    asr=LocalASR(a.model_root,'tiny.en',False)
    assistant=Assistant(store,'soak',SlowFakeProvider(),Trace(a.work/'runtime/traces.jsonl'))
    workers=Workers(assistant);summaries=LiveSummaries(workers,'live',interval=15)
    sequence=0;qa=[];qa_submitted=0;qa_failures=[];qa_cutoff_violations=0;qa_latencies=[];submitted=0
    start=time.monotonic();utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    def transcribe(item):
        nonlocal sequence
        for segment in asr.transcribe(item['path']):
            if not segment.text.strip():continue
            sequence+=1
            yield {'event_id':f'chunk-{sequence}','stream':'recorded-replay','seq':sequence,
                   'segment_id':f's-{sequence}','revision':1,'final':True,
                   'start_ms':item['start_ms']+segment.start_ms,'end_ms':item['start_ms']+segment.end_ms,'text':segment.text}
    pipe=Pipeline(transcribe,lambda event:store.ingest('soak','live',event),assistant.trace)
    next_chunk=3.;next_qa=10.;next_status=5.;sample_cursor=0;completed=False
    def collect():
        nonlocal qa_cutoff_violations
        for sid,future,asked in list(qa):
            if not future.done():continue
            qa.remove((sid,future,asked))
            try:
                result=future.result();qa_latencies.append(time.monotonic()-asked)
                watermark=store.snapshot_info('soak',sid)['watermark']
                qa_cutoff_violations+=sum(c['source_id']>watermark for c in result['claims'])
            except Exception as exc:qa_failures.append(type(exc).__name__)
    try:
        while time.monotonic()-start<a.seconds:
            elapsed=time.monotonic()-start
            if elapsed>=next_chunk:
                count=48000;indices=(np.arange(count)+sample_cursor)%len(audio);sample_cursor+=count
                pcm=(np.clip(audio[indices],-1,1)*32767).astype('<i2').tobytes()
                path=a.work/f'chunk-{submitted}.wav'
                with wave.open(str(path),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(pcm)
                if not pipe.submit({'path':path,'start_ms':submitted*3000,'temporary':True}):path.unlink(missing_ok=True)
                submitted+=1;next_chunk+=3
            if elapsed>=next_qa:
                try:
                    sid,future=workers.submit('live','What did the Americans say about the country?',conversation='private-soak')
                    qa.append((sid,future,time.monotonic()));qa_submitted+=1
                except Exception as exc:qa_failures.append(type(exc).__name__)
                next_qa+=10
            collect()
            if elapsed>=next_status:
                progress={'elapsed_seconds':elapsed,'chunks_submitted':submitted,'summary_completed':summaries.completed,
                          'summary_failures':summaries.failures,'qa_completed':len(qa_latencies),**pipe.stats()}
                (a.work/'progress.json').write_text(json.dumps(progress,indent=2))
                print(json.dumps(progress),flush=True);next_status+=30
            time.sleep(.05)
        completed=True
    finally:
        pipeline=pipe.close();summaries.close();workers.close();collect();asr.close()
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if platform.system()=='Darwin' else 1024)
        result={'mode':'ACTUAL LOCAL ASR / wall-clock prerecorded repeated audio; SIMULATED 5-second provider QA and summaries',
            'started_at_utc':utc,'completed':completed,'requested_wall_seconds':a.seconds,'actual_wall_seconds':time.monotonic()-start,
            'audio_fixture_sha256':hashlib.sha256(a.audio.read_bytes()).hexdigest(),'fixture_audio_seconds':len(audio)/16000,
            'chunks_submitted':submitted,'chunk_audio_seconds':3,'replayed_audio_seconds':submitted*3,
            'asr':{'model':'tiny.en','device':'cpu','compute_type':'int8','cpu_threads':2,'vad_filter':False},
            'pipeline':pipeline,'summary_completed':summaries.completed,'summary_failures':summaries.failures,
            'summary_last_error':summaries.last_error,'qa_submitted':qa_submitted,'qa_completed':len(qa_latencies),
            'qa_errors':qa_failures,'qa_future_evidence_violations':qa_cutoff_violations,
            'qa_p95_seconds':sorted(qa_latencies)[max(0,math.ceil(len(qa_latencies)*.95)-1)] if qa_latencies else None,
            'peak_rss_bytes':rss,'platform':platform.platform(),'source_sha256':source_hashes,
            'limitations':'Repeated public JFK recording, not microphone or technical discussion; no WER/technical-term or live LLM quality measurement; lag measured from chunk submission, excludes 3-second capture accumulation.'}
        store.close();a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2))
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
