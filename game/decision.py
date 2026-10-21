"""관측 → 기억 검색 → 생성 → 검증 → 제한 재시도/기본 행동의 LangGraph."""
import time
import copy
import re
from typing import TypedDict
from langsmith import traceable
from .memory_backend import LocalMemory, scoped
from .evidence import claim_schema, verify, statement
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
    memory: object
    policy: str
    research_rounds: int
    verification: dict
    verification_history: list
    claim: dict


@traceable(name="memory_selection",run_type="retriever")
def select(state):
    view = state["memory"].search(state["view"]) if state.get("memory_mode") == "retrieved" else scoped(state["view"])
    kind, schema = action_spec(view)
    schema = copy.deepcopy(schema)
    schema["properties"].update({
        "decision_summary": {"type": "string", "minLength": 1, "maxLength": 200},
        "evidence_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 3},
    })
    schema["required"] += ["decision_summary", "evidence_ids"]
    if state.get("policy")=="verified":
        schema["properties"].update(claim_schema(view));schema["required"]+=list(claim_schema(view))
    return {"selected": view, "kind": kind, "schema": schema, "attempts": 0, "error": "", "fallback": False, "research_rounds":0,"verification_history":[]}


@traceable(name='model_decision',run_type='llm')
def invoke_model(client,view,schema,feedback):
    payload=client.generate(view,schema,feedback)
    metrics=getattr(client,'metrics',[])
    usage=metrics[-1] if metrics else {}
    return {'payload':payload,'usage_metadata':{k:usage[k] for k in ('input_tokens','output_tokens','total_tokens') if usage.get(k) is not None}}


def generate(state):
    if state["attempts"]:
        time.sleep(1)
    try:
        payload = invoke_model(state["client"],state["selected"], state["schema"], state["error"] or None)["payload"]
        validate(payload, state["schema"])
        ids = payload["evidence_ids"]
        visible = {e["sequence"]: e for e in state["selected"]["events"]}
        if len(ids) != len(set(ids)) or any(i not in visible for i in ids):
            raise ValueError("인용은 전달받은 기록 ID만 사용할 수 있습니다")
        claim={k:payload.pop(k) for k in claim_schema(state["selected"])} if state.get("policy")=="verified" else {}
        audit = {"summary": payload.pop("decision_summary"), "evidence_ids": payload.pop("evidence_ids"),
                 "evidence": [copy.deepcopy(visible[i]) for i in ids],
                 "available_event_ids": list(visible), "citation_access_valid": True,
                 "semantic_support": "not_evaluated"}
        return {"payload": payload, "claim":claim, "audit": audit, "attempts": state["attempts"]+1, "error": ""}
    except ProviderError:
        raise
    except (ValueError, TypeError, KeyError, OSError):
        return {"error": "응답 형식 또는 연결 오류. 지정된 스키마를 따르세요.", "attempts": state["attempts"]+1}


@traceable(name='validate_evidence',run_type='chain')
def validate_evidence(state):
    if state.get('policy')!='verified':return {'verification':{'ok':True,'status':'not_evaluated'}}
    result=verify(state['selected'],state['claim'],state['audit']['evidence_ids'],state['payload'])
    audit={**state['audit'],'claim':state['claim'],'semantic_support':result['status']}
    payload=dict(state['payload'])
    # Render checked facts from structured claims, never endorse unchecked free text as fact.
    if result['ok'] and 'text' in payload:
        if result['status'] in ('supported_fact','grounded_suspicion'):payload['text']=statement(state['claim'])
        else:
            label='주장: ' if result['status']=='game_deception_allowed' else '의견: '
            text=re.sub(r'^(?:(?:의견|주장)\s*[:：]\s*)+', '', payload['text'].strip()).strip()
            payload['text']=label+(text or '판단을 보류하겠습니다.')[:500-len(label)]
    return {'verification':result,'verification_history':state['verification_history']+[result],
            'audit':audit,'payload':payload,'error':result['reason'] if not result['ok'] else ''}


@traceable(name='evidence_research',run_type='retriever')
def research(state):
    claim=state['claim']
    query=' '.join(str(claim.get(k) or '') for k in ('claim_subject','claim_type','claim_value'))+' 조사 공격 목격 방문 장소'
    selected=state['memory'].search(state['view'],query=query,expanded=True)
    return {'selected':selected,'research_rounds':state['research_rounds']+1}


def after_validation(state):
    if state['verification']['ok']:return 'done'
    return 'research' if state['research_rounds']<2 and state['attempts']<3 else 'fallback'


def fallback(state):
    kind = state["kind"]
    payload = {"text": "판단을 보류하겠습니다."} if kind == "speak" else {"target": None}
    if kind == "council": payload["text"] = "공격을 보류합니다."
    if kind == "night": payload["location"] = state["selected"]["self"].get("explored_location", "광장")
    return {"payload": payload, "fallback": True, "audit": {"summary": "모델 응답 검증 실패로 기본 행동 적용", "evidence_ids": [], "evidence": [], "citation_access_valid": None, "semantic_support": "abstained" if state.get("policy")=="verified" else "not_evaluated"}}


def route(state):
    return "done" if not state["error"] else "retry" if state["attempts"] < 2 else "fallback"


def build_graph():
    graph = StateGraph(DecisionState)
    graph.add_node("memory", select)
    graph.add_node("generate", generate)
    graph.add_node("fallback", fallback)
    graph.add_node("validate_evidence", validate_evidence)
    graph.add_node("research", research)
    graph.set_entry_point("memory")
    graph.add_edge("memory", "generate")
    graph.add_conditional_edges("generate", route, {"done": "validate_evidence", "retry": "generate", "fallback": "fallback"})
    graph.add_conditional_edges("validate_evidence",after_validation,{"done":END,"research":"research","fallback":"fallback"})
    graph.add_edge("research","generate")
    graph.add_edge("fallback", END)
    return graph.compile()


GRAPH = build_graph()


@traceable(name='participant_decision',run_type='chain',process_inputs=lambda d:{'actor':d.get('view',{}).get('self',{}).get('id'),'phase':d.get('view',{}).get('phase'),'policy':d.get('policy','legacy')})
def decide(view, client, memory_mode="retrieved", memory=None, policy="legacy"):
    result = GRAPH.invoke({"view": scoped(view), "client": client, "memory_mode": memory_mode,
                           "memory":memory or LocalMemory(),"policy":policy}, config={"recursion_limit": 20})
    outcome={key: result[key] for key in ("kind", "payload", "attempts", "fallback", "audit")}
    outcome['audit'].update(policy=policy,research_rounds=result['research_rounds'],
                            verification_history=result['verification_history'],memory_backend=(memory or LocalMemory()).name)
    return outcome
