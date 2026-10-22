"""Offline fixed vocabulary evaluation; no LLM calls, no independent holdout."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time
from .memory import rank_events
from .memory_backend import scoped
from .search_language import VERSION


def fixture():
    texts = [
        '어제 진술은 앞뒤가 안 맞는다.',
        '밤중 행적은 확인되지 않았다.',
        '숲길에서 누군가와 마주쳤다.',
        '의사가 부상자를 살려 줬다.',
        '정체는 아직 공개하지 않았다.',
        '회의에서 그 사람을 쫓아냈다.',
        '늑대가 누군가를 습격했다.',
        '밤에 대상을 뒤따라갔다.',
        '길가에서 시신을 발견했다.',
    ]
    events = [{'sequence':i+1,'type':'발언','recipient':None,
               'data':{'actor':f'p{i+1}','text':text,'day':1}}
              for i,text in enumerate(texts)]
    events += [{'sequence':i+10,'type':'발언','recipient':None,
                'data':{'actor':'p0','text':f'날씨 이야기를 나눕니다 {i}.','day':1}}
               for i in range(24)]
    # A tempting exact match must never be in the authorized candidate set.
    events.append({'sequence':99,'type':'발언','recipient':'p9',
                   'data':{'actor':'p9','text':'거짓말 목격 보호 조사 투표 공격 추적 시체 알리바이'}})
    view={'room_id':'language-fixture','self':{'id':'p0'},'players':[],
          'day':2,'phase':'discussion','events':events}
    rows=[]
    variants=[
        (1,('거짓말에 관한 발언','진술이 달라진 발언')),
        (2,('알리바이에 관한 발언','동선에 관한 발언')),
        (3,('목격한 이야기는','보았다는 이야기는')),
        (4,('보호해 준 이야기는','치료에 관한 이야기는')),
        (5,('역할에 관한 발언','진영에 관한 발언')),
        (6,('추방에 관한 발언','투표에 관한 발언')),
        (7,('공격에 관한 발언','살해에 관한 발언')),
        (8,('추적에 관한 발언','미행에 관한 발언')),
        (9,('시체에 관한 이야기','사체에 관한 이야기')),
    ]
    for expected,queries in variants:
        for i,query in enumerate(queries):
            rows.append({'id':f'variant_{expected}_{i}','group':'covered_vocabulary',
                         'query':query,'expected':expected})
    for expected,query in [(1,'앞뒤가 안 맞는다'),(3,'숲길에서 마주쳤다'),(9,'시신을 발견했다')]:
        rows.append({'id':f'exact_{expected}','group':'lexical_control','query':query,'expected':expected})
    for expected,query in [(1,'의견이 돌변한 사람'),(4,'도움을 받고 죽음을 피한 사람'),(8,'누군가의 뒤를 밟았다')]:
        rows.append({'id':f'uncovered_{expected}','group':'uncovered_expression','query':query,'expected':expected})
    return view, rows


def evaluate(repeats=30):
    view,cases=fixture()
    events=scoped(view)['events']
    rows=[]
    for index,case in enumerate(cases):
        for mode in ('before','after') if index%2==0 else ('after','before'):
            times=[]
            for _ in range(repeats):
                started=time.perf_counter()
                ranking,_=rank_events(events,case['query'],expand=mode=='after')
                times.append((time.perf_counter()-started)*1000)
            ids=[events[i]['sequence'] for i in ranking]
            rank=ids.index(case['expected'])+1
            rows.append({**case,'mode':mode,'rank':rank,'top_3_ids':ids[:3],
                         'mean_ms':statistics.mean(times),
                         'unauthorized_ids':len(set(ids)-{e['sequence'] for e in events})})
    summaries={}
    for group in ('all','covered_vocabulary','lexical_control','uncovered_expression'):
        summaries[group]={}
        for mode in ('before','after'):
            selected=[r for r in rows if r['mode']==mode and (group=='all' or r['group']==group)]
            summaries[group][mode]={
                'queries':len(selected),'hit_at_1':sum(r['rank']==1 for r in selected),
                'hit_at_3':sum(r['rank']<=3 for r in selected),
                'hit_at_8':sum(r['rank']<=8 for r in selected),
                'mrr':statistics.mean(1/r['rank'] for r in selected),
                'mean_ms':statistics.mean(r['mean_ms'] for r in selected),
                'unauthorized_ids':sum(r['unauthorized_ids'] for r in selected)}
    return {'created_at':datetime.now(timezone.utc).isoformat(),'strategy':VERSION,
            'fixture_sha256':hashlib.sha256(json.dumps([view,cases],ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'timing_repeats':repeats,'candidate_events':len(events),
            'scope':'24 fixed synthetic queries: 18 covered vocabulary, 3 lexical controls, 3 deliberately uncovered expressions. This is a vocabulary acceptance set, not an independent holdout or general semantic benchmark. Ranking excludes pinned and recent-event retention. Before: original data-only lexical scoring. After: event type + atomic player IDs + weighted vocabulary labels. Combined retrieval change. No generation/API calls; cache disabled. Unauthorized candidate filtering is tested before ranking and separately by MCP integration tests.',
            'summary':summaries,'rows':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('reports/retrieval-language-v1.json'))
    parser.add_argument('--repeats',type=int,default=30)
    args=parser.parse_args()
    if not 1<=args.repeats<=100:parser.error('repeats must be 1..100')
    result=evaluate(args.repeats)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result['summary'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
