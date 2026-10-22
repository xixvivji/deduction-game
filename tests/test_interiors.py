"""Spatial privacy, authoritative doors, collision and persistence across interiors."""
import json
import math
import tempfile
import unittest
from fastapi.testclient import TestClient
from game import exploration as ex
from game.engine import Game, RuleError
from game.server import create_app, DemoClient, Hub


class InteriorTests(unittest.TestCase):
    def setup_game(self):
        game=Game('interiors',{f'p{i}':'human' for i in range(9)},seed=42)
        for i,p in enumerate(game.players.values()):p.role='wolf' if i<2 else 'citizen'
        game.realtime=True;game.day=2;game.phase='discussion'
        state=ex.begin(game,60);state['elapsed']=9
        return game,state

    def enter(self,state,game,pid,room='warehouse'):
        state['positions'][pid]=list(ex.INTERIORS[room]['outside'])
        ex.travel(state,game,pid,room)

    def test_all_doors_and_room_destinations_have_collision_safe_paths(self):
        for room,info in ex.INTERIORS.items():
            self.assertTrue(ex.free(*info['outside']))
            paths=[(ex.OUTSIDE,ex.PLACES['광장'],info['outside'])]
            paths.extend((room,ex.ROOM_ENTRY,p) for p in ex.ROOM_POINTS)
            for space,start,end in paths:
                path=ex.route(start,end,space);self.assertTrue(path,(space,start,end))
                pos=start
                for target in path:
                    for _ in range(40):
                        distance=math.dist(pos,target)
                        if distance<.01:break
                        pos=ex.walk(pos,target[0]-pos[0],target[1]-pos[1],min(.1,distance/ex.SPEED),space)
                        self.assertTrue(ex.free(*pos,space=space))
                    self.assertLess(math.dist(pos,target),.01)
                self.assertLess(math.dist(pos,end),.01)

    def test_doors_require_proximity_living_actor_and_correct_building(self):
        game,state=self.setup_game()
        with self.assertRaises(RuleError):ex.travel(state,game,'p0','warehouse')
        self.enter(state,game,'p0')
        self.assertEqual(ex.space_of(state,'p0'),'warehouse')
        self.assertEqual(ex.epoch(state,'p0'),1)
        with self.assertRaises(RuleError):ex.travel(state,game,'p0','clinic')
        state['positions']['p0']=[400,160]
        with self.assertRaises(RuleError):ex.travel(state,game,'p0','warehouse')
        state['positions']['p0']=list(ex.ROOM_ENTRY)
        ex.travel(state,game,'p0','warehouse')
        self.assertEqual(ex.space_of(state,'p0'),ex.OUTSIDE)
        self.assertEqual(state['positions']['p0'],list(ex.INTERIORS['warehouse']['outside']))
        game.players['p0'].alive=False
        with self.assertRaises(RuleError):ex.travel(state,game,'p0','warehouse')

    def test_payloads_do_not_include_opposite_space_or_other_room_players(self):
        game,state=self.setup_game()
        self.enter(state,game,'p0');self.enter(state,game,'p2','clinic')
        # Same numeric coordinates in all three spaces must never count as visibility.
        for pid in ['p0','p2','p3']:state['positions'][pid]=[400,460]
        outside=ex.observation(state,game,'p3');inside=ex.observation(state,game,'p0')
        self.assertIsNone(outside['interior'])
        self.assertNotIn('p0',[p['id'] for p in outside['players']])
        self.assertNotIn('p2',[p['id'] for p in inside['players']])
        self.assertNotIn('p3',[p['id'] for p in inside['players']])
        for field in ['roads','courts','buildings','places']:self.assertEqual(inside[field],[])
        self.assertEqual([d['id'] for d in inside['doors']],['warehouse'])
        for hidden in ['spaces','goals','paths','seen']:self.assertNotIn(hidden,inside)
        game.players['p0'].alive=False
        dead=ex.observation(state,game,'p0')
        self.assertIsNone(dead['interior']);self.assertEqual(dead['players'],[])
        self.assertIsNone(dead['self_position'])

    def test_attack_report_and_witness_never_cross_space_boundary(self):
        game,state=self.setup_game()
        self.enter(state,game,'p0');self.enter(state,game,'p2');self.enter(state,game,'p4')
        for pid in ['p0','p2','p3','p4']:state['positions'][pid]=[400,460]
        with self.assertRaises(RuleError):ex.act(state,game,'p0','attack','p3')
        ex.act(state,game,'p0','attack','p2')
        self.assertFalse(game.players['p2'].alive)
        self.assertFalse(any(e['type']=='공격 목격' for e in game.observation('p3')['events']))
        self.assertTrue(any(e['type']=='공격 목격' for e in game.observation('p4')['events']))
        self.assertEqual(ex.observation(state,game,'p3')['bodies'],[])
        with self.assertRaises(RuleError):ex.act(state,game,'p3','report','p2')
        ex.act(state,game,'p4','report','p2')
        self.assertTrue(state['meeting'])
        self.assertEqual(game.events[-1]['data']['location'],'창고')

    def test_encounters_and_final_location_are_room_aware(self):
        game,state=self.setup_game();game.realtime=False
        self.enter(state,game,'p0');self.enter(state,game,'p2')
        for pid in ['p0','p2','p3']:state['positions'][pid]=[400,460]
        ex.tick(state,game,.1,{},10);ex.finish(state,game)
        own=game.observation('p0')
        self.assertEqual(game.exploration_locations['p0'],'창고')
        witnesses=[e['data']['player'] for e in own['events'] if e['type']=='탐색 목격']
        self.assertIn('p2',witnesses);self.assertNotIn('p3',witnesses)

    def test_inside_walls_furniture_and_boundaries_are_solid(self):
        for room in ex.INTERIORS:
            pos=[400,460]
            for _ in range(200):
                pos=ex.walk(pos,0,1,.2,room)
                self.assertTrue(ex.free(*pos,space=room))
            self.assertLessEqual(pos[1],500-ex.RADIUS)
            self.assertFalse(ex.sees([300,270],[250,270],room))
            for x,y,w,h,_ in ex.INTERIORS[room]['furniture']:
                self.assertFalse(ex.free(x+w/2,y+h/2,space=room))

    def test_ai_cannot_attack_or_report_occupants_of_other_spaces(self):
        game,state=self.setup_game();game.players['p0'].controller='ai'
        state['positions']['p0']=[400,460]
        for pid in ['p2','p3','p4','p5','p6','p7','p8']:self.enter(state,game,pid)
        game.council['p0']={'target':'p2'}
        ex.field_bots(state,game)
        self.assertTrue(game.players['p2'].alive)
        game.players['p0'].role='citizen';game.players['p2'].alive=False
        state['bodies']['p2']={'second':9};state['elapsed']=11
        ex.field_bots(state,game)
        self.assertFalse(state['meeting'])

    def test_ai_uses_doors_to_visit_and_leave_all_interiors(self):
        game=Game('tour',{f'p{i}':'ai' for i in range(11)},seed=17)
        state=ex.begin(game,180);visited=set()
        for i in range(900):
            ex.tick(state,game,.2,{},i*.2)
            visited.update(state['spaces'].values())
            for pid,pos in state['positions'].items():self.assertTrue(ex.free(*pos,space=ex.space_of(state,pid)))
        self.assertEqual(visited,{ex.OUTSIDE,*ex.INTERIORS})
        self.assertGreater(sum(state['space_epochs'].values()),8)

    def test_api_epoch_auth_and_websocket_isolation(self):
        with TestClient(create_app(':memory:',DemoClient,auto_workers=False)) as client:
            a=client.post('/api/rooms',json={'name':'A','size':9,'explore':True,'realtime':True}).json()
            url='/api/rooms/'+a['room_id'];headers={'Authorization':'Bearer '+a['token']}
            b=client.post(url+'/join',json={'name':'B'}).json()
            client.post(url+'/start',headers=headers)
            room=client.app.state.hub.room(a['room_id']);room.game.phase='discussion';client.app.state.hub.queue(room)
            state=room.data['exploration'];state['positions']['p0']=list(ex.INTERIORS['warehouse']['outside'])
            state['positions']['p1']=list(state['positions']['p0'])
            body={'door':'warehouse','day':1,'space_epoch':0}
            self.assertEqual(client.post(url+'/door',json=body).status_code,401)
            self.assertEqual(client.post(url+'/door',headers=headers,json=dict(body,x=400)).status_code,422)
            self.assertEqual(client.post(url+'/door',headers=headers,json=dict(body,day=2)).status_code,409)
            room.intents['p0']=(1,0,10)
            result=client.post(url+'/door',headers=headers,json=body)
            self.assertEqual(result.status_code,200);self.assertNotIn('p0',room.intents)
            self.assertEqual(result.json()['exploration']['space'],'warehouse')
            self.assertEqual(client.post(url+'/door',headers=headers,json=body).status_code,409)
            move={'dx':1,'dy':0,'day':1,'sequence':10,'space_epoch':0}
            self.assertEqual(client.post(url+'/move',headers=headers,json=move).status_code,409)
            self.assertEqual(client.post(url+'/move',headers=headers,json=dict(move,space_epoch=1)).status_code,200)
            field={'kind':'report','target':'p1','day':1,'space_epoch':0}
            self.assertEqual(client.post(url+'/field-action',headers=headers,json=field).status_code,409)
            with client.websocket_connect('/ws/'+a['room_id']) as ws:
                ws.send_json({'token':b['token']});view=ws.receive_json()['exploration']
                self.assertIsNone(view['interior']);self.assertNotIn('p0',[p['id'] for p in view['players']])
            self.assertEqual(client.post(url+'/door',headers=headers,json=dict(body,space_epoch=1)).status_code,200)
            self.assertEqual(ex.space_of(state,'p0'),ex.OUTSIDE)

    def test_room_space_body_and_epoch_survive_server_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            hub=Hub(directory+'/games.db',DemoClient)
            room,_=hub.create('A',9,True);room.game,state=self.setup_game()
            room.data['status']='running';room.data['exploration']=state
            self.enter(state,room.game,'p0');self.enter(state,room.game,'p2')
            ex.act(state,room.game,'p0','attack','p2')
            hub.save(room);hub.db.close()
            restored=Hub(directory+'/games.db',DemoClient)
            try:
                loaded=restored.room(room.data['id']);saved=loaded.data['exploration']
                self.assertEqual(json.loads(json.dumps(state)),saved)
                view=ex.observation(saved,loaded.game,'p0')
                self.assertEqual(view['space'],'warehouse');self.assertEqual(view['space_epoch'],1)
                self.assertEqual(view['bodies'][0]['id'],'p2')
                self.assertEqual(loaded.intents,{})
            finally:restored.db.close()
