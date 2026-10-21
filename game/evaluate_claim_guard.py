"""Paired offline replay of a sealed utterance set against old/current code.

Outputs are fixed synthetic model utterances. Retry counts are graph replay
counts, not paid API usage or live generative quality. Never tunes the guard.
"""
import argparse
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

ROOT=Path(__file__).resolve().parents[1]
FILES=('game/claim_guard.py','game/evidence.py','game/decision.py','game/ai.py')


def fingerprints(root=ROOT):
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in FILES}


def worker():
    from game.engine import Game
    from game.evidence import verify
    from game.decision import decide
    import game.evidence
    data=json.loads((ROOT/'eval/claim_guard_holdout.json').read_text())
    rows=[]
    for case in data['cases']:
        g=Game('guard-heldout',{f'p{i}':'ai' for i in range(11)},seed=1)
        g.players['p0'].role=case['role'];g.phase='discussion';g.day=2
        v=g.observation('p0');v['events']+=case['events']
        claim={'claim_type':'none','claim_subject':None,'claim_value':''}
        times=[]
        for _ in range(10):
            start=time.perf_counter()
            result=verify(v,claim,case['evidence_ids'],{'text':case['text']})
            times.append((time.perf_counter()-start)*1000)
        class Replay:
            metrics=[]
            def generate(self,*args):
                return {'text':case['text'],'decision_summary':'고정 합성 출력 재생',
                        'evidence_ids':list(case['evidence_ids']),**claim}
        outcome=decide(v,Replay(),policy='verified')
        rows.append({'case':case['id'],'expected_allow':case['allow'],'allowed':result['ok'],
                     'status':result['status'],'correct':result['ok']==case['allow'],
                     'median_check_ms':round(statistics.median(times),4),
                     'replay_attempts':outcome['attempts'],'replay_retries':outcome['attempts']-1,
                     'replay_fallback':outcome['fallback'],
                     'exemption_reasons':sorted({x['reason'] for x in result.get('opinion_guard',{}).get('exempted',[])}) if result.get('opinion_guard') else []})
    print(json.dumps({'fingerprints':fingerprints(Path(game.evidence.__file__).resolve().parents[1]),'rows':rows},ensure_ascii=False))


def run_at(cwd):
    runner='import runpy; runpy.run_path('+repr(str(Path(__file__).resolve()))+',run_name="__main__")'
    env={**os.environ,'LANGSMITH_TRACING':'false','GUARD_EVAL_WORKER':'1','PYTHONDONTWRITEBYTECODE':'1'}
    env.pop('PYTHONPATH',None)
    result=subprocess.run([sys.executable,'-c',runner],cwd=cwd,env=env,check=True,capture_output=True,text=True)
    return json.loads(result.stdout)


def summarize(rows):
    allowed=[r for r in rows if r['expected_allow']];blocked=[r for r in rows if not r['expected_allow']]
    fp=sum(not r['allowed'] for r in allowed);fn=sum(r['allowed'] for r in blocked)
    return {'cases':len(rows),'correct':sum(r['correct'] for r in rows),
            'allowed_cases':len(allowed),'unsupported_cases':len(blocked),
            'false_blocks':fp,'missed_blocks':fn,
            'false_block_rate':fp/len(allowed),'miss_rate':fn/len(blocked),
            'allowed_replay_retries':sum(r['replay_retries'] for r in allowed),
            'unsupported_replay_retries':sum(r['replay_retries'] for r in blocked),
            'allowed_fallbacks':sum(r['replay_fallback'] for r in allowed),
            'mean_case_median_check_ms':round(statistics.mean(r['median_check_ms'] for r in rows),4)}


def reviewed_report(raw):
    review=json.loads((ROOT/'eval/claim_guard_label_review.json').read_text())
    assert set(review['reviewed_ids'])=={r['case'] for r in raw['after']['rows']}
    result={'baseline_commit':raw['baseline_commit'],'heldout_sha256':raw['heldout_sha256'],
            'generated_cases':len(raw['after']['rows']),'excluded':review['excluded'],
            'scope':'Content-reviewed subset of sealed synthetic cases. Preserved raw results; no holdout tuning. Implementing-assistant review, not independent human labels or live generative quality.'}
    for mode in ('before','after'):
        rows=[r for r in raw[mode]['rows'] if r['case'] not in review['excluded']]
        result[mode]={'fingerprints':raw[mode]['fingerprints'],'rows':rows,'summary':summarize(rows)}
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'reports/claim-guard-scope-v2.json')
    parser.add_argument('--review',action='store_true',help='Apply documented exclusions to the preserved first run')
    args=parser.parse_args();target=args.output
    if args.review:
        if target.exists():raise SystemExit('출력 파일이 이미 존재합니다. --output으로 새 경로를 지정하세요.')
        result=reviewed_report(json.loads((ROOT/'reports/claim-guard-scope-v2.json').read_text()))
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({m:result[m]['summary'] for m in ('before','after')},ensure_ascii=False))
        return
    if target.exists():raise SystemExit('평가 결과는 덮어쓰지 않습니다. 새 실험은 새 버전으로 분리하세요.')
    freeze=json.loads((ROOT/'eval/claim_guard_freeze.json').read_text())
    assert freeze['source_sha256']==fingerprints(), '보류 평가 전 코드 동결 불일치'
    heldout=ROOT/'eval/claim_guard_holdout.json'
    assert freeze['heldout_sha256']==hashlib.sha256(heldout.read_bytes()).hexdigest(), '보류 입력 변경'
    archive=subprocess.run(['git','archive',freeze['baseline_commit'],'game'],cwd=ROOT,check=True,capture_output=True).stdout
    with tempfile.TemporaryDirectory(prefix='claim-guard-before-') as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(tmp,filter='data')
        before=run_at(tmp)
    after=run_at(ROOT)
    assert after['fingerprints']==freeze['source_sha256']
    report={'baseline_commit':freeze['baseline_commit'],'heldout_sha256':freeze['heldout_sha256'],
            'scope':'24 model-generated synthetic utterances sealed before implementation. Prescribed labels reviewed after freeze; no heldout tuning. Fixed-output graph replay, 10 verifier timing repetitions per case. Not externally human-labeled or live game model performance. No paid API calls in this evaluation; token/billing savings unavailable.',
            'before':{**before,'summary':summarize(before['rows'])},
            'after':{**after,'summary':summarize(after['rows'])}}
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({mode:report[mode]['summary'] for mode in ('before','after')},ensure_ascii=False))


if __name__=='__main__':
    worker() if os.environ.get('GUARD_EVAL_WORKER')=='1' else main()
