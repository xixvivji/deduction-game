import concurrent.futures
import json
import threading
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from game.server import create_app, DemoClient
from game.engine import Game
from game.decision import decide
from game.observability import Monitor
from game.mlflow_tracking import RuntimeExporter, local_uri


def view():
    g=Game('PRIVATE_ROOM',{f'p{i}':'ai' for i in range(5)},seed=1)
    g.phase='discussion';g.players['p0'].role='doctor'
    g._emit('발언',{'actor':'p0','text':'PRIVATE_SENTENCE'})
    return g.observation('p0')


class OperationsTests(unittest.TestCase):
    def test_real_graph_trace_contains_only_bounded_safe_fields(self):
        m=Monitor(history_limit=2)
        for _ in range(3):
            c=DemoClient();m.run('demo',c,decide,view(),c,policy='verified',discussion_quality=False)
        data=m.snapshot()
        self.assertEqual(data['nodes'],{'memory':3,'generate':3,'validate_evidence':3})
        self.assertEqual(data['totals']['decisions'],3);self.assertEqual(len(data['recent']),2)
        serialized=json.dumps(data)
        for private in ('PRIVATE_ROOM','PRIVATE_SENTENCE','doctor','p0','payload','evidence_ids'):
            self.assertNotIn(private,serialized)
        self.assertEqual([s['node'] for s in data['recent'][0]['stages']],['memory','generate','validate_evidence'])

    def test_rejected_fact_repair_and_fallback_are_observed(self):
        class Bad:
            metrics=[]
            def generate(self,*args):return {'text':'p2가 p1을 죽였어.','decision_summary':'PRIVATE','evidence_ids':[],'claim_type':'none','claim_subject':None,'claim_value':''}
        m=Monitor();c=Bad();result=m.run('openai',c,decide,view(),c,policy='verified',discussion_quality=False)
        s=m.snapshot();self.assertTrue(result['fallback'])
        self.assertEqual(s['totals']['retries'],1)
        self.assertIn('repair',s['nodes']);self.assertIn('fallback',s['nodes'])
        self.assertIn('rejected',[x['status'] for x in s['recent'][0]['stages']])
        self.assertNotIn('PRIVATE',json.dumps(s))

    def test_concurrent_contexts_do_not_mix(self):
        m=Monitor()
        def run():
            c=DemoClient();return m.run('demo',c,decide,view(),c,policy='verified',discussion_quality=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:run(),range(6)))
        s=m.snapshot();self.assertEqual(len(s['recent']),6);self.assertFalse(s['active'])
        self.assertTrue(all(len(t['stages'])==3 for t in s['recent']))

    def test_failure_is_measured_without_error_message(self):
        m=Monitor();c=DemoClient()
        def fail():raise RuntimeError('sk-private-and-secret')
        with self.assertRaises(RuntimeError):m.run('demo',c,fail)
        s=m.snapshot();self.assertEqual(s['totals']['failed'],1);self.assertFalse(s['active'])
        self.assertNotIn('sk-private',json.dumps(s))

    def test_usage_not_double_counted_and_unknown_usage_not_zero_claim(self):
        c=DemoClient();c.metrics=[{'input_tokens':999}];m=Monitor()
        def call():
            c.metrics.extend([{'input_tokens':7,'output_tokens':3,'success':True},{'success':False}])
            return {'attempts':2,'audit':{}}
        m.run('openai',c,call,queue_wait=.25)
        s=m.snapshot();self.assertEqual(s['totals']['input_tokens'],7);self.assertEqual(s['totals']['usage_known_calls'],1)
        self.assertEqual(s['totals']['api_calls'],2);self.assertEqual(s['recent'][0]['queue_wait_seconds'],.25)

    def test_local_dashboard_rejects_remote_origin_and_forwarding(self):
        app=create_app(':memory:',DemoClient,auto_workers=False)
        with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1234)) as c:
            self.assertEqual(c.get('/ops').status_code,200)
            r=c.get('/api/ops');self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['evaluation']['after']['correct'],18)
            for h in ({'origin':'https://evil.example'},{'x-forwarded-for':'127.0.0.1'},{'host':'evil.example'},{'sec-fetch-site':'cross-site'}):
                self.assertEqual(c.get('/api/ops',headers=h).status_code,403)
        with TestClient(app,base_url='http://127.0.0.1',client=('203.0.113.10',1234)) as c:
            self.assertEqual(c.get('/api/ops').status_code,403)

    def test_mlflow_remote_or_credential_uri_disallowed(self):
        for uri in ('https://remote.example','http://user:secret@localhost:5500','http://127.0.0.1:5500/path','file:///tmp/mlruns'):
            with self.assertRaises(ValueError):local_uri(uri)

    def test_export_queue_bounded_and_failure_does_not_break_decision(self):
        entered=threading.Event();release=threading.Event()
        class Blocked:
            def __init__(self,*args):pass
            def health(self):entered.set();release.wait(3);raise OSError('private')
            def close(self):pass
        e=RuntimeExporter('http://127.0.0.1:5500',max_pending=2,client_factory=Blocked)
        self.assertTrue(entered.wait(2))
        try:
            for _ in range(8):e.submit({'seconds':1,'prompt':'PRIVATE'})
            self.assertEqual(e.snapshot()['pending'],2);self.assertEqual(e.snapshot()['dropped'],6)
            m=Monitor(e);c=DemoClient();self.assertFalse(m.run('demo',c,decide,view(),c,policy='verified',discussion_quality=False)['fallback'])
        finally:release.set();e.close()
        self.assertFalse(e.worker.is_alive());self.assertNotIn('private',json.dumps(e.snapshot()))
