"""서버 내부 게임 엔진. 외부 입력의 인증과 직렬화는 API 계층의 책임이다."""
from collections import Counter
from dataclasses import dataclass
import random


class RuleError(ValueError):
    pass


@dataclass
class Player:
    id: str
    controller: str
    role: str
    alive: bool = True


class Game:
    LOCATIONS = ("광장", "창고", "숲길")

    def __init__(self, room_id, controllers, *, seed=None, max_days=8):
        if len(controllers) not in (5, 7) or any(c not in ("human", "ai") for c in controllers.values()):
            raise RuleError("참가자는 human/ai로 구성된 5명 또는 7명이어야 합니다")
        if max_days < 1:
            raise RuleError("최대 일수는 1 이상이어야 합니다")
        self.room_id = room_id
        self.rng = random.Random(seed)
        wolves = 1 if len(controllers) == 5 else 2
        specials = ["seer", "doctor"] if len(controllers) == 7 else ["seer"]
        roles = ["wolf"] * wolves + specials + ["citizen"] * (len(controllers) - wolves - len(specials))
        self.rng.shuffle(roles)
        self.players = {pid: Player(pid, c, role) for (pid, c), role in zip(controllers.items(), roles)}
        self.phase, self.day, self.version = "night", 1, 0
        self.max_days, self.winner = max_days, None
        self.actions, self.votes, self.speeches = {}, {}, {}
        self.events, self.commands = [], {}
        self.council = {}
        self.last_protected = {}
        self.pending_question = None
        self._emit("시작", {"day": self.day})

    def _emit(self, kind, data, recipient=None):
        self.events.append({"sequence": len(self.events) + 1, "type": kind,
                            "data": data, "recipient": recipient})

    def living(self):
        return [p.id for p in self.players.values() if p.alive]

    def submit(self, actor, command_id, expected_version, kind, **payload):
        # API가 인증된 세션에서 actor를 정해야 한다. 클라이언트가 임의 선택하면 안 된다.
        key = (actor, command_id)
        fingerprint = (kind, payload)
        if key in self.commands:
            previous, result = self.commands[key]
            if previous != fingerprint:
                raise RuleError("같은 명령 ID를 다른 내용으로 재사용할 수 없습니다")
            return result
        if expected_version != self.version:
            raise RuleError("게임 상태가 변경되었습니다. 최신 상태로 다시 시도하세요")
        p = self.players.get(actor)
        if not p or not p.alive or self.phase == "finished":
            raise RuleError("행동할 수 없는 참가자입니다")
        if kind == "night" and self.phase == "night":
            if set(payload) != {"location", "target"}:
                raise RuleError("location과 target이 필요합니다")
            location, target = payload["location"], payload["target"]
            if actor in self.actions or location not in self.LOCATIONS:
                raise RuleError("중복 행동 또는 잘못된 장소입니다")
            if target is not None:
                if not isinstance(target, str) or target not in self.living() or (target == actor and p.role != "doctor"):
                    raise RuleError("잘못된 대상입니다")
                if p.role == "doctor" and self.last_protected.get(actor) == target:
                    raise RuleError("같은 대상을 연속으로 보호할 수 없습니다")
                if p.role == "citizen" or (p.role == "wolf" and self.day == 1):
                    raise RuleError("현재 사용할 수 없는 능력입니다")
                if p.role == "wolf" and self.players[target].role == "wolf":
                    raise RuleError("동료 늑대를 공격할 수 없습니다")
            self.actions[actor] = {"location": location, "target": target}
        elif kind == "council" and self.phase == "council":
            if p.role != "wolf" or actor in self.council:
                raise RuleError("협의에 참여할 수 없습니다")
            if set(payload) != {"text", "target"} or not isinstance(payload["text"], str) or not 1 <= len(payload["text"].strip()) <= 500:
                raise RuleError("협의 발언은 1~500자입니다")
            target = payload["target"]
            if target is not None and (target not in self.living() or self.players[target].role == "wolf"):
                raise RuleError("잘못된 공격 제안입니다")
            self.council[actor] = dict(payload)
            recipients = [q.id for q in self.players.values() if q.alive and q.role == "wolf"]
            self._emit("비밀 협의", {"actor": actor, **payload, "day": self.day}, recipients)
        elif kind == "question" and self.phase == "discussion":
            if set(payload) != {"text", "target", "evidence_ids"}:
                raise RuleError("질문, 대상, 인용 기록이 필요합니다")
            text, target, ids = payload["text"], payload["target"], payload["evidence_ids"]
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
                raise RuleError("질문은 1~500자입니다")
            if not isinstance(target, str) or target == actor or target not in self.living() or self.players[target].controller != "ai":
                raise RuleError("생존한 다른 AI에게 질문하세요")
            if self.pending_question or self.speeches.get(actor, 0) >= 2 or self.speeches.get(target, 0) >= 2:
                raise RuleError("질문 또는 답변 차례가 남아 있지 않습니다")
            public = {e["sequence"] for e in self.events if e["recipient"] is None}
            if not isinstance(ids, list) or len(ids) > 3 or any(type(i) is not int or i not in public for i in ids) or len(ids) != len(set(ids)):
                raise RuleError("공개 기록을 최대 3개 인용하세요")
            self.speeches[actor] = self.speeches.get(actor, 0) + 1
            self._emit("질문", {"actor": actor, "target": target, "text": text.strip(), "evidence_ids": ids, "day": self.day})
            self.pending_question = dict(self.events[-1]["data"], sequence=self.events[-1]["sequence"])
        elif kind == "speak" and self.phase == "discussion":
            if set(payload) != {"text"} or not isinstance(payload["text"], str):
                raise RuleError("발언 문자열이 필요합니다")
            text = payload["text"].strip()
            if not text or len(text) > 500 or self.speeches.get(actor, 0) >= 2:
                raise RuleError("발언은 1~500자, 하루 최대 2회입니다")
            question = self.pending_question
            if question and question["target"] != actor:
                raise RuleError("지목된 AI의 답변을 기다려 주세요")
            self.speeches[actor] = self.speeches.get(actor, 0) + 1
            self._emit("답변" if question else "발언", {"actor": actor, "text": text, "day": self.day,
                       **({"question_id": question["sequence"], "evidence_ids": []} if question else {})})
            if question: self.pending_question = None
        elif kind == "vote" and self.phase == "vote":
            if set(payload) != {"target"}:
                raise RuleError("target이 필요합니다")
            target = payload["target"]
            if actor in self.votes or (target is not None and (target not in self.living() or target == actor)):
                raise RuleError("중복 투표 또는 잘못된 대상입니다")
            self.votes[actor] = target
        else:
            raise RuleError("현재 단계에서 허용되지 않는 명령입니다")
        self.version += 1
        self.commands[key] = (fingerprint, self.version)
        return self.version

    def advance(self, *, timeout=False):
        """서버 스케줄러 전용. 공개 플레이어 명령으로 노출하지 않는다."""
        if self.phase == "finished":
            raise RuleError("종료된 게임입니다")
        if self.phase == "council":
            wolves = [p.id for p in self.players.values() if p.alive and p.role == "wolf"]
            if not timeout and any(pid not in self.council for pid in wolves):
                raise RuleError("협의가 아직 완료되지 않았습니다")
            self.phase = "night"
        elif self.phase == "night":
            if not timeout and len(self.actions) != len(self.living()):
                raise RuleError("밤 행동이 아직 완료되지 않았습니다")
            for pid in self.living():
                self.actions.setdefault(pid, {"location": "광장", "target": None})
            private = []
            for pid, action in self.actions.items():
                others = [q for q, a in self.actions.items() if q != pid and a["location"] == action["location"]]
                if others:
                    private.append((pid, "목격", {"player": self.rng.choice(others), "location": action["location"], "day": self.day}))
                if self.players[pid].role == "seer" and action["target"]:
                    target = action["target"]
                    private.append((pid, "조사", {"target": target, "team": "wolf" if self.players[target].role == "wolf" else "village", "day": self.day}))
            # 살아 있는 늑대 전원이 같은 대상을 선택해야 한 명만 공격한다.
            targets = [a["target"] for pid, a in self.actions.items() if self.players[pid].role == "wolf"]
            victim = targets[0] if targets and targets[0] is not None and len(set(targets)) == 1 else None
            protected = set()
            for pid, action in self.actions.items():
                if self.players[pid].role == "doctor":
                    target = action["target"]
                    self.last_protected[pid] = target
                    if target is not None:
                        protected.add(target)
                        private.append((pid, "보호 선택", {"target": target, "day": self.day}))
            if victim in protected:
                victim = None
            if victim is not None:
                self.players[victim].alive = False
            for pid, kind, data in private:
                if self.players[pid].alive:
                    self._emit(kind, data, pid)
            self._emit("밤 결과", {"victim": victim, "day": self.day})
            self.actions = {}
            self.phase = "discussion"
            self._check_winner()
        elif self.phase == "discussion":
            if self.pending_question: raise RuleError("답변이 아직 완료되지 않았습니다")
            self.phase = "vote"
            self._emit("투표 시작", {"day": self.day})
        else:
            if not timeout and len(self.votes) != len(self.living()):
                raise RuleError("투표가 아직 완료되지 않았습니다")
            for pid in self.living():
                self.votes.setdefault(pid, None)
            counts = Counter(self.votes.values())
            top = max(counts.values())
            leaders = [pid for pid, count in counts.items() if count == top]
            expelled = leaders[0] if len(leaders) == 1 else None
            if expelled is not None:
                self.players[expelled].alive = False
            self._emit("투표 결과", {"votes": dict(self.votes), "expelled": expelled, "day": self.day})
            self._check_winner()
            if self.phase != "finished":
                if self.day >= self.max_days:
                    self._finish("draw")
                else:
                    self.day += 1
                    self.phase = "council"
                    self.council = {}
                    self.votes, self.speeches = {}, {}
                    self._emit("밤 시작", {"day": self.day})
        self.version += 1

    def _check_winner(self):
        wolves = sum(p.alive and p.role == "wolf" for p in self.players.values())
        others = len(self.living()) - wolves
        if not wolves:
            self._finish("village")
        elif wolves >= others:
            self._finish("wolf")

    def _finish(self, winner):
        self.winner, self.phase = winner, "finished"
        self._emit("게임 종료", {"winner": winner})

    def observation(self, viewer):
        # 호출자의 인증은 추후 API 계층에서 처리. 반환값에는 전체 상태를 넣지 않는다.
        import copy
        if viewer not in self.players:
            raise RuleError("방 참가자가 아닙니다")
        result = {"room_id": self.room_id, "phase": self.phase, "day": self.day,
                  "version": self.version, "winner": self.winner,
                  "self": {"id": viewer, "role": self.players[viewer].role},
                  "players": [{"id": p.id, "alive": p.alive, "controller": p.controller} for p in self.players.values()],
                  "events": [e for e in self.events if e["recipient"] in (None, viewer) or (isinstance(e["recipient"], list) and viewer in e["recipient"]) or self.phase == "finished"]}
        if self.pending_question and self.pending_question["target"] == viewer:
            result["question"] = self.pending_question
        if self.players[viewer].role == "doctor":
            result["self"]["last_protected"] = self.last_protected.get(viewer)
        if self.players[viewer].role == "wolf":
            result["self"]["allies"] = [p.id for p in self.players.values() if p.role == "wolf" and p.id != viewer]
        if self.phase == "finished":
            result["roles"] = {p.id: p.role for p in self.players.values()}
        return copy.deepcopy(result)

    def snapshot(self):
        import copy
        state = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ("rng", "players", "commands")})
        state["players"] = {k: vars(v).copy() for k, v in self.players.items()}
        state["commands"] = [[list(k), v] for k, v in self.commands.items()]
        state["random_state"] = self.rng.getstate()
        return state

    @classmethod
    def restore(cls, state):
        import copy
        data = copy.deepcopy(state)
        def tuples(value):
            return tuple(tuples(v) for v in value) if isinstance(value, (list, tuple)) else value
        game = cls.__new__(cls)
        random_state = data.pop("random_state")
        game.__dict__.update(data)
        game.last_protected = data.get("last_protected", {})
        game.pending_question = data.get("pending_question")
        game.players = {k: Player(**v) for k, v in data["players"].items()}
        game.commands = {tuple(k): ((v[0][0], v[0][1]), v[1]) for k, v in data["commands"]}
        game.rng = random.Random()
        game.rng.setstate(tuples(random_state))
        return game
