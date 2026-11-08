"""Isolated native LiveAssistantService replay harness, never a production QA path.

The upstream service, repository and SQL migration are unmodified. A small
SummaryEngine bridge shares our configured transport; desktop GUI, native RAG,
keyring and local LLM engine are deliberately not instantiated.
"""
import asyncio
import hashlib
import json
import platform
import time
import uuid
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from local_meeting_ai.application.live_assistant import LiveAssistantService
from local_meeting_ai.domain.entities import SegmentDraft, SummaryResult
from local_meeting_ai.domain.meeting_text import evidence_system_message
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.live_assistant import LiveAssistantRepository
from .provider import Provider
from .replay import fixture, events


class NoCredentialStore:
    def get(self):return None
    def status(self):return {'available':False,'configured':False}


class NativeEngineBridge:
    def __init__(self,provider):self.provider=provider;self.calls=[]
    def capability(self):return {'available':True,'worker':{'active_requests':0}}
    async def prepare(self,*args,**kwargs):pass
    def unload(self):pass
    def shutdown(self):pass
    async def summarize(self,context,config,progress,is_cancelled):
        if is_cancelled():raise asyncio.CancelledError()
        messages=[{'role':'system','content':config['system_prompt']+'\n'+evidence_system_message(context)},
                  {'role':'user','content':config['prompt_question']}]
        start=time.perf_counter();reply=await self.provider.chat(messages)
        if is_cancelled():raise asyncio.CancelledError()
        self.calls.append({'input_tokens':reply.input_tokens,'output_tokens':reply.output_tokens,
                           'latency_ms':(time.perf_counter()-start)*1000})
        return SummaryResult(reply.content,reply.input_tokens,reply.output_tokens)


async def run_native(config,work,output,provider=None):
    if config.provider=='demo':raise ValueError('native-baseline requires configured API mode and send_evidence=true; no demo scores are fabricated')
    if not config.send_evidence:raise ValueError('native-baseline requires explicit send_evidence=true')
    work=Path(work)/('native-'+uuid.uuid4().hex);work.mkdir(parents=True)
    db=Database(work/'native.db')
    with db.transaction() as connection:
        connection.executescript('CREATE TABLE meetings(id INTEGER PRIMARY KEY); CREATE TABLE transcriptions(id INTEGER PRIMARY KEY); INSERT INTO meetings VALUES(1); INSERT INTO transcriptions VALUES(1);')
        connection.executescript(files('local_meeting_ai.infrastructure.database').joinpath('sql/010_live_assistant.sql').read_text())
    preferences=SimpleNamespace(get_all=lambda:{'live_assistant':{
        'enabled':True,'auto_start':True,'provider':config.provider,'model':config.model,
        'behavior_mode':'questions','streaming':config.streaming,'recent_context_seconds':180,
        'max_output_tokens':config.max_tokens,'request_timeout_seconds':config.timeout_seconds}})
    engine=NativeEngineBridge(provider or Provider(config))
    service=LiveAssistantService(engine=engine,repository=LiveAssistantRepository(db),
                                 preferences=preferences,credentials=NoCredentialStore())
    cases=[];elapsed=time.perf_counter();session=SimpleNamespace(session_id='native-fixture',meeting_id=1,transcription_id=1,title='FROZEN SYNTHETIC REPLAY')
    try:
        service.session_started(session)
        for event in events():
            service.publish_segments(session_id=session.session_id,meeting_id=1,transcription_id=1,
                meeting_title=session.title,sequence=event['seq'],segments=[SegmentDraft(event['seq'],event['start_ms'],event['end_ms'],event['text'])])
            for q in fixture()['questions']:
                if q['at']!=event['seq']:continue
                start=time.perf_counter()
                result=await service.ask(1,q['question'])
                text=result['answer']['text']
                cases.append({'id':q['id'],'at_event':q['at'],'question':q['question'],'answer':text,
                    'related_segment_ids':result['answer']['related_segment_ids'],
                    'latency_ms':(time.perf_counter()-start)*1000,
                    'input_tokens':result['answer']['prompt_tokens'],'output_tokens':result['answer']['completion_tokens'],
                    'folder_task':bool(q.get('folder')),'expected_terms_present':all(t.lower() in text.lower() for t in q['expected']),
                    'semantic_correctness':'requires human review','citation_support':'not automatically scored: native output has no exact quotation contract'})
    finally:
        await service.shutdown()
    usage={key:sum(c[key] for c in engine.calls) if all(c[key] is not None for c in engine.calls) else None for key in ('input_tokens','output_tokens')}
    cost=None
    if all(v is not None for v in usage.values()) and config.input_cost_per_million is not None and config.output_cost_per_million is not None:
        cost=(usage['input_tokens']*config.input_cost_per_million+usage['output_tokens']*config.output_cost_per_million)/1e6
    report={'mode':'native upstream LiveAssistantService replay harness; endpoint quality depends on configured provider',
        'provider':config.provider,'model':config.model,'streaming':config.streaming,
        'fixture_sha256':hashlib.sha256(files('meeting_copilot').joinpath('fixtures.json').read_bytes()).hexdigest(),
        'upstream_commit':'b75077e588f5623d48d9c9bac05aa428b4257c52','native_source_sha256':hashlib.sha256(files('local_meeting_ai.application').joinpath('live_assistant.py').read_bytes()).hexdigest(),
        'platform':platform.platform(),'wall_seconds':time.perf_counter()-elapsed,'cases':cases,
        'api_usage':{**usage,'estimated_cost_usd':cost},
        'adaptations':['shared configurable LiteLLM transport via SummaryEngine bridge','minimal synthetic meeting/transcription parent rows; native repository and migration','no OS keyring access; app API-key environment only'],
        'limitations':['Synthetic transcript replay, not an audio or microphone comparison','Native desktop RAG, capture and native LLM engine not exercised','Folder tasks unsupported in this native path; record separately','Expected-term matching is not human answer correctness or semantic citation support','Do not describe fake HTTP contract runs as live paid/native quality evaluation']}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return {'report':str(output),'questions':len(cases),'mode':report['mode']}
