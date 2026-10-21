"""Small paired vote check. Not a win-rate or independent reasoning benchmark."""
import json
import time
from pathlib import Path
from statistics import mean
from dotenv import load_dotenv
from .engine import Game
from . import beliefs
from .decision import decide
from .providers import create_client
from .mcp_memory import MCPMemory


def cases():
    rows=[]
    for name in ('village_update','wolf_update','conflict','other_private'):
        g=Game('belief-'+name,{f'p{i}':'ai' for i in range(5)},seed=9)
        g.players['p0'].role='seer';g.phase='discussion';g.day=2
        g._emit('목격',{'player':'p2','location':'창고','day':1},'p0')
        v=g.observation('p0')
        old=beliefs.record_suspicion(v,beliefs.update(v),{'semantic_support':'grounded_suspicion','claim':{'claim_subject':'p2'},'evidence_ids':[2]},False)
        if name=='conflict':
            for team in ('wolf','village'):g._emit('조사',{'target':'p2','team':team,'day':2},'p0')
        else:
            g._emit('조사',{'target':'p2','team':'wolf' if name in ('wolf_update','other_private') else 'village','day':2},'p3' if name=='other_private' else 'p0')
        g.phase='vote';rows.append((name,g.observation('p0'),old,'p2' if name=='wolf_update' else None))
    return rows


def main():
    load_dotenv('.env');model=create_client('openai','gpt-4.1-mini',max_calls=24);memory=MCPMemory();rows=[]
    raw=Path('runtime/verification');raw.mkdir(parents=True,exist_ok=True)
    try:
        for index,(name,view,old,expected) in enumerate(cases()):
            for mode in (('before','after') if index%2==0 else ('after','before')):
                offset=len(model.metrics);start=time.perf_counter()
                result=decide(view,model,memory=memory,policy='verified',previous_beliefs=old,belief_memory=mode=='after')
                elapsed=time.perf_counter()-start;metrics=model.metrics[offset:]
                row={'case':name,'mode':mode,'expected':expected,'selected':result['payload'].get('target'),
                    'correct':result['payload'].get('target')==expected,'seconds':round(elapsed,3),'attempts':result['attempts'],'fallback':result['fallback'],
                    'input_tokens':sum(m.get('input_tokens') or 0 for m in metrics),'output_tokens':sum(m.get('output_tokens') or 0 for m in metrics),'audit':result['audit']}
                rows.append(row);(raw/'belief-memory-v1.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
                print(json.dumps({k:row[k] for k in ('case','mode','correct','seconds','attempts','fallback')},ensure_ascii=False),flush=True)
        summary={'model':'gpt-4.1-mini','scope':'4 synthetic cases once each per condition. Same MCP+verified pipeline, only belief memory enabled after. Previous suspicion seeded as a fixture, not learned in this run. Conservative expected vote/abstain; legal suspicion votes may count wrong. Conflicting system facts are a robustness fixture, not normal gameplay. No holdout, no win-rate claim.','results':{}}
        for mode in ('before','after'):
            group=[r for r in rows if r['mode']==mode]
            summary['results'][mode]={'runs':len(group),'correct':sum(r['correct'] for r in group),'mean_seconds':round(mean(r['seconds'] for r in group),3),'attempts':sum(r['attempts'] for r in group),'fallbacks':sum(r['fallback'] for r in group),'input_tokens':sum(r['input_tokens'] for r in group),'output_tokens':sum(r['output_tokens'] for r in group)}
        Path('reports/belief-memory-v1.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False),flush=True)
    finally:memory.close()


if __name__=='__main__':main()
