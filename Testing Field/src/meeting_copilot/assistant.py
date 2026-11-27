from __future__ import annotations
import asyncio
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from .provider import Provider, ProviderError, payload, validated_claims, validated_inferences
from .store import tokens, Denied
from .retrieval import terms, excerpt
from .skill_runtime import (AUTO, check_output, clean_terms, continue_session, contract_digest, explained_terms, load_contract, open_session,
    policy_for, prompt_view, response_extension, route, session_state, style_flags, validated_background, validated_challenge)
from .trace import task_trace_context

def skill_contract(skill):
    """Validated, versioned contract (see skill_runtime); raises ValueError for an unknown skill."""
    return load_contract(skill)

NO_USAGE={'input_tokens':0,'output_tokens':0,'estimated_cost_usd':0.0}
# Local extractive mode cannot write a challenge; these fixed questions keep the
# multi-turn flow testable without a model. One per round.
SIMULATED_CHALLENGES=(
    'The record says "{quote}" [E{id}]. What is the strongest reason this could be the wrong call?',
    'Which assumption behind "{quote}" [E{id}] did nobody state out loud?',
    'What do you give up by keeping "{quote}" [E{id}], and who pays that cost?',
    'What would you have to observe to reverse "{quote}" [E{id}]?',
    'If "{quote}" [E{id}] fails in production, what breaks first?')

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
    conversation: str
    routing: dict
    task_id: str
    skill_version: str
    contract_sha256: str

# Crash recovery (docs/CRASH_RECOVERY.md): progress is checkpointed after each step and
# a re-run of the same ask resumes from the last step that was written successfully.
STEPS=('retrieved','answered','saved')

