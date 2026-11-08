"""Exercise documented CLI commands against a separate installed interpreter."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--python',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.work.mkdir(parents=True,exist_ok=False);config=a.work/'config.json';app=Path(__file__).resolve().parents[1];commands=[]
    interpreter=str(a.python.absolute())  # Preserve venv interpreter symlink path.
    def cli(*args,ok=True):
        result=subprocess.run([interpreter,'-m','meeting_copilot','--config',str(config.resolve()),*map(str,args)],capture_output=True,text=True,timeout=90)
        commands.append({'args':list(map(str,args)),'exit_code':result.returncode,'expected_success':ok})
        assert (result.returncode==0)==ok,(args,result.stderr)
        return json.loads(result.stdout) if ok else result.stderr
    project=app/'examples/project';before={str(f.relative_to(project)):hashlib.sha256(f.read_bytes()).hexdigest() for f in project.rglob('*') if f.is_file()}
    cli('init');demo=cli('demo');assert demo['result']['claims']
    cli('create','design','--title','Audit design')
    cli('replay','design',app/'examples/replay.jsonl')
    folder=cli('approve-folder','design',project)['folder_id'];cli('refresh-folder',folder)
    result=cli('ask','design','Compare SQLite and Kafka deployment','--folder',folder,'--skill','compare_options','--conversation','notes');sid=result['snapshot_id']
    assert result['claims'] and any(c['kind']=='file' for c in result['citations'])
    follow=cli('ask','design','Why was it chosen?','--folder',folder,'--conversation','notes')
    assert follow['conversation_context'] and cli('history',follow['snapshot_id'],'--conversation','notes','--across-snapshots')
    assert cli('checkpoint',sid,'--conversation','notes')['result']['claims']
    cli('summary',sid);assert cli('live-status','design')['latest_shared_summary']
    export=a.work/'answer.json';cli('export',sid,export,'--conversation','notes');saved=json.loads(export.read_text())
    assert {c['source_id'] for t in saved['private'] for c in t['answer']['claims']}.issubset({r['id'] for r in saved['evidence']})
    cli('trace-export',a.work/'trace-export.jsonl')
    eval_result=cli('evaluate','--output',a.work/'eval.json','--stress');assert eval_result['skills_correct']==22
    cli('native-baseline','--output',a.work/'not-run.json',ok=False)
    cli('approve-folder','design',a.work,ok=False)
    cli('revoke-folder',folder);cli('export',sid,a.work/'denied.json',ok=False);assert not (a.work/'denied.json').exists()
    cli('cleanup','design','--confirm','design');cli('cleanup','demo','--confirm','demo');assert cli('list')==[]
    after={str(f.relative_to(project)):hashlib.sha256(f.read_bytes()).hexdigest() for f in project.rglob('*') if f.is_file()};assert before==after
    check=subprocess.run([interpreter,'-m','pip','check'],capture_output=True,text=True,timeout=30);assert check.returncode==0,check.stdout
    capabilities=subprocess.run([interpreter,'-c',"import importlib.util,importlib.metadata,json,meeting_copilot; assert meeting_copilot.__version__==importlib.metadata.version('meeting-copilot-prototype'); print(json.dumps({'faster_whisper_installed':importlib.util.find_spec('faster_whisper') is not None,'package_version':meeting_copilot.__version__}))"],capture_output=True,text=True,check=True)
    report={'mode':'clean installed wheel CLI smoke; demo is synthetic; no live endpoint or microphone','commands':commands,'source_project_hashes_unchanged':before==after,'source_hashes':before,'pip_check':check.stdout.strip(),**json.loads(capabilities.stdout),'native_baseline_demo_rejected':True,'result':'passed'}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':'passed','commands':len(commands),'output':str(a.output)}))


if __name__=='__main__':main()
