"""Development cases. Sealed evaluation cases are deliberately not imported here."""
import copy
import unittest
from game.engine import Game
from game.evidence import verify
from game.decision import decide

NONE={'claim_type':'none','claim_subject':None,'claim_value':''}
PUBLIC={'sequence':2,'type':'발언','recipient':None,
        'data':{'actor':'p3','text':'p1은 늑대입니다.','day':2}}


def view(role='doctor', events=()):
    g=Game('scope-dev',{f'p{i}':'ai' for i in range(11)},seed=1)
    g.players['p0'].role=role;g.phase='discussion';g.day=2
    v=g.observation('p0');v['events']+=copy.deepcopy(list(events))
    return v


def accepted(text, role='doctor', events=(), ids=()):
    return verify(view(role,events),NONE,list(ids),{'text':text})['ok']


class ClaimScopeTests(unittest.TestCase):
    def test_truthful_self_roles(self):
        for role,text in [('doctor','저는 의사입니다.'),('seer','제 역할은 조사자예요.'),
                          ('tracker','나는 추적자다.'),('citizen','p0는 시민입니다.'),
                          ('coroner','제 직업은 검시관입니다.'),('doctor','저는 시민 진영입니다.')]:
            with self.subTest(text=text):self.assertTrue(accepted(text,role))

    def test_false_self_or_other_role_is_not_exempt(self):
        for text in ['저는 조사자입니다.','p10는 의사입니다.','p1은 의사입니다.',
                     '저는 의사입니다. p1은 늑대입니다.','나는 의사이고 p1은 늑대다.']:
            with self.subTest(text=text):self.assertFalse(accepted(text))

    def test_condition_is_local_to_the_claim(self):
        for text in ['p1이 공격했다면 기록부터 확인합시다.',
                     'p1이 늑대라면 근거를 제시해 주세요.',
                     'p1이 죽인 것이 사실이라면 목격 시점을 확인해야 합니다.']:
            with self.subTest(text=text):self.assertTrue(accepted(text))
        for text in ['p1이 공격했다면 확인합시다. p2는 늑대다.',
                     'p1이 늑대라면 확인합시다, p2가 p3를 죽였어.',
                     'p1이 공격했습니다. 그게 사실이라면 확인합시다.']:
            with self.subTest(text=text):self.assertFalse(accepted(text))

    def test_question_uncertainty_and_factual_negation_are_distinct(self):
        for text in ['p1이 p2를 죽인 걸 봤나요?', 'p1이 창고에 있었나요?',
                     'p1이 공격했는지는 모르겠어요.', 'p1은 늑대일 수도 있습니다.',
                     'p1은 늑대인지 알 수 없습니다.']:
            with self.subTest(text=text):self.assertTrue(accepted(text))
        for text in ['p1은 절대로 늑대가 아닙니다.', 'p1이 p2를 공격하지 않았습니다.',
                     'p1이 공격했나요? p2는 늑대다.']:
            with self.subTest(text=text):self.assertFalse(accepted(text))

    def test_colloquial_assertions_need_structured_evidence(self):
        for text in ['p1이 p2를 죽인 걸 직접 봤어.', 'p1은 늑대야.',
                     'p1이 p2를 살해했어.', 'p1이 시장에 있었어.',
                     'p1이 범인이라고 확신합니다.']:
            with self.subTest(text=text):self.assertFalse(accepted(text))

    def test_faithful_public_quote_is_only_a_report(self):
        for text in ['p3가 "p1은 늑대입니다."라고 말했습니다.',
                     '“p1은 늑대입니다.”라고 p3가 주장했어요.']:
            with self.subTest(text=text):
                result=verify(view(events=[PUBLIC]),NONE,[2],{'text':text})
                self.assertTrue(result['ok']);self.assertEqual(result['status'],'opinion')

    def test_quotes_cannot_forge_author_source_or_endorse_another_fact(self):
        text='p3가 "p1은 늑대입니다."라고 말했습니다.'
        self.assertFalse(accepted(text,events=[PUBLIC]))
        self.assertFalse(accepted(text.replace('p3가','p4가'),events=[PUBLIC],ids=[2]))
        self.assertFalse(accepted(text,events=[{**PUBLIC,'recipient':'p0'}],ids=[2]))
        edited=copy.deepcopy(PUBLIC);edited['data']['text']='p1은 늑대입니다. 라고 단정할 수 없습니다.'
        self.assertFalse(accepted(text,events=[edited],ids=[2]))
        self.assertFalse(accepted(text+' 그러므로 p1은 늑대입니다.',events=[PUBLIC],ids=[2]))
        self.assertFalse(accepted('"p1은 늑대입니다."',events=[PUBLIC],ids=[2]))

    def test_wolf_strategy_stays_separate(self):
        r=verify(view('wolf'),NONE,[],{'text':'저는 의사입니다. p1이 범인입니다.'})
        self.assertTrue(r['ok']);self.assertEqual(r['status'],'game_deception_allowed')

    def test_graph_accepts_self_disclosure_without_repair(self):
        class Model:
            metrics=[]
            def generate(self,*args):
                return {'text':'저는 의사입니다.','decision_summary':'본인 역할 소개','evidence_ids':[],**NONE}
        r=decide(view(),Model(),policy='verified')
        self.assertFalse(r['fallback']);self.assertEqual(r['attempts'],1)
        self.assertEqual(r['audit']['fact_retries'],0)

    def test_graph_limits_newly_detected_claim_to_one_repair(self):
        class Model:
            metrics=[]
            def generate(self,*args):
                return {'text':'p1이 p2를 죽인 걸 직접 봤어.','decision_summary':'합성 입력','evidence_ids':[],**NONE}
        r=decide(view(),Model(),policy='verified')
        self.assertTrue(r['fallback']);self.assertEqual(r['attempts'],2)
        self.assertEqual(r['audit']['fact_retries'],1)
        self.assertEqual(r['audit']['research_rounds'],0)
