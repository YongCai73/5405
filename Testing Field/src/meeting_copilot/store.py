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
    return re.findall(r"[a-z0-9]+", text.lower())

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(path).resolve().parent
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
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
        CREATE TABLE IF NOT EXISTS conversation_turns(id INTEGER PRIMARY KEY AUTOINCREMENT,snapshot TEXT REFERENCES snapshots ON DELETE CASCADE,user TEXT,conversation TEXT,question TEXT,answer TEXT,created REAL);
        CREATE INDEX IF NOT EXISTS conversation_turn_scope ON conversation_turns(user,conversation,id);
        CREATE TABLE IF NOT EXISTS snapshot_turn_watermarks(snapshot TEXT PRIMARY KEY REFERENCES snapshots ON DELETE CASCADE,watermark INTEGER);
        CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints(snapshot TEXT REFERENCES snapshots ON DELETE CASCADE,user TEXT,conversation TEXT,state TEXT,PRIMARY KEY(snapshot,user,conversation));
        CREATE TABLE IF NOT EXISTS summaries(snapshot TEXT REFERENCES snapshots ON DELETE CASCADE,meeting TEXT REFERENCES meetings ON DELETE CASCADE,refs TEXT,text TEXT,PRIMARY KEY(snapshot,meeting));
        ''')
        self.db.commit()
        # Additive v0.1 upgrade: preserve existing answers as prior turns. Old
        # snapshots have turn cutoff zero; only new snapshots may consume them.
        with self.tx() as db:
            if not db.execute("SELECT 1 FROM app_meta WHERE key='private_turn_backfill'").fetchone():
                db.execute('''INSERT INTO conversation_turns(snapshot,user,conversation,question,answer,created)
                    SELECT p.snapshot,p.user,p.conversation,p.question,p.answer,s.created
                    FROM private p JOIN snapshots s ON s.id=p.snapshot
                    WHERE NOT EXISTS(SELECT 1 FROM conversation_turns t WHERE t.snapshot=p.snapshot
                    AND t.user=p.user AND t.conversation=p.conversation AND t.question=p.question)
                    ORDER BY s.created,p.rowid''')
                db.execute("INSERT INTO app_meta VALUES('private_turn_backfill','1')")

    @contextmanager
    def tx(self):
        with self.lock:
            if self.db.in_transaction:
                yield self.db
            else:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    yield self.db

    @contextmanager
    def read(self):
        """One SQLite view for policy checks and data, including across processes.

        WAL readers do not block ingestion. A fresh call sees new revocations;
        an in-flight read is authorized against one coherent database state.
        """
        with self.lock:
            if self.db.in_transaction:
                yield self.db
            else:
                with self.db:
                    self.db.execute('BEGIN')
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
        with self.read():
            return [dict(r) for r in self.db.execute('SELECT m.* FROM meetings m JOIN acl a ON m.id=a.meeting WHERE a.user=? ORDER BY m.id LIMIT 50',(user,))]

    def metadata(self,user,meeting):
        with self.read(): return self._check(user,meeting)

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
        with self.read():
            self._check(user,meeting)
            return [dict(r) for r in self.db.execute('SELECT stream,MAX(seq) AS highest,COUNT(*) AS received,MAX(seq)-COUNT(*) AS missing FROM events WHERE meeting=? GROUP BY stream',(meeting,))]

    def approve(self,user,meeting,root):
        from .project import approved_root
        root=approved_root(root)
        if root == self.root or root.is_relative_to(self.root) or self.root.is_relative_to(root):
            raise ValueError('project root must not overlap application data root')
        with self.tx() as db:
            self._check(user,meeting,True)
            fid=uuid.uuid4().hex
            db.execute('INSERT INTO folders VALUES(?,?,?,?,1)',(fid,meeting,str(root),user))
            return fid

    def _folder(self,user,fid):
        row=self.db.execute('SELECT * FROM folders WHERE id=?',(fid,)).fetchone()
        if not row or not row['active'] or row['owner']!=user: raise Denied('folder access denied')
        self._check(user,row['meeting'])
        root=Path(row['root']).resolve()
        if root == self.root or root.is_relative_to(self.root) or self.root.is_relative_to(root):
            raise Denied('project root overlaps application data root')
        return dict(row)

    def folders(self,user,meeting):
        with self.read():
            self._check(user,meeting)
            return [dict(r) for r in self.db.execute('SELECT id,meeting,active FROM folders WHERE meeting=? AND owner=? AND active=1',(meeting,user))]

    def revoke_folder(self,user,fid):
        with self.tx() as db:
            row=db.execute('SELECT * FROM folders WHERE id=? AND owner=?',(fid,user)).fetchone()
            if not row:raise Denied('folder access denied')
            self._check(user,row['meeting'],True)
            db.execute('UPDATE folders SET active=0 WHERE id=?',(fid,))

    def refresh(self,user,fid):
        from .project import scan
        with self.tx() as db:
            f=self._folder(user,fid)
            root=Path(f['root']).resolve()
            if root == self.root or root.is_relative_to(self.root) or self.root.is_relative_to(root):
                raise Denied('project root overlaps application data root')
            contents=scan(root)
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
            turn_watermark=db.execute('SELECT COALESCE(MAX(id),0) FROM conversation_turns').fetchone()[0]
            db.execute('INSERT INTO snapshot_turn_watermarks VALUES(?,?)',(sid,turn_watermark))
            return sid

    def _snapshot(self,user,sid):
        row=self.db.execute('SELECT * FROM snapshots WHERE id=? AND user=?',(sid,user)).fetchone()
        if not row: raise Denied('snapshot access denied')
        for m in json.loads(row['meetings']): self._check(user,m)
        for f in json.loads(row['folders']): self._folder(user,f)
        result=dict(row)
        turns=self.db.execute('SELECT watermark FROM snapshot_turn_watermarks WHERE snapshot=?',(sid,)).fetchone()
        result['conversation_watermark']=turns[0] if turns else 0
        return result

    def snapshot_info(self,user,sid):
        with self.read():
            s=self._snapshot(user,sid)
            return {k:s[k] for k in ('id','meeting','watermark','conversation_watermark','created','meetings','folders','refs')}

    def evidence(self,user,sid,*,kind=None,query=None,limit=20,recent=False,meeting=None,offset=0):
        with self.read():
            s=self._snapshot(user,sid);refs=json.loads(s['refs'])
            if not refs: return []
            if not isinstance(offset,int) or not 0<=offset<=1000000:raise ValueError('offset limit')
            sql='SELECT e.* FROM evidence e JOIN json_each(?) j ON e.id=j.value'
            if query:sql+=' JOIN evidence_fts ON evidence_fts.rowid=e.id'
            sql+=' WHERE 1=1';args=[json.dumps(refs)]
            if kind: sql+=' AND e.kind=?';args.append(kind)
            if meeting is not None:
                if meeting not in json.loads(s['meetings']):raise Denied('meeting outside snapshot')
                sql+=' AND e.meeting=?';args.append(meeting)
            if query:
                terms=tokens(query)[:80]
                if not terms:return []
                sql+=' AND evidence_fts MATCH ?';args.append(' OR '.join('"'+t+'"' for t in terms))
            order='bm25(evidence_fts),e.id DESC' if query else ('COALESCE(e.start_ms,0) DESC,e.id DESC' if recent else 'COALESCE(e.start_ms,0),e.id')
            sql+=' ORDER BY '+order+' LIMIT ? OFFSET ?';args.extend([max(1,min(limit,100)),offset])
            result=[dict(r) for r in self.db.execute(sql,args)]
            return result[::-1] if recent and not query else result

    def raw(self,user,sid,rid):
        with self.read():
            s=self._snapshot(user,sid)
            if rid not in json.loads(s['refs']): raise Denied('source outside snapshot')
            r=self.db.execute('SELECT * FROM evidence WHERE id=?',(rid,)).fetchone()
            if not r: raise Denied('source removed')
            return dict(r)

    def private_put(self,user,sid,conversation,question,answer):
        with self.tx() as db:
            self._snapshot(user,sid)
            db.execute('INSERT OR REPLACE INTO private VALUES(?,?,?,?,?)',(sid,user,conversation,question,json.dumps(answer)))
            db.execute('INSERT INTO conversation_turns(snapshot,user,conversation,question,answer,created) VALUES(?,?,?,?,?,?)',(sid,user,conversation,question,json.dumps(answer),time.time()))

    def conversation_history(self,user,sid,conversation,limit=8):
        """Prior private turns within this meeting and currently opted-in scope.

        Each turn keeps its own snapshot. A later snapshot never makes a revoked
        or unselected earlier meeting/folder available through conversation state.
        """
        with self.read():
            current=self._snapshot(user,sid)
            meetings=set(json.loads(current['meetings']));folders=set(json.loads(current['folders']))
            rows=self.db.execute('''SELECT t.*,s.meetings,s.folders,s.watermark FROM conversation_turns t
                JOIN snapshots s ON s.id=t.snapshot
                WHERE t.user=? AND t.conversation=? AND s.meeting=? AND t.id<=?
                AND s.watermark<=? ORDER BY t.id DESC''',
                (user,conversation,current['meeting'],current['conversation_watermark'],current['watermark']))
            result=[]
            for r in rows:
                if not set(json.loads(r['meetings'])).issubset(meetings) or not set(json.loads(r['folders'])).issubset(folders):continue
                try:self._snapshot(user,r['snapshot'])
                except Denied:continue
                result.append({'turn_id':r['id'],'snapshot_id':r['snapshot'],'question':r['question'],'answer':json.loads(r['answer'])})
                if len(result)>=max(1,min(limit,20)):break
            return result[::-1]

    def private_get(self,user,sid,conversation,question=None):
        with self.read():
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
        with self.read():
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
        with self.read():
            self._snapshot(user,sid)
            r=self.db.execute('SELECT text,refs FROM summaries WHERE snapshot=?',(sid,)).fetchone()
            return {'text':r[0],'refs':json.loads(r[1])} if r else None

    def latest_summary(self,user,meeting):
        with self.read():
            self._check(user,meeting)
            rows=self.db.execute('SELECT s.id FROM snapshots s JOIN summaries m ON m.snapshot=s.id WHERE s.user=? AND s.meeting=? ORDER BY s.created DESC',(user,meeting))
            for row in rows:
                try:
                    result=self.summary_get(user,row[0])
                    return {'snapshot_id':row[0],**result}
                except Denied:continue
            return None

    def export(self,user,sid,conversation):
        with self.read():
            private=self.private_get(user,sid,conversation);summary=self.summary_get(user,sid)
            required={c['source_id'] for turn in private for c in turn['answer'].get('claims',[])}
            if summary:required.update(summary['refs'])
            rows={r['id']:r for r in self.evidence(user,sid,limit=100)}
            for rid in required:rows[rid]=self.raw(user,sid,rid)
            return {'snapshot':self.snapshot_info(user,sid),'evidence':list(rows.values()),'private':private,'summary':summary,'truncated':len(rows)<len(json.loads(self._snapshot(user,sid)['refs'])),'note':'First 100 sources plus all answer/summary cited sources; use paginated evidence reads for the rest.'}

    def cleanup(self,user,meeting):
        with self.tx() as db:
            self._check(user,meeting,True)
            ids=[r[0] for r in db.execute('SELECT id FROM snapshots WHERE EXISTS(SELECT 1 FROM json_each(snapshots.meetings) WHERE value=?)',(meeting,))]
            for sid in ids: db.execute('DELETE FROM snapshots WHERE id=?',(sid,))
            db.execute('DELETE FROM meetings WHERE id=?',(meeting,))
