import argparse
import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from .config import Config
from .store import Store
from .trace import Trace

def output(value):print(json.dumps(value,ensure_ascii=False,indent=2,default=str))

def parser():
    p=argparse.ArgumentParser(description='Meeting Copilot: local retained evidence and private cited QA')
    p.add_argument('--config',default='config.json',help='trusted host configuration; relative data_dir is relative to config')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('demo');sub.add_parser('mic-list')
    c=sub.add_parser('create');c.add_argument('meeting');c.add_argument('--title',default='Technical meeting')
    sub.add_parser('list')
    c=sub.add_parser('replay');c.add_argument('meeting');c.add_argument('file')
    c=sub.add_parser('correct');c.add_argument('meeting');c.add_argument('segment');c.add_argument('text');c.add_argument('--revision',type=int,required=True);c.add_argument('--start-ms',type=int,default=0);c.add_argument('--end-ms',type=int,default=0)
    c=sub.add_parser('gaps');c.add_argument('meeting')
    c=sub.add_parser('approve-folder');c.add_argument('meeting');c.add_argument('root')
    for name in ('refresh-folder','revoke-folder'):
        c=sub.add_parser(name);c.add_argument('folder')
    c=sub.add_parser('folders');c.add_argument('meeting')
    for name in ('grant','revoke'):
        c=sub.add_parser(name);c.add_argument('meeting');c.add_argument('principal')
    c=sub.add_parser('snapshot');c.add_argument('meeting');c.add_argument('--previous',action='append',default=[]);c.add_argument('--folder',action='append',default=[])
    c=sub.add_parser('ask');c.add_argument('meeting');c.add_argument('question');c.add_argument('--snapshot');c.add_argument('--previous',action='append',default=[]);c.add_argument('--folder',action='append',default=[]);c.add_argument('--conversation',default='default');c.add_argument('--skill',choices=['catch_up','trace_decision','compare_options','direct'],default='trace_decision')
    c=sub.add_parser('summary');c.add_argument('snapshot')
    c=sub.add_parser('history');c.add_argument('snapshot');c.add_argument('--conversation',default='default')
    c=sub.add_parser('checkpoint');c.add_argument('snapshot');c.add_argument('--conversation',default='default')
    c=sub.add_parser('promote');c.add_argument('meeting');c.add_argument('snapshot');c.add_argument('question');c.add_argument('--conversation',default='default')
    c=sub.add_parser('export');c.add_argument('snapshot');c.add_argument('output');c.add_argument('--conversation',default='default')
    c=sub.add_parser('cleanup');c.add_argument('meeting');c.add_argument('--confirm',required=True,help='must exactly match meeting ID')
    c=sub.add_parser('listen');c.add_argument('meeting');c.add_argument('--source',required=True);c.add_argument('--seconds',type=int,default=30);c.add_argument('--consent',action='store_true');c.add_argument('--download-model',action='store_true')
    c=sub.add_parser('asr');c.add_argument('meeting');c.add_argument('audio');c.add_argument('--download-model',action='store_true')
    c=sub.add_parser('mcp');c.add_argument('snapshot')
    c=sub.add_parser('trace-export');c.add_argument('output')
    c=sub.add_parser('evaluate');c.add_argument('--output',default='results/report.json');c.add_argument('--stress',action='store_true')
    return p

def main():
    args=parser().parse_args()
    try:run(args)
    except KeyboardInterrupt:print('Stopped.',file=sys.stderr);raise SystemExit(130)
    except Exception as e:
        # Do not print provider errors, requests, environment values or tracebacks.
        from .provider import ProviderError
        print(json.dumps({'error':e.code if isinstance(e,ProviderError) else type(e).__name__,'detail':str(e) if isinstance(e,(ValueError,PermissionError)) else 'Operation failed; inspect configuration or local capability.'}),file=sys.stderr)
        raise SystemExit(1)

