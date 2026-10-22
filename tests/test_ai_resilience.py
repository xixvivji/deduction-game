import unittest
from unittest.mock import Mock, patch
from game.time_budget import run_with_budget, remaining
from game.providers import ProviderError, TransientProviderError, RoutedClient, BudgetClient, CallLimitExceeded
from game.circuit_breaker import CircuitTransport


class ResilienceTests(unittest.TestCase):
    def test_deadline_rejects_late_results_and_resets(self):
        clock=[0.]
        with patch('game.time_budget.time.monotonic',side_effect=lambda:clock[0]):
            def work():
                self.assertEqual(remaining(60),2)
                clock[0]=3
                return 'late'
            with self.assertRaises(ProviderError):run_with_budget(2,work)
            self.assertEqual(remaining(60),60)
            with self.assertRaises(ValueError):run_with_budget(float('nan'),lambda:None)

    def test_breaker_blocks_then_allows_one_probe(self):
        clock=[0];transport=Mock(side_effect=TransientProviderError('temporary'))
        breaker=CircuitTransport(transport,clock=lambda:clock[0])
        for _ in range(3):
            with self.assertRaises(TransientProviderError):breaker('url',{}, {},1)
        with self.assertRaises(ProviderError):breaker('url',{}, {},1)
        self.assertEqual(transport.call_count,3)
        clock[0]=31
        def probe(*args):
            with self.assertRaises(ProviderError):breaker('url',{}, {},1)
            return {'ok':True}
        transport.side_effect=probe
        self.assertEqual(breaker('url',{}, {},1),{'ok':True})
        transport.side_effect=None;transport.return_value={}
        self.assertEqual(breaker('url',{}, {},1),{})

    def test_auth_error_does_not_open_breaker(self):
        transport=Mock(side_effect=ProviderError('auth'));breaker=CircuitTransport(transport)
        for _ in range(4):
            with self.assertRaises(ProviderError):breaker('url',{}, {},1)
        self.assertEqual(transport.call_count,4)
        self.assertEqual(breaker.states['url']['until'],0)

    def test_routes_share_budget_but_preserve_model_metrics(self):
        class Client:
            provider='test';model='default'
            def __init__(self,name):self.model=name;self.metrics=[]
            def check(self):pass
            def generate(self,*args):self.metrics.append({'model':self.model});return self.model
        route=RoutedClient(Client('default'),Client('fast'),Client('quality'))
        budget=BudgetClient(route,3)
        self.assertEqual(budget.generate({'phase':'night'},{}),'fast')
        self.assertEqual(budget.generate({'phase':'discussion'},{}),'quality')
        self.assertEqual(budget.generate({'phase':'vote'},{},'repair'),'quality')
        with self.assertRaises(CallLimitExceeded):budget.generate({'phase':'vote'}, {})
        self.assertEqual([m['model_route'] for m in budget.metrics],['fast','quality','quality'])
        self.assertEqual(route.fast.metrics,[])
