import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from game.engine import Game
from game.ai import action_spec, take_turn
from game.providers import CloudClient, BudgetClient, ProviderError, CallLimitExceeded, create_client, post_json


class ProvidersTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret', 'GEMINI_API_KEY': 'test-secret'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.game = Game('room', {str(i): 'ai' for i in range(7)}, seed=1)
        self.actor = next(p.id for p in self.game.players.values() if p.role == 'citizen')
        self.view = self.game.observation(self.actor)
        self.schema = action_spec(self.view)[1]
        self.answer = {'target': None, 'location': '광장'}

    def test_openai_request_response_usage(self):
        def transport(url, body, headers, timeout):
            self.assertEqual(url, 'https://api.openai.com/v1/responses')
            self.assertFalse(body['store'])
            self.assertTrue(body['text']['format']['strict'])
            self.assertEqual(body['text']['format']['schema']['properties']['target']['type'], ['string', 'null'])
            self.assertNotIn('roles', json.loads(body['input'][1]['content'])['observation'])
            self.assertEqual(headers['Authorization'], 'Bearer test-secret')
            return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(self.answer)}]}], 'usage': {'input_tokens': 12, 'output_tokens': 8, 'total_tokens': 20}}
        client = CloudClient('openai', 'test-model', transport=transport)
        self.assertEqual(client.generate(self.view, self.schema), self.answer)
        self.assertEqual(client.metrics[0]['total_tokens'], 20)
        self.assertNotIn('test-secret', json.dumps(client.metrics))

    def test_gemini_request_response_usage(self):
        def transport(url, body, headers, timeout):
            self.assertNotIn('test-secret', url)
            self.assertEqual(headers['x-goog-api-key'], 'test-secret')
            self.assertEqual(body['generationConfig']['responseFormat']['text']['mimeType'], 'APPLICATION_JSON')
            return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'thought': True, 'text': 'hidden'}, {'text': json.dumps(self.answer)}]}}], 'usageMetadata': {'promptTokenCount': 12, 'candidatesTokenCount': 8, 'thoughtsTokenCount': 2, 'totalTokenCount': 22}}
        client = CloudClient('gemini', 'test-model', transport=transport)
        self.assertEqual(client.generate(self.view, self.schema), self.answer)
        self.assertEqual(client.metrics[0]['thinking_tokens'], 2)

    def test_incomplete_refusal_and_blocking(self):
        responses = [('openai', {'status': 'incomplete', 'usage': {'output_tokens': 50}}),
                     ('openai', {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]}),
                     ('gemini', {'candidates': [{'finishReason': 'MAX_TOKENS'}]}), ('gemini', {})]
        for provider, data in responses:
            with self.subTest(provider=provider, data=data):
                client = CloudClient(provider, 'test', transport=lambda *args: data)
                with self.assertRaises(ValueError):
                    client.generate(self.view, self.schema)
                self.assertFalse(client.metrics[0]['success'])

    def test_keys_and_model_required(self):
        with patch.dict(os.environ, {}, clear=True):
            for provider in ('openai', 'gemini'):
                with self.assertRaises(ProviderError):
                    CloudClient(provider, 'test').check()
        with self.assertRaises(ValueError):
            create_client('openai')
        with self.assertRaises(ValueError):
            create_client('gemini', 'test', base_url='http://example.com')

    def test_budget_counts_failed_attempts_and_stops_retry(self):
        calls = []
        def transport(*args):
            calls.append(1)
            raise ValueError('temporary failure')
        client = BudgetClient(CloudClient('openai', 'test', transport=transport), 1)
        with self.assertRaises(CallLimitExceeded):
            take_turn(self.game, self.actor, client)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.game.actions, {})

    def test_auth_error_not_retried_or_leaked(self):
        with patch('game.providers.urlopen', side_effect=HTTPError('https://example.com', 401, 'test-secret', {}, None)) as mock:
            with self.assertRaises(ProviderError) as caught:
                take_turn(self.game, self.actor, CloudClient('openai', 'test'))
            self.assertEqual(mock.call_count, 1)
            self.assertNotIn('test-secret', str(caught.exception))

    def test_rate_limit_retried_once_then_fallback(self):
        with patch('game.providers.urlopen', side_effect=HTTPError('https://example.com', 429, 'secret', {}, None)) as mock:
            with patch('game.ai.time.sleep'):
                result = take_turn(self.game, self.actor, CloudClient('openai', 'test'))
            self.assertEqual(mock.call_count, 2)
            self.assertTrue(result['fallback'])

    def test_default_remains_ollama(self):
        client = create_client()
        self.assertEqual(client.client.model, 'qwen3:4b-instruct')


if __name__ == '__main__':
    unittest.main()
