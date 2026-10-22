import asyncio
import json
import threading
import unittest
from unittest.mock import patch

from game.action_telemetry import ActionMonitor
from game.engine import Game, RuleError
from game.inference_pool import InferencePool
from game.mcp_memory import MemoryUnavailable
from game.server import DemoClient, Hub


class ActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hub = Hub(':memory:', client_factory=DemoClient)
        self.addCleanup(self.hub.db.close)

    def room(self):
        room, _ = self.hub.create('PRIVATE_NAME', 5)
        room.game = Game('PRIVATE_GAME', {f'p{i}': 'ai' for i in range(5)}, seed=1)
        room.game.phase = 'discussion'
        room.game._emit('발언', {'actor': 'p0', 'text': 'PRIVATE_TEXT'})
        room.data.update(status='running', backend='demo')
        room.client = DemoClient()
        self.hub.queue(room)
        self.hub.save(room)
        return room

    async def execute(self, room):
        actor = self.hub.active(room)
        with self.hub.monitor.actions.track('demo') as span:
            await self.hub.execute_ai_action(room, actor, room.game.observation(actor), span)

    async def test_complete_requires_engine_acceptance_and_saved_state_and_one_export(self):
        room = self.room()
        samples = []
        class Exporter:
            def submit(self, values): samples.append(values)
            def snapshot(self): return {}
        self.hub.monitor.exporter = Exporter()
        await self.execute(room)
        data = self.hub.monitor.snapshot()
        action = data['actions']['recent'][0]
        stored = json.loads(self.hub.db.execute('SELECT data FROM rooms').fetchone()[0])
        self.assertEqual(stored['game']['version'], room.game.version)
        self.assertEqual(stored['index'], 1)
        self.assertEqual(action['status'], 'completed')
        self.assertTrue(action['applied'] and action['persisted'])
        self.assertEqual(action['id'], data['recent'][0]['id'])
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['action_completed'], 1)
        self.assertEqual(action['decision']['failed'], False)
        self.assertAlmostEqual(sum(action['phase_seconds'].values()), action['seconds'])
        for secret in ('PRIVATE_NAME', 'PRIVATE_GAME', 'PRIVATE_TEXT', 'payload', 'p0'):
            self.assertNotIn(secret, json.dumps(data))

    async def test_completed_generation_is_not_success_when_engine_rejects(self):
        room = self.room()
        with patch.object(room.game, 'submit', side_effect=RuleError('PRIVATE_RULE')):
            with self.assertRaises(RuleError): await self.execute(room)
        data = self.hub.monitor.snapshot()
        self.assertEqual(data['recent'][0]['status'], 'complete')
        action = data['actions']['recent'][0]
        self.assertEqual((action['status'], action['failure_phase']), ('failed', 'apply'))
        self.assertFalse(action['applied'] or action['persisted'])
        self.assertEqual(data['actions']['completed_rate'], 0)
        self.assertIsNone(data['actions']['recent_completed_p95_seconds'])
        self.assertNotIn('PRIVATE_RULE', json.dumps(data))

    async def test_db_failure_is_not_counted_as_saved_completion(self):
        room = self.room()
        initial = room.game.version
        with patch.object(self.hub, 'save', side_effect=OSError('PRIVATE_DB')):
            with self.assertRaises(OSError): await self.execute(room)
        action = self.hub.monitor.actions.snapshot()['recent'][0]
        self.assertEqual((action['status'], action['failure_phase']), ('failed', 'persist'))
        self.assertTrue(action['applied'])
        self.assertFalse(action['persisted'])
        stored = json.loads(self.hub.db.execute('SELECT data FROM rooms').fetchone()[0])
        self.assertEqual(stored['game']['version'], initial)

    async def test_cancelled_queue_is_counted_without_a_model_call(self):
        self.hub.inference = InferencePool(1)
        room = self.room()
        async with self.hub.inference.slot():
            task = asyncio.create_task(self.execute(room))
            await asyncio.sleep(.01)
            self.assertEqual(self.hub.monitor.actions.snapshot()['active'][0]['phase'], 'queue')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        data = self.hub.monitor.snapshot()
        self.assertEqual(data['actions']['recent'][0]['failure_phase'], 'queue')
        self.assertEqual(data['actions']['totals']['cancelled'], 1)
        self.assertFalse(data['totals'].get('decisions'))
        self.assertFalse(data['actions']['active'])

    async def test_queue_timeout_is_a_failure_even_without_a_decision(self):
        self.hub.inference = InferencePool(1, wait_timeout=.01)
        room = self.room()
        from game.providers import ProviderError
        async with self.hub.inference.slot():
            with self.assertRaises(ProviderError): await self.execute(room)
        data = self.hub.monitor.snapshot()
        self.assertEqual(data['actions']['totals']['failed'], 1)
        self.assertEqual(data['actions']['recent'][0]['failure_phase'], 'queue')
        self.assertFalse(data['totals'].get('decisions'))

    async def test_cancelled_generation_keeps_usage_but_does_not_apply(self):
        room = self.room()
        initial = room.game.version
        started, finish = threading.Event(), threading.Event()
        def decision(*args, **kwargs):
            started.set()
            finish.wait(3)
            room.client.metrics.append({'input_tokens': 7, 'output_tokens': 3, 'success': True})
            return {'kind': 'speak', 'payload': {'text': 'test'}, 'attempts': 1, 'fallback': False, 'audit': {}}
        with patch('game.server.decide', side_effect=decision):
            task = asyncio.create_task(self.hub.run(room))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                task.cancel()
                finish.set()
                with self.assertRaises(asyncio.CancelledError): await task
            finally: finish.set()
        data = self.hub.monitor.snapshot()
        self.assertEqual(data['recent'][0]['status'], 'complete')
        self.assertEqual(data['actions']['totals']['cancelled'], 1)
        self.assertEqual(data['actions']['totals']['input_tokens'], 7)
        self.assertEqual(data['actions']['totals']['output_tokens'], 3)
        self.assertFalse(data['actions']['totals']['completed'])
        self.assertEqual(room.game.version, initial)
        self.assertEqual(room.data['status'], 'paused')

    async def test_memory_failure_is_recorded_and_room_pauses(self):
        room = self.room()
        with patch.object(self.hub.memory, 'search', side_effect=MemoryUnavailable('PRIVATE_MCP')):
            await self.hub.run(room)
        data = self.hub.monitor.actions.snapshot()
        self.assertEqual((data['recent'][0]['status'], data['recent'][0]['failure_phase']), ('failed', 'inference'))
        self.assertEqual(room.data['status'], 'paused')
        self.assertEqual(data['totals']['api_calls'], 0)
        self.assertNotIn('PRIVATE_MCP', json.dumps(data))

    async def test_fallback_can_be_applied_but_is_separate_from_regular_completion(self):
        room = self.room()
        class Bad(DemoClient):
            def generate(self, *args):
                return {'text': 'p2가 p1을 죽였어.', 'decision_summary': 'test', 'evidence_ids': [],
                        'claim_type': 'none', 'claim_subject': None, 'claim_value': ''}
        room.client = Bad()
        await self.execute(room)
        data = self.hub.monitor.actions.snapshot()
        self.assertEqual(data['totals']['completed'], 1)
        self.assertEqual(data['totals']['fallback_completed'], 1)
        self.assertEqual(data['totals']['regular_completed'], 0)
        self.assertEqual(data['totals']['retries'], 1)

    async def test_concurrent_actions_remain_independent_and_history_bounded(self):
        self.hub.monitor.actions = ActionMonitor(self.hub.monitor._export, history_limit=2)
        await asyncio.gather(*(self.execute(self.room()) for _ in range(4)))
        data = self.hub.monitor.actions.snapshot()
        self.assertEqual(data['totals']['completed'], 4)
        self.assertEqual(len(data['recent']), 2)
        self.assertEqual(len({s['id'] for s in data['recent']}), 2)
        self.assertFalse(data['active'])
        self.assertIsNone(data['known_tokens_per_completed_action'])


