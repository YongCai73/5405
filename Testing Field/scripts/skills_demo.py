"""Reproducible MCP + Skills demonstration on the bundled synthetic replay.

    python scripts/skills_demo.py            (run from the application directory)

It uses a fresh temporary data directory and the local extractive provider, so it
needs no API key, sends nothing over the network and does not touch `config.json`
or `data/`. Every step goes through the real CLI or a real stdio MCP session.

  Demo 1  auto-select a skill -> host-controlled retrieval -> cited answer -> correlated trace
  Demo 2  illegal calls are refused: a tool outside the bound skill over stdio MCP, and
          an unknown tool / forged identity argument on the model tool surface
  Demo 3  discussion skills: clarify with term memory, a multi-turn grill_me session, design_review
  Demo 4  feedback and a user-triggered suggestion report built from the user's own history

In this local mode the answers are extracted sentences. Explanations, critiques and
failure scenarios need a configured model; the demo shows the flow and state, not quality.
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

    # Demo 3: the discussion skills. Each `ask` takes a new snapshot; state lives in the
    # private conversation, not in the process.
    def brief(result):
        keep = {k: result[k] for k in ('skill', 'text') if k in result}
        keep['route'] = result['routing']['status']
        for key in ('explained_terms', 'previously_explained', 'session'):
            if key in result:
                keep[key] = result[key]
        return keep

    show('Demo 3a: clarify, first time a term comes up', brief(cli('ask', 'design', 'Explain the bounded queue', '--conversation', 'learn')))
    show('Demo 3b: clarify again in the same conversation (the term is remembered)', brief(cli('ask', 'design', 'Clarify the queue capacity', '--conversation', 'learn')))
    show('Demo 3c: grill_me, round 1 (one question, then it waits)', brief(cli('ask', 'design', 'Grill me on the SQLite decision', '--conversation', 'grill')))
    show('Demo 3d: grill_me, round 2 (the answer stays in the session although it says "because" and "compare")',
         brief(cli('ask', 'design', 'Because there is no broker to compare or operate', '--conversation', 'grill')))
    show('Demo 3e: grill_me closed by the user', brief(cli('ask', 'design', 'stop', '--conversation', 'grill')))
    show('Demo 3f: design_review (quotations are meeting facts; suggestions need a model)',
         brief(cli('ask', 'design', 'What could go wrong with the bounded queue design?', '--folder', folder, '--conversation', 'review')))

    # Demo 4: meta-suggestion on this user's own history. Three "who owns" questions and two
    # "briefly" requests give the rules something that repeats.
    for question in ('Who owns microphone capture? briefly', 'Who owns security policy? briefly', 'Who owns evaluation?',
                     'Ask me hard questions about the timeout', 'Challenge the retry setting'):
        last = cli('ask', 'design', question, '--conversation', 'habits')
    cli('feedback', last['snapshot_id'], 'Challenge the retry setting', '--rating', 'down', '--note', 'I wanted to be questioned, not answered', '--conversation', 'habits')
    report = cli('suggest', 'design')
    show('Demo 4: suggest (user-triggered; rules only; nothing is changed)', {
        'mode': report['mode'], 'scope': report['scope'], 'signals': report['signals'],
        'suggestions': [{k: v for k, v in item.items() if k != 'specification'} | (
            {'specification': {'draft_contract_name': item['specification']['draft_contract']['name'],
                               'draft_trigger_phrases': item['specification']['draft_contract']['routing']['phrases'],
                               'test_cases': item['specification']['test_cases']}} if 'specification' in item else {})
            for item in report['suggestions']],
        'activation': report['activation'], 'privacy': report['privacy']})
    print(f'\nDemo data directory (safe to delete): {work}')


if __name__ == '__main__':
    main()
