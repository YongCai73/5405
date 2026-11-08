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
    state={'requests':[],'status':200,'delay':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['content-length'])))
            state['requests'].append({'path':self.path,'payload':payload,'headers':dict(self.headers)})
            time.sleep(state['delay'])
            if state['status']!=200:body={'error':{'type':'api_error','message':'SENSITIVE-FAKE-SERVER-ERROR'}}
            elif self.path.endswith('/messages'):
                body={'id':'msg-fixture','type':'message','role':'assistant','model':payload['model'],'content':[{'type':'text','text':'{"claims":[{"source_id":1,"quote":"SQLite is local."}]}'}],'stop_reason':'end_turn','stop_sequence':None,'usage':{'input_tokens':17,'output_tokens':11}}
            else:
                body={'id':'chat-fixture','object':'chat.completion','created':1,'model':payload['model'],'choices':[{'index':0,'message':{'role':'assistant','content':'{"claims":[{"source_id":1,"quote":"SQLite is local."}]}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':17,'completion_tokens':11,'total_tokens':28}}
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