class AccountingTests(unittest.TestCase):
    def test_failed_usage_is_included_in_cost_per_completed_action(self):
        exported = []
        m = ActionMonitor(exported.append)
        from game.action_telemetry import DECISION_METRICS
        summary = {k: 0 for k in DECISION_METRICS}
        summary.update(input_tokens=12, output_tokens=3, usage_known_calls=1, api_calls=2)
        for fail in (True, False):
            try:
                with m.track('demo') as action:
                    m.record_decision(action.id, summary)
                    if fail: raise RuntimeError('private')
                    action.mark_applied(); action.mark_persisted()
            except RuntimeError: pass
        data = m.snapshot()
        self.assertEqual(data['completed_rate'], .5)
        self.assertEqual(data['known_tokens_per_completed_action'], 30)
        self.assertEqual(data['totals']['usage_known_calls'], 2)
        self.assertEqual(data['totals']['api_calls'], 4)
        self.assertEqual(len(exported), 2)

    def test_sink_failure_does_not_fail_saved_action(self):
        def fail(values): raise OSError('private')
        m = ActionMonitor(fail)
        with m.track('demo') as action:
            action.mark_applied(); action.mark_persisted()
        self.assertEqual(m.snapshot()['totals']['completed'], 1)


class ExporterTests(unittest.TestCase):
    def test_action_metrics_reach_sink_with_only_allowed_fields(self):
        from game.mlflow_tracking import RuntimeExporter
        received, logged = threading.Event(), []
        class Client:
            def __init__(self, uri): pass
            def health(self): pass
            def experiment(self, name): return 'test'
            def create_run(self, *args): return 'run'
            def log(self, run, values, step): logged.append(values); received.set()
            def end(self, run): pass
            def close(self): pass
        exporter = RuntimeExporter('http://127.0.0.1:5500', client_factory=Client)
        try:
            exporter.submit({'action_seconds': 2, 'action_completed': 1, 'action_queue_seconds': .5,
                             'usage_known_calls': 1, 'prompt': 'PRIVATE', 'actor': 'p0'})
            self.assertTrue(received.wait(2))
        finally: exporter.close()
        self.assertEqual(logged, [{'action_seconds': 2., 'action_completed': 1., 'action_queue_seconds': .5, 'usage_known_calls': 1.}])