def run(a):
    config_path=Path(a.config)
    if a.command=='init':
        if config_path.exists():raise ValueError('config already exists')
        config_path.write_text(json.dumps({k:v for k,v in vars(Config()).items() if not k.startswith('_')},indent=2),encoding='utf-8');output({'config':str(config_path),'provider':'demo / SIMULATED'});return
    c=Config.load(config_path);root=c.root;root.mkdir(parents=True,exist_ok=True)
    for key,rel in [('HF_HOME','cache/huggingface'),('XDG_CACHE_HOME','cache'),('LITELLM_CACHE_DIR','cache/litellm'),('TIKTOKEN_CACHE_DIR','cache/tiktoken')]:os.environ[key]=str(root/rel)
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
    from .assistant import Assistant,Workers,LiveSummaries
    from .provider import Provider
    store=Store(root/'evidence.db');trace=Trace(root/'traces.jsonl');assistant=Assistant(store,c.principal,Provider(c),trace);user=c.principal
    try:
        if a.command=='create':store.create(user,a.meeting,a.title);output({'meeting':a.meeting})
        elif a.command=='list':output(store.list_meetings(user))
        elif a.command=='demo':
            from .replay import seed
            if c.provider!='demo':raise ValueError('demo requires provider=demo; use ask for real API')
            folder=seed(store,user,root);sid=store.snapshot(user,'demo',folders=[folder]);result=asyncio.run(assistant.ask(sid,'Compare SQLite and Kafka deployment','demo','compare_options'))
            asyncio.run(assistant.summary_graph.ainvoke({'snapshot':sid}))
            path=root/'demo-export.json';path.write_text(json.dumps(store.export(user,sid,'demo'),indent=2),encoding='utf-8');output({'notice':'SIMULATED transcript; deterministic extractive QA; no microphone, ASR or paid API used.','folder_id':folder,'result':result,'export':str(path)})
        elif a.command=='replay':
            from .replay import import_jsonl
            output({'events':import_jsonl(store,user,a.meeting,a.file),'gaps':store.gaps(user,a.meeting)})
        elif a.command=='correct':
            n=uuid.uuid4().hex
            output({'source_id':store.ingest(user,a.meeting,{'event_id':n,'stream':n,'seq':1,'segment_id':a.segment,'revision':a.revision,'start_ms':a.start_ms,'end_ms':a.end_ms,'text':a.text,'final':True})})
        elif a.command=='gaps':output(store.gaps(user,a.meeting))
        elif a.command=='approve-folder':output({'folder_id':store.approve(user,a.meeting,a.root),'next':'refresh-folder FOLDER_ID'})
        elif a.command=='refresh-folder':output(store.refresh(user,a.folder))
        elif a.command=='revoke-folder':store.revoke_folder(user,a.folder);output({'revoked':a.folder})
        elif a.command=='folders':output(store.folders(user,a.meeting))
        elif a.command in ('grant','revoke'):store.grant(user,a.meeting,a.principal,a.command=='grant');output({'policy':'updated'})
        elif a.command=='snapshot':output({'snapshot_id':store.snapshot(user,a.meeting,previous=a.previous,folders=a.folder)})
        elif a.command=='ask':
            sid=a.snapshot or store.snapshot(user,a.meeting,previous=a.previous,folders=a.folder)
            if store.snapshot_info(user,sid)['meeting']!=a.meeting:raise ValueError('snapshot meeting mismatch')
            output(asyncio.run(assistant.ask(sid,a.question,a.conversation,a.skill)))
        elif a.command=='summary':output(asyncio.run(assistant.summary_graph.ainvoke({'snapshot':a.snapshot}))['result'])
        elif a.command=='history':output(store.private_get(user,a.snapshot,a.conversation))
        elif a.command=='checkpoint':output(store.checkpoint_get(user,a.snapshot,a.conversation))
        elif a.command=='promote':output({'source_id':store.promote(user,a.snapshot,a.conversation,a.question,a.meeting)})
        elif a.command=='export':Path(a.output).write_text(json.dumps(store.export(user,a.snapshot,a.conversation),indent=2),encoding='utf-8');output({'export':a.output})
        elif a.command=='cleanup':
            if a.confirm!=a.meeting:raise ValueError('confirmation mismatch')
            store.cleanup(user,a.meeting)
            from local_meeting_ai.infrastructure.storage import MeetingStorage
            from local_meeting_ai.paths import AppPaths
            MeetingStorage(AppPaths(root),0).delete_meeting(a.meeting)
            output({'deleted':a.meeting,'note':'logical DB deletion and meeting media cleanup; existing exports/traces/backups are separate retained artifacts, no secure erasure guarantee'})
        elif a.command=='mic-list':
            from .audio import microphones
            output(microphones())
        elif a.command=='listen':
            from .audio import listen
            workers=Workers(assistant);summaries=LiveSummaries(workers,a.meeting)
            try:
                result=listen(store,user,a.meeting,root,a.source,a.seconds,a.consent,c.asr_model,a.download_model,trace)
            finally:summaries.close();workers.close()
            result['background_summary_snapshot']=summaries.last_snapshot
            result['background_summary_failures']=summaries.failures
            output(result)
        elif a.command=='asr':
            from .audio import LocalASR
            with store.lock:store._check(user,a.meeting,True)
            asr=LocalASR(root,c.asr_model,a.download_model);stream=uuid.uuid4().hex
            try:
                rows=asr.transcribe(a.audio)
                for i,s in enumerate(rows,1):store.ingest(user,a.meeting,{'event_id':stream+str(i),'stream':stream,'seq':i,'segment_id':stream+str(i),'revision':1,'start_ms':s.start_ms,'end_ms':s.end_ms,'text':s.text,'final':True})
                output({'local_asr':True,'segments':len(rows),'model':c.asr_model,'text':' '.join(s.text for s in rows)})
            finally:asr.close()
        elif a.command=='mcp':
            from .mcp_server import server
            from .service import EvidenceService
            service=EvidenceService(store,user,a.snapshot,trace);service.info();server(service).run(transport='stdio')
        elif a.command=='trace-export':output({'redacted_events':trace.redacted_export(a.output),'format':'local NDJSON; external Langfuse upload/hosting not configured'})
        elif a.command=='evaluate':
            from .evaluation import evaluate
            output(evaluate(Path(a.output),root/'evaluation',stress=a.stress))
    finally:store.close()

if __name__=='__main__':main()
