"""Reproducible synthetic regression comparison and optional small live smoke test.

Default is offline. --live consumes the configured API, at most 24 attempts.
Only aggregates are published; outputs/audits stay in ignored runtime/verification.
"""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time

from .engine import Game
from . import beliefs, discussion
from .context_budget import ContextBudget, encoded_size, required_ids
from .prompt_context import compact
from .memory_backend import LocalMemory

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'b01e3f8'


def source_hash():
    return hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in sorted((ROOT/'game').glob('*.py')))).hexdigest()


def fixture():
    g = Game('synthetic-runtime-evaluation', {f'p{i}':'ai' for i in range(7)}, seed=1)
    g.players['p0'].role = 'seer';g.phase = 'vote';g.day = 4
    return g


def long_view():
    g=fixture()
    for i in range(70):
        text=(f'합성 과거 토론 {i} 기록을 확인하고 서로의 발언을 비교하겠습니다. '*20)[:500]
        g._emit('발언', {'actor':f'p{i%7}','text':text,'day':2})
    g._emit('조사', {'target':'p1','team':'wolf','day':4}, 'p0')
    return g.observation('p0')


def regressions(cwd):
    code = '''import json, unittest
suite=unittest.defaultTestLoader.loadTestsFromName('test_reasoning_guards.ReasoningGuardTests')
r=unittest.TestResult();suite.run(r)
print(json.dumps({'cases':r.testsRun,'passed':r.testsRun-len(r.failures)-len(r.errors),
                  'failed':[t.id().split('.')[-1] for t,_ in r.failures],
                  'errors':[t.id().split('.')[-1] for t,_ in r.errors]}))'''
    env={**os.environ,'LANGSMITH_TRACING':'false','PYTHONPATH':str(ROOT/'tests')}
    output=subprocess.run([sys.executable,'-c',code],cwd=cwd,env=env,check=True,capture_output=True,text=True)
    return json.loads(output.stdout)


def offline():
    # Read a Git snapshot into a temporary directory; never switch the user's branch.
    archive=subprocess.run(['git','archive',BASELINE,'game'],cwd=ROOT,check=True,capture_output=True).stdout
    with tempfile.TemporaryDirectory(prefix='game-regression-') as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(tmp,filter='data')
        before=regressions(tmp)
    after=regressions(ROOT)
    selected=LocalMemory().search(long_view())
    budget=ContextBudget(12000)
    samples=[]
    for _ in range(50):
        started=time.perf_counter();bounded,stats=budget.apply(selected)
        samples.append((time.perf_counter()-started)*1000)
    ids={e['sequence'] for e in bounded['events']}
    return {'baseline_commit':BASELINE,'source_sha256':source_hash(),
            'scope':'Eight known synthetic regression tests on old Git snapshot versus current code. Not a holdout or LLM accuracy benchmark. Context test uses a configured 12000-byte observation budget, not the 32768 default. No API calls, RSS or token savings measured.',
            'regressions':{'before':before,'after':after},
            'context':{**stats,'before_events':len(selected['events']),
                       'required_preserved':required_ids(selected)<=ids,
                       'records_unchanged':all(e in selected['events'] for e in bounded['events']),
                       'budget_overhead_mean_ms':round(statistics.mean(samples),3),
                       'budget_overhead_p95_ms':round(sorted(samples)[47],3),'iterations':50}}


