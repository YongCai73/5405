"""Synthetic scale check; logical meeting duration is not wall-clock stability."""
import asyncio,hashlib,json,platform,time
from pathlib import Path
import meeting_copilot
from meeting_copilot.store import Store,Denied
from meeting_copilot.assistant import Assistant
from meeting_copilot.provider import Provider
from meeting_copilot.config import Config
from meeting_copilot.trace import Trace
import argparse
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--concurrent-load-label',default='unspecified');args=parser.parse_args()
root=args.work;root.mkdir(parents=True,exist_ok=False)
store=Store(root/'runtime/evidence.db');store.create('scale','long','SYNTHETIC scale fixture')
t0=time.perf_counter()
for i in range(1200):
 text=f'Kafka proposed for iteration {i}.' if i<1199 else 'Kafka was rejected because migration overhead exceeds the maintenance budget.'
 store.ingest('scale','long',{'event_id':f'e{i}','segment_id':f's{i}','revision':1,'seq':i+1,'start_ms':i*3000,'end_ms':i*3000+2500,'text':text})
project=root/'project';project.mkdir()
for i in range(299):(project/f'module_{i:03}.py').write_text('# Common deployment worker settings\nQUEUE_CAPACITY = 4\n')
text=''.join(f'# filler line {i:04} retained source context\n' for i in range(3000))+'MAX_RETRY_BUDGET = 937\n'
assert len(text.encode())<=131072
(project/'zz_critical.py').write_text(text)
hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in project.iterdir()}
f=store.approve('scale','long',project);refresh=store.refresh('scale',f);sid=store.snapshot('scale','long',folders=[f])
setup_ms=(time.perf_counter()-t0)*1000
a=Assistant(store,'scale',Provider(Config()),Trace(root/'runtime/trace.jsonl'));answers=[]
for question,skill in [('Why was Kafka rejected due to migration overhead?','trace_decision'),('What is MAX_RETRY_BUDGET?','direct'),('Catch up','catch_up')]:
 started=time.perf_counter();result=asyncio.run(a.ask(sid,question,skill=skill));answers.append({'question':question,'result':result,'graph_wall_ms':(time.perf_counter()-started)*1000})
assert 'maintenance budget' in answers[0]['result']['text']
assert '937' in answers[1]['result']['text']
assert answers[1]['result']['citations'][0]['line_start']==3001
assert 'maintenance budget' in answers[2]['result']['text']
export=store.export('scale',sid,'default');ids={e['id'] for e in export['evidence']}
assert all(c['source_id'] in ids for r in answers for c in r['result']['claims'])
assert hashes=={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in project.iterdir()}
asyncio.run(a.summary_graph.ainvoke({'snapshot':sid}));assert 'maintenance budget' in store.summary_get('scale',sid)['text']
store.revoke_folder('scale',f)
try:store.export('scale',sid,'default');raise AssertionError('revoked export allowed')
except Denied:pass
result={'mode':'SYNTHETIC scale/lookup check, not 60-minute wall-clock or semantic model evaluation','package_version':meeting_copilot.__version__,'platform':platform.platform(),'transcript_segments':1200,'logical_meeting_seconds':3600,'project_files':refresh['files'],'target_file_bytes':len(text.encode()),'target_line':3001,'setup_ms':setup_ms,'source_hashes_unchanged':True,'revoked_export_denied':True,'cited_sources_in_export':True,'answers':answers,'concurrent_load':args.concurrent_load_label}
args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');store.close();print(json.dumps({k:v for k,v in result.items() if k!='answers'}))
