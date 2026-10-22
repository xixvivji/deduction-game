"""Replay retrieval over completed local games; publish aggregates only, no API calls."""
import argparse
import json
import sqlite3
import statistics
import time
from pathlib import Path
from .engine import Game
from .memory import retrieve
from .runtime_cache import TOKEN_CACHE


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='runtime/games.db')
    args=parser.parse_args()
    db=sqlite3.connect('file:'+str(Path(args.db).resolve())+'?mode=ro',uri=True)
    views=[];games=0
    for (raw,) in db.execute("SELECT data FROM rooms WHERE json_extract(data,'$.status')='finished'"):
        data=json.loads(raw)
        if not data.get('game'):continue
        game=Game.restore(data['game']);games+=1
        views.extend(game.observation(pid) for pid in game.players)
    db.close()
    if not views:raise SystemExit('No completed games')
    timings={False:[],True:[]};hits=misses=0
    # Alternate order to reduce systematic warm-up/order bias; fresh cache each trial.
    for trial in range(6):
        TOKEN_CACHE.clear()
        baseline={}
        for enabled in ([False,True] if trial%2==0 else [True,False]):
            start=time.perf_counter()
            for repeat in range(5):
                for i,view in enumerate(views):
                    result=retrieve(view,query=('목격 조사','투표 의심','발언 위치')[repeat%3],use_cache=enabled)
                    key=(repeat,i)
                    selected=[e['sequence'] for e in result['events']]
                    if key in baseline:assert baseline[key]==selected
                    else:baseline[key]=selected
                    if enabled:
                        hits+=result['memory'].get('cache_hits',0)
                        misses+=result['memory'].get('cache_misses',0)
            timings[enabled].append((time.perf_counter()-start)*1000/(5*len(views)))
    report={'scope':'Local completed-game final observations, repeated retrieval queries; not historical decision replay, live LLM latency, token cost or quality evaluation. Six alternating-order paired trials; aggregate only.',
            'games':games,'observations':len(views),'queries_per_trial':5*len(views),'selected_events_equal':True,
            'uncached_mean_ms':statistics.mean(timings[False]),'cached_mean_ms':statistics.mean(timings[True]),
            'uncached_trials_ms':timings[False],'cached_trials_ms':timings[True],
            'cache_hits':hits,'cache_misses':misses,'cache_final':TOKEN_CACHE.stats()}
    Path('reports/saved-memory-cache-v1.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
