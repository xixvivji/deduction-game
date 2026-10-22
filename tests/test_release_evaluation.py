import unittest
from game.evaluate_release import cases, summarize
from game.ai import action_spec
from game.memory_backend import scoped


class ReleaseEvaluationTests(unittest.TestCase):
    def test_fixtures_are_fixed_scoped_and_forced_actions_are_unambiguous(self):
        data=cases()
        self.assertEqual(data,cases())
        self.assertEqual(len(data),10)
        self.assertEqual(len({r['id'] for r in data}),10)
        for row in data:
            visible={e['sequence'] for e in scoped(row['view'])['events']}
            self.assertTrue(set(row['required'])<=visible)
            if row['group']=='forced':
                kind,schema=action_spec(row['view'])
                self.assertEqual(kind,'night')
                self.assertTrue(all(len(s['enum'])==1 for s in schema['properties'].values()))

    def test_summary_does_not_mix_forced_actions_into_evidence_accuracy(self):
        base={'mode':'after','seconds':1,'input_tokens':10,'output_tokens':5,'attempts':1,
              'fallback':False,'out_of_scope_citations':0,'usage_unknown_calls':0}
        report=summarize([{**base,'group':'evidence','correct':False},{**base,'group':'forced','correct':True}])
        self.assertEqual(report['evidence']['after']['correct'],0)
        self.assertEqual(report['forced']['after']['correct'],1)
