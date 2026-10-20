# 밤의 증언 — AI 사회적 추리 게임

사람과 AI가 시민·조사자·늑대가 되어 목격 정보를 모으고, 토론과 투표로 상대 진영을 찾아내는 턴제 게임입니다. GuardLab과 별개 프로젝트입니다.

## 구현된 기능

- 7인 기본(늑대 2 / 조사자 1 / 시민 4), 5인 선택 가능
- Vue 브라우저 대기실, 방 코드 참가, 빈자리 AI 배정
- 사람 여러 명 + AI 혼합 플레이, 개인 역할·목격·조사 정보 분리
- 낮 토론 2회 → 투표 → 밤 비밀 협의 → 밤 행동 → 결과 처리
- 늑대 전용 공격 제안, 합의에 따른 단일 공격
- Ollama / OpenAI / Gemini 제공자 교체, 호출 한도 및 실패 처리
- LangGraph: 개인 관측 → 기억 검색 → 생성·검증 → 1회 재시도 또는 기본 행동
- 개인 관측 안에서만 수행하는 어휘 기반 관련 기록 검색(RAG), 전체 기록 기준선
- WebSocket 상태 동기화, 동일 브라우저 탭의 새로고침·네트워크 재연결
- SQLite 게임 저장, 서버 재시작 시 복원 후 방장 재개
- 종료 후 전체 역할·개인 기록 공개 및 JSON 보고서 다운로드
- 고정 합성 상황으로 모델/기억 방식 비교 평가

## 설치

Python 3.10 이상과 Node/npm이 필요합니다. 아래 명령은 저장소 루트에서 실행합니다. 개발 검증은 Python 3.14에서 수행했습니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --prefix frontend
```

Vue는 로컬 설치 파일을 제공하므로 실행 중 외부 CDN을 요청하지 않습니다. 생성된 `.venv`, `node_modules`, `runtime`, 환경파일은 Git에서 제외됩니다.

## 웹 실행

Ollama를 별도 터미널에서 시작한 뒤 설치된 모델을 지정합니다. 자동 모델 다운로드는 하지 않습니다.

```sh
ollama serve
```

다른 터미널:

```sh
GAME_MODEL=qwen3:4b-instruct ./run_web.sh
```

브라우저에서 **http://127.0.0.1:8200** 에 접속합니다.

1. 이름을 입력하고 방을 만듭니다.
2. 친구가 같은 서버에 접속해 이름과 방 코드를 입력합니다.
3. 방장이 시작하면 빈자리를 AI로 채우고 역할을 배정합니다.
4. 자신의 차례에 장소·능력·발언·투표를 확정합니다. 기본 입력 제한은 90초이며, 미입력은 기권/기본 행동 처리됩니다.
5. 새로고침해도 같은 탭에서는 참가자 세션을 복구합니다. 접속 정보 닫기 또는 탭 저장소 삭제 시 기존 참가자 세션으로 복귀할 수 없습니다.

같은 컴퓨터에서는 새 탭 두 개로 두 참가자를 시험할 수 있습니다. 탭 복제는 세션 정보가 복제될 수 있으므로 새 URL 탭을 여세요. 같은 네트워크의 다른 기기를 연결할 때는 `GAME_HOST=0.0.0.0 ./run_web.sh`로 시작하고 서버 컴퓨터의 LAN 주소로 접속합니다. 인터넷 공개 배포는 하지 않았습니다.

규칙/화면만 빠르게 확인하는 **AI 없는 모드**:

```sh
GAME_PROVIDER=demo GAME_DB=/tmp/night-demo.db ./run_web.sh
```

화면에 규칙 봇 테스트라고 표시됩니다. 실제 모델 성능과 구분하세요.

## 모델과 호출 제한

서버는 기본 `ollama`, 기본 모델 `qwen3:4b-instruct`입니다.

- `GAME_PROVIDER`: ollama / openai / gemini / demo
- `GAME_MODEL`: 모델 ID. 외부 API는 명시적으로 설정해야 합니다.
- `GAME_MAX_CALLS`: 방당 생성 시도 한도, 기본 300회. 재시도 포함이며 금액 한도가 아닙니다.
- `GAME_DB`: SQLite 파일 경로, 기본 `runtime/games.db`
- `GAME_HOST`, `GAME_PORT`: 기본 127.0.0.1, 8200

외부 API는 서버 환경변수 `OPENAI_API_KEY` 또는 `GEMINI_API_KEY`를 설정하세요. 키는 프런트엔드나 Git에 넣지 않습니다. `.env` 자동 로딩은 하지 않습니다. 외부 모델을 선택하면 허용된 개인 게임 기록이 해당 제공자에게 전송됩니다.

```sh
GAME_PROVIDER=openai GAME_MODEL=YOUR_MODEL_ID ./run_web.sh
GAME_PROVIDER=gemini GAME_MODEL=YOUR_MODEL_ID ./run_web.sh
```

외부 API 어댑터는 모의 응답으로 검증했습니다. 실제 계정의 모델 접근·구조화 출력 호환성과 응답 품질은 아직 실호출 검증하지 않았습니다. OpenAI는 Responses API의 `store=false`, Gemini는 generateContent API를 사용합니다. `store=false`가 모든 제공자 보존 정책을 없애는 것은 아닙니다.

입력/출력/총 토큰은 제공자가 반환한 값으로 기록하고 누락값은 null로 유지합니다. Gemini 사고 토큰은 별도 필드입니다. 비용을 임의의 단가로 계산하지 않습니다. 모델 오류는 최대 1회 재시도하며, 인증/설정/호출 한도 오류는 방을 일시 중단합니다. 한도에 걸린 게임은 서버의 한도 설정을 높인 후 재시작·재개할 수 있습니다.

## 규칙

첫 밤에는 공격하지 않습니다. 목격은 같은 장소에 있던 다른 생존자 한 명에 대한 사실이며 범행 여부나 전체 동선을 증명하지 않습니다. 조사자는 대상의 진영을 확인합니다.

두 번째 밤부터 살아 있는 늑대만 한 번씩 비밀 제안을 합니다. 이후 늑대 전원이 같은 비동료 대상을 선택해야 한 명만 공격받습니다. 불일치/기권이면 공격하지 않으며, 한 늑대만 살아 있으면 혼자 결정합니다. 시민은 비밀 협의 기록이나 발언자 ID를 받지 않습니다. 사망자는 이후 행동하지 못합니다.

투표 최다 득표 동률 또는 기권 최다이면 추방하지 않습니다. 늑대가 모두 제거되면 시민 승리, 살아 있는 늑대 수가 나머지 인원 이상이면 늑대 승리입니다. 최대 8일차 이후에는 무승부입니다.

## 구조

- `game/engine.py`: 규칙·정보 분리·명령 중복 방지·직렬화
- `game/ai.py`, `game/providers.py`: 공통 프롬프트·제공자·호출 예산
- `game/decision.py`: LangGraph 판단 흐름
- `game/memory.py`: 권한이 적용된 기록 검색, 조사 결과와 현재 협의 고정 보존
- `game/server.py`: 방별 lock·진행 작업·SQLite·REST/WebSocket
- `frontend/`: Vue 화면
- `game/evaluate.py`: 고정 상황 평가
- `tests/`: 규칙·제공자·정보 분리·다중 참가자·재개 테스트

AI 호출은 전체 서버에서 최대 1건씩 실행하고 이벤트 루프 밖에서 처리합니다. 각 방은 독립 상태를 가지며 인간 명령은 방 lock 아래에서 처리됩니다. 네트워크 멀티는 단일 서버 프로세스 기준입니다. **uvicorn workers를 2 이상으로 실행하지 마세요.** 여러 서버를 지원하려면 공유 상태 저장과 분산 lock이 필요합니다.

## 테스트와 평가

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m game.demo
.venv/bin/python -m game.play --players 7 --model qwen3:4b-instruct
.venv/bin/python -m game.evaluate --model qwen3:4b-instruct --model qwen3:8b --repeats 3 --output reports/comparison.json
```

