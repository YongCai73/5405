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
                good=await session.call_tool('read_evidence',{'source_id':rid});assert not good.is_error and 'SQLite' in json.dumps(good.model_dump())
                store.grant('owner','m','viewer',False)
                for tool,args in [('get_transcript',{}),('get_summary',{}),('list_project_files',{}),('meet2notes_status',{}),('find_in_transcripts',{'query':'SQLite'})]:
                    result=await session.call_tool(tool,args);assert result.is_error
    finally:store.close()
