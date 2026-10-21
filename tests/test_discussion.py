import copy
import unittest
from unittest.mock import patch
from game import discussion
from game.decision import decide
from game.engine import Game


def view():
    g=Game('quality',{f'p{i}':'ai' for i in range(5)},seed=1)
    g.players['p0'].role='citizen';g.players['p1'].alive=False
    g.phase='discussion';g.day=2
    g._emit('발언',{'actor':'p0','text':'근거가 부족합니다. 추가 정보를 기다리며 신중하게 판단하겠습니다.','day':2})
    return g.observation('p0')


class Model:
    metrics=[]
    def __init__(self,texts):self.texts=texts;self.calls=0;self.views=[]
    def generate(self,v,schema,feedback=None):
        self.views.append(copy.deepcopy(v));text=self.texts[min(self.calls,len(self.texts)-1)];self.calls+=1
        return {'text':text,'decision_summary':'현재 발언을 정리합니다.','evidence_ids':[],
                'claim_type':'none','claim_subject':None,'claim_value':''}


class DiscussionTests(unittest.TestCase):
    def test_dead_vote_but_not_historical_or_negative(self):
        v=view()
        for text in ['p1에게 투표합시다.','p1을 추방해야 합니다.']:
            self.assertIn('dead_vote_target',discussion.check(v,text,'opinion')['issues'])
        for text in ['p1은 죽었으니 투표할 수 없습니다.','어제 p1에게 투표했습니다.','p1이 죽기 전 남긴 발언을 살펴봅시다.', 'p2가 p1에게 투표하자고 제안했습니다. p1은 이미 죽었으니 제외합시다.', 'p2가 p1에게 투표하자고 제안했는데, 이미 죽은 상태라 동의하기 어렵습니다.']:
            self.assertNotIn('dead_vote_target',discussion.check(v,text,'opinion')['issues'])

    def test_time_qualification(self):
        v=view()
        self.assertIn('unqualified_current_location',discussion.check(v,'현재 p2가 창고에 있습니다.','opinion')['issues'])
        self.assertTrue(discussion.check(v,'1일 차 기록 당시 p2가 창고에 있었습니다.','opinion')['ok'])

    def test_similar_repeated_deferral(self):
        result=discussion.check(view(),'근거가 부족합니다. 추가 정보를 기다리며 신중하게 판단하겠습니다.','opinion')
        self.assertIn('near_duplicate',result['issues']);self.assertIn('repeated_deferral',result['issues'])
        self.assertTrue(discussion.check(view(),'p2, 직접 본 기록이 어느 시점인지 알려줄 수 있나요?','opinion')['ok'])

    def test_direct_question_may_repeat_answer(self):
        v=view();v['question']={'text':'이전 의견을 다시 알려줘'}
        self.assertTrue(discussion.check(v,v['events'][-1]['data']['text'],'opinion')['ok'])

    def test_repair_succeeds_without_research(self):
        model=Model(['p1을 추방해야 합니다.','p2, 직접 목격한 시점을 알려줄 수 있나요?'])
        with patch('game.decision.time.sleep'):
            r=decide(view(),model,policy='verified')
        self.assertEqual(r['attempts'],2);self.assertEqual(r['audit']['quality_retries'],1)
        self.assertEqual(r['audit']['research_rounds'],0);self.assertFalse(r['fallback'])
        self.assertIn('p1',model.views[0]['discussion_context']['dead'])

    def test_bounded_failure_uses_safe_living_question(self):
        model=Model(['p1에게 투표합시다.'])
        with patch('game.decision.time.sleep'):
            r=decide(view(),model,policy='verified')
        self.assertEqual(model.calls,2);self.assertTrue(r['fallback'])
        self.assertNotIn('p1',r['payload']['text']);self.assertIn('?',r['payload']['text'])
        self.assertEqual(len(r['audit']['quality_history']),2)

    def test_baseline_switch_preserves_old_path(self):
        model=Model(['p1에게 투표합시다.'])
        r=decide(view(),model,policy='verified',discussion_quality=False)
        self.assertEqual(r['attempts'],1);self.assertFalse(r['fallback'])
        self.assertNotIn('discussion_context',model.views[0])

    def test_auxiliary_context_records_are_valid_citations(self):
        v=view();selected=copy.deepcopy(v);selected['events']=[]
        selected['memory']={'selected_events':0}
        discussion.attach_context(selected,v)
        ids={e['sequence'] for e in selected['events']}
        self.assertTrue(all(e['sequence'] in ids for e in selected['discussion_context']['own_recent']))
        self.assertEqual(selected['memory']['selected_events'],len(ids))

    def test_only_public_and_self_recent_context(self):
        v=view();v['events'].append({'sequence':100,'type':'발언','recipient':'p3','data':{'actor':'p3','text':'secret'}})
        ctx=discussion.context(v)
        self.assertNotIn('secret',str(ctx));self.assertLessEqual(len(ctx['own_recent']),3)
        self.assertLessEqual(len(ctx['public_recent']),3)
