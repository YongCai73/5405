from __future__ import annotations
import asyncio
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from .provider import Provider, validated_claims
from .store import tokens, Denied

STOP=set('a an the is are was were we why what which how when who did do does for from to of in on and or it its this that our have has can about please tell me with use using'.split())

def terms(q):return set(tokens(q))-STOP

def skill_contract(skill):
    if skill=='direct':return {'name':'direct','retrieval':'question keywords','output_fields':['evidence'],'abstention':'insufficient evidence'}
    if skill not in ('catch_up','trace_decision','compare_options'):raise ValueError('unknown skill')
    return json.loads(files('meeting_copilot').joinpath('skills',skill+'.json').read_text(encoding='utf-8'))

def citation(e,quote):
    result={'source_id':e['id'],'kind':e['kind'],'meeting':e['meeting'],'revision':e['revision'],'sha256':e['hash']}
    if e['kind']=='file':
        start=e['text'].index(quote);result.update(path=e['path'],line_start=e['text'][:start].count('\n')+1,line_end=e['text'][:start+len(quote)].count('\n')+1,folder=e['folder'])
    else:result.update(segment=e['logical'],start_ms=e['start_ms'],end_ms=e['end_ms'])
    return result

class State(TypedDict,total=False):
    snapshot: str
    question: str
    skill: str
    evidence: list
    result: dict

class Assistant:
    def __init__(self,store,user,provider,trace):
        os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
        self.store=store;self.user=user;self.provider=provider;self.trace=trace
        graph=StateGraph(State);graph.add_node('retrieve',self.retrieve);graph.add_node('answer',self.answer)
        graph.add_edge(START,'retrieve');graph.add_edge('retrieve','answer');graph.add_edge('answer',END);self.graph=graph.compile()
        summary=StateGraph(State);summary.add_node('summarize',self.summarize);summary.add_edge(START,'summarize');summary.add_edge('summarize',END);self.summary_graph=summary.compile()

    async def retrieve(self,state):
        skill_contract(state['skill'])
        rows=self.store.evidence(self.user,state['snapshot'],query=state['question'],limit=100)
        if state['skill']=='catch_up':
            rows=self.store.evidence(self.user,state['snapshot'],kind='segment',limit=8,recent=True)
        q=terms(state['question'])
        rows.sort(key=lambda e:len(q&terms(e['text'])),reverse=True)
        selected=[];size=0
        for e in rows:
            if state['skill']!='catch_up' and not q&terms(e['text']):continue
            if size+len(e['text'])>24000:continue
            selected.append(e);size+=len(e['text'])
            if len(selected)>=16:break
        self.trace.emit('retrieval',snapshot_id=state['snapshot'],source_ids=[e['id'] for e in selected],policy='allow')
        return {'evidence':selected}

    async def answer(self,state):
        start=time.perf_counter();evidence=state['evidence'];usage={'input_tokens':0,'output_tokens':0,'estimated_cost_usd':0.0}
        simulated=self.provider.config.provider=='demo'
        if simulated:
            q=terms(state['question']);claims=[]
            for e in evidence:
                sentences=re.split(r'(?<=[.!?])\s+|\n',e['text'])
                best=max(sentences,key=lambda s:len(q&terms(s)),default='')
                if best and (state['skill']=='catch_up' or q&terms(best)):
                    claims.append({'source_id':e['id'],'quote':best[:1200]})
            claims=claims[:8]
        else:
            reply=await self.provider.complete(state['question'],[{'id':e['id'],'text':e['text'],'kind':e['kind']} for e in evidence],skill_contract(state['skill']))
            claims=validated_claims(reply.content,evidence)
            usage={k:getattr(reply,k) for k in usage}
        # Recheck authorization after a slow provider returns; revocation invalidates in-flight reads.
        self.store.snapshot_info(self.user,state['snapshot'])
        by_id={e['id']:e for e in evidence}
        cites=[citation(by_id[c['source_id']],c['quote']) for c in claims]
        text='\n'.join(f"{c['quote']} [E{c['source_id']}]" for c in claims) if claims else 'Insufficient evidence in this authorized snapshot.'
        result={'snapshot_id':state['snapshot'],'mode':'SIMULATED / deterministic extractive' if simulated else 'API / validated extractive quotations','skill':state['skill'],'contract':skill_contract(state['skill']),'text':text,'claims':claims,'citations':cites,'abstained':not bool(claims),'usage':usage,'latency_ms':round((time.perf_counter()-start)*1000,3)}
        if state['skill']=='catch_up':
            result['fields']={'recent_evidence':claims,'open_questions':[c for c in claims if c['quote'].endswith('?')]}
        elif state['skill']=='trace_decision':
            result['fields']={'decision_evidence':claims,'rationale_evidence':[c for c in claims if any(t in c['quote'].lower() for t in ('because','reason','due to'))]}
        elif state['skill']=='compare_options':
            result['fields']={'option_evidence':claims,'tradeoff_evidence':[c for c in claims if any(t in c['quote'].lower() for t in ('because','latency','complexity','tradeoff','capacity'))]}
        else:
            result['fields']={'evidence':claims}
        self.trace.emit('answer',snapshot_id=state['snapshot'],source_ids=[c['source_id'] for c in claims],latency_ms=result['latency_ms'],mode=result['mode'],**usage)
        return {'result':result}

    async def ask(self,snapshot,question,conversation='default',skill='trace_decision'):
        if not question or len(question)>4000 or not conversation or len(conversation)>100:raise ValueError('question/conversation limit')
        self.store.snapshot_info(self.user,snapshot)
        state=await self.graph.ainvoke({'snapshot':snapshot,'question':question,'skill':skill})
        result=state['result']
        self.store.checkpoint_put(self.user,snapshot,conversation,{'snapshot':snapshot,'question':question,'skill':skill,'source_ids':[e['id'] for e in state['evidence']],'result':result,'next':[]})
        self.store.private_put(self.user,snapshot,conversation,question,result)
        return result

    async def summarize(self,state):
        s=self.store.snapshot_info(self.user,state['snapshot'])
        rows=self.store.evidence(self.user,state['snapshot'],kind='segment',meeting=s['meeting'],limit=12,recent=True)
        # Shared worker never consumes private graph state, project files, supplements or API responses.
        rows=rows[-12:];text='\n'.join(f"{r['text'][:500]} [E{r['id']}]" for r in rows)
        self.store.summary_put(self.user,state['snapshot'],text,[r['id'] for r in rows])
        return {'result':{'text':text,'source_ids':[r['id'] for r in rows],'mode':'shared extractive'}}

