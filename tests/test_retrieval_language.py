import copy
import unittest
from game.evaluate_retrieval_language import fixture
from game.memory import rank_events, retrieve
from game.memory_backend import LocalMemory, scoped
from game.mcp_memory import MCPMemory
from game.runtime_cache import TOKEN_CACHE
from game.search_language import concepts, terms


class RetrievalLanguageTests(unittest.TestCase):
    def test_old_unpinned_record_found_by_different_wording(self):
        view,_=fixture();view=scoped(view)
        before=retrieve(view,'거짓말',top_k=1,recent=0,use_cache=False,expand=False)
        after=retrieve(view,'거짓말',top_k=1,recent=0,use_cache=False)
        self.assertNotEqual(before['events'][0]['sequence'],1)
        self.assertEqual(after['events'][0]['sequence'],1)

    def test_recall_keeps_negation_and_does_not_rewrite_facts(self):
        view,_=fixture();view=scoped(view);old=copy.deepcopy(view)
        selected=retrieve(view,'알리바이',top_k=1,recent=0)
        self.assertEqual(selected['events'][0]['data']['text'],'밤중 행적은 확인되지 않았다.')
        self.assertEqual(view,old)
        self.assertNotIn('confirmed',selected['events'][0])

    def test_player_ids_are_atomic_and_korean_particles_work(self):
        self.assertIn('p10',terms('p10은 창고에 있었다'))
        self.assertNotIn('p1',terms('p10은 창고에 있었다'))
        events=[{'sequence':1,'type':'발언','data':{'actor':'p1','text':'방문했다'}},
                {'sequence':2,'type':'발언','data':{'actor':'p10','text':'방문했다'}}]
        ranked,_=rank_events(events,'p1은 어디에 있었나')
        self.assertEqual(ranked[0],0)

    def test_related_groups_do_not_mark_unrelated_substrings_or_roles(self):
        self.assertNotIn('whereabouts',concepts('저장소를 점검했다'))
        self.assertNotIn('protection',concepts('보호자 이름'))
        self.assertNotIn('attack',concepts('늑대인 것 같다'))
        self.assertNotEqual(concepts('공격했다'),concepts('추방했다'))

    def test_cache_version_and_mode_isolation_preserve_results(self):
        view,_=fixture();view=scoped(view);TOKEN_CACHE.clear()
        retrieve(view,'거짓말',top_k=1,recent=0,use_cache=True,expand=False)
        expanded=retrieve(view,'거짓말',top_k=1,recent=0,use_cache=True)
        cached=retrieve(view,'거짓말',top_k=1,recent=0,use_cache=True)
        plain=retrieve(view,'거짓말',top_k=1,recent=0,use_cache=False)
        self.assertEqual(expanded['memory']['cache_hits'],0)
        self.assertEqual(cached['memory']['cache_hits'],len(view['events']))
        self.assertEqual(cached['events'],plain['events'])

    def test_local_scope_rejects_exact_private_match(self):
        view,_=fixture()
        result=LocalMemory().search(view,'거짓말')
        ids={e['sequence'] for e in result['events']}
        self.assertIn(1,ids)
        self.assertNotIn(99,ids)

    def test_structured_event_type_can_supply_missing_search_words(self):
        events=[{'sequence':1,'type':'목격','data':{'player':'p1','location':'창고'}},
                {'sequence':2,'type':'발언','data':{'actor':'p2','text':'창고'}}]
        ranked,_=rank_events(events,'보았던 사람')
        self.assertEqual(ranked[0],0)
        self.assertIn('protection',concepts('보호 {"target":"p1"}'))
        self.assertIn('statement_consistency',concepts('말을 바꿨다'))

    def test_fixture_is_fixed_and_has_no_pin_types(self):
        view,cases=fixture()
        self.assertEqual((view,cases),fixture())
        self.assertEqual(len(cases),24)
        self.assertEqual({e['type'] for e in view['events']},{'발언'})
        self.assertTrue(all(c['expected']<10 for c in cases))


class RetrievalLanguageMCPTests(unittest.TestCase):
    def test_real_mcp_uses_expansion_and_keeps_private_records_out(self):
        view,_=fixture()
        memory=MCPMemory()
        try:
            result=memory.search(view,'거짓말')
            self.assertIn(1,[e['sequence'] for e in result['events']])
            self.assertNotIn(99,[e['sequence'] for e in result['events']])
            self.assertEqual(result['memory']['strategy'],'game-vocabulary-v1')
        finally:
            memory.close()