def live_cases():
    rows=[]
    for name in ('idle_speech','old_sighting','confirmed_wolf','retracted_village','reported_accusation','dated_location'):
        g=fixture();old=None;expected=None
        if name=='idle_speech':g._emit('발언',{'actor':'p1','text':'날씨가 좋네요.','day':4})
        if name in ('old_sighting','retracted_village'):
            g.day=1;g._emit('목격',{'player':'p1','location':'창고','day':1},'p0')
            v=g.observation('p0')
            old=beliefs.record_suspicion(v,beliefs.update(v),{'semantic_support':'grounded_suspicion',
                'claim':{'claim_subject':'p1'},'evidence_ids':[2]},False)
            g.day=4
        if name=='retracted_village':g._emit('조사',{'target':'p1','team':'village','day':4},'p0')
        if name=='confirmed_wolf':
            g._emit('조사',{'target':'p1','team':'wolf','day':4},'p0');expected='p1'
        if name=='reported_accusation':g._emit('발언',{'actor':'p2','text':'p1은 늑대입니다. 제가 그렇게 생각합니다.','day':4})
        if name=='dated_location':
            g.phase='discussion'
            g._emit('탐색 목격',{'player':'p1','location':'창고','day':2,'second':18.5},'p0')
            g._emit('질문',{'actor':'p2','target':'p0','text':'p1의 창고 위치 목격을 당시 시점과 함께 알려 주세요.','evidence_ids':[],'day':4})
            expected='dated_location'
        v=g.observation('p0')
        if name=='dated_location':v['question']={'sequence':3,**g.events[-1]['data']}
        rows.append((name,v,old,expected,ContextBudget()))
    for mode,limit in [('unbounded',262144),('bounded',12000)]:
        rows.append(('long_'+mode,long_view(),None,'p1',ContextBudget(limit)))
    return rows


def live():
    from dotenv import load_dotenv
    from .providers import create_client, PooledTransport
    from .decision import decide
    from .mcp_memory import MCPMemory
    from .time_budget import run_with_budget
    load_dotenv(ROOT/'.env')
    model_name=os.environ.get('GAME_MODEL','gpt-4.1-mini')
    provider=os.environ.get('GAME_PROVIDER','openai')
    records=[];raw=ROOT/'runtime/verification/reasoning-runtime-live-v1.json'
    raw.parent.mkdir(parents=True,exist_ok=True)
    transport=PooledTransport();memory=None
    try:
        model=create_client(provider,model_name,max_calls=24,transport=transport)
        model.check();memory=MCPMemory()
        for name,v,old,expected,budget in live_cases():
            offset=len(model.metrics);start=time.perf_counter()
            result=run_with_budget(120,decide,v,model,memory=memory,policy='verified',
                    previous_beliefs=old,belief_memory=True,context_budget=budget)
            metrics=model.metrics[offset:]
            correct=(result['payload'].get('target')==expected) if expected!='dated_location' else (
                    result['audit'].get('semantic_support')=='supported_fact' and
                    result['audit'].get('claim',{}).get('claim_type')=='visited' and
                    '2일 차 탐색 18.5초' in result['payload'].get('text',''))
            row={'case':name,'correct':correct,'fallback':result['fallback'],'attempts':result['attempts'],
                 'seconds':round(time.perf_counter()-start,3),
                 'input_tokens':sum(m['input_tokens'] for m in metrics) if metrics and all(m.get('input_tokens') is not None for m in metrics) else None,
                 'output_tokens':sum(m['output_tokens'] for m in metrics) if metrics and all(m.get('output_tokens') is not None for m in metrics) else None,
                 'context_bytes':result['audit']['runtime']['context_budget']['after_bytes']}
            records.append({**row,'result':result,'metrics':metrics})
            raw.write_text(json.dumps(records,ensure_ascii=False,indent=2))
            print(json.dumps(row,ensure_ascii=False),flush=True)
        return {'provider':provider,'model':model_name,'source_sha256':source_hash(),
                'scope':'Six hand-authored synthetic functional cases once each, plus one identical long-context vote with two byte budgets. Current pipeline only; no old-code live comparison, independent judge, held-out evaluation, win-rate or billing claim. Unknown usage remains null.',
                'cases':[{k:v for k,v in r.items() if k not in ('result','metrics')} for r in records]}
    finally:
        if memory is not None:memory.close()
        transport.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    os.environ['LANGSMITH_TRACING']='false'  # Local evals; never upload raw evaluation fixtures by default.
    result=live() if args.live else offline()
    target=ROOT/'reports'/('reasoning-runtime-live-v1.json' if args.live else 'reasoning-runtime-v1.json')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
