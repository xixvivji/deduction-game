"""LLM이 아닌 규칙 기반 봇으로 엔진 전체 진행을 확인한다."""
from .engine import Game


def main():
    game = Game("demo", {f"p{i}": "human" if i == 0 else "ai" for i in range(5)}, seed=7)
    command = 0
    while game.phase != "finished":
        for pid in game.living():
            command += 1
            if game.phase == "council":
                if game.players[pid].role == "wolf":
                    game.submit(pid, str(command), game.version, "council", text="공격 보류", target=None)
            elif game.phase == "night":
                game.submit(pid, str(command), game.version, "night", location="광장", target=None)
            elif game.phase == "discussion":
                game.submit(pid, str(command), game.version, "speak", text="아직 확신할 증거가 없습니다.")
            else:
                game.submit(pid, str(command), game.version, "vote", target=None)
        game.advance()
        print(f"day={game.day} phase={game.phase} winner={game.winner}")
    print("규칙 기반 데모 종료 (실제 AI 호출 없음)")


if __name__ == "__main__":
    main()
