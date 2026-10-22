"""Synthetic regressions; these are engineering cases, not an independent model benchmark."""
import copy
import unittest
from game.engine import Game
from game.evidence import verify
from game import beliefs
from game.decision import decide


def view(day=2):
    g = Game('guard-fixture', {f'p{i}':'ai' for i in range(5)}, seed=1)
    g.players['p0'].role = 'seer'
    g.phase = 'discussion'
    g.day = day
    return g.observation('p0')


def event(kind, **data):
    return {'sequence':2, 'type':kind, 'recipient':'p0', 'data':data}


def claim(kind='none', subject=None, value=''):
    return {'claim_type':kind, 'claim_subject':subject, 'claim_value':value}


class ReasoningGuardTests(unittest.TestCase):
    def test_none_cannot_hide_an_attack_assertion(self):
        self.assertFalse(verify(view(), claim(), [], {'text':'p1이 p2를 공격했습니다.'})['ok'])

    def test_none_cannot_hide_a_role_assertion(self):
        self.assertFalse(verify(view(), claim(), [], {'text':'p1은 늑대입니다.'})['ok'])

    def test_plain_questions_and_uncertainty_remain_possible(self):
        for text in ['p1이 p2를 공격했나요?', 'p1이 늑대인지 알 수 없습니다.', 'p1, 직접 목격한 시점을 알려줄 수 있나요?']:
            with self.subTest(text=text):
                self.assertTrue(verify(view(), claim(), [], {'text':text})['ok'])

    def test_idle_speech_does_not_support_suspicion(self):
        v = view();v['events'].append(event('발언', actor='p1', text='날씨가 좋네요.', day=2))
        self.assertFalse(verify(v, claim('suspect_wolf','p1'), [2], {})['ok'])

    def test_missing_coroner_role_is_not_a_village_confirmation(self):
        v = view();v['events'].append(event('검시', target='p1', day=2))
        self.assertFalse(verify(v, claim('confirmed_village','p1'), [2], {})['ok'])

    def test_stale_sighting_cannot_support_a_current_suspicion(self):
        v = view(day=5);v['events'].append(event('목격', player='p1', location='창고', day=1))
        self.assertFalse(verify(v, claim('suspect_wolf','p1'), [2], {})['ok'])
        # The historical location can still be reported as a dated observation.
        self.assertTrue(verify(v, claim('visited','p1','창고'), [2], {})['ok'])

    def test_old_suspicion_expires_by_evidence_day_not_repeated_claim_day(self):
        v = view(day=2);v['events'].append(event('목격', player='p1', location='창고', day=1))
        audit = {'semantic_support':'grounded_suspicion','claim':claim('suspect_wolf','p1'),'evidence_ids':[2]}
        ledger = beliefs.record_suspicion(v, beliefs.update(v), audit, False)
        v['day'] = 3
        ledger = beliefs.record_suspicion(v, beliefs.update(v, ledger), audit, False)
        v['day'] = 4
        ledger = beliefs.update(v, ledger)
        self.assertEqual(ledger['entries']['p1']['status'], 'needs_review')
        self.assertEqual(ledger['entries']['p1']['evidence_ids'], [2])
        self.assertEqual(beliefs.update(v, ledger)['changes'], [])

    def test_speech_does_not_poison_private_ledger(self):
        v = view();v['events'].append(event('발언', actor='p1', text='날씨가 좋네요.', day=2))
        audit = {'semantic_support':'grounded_suspicion','claim':claim('suspect_wolf','p1'),'evidence_ids':[2]}
        ledger = beliefs.record_suspicion(v, beliefs.update(v), audit, False)
        self.assertEqual(ledger['entries']['p1']['status'], 'unknown')

