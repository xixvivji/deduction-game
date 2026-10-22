import asyncio
import json
import math
import time
import tempfile
import unittest
from fastapi.testclient import TestClient
from game import exploration as ex
from game.engine import Game, RuleError
from game.ai import action_spec
from game.decision import decide
from game.server import create_app, DemoClient, Hub


class ExplorationTests(unittest.TestCase):
    def game(self):return Game('walk',{f'p{i}':'human' if i<2 else 'ai' for i in range(9)},seed=42)

    def test_speed_diagonal_and_wall_tunneling(self):
        a=[460,300]
        self.assertAlmostEqual(math.dist(a,ex.walk(a,1,1,.1)),ex.SPEED*.1)
        self.assertLessEqual(math.dist(a,ex.walk(a,1,0,100)),ex.SPEED*.2+.0001)
        b=[140,160]
        for _ in range(100):b=ex.walk(b,1,0,.2)
        self.assertLessEqual(b[0],180-ex.RADIUS)
        self.assertTrue(ex.free(*b))

    def test_sight_distance_and_occlusion(self):
        self.assertTrue(ex.sees([460,300],[500,300]))
        self.assertFalse(ex.sees([460,300],[820,180]))
        self.assertFalse(ex.sees([200,80],[200,250]))

    def test_filtered_payload_dead_observer_and_no_goals(self):
        g=self.game();s=ex.begin(g)
        s['positions']['p0']=[460,300];s['positions']['p1']=[820,180]
        v=ex.observation(s,g,'p0')
        self.assertNotIn('p1',[p['id'] for p in v['players']])
        self.assertNotIn('paths',v);self.assertNotIn('seen',v)
        self.assertNotIn('role',json.dumps(v))
        g.players['p0'].alive=False
        self.assertEqual(ex.observation(s,g,'p0')['players'],[])
        self.assertIsNone(ex.observation(s,g,'p0')['self_position'])

    def test_expired_input_stops_and_bots_route(self):
        g=self.game();s=ex.begin(g);before=list(s['positions']['p0']);bot=list(s['positions']['p2'])
        ex.tick(s,g,.1,{'p0':(1,0,0)},10)
        self.assertEqual(before,s['positions']['p0'])
        for _ in range(20):ex.tick(s,g,.1,{},10)
        self.assertNotEqual(bot,s['positions']['p2'])
        self.assertTrue(all(ex.free(*pos) for pos in s['positions'].values()))

    def test_encounters_private_and_night_location_authoritative(self):
        g=self.game();s=ex.begin(g)
        s['positions']['p0']=[820,180];s['positions']['p1']=[800,180]
        ex.tick(s,g,.1,{},10);ex.finish(s,g)
        own=g.observation('p0')
        self.assertEqual(own['self']['explored_location'],'창고')
        self.assertTrue(any(e['type']=='탐색 목격' and e['data']['player']=='p1' for e in own['events']))
        self.assertTrue(all(e['recipient'] in (None,'p0') for e in own['events']))
        self.assertEqual(action_spec(own)[1]['properties']['location']['enum'],['창고'])
        with self.assertRaises(RuleError):g.submit('p0','fake',g.version,'night',location='광장',target=None)
        outcome=decide(own,DemoClient())
        self.assertEqual(outcome['payload']['location'],'창고')
        g.advance(timeout=True)
        self.assertFalse(any(e['type']=='목격' for e in g.events))
        restored=Game.restore(json.loads(json.dumps(g.snapshot())))
        self.assertEqual(restored.exploration_locations,g.exploration_locations)

    def setup(self,c):
        a=c.post('/api/rooms',json={'name':'A','size':9,'explore':True}).json()
        url='/api/rooms/'+a['room_id'];headers={'Authorization':'Bearer '+a['token']}
        b=c.post(url+'/join',json={'name':'B'}).json()
        state=c.post(url+'/start',headers=headers).json()
        return a,b,url,headers,state

    def test_api_auth_validation_stale_input_and_command_block(self):
        with TestClient(create_app(':memory:',DemoClient,auto_workers=False)) as c:
            a,b,url,h,state=self.setup(c)
            self.assertEqual(state['phase'],'explore');self.assertFalse(state['can_act'])
            body={'dx':1,'dy':0,'day':1,'sequence':2}
            self.assertEqual(c.post(url+'/move',json=body).status_code,401)
            for invalid in [dict(body,dx=2),dict(body,dx=True),dict(body,x=900)]:
                self.assertEqual(c.post(url+'/move',headers=h,json=invalid).status_code,422)
            self.assertEqual(c.post(url+'/move',headers=h,json=dict(body,day=2)).status_code,409)
            room=c.app.state.hub.room(a['room_id']);old=list(room.data['exploration']['positions']['p0'])
            self.assertEqual(c.post(url+'/move',headers=h,json=body).status_code,200)
            c.post(url+'/move',headers=h,json=dict(body,sequence=1,dx=-1))
            self.assertEqual(room.intents['p0'][0],1)
            self.assertEqual(old,room.data['exploration']['positions']['p0'])
            command={'command_id':'bypass','version':state['version'],'kind':'night','payload':{'location':'광장','target':None}}
            self.assertEqual(c.post(url+'/commands',headers=h,json=command).status_code,409)
            room.game.players['p0'].alive=False
            self.assertEqual(c.post(url+'/move',headers=h,json=body).status_code,409)

    def test_two_sessions_receive_different_visible_positions(self):
        with TestClient(create_app(':memory:',DemoClient,auto_workers=False)) as c:
            a,b,url,h,_=self.setup(c);room=c.app.state.hub.room(a['room_id'])
            room.data['exploration']['positions']['p0']=[100,460]
            room.data['exploration']['positions']['p1']=[820,180]
            va=c.get(url,headers=h).json()['exploration']
            vb=c.get(url,headers={'Authorization':'Bearer '+b['token']}).json()['exploration']
            self.assertNotIn('p1',[p['id'] for p in va['players']]);self.assertNotIn('p0',[p['id'] for p in vb['players']])
            with c.websocket_connect('/ws/'+a['room_id']) as ws:
                ws.send_json({'token':a['token']})
                self.assertEqual(ws.receive_json()['exploration']['players'],va['players'])

    def test_saved_exploration_preserves_progress_but_not_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path=directory+'/game.db'
            hub=Hub(path,DemoClient)
            room,_=hub.create('A',9,True)
            room.game=self.game();room.data['status']='running';hub.queue(room)
            ex.tick(room.data['exploration'],room.game,.1,{'p0':(1,0,10)},10)
            room.intents['p0']=(1,0,10)
            hub.save(room);hub.db.close()
            restored=Hub(path,DemoClient)
            try:
                saved=restored.room(room.data['id'])
                self.assertEqual(saved.data['exploration'],room.data['exploration'])
                self.assertEqual(saved.intents,{})
                self.assertEqual(saved.game.snapshot(),room.game.snapshot())
            finally:restored.db.close()

    def test_worker_exploration_then_night_and_next_day(self):
        async def run():
            hub=Hub(':memory:',DemoClient,turn_seconds=.01)
            room,_=hub.create('A',9,True);room.game=self.game();room.data['status']='running';hub.queue(room)
            room.data['exploration']['duration']=.15
            task=asyncio.create_task(hub.run(room))
            try:
                for _ in range(300):
                    await asyncio.sleep(.02)
                    if room.data["exploration"]["day"]>=2:break
                self.assertGreaterEqual(room.game.day,2)
                # First exploration was resolved, and a new night receives a fresh map.
                self.assertEqual(room.game.exploration_day,1)
                self.assertEqual(room.data['exploration']['day'],2)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True);hub.db.close()
        asyncio.run(run())
