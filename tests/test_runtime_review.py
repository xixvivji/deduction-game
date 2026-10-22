"""Regression cases found while reviewing runtime lifecycle and accounting."""
import asyncio
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch

from game.ai import action_spec
from game.engine import Game
from game.inference_pool import InferencePool, run_blocking
from game.providers import CloudClient, ProviderError
from game.server import Hub
from game.time_budget import run_with_budget


class CancellationReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_keeps_slot_until_thread_finishes(self):
        pool = InferencePool(1)
        started, finish = threading.Event(), threading.Event()
        def work():
            started.set()
            finish.wait(3)
        async def job():
            async with pool.slot():
                await run_blocking(work)
        task = asyncio.create_task(job())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            await asyncio.sleep(.01)
            task.cancel()
            await asyncio.sleep(.01)
            self.assertEqual(pool.active, 1)
            self.assertFalse(task.done())
        finally:
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(pool.active, 0)

    async def test_shutdown_saves_attempt_usage_without_committing_cancelled_action(self):
        class Client:
            calls = 0
            def __init__(self): self.metrics = []
            def check(self): pass
        client = Client()
        hub = Hub(':memory:', client_factory=lambda: client)
        self.addCleanup(hub.db.close)
        room, _ = hub.create('review', 5)
        room.game = Game(room.data['id'], {f'p{i}':'ai' for i in range(5)}, seed=1)
        room.data['status'] = 'running'
        hub.queue(room)
        initial_version = room.game.version
        started, finish = threading.Event(), threading.Event()
        def blocked_decision(*args, **kwargs):
            client.calls += 1
            started.set()
            finish.wait(3)
            client.metrics.append({'total_tokens':17, 'success':True})
            return {'kind':'night', 'payload':{'target':None, 'location':'광장'}}
        with patch('game.server.decide', side_effect=blocked_decision):
            hub.launch(room)
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                room.task.cancel()
                finish.set()
                with self.assertRaises(asyncio.CancelledError): await room.task
            finally:
                finish.set()
        stored = json.loads(hub.db.execute('SELECT data FROM rooms').fetchone()[0])
        self.assertEqual(stored['calls'], 1)
        self.assertEqual(stored['metrics'][0]['total_tokens'], 17)
        self.assertEqual(stored['status'], 'paused')
        self.assertEqual(room.game.version, initial_version)
        self.assertFalse(room.busy)
        self.assertEqual(stored['history'], [])


class UsageReviewTests(unittest.TestCase):
    def test_late_cloud_response_keeps_usage_while_rejecting_action(self):
        game = Game('review', {str(i):'ai' for i in range(5)}, seed=1)
        view = game.observation('0')
        clock = [0.]
        def transport(*args):
            clock[0] = 3.
            return {'status':'completed', 'output':[],
                    'usage':{'input_tokens':11, 'output_tokens':6, 'total_tokens':17}}
        client = CloudClient('openai', 'test', transport=transport)
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test'}), patch('game.time_budget.time.monotonic', side_effect=lambda:clock[0]):
            with self.assertRaises(ProviderError):
                run_with_budget(2, client.generate, view, action_spec(view)[1])
        self.assertEqual(client.metrics[0]['total_tokens'], 17)
        self.assertFalse(client.metrics[0]['success'])


class LifecycleReviewTests(unittest.TestCase):
    def test_memory_closes_when_transport_startup_fails(self):
        from fastapi.testclient import TestClient
        from game.server import create_app
        memory = Mock()
        with patch('game.server.LocalMemory', return_value=memory), patch('game.server.PooledTransport', side_effect=RuntimeError('init failed')):
            with self.assertRaisesRegex(RuntimeError, 'init failed'):
                with TestClient(create_app(':memory:', auto_workers=False)): pass
        memory.close.assert_called_once()

    def test_invalid_config_is_rejected_before_opening_resources(self):
        from fastapi.testclient import TestClient
        from game.server import create_app
        with patch.dict(os.environ, {'GAME_DECISION_SECONDS':'nan'}), patch('game.server.LocalMemory') as memory, patch('game.server.PooledTransport') as transport:
            with self.assertRaises(ValueError):
                with TestClient(create_app(':memory:', auto_workers=False)): pass
        memory.assert_not_called()
        transport.assert_not_called()

    def test_database_closes_when_hub_initialization_fails(self):
        db = Mock()
        db.execute.side_effect = RuntimeError('db failed')
        with patch('game.server.sqlite3.connect', return_value=db):
            with self.assertRaisesRegex(RuntimeError, 'db failed'): Hub(':memory:')
        db.close.assert_called_once()

    def test_search_cannot_enqueue_after_close_during_snapshot_copy(self):
        import queue
        from game.mcp_memory import MCPMemory, MemoryUnavailable
        memory = object.__new__(MCPMemory)
        memory.closed=False;memory.jobs=queue.Queue(maxsize=16)
        memory.state_lock=threading.Lock();memory.thread=Mock()
        copying, finish = threading.Event(), threading.Event()
        def copy_view(view):
            copying.set();finish.wait(2);return view
        failures=[]
        def search():
            try: memory.search({'self':{'id':'p0'}})
            except MemoryUnavailable: failures.append(True)
        with patch('game.mcp_memory.scoped', side_effect=copy_view), patch('game.mcp_memory.Future'):
            worker=threading.Thread(target=search)
            worker.start()
            try:
                self.assertTrue(copying.wait(1))
                memory.close()
            finally:
                finish.set();worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures,[True])
        self.assertEqual(list(memory.jobs.queue),[None])


class IntegrationReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_rooms_overlap_and_preserve_separate_usage_on_failure(self):
        clients=[]
        class Client:
            def __init__(self): self.calls=0;self.metrics=[];self.number=len(clients)+1;clients.append(self)
            def check(self):pass
        with patch.dict(os.environ, {'GAME_AI_CONCURRENCY':'2'}):
            hub=Hub(':memory:',client_factory=Client)
        self.addCleanup(hub.db.close)
        rooms=[]
        for _ in range(2):
            room,_=hub.create('integration',5)
            room.game=Game(room.data['id'],{f'p{i}':'ai' for i in range(5)},seed=1)
            room.data['status']='running';hub.queue(room);rooms.append(room)
        barrier=threading.Barrier(2)
        def decision(view,client,*args,**kwargs):
            barrier.wait(timeout=2)  # Fails if rooms accidentally run sequentially.
            client.calls+=1
            client.metrics.append({'total_tokens':client.number})
            raise ProviderError('controlled integration failure')
        with patch('game.server.decide',side_effect=decision):
            await asyncio.wait_for(asyncio.gather(*(hub.run(r) for r in rooms)),5)
        self.assertEqual([r.data['calls'] for r in rooms],[1,1])
        self.assertEqual([r.data['metrics'][0]['total_tokens'] for r in rooms],[1,2])
        self.assertTrue(all(r.data['status']=='paused' for r in rooms))
        self.assertTrue(all(r.data['error']=='controlled integration failure' for r in rooms))
        self.assertEqual(hub.inference.active,0)


class ConnectionReuseTests(unittest.TestCase):
    def test_real_local_http_keepalive_reuses_connection(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import httpx
        from game.providers import PooledTransport
        peers=[]
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                peers.append(self.client_address)
                body=b'{"ok": true}'
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
                self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        transport=PooledTransport(httpx.Client(trust_env=False))
        try:
            url=f'http://127.0.0.1:{server.server_port}/'
            for _ in range(2):self.assertEqual(transport(url,{}, {},2),{'ok':True})
            self.assertEqual(len(peers),2)
            self.assertEqual(peers[0],peers[1])
        finally:
            transport.close();server.shutdown();server.server_close();worker.join(2)


class BudgetRestoreReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_client_check_preserves_restored_call_budget(self):
        class Client:
            calls=0
            metrics=[]
            def check(self): raise ProviderError('missing configuration')
        hub=Hub(':memory:',client_factory=Client)
        self.addCleanup(hub.db.close)
        room,_=hub.create('restore',5)
        room.data.update(status='running', calls=42)
        hub.save(room)
        await hub.run(room)
        self.assertEqual(room.data['status'],'paused')
        self.assertEqual(room.data['calls'],42)
        self.assertEqual(json.loads(hub.db.execute('SELECT data FROM rooms').fetchone()[0])['calls'],42)


class StartupCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_startup_closes_memory_after_constructor_finishes(self):
        from game.server import create_app
        started,finish=threading.Event(),threading.Event()
        memory=Mock()
        def factory():
            started.set();finish.wait(3);return memory
        app=create_app(':memory:')
        context=app.router.lifespan_context(app)
        with patch('game.server.MCPMemory', side_effect=factory), patch('game.server.PooledTransport') as transport:
            task=asyncio.create_task(context.__aenter__())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,1))
                task.cancel();await asyncio.sleep(.01)
                task.cancel();await asyncio.sleep(.01)
                self.assertFalse(task.done())
            finally:
                finish.set()
                with self.assertRaises(asyncio.CancelledError):await task
            memory.close.assert_called_once()
            transport.assert_not_called()
        self.assertFalse(app.state.ready)
