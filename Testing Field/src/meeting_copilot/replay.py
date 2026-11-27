import json
from importlib.resources import files

def fixture():return json.loads(files('meeting_copilot').joinpath('fixtures.json').read_text(encoding='utf-8'))

def events():
    for raw in fixture()['events']:
        yield {'revision':1,'final':True,'event_id':'fixture-'+str(raw['seq']),'stream':'fixture',**raw}

def import_jsonl(store,user,meeting,path):
    count=0
    with open(path,encoding='utf-8') as f:
        for line in f:
            if not line.strip():continue
            store.ingest(user,meeting,json.loads(line));count+=1
    return count

def seed(store,user,root,meeting='demo'):
    if meeting not in [m['id'] for m in store.list_meetings(user)]:store.create(user,meeting,'Atlas technical design (SIMULATED REPLAY)')
    for event in events():store.ingest(user,meeting,event)
    project=root.parent/'demo-project';project.mkdir(parents=True,exist_ok=True)
    for name,text in fixture()['project'].items():
        p=project/name
        if not p.exists():p.write_text(text,encoding='utf-8')
    fid=store.approve(user,meeting,project);store.refresh(user,fid)
    return fid
