import copy
import os
import unittest
from unittest.mock import patch, Mock
from concurrent.futures import ThreadPoolExecutor
from game.engine import Game
from game.decision import decide
from game.memory_backend import LocalMemory,signature
from game.mcp_memory import MCPMemory
from game.evidence import verify


def fixture():
    g=Game('evidence',{f'p{i}':'ai' for i in range(7)},seed=42)
    for i,p in enumerate(g.players.values()):p.role='wolf' if i==1 else 'citizen'
    g.phase='vote'
    g._emit('조사',{'target':'p1','team':'wolf','day':1},'p0')
    return g


class Scripted:
    def __init__(self,claim='confirmed_wolf',subject='p1',ids=None):self.calls=0;self.claim=claim;self.subject=subject;self.ids=ids or [2];self.metrics=[]
    def generate(self,view,schema,feedback=None):
        self.calls+=1
        return {'target':self.subject,'decision_summary':'조사 결과를 참고합니다.','evidence_ids':self.ids,
                'claim_type':self.claim,'claim_subject':self.subject,'claim_value':''}


class EvidenceTests(unittest.TestCase):
    def test_opinion_label_is_not_duplicated(self):
        from game.decision import validate_evidence
        view=fixture().observation('p0');view['phase']='discussion'
        state={'policy':'verified','selected':view,
               'claim':{'claim_type':'none','claim_subject':None,'claim_value':''},
               'audit':{'evidence_ids':[]},'payload':{'text':'의견: 의견: 아직 판단하기 어렵습니다.'},
               'verification_history':[]}
        result=validate_evidence(state)
        self.assertEqual(result['payload']['text'],'의견: 아직 판단하기 어렵습니다.')

    def test_fact_supported_and_local_di(self):
        result=decide(fixture().observation('p0'),Scripted(),memory=LocalMemory(),policy='verified')
        self.assertEqual(result['audit']['semantic_support'],'supported_fact')
        self.assertEqual(result['attempts'],1);self.assertEqual(result['audit']['research_rounds'],0)

    def test_location_is_not_proof_of_wolf_bounded_research_and_abstain(self):
        g=fixture();g.events[1]={'sequence':2,'type':'탐색 목격','recipient':'p0','data':{'player':'p1','location':'광장'}}
        model=Scripted()
        with patch('game.decision.time.sleep'):
            result=decide(g.observation('p0'),model,policy='verified')
        self.assertEqual(model.calls,3);self.assertEqual(result['audit']['research_rounds'],2)
        self.assertTrue(result['fallback']);self.assertIsNone(result['payload']['target'])

    def test_research_repairs_evidence(self):
        g=fixture()
        class FirstMiss(LocalMemory):
            def search(self,view,query=None,expanded=False):
                result=super().search(view,query,expanded)
                if not expanded:result['events']=[result['events'][0]]
                return result
        class Retry:
            metrics=[]
            def generate(self,view,schema,feedback=None):
                present=any(e['type']=='조사' for e in view['events'])
                return {'target':'p1','decision_summary':'기록 확인','evidence_ids':[2] if present else [1],
                        'claim_type':'confirmed_wolf','claim_subject':'p1','claim_value':''}
        with patch('game.decision.time.sleep'):
            result=decide(g.observation('p0'),Retry(),memory=FirstMiss(),policy='verified')
        self.assertFalse(result['fallback']);self.assertEqual(result['audit']['research_rounds'],1)

    def test_unseen_citation_rejected_and_wolf_deception_distinguished(self):
        g=fixture();view=g.observation('p1')
        with patch('game.decision.time.sleep'):
            invalid=decide(view,Scripted(subject='p2',ids=[2]),policy='verified')
        self.assertTrue(invalid['fallback'])
        valid=decide(view,Scripted(subject='p2',ids=[1]),policy='verified')
        self.assertEqual(valid['audit']['semantic_support'],'game_deception_allowed')

    def test_checked_speech_uses_canonical_fact_not_free_text(self):
        g=fixture();g.phase='discussion'
        class Writer:
            metrics=[]
            def generate(self,*args):return {'text':'p6가 범인이라는 임의 주장','decision_summary':'조사 확인','evidence_ids':[2], 'claim_type':'confirmed_wolf','claim_subject':'p1','claim_value':''}
        result=decide(g.observation('p0'),Writer(),policy='verified')
        self.assertEqual(result['payload']['text'],'p1는 확인된 기록상 늑대 진영입니다.')
        self.assertNotIn('p6',result['payload']['text'])

    def test_langsmith_spans_with_local_mock_client(self):
        from langsmith import Client,tracing_context
        client=Client(api_key='local-test-only',auto_batch_tracing=False)
        with patch.object(Client,'create_run') as create,patch.object(Client,'update_run'):
            with tracing_context(enabled=True,client=client):
                decide(fixture().observation('p0'),Scripted(),policy='verified')
            names={call.kwargs.get('name') for call in create.call_args_list}
        self.assertTrue({'participant_decision','model_decision','evidence_verifier'}<=names,names)


class MCPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.memory=MCPMemory()
    @classmethod
    def tearDownClass(cls):cls.memory.close()

    def test_actual_stdio_search_isolated_across_participants(self):
        g=fixture();views=[g.observation('p0'),g.observation('p2')]
        # Deliberately include another player's event: service must still filter it.
        views[1]['events']=copy.deepcopy(g.events)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.memory.search,views))
        self.assertTrue(any(e['type']=='조사' for e in results[0]['events']))
        self.assertFalse(any(e['type']=='조사' for e in results[1]['events']))
        self.assertIn('evidence_rules',results[0])
        result=decide(views[0],Scripted(),memory=self.memory,policy='verified')
        self.assertEqual(result['audit']['memory_backend'],'mcp')

    def test_unsigned_or_released_context_cannot_search(self):
        from game.mcp_memory_server import register_context,search_memory,release_context
        view=fixture().observation('p0')
        with patch.dict(os.environ,{'GAME_MEMORY_SECRET':'local-fixture-secret'}):
            with self.assertRaises(ValueError):register_context(view,'forged')
            cap=register_context(view,signature(view,'local-fixture-secret'))['capability']
            self.assertEqual(search_memory(cap)['self']['id'],'p0')
            with self.assertRaises(TypeError):search_memory(cap,actor='p2')
            release_context(cap)
            with self.assertRaises(ValueError):search_memory(cap)

class WebEvidenceIntegration(unittest.TestCase):
    def test_web_worker_uses_mcp_and_closes_connection(self):
        import time
        from fastapi.testclient import TestClient
        from game.server import create_app, DemoClient
        app=create_app(':memory:',DemoClient,turn_seconds=.03)
        with TestClient(app) as c:
            a=c.post('/api/rooms',json={'name':'검증','size':5}).json()
            url='/api/rooms/'+a['room_id'];h={'Authorization':'Bearer '+a['token']}
            c.post(url+'/start',headers=h)
            room=app.state.hub.room(a['room_id'])
            for _ in range(100):
                if room.data['history']:break
                time.sleep(.03)
            self.assertTrue(room.data['history'],room.data.get('error'))
            audit=room.data['history'][0]['audit']
            self.assertEqual(audit['memory_backend'],'mcp')
            self.assertEqual(audit['policy'],'verified')
            memory=app.state.hub.memory
        self.assertTrue(memory.closed)
        self.assertFalse(memory.thread.is_alive())
