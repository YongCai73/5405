import hashlib
import pytest
from meeting_copilot.service import EvidenceService
from meeting_copilot.project import safe_relative,approved_root
from meeting_copilot.store import Denied


def test_refresh_retains_old_hash_lines_and_deleted_files(store,tmp_path):
    p=tmp_path/'source folder';p.mkdir();f=p/'a.py';f.write_text('alpha\nbeta\n',encoding='utf-8')
    before=hashlib.sha256(f.read_bytes()).hexdigest();fid=store.approve('alice','m',p);store.refresh('alice',fid)
    sid=store.snapshot('alice','m',folders=[fid]);s=EvidenceService(store,'alice',sid);rid=s.list_project_files()[0]['id']
    assert hashlib.sha256(f.read_bytes()).hexdigest()==before
    assert s.read_project_file(rid,2,2)['text']=='beta'
    f.write_text('new\n',encoding='utf-8');store.refresh('alice',fid)
    assert s.read_project_file(rid)['text']=='alpha\nbeta'
    assert s.read_project_file(rid)['sha256']==before
    ns=store.snapshot('alice','m',folders=[fid]);assert EvidenceService(store,'alice',ns).list_project_files()[0]['hash']!=before
    f.unlink();store.refresh('alice',fid);assert EvidenceService(store,'alice',store.snapshot('alice','m',folders=[fid])).list_project_files()==[]
    assert s.read_project_file(rid)['text']=='alpha\nbeta'
    store.revoke_folder('alice',fid)
    for call in [s.list_project_files,lambda:s.search_project('alpha'),lambda:s.read_project_file(rid),lambda:store.export('alice',sid,'default')]:
        with pytest.raises(Denied):call()


def test_excluded_paths_and_source_unchanged(store,tmp_path):
    p=tmp_path/'approved';p.mkdir();outside=tmp_path/'outside';outside.mkdir();(outside/'secret.md').write_text('ESCAPE-CANARY')
    files={'.env':'SECRET','id_rsa':'PRIVATE','credentials.json':'SECRET','api_key.md':'SECRET','normal.md':'okay','binary.txt':'a\x00b','big.txt':'a'*131073}
    for name,text in files.items():(p/name).write_text(text,encoding='utf-8')
    (p/'.git').mkdir();(p/'.git'/'config.txt').write_text('hidden')
    try:(p/'linked.md').symlink_to(outside/'secret.md');(p/'linked-dir').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('symlink creation requires platform permission')
    hashes={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file()}
    fid=store.approve('alice','m',p);store.refresh('alice',fid);sid=store.snapshot('alice','m',folders=[fid]);svc=EvidenceService(store,'alice',sid)
    assert [x['path'] for x in svc.list_project_files()]==['normal.md']
    assert hashes=={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file()}
    for path in ['../outside/a','/etc/passwd','C:\\a','a/../../b','a\\b']:
        with pytest.raises(ValueError):safe_relative(path)
    with pytest.raises(ValueError):approved_root(p/'linked-dir')


def test_folder_scope_and_read_bounds(store,tmp_path):
    p=tmp_path/'p';p.mkdir();(p/'a.md').write_text('abc')
    f=store.approve('alice','m',p);store.refresh('alice',f);s=EvidenceService(store,'alice',store.snapshot('alice','m'))
    assert s.list_project_files()==[]
    fs=EvidenceService(store,'alice',store.snapshot('alice','m',folders=[f]));rid=fs.list_project_files()[0]['id']
    with pytest.raises(Denied):s.read_project_file(rid)
    with pytest.raises(ValueError):fs.read_project_file(rid,1,500)
    with pytest.raises(ValueError):fs.list_project_files(1001)
    with pytest.raises(Denied):store.refresh('bob',f)
