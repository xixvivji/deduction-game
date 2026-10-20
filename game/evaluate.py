"""합성 고정 상황에서 모델 및 전체 기록/검색 기록을 비교한다. 승률 평가가 아니다."""
import argparse
import json
import time
from pathlib import Path
from .engine import Game
from .decision import decide
from .memory import retrieve
from .providers import create_client


def cases():
    items=[]
    for index, seed in enumerate((11,42,8,19)):
        game=Game(f'eval-{seed}',{f'p{i}':'ai' for i in range(7)},seed=seed)
        seer=next(p.id for p in game.players.values() if p.role=='seer')
        wolves=[p.id for p in game.players.values() if p.role=='wolf']
        wolf=wolves[index % len(wolves)]
        game._emit('조사',{'target':wolf,'team':'wolf','day':1},seer)
        for i in range(40):
            game._emit('발언',{'actor':f'p{i%7}','text':f'{i}번째 대화: 날씨가 흐렸고 저는 시장의 소리를 들었습니다. 확실한 증거는 없습니다.'})
        game._emit('발언',{'actor':wolf,'text':'저는 시민입니다. 근거 없이 저를 의심하지 마세요.'})
        game.phase='vote';game.day=2
        items.append({'id':f'known-wolf-{seed}','view':game.observation(seer),'expected_target':wolf})
    return items


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',choices=('ollama','openai','gemini'),default='ollama')
    parser.add_argument('--model',action='append',required=True)
    parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if not 1 <= args.repeats <= 20: parser.error('repeats는 1~20입니다')
    rows=[]
    for model in args.model:
        client=create_client(args.provider,model,max_calls=4*len(cases())*args.repeats+2)
        if not args.dry_run: client.check()
        for repeat in range(args.repeats):
            # 순서 효과를 줄이기 위해 반복 회차마다 조건 순서를 바꾼다.
            modes=('full','retrieved') if repeat%2==0 else ('retrieved','full')
            for case in cases():
                for mode in modes:
                    view=case['view'];selected=retrieve(view) if mode=='retrieved' else view
                    row={'case':case['id'],'provider':args.provider,'model':model,'memory':mode,'repeat':repeat,
                         'original_events':len(view['events']),'selected_events':len(selected['events']),
                         'context_chars':len(json.dumps(selected,ensure_ascii=False))}
                    if not args.dry_run:
                        start=time.perf_counter();offset=len(client.metrics)
                        result=decide(view,client,mode)
                        row.update(seconds=round(time.perf_counter()-start,3),correct=result['payload'].get('target')==case['expected_target'],
                                   attempts=result['attempts'],fallback=result['fallback'],audit=result['audit'],selected_target=result['payload'].get('target'),expected_target=case['expected_target'],calls=client.metrics[offset:])
                    rows.append(row)
    report={'scope':'합성 조사 결과 기반 투표 4개 상황. 추리 전반·역할 누설·승률 평가가 아님. 최초 모델 로딩 시간 포함.',
            'fixture_version':2,'dry_run':args.dry_run,'rows':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f'{len(rows)}개 조건 기록: {args.output}')


if __name__=='__main__':main()
