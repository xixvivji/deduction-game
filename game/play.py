"""python3 -m game.play: 사람 1명 + AI 참가자의 터미널 게임."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from .engine import Game, RuleError
from .ai import take_turn, action_spec
from .providers import create_client, ProviderError


def human_turn(game, actor):
    view = game.observation(actor)
    kind, schema = action_spec(view)
    while True:
        if kind == "speak":
            payload = {"text": input("발언 (빈칸=판단 보류): ").strip() or "판단을 보류합니다."}
        else:
            targets = schema["properties"]["target"]["enum"]
            print("선택 가능한 대상:", [t for t in targets if t is not None], "빈칸=기권/능력 미사용")
            target = (input("대상 ID: ").strip() or None) if len(targets) > 1 else None
            payload = {"target": target}
            if kind == "council":
                payload["text"] = input("늑대 동료에게 제안할 이유: ").strip() or "이 대상을 제안합니다."
            if kind == "night":
                payload["location"] = input("장소 (광장/창고/숲길, 기본 광장): ").strip() or "광장"
        try:
            game.submit(actor, str(uuid4()), game.version, kind, **payload)
            return
        except RuleError as error:
            print("다시 선택하세요:", error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=int, choices=(5, 7, 9, 11), default=7)
    parser.add_argument("--provider", choices=("ollama", "openai", "gemini"), default="ollama")
    parser.add_argument("--model", help="외부 API 사용 시 필수")
    parser.add_argument("--max-calls", type=int, default=200)
    parser.add_argument("--max-output-tokens", type=int, default=1024, help="외부 API 출력 한도")
    parser.add_argument("--base-url", help="Ollama 서버 주소")
    parser.add_argument("--seed", type=int, default=None, help="재현 실험용. 일반 플레이에서는 생략")
    parser.add_argument("--auto", action="store_true", help="모든 참가자를 실제 AI로 실행")
    parser.add_argument("--max-days", type=int, default=8)
    parser.add_argument("--metrics", type=Path, help="발언·역할을 제외한 호출 통계 JSON 저장 위치")
    args = parser.parse_args()
    try:
        client = create_client(args.provider, args.model, base_url=args.base_url, max_calls=args.max_calls, max_output_tokens=args.max_output_tokens)
        client.check()
    except (OSError, ValueError, KeyError, ProviderError) as error:
        parser.exit(1, f"모델 설정 확인 실패: {error}\n")
    game = Game(str(uuid4()), {f"p{i}": "human" if i == 0 and not args.auto else "ai" for i in range(args.players)}, seed=args.seed, max_days=args.max_days)
    human = "p0"
    seen, turns = 0, []
    if not args.auto:
        print("내 정보:", game.observation(human)["self"])
    def show():
        nonlocal seen
        for event in game.observation(human)["events"]:
            if event["sequence"] > seen:
                print(event["type"], json.dumps(event["data"], ensure_ascii=False))
                seen = event["sequence"]
    try:
        while game.phase != "finished":
            show()
            print(f"\n{game.day}일 / {game.phase} / 생존: {game.living()}")
            rounds = 2 if game.phase == "discussion" else 1
            for _ in range(rounds):
                for actor in game.living():
                    if game.phase == "council" and game.players[actor].role != "wolf":
                        continue
                    if game.players[actor].controller == "human":
                        human_turn(game, actor)
                    else:
                        print(f"{actor} 판단 중…", flush=True)
                        result = take_turn(game, actor, client)
                        turns.append(result)
                        if result["fallback"]:
                            print(f"{actor}: 응답 실패로 기본 행동 적용 ({result['error']})")
                    show()
            game.advance()
        show()
        print("최종 역할:", game.observation(human)["roles"])
    except ProviderError as error:
        print(f"\n게임 일시 중단: {error}. 저장/재개는 아직 지원하지 않습니다.")
    except (KeyboardInterrupt, EOFError):
        print("\n게임을 종료했습니다. 진행 상태 저장은 아직 지원하지 않습니다.")
    finally:
        report = {"calls": client.metrics, "turns": turns, "winner": game.winner, "phase": game.phase}
        if args.metrics:
            args.metrics.parent.mkdir(parents=True, exist_ok=True)
            args.metrics.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"모델 호출 {len(client.metrics)}회 / 기본 행동 대체 {sum(t['fallback'] for t in turns)}회")


if __name__ == "__main__":
    main()
