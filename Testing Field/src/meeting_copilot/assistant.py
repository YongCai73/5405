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
from .provider import Provider, validated_claims, validated_inferences
from .store import tokens, Denied
from .retrieval import terms, excerpt

def skill_contract(skill):
    if skill=='direct':return {'name':'direct','retrieval':'question keywords','output_fields':['evidence'],'abstention':'insufficient evidence'}
    if skill not in ('catch_up','trace_decision','compare_options'):raise ValueError('unknown skill')
    return json.loads(files('meeting_copilot').joinpath('skills',skill+'.json').read_text(encoding='utf-8'))

def citation(e,quote):
    result={'source_id':e['id'],'kind':e['kind'],'meeting':e['meeting'],'revision':e['revision'],'sha256':e['hash']}
    if e['kind']=='file':
        start=e['text'].index(quote);line=e.get('line_offset',0)
        result.update(path=e['path'],line_start=line+e['text'][:start].count('\n')+1,line_end=line+e['text'][:start+len(quote)].count('\n')+1,folder=e['folder'],char_start=e.get('text_offset',0)+start,char_end=e.get('text_offset',0)+start+len(quote))
    else:result.update(segment=e['logical'],start_ms=e['start_ms'],end_ms=e['end_ms'])
    return result

class State(TypedDict,total=False):
    snapshot: str
    question: str
    skill: str
    evidence: list
    result: dict
    history: list

