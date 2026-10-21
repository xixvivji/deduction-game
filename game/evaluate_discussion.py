"""Same-input OpenAI discussion comparison. Texts stay in ignored runtime directory."""
import argparse
import json
import time
from pathlib import Path
from statistics import mean
from dotenv import load_dotenv
from .engine import Game
from .decision import decide
from .mcp_memory import MCPMemory
from .providers import create_client
from . import discussion


def cases():
    rows=[]
    for name in ['dead_target','past_location','repeated_deferral','direct_question']:
        g=Game('discussion-'+name,{f'p{i}':'ai' for i in range(5)},seed=7)
        g.players['p0'].role='citizen';g.phase='discussion';g.day=3
        if name=='dead_target':
            g.players['p1'].alive=False
            g._emit('발언',{'actor':'p2','text':'이번에는 p1에게 투표하자는 의견입니다. 여러분은 어떻게 생각하나요?','day':3})
        elif name=='past_location':
            g._emit('목격',{'player':'p2','location':'창고','day':1},'p0')
            g._emit('발언',{'actor':'p3','text':'p2는 지금 어디 있나요? 예전 목격으로 현재 위치까지 알 수 있나요?','day':3})
        else:
            for pid in ['p0','p2','p0','p3']:
                g._emit('발언',{'actor':pid,'text':'근거가 부족합니다. 추가 정보를 기다리며 신중하게 판단하겠습니다.','day':3})
        v=g.observation('p0')
        if name=='direct_question':
            g._emit('질문',{'actor':'p2','target':'p0','text':'방금 판단을 보류한 이유가 무엇인가요?','evidence_ids':[],'day':3})
            v=g.observation('p0');v['question']={**g.events[-1]['data'],'sequence':g.events[-1]['sequence']}
        rows.append((name,v))
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=2)
    parser.add_argument('--model',default='gpt-4.1-mini')
    args=parser.parse_args()
    if not 1<=args.repeats<=5:parser.error('repeats must be 1..5')
    load_dotenv('.env')
    model=create_client('openai',args.model,max_calls=4*2*args.repeats*3)
    memory=MCPMemory();rows=[]
    rawdir=Path('runtime/verification');rawdir.mkdir(parents=True,exist_ok=True)
    try:
        for repeat in range(args.repeats):
            for index,(name,view) in enumerate(cases()):
                for mode in (('before','after') if (repeat+index)%2==0 else ('after','before')):
                    offset=len(model.metrics);start=time.perf_counter()
                    result=decide(view,model,memory=memory,policy='verified',discussion_quality=mode=='after')
                    seconds=time.perf_counter()-start;metrics=model.metrics[offset:]
                    check=discussion.check(view,result['payload']['text'],result['audit']['semantic_support'])
                    row={'case':name,'repeat':repeat,'mode':mode,'seconds':round(seconds,3),'attempts':result['attempts'],'fallback':result['fallback'],
                         'quality_retries':result['audit']['quality_retries'],'detected_issues':check['issues'],
                         'input_tokens':sum(m.get('input_tokens') or 0 for m in metrics),'output_tokens':sum(m.get('output_tokens') or 0 for m in metrics),
                         'text':result['payload']['text'],'audit':result['audit']}
                    rows.append(row);(rawdir/'discussion-quality-v1.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
                    print(json.dumps({k:row[k] for k in ('case','mode','seconds','attempts','fallback','detected_issues')},ensure_ascii=False),flush=True)
        summary={'model':args.model,'scope':'4 synthetic situations x repeats, paired same-input. Both MCP + verified evidence. Only discussion quality toggle differs. Heuristic flags are NOT semantic accuracy or an independent judge. No holdout; order alternated; no warm-up excluded. Raw text stays local.','repeats':args.repeats,'results':{}}
        for mode in ('before','after'):
            group=[r for r in rows if r['mode']==mode]
            summary['results'][mode]={'runs':len(group),'heuristic_flagged_outputs':sum(bool(r['detected_issues']) for r in group),'mean_seconds':round(mean(r['seconds'] for r in group),3),'generation_attempts':sum(r['attempts'] for r in group),'quality_retries':sum(r['quality_retries'] for r in group),'fallbacks':sum(r['fallback'] for r in group),'input_tokens':sum(r['input_tokens'] for r in group),'output_tokens':sum(r['output_tokens'] for r in group)}
        Path('reports/discussion-quality-v1.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False),flush=True)
    finally:memory.close()


if __name__=='__main__':main()
