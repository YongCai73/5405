from dataclasses import dataclass, field
from pathlib import Path
import json

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
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    asr_model: str = 'tiny.en'
    _base: Path = field(default_factory=Path.cwd,repr=False)

    @classmethod
    def load(cls,path):
        p=Path(path).resolve();v=json.loads(p.read_text(encoding='utf-8'))
        if set(v)-{k for k in cls.__dataclass_fields__ if not k.startswith('_')}:raise ValueError('unknown configuration keys')
        c=cls(**v);c._base=p.parent
        if c.provider not in ('demo','openai','anthropic'):raise ValueError('unknown provider')
        if not 0<c.timeout_seconds<=120 or not 1<=c.max_tokens<=4096:raise ValueError('provider resource limit')
        if not c.principal:raise ValueError('host principal required')
        return c

    @property
    def root(self):return (self._base/self.data_dir).resolve()
