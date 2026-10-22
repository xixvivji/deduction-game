import unittest
from unittest.mock import patch
from game.engine import Game
from game.decision import decide
from game.server import DemoClient, Hub


class AuditTests(unittest.TestCase):
    def test_private_or_missing_citation_is_rejected(self):
        game=Game('r',{str(i):'ai' for i in range(5)},seed=1)
        viewer=next(p.id for p in game.players.values() if p.role=='citizen')
        seer=next(p.id for p in game.players.values() if p.role=='seer')
        game._emit('조사',{'target':'0','team':'wolf'},seer)
        private_id=game.events[-1]['sequence']
        class Invalid:
            def generate(self,*args):
                return {'location':'광장','target':None,'decision_summary':'비공개 기록 인용 시도','evidence_ids':[private_id]}
        with patch('game.decision.time.sleep'):
            result=decide(game.observation(viewer),Invalid())
        self.assertTrue(result['fallback'])
        self.assertEqual(result['audit']['evidence_ids'],[])

    def test_valid_audit_is_separate_from_engine_action(self):
        game=Game('r',{str(i):'ai' for i in range(5)},seed=1)
        class Valid:
            def generate(self,*args):
                return {'location':'광장','target':None,'decision_summary':'첫날이므로 공격하지 않습니다.','evidence_ids':[1]}
        result=decide(game.observation('0'),Valid())
        self.assertEqual(result['audit']['evidence_ids'],[1])
        self.assertNotIn('decision_summary',result['payload'])
        game.submit('0','ok',game.version,result['kind'],**result['payload'])

    def test_report_not_visible_until_game_end(self):
        hub=Hub(':memory:',DemoClient)
        try:
            room,_=hub.create('A',5)
            room.game=Game(room.data['id'],{f'p{i}':'human' if i==0 else 'ai' for i in range(5)},seed=1)
            room.data['history']=[{'audit':{'summary':'secret-audit'}}]
            self.assertNotIn('report',hub.view(room,'p0'))
            room.game._finish('draw')
            self.assertEqual(hub.view(room,'p0')['report']['decisions'][0]['audit']['summary'],'secret-audit')
        finally:hub.db.close()
