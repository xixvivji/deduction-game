"""Offline paired workload: retrieval, belief update, prompt serialization. No LLM/API calls."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
from .engine import Game
from .memory import retrieve
from .runtime_cache import TOKEN_CACHE
from . import beliefs,discussion
from .ai import prompt_messages


def workload(optimized,rounds=25):
    TOKEN_CACHE.clear()
    g=Game('runtime-benchmark',{f'p{i}':'ai' for i in range(7)},seed=1)
    g.players['p0'].role='seer';g.phase='discussion';g.day=2
    for i in range(180):g._emit('발언',{'actor':f'p{i%7}','text':f'기록 {i}: 창고에서 본 위치와 조사 당시를 구분해 이야기하겠습니다.','day':1})
    g._emit('조사',{'target':'p2','team':'village','day':1},'p0')
    previous=None;durations=[];processed=0;prompt_bytes=0;hits=0;misses=0;digest=hashlib.sha256()
    tracemalloc.start()
    for i in range(rounds):
        g._emit('발언',{'actor':f'p{i%7}','text':f'추가 발언 {i}: 목격 기록을 비교하겠습니다.','day':2})
        v=g.observation('p0');start=time.perf_counter()
        selected=retrieve(v,use_cache=optimized)
        ledger=beliefs.update(v,previous,incremental=optimized)
        discussion.attach_context(selected,v);beliefs.attach(selected,ledger,v)
        prompt=prompt_messages(selected,compact_context=optimized)
        prompt_bytes+=len(json.dumps(prompt,ensure_ascii=False).encode())
        durations.append((time.perf_counter()-start)*1000)
        processed+=ledger['update_stats']['fact_events_processed']
        hits+=selected['memory'].get('cache_hits',0);misses+=selected['memory'].get('cache_misses',0)
        semantic=beliefs.public_ledger(ledger);semantic.pop('update_stats',None)
        digest.update(json.dumps({'events':selected['events'],'beliefs':semantic},sort_keys=True,ensure_ascii=False).encode())
        previous=ledger
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    return {'rounds':rounds,'mean_ms':round(statistics.mean(durations),3),'first_ms':round(durations[0],3),'warm_mean_ms':round(statistics.mean(durations[1:]),3),
            'python_tracked_peak_bytes':peak,'process_peak_rss_bytes':rss,'fact_events_processed':processed,
            'prompt_utf8_bytes_total':prompt_bytes,'cache_hits':hits,'cache_misses':misses,'cache':TOKEN_CACHE.stats(),
            'semantic_digest':digest.hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',choices=['before','after']);args=parser.parse_args()
    if args.worker:
        print(json.dumps(workload(args.worker=='after')));return
    results={mode:json.loads(subprocess.check_output([sys.executable,'-m','game.benchmark_runtime','--worker',mode])) for mode in ('before','after')}
    report={'scope':'Offline synthetic workload, 25 incremental snapshots in separate fresh processes. No API calls, no token/billing or generated-answer-quality measurement. Timings include tracemalloc overhead. Python tracked peak is measured allocations during loop; RSS is whole process high-water mark including imports. Cache byte count is an estimate, not a process RAM cap.',
            'semantic_outputs_equal':results['before']['semantic_digest']==results['after']['semantic_digest'],'results':results}
    assert report['semantic_outputs_equal'],'Retrieval or belief semantics changed'
    Path('reports/runtime-optimization-v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
