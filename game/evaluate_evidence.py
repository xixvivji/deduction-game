"""Paired synthetic evaluation: full-context legacy vs MCP retrieval + typed evidence validation.
Not a win-rate benchmark, not an isolated retrieval ablation, no monetary cost estimates.
"""
import argparse
import copy
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import statistics
import time
from .engine import Game
from .decision import decide
from .memory_backend import LocalMemory,scoped
from .mcp_memory import MCPMemory
from .providers import create_client


def cases():
    result=[]
    for index,kind in enumerate(['investigation','attack','rumor','location','private_other','contradiction','village','no_evidence']):
        g=Game('evidence-'+kind,{f'p{i}':'ai' for i in range(7)},seed=100+index)
        for p in g.players.values():p.role='citizen'
        target=f'p{index%5+1}';g.players[target].role='wolf';g.players['p6'].role='wolf';g.players['p0'].role='seer';g.phase='vote';g.day=2
        for i in range(32):g._emit('발언',{'actor':f'p{i%7}','text':f'일상 대화 {i}: 아직 확실한 정보가 없습니다.','day':1})
        required=[];expected=None
        if kind in ('investigation','contradiction'):
            g._emit('조사',{'target':target,'team':'wolf','day':1},'p0');required=[len(g.events)];expected=target
        elif kind=='attack':
            g._emit('공격 목격',{'actor':target,'target':'p6','location':'광장','day':1},'p0');required=[len(g.events)];expected=target
        elif kind=='location':g._emit('탐색 목격',{'player':target,'location':'창고','day':1,'second':20},'p0')
        elif kind=='private_other':g._emit('조사',{'target':target,'team':'wolf','day':1},'p6')
        elif kind=='village':
            g.players[target].role='citizen';g._emit('조사',{'target':target,'team':'village','day':1},'p0');required=[len(g.events)]
        if kind in ('rumor','contradiction','village'):
            g._emit('발언',{'actor':target,'text':'저는 시민입니다. p6가 늑대라고 생각합니다. 근거는 없습니다.','day':2})
        view=g.observation('p0')
        result.append({'id':kind,'view':view,'expected':expected,'required':required})
    return result


def summarize(rows):
    summary={}
    for mode in ('before','after'):
        group=[r for r in rows if r['mode']==mode];seconds=sorted(r['seconds'] for r in group)
        supported=[r for r in group if r['required_recall'] is not None]
        summary[mode]={'runs':len(group),'correct':sum(r['correct'] for r in group),
          'accuracy':sum(r['correct'] for r in group)/len(group),'mean_seconds':statistics.mean(seconds),
          'p95_seconds':seconds[math.ceil(.95*len(seconds))-1],
          'input_tokens':sum(r['input_tokens'] for r in group),'output_tokens':sum(r['output_tokens'] for r in group),
          'mean_attempts':statistics.mean(r['attempts'] for r in group),'research_rounds':sum(r['research_rounds'] for r in group),
          'fallbacks':sum(r['fallback'] for r in group),
          'evidence_recall':statistics.mean(r['required_recall'] for r in supported) if supported else None,
          'out_of_scope_retrieval_ids':sum(r['out_of_scope_retrieval_ids'] for r in group)}
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3:4b-instruct');parser.add_argument('--repeats',type=int,default=2)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.repeats<=20:parser.error('repeats must be 1..20')
    data=cases();rows=[];client=create_client('ollama',args.model,max_calls=6*len(data)*args.repeats+5);client.check()
    start=time.perf_counter();memory=MCPMemory();startup=time.perf_counter()-start
    try:
        # Excluded warm-up. Same participant pipeline; never include expected answer in model input.
        decide(data[0]['view'],client,memory=memory,policy='verified')
        for repeat in range(args.repeats):
            for index,case in enumerate(data):
                for mode in (('before','after') if (repeat+index)%2==0 else ('after','before')):
                    view=copy.deepcopy(case['view']);after=mode=='after'
                    selected=memory.search(view) if after else scoped(view)
                    visible={e['sequence'] for e in scoped(view)['events']};ids={e['sequence'] for e in selected['events']}
                    offset=len(client.metrics);start=time.perf_counter()
                    result=decide(view,client,'retrieved' if after else 'full',memory if after else LocalMemory(),'verified' if after else 'legacy')
                    seconds=time.perf_counter()-start;calls=client.metrics[offset:]
                    rows.append({'case':case['id'],'repeat':repeat,'mode':mode,'seconds':round(seconds,4),
                       'expected_target':case['expected'],'selected_target':result['payload'].get('target'),
                       'correct':result['payload'].get('target')==case['expected'],'attempts':result['attempts'],
                       'fallback':result['fallback'],'research_rounds':result['audit']['research_rounds'],
                       'input_tokens':sum(c.get('input_tokens') or 0 for c in calls),'output_tokens':sum(c.get('output_tokens') or 0 for c in calls),
                       'selected_events':len(ids),'original_events':len(visible),'required_recall':len(ids&set(case['required']))/len(case['required']) if case['required'] else None,
                       'out_of_scope_retrieval_ids':len(ids-visible),'audit':result['audit'],'calls':calls})
                    report={'created_at':datetime.now(timezone.utc).isoformat(),'model':args.model,'fixture_version':1,
                      'scope':'8 synthetic vote situations, repeated paired comparison. No independent holdout, no win-rate/general-reasoning claim. Combined intervention, not retrieval-only ablation. Warm-up excluded; cache effects may remain. Evidence recall measures selected context including pinned facts, not vector Recall@k. Scope violations measure retrieved IDs, not all prose leakage.',
                      'mcp_startup_seconds':startup,'rows':rows}
                    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
                    print(f'{repeat+1}/{args.repeats} {case["id"]} {mode}: correct={rows[-1]["correct"]} {seconds:.2f}s attempts={result["attempts"]}',flush=True)
        report['summary']=summarize(rows);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report['summary'],ensure_ascii=False,indent=2))
    finally:memory.close()


if __name__=='__main__':main()
