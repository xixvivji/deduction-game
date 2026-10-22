import json
import unittest
from collections import Counter
from fastapi.testclient import TestClient
from game.engine import Game, RuleError
from game.ai import action_spec, prompt_messages
from game.memory import retrieve
from game.server import create_app, DemoClient


class ExpandedRolesTests(unittest.TestCase):
    def make(self, n=9):
        return Game('expanded', {f'p{i}':'human' if i==0 else 'ai' for i in range(n)},seed=42)

    def role(self,g,role):
        return next(p.id for p in g.players.values() if p.role==role)

    def test_compositions_and_legacy_sizes(self):
        for n,w in [(5,1),(7,2),(9,2),(11,3)]:
            g=self.make(n);c=Counter(p.role for p in g.players.values())
            self.assertEqual(len(g.players),n);self.assertEqual(c['wolf'],w)
            self.assertEqual(c['tracker'],int(n>=9));self.assertEqual(c['coroner'],int(n>=9))
            self.assertEqual(c['doctor'],int(n>=7))
        for n in [6,8,10,12]:
            with self.assertRaises(RuleError):self.make(n)

    def test_tracker_private_location_and_snapshot(self):
        g=self.make();tracker=self.role(g,'tracker');target=self.role(g,'citizen')
        g.submit(target,'walk',g.version,'night',location='창고',target=None)
        g.submit(tracker,'track',g.version,'night',location='숲길',target=target)
        g=Game.restore(json.loads(json.dumps(g.snapshot())));g.advance(timeout=True)
        own=[e for e in g.observation(tracker)['events'] if e['type']=='추적']
        self.assertEqual(own[0]['data'],{'target':target,'location':'창고','day':1})
        self.assertFalse(any(e['type']=='추적' for e in g.observation(target)['events']))
        self.assertNotIn('role',own[0]['data'])

    def test_coroner_dead_only_and_private_exact_role(self):
        g=self.make();coroner=self.role(g,'coroner');target=self.role(g,'wolf')
        with self.assertRaises(RuleError):
            g.submit(coroner,'live',g.version,'night',location='광장',target=target)
        g.players[target].alive=False
        schema=action_spec(g.observation(coroner))[1]
        self.assertEqual(schema['properties']['target']['enum'],[None,target])
        g.submit(coroner,'dead',g.version,'night',location='광장',target=target)
        g.advance(timeout=True)
        own=[e for e in g.observation(coroner)['events'] if e['type']=='검시']
        self.assertEqual(own[0]['data']['role'],'wolf')
        observer=self.role(g,'citizen')
        self.assertFalse(any(e['type']=='검시' for e in g.observation(observer)['events']))
        for i in range(30):g._emit('발언',{'text':f'잡담{i}'})
        self.assertTrue(any(e['type']=='검시' for e in retrieve(g.observation(coroner))['events']))

    def test_new_roles_target_validation_and_no_dead_actor(self):
        g=self.make();tracker=self.role(g,'tracker');coroner=self.role(g,'coroner');dead=self.role(g,'citizen')
        self.assertEqual(action_spec(g.observation(coroner))[1]['properties']['target']['enum'],[None])
        g.players[dead].alive=False
        for target in [tracker,dead,'missing']:
            with self.assertRaises(RuleError):g.submit(tracker,'bad'+target,g.version,'night',location='광장',target=target)
        self.assertNotIn(dead,action_spec(g.observation(tracker))[1]['properties']['target']['enum'])
        g.players[tracker].alive=False
        with self.assertRaises(RuleError):g.submit(tracker,'deadactor',g.version,'night',location='광장',target=None)

    def test_killed_tracker_does_not_receive_private_result(self):
        g=self.make();g.day=2;tracker=self.role(g,'tracker');target=self.role(g,'citizen')
        g.submit(tracker,'track',g.version,'night',location='광장',target=target)
        for p in list(g.players.values()):
            if p.role=='wolf':g.submit(p.id,p.id,g.version,'night',location='창고',target=tracker)
        g.advance(timeout=True)
        self.assertFalse(g.players[tracker].alive)
        self.assertFalse(any(e['type']=='추적' for e in g.observation(tracker)['events']))

    def test_api_eleven_humans_capacity_and_all_roles(self):
        with TestClient(create_app(':memory:',DemoClient,auto_workers=False)) as c:
            session=c.post('/api/rooms',json={'name':'host','size':11}).json();url='/api/rooms/'+session['room_id']
            for i in range(10):self.assertEqual(c.post(url+'/join',json={'name':str(i)}).status_code,200)
            self.assertEqual(c.post(url+'/join',json={'name':'overflow'}).status_code,409)
            r=c.post(url+'/start',headers={'Authorization':'Bearer '+session['token']})
            self.assertEqual(r.status_code,200)
            self.assertEqual(len(r.json()['players']),11)
            self.assertNotIn('roles',r.json())
            self.assertEqual(c.post('/api/rooms',json={'name':'bad','size':10}).status_code,409)

    def test_large_game_reaches_end_with_schema_valid_actions(self):
        for n in (9,11):
            g=self.make(n);g.max_days=2
            while g.phase!='finished':
                actors=g.living()
                if g.phase=='council':actors=[p for p in actors if g.players[p].role=='wolf']
                for actor in actors:
                    kind,schema=action_spec(g.observation(actor))
                    if kind=='speak':payload={'text':'검증 발언'}
                    else:
                        targets=schema['properties']['target']['enum']
                        payload={'target':next((t for t in targets if t is not None),None)}
                        if kind=='night':payload['location']='창고'
                        if kind=='council':payload['text']='목표를 제안합니다.'
                    g.submit(actor,f'{g.day}:{g.phase}:{actor}',g.version,kind,**payload)
                g.advance()
            self.assertIn(g.winner,('draw','wolf','village'))
