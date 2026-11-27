import asyncio
import hashlib
import json
import pytest
from meeting_copilot.store import Denied, Store
from meeting_copilot.service import EvidenceService
from meeting_copilot.provider import Provider, Completion, validated_inferences
from meeting_copilot.config import Config
from meeting_copilot.assistant import Assistant
from meeting_copilot.model_tools import EvidenceTools


async def test_long_meeting_search_ranks_before_limit_and_exports_citations(store,event,assistant):
    for i in range(180):
        store.ingest('alice','m',{**event,'event_id':f'e{i}','segment_id':f's{i}','seq':i+1,
                     'start_ms':i*1000,'end_ms':i*1000+500,
                     'text':'SQLite common discussion.' if i<179 else 'SQLite rejected Kafka because deployment overhead exceeds the budget.'})
    sid=store.snapshot('alice','m')
    result=await assistant.ask(sid,'Why was Kafka rejected for deployment overhead?')
    assert 'exceeds the budget' in result['text']
    exported=store.export('alice',sid,'default')
    ids={r['id'] for r in exported['evidence']}
    assert all(c['source_id'] in ids for c in result['claims'])
    assert len(EvidenceService(store,'alice',sid).get_transcript(offset=100))==80


@pytest.mark.parametrize('newline',['\n','\r\n'],ids=['LF','CRLF'])
async def test_late_file_identifier_exact_line_hash_and_multi_file_ranking(store,event,assistant,tmp_path,newline):
    project=tmp_path/'project';project.mkdir()
    for i in range(130):
        (project/f'a{i:03}.py').write_bytes(('queue capacity common settings'+newline).encode('utf-8'))
    content=newline.join([f'# filler line {i} unrelated' for i in range(240)]+['MAX_PENDING_JOBS = 937  # prevents queue saturation',''])
    target=project/'z_worker.py';target.write_bytes(content.encode('utf-8'))
    raw=target.read_bytes();decoded=raw.decode('utf-8')
    before=hashlib.sha256(raw).hexdigest()
    folder=store.approve('alice','m',project);store.refresh('alice',folder)
    sid=store.snapshot('alice','m',folders=[folder])
    answer=await assistant.ask(sid,'What is MAX_PENDING_JOBS queue capacity?')
    assert '937' in answer['text']
    claim=next(c for c in answer['claims'] if '937' in c['quote'])
    cite=next(c for c in answer['citations'] if c['source_id']==claim['source_id'])
    assert cite['line_start']==241 and cite['line_end']==241
    retained=store.raw('alice',sid,claim['source_id'])
    assert retained['text']==decoded and retained['text'].encode('utf-8')==raw
    assert decoded[cite['char_start']:cite['char_end']]==claim['quote']
    assert cite['char_start']==sum(len(line) for line in decoded.splitlines(keepends=True)[:240])
    assert cite['sha256']==before
    matches=EvidenceService(store,'alice',sid).search_project('MAX_PENDING_JOBS queue capacity')
    found=next(r for r in matches if r['path']=='z_worker.py')
    assert '937' in found['excerpt'] and found['line_start']>80
    assert hashlib.sha256((project/'z_worker.py').read_bytes()).hexdigest()==before
    target.write_bytes(('MAX_PENDING_JOBS = 12345'+newline).encode('utf-8'))
    store.refresh('alice',folder)
    assert '937' in (await assistant.ask(sid,'MAX_PENDING_JOBS'))['text']
    assert '12345' not in json.dumps(store.export('alice',sid,'default'))


def test_actual_runtime_overlap_even_if_not_named_data(store,tmp_path):
    for path in [store.root,store.root.parent,store.root/'exports']:
        path.mkdir(exist_ok=True)
        with pytest.raises(ValueError,match='overlap'):store.approve('alice','m',path)
    sibling=tmp_path/'allowed';sibling.mkdir();(sibling/'readme.md').write_text('safe')
    assert store.refresh('alice',store.approve('alice','m',sibling))['files']==1


