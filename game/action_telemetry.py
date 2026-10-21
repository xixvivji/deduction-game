"""Bounded server action lifecycle: queue entry through rule acceptance and DB commit.

No browser latency or semantic correctness is inferred from server completion.
"""
import asyncio
from collections import Counter, deque
from contextlib import contextmanager
import copy
import math
import threading
import time
from uuid import uuid4

PHASES = ('queue', 'inference', 'commit_wait', 'apply', 'bookkeeping', 'persist')
DECISION_METRICS = ('seconds', 'api_calls', 'api_errors', 'input_tokens', 'output_tokens',
                    'usage_known_calls', 'retries', 'fallback', 'failed', 'forced')
ACTION_METRICS = ('action_seconds', 'action_completed', 'action_regular_completed',
                  'action_fallback_completed', 'action_failed', 'action_cancelled',
                  'action_applied', 'action_persisted', 'action_count') + tuple(
                      'action_' + p + '_seconds' for p in PHASES)


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


class ActionSpan:
    def __init__(self, owner, backend):
        self.owner = owner
        self.id = uuid4().hex[:12]
        self.started = self.changed = time.perf_counter()
        self.phase = 'queue'
        self.durations = Counter()
        self.data = {'id': self.id, 'backend': backend if backend in ('openai', 'gemini', 'ollama', 'demo') else 'other',
                     'started_at': time.time(), 'applied': False, 'persisted': False}
        self.decision = {}

    def enter(self, phase):
        if phase not in PHASES:
            raise ValueError('Unknown action phase')
        with self.owner.lock:
            now = time.perf_counter()
            self.durations[self.phase] += now - self.changed
            self.phase, self.changed = phase, now

    def mark_applied(self):
        with self.owner.lock:
            self.data['applied'] = True

    def mark_persisted(self):
        with self.owner.lock:
            self.data['persisted'] = True


class ActionMonitor:
    def __init__(self, export, history_limit=200):
        self.export = export
        self.lock = threading.Lock()
        self.active = {}
        self.recent = deque(maxlen=history_limit)
        self.totals = Counter()

    @contextmanager
    def track(self, backend):
        span = ActionSpan(self, backend)
        with self.lock:
            self.active[span.id] = span
        error = None
        try:
            yield span
        except BaseException as caught:
            error = caught
            raise
        finally:
            self._finish(span, error)

    def record_decision(self, trace_id, summary):
        with self.lock:
            span = self.active.get(trace_id)
            if span is None:
                return False
            span.decision = {key: summary[key] for key in DECISION_METRICS}
            return True

    def _finish(self, span, error):
        with self.lock:
            now = time.perf_counter()
            span.durations[span.phase] += now - span.changed
            completed = error is None and span.data['applied'] and span.data['persisted']
            cancelled = isinstance(error, asyncio.CancelledError)
            fallback = bool(span.decision.get('fallback'))
            status = ('fallback_completed' if fallback else 'completed') if completed else 'cancelled' if cancelled else 'failed'
            item = {**span.data, 'status': status, 'failure_phase': None if completed else span.phase,
                    'seconds': now - span.started, 'phase_seconds': dict(span.durations),
                    'decision': dict(span.decision)}
            self.active.pop(span.id, None)
            self.recent.appendleft(item)
            self.totals['finalized'] += 1
            for key, value in {'completed': completed, 'regular_completed': completed and not fallback,
                               'fallback_completed': completed and fallback, 'cancelled': cancelled,
                               'failed': not completed and not cancelled,
                               'applied': item['applied'], 'persisted': item['persisted']}.items():
                self.totals[key] += int(value)
            for key in ('api_calls', 'api_errors', 'usage_known_calls', 'input_tokens', 'output_tokens', 'retries'):
                self.totals[key] += span.decision.get(key, 0)
        values = {**span.decision, 'action_seconds': item['seconds'], 'action_count': 1,
                  'action_completed': int(completed), 'action_regular_completed': int(completed and not fallback),
                  'action_fallback_completed': int(completed and fallback), 'action_cancelled': int(cancelled),
                  'action_failed': int(not completed and not cancelled),
                  'action_applied': int(item['applied']), 'action_persisted': int(item['persisted'])}
        values.update({'action_' + p + '_seconds': item['phase_seconds'].get(p, 0) for p in PHASES})
        try:
            self.export(values)
        except Exception:
            pass  # A telemetry sink cannot turn a saved game action into a failure.

    def snapshot(self):
        with self.lock:
            now = time.perf_counter()
            active = [{**span.data, 'phase': span.phase, 'seconds': now - span.started} for span in self.active.values()]
            recent = copy.deepcopy(list(self.recent))
            totals = dict(self.totals)
        times = [r['seconds'] for r in recent if r['persisted'] and r['status'] in ('completed', 'fallback_completed')]
        phases = {p: sum(r['phase_seconds'].get(p, 0) for r in recent) for p in PHASES}
        n = totals.get('finalized', 0)
        completed = totals.get('completed', 0)
        tokens = totals.get('input_tokens', 0) + totals.get('output_tokens', 0)
        return {'scope': 'AI queue entry to game acceptance and DB commit; excludes browser display and client initialization',
                'totals': totals, 'active': active, 'recent': recent[:30],
                'history_limit': self.recent.maxlen, 'retained_actions': len(recent),
                'completed_rate': completed / n if n else None,
                'regular_completed_rate': totals.get('regular_completed', 0) / n if n else None,
                'known_tokens_per_completed_action': tokens / completed if completed and totals.get('usage_known_calls', 0) else None,
                'recent_completed_samples': len(times),
                'recent_completed_p50_seconds': percentile(times, .50),
                'recent_completed_p95_seconds': percentile(times, .95),
                'recent_phase_seconds': phases,
                'recent_phase_mean_seconds': {p: v / len(recent) if recent else None for p, v in phases.items()}}
