import json
import threading
import time
from pathlib import Path
from .private_files import append_private_text, private_directory, private_file, write_private_text

class Trace:
    """Allowlisted metadata only: no questions, excerpts, paths, keys or model errors."""
    allowed={'snapshot_id','source_ids','latency_ms','dropped_segments','input_tokens','output_tokens','estimated_cost_usd','policy','reason','watermark','queue_depth','mode'}
    def __init__(self,path):
        self.path=Path(path);private_directory(self.path.parent);private_file(self.path);self.lock=threading.Lock()
    def emit(self,event,**values):
        row={'event':event,'time':time.time(),**{k:v for k,v in values.items() if k in self.allowed}}
        with self.lock:
            append_private_text(self.path,json.dumps(row)+'\n')
    def redacted_export(self,destination,authorize=None):
        rows=[]
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                r=json.loads(line)
                if authorize and r.get('snapshot_id'):
                    try:authorize(r['snapshot_id'])
                    except PermissionError:continue
                rows.append({k:v for k,v in r.items() if k in self.allowed|{'event','time'}})
        write_private_text(destination,''.join(json.dumps(r)+'\n' for r in rows))
        return len(rows)
