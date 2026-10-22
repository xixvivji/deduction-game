"""단일 서버 프로세스용 방 관리. 모델 I/O는 스레드로 넘기고 상태 변경은 방 lock 안에서만 수행."""
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt, ConfigDict
from .engine import Game, RuleError
from . import exploration
from .ai import action_spec
from .decision import decide
from .memory_backend import LocalMemory
from .mcp_memory import MCPMemory, MemoryUnavailable
from .providers import create_client, ProviderError, PooledTransport
from .inference_pool import InferencePool, run_blocking, create_blocking_resource
from .time_budget import run_with_budget
from .circuit_breaker import CircuitTransport
from .runtime_settings import RuntimeSettings
from .observability import Monitor
from .mlflow_tracking import RuntimeExporter
from .operations import install as install_operations

ROOT = Path(__file__).resolve().parent.parent


def public_citations(view, ids):
    public_ids = {e["sequence"] for e in view["events"] if e["recipient"] is None}
    return [i for i in ids if i in public_ids]


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class DemoClient:
    """화면/규칙 테스트용 봇. 실제 AI가 아님을 화면에 표시한다."""
    metrics = []
    def __init__(self): self.metrics = []
    def check(self): pass
    def generate(self, view, schema, feedback=None):
        payload = self._action(view, schema)
        if "decision_summary" in schema["properties"]:
            payload.update(decision_summary="규칙 봇의 기본 선택입니다.", evidence_ids=[])
        if "claim_type" in schema["properties"]:payload.update(claim_type="none",claim_subject=None,claim_value="")
        return payload

    def _action(self, view, schema):
        kind = action_spec(view)[0]
        if kind == "speak": return {"text": "[규칙 봇] 질문을 받았습니다. 실제 기억 기반 답변은 AI 모드에서 제공합니다." if view.get("question") else "[규칙 봇] 아직 판단할 근거가 부족합니다."}
        payload = {"target": None}
        if kind == "night": payload["location"] = view["self"].get("explored_location", "광장")
        if kind == "council":
            targets = schema["properties"]["target"]["enum"]
            payload.update(target=next((t for t in targets if t), None), text="이 대상을 함께 검토합시다.")
        if kind == "night" and view["self"]["role"] == "wolf" and view["day"] > 1 and not view.get("realtime"):
            payload["target"] = next((e["data"]["target"] for e in reversed(view["events"]) if e["type"] == "비밀 협의" and e["data"].get("target")), None)
        return payload


class Room:
    def __init__(self, data):
        self.data = data
        self.game = Game.restore(data["game"]) if data.get("game") else None
        self.lock = asyncio.Lock()
        self.task = None
        self.client = None
        self.busy = False
        self.intents = {}
        self.move_sequences = {}
        self.last_step = time.monotonic()
        self.last_saved_step = 0.
        self.last_access = time.monotonic()
        self.connections = 0