async def test_conversation_across_snapshots_scope_revoke_and_future(store,event,assistant,tmp_path):
    first_id=store.ingest('alice','m',event);old=store.snapshot('alice','m')
    await assistant.ask(old,'Why SQLite?','thread-a')
    newer=store.snapshot('alice','m')
    followup=await assistant.ask(newer,'Why was it chosen?','thread-a')
    assert 'SQLite' in followup['text'] and followup['conversation_context'][0]['snapshot_id']==old
    assert store.conversation_history('alice',newer,'thread-b')==[]
    assert store.conversation_history('alice',old,'thread-a')==[]
    store.create('alice','other','Other');other=store.snapshot('alice','other')
    assert store.conversation_history('alice',other,'thread-a')==[]
    store.create('owner','past','Past');store.grant('owner','past','alice')
    scoped=store.snapshot('alice','m',previous=['past'])
    store.private_put('alice',scoped,'thread-a','secret prior question',{'text':'PRIOR-PRIVATE-CANARY'})
    narrow=store.snapshot('alice','m')
    assert 'PRIOR-PRIVATE-CANARY' not in json.dumps(store.conversation_history('alice',narrow,'thread-a'))
    wide=store.snapshot('alice','m',previous=['past'])
    assert 'PRIOR-PRIVATE-CANARY' in json.dumps(store.conversation_history('alice',wide,'thread-a'))
    store.grant('owner','past','alice',False)
    with pytest.raises(Denied):store.conversation_history('alice',wide,'thread-a')
    assert 'PRIOR-PRIVATE-CANARY' not in json.dumps(store.conversation_history('alice',store.snapshot('alice','m'),'thread-a'))
    assert store.raw('alice',old,first_id)['text']==event['text']


class Synthesis(Provider):
    def __init__(self):super().__init__(Config(provider='openai'));self.contracts=[]
    async def complete(self,q,e,c):
        self.contracts.append(c)
        if not e:return Completion('{"claims":[],"inferences":[]}')
        return Completion(json.dumps({'claims':[{'source_id':e[0]['id'],'quote':e[0]['text']}],
            'inferences':[{'text':'The documented choice prioritizes simplicity.',
                           'source_ids':[e[0]['id']],'field':c['output_fields'][0]}]}),10,20)


async def test_synthesis_keeps_quotes_inference_and_private_context_separate(store,event,assistant):
    store.ingest('alice','m',event);p=Synthesis();a=Assistant(store,'alice',p,assistant.trace)
    first=store.snapshot('alice','m');result=await a.ask(first,'SQLite PRIVATE-QUESTION','thread')
    assert result['inferences'] and 'Model inference' in result['text']
    assert result['claims'][0]['quote']==event['text']
    assert 'not automatically verified' in result['validation']
    second=store.snapshot('alice','m');await a.ask(second,'Why it?','thread')
    assert p.contracts[-1]['private_conversation'][0]['snapshot_id']==first
    await a.summary_graph.ainvoke({'snapshot':second})
    assert 'private_conversation' not in p.contracts[-1]
    assert 'PRIVATE-QUESTION' not in store.summary_get('alice',second)['text']


def test_inference_reference_validation_does_not_claim_entailment():
    contract={'output_fields':['rationale_evidence']};claims=[{'source_id':1,'quote':'SQLite is local.'}]
    invalid=[{'text':'Invented [E999]','source_ids':[1],'field':'rationale_evidence'},
             {'text':'Invented','source_ids':[999],'field':'rationale_evidence'},
             {'text':'Invented','source_ids':[True],'field':'rationale_evidence'}]
    assert validated_inferences(json.dumps({'inferences':invalid}),claims,contract)==[]
    # A syntactically valid inference can still be semantically wrong: label, don't claim proof.
    valid={'text':'Semantically unverified conclusion','source_ids':[1],'field':'rationale_evidence'}
    assert 'not automatically verified' in validated_inferences(json.dumps({'inferences':[valid]}),claims,contract)[0]['label']


def test_read_only_tools_cannot_choose_identity_snapshot_or_escape(store,event):
    rid=store.ingest('alice','m',event);sid=store.snapshot('alice','m')
    store.create('bob','secret','HIDDEN');hidden=store.ingest('bob','secret',event)
    tools=EvidenceTools(store,'alice',sid)
    assert tools.call('grant','{}')['status']=='denied'
    assert tools.call('search_evidence',json.dumps({'query':'SQLite','user':'bob'}))['status']=='denied'
    assert tools.call('read_evidence',json.dumps({'source_id':hidden,'start_line':1,'end_line':2}))['status']=='denied'
    ok=tools.call('search_evidence','{"query":"SQLite"}')
    assert ok['evidence'][0]['id']==rid
    store.grant('alice','m','bob');bs=store.snapshot('bob','m');other=EvidenceTools(store,'bob',bs)
    store.grant('alice','m','bob',False)
    with pytest.raises(Denied):other.call('search_evidence','{"query":"SQLite"}')


