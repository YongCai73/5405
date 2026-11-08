import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from meeting_copilot.config import Config
from meeting_copilot.provider import Provider,ProviderError,validated_claims

@pytest.fixture
def fake_http():
    state={'requests':[],'status':200,'delay':0,'stream_delay':0,'closed_streams':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['content-length'])))
            state['requests'].append({'path':self.path,'payload':payload,'headers':dict(self.headers)})
            if state.get('response_gate') is not None:state['response_gate'].wait(timeout=45)
            time.sleep(state['delay'])
            anthropic=self.path.endswith('/messages')
            tool=state.get('tool') if len(state['requests'])==1 or state.get('always_tool') else None
            content=state.get('content','{"claims":[{"source_id":1,"quote":"SQLite is local."}]}')
            if state['status']!=200:
                body={'error':{'type':'api_error','message':'SENSITIVE-FAKE-SERVER-ERROR'}}
            elif anthropic:
                block={'type':'tool_use','id':'call-fixture','name':tool['name'],'input':tool['arguments']} if tool else {'type':'text','text':content}
                body={'id':'msg-fixture','type':'message','role':'assistant','model':payload['model'],'content':[block],'stop_reason':'tool_use' if tool else 'end_turn','stop_sequence':None,'usage':{'input_tokens':17,'output_tokens':11}}
            else:
                message={'role':'assistant','content':None if tool else content}
                if tool:message['tool_calls']=[{'id':'call-fixture','type':'function','function':{'name':tool['name'],'arguments':json.dumps(tool['arguments'])}}]
                body={'id':'chat-fixture','object':'chat.completion','created':1,'model':payload['model'],'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if tool else 'stop'}],'usage':{'prompt_tokens':17,'completion_tokens':11,'total_tokens':28}}
            if payload.get('stream') and state['status']==200:
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                def emit(data):
                    prefix=('event: '+data['type']+'\n') if anthropic else ''
                    self.wfile.write((prefix+'data: '+json.dumps(data)+'\n\n').encode());self.wfile.flush()
                try:
                    if anthropic:
                        initial={**body,'content':[],'stop_reason':None,'usage':{'input_tokens':17,'output_tokens':0}}
                        emit({'type':'message_start','message':initial})
                        block={'type':'tool_use','id':'call-fixture','name':tool['name'],'input':{}} if tool else {'type':'text','text':''}
                        emit({'type':'content_block_start','index':0,'content_block':block})
                        value=json.dumps(tool['arguments']) if tool else content
                        for part in [value[:7],value[7:]]:
                            time.sleep(state['stream_delay'])
                            delta={'type':'input_json_delta','partial_json':part} if tool else {'type':'text_delta','text':part}
                            emit({'type':'content_block_delta','index':0,'delta':delta})
                        emit({'type':'content_block_stop','index':0})
                        if state.get('stream_error'):
                            emit({'type':'error','error':{'type':'overloaded_error','message':'SENSITIVE-STREAM-ERROR'}})
                        else:
                            emit({'type':'message_delta','delta':{'stop_reason':'tool_use' if tool else 'end_turn','stop_sequence':None},'usage':{'output_tokens':11}})
                            emit({'type':'message_stop'})
                    else:
                        def chunk(delta,finish=None):return {'id':'chat-fixture','object':'chat.completion.chunk','created':1,'model':payload['model'],'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                        emit(chunk({'role':'assistant','content':''}))
                        if tool:
                            arguments=json.dumps(tool['arguments'])
                            emit(chunk({'tool_calls':[{'index':0,'id':'call-fixture','type':'function','function':{'name':tool['name'],'arguments':arguments[:7]}}]}))
                            time.sleep(state['stream_delay'])
                            emit(chunk({'tool_calls':[{'index':0,'function':{'arguments':arguments[7:]}}]}))
                        else:
                            for part in [content[:7],content[7:]]:
                                time.sleep(state['stream_delay']);emit(chunk({'content':part}))
                        emit(chunk({},'tool_calls' if tool else 'stop'))
                        if not state.get('omit_usage'):emit({'id':'chat-fixture','object':'chat.completion.chunk','created':1,'model':payload['model'],'choices':[],'usage':body['usage']})
                        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
                finally:state['closed_streams']+=1
                return
            if state.get('omit_usage') and not anthropic:body.pop('usage',None)
            raw=json.dumps(body).encode();self.send_response(state['status']);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,name='fake-api-loopback');thread.start()
    state['url']='http://127.0.0.1:'+str(server.server_port)
    yield state
    server.shutdown();server.server_close();thread.join()

def provider(state,kind,monkeypatch,**kw):
    monkeypatch.setenv('MEETING_COPILOT_API_KEY','fake-contract-key')
    return Provider(Config(provider=kind,model='claude-3-5-sonnet-20241022' if kind=='anthropic' else 'gpt-4o-mini',base_url=state['url']+('/v1' if kind=='openai' else ''),send_evidence=True,**kw))

@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_wire_protocol_roles_usage_cost(fake_http,kind,monkeypatch):
    p=provider(fake_http,kind,monkeypatch,input_cost_per_million=1,output_cost_per_million=2)
    result=await p.complete('Why SQLite?',[{'id':1,'text':'SQLite is local.'}],{'name':'trace_decision'})
    assert result.input_tokens==17 and result.output_tokens==11 and result.estimated_cost_usd==39/1e6
    assert validated_claims(result.content,[{'id':1,'text':'SQLite is local.'}])==[{'source_id':1,'quote':'SQLite is local.'}]
    request=fake_http['requests'][-1]
    if kind=='openai':
        assert request['path']=='/v1/chat/completions'
        assert request['payload']['messages'][0]['role']=='system'
        assert next(v for k,v in request['headers'].items() if k.lower()=='authorization')=='Bearer fake-contract-key'
    else:
        assert request['path']=='/v1/messages'
        assert request['payload']['system'] and request['payload']['messages'][0]['role']=='user'
        assert next(v for k,v in request['headers'].items() if k.lower()=='x-api-key')=='fake-contract-key'
    assert request['payload']['max_tokens']==800 and not request['payload'].get('stream',False)

@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_errors_redacted(fake_http,kind,monkeypatch):
    fake_http['status']=401;p=provider(fake_http,kind,monkeypatch)
    with pytest.raises(ProviderError) as e:await p.complete('secret question',[],{})
    assert 'SENSITIVE' not in str(e.value) and 'fake-contract-key' not in str(e.value)
    assert e.value.code in ('authentication','provider_error')

@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_timeout_and_cancellation(fake_http,kind,monkeypatch):
    fake_http['delay']=.5;p=provider(fake_http,kind,monkeypatch,timeout_seconds=.1)
    with pytest.raises(ProviderError) as e:await p.complete('q',[],{})
    assert e.value.code=='timeout'
    p.config.timeout_seconds=2
    task=asyncio.create_task(p.complete('q',[],{}));await asyncio.sleep(.05);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task

async def test_no_key_no_upload_opt_in(monkeypatch):
    monkeypatch.delenv('MEETING_COPILOT_API_KEY',raising=False)
    p=Provider(Config(provider='openai'))
    with pytest.raises(ProviderError,match='evidence_upload_not_enabled'):await p.complete('q',[],{})
    p.config.send_evidence=True
    with pytest.raises(ProviderError,match='missing_api_key'):await p.complete('q',[],{})


def test_quote_validation_rejects_invented_and_wrong_source():
    assert validated_claims('{"claims":[{"source_id":9,"quote":"SQLite is local."},{"source_id":1,"quote":"invented"}]}',[{'id':1,'text':'SQLite is local.'}])==[]

@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_stream_text_usage_and_synthesis(fake_http,kind,monkeypatch):
    p=provider(fake_http,kind,monkeypatch,streaming=True,input_cost_per_million=1,output_cost_per_million=2)
    reply=await p.complete('Why?',[{'id':1,'text':'SQLite is local.'}],{'name':'direct'})
    assert reply.content==fake_http.get('content','{"claims":[{"source_id":1,"quote":"SQLite is local."}]}')
    assert reply.input_tokens==17 and reply.output_tokens==11
    assert reply.estimated_cost_usd==39/1e6
    assert fake_http['requests'][0]['payload']['stream'] is True


@pytest.mark.parametrize('kind',['openai','anthropic'])
@pytest.mark.parametrize('streaming',[False,True])
async def test_tool_round_normalization_host_scope_and_usage(fake_http,kind,streaming,monkeypatch,store,event):
    from meeting_copilot.model_tools import EvidenceTools
    store.ingest('alice','m',{**event,'text':'SQLite is local.'});sid=store.snapshot('alice','m')
    fake_http['tool']={'name':'search_evidence','arguments':{'query':'SQLite'}}
    p=provider(fake_http,kind,monkeypatch,streaming=streaming,tool_rounds=1)
    reply=await p.complete('Why SQLite?',[],{'name':'direct'},tool_context=EvidenceTools(store,'alice',sid))
    assert len(fake_http['requests'])==2
    assert reply.input_tokens==34 and reply.output_tokens==22
    assert reply.tool_events==[{'name':'search_evidence','status':'ok'}]
    assert reply.evidence[0]['text']=='SQLite is local.'
    messages=fake_http['requests'][1]['payload']['messages']
    if kind=='openai':
        assert messages[-1]['role']=='tool' and messages[-1]['tool_call_id']=='call-fixture'
        assert messages[-2]['tool_calls'][0]['function']['name']=='search_evidence'
    else:
        assert messages[-1]['role']=='user'
        assert messages[-1]['content'][0]['type']=='tool_result'
        assert messages[-1]['content'][0]['tool_use_id']=='call-fixture'
        assert messages[-2]['content'][0]['type']=='tool_use'


@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_model_identity_injection_is_denied_over_wire(fake_http,kind,monkeypatch,store,event):
    from meeting_copilot.model_tools import EvidenceTools
    store.ingest('alice','m',event);sid=store.snapshot('alice','m')
    fake_http['tool']={'name':'search_evidence','arguments':{'query':'SQLite','user':'bob','snapshot':'forged'}}
    p=provider(fake_http,kind,monkeypatch,tool_rounds=1)
    result=await p.complete('q',[],{},tool_context=EvidenceTools(store,'alice',sid))
    assert result.tool_events[0]['status']=='denied' and result.evidence==[]


@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_stream_timeout_and_cancel_close(fake_http,kind,monkeypatch):
    fake_http['stream_delay']=.4
    p=provider(fake_http,kind,monkeypatch,streaming=True,timeout_seconds=.15)
    with pytest.raises(ProviderError,match='timeout'):await p.complete('q',[],{})
    p.config.timeout_seconds=3
    task=asyncio.create_task(p.complete('q',[],{}));await asyncio.sleep(.1);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task


async def test_anthropic_midstream_error_redacted(fake_http,monkeypatch):
    fake_http['stream_error']=True
    p=provider(fake_http,'anthropic',monkeypatch,streaming=True)
    with pytest.raises(ProviderError) as error:await p.complete('q',[],{})
    assert 'SENSITIVE' not in str(error.value)

@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_native_upstream_service_replay_harness_over_wire(fake_http,kind,monkeypatch,tmp_path):
    from meeting_copilot.native_baseline import run_native
    fake_http['content']='{"respond":true,"kind":"information","text":"SQLite is local.","confidence":0.5,"related_segment_ids":[],"memory_summary":"SQLite"}'
    p=provider(fake_http,kind,monkeypatch)
    output=tmp_path/'native-contract.json'
    result=await run_native(p.config,tmp_path/'native',output)
    report=json.loads(output.read_text())
    assert result['questions']==22 and len(report['cases'])==22
    assert len(fake_http['requests'])==22
    assert report['api_usage']['input_tokens']==22*17
    assert report['api_usage']['output_tokens']==22*11
    request=fake_http['requests'][0]['payload']
    assert 'Meet2Notes Live AI Assistant' in json.dumps(request)
    assert 'QUESTION FROM USER' in json.dumps(request)
    assert all(c['answer']=='SQLite is local.' for c in report['cases'])


def test_opt_in_api_evaluator_runs_same_fixture_variants(fake_http,monkeypatch,tmp_path):
    from meeting_copilot.evaluation import evaluate
    p=provider(fake_http,'openai',monkeypatch,input_cost_per_million=1,output_cost_per_million=2)
    target=tmp_path/'api-evaluation.json'
    evaluate(target,tmp_path/'eval',config=p.config)
    result=json.loads(target.read_text())
    assert len(fake_http['requests'])==88
    assert result['api_usage']=={'input_tokens':88*17,'output_tokens':88*11,'estimated_cost_usd':pytest.approx(88*39/1e6)}
    assert result['mode'].startswith('API evaluation')
    assert result['configurations']['no_policy']['unauthorized_disclosures']==3
    assert result['native_meet2notes_baseline']['status']=='not measured'
    assert 'not semantic' in result['mode']

@pytest.mark.parametrize('streaming',[False,True])
async def test_missing_provider_usage_is_unknown_not_fabricated(fake_http,monkeypatch,streaming):
    fake_http['omit_usage']=True
    p=provider(fake_http,'openai',monkeypatch,streaming=streaming,input_cost_per_million=1,output_cost_per_million=2)
    result=await p.complete('Why SQLite?',[{'id':1,'text':'SQLite is local.'}],{})
    assert result.content
    assert result.input_tokens is None and result.output_tokens is None and result.estimated_cost_usd is None
