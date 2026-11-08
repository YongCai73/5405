"""MCP receives a host-bound context, never caller-selected identity or scope."""
import json
from .store import Denied
from .project import safe_relative

class EvidenceService:
    def __init__(self,store,user,snapshot,trace=None):self.store=store;self.user=user;self.snapshot=snapshot;self.trace=trace
    def info(self):
        try:
            result=self.store.snapshot_info(self.user,self.snapshot)
            if self.trace:self.trace.emit('policy',snapshot_id=self.snapshot,policy='allow',watermark=result['watermark'])
            return result
        except Denied:
            if self.trace:self.trace.emit('policy',snapshot_id=self.snapshot,policy='deny',reason='scope_revoked_or_not_owned')
            raise
    def list_meetings(self):return [self.store.metadata(self.user,m) for m in json.loads(self.info()['meetings'])]
    def get_meeting(self,meeting):
        if meeting not in json.loads(self.info()['meetings']):raise Denied('meeting not opted in')
        return self.store.metadata(self.user,meeting)
    def get_transcript(self):self.info();return self.store.evidence(self.user,self.snapshot,kind='segment',limit=100)
    def find_in_transcripts(self,query):self.info();return self.store.evidence(self.user,self.snapshot,kind='segment',query=query,limit=30)
    def raw(self,source_id):self.info();return self.store.raw(self.user,self.snapshot,source_id)
    def summary(self):self.info();return self.store.summary_get(self.user,self.snapshot)
    def project(self,query=None,offset=0):
        self.info()
        if not 0<=offset<=1000:raise ValueError('offset limit')
        # Filtering by retained IDs happens before returning any metadata or text.
        with self.store.lock:
            s=self.store._snapshot(self.user,self.snapshot)
            sql="SELECT e.* FROM evidence e JOIN json_each(?) j ON j.value=e.id WHERE kind='file'";args=[s['refs']]
            if query:
                from .store import tokens
                ts=tokens(query)
                if not ts:return []
                sql+=' AND e.id IN (SELECT rowid FROM evidence_fts WHERE evidence_fts MATCH ?)';args.append(' OR '.join('"'+t+'"' for t in ts))
            sql+=' ORDER BY path LIMIT 100 OFFSET ?';args.append(offset)
            return [dict(r) for r in self.store.db.execute(sql,args)]
    def list_project_files(self,offset=0):return [{k:r[k] for k in ('id','folder','path','hash','revision')} for r in self.project(offset=offset)]
    def search_project(self,query):return [{'source_id':r['id'],'path':r['path'],'sha256':r['hash'],'excerpt':r['text'][:1000]} for r in self.project(query)[:20]]
    def read_project_file(self,source_id,start_line=1,end_line=100):
        if not 1<=start_line<=end_line or end_line-start_line>=200:raise ValueError('line range limit')
        r=self.raw(source_id)
        if r['kind']!='file':raise ValueError('not project evidence')
        safe_relative(r['path'])
        return {'source_id':r['id'],'path':r['path'],'sha256':r['hash'],'revision':r['revision'],'start_line':start_line,'end_line':end_line,'text':'\n'.join(r['text'].splitlines()[start_line-1:end_line])[:16000]}