class Hub:
    def __init__(self, db_path, client_factory=None, turn_seconds=90, memory=None, transport=None, settings=None):
        self.settings = settings or RuntimeSettings.from_env()
        self.memory=memory or LocalMemory()
        self.transport=transport
        self.db = sqlite3.connect(db_path)
        try:
            self.db.execute("CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            self.rooms = {key: Room(json.loads(data)) for key,data in self.db.execute("SELECT id,data FROM rooms WHERE json_extract(data, '$.status') != 'finished'")}
        except BaseException:
            self.db.close()
            raise
        self.client_factory = client_factory or self.default_client
        self.turn_seconds = turn_seconds
        self.inference = InferencePool(concurrency=self.settings.concurrency)
        self.monitor = Monitor()

    def default_client(self):
        if os.environ.get("GAME_PROVIDER") == "demo": return DemoClient()
        return create_client(os.environ.get("GAME_PROVIDER", "ollama"), os.environ.get("GAME_MODEL"), max_calls=int(os.environ.get("GAME_MAX_CALLS", "300")), transport=self.transport, fast_model=os.environ.get("GAME_FAST_MODEL"), quality_model=os.environ.get("GAME_QUALITY_MODEL"))

    def save(self, room):
        room.data["game"] = room.game.snapshot() if room.game else None
        self.db.execute("INSERT OR REPLACE INTO rooms VALUES (?, ?)", (room.data["id"], json.dumps(room.data, ensure_ascii=False)))
        self.db.commit()

    def create(self, name, size, explore=False, realtime=False):
        self.evict_finished()
        if sum(r.data["status"] != "finished" for r in self.rooms.values()) >= 100: raise RuleError("방 한도에 도달했습니다")
        rid, token = secrets.token_hex(4), secrets.token_urlsafe(32)
        room = Room({"id":rid, "size":size, "explore_enabled":explore or realtime, "realtime":realtime, "members":{"p0":{"name":name,"token_hash":digest(token)}}, "status":"lobby", "queue":[], "index":0, "deadline":None, "error":None, "history":[], "metrics":[], "calls":0, "backend":os.environ.get("GAME_PROVIDER", "ollama"), "model":os.environ.get("GAME_MODEL", "qwen3:4b-instruct"), "memory":"retrieved"})
        self.rooms[rid] = room
        self.save(room)
        return room, token

    def room(self, rid):
        if rid not in self.rooms:
            row = self.db.execute("SELECT data FROM rooms WHERE id=?", (rid,)).fetchone()
            if row is None: raise HTTPException(404, "방을 찾을 수 없습니다")
            self.rooms[rid] = Room(json.loads(row[0]))
        self.rooms[rid].last_access = time.monotonic()
        return self.rooms[rid]

    def evict_finished(self, idle_seconds=60):
        now = time.monotonic()
        removed = 0
        for rid, room in list(self.rooms.items()):
            if (room.data["status"] == "finished" and not room.connections
                    and not room.busy and not room.lock.locked()
                    and (room.task is None or room.task.done())
                    and now - room.last_access >= idle_seconds):
                self.save(room)
                del self.rooms[rid]
                removed += 1
        return removed

    def authenticate(self, room, token):
        hashed = digest(token)
        for pid, member in room.data["members"].items():
            if secrets.compare_digest(hashed, member["token_hash"]): return pid
        raise HTTPException(401, "참가자 인증에 실패했습니다")

    def active(self, room):
        if room.data.get("exploration", {}).get("active"): return None
        q = room.data["queue"]
        return q[room.data["index"]] if room.data["index"] < len(q) else None

    def queue(self, room):
        g = room.game
        if g.phase == ("discussion" if g.realtime else "night") and room.data.get("explore_enabled") and g.exploration_day != g.day:
            room.data["exploration"] = exploration.begin(g, duration=60 if g.realtime else 30)
            room.intents.clear();room.move_sequences.clear();room.last_step=time.monotonic();room.last_saved_step=0.
        players = g.living()
        if g.phase == "council": players = [p for p in players if g.players[p].role == "wolf"]
        room.data.update(queue=players * (2 if g.phase == "discussion" else 1), index=0, deadline=time.time()+self.turn_seconds)

    def view(self, room, pid):
        g = room.game
        view = g.observation(pid) if g else {"phase":"lobby","events":[],"players":[],"self":{"id":pid}}
        actor = self.active(room)
        # 협의 중인 늑대 ID는 시민에게 숨긴다.
        visible_actor = actor
        if g and g.phase == "council" and g.players[pid].role != "wolf": visible_actor = None
        allowed = bool(g and room.data["status"] == "running" and actor == pid)
        view.update(room_id=room.data["id"], size=room.data["size"], status=room.data["status"], members={k:v["name"] for k,v in room.data["members"].items()}, active=visible_actor, can_act=allowed,
                    host=pid=="p0", backend=room.data["backend"], model=room.data["model"], error=room.data["error"], busy=room.busy if not g or g.phase != "council" or g.players[pid].role == "wolf" else False)
        # 개인 타이머도 늑대 협의 중에는 시민에게 공개하지 않는다.
        view["deadline"] = room.data["deadline"] if not g or g.phase != "council" or g.players[pid].role == "wolf" else None
        view["action"] = {"kind":action_spec(view)[0],"schema":action_spec(view)[1]} if allowed else None
        view["question_targets"] = list(dict.fromkeys(p for p in room.data["queue"][room.data["index"]+1:]
            if g and g.phase == "discussion" and p != pid and g.players[p].controller == "ai" and g.players[p].alive and g.speeches.get(p, 0) < 2)) if allowed else []
        view["explore_enabled"] = room.data.get("explore_enabled", False)
        if g and room.data.get("exploration", {}).get("active"):
            # Unreported deaths cannot be read from the global roster.
            if g.realtime:
                hidden=room.data["exploration"].get("bodies",{})
                for player in view["players"]:
                    if player["id"] in hidden and player["id"] != pid: player["alive"]=True
            view.update(phase="explore", active=None, can_act=False, action=None, deadline=None, busy=False,
                        exploration=exploration.observation(room.data["exploration"],g,pid))
        if g and g.phase == "finished":
            view["report"] = {"decisions":room.data["history"], "metrics":room.data["metrics"]}
        return view

    def collect_usage(self, room):
        if room.client is not None:
            room.data["metrics"].extend(room.client.metrics)
            room.client.metrics.clear()
            room.data["calls"] = getattr(room.client,"calls",room.data["calls"])

    def launch(self, room):
        if not room.task or room.task.done(): room.task = asyncio.create_task(self.run(room))

    async def run(self, room):
        try:
            if room.client is None:
                room.client = self.client_factory()
                if hasattr(room.client, "calls"): room.client.calls = room.data["calls"]
                room.client.check() if isinstance(room.client, DemoClient) else await run_blocking(room.client.check)
                source = getattr(room.client, "client", room.client)
                room.data["backend"] = "demo" if isinstance(source, DemoClient) else getattr(source, "provider", "ollama")
                room.data["model"] = getattr(source, "model", "규칙 봇")
            room.last_step = time.monotonic()
            while room.data["status"] == "running":
                if room.data.get("exploration", {}).get("active"):
                    async with room.lock:
                        now=time.monotonic();state=room.data["exploration"]
                        done=exploration.tick(state,room.game,now-room.last_step,room.intents,now)
                        room.last_step=now
                        if done:
                            exploration.finish(state,room.game);room.intents.clear();self.queue(room)
                        if done or state["elapsed"]-room.last_saved_step>=1:
                            self.save(room);room.last_saved_step=state["elapsed"]
                    await asyncio.sleep(.05)
                    continue
                async with room.lock:
                    g = room.game
                    if g.phase == "finished":
                        room.data["status"] = "finished"
                        self.save(room)
                        return
                    actor = self.active(room)
                    if actor is None:
                        g.advance(timeout=True)
                        self.queue(room)
                        self.save(room)
                        continue
                    if g.players[actor].controller == "human":
                        if time.time() >= room.data["deadline"]:
                            kind, _ = action_spec(g.observation(actor))
                            payload = {"text":"시간 초과로 발언을 건너뜁니다."} if kind == "speak" else {"target":None}
                            if kind == "night": payload["location"] = g.observation(actor)["self"].get("explored_location", "광장")
                            if kind == "council": payload["text"] = "시간 초과로 제안을 보류합니다."
                            g.submit(actor, str(uuid4()), g.version, kind, **payload)
                            room.data["index"] += 1
                            room.data["deadline"] = time.time()+self.turn_seconds
                            self.save(room)
                        ai_view = None
                    else:
                        ai_view = g.observation(actor)
                        room.busy = True
                if ai_view is None:
                    await asyncio.sleep(.2)
                    continue
                with self.monitor.actions.track(room.data["backend"]) as action:
                    await self.execute_ai_action(room, actor, ai_view, action)
        except asyncio.CancelledError:
            # run_blocking has drained the in-flight thread; persist its usage, but
            # never apply a cancelled action. Resume retains the consumed budget.
            async with room.lock:
                room.busy = False
                if room.data["status"] == "running":
                    room.data["status"] = "paused"
                    room.data["error"] = "서버 작업이 중단되었습니다. 방장이 재개할 수 있습니다."
                self.collect_usage(room)
                self.save(room)
            raise
        except Exception as error:
            async with room.lock:
                room.busy = False
                room.data["status"] = "paused"
                room.data["error"] = str(error) if isinstance(error, (ProviderError, MemoryUnavailable)) else "실행 오류가 발생했습니다. 서버 설정을 확인한 뒤 재개하세요."
                self.collect_usage(room)
                self.save(room)


    async def execute_ai_action(self, room, actor, ai_view, action):
        """Decide, validate with the game engine, then persist under the room lock."""
        queued=time.perf_counter()
        async with self.inference.slot():
            queue_wait=time.perf_counter()-queued
            action.enter("inference")
            outcome = await run_blocking(
                self.monitor.run, room.data["backend"], room.client, run_with_budget, self.settings.decision_seconds, decide, ai_view, room.client, room.data["memory"], self.memory, "verified",
                discussion_quality=not isinstance(room.client, DemoClient),
                previous_beliefs=room.data.get("beliefs",{}).get(actor),
                belief_memory=not isinstance(room.client, DemoClient),
                skip_forced=True,
                context_budget=self.settings.context_budget,
                suspicion_policy=self.settings.suspicion_policy,
                queue_wait=queue_wait, action_id=action.id,
            )
        outcome["audit"].setdefault("runtime",{})["queue_wait_seconds"]=queue_wait
        action.enter("commit_wait")
        async with room.lock:
            action.enter("apply")
            g = room.game
            g.submit(actor, str(uuid4()), ai_view["version"], outcome["kind"], **outcome["payload"])
            action.mark_applied()
            action.enter("bookkeeping")
            if ai_view.get("question"):
                # Only public citations are attached to the public answer, never private audit summaries.
                g.events[-1]["data"]["evidence_ids"] = public_citations(ai_view, outcome["audit"]["evidence_ids"])
            room.busy = False
            if "belief_memory" in outcome["audit"]:
                room.data.setdefault("beliefs",{})[actor]=outcome.get("belief_state",outcome["audit"]["belief_memory"])
            room.data["history"].append({"actor":actor,"phase":ai_view["phase"],"day":ai_view["day"],"attempts":outcome["attempts"],"fallback":outcome["fallback"],"action":outcome["payload"],"audit":outcome["audit"]})
            self.collect_usage(room)
            room.data["index"] += 1
            room.data["deadline"] = time.time()+self.turn_seconds
            action.enter("persist")
            self.save(room)
            action.mark_persisted()


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    size: int = Field(default=7, ge=5, le=11)
    explore: bool = False
    realtime: bool = False


class MoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dx: StrictInt = Field(ge=-1,le=1)
    dy: StrictInt = Field(ge=-1,le=1)
    day: StrictInt = Field(ge=1)
    sequence: StrictInt = Field(ge=0)


class FieldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(attack|report)$")
    target: str = Field(min_length=1,max_length=20)
    day: StrictInt = Field(ge=1)


class JoinBody(BaseModel):
    name: str = Field(min_length=1,max_length=20)


class CommandBody(BaseModel):
    command_id: str = Field(min_length=1,max_length=80)
    version: int
    kind: str
    payload: dict


def create_app(db_path=None, client_factory=None, turn_seconds=90, auto_workers=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.ready = False
        settings = RuntimeSettings.from_env()
        path = db_path or os.environ.get("GAME_DB", str(ROOT/"runtime"/"games.db"))
        if path != ":memory:": Path(path).parent.mkdir(parents=True,exist_ok=True)
        memory = transport = hub = exporter = None
        try:
            memory=await create_blocking_resource(MCPMemory) if auto_workers else LocalMemory()
            transport = CircuitTransport(PooledTransport())
            hub = Hub(path,client_factory,turn_seconds,memory,transport,settings)
            app.state.hub = hub
            if auto_workers and os.environ.get("GAME_MLFLOW_TRACKING_URI"):
                exporter = RuntimeExporter(os.environ["GAME_MLFLOW_TRACKING_URI"])
                hub.monitor.exporter = exporter
            for room in hub.rooms.values():
                if room.data["status"] == "running":
                    room.data["status"] = "paused"
                    room.data["error"] = "서버가 재시작되었습니다. 방장이 재개할 수 있습니다."
                    hub.save(room)
            async def cleanup():
                while True:
                    await asyncio.sleep(30)
                    hub.evict_finished()
            cleanup_task = asyncio.create_task(cleanup())
            app.state.ready=True
            try:
                yield
            finally:
                cleanup_task.cancel()
                await asyncio.gather(cleanup_task, return_exceptions=True)
        finally:
            app.state.ready=False
            try:
                if hub is not None:
                    tasks = [r.task for r in hub.rooms.values() if r.task and not r.task.done()]
                    for task in tasks: task.cancel()
                    try:
                        await asyncio.gather(*tasks,return_exceptions=True)
                    finally:
                        hub.db.close()
            finally:
                try:
                    if memory is not None: await asyncio.to_thread(memory.close)
                finally:
                    try:
                        if transport is not None: transport.close()
                    finally:
                        if exporter is not None: await asyncio.to_thread(exporter.close)
    app = FastAPI(lifespan=lifespan)
    install_operations(app, ROOT)

    @app.exception_handler(RuleError)
    async def rule_error(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail":str(error)},status_code=409)

    def auth(request, rid):
        hub = request.app.state.hub
        room = hub.room(rid)
        bearer = request.headers.get("authorization", "")
        pid = hub.authenticate(room,bearer.removeprefix("Bearer "))
        return hub,room,pid

    @app.get("/health/live")
    async def live():
        return {"status":"alive"}

    @app.get("/health/ready")
    async def ready(request:Request):
        hub=getattr(request.app.state,'hub',None)
        if not getattr(request.app.state,'ready',False) or hub is None or getattr(hub.memory,'closed',False):
            raise HTTPException(503,'not ready')
        try:hub.db.execute('SELECT 1').fetchone()
        except Exception:raise HTTPException(503,'not ready') from None
        return {"status":"ready"}

    @app.get("/")
    async def index(): return FileResponse(ROOT/"frontend"/"index.html")

    @app.get("/vendor/vue.js")
    async def vue(): return FileResponse(ROOT/"frontend"/"node_modules"/"vue"/"dist"/"vue.global.prod.js")

    @app.post("/api/rooms")
    async def create(body:CreateBody, request:Request):
        if body.size not in Game.SIZES: raise RuleError("5·7·9·11인 중 선택하세요")
        room, token = request.app.state.hub.create(body.name,body.size,body.explore,body.realtime)
        return {"room_id":room.data["id"],"token":token}

    @app.post("/api/rooms/{rid}/join")
    async def join(rid:str,body:JoinBody,request:Request):
        hub=request.app.state.hub;room=hub.room(rid)
        async with room.lock:
            if room.data["status"]!="lobby" or len(room.data["members"])>=room.data["size"]: raise RuleError("지금은 참가할 수 없습니다")
            pid=f"p{len(room.data['members'])}";token=secrets.token_urlsafe(32)
            room.data["members"][pid]={"name":body.name,"token_hash":digest(token)}
            hub.save(room)
        return {"room_id":rid,"token":token}

    @app.get("/api/rooms/{rid}")
    async def state(rid:str,request:Request):
        hub,room,pid=auth(request,rid)
        return hub.view(room,pid)

    @app.post("/api/rooms/{rid}/start")
    async def start(rid:str,request:Request):
        hub,room,pid=auth(request,rid)
        async with room.lock:
            if pid!="p0" or room.data["status"]!="lobby": raise RuleError("방장만 대기실에서 시작할 수 있습니다")
            room.game=Game(rid,{f"p{i}":"human" if f"p{i}" in room.data["members"] else "ai" for i in range(room.data["size"])})
            room.game.realtime=room.data.get("realtime",False)
            room.data["status"]="running";hub.queue(room);hub.save(room)
            if auto_workers: hub.launch(room)
        return hub.view(room,pid)

    @app.post("/api/rooms/{rid}/resume")
    async def resume(rid:str,request:Request):
        hub,room,pid=auth(request,rid)
        async with room.lock:
            if pid!="p0" or room.data["status"]!="paused": raise RuleError("방장만 일시 중단된 게임을 재개할 수 있습니다")
            room.data.update(status="running",error=None,deadline=time.time()+hub.turn_seconds)
            room.client=None
            room.intents.clear();room.last_step=time.monotonic()
            hub.save(room)
            if auto_workers: hub.launch(room)
        return hub.view(room,pid)

    @app.post("/api/rooms/{rid}/move")
    async def move(rid:str,body:MoveBody,request:Request):
        hub,room,pid=auth(request,rid)
        async with room.lock:
            state=room.data.get("exploration",{})
            if room.data["status"]!="running" or not state.get("active") or state["day"]!=body.day or not room.game.players[pid].alive:
                raise RuleError("지금은 이동할 수 없습니다")
            if body.sequence>room.move_sequences.get(pid,-1):
                room.move_sequences[pid]=body.sequence
                room.intents[pid]=(body.dx,body.dy,time.monotonic())
        return {"accepted":True}

    @app.post("/api/rooms/{rid}/field-action")
    async def field_action(rid:str,body:FieldBody,request:Request):
        hub,room,pid=auth(request,rid)
        async with room.lock:
            state=room.data.get("exploration",{})
            if room.data["status"]!="running" or not state.get("active") or state["day"]!=body.day:
                raise RuleError("현재 탐색에서만 사용할 수 있습니다")
            exploration.act(state,room.game,pid,body.kind,body.target)
            if state.get("meeting") or room.game.phase=="finished":
                exploration.finish(state,room.game);room.intents.clear();hub.queue(room)
            hub.save(room)
        return hub.view(room,pid)

    @app.post("/api/rooms/{rid}/commands")
    async def command(rid:str,body:CommandBody,request:Request):
        hub,room,pid=auth(request,rid)
        async with room.lock:
            g=room.game
            if not g: raise RuleError("게임이 시작되지 않았습니다")
            if (pid,body.command_id) in g.commands:
                g.submit(pid,body.command_id,body.version,body.kind,**body.payload)
                return hub.view(room,pid)
            if room.data["status"]!="running" or hub.active(room)!=pid: raise RuleError("지금은 내 차례가 아닙니다")
            if body.kind == "question":
                target = body.payload.get("target")
                if target not in hub.view(room,pid)["question_targets"]: raise RuleError("답변 차례가 남아 있는 AI를 선택하세요")
            g.submit(pid,body.command_id,body.version,body.kind,**body.payload)
            if body.kind == "question":
                q = room.data["queue"]
                target_index = q.index(body.payload["target"], room.data["index"]+1)
                q.insert(room.data["index"]+1, q.pop(target_index))
            room.data["index"]+=1;room.data["deadline"]=time.time()+hub.turn_seconds
            hub.save(room)
        return hub.view(room,pid)

    @app.websocket("/ws/{rid}")
    async def socket(ws:WebSocket,rid:str):
        origin=ws.headers.get("origin")
        if origin and origin.split("://",1)[-1]!=ws.headers.get("host"):
            await ws.close(code=1008);return
        await ws.accept()
        connected_room = None
        try:
            data=await asyncio.wait_for(ws.receive_json(),timeout=5)
            if not isinstance(data, dict):
                await ws.close(code=1008);return
            hub=ws.app.state.hub;room=hub.room(rid);pid=hub.authenticate(room,str(data.get("token","")))
            room.connections += 1
            connected_room = room
            while True:
                await ws.send_json(hub.view(room,pid))
                await asyncio.sleep(.1 if room.data.get("exploration",{}).get("active") else .5)
        except (WebSocketDisconnect, HTTPException, asyncio.TimeoutError, ValueError, RuntimeError, OSError):
            try: await ws.close(code=1008)
            except RuntimeError: pass
        finally:
            if connected_room is not None:
                connected_room.connections -= 1
                connected_room.last_access = time.monotonic()
    app.mount("/assets",StaticFiles(directory=ROOT/"frontend"/"assets"),name="assets")
    return app


app=create_app()
