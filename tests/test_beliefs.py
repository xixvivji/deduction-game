import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from game.engine import Game
from game import beliefs
from game.decision import decide
from game.server import create_app, DemoClient, Hub
from game.providers import ProviderError
import asyncio


def game():
    g=Game('belief-test',{f'p{i}':'ai' for i in range(5)},seed=1)
    g.players['p0'].role='seer';g.phase='discussion';g.day=2
    g._emit('목격',{'player':'p2','location':'창고','day':1},'p0')
    return g


def suspicion(g):
    v=g.observation('p0');ledger=beliefs.update(v)
    return beliefs.record_suspicion(v,ledger,{'semantic_support':'grounded_suspicion','claim':{'claim_subject':'p2'},'evidence_ids':[2]},False)


class Model:
    metrics=[]
    def __init__(self,kind='confirmed_village'):self.kind=kind;self.views=[]
    def generate(self,v,schema,feedback=None):
        self.views.append(copy.deepcopy(v))
        return {'text':'기록을 확인했습니다.','decision_summary':'새 조사 반영','evidence_ids':[3],
                'claim_type':self.kind,'claim_subject':'p2','claim_value':''}


class BeliefTests(unittest.TestCase):
    def test_suspicion_retracted_by_private_fact(self):
        g=game();old=suspicion(g);self.assertEqual(old['entries']['p2']['status'],'suspected')
        g._emit('조사',{'target':'p2','team':'village','day':2},'p0')
        ledger=beliefs.update(g.observation('p0'),old)
        self.assertEqual(ledger['entries']['p2']['status'],'confirmed_village')
        self.assertEqual(ledger['changes'][0]['from'],'suspected')
        self.assertEqual(ledger['entries']['p2']['evidence_ids'],[3])

    def test_other_participant_and_room_isolation(self):
        g=game();old=suspicion(g)
        self.assertEqual(beliefs.update(g.observation('p3'),old)['entries']['p2']['status'],'unknown')
        v=g.observation('p0');v['room_id']='different'
        self.assertEqual(beliefs.update(v,old)['entries']['p2']['status'],'unknown')

    def test_hidden_fact_is_not_used_even_if_injected(self):
        g=game();g._emit('조사',{'target':'p2','team':'wolf','day':2},'p3')
        v=g.observation('p0');v['events']=copy.deepcopy(g.events)
        self.assertEqual(beliefs.update(v)['entries']['p2']['status'],'unknown')

    def test_sighting_and_public_claim_are_not_confirmation(self):
        g=game();g._emit('발언',{'actor':'p3','text':'p2는 늑대입니다.','day':2})
        self.assertEqual(beliefs.update(g.observation('p0'))['entries']['p2']['status'],'unknown')

    def test_conflicting_facts_do_not_overwrite_each_other(self):
        g=game()
        for team in ['wolf','village']:g._emit('조사',{'target':'p2','team':team,'day':2},'p0')
        v=g.observation('p0');ledger=beliefs.update(v)
        self.assertEqual(ledger['entries']['p2']['status'],'conflicted')
        self.assertFalse(beliefs.check(v,ledger,{'claim_type':'confirmed_wolf','claim_subject':'p2'},{})['ok'])

    def test_wolf_deception_not_recorded_as_private_suspicion(self):
        g=game();g.players['p0'].role='wolf'
        self.assertEqual(suspicion(g)['entries']['p2']['status'],'unknown')

    def test_no_repeat_transition_and_dead_target_retained(self):
        g=game();old=suspicion(g);g.players['p2'].alive=False
        new=beliefs.update(g.observation('p0'),old)
        self.assertFalse(new['entries']['p2']['alive']);self.assertEqual(new['changes'],[])
        self.assertEqual(len(new['history']),1)

    def test_evidence_pinned_after_retrieval(self):
        g=game();v=g.observation('p0');ledger=suspicion(g)
        selected=copy.deepcopy(v);selected['events']=[]
        beliefs.attach(selected,ledger,v)
        self.assertEqual([e['sequence'] for e in selected['events']],[2])

    def test_graph_retraction_and_previous_context(self):
        g=game();old=suspicion(g);g._emit('조사',{'target':'p2','team':'village','day':2},'p0')
        model=Model();r=decide(g.observation('p0'),model,policy='verified',previous_beliefs=old,belief_memory=True)
        self.assertIn('철회',r['payload']['text']);self.assertFalse(r['fallback'])
        self.assertEqual(model.views[0]['belief_memory']['entries']['p2']['status'],'confirmed_village')

    def test_contradictory_claim_research_is_bounded(self):
        g=game();g._emit('조사',{'target':'p2','team':'village','day':2},'p0')
        class Suspect(Model):
            def generate(self,v,schema,feedback=None):
                p=super().generate(v,schema,feedback);p['claim_type']='suspect_wolf';p['evidence_ids']=[2];return p
        with patch('game.decision.time.sleep'):
            r=decide(g.observation('p0'),Suspect(),policy='verified',belief_memory=True)
        self.assertTrue(r['fallback']);self.assertEqual(r['attempts'],2)
        self.assertEqual(r['audit']['research_rounds'],0)
        self.assertEqual(r['audit']['fact_retries'],1)
        self.assertEqual(r['audit']['belief_memory']['entries']['p2']['status'],'confirmed_village')

    def test_vote_against_confirmed_villager_is_rejected(self):
        g=game();g._emit('조사',{'target':'p2','team':'village','day':2},'p0');g.phase='vote'
        v=g.observation('p0');ledger=beliefs.update(v)
        self.assertFalse(beliefs.check(v,ledger,{}, {'target':'p2'})['ok'])

    def test_real_worker_path_persists_generated_ledger(self):
        class Once(Model):
            def check(self):pass
            def generate(self,v,schema,feedback=None):
                if self.views:raise ProviderError('test stop after first decision')
                return super().generate(v,schema,feedback)
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                hub=Hub(tmp+'/rooms.db',Once)
                room,_=hub.create('test',5);room.game=game()
                room.game._emit('조사',{'target':'p2','team':'village','day':2},'p0')
                room.data['status']='running';hub.queue(room)
                await hub.run(room)
                self.assertEqual(room.data['beliefs']['p0']['entries']['p2']['status'],'confirmed_village')
                self.assertIn('belief_memory',room.data['history'][0]['audit'])
                hub.db.close()
                restored=Hub(tmp+'/rooms.db',Once)
                self.assertEqual(restored.room(room.data['id']).data['beliefs'],room.data['beliefs'])
                restored.db.close()
        asyncio.run(run())

    def test_storage_restart_and_api_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/rooms.db'
            with TestClient(create_app(path,DemoClient,auto_workers=False)) as c:
                access=c.post('/api/rooms',json={'name':'test','size':5}).json()
                headers={'Authorization':'Bearer '+access['token']};url='/api/rooms/'+access['room_id']
                c.post(url+'/start',headers=headers)
                room=c.app.state.hub.room(access['room_id']);ledger=beliefs.update(room.game.observation('p1'))
                room.data['beliefs']={'p1':ledger};room.data['history']=[{'audit':{'belief_memory':ledger}}]
                c.portal.call(c.app.state.hub.save,room)
                self.assertNotIn('belief_memory',json.dumps(c.get(url,headers=headers).json()))
            with TestClient(create_app(path,DemoClient,auto_workers=False)) as c:
                room=c.app.state.hub.room(access['room_id'])
                self.assertEqual(room.data['beliefs']['p1'],ledger)
                room.game._finish('village')
                self.assertIn('belief_memory',json.dumps(c.get(url,headers=headers).json()))