CLI는 이전 순차 기준선이고, 웹의 AI는 LangGraph/기억 검색을 사용합니다. 평가 도구는 웹과 동일한 그래프를 사용합니다. `--dry-run`은 모델 호출 없이 기록 선택량만 확인합니다.

평가는 합성 조사 결과로부터 투표 대상을 고르는 4개 고정 상황입니다. 전체 기록과 검색 기록에 같은 질문·상태를 사용하며 정답은 모델 입력 밖에서 채점합니다. 모델 최초 적재 시간도 포함합니다. 소수 사례 결과를 전체 추리력·승률·사람 수준 설득력으로 일반화하지 않습니다.

LangGraph 추적은 선택적으로 `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`를 설정해 활성화할 수 있습니다. 추적에는 개인 역할과 대화가 포함될 수 있으며 기본으로 활성화하지 않습니다. 실제 LangSmith 계정 연결은 아직 검증하지 않았습니다.

## 남아 있는 한계

모델의 자발적 역할 공개와 잘못된 추리를 완전히 막지 못합니다. 검색은 벡터 DB가 아닌 어휘 기반이며 의미상 관련된 기록을 놓칠 수 있습니다. 사람 입력은 순차 차례제로 처리되고 음성/실시간 이동은 없습니다. AI 발언은 완성 후 표시하며 토큰 단위 스트리밍은 없습니다.

게임 저장은 각 행동 완료 시 이루어집니다. 추론 중 서버가 강제 종료되면 해당 모델 요청이 재개 시 다시 발생할 수 있습니다. 브라우저 세션은 탭 단위이며 계정 로그인/기기 간 세션 이전은 없습니다. LAN 및 로컬 개발용이며 공개 서비스의 계정·초대 권한·방 정리·남용 방지·TLS 운영은 별도 작업입니다. MCP 도구 서버와 벡터 임베딩은 현재 구현 범위에 포함하지 않았습니다.

## Git 관리

Git Flow (`main`, `develop`, `feature/*`, `release/*`, `hotfix/*`) 및 타입 + 한글 커밋 메시지. 저장소: https://github.com/xixvivji/deduction-game

## API 참고

- https://developers.openai.com/api/docs/guides/structured-outputs
- https://ai.google.dev/gemini-api/docs/generate-content/structured-output
