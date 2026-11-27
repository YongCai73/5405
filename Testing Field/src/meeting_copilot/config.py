from dataclasses import dataclass, field
from pathlib import Path
import json
import math

@dataclass
class Config:
    principal: str = 'local-host'
    data_dir: str = 'data'
    provider: str = 'demo'
    model: str = 'simulated-extractive'
    base_url: str | None = None
    api_key_env: str = 'MEETING_COPILOT_API_KEY'
    send_evidence: bool = False
    timeout_seconds: float = 20
    max_tokens: int = 800
    streaming: bool = False
    tool_rounds: int = 0
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    asr_model: str = 'tiny.en'
    _base: Path = field(default_factory=Path.cwd,repr=False)

    @classmethod
    def load(cls,path):
        p=Path(path).resolve();v=json.loads(p.read_text(encoding='utf-8'))
        if set(v)-{k for k in cls.__dataclass_fields__ if not k.startswith('_')}:raise ValueError('unknown configuration keys')
        c=cls(**v);c._base=p.parent
        if any(not isinstance(getattr(c,k),str) or not getattr(c,k).strip() for k in ('principal','data_dir','provider','model','api_key_env','asr_model')):raise ValueError('configuration string fields must be nonempty')
        if type(c.send_evidence) is not bool:raise ValueError('send_evidence must be a JSON boolean')
        if c.provider not in ('demo','openai','anthropic'):raise ValueError('unknown provider')
        if type(c.timeout_seconds) not in (int,float) or not 0<c.timeout_seconds<=120 or type(c.max_tokens) is not int or not 1<=c.max_tokens<=4096:raise ValueError('provider resource limit')
        if type(c.streaming) is not bool or type(c.tool_rounds) is not int or not 0<=c.tool_rounds<=2:raise ValueError('streaming/tool resource limit')
        for price in (c.input_cost_per_million,c.output_cost_per_million):
            if price is not None and (type(price) not in (int,float) or not math.isfinite(price) or price<0):raise ValueError('cost rates must be nonnegative finite numbers or null')
        return c

    @property
    def root(self):return (self._base/self.data_dir).resolve()
