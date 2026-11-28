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
    # Tool loop: after reading the retrieved evidence the model may search or read more within the
    # skill's tool allowlist and budget, this many times (0 turns it off).
    tool_rounds: int = 1
    # Re-search loop: when an answer cites no evidence, the question is rewritten into new search
    # terms and retrieval runs again, at most this many times (0 turns it off, 5 is the ceiling).
    research_retries: int = 1
    # OpenAI-compatible endpoints with a thinking switch (DeepSeek): null leaves the
    # provider default, 'enabled'/'disabled' sends {"thinking": {"type": ...}}.
    thinking: str | None = None
    # With a model configured, a question the meeting record cannot answer still gets a reply from the
    # model, shown apart from quotations and labelled as not from the meeting. False restores abstention.
    general_answers: bool = True
    # Retrieval measured in content rather than in segments: the part of the transcript a question is
    # about (whole meeting, opening, end, recent), a short meeting handed over whole, and the speech
    # around a keyword hit. False restores the per-skill segment limits exactly.
    transcript_context: bool = True
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
        if c.thinking not in (None,'enabled','disabled'):raise ValueError('thinking must be null, "enabled" or "disabled"')
        if type(c.general_answers) is not bool:raise ValueError('general_answers must be a JSON boolean')
        if type(c.research_retries) is not int or not 0<=c.research_retries<=5:raise ValueError('research_retries must be an integer from 0 to 5')
        if type(c.transcript_context) is not bool:raise ValueError('transcript_context must be a JSON boolean')
        for price in (c.input_cost_per_million,c.output_cost_per_million):
            if price is not None and (type(price) not in (int,float) or not math.isfinite(price) or price<0):raise ValueError('cost rates must be nonnegative finite numbers or null')
        return c

    @property
    def root(self):return (self._base/self.data_dir).resolve()
