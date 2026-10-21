import json
import unittest
from fastapi.testclient import TestClient
from game.engine import Game, RuleError
from game import exploration as ex
from game.server import Hub, create_app, DemoClient
from game.ai import action_spec
from game.memory import retrieve


class FieldTests(unittest.TestCase):
    def setup_game(self):
        g=Game('field',{f'p{i}':'human' for i in range(9)},seed=42)
        for i,p in enumerate(g.players.values()):p.role='wolf' if i<2 else 'citizen'
        g.realtime=True;g.day=2;g.phase='discussion'
        s=ex.begin(g,60);s['elapsed']=9
        for i,p in enumerate(g.players):s['positions'][p]=[800,300+i*20]
        s['positions']['p0']=[460,300];s['positions']['p2']=[490,300]
        return g,s

    def test_attack_authority_range_cooldown_peace(self):
        g,s=self.setup_game()
        for actor,target in [('p2','p0'),('p0','p1'),('p0','p8')]:
            with self.assertRaises(RuleError):ex.act(s,g,actor,'attack',target)
        g.day=1
        with self.assertRaises(RuleError):ex.act(s,g,'p0','attack','p2')
        g.day=2;s['elapsed']=7
        with self.assertRaises(RuleError):ex.act(s,g,'p0','attack','p2')
        s['elapsed']=9;ex.act(s,g,'p0','attack','p2')
        self.assertFalse(g.players['p2'].alive)
        s['positions']['p3']=[480,300]
        with self.assertRaises(RuleError):ex.act(s,g,'p0','attack','p3')
        with self.assertRaises(RuleError):ex.act(s,g,'p2','report','p2')

    def test_one_use_shield_and_private_witness(self):
        g,s=self.setup_game();s['shields']=['p2'];s['positions']['p3']=[500,300]
        ex.act(s,g,'p0','attack','p2')
        self.assertTrue(g.players['p2'].alive);self.assertEqual(s['shields'],[])
        self.assertFalse(any(e['type']=='보호 발동' for e in g.observation('p3')['events']))
        s['elapsed']=25;ex.act(s,g,'p0','attack','p2')
        self.assertTrue(any(e['type']=='공격 목격' for e in g.observation('p3')['events']))
        self.assertFalse(any(e['type']=='공격 목격' for e in g.observation('p8')['events']))
        for i in range(30):g._emit('발언',{'text':str(i)})
        self.assertTrue(any(e['type']=='공격 목격' for e in retrieve(g.observation('p3'))['events']))

    def test_visible_body_report_and_meeting(self):
        g,s=self.setup_game();ex.act(s,g,'p0','attack','p2')
        self.assertEqual(ex.observation(s,g,'p8')['bodies'],[])
        with self.assertRaises(RuleError):ex.act(s,g,'p8','report','p2')
        s['positions']['p3']=[530,300]
        self.assertTrue(ex.observation(s,g,'p3')['bodies'][0]['reportable'])
        ex.act(s,g,'p3','report','p2');ex.finish(s,g)
        self.assertFalse(s['active']);self.assertEqual(g.phase,'discussion')
        self.assertTrue(any(e['type']=='시체 신고' and e['recipient'] is None for e in g.events))
        count=len(g.events);ex.finish(s,g);self.assertEqual(len(g.events),count)
        self.assertTrue(Game.restore(json.loads(json.dumps(g.snapshot()))).realtime)

    def test_night_preparation_no_duplicate_attack_and_tracker_previous_day(self):
        g,s=self.setup_game();g.phase='night';g.players['p3'].role='doctor';g.players['p4'].role='tracker'
        g.exploration_locations={'p2':'숲길'}
        self.assertEqual(action_spec(g.observation('p0'))[1]['properties']['target']['enum'],[None])
        with self.assertRaises(RuleError):g.submit('p0','bad',g.version,'night',location='광장',target='p2')
        g.submit('p3','protect',g.version,'night',location='광장',target='p2')
        g.submit('p4','track',g.version,'night',location='광장',target='p2')
        g.advance(timeout=True)
        self.assertTrue(g.players['p2'].alive)
        self.assertIn('p2',ex.begin(g)['shields'])
        event=next(e for e in g.events if e['type']=='추적')
        self.assertEqual(event['data']['day'],1);self.assertEqual(event['data']['location'],'숲길')

    def test_ai_reports_only_visible_body_and_honors_council_target(self):
        g,s=self.setup_game();g.players['p0'].controller='ai';g.council['p0']={'target':'p2'}
        ex.field_bots(s,g);self.assertFalse(g.players['p2'].alive)
        g.players['p3'].controller='ai';s['positions']['p3']=[530,300];s['elapsed']=11
        ex.field_bots(s,g);self.assertTrue(s['meeting'])

    def test_victory_ends_field_and_blocks_further_actions(self):
        g,s=self.setup_game();g.players['p6'].alive=False;g.players['p7'].alive=False;g.players['p8'].alive=False
        ex.act(s,g,'p0','attack','p2')
        s['elapsed']=25;s['positions']['p3']=[490,300]
        ex.act(s,g,'p0','attack','p3')
        self.assertEqual(g.winner,'wolf');self.assertEqual(g.phase,'finished')
        with self.assertRaises(RuleError):ex.act(s,g,'p0','report','p2')

    def test_citizen_evades_only_known_visible_threat(self):
        g,s=self.setup_game();g.players['p2'].controller='ai'
        g._emit('조사',{'target':'p0','team':'wolf'},'p2')
        ex.field_bots(s,g)
        farthest=max(ex.PLACES,key=lambda n:__import__('math').dist(ex.PLACES[n],s['positions']['p0']))
        self.assertEqual(s['paths']['p2'],ex.route(s['positions']['p2'],ex.PLACES[farthest]))

    def test_endpoint_auth_hidden_death_and_queue_transition(self):
        with TestClient(create_app(':memory:',DemoClient,auto_workers=False)) as c:
            a=c.post('/api/rooms',json={'name':'A','size':9,'realtime':True}).json()
            url='/api/rooms/'+a['room_id'];h={'Authorization':'Bearer '+a['token']}
            initial=c.post(url+'/start',headers=h).json()
            self.assertEqual(initial['phase'],'night')
            hub=c.app.state.hub;room=hub.room(a['room_id']);g,s=self.setup_game();room.game=g;room.data['exploration']=s
            body={'day':2,'kind':'attack','target':'p2'}
            self.assertEqual(c.post(url+'/field-action',json=body).status_code,401)
            self.assertEqual(c.post(url+'/field-action',headers=h,json=body|{'day':1}).status_code,409)
            self.assertEqual(c.post(url+'/field-action',headers=h,json=body|{'x':500}).status_code,422)
            self.assertEqual(c.post(url+'/field-action',headers=h,json=body).status_code,200)
            public=hub.view(room,'p8')
            self.assertTrue(next(p for p in public['players'] if p['id']=='p2')['alive'])
            self.assertEqual(public['exploration']['bodies'],[])
            self.assertEqual(c.post(url+'/field-action',headers=h,json={'day':2,'kind':'report','target':'p2'}).status_code,200)
            self.assertEqual(hub.view(room,'p0')['phase'],'discussion')
            self.assertNotIn('p2',room.data['queue'])
            self.assertFalse(next(p for p in hub.view(room,'p8')['players'] if p['id']=='p2')['alive'])
            g.phase='vote';g.advance(timeout=True);hub.queue(room)
            self.assertEqual(g.phase,'council')
            g.advance(timeout=True);hub.queue(room);self.assertFalse(room.data['exploration']['active'])
            g.advance(timeout=True);hub.queue(room)
            self.assertTrue(room.data['exploration']['active']);self.assertEqual(room.data['exploration']['day'],3)