class AdditionalReasoningTests(unittest.TestCase):
    def test_each_assertion_is_checked_even_after_an_innocent_question(self):
        for text in ['p1이 공격했나요? p2는 늑대다.',
                     'p1은 늑대가 아닙니다. p2가 p3를 공격했습니다.',
                     'p1은 1일 차에 창고에 있었습니다.',
                     '제가 p1을 조사했습니다.']:
            with self.subTest(text=text):
                self.assertFalse(verify(view(), claim(), [], {'text':text})['ok'])

    def test_wolf_free_text_is_game_deception_not_verified_fact(self):
        v=view();v['self']['role']='wolf'
        result=verify(v,claim(),[],{'text':'p1이 p2를 공격했습니다.'})
        self.assertTrue(result['ok']);self.assertEqual(result['status'],'game_deception_allowed')

    def test_victim_or_malformed_sighting_is_not_suspicion_support(self):
        for e in [event('공격 목격',actor='p2',target='p1',day=2),
                  event('목격',player='p1',day=2),
                  event('추적',target='p1',location='기록 없음',day=2),
                  event('목격',player='p1',location='창고',day=3)]:
            v=view();v['events'].append(e)
            self.assertFalse(verify(v,claim('suspect_wolf','p1'),[2],{})['ok'])

    def test_fresh_suspicion_can_return_after_expiry_and_confirmation_overrides(self):
        v=view();v['events'].append(event('목격',player='p1',location='창고',day=1))
        audit={'semantic_support':'grounded_suspicion','claim':claim('suspect_wolf','p1'),'evidence_ids':[2]}
        ledger=beliefs.record_suspicion(v,beliefs.update(v),audit,False)
        v['day']=5;ledger=beliefs.update(v,ledger)
        fresh=event('탐색 목격',player='p1',location='시장',day=5);fresh['sequence']=3
        v['events'].append(fresh);audit['evidence_ids']=[3]
        ledger=beliefs.record_suspicion(v,ledger,audit,False)
        self.assertEqual(ledger['entries']['p1']['status'],'suspected')
        self.assertEqual(ledger['entries']['p1']['evidence_day'],5)
        confirmed=event('조사',target='p1',team='village',day=5);confirmed['sequence']=4
        v['events'].append(confirmed);v['day']=20
        ledger=beliefs.update(v,ledger)
        self.assertEqual(ledger['entries']['p1']['status'],'confirmed_village')
        self.assertIn('철회',beliefs.retraction(ledger,claim('confirmed_village','p1')))

    def test_structured_location_uses_matching_source_time(self):
        from game.evidence import statement
        sources=[event('탐색 목격',player='p1',location='창고',day=1,second=12.5),
                 {**event('목격',player='p2',location='창고',day=2),'sequence':3}]
        text=statement(claim('visited','p1','창고'),sources)
        self.assertIn('1일 차 탐색 12.5초',text);self.assertNotIn('2일 차',text)
        self.assertIn('현재 위치나 전체 동선을 의미하지 않습니다',text)

    def test_untyped_assertion_has_one_repair_and_no_pointless_research(self):
        class Model:
            metrics=[]
            def __init__(self):self.calls=0
            def generate(self,v,schema,feedback=None):
                self.calls+=1
                return {'text':'p1이 p2를 공격했습니다.','decision_summary':'합성 회귀 입력',
                        'evidence_ids':[],**claim()}
        model=Model();result=decide(view(),model,policy='verified')
        self.assertEqual(model.calls,2);self.assertTrue(result['fallback'])
        self.assertEqual(result['audit']['fact_retries'],1)
        self.assertEqual(result['audit']['research_rounds'],0)
        self.assertNotIn('공격했습니다',result['payload']['text'])

    def test_repair_can_switch_to_supported_structured_fact(self):
        v=view();v['events'].append(event('공격 목격',actor='p1',target='p2',day=2,second=7))
        class Model:
            metrics=[]
            def generate(self,v,schema,feedback=None):
                return {'text':'p1이 p2를 공격했습니다.','decision_summary':'합성 회귀 입력',
                        'evidence_ids':[2] if feedback else [],
                        **(claim('witnessed_attack','p1','p2') if feedback else claim())}
        result=decide(v,Model(),policy='verified')
        self.assertEqual(result['attempts'],2);self.assertFalse(result['fallback'])
        self.assertIn('2일 차 탐색 7초',result['payload']['text'])


if __name__ == '__main__':
    unittest.main()
