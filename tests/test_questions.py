import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from game.engine import Game, RuleError
from game.memory import retrieve
from game.decision import decide
from game.server import create_app, DemoClient, public_citations


class QuestionTests(unittest.TestCase):
    def game(self):
        g = Game('q', {f'p{i}': 'human' if i == 0 else 'ai' for i in range(7)}, seed=3)
        g.advance(timeout=True)
        return g

    def ask(self, g, ids=None):
        g.submit('p0', 'ask', g.version, 'question', text='어제 창고에서 누구를 보았나요?', target='p2', evidence_ids=ids or [])

    def test_answer_consumes_existing_speech_and_restores(self):
        g = self.game(); self.ask(g)
        g = Game.restore(json.loads(json.dumps(g.snapshot())))
        self.assertIn('question', g.observation('p2'))
        self.assertNotIn('question', g.observation('p1'))
        before = g.snapshot()
        with self.assertRaises(RuleError):
            g.submit('p1', 'wrong', g.version, 'speak', text='끼어들기')
        self.assertEqual(g.snapshot(), before)
        g.submit('p2', 'answer', g.version, 'speak', text='확인할 기록이 없습니다.')
        self.assertEqual(g.events[-1]['type'], '답변')
        self.assertEqual(g.speeches, {'p0': 1, 'p2': 1})
        self.assertIsNone(g.pending_question)

    def test_private_citations_and_exhausted_targets_rejected(self):
        g = self.game(); g._emit('목격', {'player':'p3','location':'창고'}, 'p0')
        with self.assertRaises(RuleError): self.ask(g, [g.events[-1]['sequence']])
        self.assertEqual(g.speeches, {})
        g.speeches['p2'] = 2
        with self.assertRaises(RuleError): self.ask(g)

    def test_question_search_pins_quotes_and_has_no_other_private_memory(self):
        g = self.game()
        g._emit('발언', {'actor':'p2','text':'어제 창고에서 p3을 봤습니다.', 'day':1})
        quote = g.events[-1]['sequence']
        for i in range(35): g._emit('발언', {'actor':'p4','text':f'날씨 이야기 {i}'})
        g._emit('목격', {'text':'PRIVATE OTHER'}, 'p1')
        self.ask(g, [quote])
        view = retrieve(g.observation('p2'), top_k=2, recent=2)
        self.assertIn(quote, [e['sequence'] for e in view['events']])
        self.assertIn(g.pending_question['sequence'], [e['sequence'] for e in view['events']])
        self.assertNotIn('PRIVATE OTHER', json.dumps(view))
        self.assertLess(len(view['events']), len(g.events))

    def test_generation_receives_question_and_retrieved_quote(self):
        g = self.game(); g._emit('발언', {'actor':'p2','text':'창고에 있었습니다.'})
        quote = g.events[-1]['sequence']; self.ask(g, [quote])
        class Grounded:
            def generate(self, view, schema, feedback=None):
                assert view['question']['target'] == 'p2'
                assert quote in [e['sequence'] for e in view['events']]
                return {'text':'앞서 창고에 있었다고 말했습니다.', 'decision_summary':'과거 발언 참조', 'evidence_ids':[quote]}
        result = decide(g.observation('p2'), Grounded())
        self.assertFalse(result['fallback'])
        self.assertEqual(result['audit']['evidence_ids'], [quote])

    def test_fallback_can_complete_pending_answer(self):
        g = self.game(); self.ask(g)
        class Invalid:
            def generate(self, *args): return {}
        with patch('game.decision.time.sleep'):
            result = decide(g.observation('p2'), Invalid())
        g.submit('p2','fallback',g.version,result['kind'],**result['payload'])
        self.assertIsNone(g.pending_question)
        self.assertEqual(g.events[-1]['type'], '답변')

    def test_api_moves_one_slot_and_duplicate_is_idempotent(self):
        with TestClient(create_app(':memory:', DemoClient, auto_workers=False)) as c:
            session = c.post('/api/rooms', json={'name':'A','size':7}).json()
            url = '/api/rooms/'+session['room_id']; headers={'Authorization':'Bearer '+session['token']}
            c.post(url+'/start', headers=headers)
            hub=c.app.state.hub; room=hub.room(session['room_id'])
            room.game.advance(timeout=True); hub.queue(room)
            before=list(room.data['queue'])
            body={'command_id':'ask','version':room.game.version,'kind':'question','payload':{'text':'창고에서 무엇을 봤나요?','target':'p5','evidence_ids':[]}}
            response=c.post(url+'/commands',headers=headers,json=body)
            self.assertEqual(response.status_code,200, response.text)
            self.assertEqual(hub.active(room),'p5')
            self.assertEqual(sorted(before),sorted(room.data['queue']))
            index=room.data['index']; queue=list(room.data['queue'])
            self.assertEqual(c.post(url+'/commands',headers=headers,json=body).status_code,200)
            self.assertEqual(room.data['index'],index)
            self.assertEqual(room.data['queue'],queue)
            self.assertNotIn('question',response.json())

    def test_public_answer_never_exposes_private_citations(self):
        view = {"events": [
            {"sequence":1,"recipient":None},
            {"sequence":2,"recipient":"p2"},
            {"sequence":3,"recipient":["p2","p3"]}]}
        self.assertEqual(public_citations(view,[1,2,3,99]),[1])
