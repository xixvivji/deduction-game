import unittest
from game.engine import Game, RuleError


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.g = Game("test", {str(i): "ai" for i in range(5)}, seed=1)
        self.wolf = next(p.id for p in self.g.players.values() if p.role == "wolf")
        self.seer = next(p.id for p in self.g.players.values() if p.role == "seer")
        self.citizen = next(p.id for p in self.g.players.values() if p.role == "citizen")
        self.n = 0

    def submit(self, pid, kind, **payload):
        self.n += 1
        return self.g.submit(pid, str(self.n), self.g.version, kind, **payload)

    def test_roles_and_privacy(self):
        view = self.g.observation(self.citizen)
        self.assertNotIn("roles", view)
        self.assertTrue(all("role" not in p for p in view["players"]))
        self.submit(self.seer, "night", location="광장", target=self.wolf)
        self.g.advance(timeout=True)
        self.assertTrue(any(e["type"] == "조사" for e in self.g.observation(self.seer)["events"]))
        self.assertFalse(any(e["type"] == "조사" for e in self.g.observation(self.citizen)["events"]))

    def test_invalid_actions_do_not_mutate(self):
        for pid in (self.wolf, self.citizen):
            target = self.seer
            with self.assertRaises(RuleError):
                self.submit(pid, "night", location="광장", target=target)
        self.assertEqual(self.g.version, 0)
        self.assertEqual(self.g.actions, {})
        with self.assertRaises(RuleError):
            self.g.advance()

    def test_idempotency_and_version(self):
        version = self.g.submit(self.citizen, "same", 0, "night", location="광장", target=None)
        self.assertEqual(self.g.submit(self.citizen, "same", 0, "night", location="광장", target=None), version)
        with self.assertRaises(RuleError):
            self.g.submit(self.citizen, "same", 0, "night", location="창고", target=None)
        with self.assertRaises(RuleError):
            self.g.submit(self.seer, "new", 0, "night", location="광장", target=None)

    def test_village_win_and_role_reveal(self):
        self.g.advance(timeout=True)
        self.g.advance()
        for pid in self.g.living():
            self.submit(pid, "vote", target=self.wolf if pid != self.wolf else None)
        self.g.advance()
        self.assertEqual(self.g.winner, "village")
        self.assertEqual(len(self.g.observation(self.citizen)["roles"]), 5)
        with self.assertRaises(RuleError):
            self.g.advance()

    def test_attack_and_dead_player(self):
        self.g.advance(timeout=True)
        self.g.advance()
        self.g.advance(timeout=True)
        self.g.advance(timeout=True)  # council -> night
        self.submit(self.wolf, "night", location="창고", target=self.citizen)
        self.g.advance(timeout=True)
        self.assertFalse(self.g.players[self.citizen].alive)
        with self.assertRaises(RuleError):
            self.submit(self.citizen, "speak", text="사망 후 발언")

    def test_draw_and_speech_limit(self):
        self.g.max_days = 1
        self.g.advance(timeout=True)
        for _ in range(2):
            self.submit(self.citizen, "speak", text="확인 중")
        with self.assertRaises(RuleError):
            self.submit(self.citizen, "speak", text="세 번째 발언")
        self.g.advance()
        self.g.advance(timeout=True)
        self.assertEqual(self.g.winner, "draw")

    def test_tied_vote_expels_nobody(self):
        self.g.advance(timeout=True)
        self.g.advance()
        for actor, target in {"0": "1", "1": "0", "2": "0", "3": "1", "4": None}.items():
            self.submit(actor, "vote", target=target)
        self.g.advance()
        self.assertEqual(len(self.g.living()), 5)
        self.assertEqual(self.g.day, 2)

    def test_wolf_wins_at_parity(self):
        self.g.advance(timeout=True)
        while self.g.phase != "finished":
            self.g.advance()  # discussion -> vote
            self.g.advance(timeout=True)  # abstain -> council
            self.g.advance(timeout=True)  # council -> night
            target = next(pid for pid in self.g.living() if pid != self.wolf)
            self.submit(self.wolf, "night", location="광장", target=target)
            self.g.advance(timeout=True)
        self.assertEqual(self.g.winner, "wolf")
        self.assertEqual(len(self.g.living()), 2)

    def test_observation_is_copy(self):
        view = self.g.observation(self.citizen)
        view["events"][0]["data"]["day"] = 99
        self.assertEqual(self.g.events[0]["data"]["day"], 1)


if __name__ == "__main__":
    unittest.main()
