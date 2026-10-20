import json
import unittest
from game.engine import Game, RuleError
from game.ai import action_spec


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.g=Game('doctor',{str(i):'human' for i in range(7)},seed=4)
        self.doctor=next(p.id for p in self.g.players.values() if p.role=='doctor')
        self.wolves=[p.id for p in self.g.players.values() if p.role=='wolf']
        self.citizen=next(p.id for p in self.g.players.values() if p.role=='citizen')
        self.g.day=2

    def protect(self,target):
        self.g.submit(self.doctor,'protect-'+str(self.g.day),self.g.version,'night',location='광장',target=target)

    def attack(self,target):
        for wolf in self.wolves:
            self.g.submit(wolf,'attack-'+str(self.g.day),self.g.version,'night',location='광장',target=target)
        self.g.advance(timeout=True)

    def test_protection_blocks_attack_without_public_disclosure(self):
        self.protect(self.citizen);self.attack(self.citizen)
        self.assertTrue(self.g.players[self.citizen].alive)
        public=[e for e in self.g.events if e['recipient'] is None]
        self.assertEqual(public[-1]['data'],{'victim':None,'day':2})
        self.assertFalse(any(e['type']=='보호 선택' for e in self.g.observation(self.citizen)['events']))

    def test_self_protection_and_repeat_restriction_survive_restore(self):
        self.protect(self.doctor);self.attack(self.doctor)
        self.assertTrue(self.g.players[self.doctor].alive)
        restored=Game.restore(json.loads(json.dumps(self.g.snapshot())))
        restored.phase='night';restored.day=3
        schema=action_spec(restored.observation(self.doctor))[1]
        self.assertNotIn(self.doctor,schema['properties']['target']['enum'])
        with self.assertRaises(RuleError):
            restored.submit(self.doctor,'repeat',restored.version,'night',location='광장',target=self.doctor)

    def test_wrong_target_does_not_save_doctor(self):
        self.protect(self.citizen);self.attack(self.doctor)
        self.assertFalse(self.g.players[self.doctor].alive)

    def test_skipping_resets_previous_target(self):
        self.g.last_protected[self.doctor]=self.citizen
        self.protect(None);self.g.advance(timeout=True)
        self.g.phase='night';self.g.day=3
        self.assertIn(self.citizen,action_spec(self.g.observation(self.doctor))[1]['properties']['target']['enum'])

    def test_no_doctor_in_legacy_five_player_mode(self):
        g=Game('five',{str(i):'ai' for i in range(5)})
        self.assertFalse(any(p.role=='doctor' for p in g.players.values()))

    def test_seer_still_cannot_target_self(self):
        seer=next(p.id for p in self.g.players.values() if p.role=='seer')
        with self.assertRaises(RuleError):
            self.g.submit(seer,'self',self.g.version,'night',location='광장',target=seer)