class Assistant:
    def __init__(self,store,user,provider,trace):
        os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
        self.store=store;self.user=user;self.provider=provider;self.trace=trace
        graph=StateGraph(State);graph.add_node('retrieve',self.retrieve);graph.add_node('answer',self.answer)
        # Resume (C1/C2): evidence restored from a 'retrieved' checkpoint skips retrieval.
        graph.add_conditional_edges(START,lambda s:'answer' if s.get('evidence') is not None else 'retrieve',['retrieve','answer'])
        graph.add_edge('retrieve','answer');graph.add_edge('answer',END);self.graph=graph.compile()
        summary=StateGraph(State);summary.add_node('summarize',self.summarize);summary.add_edge(START,'summarize');summary.add_edge('summarize',END);self.summary_graph=summary.compile()

    def validate_evidence(self,snapshot,evidence):
        for row in evidence:
            retained=self.store.raw(self.user,snapshot,row['id'])
            offset=row.get('text_offset',0)
            metadata=('hash','kind','meeting','logical','revision','folder','path','start_ms','end_ms')
            if any(retained[k]!=row.get(k) for k in metadata) or type(offset) is not int or offset<0 or retained['text'][offset:offset+len(row['text'])]!=row['text'] or row.get('line_offset',0)!=retained['text'][:offset].count('\n'):
                raise Denied('evidence provenance mismatch')

    async def retrieve(self,state):
        started=time.perf_counter()
        # The retrieval strategy is read from the skill contract, not chosen by the model.
        plan=skill_contract(state['skill'])['retrieval_plan'];recent=plan['strategy']=='recent_segments'
        query_question=state['question']
        if state.get('history') and re.search(r'\b(it|that|they|those|this|them|earlier|more)\b',query_question,re.I):
            query_question+=' '+state['history'][-1]['question']
        session=open_session(state.get('history') or [])
        if session and session['skill']==state['skill']:
            # A follow-up answer in a multi-turn session keeps retrieving for the session topic.
            query_question+=' '+session['topic']
        query=' '.join(sorted(terms(query_question)))
        rows=self.store.evidence(self.user,state['snapshot'],query=query,limit=100) if query else []
        if recent:
            meeting=self.store.snapshot_info(self.user,state['snapshot'])['meeting']
            rows=self.store.evidence(self.user,state['snapshot'],kind='segment',meeting=meeting,limit=plan['limit'],recent=True)
        q=terms(query_question)
        rows=[excerpt(e,query_question) for e in rows]
        if not recent:rows.sort(key=lambda e:len(q&terms(e['text'])),reverse=True)
        selected=[];size=0
        for e in rows:
            if not recent and not q&terms(e['text']):continue
            if size+len(e['text'])>24000:continue
            selected.append(e);size+=len(e['text'])
            if len(selected)>=plan['limit']:break
        self.trace.emit('retrieval',snapshot_id=state['snapshot'],source_ids=[e['id'] for e in selected],policy='allow',latency_ms=round((time.perf_counter()-started)*1000,3))
        if state.get('conversation'):self._checkpoint(state,'retrieved',selected)
        return {'evidence':selected}

    def _checkpoint(self,state,step,evidence,result=None,turn_id=None):
        """Write progress for (snapshot, user, conversation); see docs/CRASH_RECOVERY.md."""
        data={'version':1,'snapshot':state['snapshot'],'question':state['question'],'skill':state['skill'],'step':step,
              'source_ids':[e['id'] for e in evidence],'evidence':evidence,'updated':time.time()}
        if result is not None:data['result']=result
        if turn_id is not None:data['turn_id']=turn_id
        # Pin the routing decision and contract version so a resume cannot silently
        # switch skill; the store keeps these extra keys at checkpoint version 1.
        for key in ('routing','task_id','skill_version','contract_sha256'):
            if state.get(key) is not None:data[key]=state[key]
        self.store.checkpoint_put(self.user,state['snapshot'],state['conversation'],data)

    def preferences(self):
        """The user's saved wording preferences (style only); see meta_suggest."""
        from .meta_suggest import preferences
        return preferences(self.store,self.user)

    async def answer(self,state):
        start=time.perf_counter();evidence=state['evidence'];usage=dict(NO_USAGE);inferences=[];tool_events=[]
        background=[];notes=[];challenge=None;new_terms=[]
        simulated=self.provider.config.provider=='demo'
        contract=skill_contract(state['skill']);limits=contract['termination'];rejected={'claims':0,'inferences':0}
        history=state.get('history',[])
        # Multi-turn state and term memory come only from this user's stored private turns.
        session=session_state(contract,history,state['question'])
        known=explained_terms(history) if contract.get('memory')=='explained_terms' else []
        general=contract.get('general_knowledge','none')=='labelled'
        policy=policy_for(contract,'model',self.provider.config.tool_rounds)
        tools_enabled=not simulated and policy.max_rounds>0 and bool(policy.allowed)
        # No evidence, no way to fetch any, and no permission to use labelled background:
        # abstain without paying for a provider call.
        skipped=not simulated and not evidence and not tools_enabled and not general
        self.validate_evidence(state['snapshot'],evidence)
        if skipped:
            claims=[]
        elif simulated:
            q=terms(state['question']);claims=[];recent=contract['retrieval_plan']['strategy']=='recent_segments'
            if session:q|=terms(session['topic'])
            if history and re.search(r'\b(it|that|they|those|this|them|earlier|more)\b',state['question'],re.I):q|=terms(history[-1]['question'])
            for e in evidence:
                sentences=re.split(r'(?<=[.!?])\s+|\n',e['text'])
                best=max(sentences,key=lambda s:len(q&terms(s)),default='')
                if best and (recent or q&terms(best)):
                    claims.append({'source_id':e['id'],'quote':best[:1200]})
            claims=claims[:limits['max_claims']]
            if contract.get('memory')=='explained_terms':
                new_terms=sorted(t for t in terms(state['question']) if any(t in terms(c['quote']) for c in claims))[:8]
            if session and claims and not session['closing']:
                challenge=SIMULATED_CHALLENGES[(session['round']-1)%len(SIMULATED_CHALLENGES)].replace('{quote}',claims[0]['quote'][:200]).replace('{id}',str(claims[0]['source_id']))
            if session and session['answered_round'] and claims:
                said=terms(state['question']);found=sorted(t for t in said if any(t in terms(c['quote']) for c in claims))
                notes.append('SIMULATED mode does not judge your answer. Terms from it found in the cited record: '+(', '.join(found) or 'none')+'. Not found: '+(', '.join(sorted(said-set(found))) or 'none')+'.')
            if general or session or len(contract['output_fields'])>2:
                notes.append('SIMULATED mode only extracts matching sentences. Explanations, critiques, assumptions and failure scenarios need a configured model.')
        else:
            context=[{'snapshot_id':t['snapshot_id'],'question':t['question'][:1000],'prior_answer':t['answer']['text'][:1500]} for t in history[-4:]]
            kwargs={}
            if tools_enabled:
                from .model_tools import EvidenceTools
                kwargs['tool_context']=EvidenceTools(self.store,self.user,state['snapshot'],evidence,policy=policy,trace=self.trace)
            extension=response_extension(contract,session,known,self.preferences())
            reply=await self.provider.complete(state['question'],[{'id':e['id'],'text':e['text'],'kind':e['kind']} for e in evidence],
                {**prompt_view(contract),'private_conversation':context,'response_extension':extension},**kwargs)
            if reply.evidence:evidence=reply.evidence
            claims=validated_claims(reply.content,evidence)[:limits['max_claims']]
            inferences=validated_inferences(reply.content,claims,contract)[:limits['max_inferences']]
            raw=payload(reply.content)
            for key,kept in (('claims',claims),('inferences',inferences)):
                rejected[key]=max(0,len(raw[key])-len(kept)) if isinstance(raw.get(key),list) else 0
            background=validated_background(raw,contract)
            if contract.get('memory')=='explained_terms':
                new_terms=clean_terms((raw.get('explained_terms') if isinstance(raw.get('explained_terms'),list) else [])+[b['term'] for b in background])
            if session and not session['answered_round']:
                # Round 1 has no user answer yet, so there is nothing to assess.
                rejected['inferences']+=len(inferences);inferences=[]
            if session and claims and not session['closing']:
                challenge=validated_challenge(raw)
                if not challenge:notes.append('The model did not return one valid question, so the session was closed.')
            tool_events=reply.tool_events
            usage={k:getattr(reply,k) for k in usage}
        # Recheck authorization after a slow provider returns; revocation invalidates in-flight reads.
        self.store.snapshot_info(self.user,state['snapshot'])
        self.validate_evidence(state['snapshot'],evidence)
        for turn in history:self.store.snapshot_info(self.user,turn['snapshot_id'])
        cites=[citation(next(e for e in evidence if e['id']==c['source_id'] and c['quote'] in e['text']),c['quote']) for c in claims]
        text='\n'.join(f"{c['quote']} [E{c['source_id']}]" for c in claims) if claims else 'Insufficient evidence in this authorized snapshot.'
        if inferences:
            line=lambda i:i['text']+' '+' '.join(f'[E{rid}]' for rid in i['source_ids'])
            used=[f for f in contract['output_fields'] if any(i['field']==f for i in inferences)]
            if len(used)>1 and len(contract['output_fields'])>2:
                # Several kinds of model wording (assumptions, failure scenarios, ...): show them by field.
                body='\n'.join(f.replace('_',' ').capitalize()+':\n'+'\n'.join('- '+line(i) for i in inferences if i['field']==f) for f in used)
            else:
                body='\n'.join(line(i) for i in inferences)
            text=contract.get('inference_label','Model inference (review against quotations)')+':\n'+body+'\n\nExact supporting quotations:\n'+text
        if background:
            text+='\n\nGeneral background (model knowledge; NOT from the meeting or project files, not verified):\n'+'\n'.join(f"{b['term']}: {b['text']}" for b in background)
        # A remembered term counts as mentioned when the question shares a content word with it
        # ("queue capacity" mentions the remembered "bounded queue").
        asked=terms(state['question']);previously=[t for t in known if terms(t)&asked]
        if previously and simulated:
            text='Already explained earlier in this conversation (not repeated in full): '+', '.join(previously)+'\n\n'+text
        if challenge:
            text+=f"\n\nChallenge {session['round']} of {session['max_rounds']}: {challenge}"
        elif session and session['answered_round']:
            text+='\n\nChallenge session closed.'
        if notes:text+='\n\n'+'\n'.join('Note: '+n for n in notes)
        routing=state.get('routing') or route(state['question'],state['skill'])
        abstained=not claims and not background
        abstain_reason=None if not abstained else ('no_evidence_retrieved' if not evidence else 'no_validated_quotation')
        mode='SIMULATED / deterministic extractive' if simulated else ('API / provider not called: no evidence retrieved' if skipped else 'API / model synthesis with validated quotations')
        report={'claims_rejected':rejected['claims'],'inferences_rejected':rejected['inferences'],'tool_calls':len(tool_events),'tool_calls_denied':sum(e.get('status')!='ok' for e in tool_events),
                'style_flags':style_flags([i['text'] for i in inferences]+[b['text'] for b in background]+([challenge] if challenge else []))}
        result={'snapshot_id':state['snapshot'],'mode':mode,'skill':state['skill'],'skill_version':contract['version'],'contract_sha256':contract_digest(contract),'routing':routing,'task_id':state.get('task_id'),'abstain_reason':abstain_reason,'validation_report':report,'contract':contract,'text':text,'claims':claims,'inferences':inferences,'citations':cites,'abstained':abstained,'usage':usage,'tool_events':tool_events,'conversation_context':[{'turn_id':t['turn_id'],'snapshot_id':t['snapshot_id']} for t in history],'validation':'Exact quotation membership and source references checked; semantic entailment of model inference is not automatically verified.','latency_ms':round((time.perf_counter()-start)*1000,3)}
        if background:result['background']=background
        if notes:result['notes']=notes
        if contract.get('memory')=='explained_terms':
            # Carried forward so a later turn can avoid defining the same term again.
            result['explained_terms']=list(dict.fromkeys(known+new_terms))[-40:];result['previously_explained']=previously
        if session:
            asked=session['round'] if challenge else session['answered_round']
            result['session']={'skill':state['skill'],'topic':session['topic'],'round':asked,'max_rounds':session['max_rounds'],'awaiting':'answer' if challenge else None,'challenge':challenge}
            if challenge:result['challenge']={'round':asked,'of':session['max_rounds'],'question':challenge}
        if state['skill']=='catch_up':
            result['fields']={'recent_evidence':claims,'open_questions':[c for c in claims if c['quote'].endswith('?')]}
        elif state['skill']=='trace_decision':
            result['fields']={'decision_evidence':claims,'rationale_evidence':[c for c in claims if any(t in c['quote'].lower() for t in ('because','reason','due to'))]}
        elif state['skill']=='compare_options':
            result['fields']={'option_evidence':claims,'tradeoff_evidence':[c for c in claims if any(t in c['quote'].lower() for t in ('because','latency','complexity','tradeoff','capacity'))]}
        else:
            # Quotations go to the contract's evidence field; other fields hold model inferences only.
            fields=contract['output_fields'];holder=next((f for f in fields if f.endswith('evidence')),fields[0])
            result['fields']={f:(claims if f==holder else []) for f in fields}
        check_output(result,contract)
        self.trace.emit('answer',snapshot_id=state['snapshot'],source_ids=[c['source_id'] for c in claims],latency_ms=result['latency_ms'],mode=result['mode'],
                        skill=state['skill'],skill_version=contract['version'],tool_calls=len(tool_events),tool_denied=report['tool_calls_denied'],reason=abstain_reason,
                        step=f"round_{result['session']['round']}" if session else None,**usage)
        result['inferred_fields']={name:[i for i in inferences if i['field']==name] for name in result['contract']['output_fields']}
        if state.get('conversation'):self._checkpoint(state,'answered',evidence,result)
        return {'result':result,'evidence':evidence}

    def _evidence_for(self,snapshot,result):
        """Retained rows for every source an answer cites (used when no evidence was stored)."""
        ids={c['source_id'] for c in result.get('claims',[])}|{i for inf in result.get('inferences',[]) for i in inf.get('source_ids',[])}
        return [self.store.raw(self.user,snapshot,rid) for rid in sorted(ids)]

    def resume_point(self,snapshot,question,conversation,skill,routing=None):
        """The checkpoint to resume from, or None to start fresh.

        A checkpoint only applies to the same question and the same skill request: either
        the same value the user passed before (a skill name or 'auto'), or a request that
        resolves to the skill the checkpoint already pinned. A different question or skill
        in the same conversation starts over and overwrites it.
        """
        saved=self.store.checkpoint_get(self.user,snapshot,conversation)
        if not saved or saved.get('question')!=question or saved.get('step') not in STEPS:return None
        if (saved.get('routing') or {}).get('requested',saved.get('skill'))==skill:return saved
        routing=routing or route(question,skill)
        return saved if saved.get('skill')==routing['selected'] else None

    async def ask(self,snapshot,question,conversation='default',skill='trace_decision'):
        """Answer one private question. `skill` is a skill name, 'direct', or 'auto'."""
        if not question or len(question)>4000 or not conversation or len(conversation)>100:raise ValueError('question/conversation limit')
        if skill!=AUTO:skill_contract(skill)
        # Authorization first: a checkpoint never grants access after revocation.
        self.store.snapshot_info(self.user,snapshot)
        started=time.perf_counter()
        # An open multi-turn session (grill_me) keeps auto routing in that skill.
        routing=continue_session(self.store.conversation_history(self.user,snapshot,conversation,limit=20),route(question,skill))
        saved=self.resume_point(snapshot,question,conversation,skill,routing)
        task_id=uuid.uuid4().hex
        if saved:
            # Recovery never re-routes: the checkpoint pins the first decision and task ID,
            # even if the router or trigger phrases changed in between.
            routing=saved.get('routing') or route(question,saved['skill'])
            task_id=saved.get('task_id') or task_id
        contract=skill_contract(routing['selected'])
        state={'snapshot':snapshot,'question':question,'skill':routing['selected'],'conversation':conversation,'routing':routing,
               'task_id':task_id,'skill_version':contract['version'],'contract_sha256':contract_digest(contract)}
        with task_trace_context(task_id=task_id,skill=state['skill'],skill_version=contract['version']):
            self.trace.emit('skill_route',snapshot_id=snapshot,route_mode=routing['mode'],route_status=routing['status'],candidates=routing['candidates'],
                            step='resumed' if saved else 'new',latency_ms=round((time.perf_counter()-started)*1000,3))
            try:
                return await self._ask(state,saved,skill)
            except Exception as e:
                # Fixed code or exception class name only; never the provider message.
                self.trace.emit('ask_failed',snapshot_id=snapshot,error=e.code if isinstance(e,ProviderError) else type(e).__name__)
                raise

    @staticmethod
    def _pinned(state,result):
        """Checkpoint identity for a stored answer: the skill and version that produced it."""
        pinned={**state,'skill':result.get('skill',state['skill'])}
        for key in ('routing','task_id','skill_version','contract_sha256'):
            if result.get(key) is not None:pinned[key]=result[key]
            elif result.get('skill',state['skill'])!=state['skill']:pinned.pop(key,None)
        return pinned

    async def _ask(self,state,saved,requested):
        snapshot,question,conversation,routing=state['snapshot'],state['question'],state['conversation'],state['routing']
        if saved:self.trace.emit('ask_resumed',snapshot_id=snapshot,step=saved['step'])
        committed=self.store.private_get(self.user,snapshot,conversation,question)
        if committed:
            # C4, or a crash after the turn was written: the store keeps the first committed
            # answer per (snapshot, conversation, question), so never answer twice.
            result=committed[0]['answer']
            evidence=saved['evidence'] if saved and saved.get('evidence') is not None else self._evidence_for(snapshot,result)
            self.validate_evidence(snapshot,evidence)
            if not saved or saved['step']!='saved':
                self._checkpoint(self._pinned(state,result),'saved',evidence,result,self.store.private_put(self.user,snapshot,conversation,question,result))
            # No model call happened now, so report no new usage (keeps cost accounting honest).
            out={**result,'usage':dict(NO_USAGE),'resumed_from_checkpoint':'saved'}
            answered=result.get('skill');asked=(result.get('routing') or {}).get('requested',answered)
            if answered is not None and asked!=requested and answered!=routing['selected']:
                # Skill is not part of the store key. Say so instead of silently returning
                # an answer produced by another skill.
                out['skill_conflict']={'requested':requested,'would_use':routing['selected'],'answered_with':answered,
                    'action':'Returned the first committed answer. Use a new conversation or a new snapshot to answer this question with another skill.'}
                self.trace.emit('skill_conflict',snapshot_id=snapshot,reason='question_already_answered_with_other_skill')
            return out
        if saved and saved['step']=='retrieved' and saved.get('skill_version') is not None and \
                (saved['skill_version'],saved.get('contract_sha256'))!=(state['skill_version'],state['contract_sha256']):
            # Evidence was selected under an older contract and no answer exists yet: redo
            # retrieval under the current contract of the SAME skill, and record that.
            self.trace.emit('skill_contract_changed',snapshot_id=snapshot,step='retrieved',reason='retrieval_restarted_under_current_contract')
            saved=None
        if saved and saved['step']=='answered':  # C3: answer stored, turn not yet saved
            # The answer keeps the skill version it was produced with; it is not regenerated.
            result=saved['result'];state=self._pinned(state,result)
            # Old-format checkpoints (before steps existed) have a result but no evidence.
            evidence=saved['evidence'] if saved.get('evidence') is not None else self._evidence_for(snapshot,result)
            self.validate_evidence(snapshot,evidence)  # re-check provenance and current access
        else:
            history=self.store.conversation_history(self.user,snapshot,conversation,limit=20)
            start={**state,'history':history}
            if saved:start['evidence']=saved['evidence']  # C1/C2: skip retrieval, re-checked in answer
            out=await self.graph.ainvoke(start)
            result,evidence=out['result'],out['evidence']
        turn_id=self.store.private_put(self.user,snapshot,conversation,question,result)
        self._checkpoint(state,'saved',evidence,result,turn_id)
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
            reply=await self.provider.complete('Summarize the recent meeting: decisions, reasons, tradeoffs and open questions. Do not invent missing information.',[{'id':r['id'],'text':r['text']} for r in rows],prompt_view(skill_contract('catch_up')))
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
