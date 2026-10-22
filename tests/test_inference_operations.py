import asyncio
import json
import threading
import unittest
from unittest.mock import Mock
import httpx
from game.providers import PooledTransport, ProviderError, TransientProviderError, CloudClient, BudgetClient
from game.inference_pool import InferencePool, run_blocking
from game.engine import Game
from game.decision import decide


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_limit_and_overload(self):
        pool=InferencePool(2,1,.05)
        async with pool.slot():
            async with pool.slot():
                self.assertEqual(pool.active,2)
                async def wait():
                    async with pool.slot():pass
                task=asyncio.create_task(wait())
                await asyncio.sleep(.01)
                with self.assertRaisesRegex(ProviderError,'가득'):
                    async with pool.slot():pass
                with self.assertRaisesRegex(ProviderError,'초과'):await task
        self.assertEqual((pool.active,pool.waiters),(0,0))
        async with pool.slot():self.assertEqual(pool.active,1)

    async def test_cancel_drains_thread_before_releasing_slot(self):
        pool=InferencePool(1);started=threading.Event();finish=threading.Event()
        def work():started.set();finish.wait(2)
        async def job():
            async with pool.slot():await run_blocking(work)
        task=asyncio.create_task(job())
        await asyncio.to_thread(started.wait,1)
        task.cancel();await asyncio.sleep(.01)
        self.assertEqual(pool.active,1)
        finish.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(pool.active,0)


class TransportTests(unittest.TestCase):
    def test_shared_transport_headers_errors_and_close(self):
        seen=[]
        def handler(request):
            seen.append(request.headers['authorization'])
            return httpx.Response(200,json={'ok':True})
        client=httpx.Client(transport=httpx.MockTransport(handler));transport=PooledTransport(client)
        for key in ('room-a','room-b'):
            self.assertEqual(transport('https://example.com',{}, {'Authorization':key},1),{'ok':True})
        self.assertEqual(seen,['room-a','room-b'])
        a=BudgetClient(CloudClient('openai','test',transport=transport),1)
        b=BudgetClient(CloudClient('openai','test',transport=transport),2)
        a.calls=1;self.assertEqual(b.calls,0);self.assertIsNot(a.metrics,b.metrics)
        transport.close();self.assertTrue(client.is_closed)
        for code,error in ((429,TransientProviderError),(401,ProviderError),(302,ProviderError)):
            transport=PooledTransport(httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(code,headers={'Retry-After':'2'},text='secret'))))
            with self.assertRaises(error) as caught:transport('https://example.com',{}, {},1)
            self.assertNotIn('secret',str(caught.exception));transport.close()

    def test_only_fully_fixed_night_skips_model_and_search(self):
        game=Game('fixed',{str(i):'ai' for i in range(7)},seed=1)
        actor=next(p.id for p in game.players.values() if p.role=='citizen')
        view=game.observation(actor);view['realtime']=True
        client=Mock();memory=Mock()
        result=decide(view,client,memory=memory,skip_forced=True)
        self.assertEqual(result['attempts'],0);self.assertFalse(result['fallback'])
        self.assertEqual(result['payload'],{'target':None,'location':'광장'})
        client.generate.assert_not_called();memory.search.assert_not_called()
        # A real location choice must still run the graph.
        view['realtime']=False
        from unittest.mock import patch
        with patch('game.decision.GRAPH.invoke',side_effect=RuntimeError('graph used')):
            with self.assertRaisesRegex(RuntimeError,'graph used'):
                decide(view,client,skip_forced=True)
