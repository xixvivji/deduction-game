"""Paired server-action experiment. Synthetic LLM wait; no external API or live game DB."""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time

from .engine import Game
from .runtime_settings import RuntimeSettings
from .server import DemoClient, Hub


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def workload(concurrency, delay_ms, cases=4):
    class WaitingDemo(DemoClient):
        def generate(self, *args, **kwargs):
            time.sleep(delay_ms / 1000)
            return super().generate(*args, **kwargs)
    with tempfile.TemporaryDirectory(prefix='night-actions-') as directory:
        settings = replace(RuntimeSettings.from_env(), concurrency=concurrency)
        hub = Hub(str(Path(directory) / 'games.db'), client_factory=WaitingDemo, settings=settings)
        try:
            rooms, inputs = [], []
            for i in range(cases):
                room, _ = hub.create('synthetic', 5)
                room.game = Game(f'fixed-case-{i}', {f'p{j}': 'ai' for j in range(5)}, seed=i + 1)
                room.game.phase = 'discussion'
                room.data.update(status='running', backend='demo')
                room.client = WaitingDemo()
                hub.queue(room); hub.save(room)
                rooms.append(room)
                inputs.append(room.game.observation(hub.active(room)))
            async def action(room):
                actor = hub.active(room)
                with hub.monitor.actions.track('demo') as span:
                    await hub.execute_ai_action(room, actor, room.game.observation(actor), span)
            start = time.perf_counter()
            await asyncio.gather(*(action(room) for room in rooms))
            batch_seconds = time.perf_counter() - start
            snapshot = hub.monitor.snapshot()
            a = snapshot['actions']
            # Compare applied payloads, not timings, randomly generated command IDs, or private room tokens.
            outputs = [[{'actor': h['actor'], 'phase': h['phase'], 'action': h['action']} for h in room.data['history']] for room in rooms]
            return {'concurrency': concurrency, 'cases': cases, 'injected_wait_ms_per_generation': delay_ms,
                    'batch_seconds': batch_seconds, 'actions_per_second': cases / batch_seconds,
                    'completed': a['totals']['completed'], 'fallback_completed': a['totals']['fallback_completed'],
                    'failed': a['totals']['failed'], 'completed_p50_seconds': a['recent_completed_p50_seconds'],
                    'completed_p95_seconds': a['recent_completed_p95_seconds'],
                    'phase_mean_seconds': a['recent_phase_mean_seconds'], 'api_calls': a['totals']['api_calls'],
                    'process_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                    'input_sha256': digest(inputs), 'applied_actions_sha256': digest(outputs)}
        finally:
            hub.db.close(); hub.memory.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=int, choices=[1, 2])
    parser.add_argument('--delay-ms', type=float, default=40)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', default='reports/action-runtime-v1.json')
    args = parser.parse_args()
    if not 0 <= args.delay_ms <= 1000 or not 1 <= args.rounds <= 10:
        parser.error('delay-ms 0..1000, rounds 1..10')
    if args.worker:
        print(json.dumps(asyncio.run(workload(args.worker, args.delay_ms))))
        return
    results = {'1': [], '2': []}
    env = {**os.environ, 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false', 'GAME_MEMORY_TOKEN_CACHE': 'false'}
    for repetition in range(args.rounds):
        for concurrency in ([1, 2] if repetition % 2 == 0 else [2, 1]):
            result = subprocess.check_output([sys.executable, '-m', 'game.benchmark_actions', '--worker', str(concurrency), '--delay-ms', str(args.delay_ms)], env=env, text=True)
            results[str(concurrency)].append(json.loads(result))
    samples = [s for values in results.values() for s in values]
    assert len({s['input_sha256'] for s in samples}) == 1, 'Inputs differ'
    assert len({s['applied_actions_sha256'] for s in samples}) == 1, 'Applied actions differ'
    assert all(s['completed'] == s['cases'] and s['api_calls'] == 0 for s in samples)
    summary = {}
    for mode, values in results.items():
        summary[mode] = {'completed': sum(v['completed'] for v in values),
                         'median_batch_seconds': statistics.median(v['batch_seconds'] for v in values),
                         'median_completed_p95_seconds': statistics.median(v['completed_p95_seconds'] for v in values),
                         'median_queue_mean_seconds': statistics.median(v['phase_mean_seconds']['queue'] for v in values),
                         'median_peak_rss_bytes': statistics.median(v['process_peak_rss_bytes'] for v in values)}
    report = {'scope': 'Synthetic 4-room, one-action-per-room workload, 40ms default injected generation wait. Real LangGraph, local retrieval, rule application and temporary SQLite commit. No external LLM, API charge, browser latency, real-model quality, or production speedup measurement. Each repetition is a fresh process; initialization/imports excluded from timers. RSS includes whole-process imports and is a peak, not per-request memory.',
              'rounds': args.rounds, 'input_sha256': samples[0]['input_sha256'], 'inputs_equal': True, 'applied_actions_equal': True,
              'changed_setting': 'GAME_AI_CONCURRENCY: 1 -> 2; no automatic concurrency change',
              'results': results, 'summary': summary}
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(path), 'summary': summary, 'inputs_equal': True, 'applied_actions_equal': True}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