async def test_catch_up_does_not_replace_current_with_opted_in_old_meeting(store,event,assistant):
    store.ingest('alice','m',event);store.create('alice','prior','Prior')
    for i in range(12):
        store.ingest('alice','prior',{**event,'event_id':f'old{i}','segment_id':f'old{i}','seq':i+1,'start_ms':999999+i,'end_ms':1001000+i,'text':'PRIOR-MEETING-TOPIC'})
    sid=store.snapshot('alice','m',previous=['prior'])
    result=await assistant.ask(sid,'Catch up',skill='catch_up')
    assert 'SQLite' in result['text'] and 'PRIOR-MEETING' not in result['text']


async def test_graph_cannot_output_mismatched_retained_provenance(store,event,assistant):
    rid=store.ingest('alice','m',event);sid=store.snapshot('alice','m')
    row=store.raw('alice',sid,rid)
    with pytest.raises(Denied,match='provenance'):
        await assistant.answer({'snapshot':sid,'question':'SQLite','skill':'direct','evidence':[{**row,'text':'SQLite forged claim'}]})
    with pytest.raises(Denied,match='provenance'):
        await assistant.answer({'snapshot':sid,'question':'SQLite','skill':'direct','evidence':[{**row,'hash':'forged'}]})


def test_trace_export_filters_other_principals_and_revoked_metadata(store,event,assistant,tmp_path):
    store.ingest('alice','m',event);sid=store.snapshot('alice','m')
    store.grant('alice','m','bob');bs=store.snapshot('bob','m')
    assistant.trace.emit('answer',snapshot_id=sid,source_ids=[1])
    assistant.trace.emit('answer',snapshot_id=bs,source_ids=[1])
    target=tmp_path/'redacted.jsonl'
    assert assistant.trace.redacted_export(target,lambda s:store.snapshot_info('bob',s))==1
    assert sid not in target.read_text() and bs in target.read_text()
    store.grant('alice','m','bob',False)
    assert assistant.trace.redacted_export(target,lambda s:store.snapshot_info('bob',s))==0


def test_upgrade_rejects_legacy_overlapping_folder_even_for_old_snapshots(store,event):
    store.ingest('alice','m',event)
    # Simulate a v0.1 approval that is no longer permissible; don't read disk secrets.
    with store.tx() as db:
        db.execute('INSERT INTO folders VALUES(?,?,?,?,1)',('legacy','m',str(store.root.parent),'alice'))
        db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)',('legacy-snapshot','alice','m',1,'["m"]','["legacy"]','[1]',1))
    for read in [lambda:store.snapshot_info('alice','legacy-snapshot'),lambda:store.refresh('alice','legacy'),lambda:store.raw('alice','legacy-snapshot',1)]:
        with pytest.raises(Denied,match='overlaps'):read()


def test_private_question_cutoff_uses_turn_watermark_not_wall_clock(store,event,monkeypatch):
    import meeting_copilot.store as storage
    store.ingest('alice','m',event)
    first=store.snapshot('alice','m');pinned=store.snapshot('alice','m')
    # A clock adjustment cannot move a later answer into an earlier snapshot.
    monkeypatch.setattr(storage.time,'time',lambda:1)
    store.private_put('alice',first,'thread','future question',{'text':'FUTURE-PRIVATE-CANARY'})
    assert store.conversation_history('alice',pinned,'thread')==[]
    fresh=store.snapshot('alice','m')
    assert store.conversation_history('alice',fresh,'thread')[0]['answer']['text']=='FUTURE-PRIVATE-CANARY'


def test_additive_upgrade_backfills_existing_answers_without_changing_old_cutoffs(tmp_path):
    import sqlite3
    path=tmp_path/'runtime/evidence.db';store=Store(path);store.create('alice','m','M');sid=store.snapshot('alice','m')
    store.private_put('alice',sid,'thread','legacy question',{'text':'LEGACY ANSWER'})
    with store.tx() as db:
        db.execute('DROP TABLE conversation_turns');db.execute('DROP TABLE snapshot_turn_watermarks');db.execute('DROP TABLE app_meta')
    store.close()
    store=Store(path)
    try:
        assert store.private_get('alice',sid,'thread')[0]['answer']['text']=='LEGACY ANSWER'
        assert store.conversation_history('alice',sid,'thread')==[]
        fresh=store.snapshot('alice','m');assert store.conversation_history('alice',fresh,'thread')[0]['answer']['text']=='LEGACY ANSWER'
    finally:store.close()
    store=Store(path)
    try:assert store.db.execute('SELECT COUNT(*) FROM conversation_turns').fetchone()[0]==1
    finally:store.close()


