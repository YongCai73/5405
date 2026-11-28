import json
import threading
import time
from pathlib import Path
from .private_files import append_private_text, private_directory, private_file, write_private_text

from contextlib import contextmanager
from contextvars import ContextVar


_evaluation_context = ContextVar(
    "evaluation_context",
    default=None,
)


@contextmanager
def evaluation_trace_context(run_id, case_id, variant):
    """Attach evaluation identifiers to trace events."""
    token = _evaluation_context.set({
        "run_id": run_id,
        "case_id": case_id,
        "variant": variant,
    })

    try:
        yield
    finally:
        _evaluation_context.reset(token)


_task_context = ContextVar("task_context", default=None)


@contextmanager
def task_trace_context(**fields):
    """Attach one ask's task ID, skill and skill version to every event it emits.

    Tool calls, policy checks, retrieval and the answer are written from different
    modules; the context lets them share `task_id` without passing it around.
    """
    token = _task_context.set({k: v for k, v in fields.items() if v is not None})
    try:
        yield
    finally:
        _task_context.reset(token)


class Trace:
    """Allowlisted metadata only: no questions, excerpts, paths, keys or model errors."""
    allowed={'snapshot_id','source_ids', 'run_id', 'case_id', 'variant', 'latency_ms','dropped_segments','input_tokens','output_tokens','estimated_cost_usd','policy','reason','watermark','queue_depth','mode',
             # Skill runtime correlation: fixed codes and registered names only.
             'task_id','skill','skill_version','route_mode','route_status','candidates','tool','tool_surface','tool_calls','tool_denied','step','error'}
    def __init__(self,path):
        self.path=Path(path);private_directory(self.path.parent);private_file(self.path);self.lock=threading.Lock()

    def emit(self, event, **values):
        context = _evaluation_context.get() or {}
        metadata = {**(_task_context.get() or {}), **{k: v for k, v in values.items() if v is not None}, **context}

        row = {
            'event': event,
            'time': time.time(),
            **{
                k: v
                for k, v in metadata.items()
                if k in self.allowed
            },
        }
        with self.lock:
            append_private_text(self.path,json.dumps(row)+'\n')
    def rows(self,task_id=None,snapshot_id=None,authorize=None):
        """Events of one task or snapshot, in write order, for local inspection."""
        result=[];denied=set()
        if not self.path.exists():return result
        for line in self.path.read_text(encoding='utf-8').splitlines():
            r=json.loads(line)
            if task_id is not None and r.get('task_id')!=task_id:continue
            if snapshot_id is not None and r.get('snapshot_id')!=snapshot_id:continue
            sid=r.get('snapshot_id')
            if authorize and sid:
                if sid in denied:continue
                try:authorize(sid)
                except PermissionError:denied.add(sid);continue
            result.append({k:v for k,v in r.items() if k in self.allowed|{'event','time'}})
        return result
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
