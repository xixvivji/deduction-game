import copy
import json
import queue
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from game.runtime_cache import EventTokenCache, TOKEN_CACHE
from game.memory import retrieve, terms
from game.engine import Game
from game import beliefs, discussion
from game.prompt_context import compact
from game.decision import decide
from game.providers import TransientProviderError,ProviderError,CloudClient,retry_after_seconds
from game.server import create_app,DemoClient
from game.mcp_memory import MCPMemory,MemoryUnavailable


def view():
    g=Game('runtime',{f'p{i}':'ai' for i in range(5)},seed=1)
    g.players['p0'].role='seer';g.phase='vote'
    for i in range(25):g._emit('발언',{'actor':'p2','text':f'기록 {i} 창고 목격','day':1})
    return g.observation('p0')


class RuntimeTests(unittest.TestCase):
    def test_cache_scope_edit_and_copy(self):
        c=EventTokenCache()
        a,hit=c.get(('a','p0'),1,'창고 목격',terms);self.assertFalse(hit);a.clear()
        b,hit=c.get(('a','p0'),1,'창고 목격',terms);self.assertTrue(hit);self.assertTrue(b)
        for ns,text in [(('b','p0'),'창고 목격'),(('a','p1'),'창고 목격'),(('a','p0'),'광장')]:
            self.assertFalse(c.get(ns,1,text,terms)[1])

    def test_cache_lru_ttl_and_byte_limit(self):
        now=[0];c=EventTokenCache(max_entries=2,max_bytes=2000,ttl=5,clock=lambda:now[0])
        for i in range(5):c.get(('a','p0'),i,'a',terms)
        self.assertLessEqual(c.stats()['entries'],2);self.assertLessEqual(c.stats()['estimated_bytes'],2000)
        now[0]=6;self.assertFalse(c.get(('a','p0'),4,'a',terms)[1])
        tiny=EventTokenCache(max_bytes=1);tiny.get(('a','p0'),1,'x',terms)
        self.assertEqual(tiny.stats()['entries'],0)

    def test_cache_does_not_change_retrieval(self):
        TOKEN_CACHE.clear();v=view();baseline=retrieve(v,use_cache=False)
        retrieve(v);cached=retrieve(v)
        self.assertEqual(baseline['events'],cached['events'])
        self.assertEqual(cached['memory']['cache_hits'],len(v['events']))

    def test_incremental_matches_full_and_rebuilds_on_edit_delete(self):
        v=view();old=beliefs.update(v)
        v['events'].append({'sequence':100,'type':'조사','recipient':'p0','data':{'target':'p2','team':'wolf'}})
        inc=beliefs.update(v,old);full=beliefs.update(v,old,incremental=False)
        self.assertEqual(inc['entries'],full['entries']);self.assertEqual(inc['history'],full['history'])
        self.assertEqual(inc['update_stats']['fact_events_processed'],1)
        v['events'][-1]['data']['team']='village'
        changed=beliefs.update(v,inc);self.assertFalse(changed['update_stats']['checkpoint_reused'])
        self.assertEqual(changed['entries']['p2']['status'],'confirmed_village')
        v['events'].pop();deleted=beliefs.update(v,changed)
        self.assertFalse(deleted['update_stats']['checkpoint_reused']);self.assertEqual(deleted['entries']['p2']['status'],'unknown')

    def test_checkpoints_are_not_in_prompt_or_audit(self):
        v=view();ledger=beliefs.update(v);selected=copy.deepcopy(v);beliefs.attach(selected,ledger,v)
        self.assertNotIn('_checkpoint',selected['belief_memory'])
        self.assertNotIn('_checkpoint',beliefs.public_ledger(ledger))

    def test_prompt_references_preserve_all_game_evidence(self):
        v=view();discussion.attach_context(v,v);original=copy.deepcopy(v)
        packed=compact(v)
        self.assertEqual(packed['events'],v['events']);self.assertEqual(v,original)
        self.assertTrue(all(i in {e['sequence'] for e in v['events']} for i in packed['discussion_context']['own_recent']))
        self.assertTrue(all(isinstance(i,int) for i in packed['discussion_context']['public_recent']))

    def test_content_repair_has_no_sleep_but_transport_retry_does(self):
        class Client:
            metrics=[]
            def __init__(self,error):self.error=error;self.calls=0
            def generate(self,*args):
                self.calls+=1
                if self.calls==1:raise self.error
                return {'target':None,'decision_summary':'보류','evidence_ids':[],'claim_type':'none','claim_subject':None,'claim_value':''}
        with patch('game.decision.time.sleep') as sleep:
            r=decide(view(),Client(ValueError('format')),policy='verified');sleep.assert_not_called();self.assertEqual(r['attempts'],2)
        with patch('game.decision.time.sleep') as sleep:
            r=decide(view(),Client(TransientProviderError('temporary',.25)),policy='verified')
            sleep.assert_called_once_with(.25);self.assertEqual(r['audit']['retry_wait_seconds'],.25)

    def test_prompt_limit_prevents_network_request(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test','GAME_MAX_PROMPT_BYTES':'10'}):
            with patch('game.providers.post_json') as transport:
                c=CloudClient('openai','test',transport=transport)
                with self.assertRaises(ProviderError):c.generate(view(),{})
                transport.assert_not_called()

    def test_retry_after_clamped_and_invalid(self):
        self.assertEqual(retry_after_seconds('10000'),30)
        self.assertEqual(retry_after_seconds('-1'),0)
        self.assertIsNone(retry_after_seconds('bad'))

    def test_health_checks_are_small_and_fail_closed(self):
        app=create_app(':memory:',DemoClient,auto_workers=False)
        with TestClient(app) as c:
            self.assertEqual(c.get('/health/live').json(),{'status':'alive'})
            self.assertEqual(c.get('/health/ready').json(),{'status':'ready'})
            app.state.ready=False
            self.assertEqual(c.get('/health/ready').status_code,503)

    def test_mcp_queue_backpressure(self):
        memory=object.__new__(MCPMemory);memory.closed=False;memory.jobs=queue.Queue(maxsize=1);memory.jobs.put('occupied')
        with self.assertRaises(MemoryUnavailable):memory.search(view())
