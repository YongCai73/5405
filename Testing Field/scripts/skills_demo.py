"""Reproducible MCP + Skills demonstration on the bundled synthetic replay.

    python scripts/skills_demo.py            (run from the application directory)

It uses a fresh temporary data directory and the local extractive provider, so it
needs no API key, sends nothing over the network and does not touch `config.json`
or `data/`. Every step goes through the real CLI or a real stdio MCP session.

  Demo 1  auto-select a skill -> host-controlled retrieval -> cited answer -> correlated trace
  Demo 2  illegal calls are refused: a tool outside the bound skill over stdio MCP, and
          an unknown tool / forged identity argument on the model tool surface
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ENV = {**os.environ, 'PYTHONUTF8': '1'}


def show(title, value):
    print(f'\n=== {title} ===')
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main():
    work = Path(tempfile.mkdtemp(prefix='meeting-copilot-skills-demo-'))
    config = work/'config.json'
    config.write_text(json.dumps({'principal': 'local-host', 'data_dir': 'runtime'}), encoding='utf-8')

    def cli(*args):
        done = subprocess.run([sys.executable, '-m', 'meeting_copilot', '--config', str(config), *map(str, args)],
                              capture_output=True, text=True, encoding='utf-8', cwd=APP, env=ENV)
        if done.returncode:
            raise SystemExit(done.stderr)
        return json.loads(done.stdout)

    cli('create', 'design', '--title', 'Skills demo (SIMULATED replay)')
    cli('replay', 'design', APP/'examples'/'replay.jsonl')
    folder = cli('approve-folder', 'design', APP/'examples'/'project')['folder_id']
    cli('refresh-folder', folder)

    question = 'Why was Kafka rejected?'
    show('Demo 1a: routing decision (no evidence read, no model call)', cli('route', question))
    answer = cli('ask', 'design', question, '--folder', folder, '--conversation', 'demo')
    show('Demo 1b: auto-routed, cited answer', {k: answer[k] for k in (
        'task_id', 'snapshot_id', 'mode', 'skill', 'skill_version', 'text', 'citations', 'abstained', 'validation_report')}
        | {'routing': {k: answer['routing'][k] for k in ('requested', 'status', 'selected', 'scores', 'matched')}})
    show('Demo 1c: trace events of that task (trace-show --task)', cli('trace-show', '--task', answer['task_id']))

    async def mcp_session():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable, env=ENV, cwd=str(APP),
            args=['-m', 'meeting_copilot', '--config', str(config), 'mcp', answer['snapshot_id'], '--skill', 'catch_up'])
        with open(os.devnull, 'w') as quiet:
            async with stdio_client(params, errlog=quiet) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    rows = []
                    for tool, args in [('get_transcript', {'offset': 0}), ('search_project', {'query': 'SQLite'}),
                                       ('find_in_transcripts', {'query': 'Kafka'})]:
                        result = await session.call_tool(tool, args)
                        rows.append({'tool': tool, 'refused': bool(result.is_error)})
                    return rows

    show('Demo 2a: stdio MCP session bound to catch_up (search tools are outside its allowlist)', asyncio.run(mcp_session()))
    events = [e for e in cli('trace-show', '--snapshot', answer['snapshot_id']) if e['event'] == 'tool_call']
    show('Demo 2b: the same calls in the local trace (decision and reason code, no arguments)', events)

    from meeting_copilot.model_tools import EvidenceTools
    from meeting_copilot.skill_runtime import load_contract, policy_for
    from meeting_copilot.store import Store
    store = Store(work/'runtime'/'evidence.db')
    try:
        tools = EvidenceTools(store, 'local-host', answer['snapshot_id'], policy=policy_for(load_contract('catch_up'), 'model'))
        show('Demo 2c: model tool surface under catch_up', [
            {'call': 'delete_meeting {}', **tools.call('delete_meeting', '{}')},
            {'call': 'search_evidence (not allowed for catch_up)', **tools.call('search_evidence', json.dumps({'query': 'Kafka'}))},
            {'call': 'read_evidence with forged principal', **tools.call('read_evidence', json.dumps({'source_id': 1, 'start_line': 1, 'end_line': 1, 'principal': 'someone-else'}))},
        ])
    finally:
        store.close()
    print(f'\nDemo data directory (safe to delete): {work}')


if __name__ == '__main__':
    main()
