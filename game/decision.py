"""관측 → 기억 검색 → 생성 → 검증 → 제한 재시도/기본 행동의 LangGraph."""
import time
import copy
from typing import TypedDict
from langgraph.graph import StateGraph, END
from .ai import action_spec, validate
from .memory import retrieve
from .providers import ProviderError


class DecisionState(TypedDict, total=False):
    view: dict
    client: object
    memory_mode: str
    selected: dict
    attempts: int
    payload: dict
    error: str
    fallback: bool
    kind: str
    schema: dict
    audit: dict


def select(state):
    view = retrieve(state["view"]) if state.get("memory_mode") == "retrieved" else state["view"]
    kind, schema = action_spec(view)
    schema = copy.deepcopy(schema)
    schema["properties"].update({
        "decision_summary": {"type": "string", "minLength": 1, "maxLength": 200},
        "evidence_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 3},
    })
    schema["required"] += ["decision_summary", "evidence_ids"]
    return {"selected": view, "kind": kind, "schema": schema, "attempts": 0, "error": "", "fallback": False}


def generate(state):
    if state["attempts"]:
        time.sleep(1)
    try:
        payload = state["client"].generate(state["selected"], state["schema"], state["error"] or None)
        validate(payload, state["schema"])
        ids = payload["evidence_ids"]
        visible = {e["sequence"]: e for e in state["selected"]["events"]}
        if len(ids) != len(set(ids)) or any(i not in visible for i in ids):
            raise ValueError("인용은 전달받은 기록 ID만 사용할 수 있습니다")
        audit = {"summary": payload.pop("decision_summary"), "evidence_ids": payload.pop("evidence_ids"),
                 "evidence": [copy.deepcopy(visible[i]) for i in ids],
                 "available_event_ids": list(visible), "citation_access_valid": True,
                 "semantic_support": "not_evaluated"}
        return {"payload": payload, "audit": audit, "attempts": state["attempts"]+1, "error": ""}
    except ProviderError:
        raise
    except (ValueError, TypeError, KeyError, OSError):
        return {"error": "응답 형식 또는 연결 오류. 지정된 스키마를 따르세요.", "attempts": state["attempts"]+1}


def fallback(state):
    kind = state["kind"]
    payload = {"text": "판단을 보류하겠습니다."} if kind == "speak" else {"target": None}
    if kind == "council": payload["text"] = "공격을 보류합니다."
    if kind == "night": payload["location"] = "광장"
    return {"payload": payload, "fallback": True, "audit": {"summary": "모델 응답 검증 실패로 기본 행동 적용", "evidence_ids": [], "evidence": [], "citation_access_valid": None, "semantic_support": "not_evaluated"}}


def route(state):
    return "done" if not state["error"] else "retry" if state["attempts"] < 2 else "fallback"


def build_graph():
    graph = StateGraph(DecisionState)
    graph.add_node("memory", select)
    graph.add_node("generate", generate)
    graph.add_node("fallback", fallback)
    graph.set_entry_point("memory")
    graph.add_edge("memory", "generate")
    graph.add_conditional_edges("generate", route, {"done": END, "retry": "generate", "fallback": "fallback"})
    graph.add_edge("fallback", END)
    return graph.compile()


GRAPH = build_graph()


def decide(view, client, memory_mode="retrieved"):
    result = GRAPH.invoke({"view": view, "client": client, "memory_mode": memory_mode}, config={"recursion_limit": 10})
    return {key: result[key] for key in ("kind", "payload", "attempts", "fallback", "audit")}
