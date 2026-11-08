"""One API boundary. Generation returns only validated verbatim evidence claims."""
import asyncio
import json
import os
import re
from dataclasses import dataclass
from local_meeting_ai.adapters.litellm_compat import _filter_supported_parameters
from local_meeting_ai.domain.meeting_text import evidence_system_message

class ProviderError(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)

@dataclass
class Completion:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None

class Provider:
    def __init__(self,config):self.config=config
    async def complete(self,question,evidence,contract):
        c=self.config
        if c.provider=='demo':raise ValueError('demo selection is handled deterministically by graph')
        if not c.send_evidence:raise ProviderError('evidence_upload_not_enabled')
        key=os.environ.get(c.api_key_env)
        if not key:raise ProviderError('missing_api_key')
        # Set before importing: LiteLLM must not fetch external pricing metadata or log payloads.
        os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP','True')
        import litellm
        litellm.suppress_debug_info=True
        system=evidence_system_message(json.dumps(evidence))+'\nReturn JSON only: {"claims":[{"source_id":integer,"quote":"exact substring"}]}. No claim if unsupported. '+json.dumps(contract)
        args={'model':c.provider+'/'+c.model,'api_key':key,'messages':[{'role':'system','content':system},{'role':'user','content':question}],'max_tokens':c.max_tokens,'temperature':0,'timeout':c.timeout_seconds,'num_retries':0,'stream':False,'caching':False}
        if c.base_url:args['api_base']=c.base_url
        args=_filter_supported_parameters(litellm,args,args['model']);args['drop_params']=True
        try:
            response=await asyncio.wait_for(litellm.acompletion(**args),timeout=c.timeout_seconds)
        except asyncio.CancelledError:raise
        except (asyncio.TimeoutError,TimeoutError):raise ProviderError('timeout') from None
        except Exception as e:
            name=type(e).__name__.lower();code='provider_error'
            for word in ('timeout','authentication','ratelimit','badrequest','connection'):
                if word in name:code=word;break
            raise ProviderError(code) from None
        usage=response.usage
        inp=getattr(usage,'prompt_tokens',None);out=getattr(usage,'completion_tokens',None)
        cost=None
        if inp is not None and out is not None and c.input_cost_per_million is not None and c.output_cost_per_million is not None:
            cost=(inp*c.input_cost_per_million+out*c.output_cost_per_million)/1e6
        return Completion(response.choices[0].message.content or '',inp,out,cost)

def validated_claims(content,evidence):
    try:
        payload=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',content.strip()))
        rows=payload['claims']
        if not isinstance(rows,list):return []
    except (ValueError,KeyError,TypeError):return []
    sources={e['id']:e for e in evidence};claims=[]
    for c in rows[:8]:
        if not isinstance(c,dict):continue
        rid=c.get('source_id');quote=c.get('quote')
        if isinstance(rid,int) and rid in sources and isinstance(quote,str) and 3<=len(quote)<=1200 and quote in sources[rid]['text']:
            claims.append({'source_id':rid,'quote':quote})
    return claims
