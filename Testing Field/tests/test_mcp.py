import json
import os
import sys
import pytest
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from meeting_copilot.store import Store

async def test_actual_stdio_tools_scope_and_revocation(tmp_path,event):
    config=tmp_path/'host.json';config.write_text(json.dumps({'principal':'viewer','data_dir':'runtime'}))
    root=tmp_path/'runtime';store=Store(root/'evidence.db');store.create('owner','m','Chosen');rid=store.ingest('owner','m',event);store.create('owner','other','NOT-OPTED-IN')
    secret=store.ingest('owner','other',{**event,'text':'OTHER-CANARY'});store.grant('owner','m','viewer');store.grant('owner','other','viewer');sid=store.snapshot('viewer','m')
    params=StdioServerParameters(command=sys.executable,args=['-m','meeting_copilot','--config',str(config),'mcp',sid],env=dict(os.environ))
    try:
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                initialized=await session.initialize();assert initialized.server_info.name=='Meeting Copilot'
                listed=await session.list_tools();tools={t.name:t for t in listed.tools}
                assert len(tools)==10
                for t in tools.values():
                    schema=t.input_schema
                    assert not ({'user','principal','snapshot','root','command'} & set(schema.get('properties',{})))
                    assert t.annotations.read_only_hint and not t.annotations.destructive_hint
                result=await session.call_tool('list_meetings',{})
                text=json.dumps(result.model_dump());assert 'Chosen' in text and 'NOT-OPTED-IN' not in text
                bad=await session.call_tool('read_evidence',{'source_id':secret});assert bad.is_error
                spoofed=await session.call_tool('read_evidence',{'source_id':secret,'principal':'owner'});assert spoofed.is_error
                good=await session.call_tool('read_evidence',{'source_id':rid});assert not good.is_error and 'SQLite' in json.dumps(good.model_dump())
                future=store.ingest('owner','m',{**event,'event_id':'late-final','seq':2,'revision':2,'text':'FUTURE-FINAL-CANARY','final':True})
                late=await session.call_tool('read_evidence',{'source_id':future});assert late.is_error
                pinned=await session.call_tool('get_transcript',{'offset':0})
                assert 'SQLite' in json.dumps(pinned.model_dump()) and 'FUTURE-FINAL-CANARY' not in json.dumps(pinned.model_dump())
                store.grant('owner','m','viewer',False)
                for tool,args in [('get_transcript',{}),('get_summary',{}),('list_project_files',{}),('meet2notes_status',{}),('find_in_transcripts',{'query':'SQLite'})]:
                    result=await session.call_tool(tool,args);assert result.is_error
    finally:store.close()


async def test_stdio_session_bound_to_a_skill_allows_only_its_tools_and_traces_refusals(tmp_path,event):
    config=tmp_path/'host.json';config.write_text(json.dumps({'principal':'alice','data_dir':'runtime'}))
    root=tmp_path/'runtime';store=Store(root/'evidence.db');store.create('alice','m','Chosen');rid=store.ingest('alice','m',{**event,'final':True});sid=store.snapshot('alice','m')
    params=StdioServerParameters(command=sys.executable,args=['-m','meeting_copilot','--config',str(config),'mcp',sid,'--skill','catch_up'],env=dict(os.environ))
    try:
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools)==10
                good=await session.call_tool('get_transcript',{'offset':0});assert not good.is_error and 'SQLite' in json.dumps(good.model_dump())
                # A registered tool outside the bound skill is refused by the gateway.
                for tool,args in [('search_project',{'query':'SQLite'}),('find_in_transcripts',{'query':'SQLite'}),('list_project_files',{})]:
                    assert (await session.call_tool(tool,args)).is_error
                # Out-of-range argument: rejected by the SDK schema before the gateway (no trace row).
                assert (await session.call_tool('get_transcript',{'offset':-5})).is_error
                # The MCP SDK drops undeclared arguments before the tool body runs, so a forged
                # scope argument is not an error on this transport; it simply has no effect and
                # the read stays inside the host-bound snapshot. (The in-process gateway and the
                # model tool surface reject unknown arguments outright; see test_skill_runtime.)
                forged=await session.call_tool('read_evidence',{'source_id':rid,'snapshot':'another','principal':'owner'})
                assert not forged.is_error and json.loads(forged.content[0].text)['id']==rid
    finally:store.close()
    rows=[json.loads(line) for line in (root/'traces.jsonl').read_text(encoding='utf-8').splitlines()]
    calls=[r for r in rows if r['event']=='tool_call']
    assert [(r['tool'],r['policy']) for r in calls if r['policy']=='allow']==[('get_transcript','allow'),('read_evidence','allow')]
    assert [(r['tool'],r['reason']) for r in calls if r['policy']=='deny']==[(t,'tool_not_allowed_for_skill') for t in ('search_project','find_in_transcripts','list_project_files')]
    assert all(r['tool_surface']=='mcp' and r['skill']=='catch_up' and r['task_id'].startswith('mcp-') and r['snapshot_id']==sid for r in calls)
    assert len({r['task_id'] for r in calls})==1 and 'SQLite' not in json.dumps(calls)
