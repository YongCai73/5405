"""Authoritative append-only evidence; policy is checked on every public read."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

class Denied(PermissionError): pass

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())[:80]

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(path).resolve().parent
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meetings(id TEXT PRIMARY KEY,title TEXT,owner TEXT);
        CREATE TABLE IF NOT EXISTS acl(meeting TEXT REFERENCES meetings ON DELETE CASCADE,user TEXT,PRIMARY KEY(meeting,user));
        CREATE TABLE IF NOT EXISTS folders(id TEXT PRIMARY KEY,meeting TEXT REFERENCES meetings ON DELETE CASCADE,root TEXT,owner TEXT,active INTEGER);
        CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,meeting TEXT REFERENCES meetings ON DELETE CASCADE,kind TEXT,logical TEXT,revision INTEGER,text TEXT,hash TEXT,start_ms INTEGER,end_ms INTEGER,final INTEGER,folder TEXT,path TEXT,observed_at REAL,UNIQUE(meeting,kind,logical,revision));
        CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(text);
        CREATE TRIGGER IF NOT EXISTS ev_insert AFTER INSERT ON evidence BEGIN INSERT INTO evidence_fts(rowid,text) VALUES(new.id,new.text); END;
        CREATE TRIGGER IF NOT EXISTS ev_delete AFTER DELETE ON evidence BEGIN DELETE FROM evidence_fts WHERE rowid=old.id; END;
        CREATE TABLE IF NOT EXISTS events(meeting TEXT REFERENCES meetings ON DELETE CASCADE,event TEXT,stream TEXT,seq INTEGER,hash TEXT,evidence INTEGER,PRIMARY KEY(meeting,event),UNIQUE(meeting,stream,seq));
        CREATE TABLE IF NOT EXISTS manifests(id INTEGER PRIMARY KEY AUTOINCREMENT,folder TEXT REFERENCES folders ON DELETE CASCADE,watermark INTEGER,refs TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,user TEXT,meeting TEXT REFERENCES meetings ON DELETE CASCADE,watermark INTEGER,meetings TEXT,folders TEXT,refs TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS private(snapshot TEXT REFERENCES snapshots ON DELETE CASCADE,user TEXT,conversation TEXT,question TEXT,answer TEXT,PRIMARY KEY(snapshot,user,conversation,question));
        CREATE TABLE IF NOT EXISTS checkpoints(snapshot TEXT REFERENCES snapshots ON DELETE CASCADE,user TEXT,conversation TEXT,state TEXT,PRIMARY KEY(snapshot,user,conversation));
        CREATE TABLE IF NOT EXISTS summaries(snapshot TEXT REFERENCES snapshots ON DELETE CASCADE,meeting TEXT REFERENCES meetings ON DELETE CASCADE,refs TEXT,text TEXT,PRIMARY KEY(snapshot,meeting));
        ''')
        self.db.commit()

    @contextmanager
    def tx(self):
        with self.lock:
            if self.db.in_transaction:
                yield self.db
            else:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    yield self.db

    def close(self): self.db.close()

    def _check(self, user, meeting, owner=False):
        row=self.db.execute('SELECT m.* FROM meetings m JOIN acl a ON a.meeting=m.id WHERE m.id=? AND a.user=?',(meeting,user)).fetchone()
        if not row or (owner and row['owner']!=user): raise Denied('meeting access denied')
        return dict(row)

    def create(self,user,meeting,title):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',meeting): raise ValueError('invalid meeting ID')
        with self.tx() as db:
            db.execute('INSERT INTO meetings VALUES(?,?,?)',(meeting,title[:200],user))
            db.execute('INSERT INTO acl VALUES(?,?)',(meeting,user))

    def grant(self,user,meeting,other,allow=True):
        with self.tx() as db:
            self._check(user,meeting,True)
            if not allow and other==user: raise ValueError('owner cannot revoke self; use cleanup')
            if allow: db.execute('INSERT OR IGNORE INTO acl VALUES(?,?)',(meeting,other))
            else: db.execute('DELETE FROM acl WHERE meeting=? AND user=?',(meeting,other))

    def list_meetings(self,user):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT m.* FROM meetings m JOIN acl a ON m.id=a.meeting WHERE a.user=? ORDER BY m.id LIMIT 50',(user,))]

    def metadata(self,user,meeting):
        with self.lock: return self._check(user,meeting)

    def ingest(self,user,meeting,event,*,kind='segment'):
        """Observed time/watermark assigned here, never accepted from an event."""
        required=('event_id','segment_id','revision','seq','start_ms','end_ms','text')
        if any(k not in event for k in required): raise ValueError('incomplete event')
        if kind not in ('segment','supplement'): raise ValueError('invalid kind')
        if not isinstance(event['text'],str) or not 0<len(event['text'])<=16000: raise ValueError('invalid text length')
        if not isinstance(event['revision'],int) or event['revision']<1 or event['seq']<1 or event['start_ms']<0 or event['end_ms']<event['start_ms']: raise ValueError('invalid event numbers')
        payload={k:event[k] for k in required};payload['final']=bool(event.get('final',False));payload['stream']=str(event.get('stream','replay'));payload['kind']=kind
        h=digest(json.dumps(payload,sort_keys=True))
        with self.tx() as db:
            self._check(user,meeting,True)
            old=db.execute('SELECT * FROM events WHERE meeting=? AND event=?',(meeting,event['event_id'])).fetchone()
            if old:
                if old['hash']!=h: raise ValueError('event ID collision')
                return old['evidence']
            rid=db.execute('INSERT INTO evidence(meeting,kind,logical,revision,text,hash,start_ms,end_ms,final,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(meeting,kind,event['segment_id'],event['revision'],event['text'],digest(event['text']),event['start_ms'],event['end_ms'],int(payload['final']),time.time())).lastrowid
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(meeting,event['event_id'],payload['stream'],event['seq'],h,rid))
            return rid

    def gaps(self,user,meeting):
        with self.lock:
            self._check(user,meeting)
            return [dict(r) for r in self.db.execute('SELECT stream,MAX(seq) AS highest,COUNT(*) AS received,MAX(seq)-COUNT(*) AS missing FROM events WHERE meeting=? GROUP BY stream',(meeting,))]

    def approve(self,user,meeting,root):
        from .project import approved_root
        root=approved_root(root)
        if root == self.root:raise ValueError('cannot index application data root')
        with self.tx() as db:
            self._check(user,meeting,True)
            fid=uuid.uuid4().hex
            db.execute('INSERT INTO folders VALUES(?,?,?,?,1)',(fid,meeting,str(root),user))
            return fid

    def _folder(self,user,fid):
        row=self.db.execute('SELECT * FROM folders WHERE id=?',(fid,)).fetchone()
        if not row or not row['active'] or row['owner']!=user: raise Denied('folder access denied')
        self._check(user,row['meeting'])
        return dict(row)

    def folders(self,user,meeting):
        with self.lock:
            self._check(user,meeting)
            return [dict(r) for r in self.db.execute('SELECT id,meeting,active FROM folders WHERE meeting=? AND owner=? AND active=1',(meeting,user))]

    def revoke_folder(self,user,fid):
        with self.tx() as db:
            self._folder(user,fid)
            db.execute('UPDATE folders SET active=0 WHERE id=?',(fid,))

    def refresh(self,user,fid):
        from .project import scan
        with self.tx() as db:
            f=self._folder(user,fid)
            contents=scan(Path(f['root']))
            refs=[]
            for path,text in contents:
                logical=fid+':'+path;h=digest(text)
                old=db.execute("SELECT * FROM evidence WHERE logical=? AND kind='file' ORDER BY revision DESC LIMIT 1",(logical,)).fetchone()
                if old and old['hash']==h: rid=old['id']
                else:
                    rid=db.execute('INSERT INTO evidence(meeting,kind,logical,revision,text,hash,folder,path,observed_at) VALUES(?,?,?,?,?,?,?,?,?)',(f['meeting'],'file',logical,old['revision']+1 if old else 1,text,h,fid,path,time.time())).lastrowid
                refs.append(rid)
            watermark=db.execute('SELECT COALESCE(MAX(id),0) FROM evidence').fetchone()[0]
            db.execute('INSERT INTO manifests(folder,watermark,refs) VALUES(?,?,?)',(fid,watermark,json.dumps(refs)))
            return {'files':len(refs),'watermark':watermark}

    def snapshot(self,user,meeting,*,previous=(),folders=()):
        with self.tx() as db:
            meetings=list(dict.fromkeys([meeting,*previous]))
            if len(meetings)>10 or len(folders)>10: raise ValueError('scope too large')
            for m in meetings: self._check(user,m)
            refs=[]
            for m in meetings:
                refs.extend(r[0] for r in db.execute("SELECT e.id FROM evidence e WHERE e.meeting=? AND kind IN ('segment','supplement') AND revision=(SELECT MAX(revision) FROM evidence v WHERE v.meeting=e.meeting AND v.kind=e.kind AND v.logical=e.logical)",(m,)))
            for fid in folders:
                f=self._folder(user,fid)
                if f['meeting'] not in meetings: raise Denied('folder outside snapshot meetings')
                manifest=db.execute('SELECT refs FROM manifests WHERE folder=? ORDER BY id DESC LIMIT 1',(fid,)).fetchone()
                if manifest: refs.extend(json.loads(manifest[0]))
            sid=uuid.uuid4().hex
            w=db.execute('SELECT COALESCE(MAX(id),0) FROM evidence').fetchone()[0]
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)',(sid,user,meeting,w,json.dumps(meetings),json.dumps(list(folders)),json.dumps(refs),time.time()))
            return sid

    def _snapshot(self,user,sid):
        row=self.db.execute('SELECT * FROM snapshots WHERE id=? AND user=?',(sid,user)).fetchone()
        if not row: raise Denied('snapshot access denied')
        for m in json.loads(row['meetings']): self._check(user,m)
        for f in json.loads(row['folders']): self._folder(user,f)
        return dict(row)

    def snapshot_info(self,user,sid):
        with self.lock:
            s=self._snapshot(user,sid)
            return {k:s[k] for k in ('id','meeting','watermark','created','meetings','folders','refs')}

    def evidence(self,user,sid,*,kind=None,query=None,limit=20,recent=False,meeting=None):
        with self.lock:
            s=self._snapshot(user,sid);refs=json.loads(s['refs'])
            if not refs: return []
            sql='SELECT e.* FROM evidence e JOIN json_each(?) j ON e.id=j.value WHERE 1=1';args=[json.dumps(refs)]
            if kind: sql+=' AND e.kind=?';args.append(kind)
            if meeting is not None:
                if meeting not in json.loads(s['meetings']):raise Denied('meeting outside snapshot')
                sql+=' AND e.meeting=?';args.append(meeting)
            if query:
                terms=tokens(query)
                if not terms:return []
                sql+=' AND e.id IN (SELECT rowid FROM evidence_fts WHERE evidence_fts MATCH ?)';args.append(' OR '.join('"'+t+'"' for t in terms))
            sql+=(' ORDER BY COALESCE(e.start_ms,0) DESC,e.id DESC LIMIT ?' if recent else ' ORDER BY COALESCE(e.start_ms,0),e.id LIMIT ?');args.append(max(1,min(limit,100)))
            result=[dict(r) for r in self.db.execute(sql,args)]
            return result[::-1] if recent else result

    def raw(self,user,sid,rid):
        with self.lock:
            s=self._snapshot(user,sid)
            if rid not in json.loads(s['refs']): raise Denied('source outside snapshot')
            r=self.db.execute('SELECT * FROM evidence WHERE id=?',(rid,)).fetchone()
            if not r: raise Denied('source removed')
            return dict(r)

    def private_put(self,user,sid,conversation,question,answer):
        with self.tx() as db:
            self._snapshot(user,sid)
            db.execute('INSERT OR REPLACE INTO private VALUES(?,?,?,?,?)',(sid,user,conversation,question,json.dumps(answer)))

    def private_get(self,user,sid,conversation,question=None):
        with self.lock:
            self._snapshot(user,sid)
            sql='SELECT question,answer FROM private WHERE snapshot=? AND user=? AND conversation=?';args=[sid,user,conversation]
            if question is not None:sql+=' AND question=?';args.append(question)
            return [{'question':r[0],'answer':json.loads(r[1])} for r in self.db.execute(sql,args)]

    def checkpoint_put(self,user,sid,conversation,state):
        with self.tx() as db:
            s=self._snapshot(user,sid)
            if state.get('snapshot')!=sid or not set(state.get('source_ids',[])).issubset(set(json.loads(s['refs']))):raise Denied('checkpoint scope mismatch')
            db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',(sid,user,conversation,json.dumps(state)))

    def checkpoint_get(self,user,sid,conversation):
        with self.lock:
            self._snapshot(user,sid)
            row=self.db.execute('SELECT state FROM checkpoints WHERE snapshot=? AND user=? AND conversation=?',(sid,user,conversation)).fetchone()
            return json.loads(row[0]) if row else None

    def promote(self,user,sid,conversation,question,meeting):
        with self.lock:
            s=self._snapshot(user,sid)
            if meeting!=s['meeting']: raise Denied('promotion meeting mismatch')
            result=self.private_get(user,sid,conversation,question)
            if not result: raise ValueError('private answer absent')
            n=uuid.uuid4().hex
            return self.ingest(user,meeting,{'event_id':n,'segment_id':n,'revision':1,'seq':1,'stream':n,'start_ms':0,'end_ms':0,'text':'USER SUPPLEMENT (unverified): '+result[0]['answer']['text'],'final':True},kind='supplement')

    def summary_put(self,user,sid,text,refs):
        with self.tx() as db:
            s=self._snapshot(user,sid)
            for rid in refs:
                e=self.raw(user,sid,rid)
                if e['kind']!='segment' or e['meeting']!=s['meeting']:raise Denied('shared summary requires current meeting transcripts')
            db.execute('INSERT OR REPLACE INTO summaries VALUES(?,?,?,?)',(sid,s['meeting'],json.dumps(refs),text))

    def summary_get(self,user,sid):
        with self.lock:
            self._snapshot(user,sid)
            r=self.db.execute('SELECT text,refs FROM summaries WHERE snapshot=?',(sid,)).fetchone()
            return {'text':r[0],'refs':json.loads(r[1])} if r else None

    def export(self,user,sid,conversation):
        with self.lock:
            return {'snapshot':self.snapshot_info(user,sid),'evidence':self.evidence(user,sid,limit=100),'private':self.private_get(user,sid,conversation),'summary':self.summary_get(user,sid),'truncated':len(json.loads(self._snapshot(user,sid)['refs']))>100}

    def cleanup(self,user,meeting):
        with self.tx() as db:
            self._check(user,meeting,True)
            ids=[r[0] for r in db.execute('SELECT id FROM snapshots WHERE EXISTS(SELECT 1 FROM json_each(snapshots.meetings) WHERE value=?)',(meeting,))]
            for sid in ids: db.execute('DELETE FROM snapshots WHERE id=?',(sid,))
            db.execute('DELETE FROM meetings WHERE id=?',(meeting,))