class Workers:
    """Two isolated workers, bounded pending requests; snapshots pinned at submit."""
    def __init__(self,assistant):
        self.a=assistant;self.qa=ThreadPoolExecutor(1,thread_name_prefix='private-qa');self.bg=ThreadPoolExecutor(1,thread_name_prefix='shared-summary');self.slots=threading.BoundedSemaphore(4);self.bgslots=threading.BoundedSemaphore(2)
    def submit(self,meeting,question,conversation='default',skill='trace_decision',previous=(),folders=()):
        if not self.slots.acquire(False):raise RuntimeError('private QA queue full')
        try:
            sid=self.a.store.snapshot(self.a.user,meeting,previous=previous,folders=folders)
            f=self.qa.submit(lambda: asyncio.run(self.a.ask(sid,question,conversation,skill)))
            f.add_done_callback(lambda _:self.slots.release());return sid,f
        except BaseException:self.slots.release();raise
    def summary(self,sid):
        if not self.bgslots.acquire(False):raise RuntimeError('summary queue full')
        f=self.bg.submit(lambda: asyncio.run(self.a.summary_graph.ainvoke({'snapshot':sid})));f.add_done_callback(lambda _:self.bgslots.release());return f
    def close(self):
        self.qa.shutdown(wait=True,cancel_futures=True);self.bg.shutdown(wait=True,cancel_futures=True)


class LiveSummaries:
    """Periodic host snapshots run on the bounded shared worker, separate from capture."""
    def __init__(self,workers,meeting,interval=15):
        self.workers=workers;self.meeting=meeting;self.interval=interval;self.stop_event=threading.Event();self.last_snapshot=None;self.failures=0
        self.thread=threading.Thread(target=self._run,name='summary-scheduler');self.thread.start()
    def _run(self):
        while not self.stop_event.wait(self.interval):
            try:
                a=self.workers.a;sid=a.store.snapshot(a.user,self.meeting)
                future=self.workers.summary(sid);self.last_snapshot=sid
                def checked(f):
                    if not f.cancelled() and f.exception() is not None:self.failures+=1
                future.add_done_callback(checked)
            except Exception:self.failures+=1
    def close(self):
        self.stop_event.set();self.thread.join()
