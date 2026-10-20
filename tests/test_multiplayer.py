import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from game.engine import Game, RuleError
from game.memory import retrieve
from game.decision import decide
from game.server import create_app, DemoClient
from game.providers import ProviderError


class CouncilTests(unittest.TestCase):
    def setUp(self):
        self.g=Game('r',{str(i):'ai' for i in range(7)},seed=3)
        self.g.advance(timeout=True);self.g.advance();self.g.advance(timeout=True)
        self.wolves=[p.id for p in self.g.players.values() if p.role=='wolf']
        self.citizen=next(p.id for p in self.g.players.values() if p.role=='citizen')

    def test_council_visibility_and_permissions(self):
        self.assertEqual(self.g.phase,'council')
        self.g.submit(self.wolves[0],'one',self.g.version,'council',text='함께 공격',target=self.citizen)
        for wolf in self.wolves:
            self.assertTrue(any(e['type']=='비밀 협의' for e in self.g.observation(wolf)['events']))
        self.assertFalse(any(e['type']=='비밀 협의' for e in self.g.observation(self.citizen)['events']))
        with self.assertRaises(RuleError):
            self.g.submit(self.citizen,'two',self.g.version,'council',text='참가',target=None)
        self.g.players[self.wolves[1]].alive=False
        with self.assertRaises(RuleError):
            self.g.submit(self.wolves[1],'three',self.g.version,'council',text='사망',target=None)

    def test_graph_council_and_snapshot_idempotence(self):
        wolf=self.wolves[0]
        outcome=decide(self.g.observation(wolf),DemoClient())
        self.g.submit(wolf,'same',self.g.version,outcome['kind'],**outcome['payload'])
        restored=Game.restore(json.loads(json.dumps(self.g.snapshot())))
        self.assertEqual(restored.observation(wolf),self.g.observation(wolf))
        old=restored.version
        restored.submit(wolf,'same',0,outcome['kind'],**outcome['payload'])
        self.assertEqual(restored.version,old)

    def test_graph_invalid_output_falls_back_after_two_calls(self):
        class Invalid:
            calls=0
            def generate(self,*args):
                self.calls+=1
                return {"unexpected":"value"}
        client=Invalid()
        with patch('game.decision.time.sleep'):
            outcome=decide(self.g.observation(self.wolves[0]),client)
        self.assertEqual(client.calls,2)
        self.assertTrue(outcome['fallback'])
        self.assertEqual(outcome['payload']['target'],None)

    def test_graph_auth_failure_stops_without_retry(self):
        class Denied:
            calls=0
            def generate(self,*args):
                self.calls+=1
                raise ProviderError('인증 실패')
        client=Denied()
        with self.assertRaises(ProviderError):
            decide(self.g.observation(self.wolves[0]),client)
        self.assertEqual(client.calls,1)

    def test_retrieval_preserves_private_evidence_without_adding_secrets(self):
        for i in range(30): self.g._emit('발언',{'text':f'잡담 {i}'})
        self.g._emit('조사',{'target':self.wolves[0],'team':'wolf'},self.citizen)
        self.g._emit('비밀 협의',{'text':'TOP SECRET','day':2},self.wolves)
        view=retrieve(self.g.observation(self.citizen),top_k=3,recent=2)
        self.assertLess(len(view['events']),len(self.g.events))
        self.assertTrue(any(e['type']=='조사' for e in view['events']))
        self.assertNotIn('TOP SECRET',json.dumps(view))


class WebTests(unittest.TestCase):
    def setup_room(self, client):
        a=client.post('/api/rooms',json={'name':'A','size':7}).json()
        b=client.post(f"/api/rooms/{a['room_id']}/join",json={'name':'B'}).json()
        return a,b

    def headers(self,s): return {'Authorization':'Bearer '+s['token']}

    def test_auth_host_start_private_state_and_duplicate(self):
        with TestClient(create_app(':memory:',DemoClient,auto_workers=False)) as c:
            a,b=self.setup_room(c);url='/api/rooms/'+a['room_id']
            self.assertEqual(c.get(url).status_code,401)
            self.assertEqual(c.post(url+'/start',headers=self.headers(b)).status_code,409)
            first=c.post(url+'/start',headers=self.headers(a)).json()
            other=c.get(url,headers=self.headers(b)).json()
            self.assertNotIn('roles',first)
            self.assertEqual(other['self']['id'],'p1')
            self.assertNotIn('token_hash',json.dumps(first))
            body={'command_id':'same','version':first['version'],'kind':'night','payload':{'location':'광장','target':None}}
            self.assertEqual(c.post(url+'/commands',headers=self.headers(b),json=body).status_code,409)
            response=c.post(url+'/commands',headers=self.headers(a),json=body)
            self.assertEqual(response.status_code,200)
            duplicate=c.post(url+'/commands',headers=self.headers(a),json=body)
            self.assertEqual(response.json()['version'],duplicate.json()['version'])
            with c.websocket_connect('/ws/'+a['room_id'],headers={'origin':'http://testserver'}) as ws:
                ws.send_json({'token':b['token']})
                self.assertEqual(ws.receive_json()['self']['id'],'p1')

    def test_restart_resume_and_old_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'games.db')
            with TestClient(create_app(db,DemoClient,auto_workers=False)) as c:
                a,b=self.setup_room(c);url='/api/rooms/'+a['room_id']
                before=c.post(url+'/start',headers=self.headers(a)).json()
            with TestClient(create_app(db,DemoClient,auto_workers=False)) as c:
                after=c.get(url,headers=self.headers(a)).json()
                self.assertEqual(after['status'],'paused')
                self.assertEqual(after['self'],before['self'])
                self.assertEqual(c.post(url+'/resume',headers=self.headers(b)).status_code,409)
                self.assertEqual(c.post(url+'/resume',headers=self.headers(a)).json()['status'],'running')

    def test_background_game_ends_with_human_timeouts(self):
        with TestClient(create_app(':memory:',DemoClient,turn_seconds=.01)) as c:
            a=c.post('/api/rooms',json={'name':'A','size':5}).json();url='/api/rooms/'+a['room_id']
            c.post(url+'/start',headers=self.headers(a))
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                state=c.get(url,headers=self.headers(a)).json()
                if state['status'] in ('finished','paused'):break
                time.sleep(.05)
            self.assertEqual(state['status'],'finished',state.get('error'))
            self.assertIn('roles',state)
            self.assertIn('report',state)

    def test_other_room_token_rejected(self):
        with TestClient(create_app(':memory:',DemoClient,auto_workers=False)) as c:
            a,b=self.setup_room(c)
            other=c.post('/api/rooms',json={'name':'C','size':5}).json()
            self.assertEqual(c.get('/api/rooms/'+a['room_id'],headers=self.headers(other)).status_code,401)
