import unittest
from unittest.mock import patch
from game.play import human_turn
from game.engine import Game, RuleError
from game.ai import take_turn, action_spec, validate


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.views = []

    def generate(self, view, schema, feedback):
        self.views.append(view)
        return next(self.responses)


class AITests(unittest.TestCase):
    def setUp(self):
        self.g = Game('seven', {str(i): 'ai' for i in range(7)}, seed=3)
        self.wolves = [p.id for p in self.g.players.values() if p.role == 'wolf']
        self.citizens = [p.id for p in self.g.players.values() if p.role == 'citizen']

    def next_night(self):
        self.g.advance(timeout=True)
        self.g.advance()
        self.g.advance(timeout=True)

        self.g.advance(timeout=True)  # council -> night

    def attack(self, targets):
        for actor, target in zip(self.wolves, targets):
            self.g.submit(actor, actor, self.g.version, 'night', location='광장', target=target)
        self.g.advance(timeout=True)

    def test_allies_private_and_first_night_safe(self):
        for wolf in self.wolves:
            self.assertEqual(len(self.g.observation(wolf)['self']['allies']), 1)
        self.assertNotIn('allies', self.g.observation(self.citizens[0])['self'])
        self.g.advance(timeout=True)
        self.assertEqual(len(self.g.living()), 7)

    def test_consensus_kills_only_one(self):
        self.next_night()
        self.attack([self.citizens[0]] * 2)
        self.assertEqual(len(self.g.living()), 6)

    def test_disagreement_kills_nobody(self):
        self.next_night()
        self.attack(self.citizens[:2])
        self.assertEqual(len(self.g.living()), 7)

    def test_friendly_fire_rejected(self):
        self.next_night()
        with self.assertRaises(RuleError):
            self.g.submit(self.wolves[0], 'x', self.g.version, 'night', location='광장', target=self.wolves[1])

    def test_retry_then_valid_and_filtered_input(self):
        citizen = self.citizens[0]
        client = FakeClient([{'target': 'missing'}, {'target': None, 'location': '광장'}])
        result = take_turn(self.g, citizen, client)
        self.assertEqual(result, {'attempts': 2, 'fallback': False})
        self.assertNotIn('roles', client.views[0])
        self.assertTrue(all('role' not in p for p in client.views[0]['players']))

    def test_two_failures_default_action(self):
        result = take_turn(self.g, self.citizens[0], FakeClient([{}, {}]))
        self.assertTrue(result['fallback'])
        self.assertEqual(self.g.actions[self.citizens[0]], {'target': None, 'location': '광장'})

    def test_human_input_retries_invalid_location(self):
        citizen = self.citizens[0]
        with patch('builtins.input', side_effect=['없는장소', '창고']), patch('builtins.print'):
            human_turn(self.g, citizen)
        self.assertEqual(self.g.actions[citizen], {'target': None, 'location': '창고'})

    def test_schema_rejects_unexpected_types(self):
        _, schema = action_spec(self.g.observation(self.citizens[0]))
        with self.assertRaises(ValueError):
            validate({'target': [], 'location': '광장'}, schema)


if __name__ == '__main__':
    unittest.main()
