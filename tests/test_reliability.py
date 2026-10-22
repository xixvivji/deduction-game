import copy
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from game.decision import decide
from game.evaluation_reports import comparison, load
from game.mcp_memory import MCPMemory, MemoryUnavailable
from game.time_budget import run_with_budget
from tools.reliability_probe import CountingMemory, ReplayClient, fixtures, owned_child


def wait_for(predicate, seconds=4):
    until = time.monotonic()+seconds
    while time.monotonic()<until:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for condition')


class ResearchGateTests(unittest.TestCase):
    def test_same_failure_stops_but_one_feedback_attempt_is_preserved(self):
        row = next(r for r in fixtures() if r['id']=='repeated_failure')
        client = ReplayClient(row['id']); memory = CountingMemory(row['id'])
        result = decide(row['view'], client, memory=memory, policy='verified', discussion_quality=False)
        self.assertEqual(client.calls, 2)
        self.assertEqual(memory.calls, 2)  # Initial search + one expanded search.
        self.assertTrue(result['fallback'])
        self.assertTrue(result['audit']['stopped_repeated_failure'])
        self.assertEqual(result['audit']['research_history'], [{'new_events':0}])

    def test_new_evidence_and_changed_claim_still_recover(self):
        for name in ('new_evidence','changed_claim','repairable'):
            with self.subTest(name=name):
                row = next(r for r in fixtures() if r['id']==name)
                result = decide(row['view'],ReplayClient(name),memory=CountingMemory(name),policy='verified',discussion_quality=False)
                self.assertFalse(result['fallback'])
                self.assertEqual(result['payload']['target'],row['expected'])
                self.assertEqual(result['attempts'],2)

    def test_newly_found_context_allows_another_citation_repair(self):
        row = next(r for r in fixtures() if r['id']=='new_evidence')
        class SlowRepair(ReplayClient):
            def generate(self,*args):
                result = super().generate(*args)
                if self.calls==2: result['evidence_ids']=[1]
                return result
        model = SlowRepair('new_evidence')
        result = decide(row['view'],model,memory=CountingMemory('new_evidence'),policy='verified',discussion_quality=False)
        self.assertFalse(result['fallback']);self.assertEqual(result['attempts'],3)
        self.assertEqual(result['audit']['research_rounds'],1)
        self.assertEqual(result['audit']['fact_retries'],1)

    def test_no_cross_request_cache_of_failed_claims(self):
        row = next(r for r in fixtures() if r['id']=='repeated_failure')
        for _ in range(2):
            model=ReplayClient(row['id'])
            result=decide(row['view'],model,policy='verified',discussion_quality=False)
            self.assertEqual(result['audit']['research_rounds'],1)
            self.assertEqual(model.calls,2)


class MCPRecoveryTests(unittest.TestCase):
    def test_single_reconnect_serves_concurrent_isolated_participants(self):
        memory=MCPMemory(ping_interval=.05,backoff=.01)
        try:
            view=fixtures()[0]['view'];memory.search(view)
            other=copy.deepcopy(view);other['self']['id']='p2'
            os.kill(owned_child(),signal.SIGTERM)
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(memory.search,[view,other]*4))
            for i,result in enumerate(results):
                self.assertEqual(any(e['type']=='조사' for e in result['events']),i%2==0)
            stats=memory.snapshot()
            self.assertEqual(stats['recoveries'],1)
            self.assertEqual(stats['reconnect_attempts'],1)
            self.assertTrue(stats['healthy'])
            serialized=json.dumps(stats)
            for private in (memory.secret,'capability','evidence_rules','p0'):
                self.assertNotIn(private,serialized)
        finally:memory.close()
        self.assertFalse(memory.thread.is_alive())

    def test_idle_ping_recovers_without_a_search(self):
        memory=MCPMemory(ping_interval=.05,backoff=.01)
        try:
            os.kill(owned_child(),signal.SIGTERM)
            wait_for(lambda:memory.snapshot()['recoveries']==1)
            self.assertTrue(memory.healthy)
        finally:memory.close()

    def test_failed_reconnects_enter_cooldown_and_close_stops_retries(self):
        memory=MCPMemory(ping_interval=.05,backoff=.01,cooldown=2)
        try:
            with patch('game.mcp_memory.stdio_client',side_effect=OSError('PRIVATE_FAILURE')):
                os.kill(owned_child(),signal.SIGTERM)
                wait_for(lambda:memory.snapshot()['state']=='cooldown')
                attempts=memory.snapshot()['reconnect_attempts']
                self.assertEqual(attempts,2)
                with self.assertRaises(MemoryUnavailable):memory.search(fixtures()[0]['view'])
                time.sleep(.1)
                self.assertEqual(memory.snapshot()['reconnect_attempts'],attempts)
                memory.close()
                self.assertFalse(memory.thread.is_alive())
        finally:memory.close()

    def test_expired_request_is_not_delivered_after_recovery(self):
        memory=MCPMemory(ping_interval=.05,backoff=.3)
        try:
            os.kill(owned_child(),signal.SIGTERM)
            wait_for(lambda:memory.snapshot()['state']=='reconnecting')
            with self.assertRaises(MemoryUnavailable):
                run_with_budget(.05,memory.search,fixtures()[0]['view'])
            wait_for(lambda:memory.snapshot()['recoveries']==1)
            self.assertEqual(memory.search(fixtures()[0]['view'])['self']['id'],'p0')
        finally:memory.close()

    def test_tool_rejection_does_not_restart_process(self):
        memory=MCPMemory()
        try:
            with patch('game.mcp_memory.signature',return_value='invalid'):
                with self.assertRaises(MemoryUnavailable):memory.search(fixtures()[0]['view'])
            self.assertEqual(memory.snapshot()['reconnect_attempts'],0)
            self.assertEqual(memory.snapshot()['tool_errors'],1)
            self.assertTrue(memory.healthy)
        finally:memory.close()


class EvaluationReportTests(unittest.TestCase):
    def test_missing_reports_are_not_zero_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            result=load(Path(directory))
        self.assertEqual(result['scripted']['status'],'not_measured')
        self.assertNotIn('before',result['scripted'])

    def test_mismatched_inputs_cannot_be_compared(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'reports').mkdir()
            for mode in ('before','after'):
                (root/'reports'/f'reliability-{mode}-v1.json').write_text(json.dumps({'decisions':{'fixture_sha256':mode,'config':{}}}))
            self.assertEqual(comparison(root,'reliability')['status'],'incomparable')

    def test_report_api_does_not_return_raw_fixture_or_answers(self):
        root=Path(__file__).resolve().parents[1]
        result=load(root)
        self.assertEqual(result['scripted']['status'],'measured')
        encoded=json.dumps(result)
        for private in ('decision_summary','evidence_ids','claim_subject','recipient','capability','roles'):
            self.assertNotIn(private,encoded)