class Assistant:
    def __init__(self,store,user,provider,trace):
        os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
        self.store=store;self.user=user;self.provider=provider;self.trace=trace
        graph=StateGraph(State);graph.add_node('retrieve',self.retrieve);graph.add_node('answer',self.answer)
        graph.add_edge(START,'retrieve');graph.add_edge('retrieve','answer');graph.add_edge('answer',END);self.graph=graph.compile()
        summary=StateGraph(State);summary.add_node('summarize',self.summarize);summary.add_edge(START,'summarize');summary.add_edge('summarize',END);self.summary_graph=summary.compile()

    def validate_evidence(self,snapshot,evidence):
        for row in evidence:
            retained=self.store.raw(self.user,snapshot,row['id'])
            offset=row.get('text_offset',0)
            metadata=('hash','kind','meeting','logical','revision','folder','path','start_ms','end_ms')
            if any(retained[k]!=row.get(k) for k in metadata) or type(offset) is not int or offset<0 or retained['text'][offset:offset+len(row['text'])]!=row['text'] or row.get('line_offset',0)!=retained['text'][:offset].count('\n'):
                raise Denied('evidence provenance mismatch')

    async def retrieve(self,state):
        skill_contract(state['skill'])
        query_question=state['question']
        if state.get('history') and re.search(r'\b(it|that|they|those|this|them|earlier|more)\b',query_question,re.I):
            query_question+=' '+state['history'][-1]['question']
        query=' '.join(sorted(terms(query_question)))
        rows=self.store.evidence(self.user,state['snapshot'],query=query,limit=100) if query else []
        if state['skill']=='catch_up':
            meeting=self.store.snapshot_info(self.user,state['snapshot'])['meeting']
            rows=self.store.evidence(self.user,state['snapshot'],kind='segment',meeting=meeting,limit=8,recent=True)
        q=terms(query_question)
        rows=[excerpt(e,query_question) for e in rows]
        if state['skill']!='catch_up':rows.sort(key=lambda e:len(q&terms(e['text'])),reverse=True)
        selected=[];size=0
        for e in rows:
            if state['skill']!='catch_up' and not q&terms(e['text']):continue
            if size+len(e['text'])>24000:continue
            selected.append(e);size+=len(e['text'])
            if len(selected)>=16:break
        self.trace.emit('retrieval',snapshot_id=state['snapshot'],source_ids=[e['id'] for e in selected],policy='allow')
        return {'evidence':selected}

    async def answer(self,state):
        start=time.perf_counter();evidence=state['evidence'];usage={'input_tokens':0,'output_tokens':0,'estimated_cost_usd':0.0};inferences=[];tool_events=[]
        simulated=self.provider.config.provider=='demo'
        self.validate_evidence(state['snapshot'],evidence)
        if simulated:
            q=terms(state['question']);claims=[]
            if state.get('history') and re.search(r'\b(it|that|they|those|this|them|earlier|more)\b',state['question'],re.I):q|=terms(state['history'][-1]['question'])
            for e in evidence:
                sentences=re.split(r'(?<=[.!?])\s+|\n',e['text'])
                best=max(sentences,key=lambda s:len(q&terms(s)),default='')
                if best and (state['skill']=='catch_up' or q&terms(best)):
                    claims.append({'source_id':e['id'],'quote':best[:1200]})
            claims=claims[:8]
        else:
            contract=skill_contract(state['skill'])
            context=[{'snapshot_id':t['snapshot_id'],'question':t['question'][:1000],'prior_answer':t['answer']['text'][:1500]} for t in state.get('history',[])[-4:]]
            kwargs={}
            if self.provider.config.tool_rounds:
                from .model_tools import EvidenceTools
                kwargs['tool_context']=EvidenceTools(self.store,self.user,state['snapshot'],evidence)
            reply=await self.provider.complete(state['question'],[{'id':e['id'],'text':e['text'],'kind':e['kind']} for e in evidence],{**contract,'private_conversation':context},**kwargs)
            if reply.evidence:evidence=reply.evidence
            claims=validated_claims(reply.content,evidence)
            inferences=validated_inferences(reply.content,claims,contract)
            tool_events=reply.tool_events
            usage={k:getattr(reply,k) for k in usage}
        # Recheck authorization after a slow provider returns; revocation invalidates in-flight reads.
        self.store.snapshot_info(self.user,state['snapshot'])
        self.validate_evidence(state['snapshot'],evidence)
        for turn in state.get('history',[]):self.store.snapshot_info(self.user,turn['snapshot_id'])
        cites=[citation(next(e for e in evidence if e['id']==c['source_id'] and c['quote'] in e['text']),c['quote']) for c in claims]
        text='\n'.join(f"{c['quote']} [E{c['source_id']}]" for c in claims) if claims else 'Insufficient evidence in this authorized snapshot.'
        if inferences:
            text='Model inference (review against quotations):\n'+'\n'.join(i['text']+' '+ ' '.join(f'[E{rid}]' for rid in i['source_ids']) for i in inferences)+'\n\nExact supporting quotations:\n'+text
        result={'snapshot_id':state['snapshot'],'mode':'SIMULATED / deterministic extractive' if simulated else 'API / model synthesis with validated quotations','skill':state['skill'],'contract':skill_contract(state['skill']),'text':text,'claims':claims,'inferences':inferences,'citations':cites,'abstained':not bool(claims),'usage':usage,'tool_events':tool_events,'conversation_context':[{'turn_id':t['turn_id'],'snapshot_id':t['snapshot_id']} for t in state.get('history',[])],'validation':'Exact quotation membership and source references checked; semantic entailment of model inference is not automatically verified.','latency_ms':round((time.perf_counter()-start)*1000,3)}
        if state['skill']=='catch_up':
            result['fields']={'recent_evidence':claims,'open_questions':[c for c in claims if c['quote'].endswith('?')]}
        elif state['skill']=='trace_decision':
            result['fields']={'decision_evidence':claims,'rationale_evidence':[c for c in claims if any(t in c['quote'].lower() for t in ('because','reason','due to'))]}
        elif state['skill']=='compare_options':
            result['fields']={'option_evidence':claims,'tradeoff_evidence':[c for c in claims if any(t in c['quote'].lower() for t in ('because','latency','complexity','tradeoff','capacity'))]}
        else:
            result['fields']={'evidence':claims}
        self.trace.emit('answer',snapshot_id=state['snapshot'],source_ids=[c['source_id'] for c in claims],latency_ms=result['latency_ms'],mode=result['mode'],**usage)
        result['inferred_fields']={name:[i for i in inferences if i['field']==name] for name in result['contract']['output_fields']}
        return {'result':result,'evidence':evidence}

    async def ask(self,snapshot,question,conversation='default',skill='trace_decision'):
        if not question or len(question)>4000 or not conversation or len(conversation)>100:raise ValueError('question/conversation limit')
        self.store.snapshot_info(self.user,snapshot)
        history=self.store.conversation_history(self.user,snapshot,conversation)
        state=await self.graph.ainvoke({'snapshot':snapshot,'question':question,'skill':skill,'history':history})
        result=state['result']
        self.store.checkpoint_put(self.user,snapshot,conversation,{'snapshot':snapshot,'question':question,'skill':skill,'source_ids':[e['id'] for e in state['evidence']],'result':result,'next':[]})
        self.store.private_put(self.user,snapshot,conversation,question,result)
        return result

    async def summarize(self,state):
        started=time.perf_counter()
        s=self.store.snapshot_info(self.user,state['snapshot'])
        rows=self.store.evidence(self.user,state['snapshot'],kind='segment',meeting=s['meeting'],limit=12,recent=True)
        # Shared generation has no private conversation, project, supplement or tools.
        rows=[excerpt(r,'',1600) for r in rows]
        if self.provider.config.provider=='demo':
            text='\n'.join(f"{r['text'][:500]} [E{r['id']}]" for r in rows)
            result={'text':text,'source_ids':[r['id'] for r in rows],'mode':'shared extractive / SIMULATED'}
        else:
            reply=await self.provider.complete('Summarize the recent meeting: decisions, reasons, tradeoffs and open questions. Do not invent missing information.',[{'id':r['id'],'text':r['text']} for r in rows],skill_contract('catch_up'))
            claims=validated_claims(reply.content,rows);inferences=validated_inferences(reply.content,claims,skill_contract('catch_up'))
            text='\n'.join('Model inference: '+i['text']+' '+ ' '.join(f'[E{n}]' for n in i['source_ids']) for i in inferences)
            text+='\n'+'\n'.join(f"Quote: {c['quote']} [E{c['source_id']}]" for c in claims)
            result={'text':text.strip() or 'Insufficient evidence.','source_ids':list(dict.fromkeys(c['source_id'] for c in claims)),'mode':'shared API synthesis; inference not semantically verified','claims':claims,'inferences':inferences,'usage':{k:getattr(reply,k) for k in ('input_tokens','output_tokens','estimated_cost_usd')}}
        self.store.summary_put(self.user,state['snapshot'],result['text'],result['source_ids'])
        self.trace.emit('shared_summary',snapshot_id=state['snapshot'],source_ids=result['source_ids'],mode=result['mode'],latency_ms=(time.perf_counter()-started)*1000,**result.get('usage',{}))
        return {'result':result}

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
        try:
            f=self.bg.submit(lambda: asyncio.run(self.a.summary_graph.ainvoke({'snapshot':sid})));f.add_done_callback(lambda _:self.bgslots.release());return f
        except BaseException:self.bgslots.release();raise
    def close(self):
        self.qa.shutdown(wait=True,cancel_futures=True);self.bg.shutdown(wait=True,cancel_futures=True)


class LiveSummaries:
    """Periodic host snapshots run on the bounded shared worker, separate from capture."""
    def __init__(self,workers,meeting,interval=15):
        if interval<=0:raise ValueError('summary interval must be positive')
        self.workers=workers;self.meeting=meeting;self.interval=interval;self.stop_event=threading.Event();self.last_snapshot=None;self.failures=0;self.completed=0;self.last_error=None
        self.thread=threading.Thread(target=self._run,name='summary-scheduler');self.thread.start()
    def _run(self):
        while not self.stop_event.wait(self.interval):
            try:
                a=self.workers.a;sid=a.store.snapshot(a.user,self.meeting)
                future=self.workers.summary(sid)
                def checked(f,snapshot=sid):
                    if f.cancelled():return
                    if f.exception() is not None:self.failures+=1;self.last_error=type(f.exception()).__name__
                    else:self.completed+=1;self.last_snapshot=snapshot
                future.add_done_callback(checked)
            except Exception as e:self.failures+=1;self.last_error=type(e).__name__
    def close(self):
        self.stop_event.set();self.thread.join()
