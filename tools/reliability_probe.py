"""Versioned AI replay/live evaluation and isolated MCP fault injection. No raw answers in reports."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time

from game.decision import decide
from game.engine import Game
from game.memory_backend import LocalMemory, scoped
from game.mcp_memory import MCPMemory, MemoryUnavailable
from game.time_budget import run_with_budget

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'd873595'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fixtures():
    rows = []
    for name in ('supported', 'repeated_failure', 'repairable', 'new_evidence', 'changed_claim', 'normal_opinion', 'untyped_fact', 'private_citation'):
        game = Game('reliability-' + name, {f'p{i}': 'ai' for i in range(7)}, seed=42)
        for player in game.players.values(): player.role = 'citizen'
        game.players['p1'].role = 'wolf'
        game.phase = 'discussion' if name in ('normal_opinion', 'untyped_fact') else 'vote'
        if name in ('supported', 'new_evidence', 'changed_claim'):
            game._emit('조사', {'target': 'p1', 'team': 'wolf', 'day': 1}, 'p0')
        else:
            game._emit('탐색 목격', {'player': 'p1', 'location': '광장', 'day': 1}, 'p0')
        if name == 'private_citation':
            game._emit('조사', {'target': 'p1', 'team': 'wolf', 'day': 1}, 'p2')
        rows.append({'id': name, 'view': game.observation('p0'),
                     'expected': 'p1' if name in ('supported', 'new_evidence', 'changed_claim') else None,
                     'allowed': name in ('supported', 'new_evidence', 'changed_claim', 'normal_opinion', 'repairable')})
    return rows


class ReplayClient:
    def __init__(self, name): self.name = name; self.calls = 0; self.metrics = []
    def generate(self, view, schema, feedback=None):
        self.calls += 1
        payload = {'target': 'p1', 'decision_summary': '평가용 고정 응답', 'evidence_ids': [2],
                   'claim_type': 'confirmed_wolf', 'claim_subject': 'p1', 'claim_value': ''}
        if self.name == 'new_evidence' and not any(e['sequence'] == 2 for e in view['events']): payload['evidence_ids'] = [1]
        if self.name == 'changed_claim' and self.calls == 1: payload['claim_subject'] = 'p2'; payload['target'] = 'p2'
        if self.name == 'private_citation': payload['evidence_ids'] = [3]
        if self.name == 'repairable' and feedback:
            payload.update(target=None, evidence_ids=[], claim_type='none', claim_subject=None)
        if self.name in ('normal_opinion', 'untyped_fact'):
            payload.pop('target')
            payload.update(text='아직 판단하기 어렵습니다.' if self.name == 'normal_opinion' else 'p1이 p2를 죽였어.',
                           evidence_ids=[], claim_type='none', claim_subject=None)
        return payload


class CountingMemory(LocalMemory):
    def __init__(self, name): self.case = name; self.calls = 0
    def search(self, view, query=None, expanded=False):
        self.calls += 1
        result = super().search(view, query, expanded)
        if self.case == 'new_evidence' and not expanded:
            result['events'] = [e for e in result['events'] if e['sequence'] != 2]
        return result


def summarize(rows):
    seconds = sorted(r['seconds'] for r in rows)
    known = [r for r in rows if r['input_tokens'] is not None]
    return {'runs': len(rows), 'correct': sum(r['correct'] for r in rows),
            'model_calls': sum(r['attempts'] for r in rows), 'research_calls': sum(r['research_calls'] for r in rows),
            'fallbacks': sum(r['fallback'] for r in rows), 'false_blocks': sum(r['false_block'] for r in rows),
            'scope_violations': sum(r['scope_violations'] for r in rows),
            'mean_ms': round(statistics.mean(seconds)*1000, 3), 'p95_ms': round(seconds[math.ceil(.95*len(seconds))-1]*1000, 3),
            'input_tokens': sum(r['input_tokens'] for r in known) if known else None,
            'output_tokens': sum(r['output_tokens'] for r in known) if known else None,
            'usage_unknown_calls': sum(r['usage_unknown_calls'] for r in rows)}


def decisions(repeats=5, live=False):
    data = fixtures()
    client = memory = transport = None
    config = {'kind': 'scripted', 'repeats': repeats, 'policy': 'verified', 'discussion_quality': False, 'backend': 'local'}
    if live:
        from dotenv import load_dotenv
        from game.providers import create_client, PooledTransport
        from game.evaluate_evidence import cases
        load_dotenv(ROOT / '.env')
        provider = os.environ.get('GAME_PROVIDER', 'openai'); model = os.environ.get('GAME_MODEL')
        if provider not in ('openai', 'gemini'): raise ValueError('Cloud provider required')
        data = cases()
        transport = PooledTransport()
        client = create_client(provider, model, max_calls=len(data)*repeats*3, transport=transport)
        client.check(); memory = MCPMemory()
        config.update(kind='live', provider=provider, model=model, backend='mcp')
    rows = []
    try:
        for repeat in range(repeats):
            for case in data:
                current = client if live else ReplayClient(case['id'])
                store = memory if live else CountingMemory(case['id'])
                before = len(current.metrics); started = time.perf_counter()
                result = run_with_budget(120, decide, copy.deepcopy(case['view']), current, memory=store,
                                         policy='verified', discussion_quality=False)
                elapsed = time.perf_counter() - started; usage = current.metrics[before:]
                available = {e['sequence'] for e in scoped(case['view'])['events']}
                known = [m for m in usage if m.get('input_tokens') is not None and m.get('output_tokens') is not None]
                row = {'case': case['id'], 'repeat': repeat, 'seconds': round(elapsed, 6), 'attempts': result['attempts'],
                       'research_calls': result['audit']['research_rounds'], 'fallback': result['fallback'],
                       'correct': result['payload'].get('target') == case['expected'],
                       'false_block': bool(case.get('allowed', case['expected'] is not None) and result['fallback']),
                       'scope_violations': len(set(result['audit'].get('evidence_ids', [])) - available),
                       'input_tokens': sum(m['input_tokens'] for m in known) if known else None,
                       'output_tokens': sum(m['output_tokens'] for m in known) if known else None,
                       'usage_unknown_calls': len(usage)-len(known)}
                if case['id'] in ('normal_opinion', 'untyped_fact'):
                    row['correct'] = not result['fallback'] if case['id'] == 'normal_opinion' else result['fallback']
                rows.append(row)
                if live: print(json.dumps({k: row[k] for k in ('case', 'seconds', 'attempts', 'research_calls', 'correct')}), flush=True)
    finally:
        if memory: memory.close()
        if transport: transport.close()
    return {'config': config, 'fixture_sha256': digest(data), 'rows': rows, 'summary': summarize(rows)}


def owned_child():
    """Only kill a direct child of this evaluation process with the exact MCP module argv."""
    candidates = []
    if Path('/proc/self/status').exists():
        # Slim Linux containers do not ship ps; inspect only direct children via procfs.
        for process in Path('/proc').iterdir():
            if not process.name.isdecimal(): continue
            try:
                status = (process/'status').read_text()
                parent = int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
                if parent != os.getpid(): continue
                argv = (process/'cmdline').read_bytes().decode().strip('\0').split('\0')
                if argv[-2:] == ['-m','game.mcp_memory_server']: candidates.append(int(process.name))
            except (OSError, ValueError, StopIteration): continue
    else:
        listing = subprocess.check_output(['ps', '-eo', 'pid=,ppid=,args='], text=True)
        for line in listing.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) == 3 and int(parts[1]) == os.getpid() and parts[2].split()[-2:] == ['-m', 'game.mcp_memory_server']:
                candidates.append(int(parts[0]))
    if len(candidates) != 1: raise RuntimeError('Evaluation MCP child not uniquely identified')
    return candidates[0]


def faults(repeats=3, window=5.):
    rows = []; view = fixtures()[0]['view']
    for _ in range(repeats):
        memory = MCPMemory()
        try:
            memory.search(view)
            pid = owned_child(); started = time.perf_counter(); os.kill(pid, signal.SIGTERM)
            errors = 0; recovered = False; first_failure = None
            # On-demand probes exercise the real session, not an injected success stub.
            time.sleep(.05)
            while time.perf_counter()-started < window:
                try:
                    result = memory.search(view)
                    if any(e['type'] == '조사' for e in result['events']): recovered = True; break
                except MemoryUnavailable:
                    errors += 1
                    if first_failure is None: first_failure = time.perf_counter()-started
                time.sleep(.1)
            stats = memory.snapshot() if hasattr(memory, 'snapshot') else {}
            rows.append({'recovered': recovered, 'recovery_seconds': round(time.perf_counter()-started, 4) if recovered else None,
                         'observation_seconds': round(time.perf_counter()-started, 4), 'failed_requests': errors,
                         'detection_seconds': stats.get('last_detection_seconds', first_failure),
                         'reconnects': stats.get('recoveries', 0)})
        finally: memory.close()
    times = [r['recovery_seconds'] for r in rows if r['recovered']]
    return {'kind': 'actual_stdio_process_termination', 'window_seconds': window, 'rows': rows,
            'summary': {'trials': len(rows), 'recovered': len(times), 'failed_requests': sum(r['failed_requests'] for r in rows),
                        'mean_recovery_seconds': round(statistics.mean(times), 4) if times else None}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--faults', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10: parser.error('repeats must be 1..10')
    os.environ['LANGSMITH_TRACING'] = 'false'
    files = ['game/decision.py', 'game/mcp_memory.py', 'tools/reliability_probe.py']
    report = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
              'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'source_sha256': {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
              'decisions': decisions(args.repeats, args.live),
              'scope': 'Synthetic development fixtures, not independent holdout or game win rate. Scripted latency is local graph overhead, not API latency. Live calls are stochastic and time-separated. Tokens are returned API usage only, never billing estimates. Fault recovery measured by active probes on an isolated MCP subprocess; no production games touched.'}
    if args.faults: report['mcp'] = faults()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'decisions': report['decisions']['summary'], 'mcp': report.get('mcp', {}).get('summary')}, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