def test_cross_connection_acl_swap_cannot_mix_two_authorization_states(tmp_path,event,monkeypatch):
    path=tmp_path/'runtime/evidence.db';reader=Store(path);writer=Store(path)
    try:
        for meeting in ('m','previous'):
            writer.create('owner',meeting,meeting);writer.grant('owner',meeting,'viewer');writer.ingest('owner',meeting,event)
        sid=reader.snapshot('viewer','m',previous=['previous'])
        writer.grant('owner','previous','viewer',False)
        original=reader._check;swapped=False
        def interleave(user,meeting,owner=False):
            nonlocal swapped
            result=original(user,meeting,owner)
            if meeting=='m' and not swapped:
                swapped=True
                with writer.tx() as db:
                    db.execute('DELETE FROM acl WHERE user=? AND meeting=?',('viewer','m'))
                    db.execute('INSERT INTO acl VALUES(?,?)',('previous','viewer'))
            return result
        monkeypatch.setattr(reader,'_check',interleave)
        # Before the atomic swap only m is allowed; afterwards only previous is.
        # A read must never combine both grants from different SQLite states.
        with pytest.raises(Denied):reader.evidence('viewer',sid)
        assert swapped
        with pytest.raises(Denied):reader.export('viewer',sid,'default')
        assert [m['id'] for m in reader.list_meetings('viewer')]==['previous']
    finally:reader.close();writer.close()


@pytest.mark.parametrize('newline',['\n','\r\n'],ids=['LF','CRLF'])
async def test_tool_reads_preserve_multiple_views_and_exact_original_lines(store,tmp_path,assistant,newline):
    first='FIRST_MARKER chooses SQLite for local deployment.'
    second='SECOND_MARKER records the later capacity decision.'
    third='THIRD_MARKER records the separate rollback decision.'
    unseen='UNSENT_MARKER must not become a validated quotation.'
    project=tmp_path/'multi-view-project';project.mkdir()
    original=newline.join([first]+['# filler']*999+[unseen]+['# filler']*1000+[second,third,''])
    target=project/'decisions.md';target.write_bytes(original.encode('utf-8'))
    raw=target.read_bytes();decoded=raw.decode('utf-8');before=hashlib.sha256(raw).hexdigest()
    folder=store.approve('alice','m',project);store.refresh('alice',folder)
    sid=store.snapshot('alice','m',folders=[folder])

    class MultipleViews(Provider):
        def __init__(self):super().__init__(Config(provider='openai',tool_rounds=2))
        async def complete(self,q,e,c,tool_context=None):
            rid=e[0]['id']
            assert first in e[0]['text'] and unseen not in e[0]['text']
            for line,quote in [(2002,second),(2003,third)]:
                reply=tool_context.call('read_evidence',json.dumps({'source_id':rid,'start_line':line,'end_line':line}))
                assert reply['status']=='ok' and quote in reply['evidence'][0]['text']
            return Completion(json.dumps({'claims':[{'source_id':rid,'quote':q} for q in (first,second,third,unseen)]}),
                              evidence=list(tool_context.sources.values()))

    assistant.provider=MultipleViews()
    result=await assistant.ask(sid,'Explain FIRST_MARKER')
    assert [c['quote'] for c in result['claims']]==[first,second,third]
    assert [c['line_start'] for c in result['citations']]==[1,2002,2003]
    for claim,cite in zip(result['claims'],result['citations']):
        assert store.raw('alice',sid,claim['source_id'])['text']==decoded
        assert decoded[cite['char_start']:cite['char_end']]==claim['quote']
        assert cite['line_end']==cite['line_start'] and cite['sha256']==before
        assert cite['char_start']==sum(len(line) for line in decoded.splitlines(keepends=True)[:cite['line_start']-1])
    assert unseen not in result['text']
    assert target.read_bytes()==raw
