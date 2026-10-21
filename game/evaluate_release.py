"""Fixed same-input cloud evaluation for the presentation; raw answers stay local."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from dotenv import load_dotenv
from .evaluate_evidence import cases as evidence_cases
from .engine import Game
from .decision import decide
from .memory_backend import LocalMemory, scoped
from .mcp_memory import MCPMemory
from .providers import create_client, PooledTransport
from .circuit_breaker import CircuitTransport
from .time_budget import run_with_budget


def cases():
    rows=[{**row,'group':'evidence'} for row in evidence_cases()]
    for role in ('citizen','wolf'):
        g=Game('forced-'+role,{f'p{i}':'ai' for i in range(5)},seed=1)
        g.players['p0'].role=role;g.phase='night';g.day=1;g.realtime=True
        rows.append({'id':'forced_'+role,'group':'forced','view':g.observation('p0'),'expected':None,'required':[]})
    return rows


def summarize(rows):
    result={}
    for group in ('evidence','forced'):
        result[group]={}
        for mode in ('before','after'):
            selected=[r for r in rows if r['mode']==mode and r['group']==group]
            if not selected:continue
            result[group][mode]={'runs':len(selected),'correct':sum(r['correct'] for r in selected),
                'mean_seconds':round(statistics.mean(r['seconds'] for r in selected),3),
                'input_tokens':sum(r['input_tokens'] for r in selected),
                'output_tokens':sum(r['output_tokens'] for r in selected),
                'generation_attempts':sum(r['attempts'] for r in selected),
                'retries':sum(max(0,r['attempts']-1) for r in selected),
                'fallbacks':sum(r['fallback'] for r in selected),
                'out_of_scope_citations':sum(r['out_of_scope_citations'] for r in selected),
                'usage_unknown_calls':sum(r['usage_unknown_calls'] for r in selected)}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=2)
    args=parser.parse_args()
    if not 1<=args.repeats<=3:parser.error('repeats must be 1..3')
    load_dotenv('.env')
    provider=os.environ.get('GAME_PROVIDER','openai');model=os.environ.get('GAME_MODEL','gpt-4.1-mini')
    if provider not in ('openai','gemini'):parser.error('Configure a cloud provider for this evaluation')
    fixtures=cases();rows=[];private=[]
    transport=CircuitTransport(PooledTransport())
    client=create_client(provider,model,max_calls=len(fixtures)*2*args.repeats*3,transport=transport)
    client.check();memory=MCPMemory()
    out=Path('runtime/verification');out.mkdir(parents=True,exist_ok=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'code_revision':subprocess.check_output(['git','rev-parse','--short','HEAD'],text=True).strip(),
        'provider':provider,'model':model,'repeats':args.repeats,'fixture_version':1,
        'fixture_sha256':hashlib.sha256(json.dumps(fixtures,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        'scope':'8 synthetic vote situations and 2 forced night situations, each repeated per condition. Same model and shared HTTP transport; order alternated. Before: full scoped context + legacy validation. After: MCP retrieval + typed claim validation + belief memory + forced-action skip. Combined intervention, not runtime or RAG-only ablation. No warm-up exclusion. Conservative vote/abstain label, not win rate, general reasoning, search precision or independent holdout. Token usage, not monetary billing. Out-of-scope citations are not whole-prose leakage detection.',
        'summary':{},'rows':rows}
    try:
        for repeat in range(args.repeats):
            for index,case in enumerate(fixtures):
                for mode in (('before','after') if (repeat+index)%2==0 else ('after','before')):
                    after=mode=='after';view=copy.deepcopy(case['view']);offset=len(client.metrics);start=time.perf_counter()
                    response=run_with_budget(120,decide,view,client,'retrieved' if after else 'full',memory if after else LocalMemory(),
                        'verified' if after else 'legacy',belief_memory=after,skip_forced=after)
                    duration=time.perf_counter()-start;metrics=client.metrics[offset:]
                    visible={e['sequence'] for e in scoped(view)['events']}
                    correct=response['payload'].get('target')==case['expected']
                    if case['group']=='forced':correct=correct and response['payload'].get('location')=='광장'
                    row={'case':case['id'],'group':case['group'],'repeat':repeat,'mode':mode,'seconds':round(duration,4),'correct':correct,
                        'attempts':response['attempts'],'fallback':response['fallback'],
                        'input_tokens':sum(m.get('input_tokens') or 0 for m in metrics),'output_tokens':sum(m.get('output_tokens') or 0 for m in metrics),
                        'usage_unknown_calls':sum(m.get('input_tokens') is None or m.get('output_tokens') is None for m in metrics),
                        'out_of_scope_citations':len(set(response['audit'].get('evidence_ids',[]))-visible)}
                    rows.append(row);private.append({'row':row,'response':response,'metrics':metrics})
                    (out/'release-evaluation-v1.json').write_text(json.dumps(private,ensure_ascii=False,indent=2))
                    report['summary']=summarize(rows)
                    Path('reports/release-evaluation-v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                    print(json.dumps(row,ensure_ascii=False),flush=True)
        print(json.dumps(report['summary'],ensure_ascii=False,indent=2),flush=True)
    finally:
        memory.close();transport.close()


if __name__=='__main__':main()
