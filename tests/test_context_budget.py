import copy
import json
import os
import unittest
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from game.context_budget import ContextBudget, ContextBudgetExceeded, encoded_size
from game.prompt_context import compact
from game.runtime_settings import RuntimeSettings
from game.memory_backend import LocalMemory
from game.decision import decide
from game.server import create_app
from game.suspicion import SuspicionPolicy
from test_reasoning_guards import view, event, claim


def long_view():
    v=view()
    for i in range(2,70):
        v['events'].append({'sequence':i,'type':'발언','recipient':None,
                            'data':{'actor':'p1','text':('과거 발언 기록 %s ' % i)*15,'day':1}})
    v['events'].append({**event('조사',target='p2',team='village',day=1),'sequence':70})
    return v


class ContextBudgetTests(unittest.TestCase):
    def test_size_bound_keeps_exact_evidence_and_does_not_mutate_input(self):
        v=long_view();original=copy.deepcopy(v)
        selected,stats=ContextBudget(4500).apply(v)
        self.assertEqual(v,original)
        self.assertLessEqual(encoded_size(compact(selected)),4500)
        self.assertEqual(stats['after_bytes'],encoded_size(compact(selected)))
        self.assertGreater(stats['dropped_events'],0)
        self.assertEqual(selected['events'][-1],v['events'][-1])
        for e in selected['events']:self.assertIn(e,v['events'])

    def test_low_rank_removed_before_high_rank(self):
        v=long_view();v['memory']={'ranked_event_ids':[2,3]+list(range(69,3,-1))}
        selected,_=ContextBudget(3000).apply(v)
        ids={e['sequence'] for e in selected['events']}
        self.assertIn(2,ids);self.assertIn(3,ids);self.assertNotIn(4,ids)
        self.assertNotIn('memory',compact(selected))

    def test_question_and_belief_references_are_pinned(self):
        v=long_view()
        v['question']={'sequence':2,'evidence_ids':[3],'text':'이 기록을 확인해 줘'}
        v['belief_memory']={'entries':{'p1':{'evidence_ids':[4]}},'history':[]}
        selected,_=ContextBudget(4500).apply(v)
        ids={e['sequence'] for e in selected['events']}
        self.assertTrue({2,3,4,70}<=ids)

    def test_required_overflow_aborts_before_generating(self):
        v=view();v['events'].append(event('조사',target='p1',team='wolf',day=1))
        client=Mock()
        with self.assertRaises(ContextBudgetExceeded):
            decide(v,client,context_budget=ContextBudget(1),policy='verified')
        client.generate.assert_not_called()

    def test_missing_reference_is_not_silently_trimmed(self):
        v=view();v['question']={'sequence':999,'evidence_ids':[]}
        with self.assertRaises(ContextBudgetExceeded):ContextBudget().apply(v)

    def test_budget_applies_after_research_and_preserves_all_conflicting_facts(self):
        v=long_view()
        extra=event('조사',target='p2',team='wolf',day=2);extra['sequence']=71
        v['events'].append(extra)
        class Model:
            metrics=[]
            def __init__(self):self.views=[]
            def generate(self,v,schema,feedback=None):
                self.views.append(copy.deepcopy(v))
                return {'text':'검증 대상','decision_summary':'상충 근거 테스트','evidence_ids':[70],
                        **claim('confirmed_wolf','p1')}
        model=Model();r=decide(v,model,policy='verified',belief_memory=True,context_budget=ContextBudget(6000))
        self.assertTrue(r['fallback']);self.assertEqual(len(model.views),3)
        for selected in model.views:
            self.assertLessEqual(encoded_size(compact(selected)),6000)
            self.assertTrue({70,71}<={e['sequence'] for e in selected['events']})
        self.assertEqual(len(r['audit']['runtime']['context_budget_history']),3)

    def test_server_config_is_validated_at_startup_and_is_immutable(self):
        from dataclasses import FrozenInstanceError
        with patch.dict(os.environ,{'GAME_CONTEXT_BYTES':'4096','GAME_SUSPICION_DAYS':'1'}):
            settings=RuntimeSettings.from_env()
        self.assertEqual(settings.context_budget.max_bytes,4096)
        self.assertEqual(settings.suspicion_policy.max_age_days,1)
        with self.assertRaises(FrozenInstanceError):settings.context_budget.max_bytes=1024
        for key,val in [('GAME_CONTEXT_BYTES','0'),('GAME_CONTEXT_BYTES','abc'),('GAME_SUSPICION_DAYS','-1')]:
            with self.subTest(key=key,val=val),patch.dict(os.environ,{key:val}),patch('game.server.LocalMemory') as memory:
                with self.assertRaises(ValueError):
                    with TestClient(create_app(':memory:',auto_workers=False)):pass
                memory.assert_not_called()

    def test_injected_suspicion_policy_is_shared_by_verifier_and_ledger(self):
        from game.evidence import verify
        from game import beliefs
        v=view(day=2);v['events'].append(event('목격',player='p1',location='창고',day=1))
        c=claim('suspect_wolf','p1');policy=SuspicionPolicy(0)
        self.assertFalse(verify(v,c,[2],{},suspicion_policy=policy)['ok'])
        ledger=beliefs.record_suspicion(v,beliefs.update(v,suspicion_policy=policy),
                {'semantic_support':'grounded_suspicion','claim':c,'evidence_ids':[2]},False,suspicion_policy=policy)
        self.assertEqual(ledger['entries']['p1']['status'],'unknown')
        self.assertEqual(ledger['policy']['suspicion_max_age_days'],0)

class ContextBudgetServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_overflow_pauses_room_without_call_or_action(self):
        import asyncio
        from game.engine import Game
        from game.server import Hub
        class Client:
            calls=0
            def __init__(self):self.metrics=[]
            def check(self):pass
            def generate(self,*args):
                self.calls+=1
                raise AssertionError('필수 문맥 초과 전에 생성하면 안 됩니다')
        client=Client()
        hub=Hub(':memory:',client_factory=lambda:client,memory=LocalMemory(),
                settings=RuntimeSettings(1,10,ContextBudget(1)))
        self.addCleanup(hub.db.close)
        room,_=hub.create('합성 검증',5)
        room.game=Game(room.data['id'],{f'p{i}':'ai' for i in range(5)},seed=1)
        room.game.phase='vote';room.data['status']='running';hub.queue(room)
        version=room.game.version
        await asyncio.wait_for(hub.run(room),3)
        self.assertEqual(room.data['status'],'paused')
        self.assertEqual(client.calls,0)
        self.assertEqual(room.game.version,version)
        self.assertFalse(room.data['history']);self.assertFalse(room.busy)
        self.assertIn('문맥 예산',room.data['error'])
        saved=json.loads(hub.db.execute('SELECT data FROM rooms').fetchone()[0])
        self.assertEqual(saved['status'],'paused')
