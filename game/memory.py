"""권한 필터링된 개인 관측에서만 검색하는 경량 어휘 기반 RAG."""
import copy
import json
import math
import re
import time
from .runtime_cache import TOKEN_CACHE
from collections import Counter


def terms(text):
    words = re.findall(r"[가-힣a-zA-Z0-9]+", text.lower())
    return words + [word[i:i+2] for word in words if len(word) > 2 for i in range(len(word)-1)]


def retrieve(view, query=None, top_k=8, recent=6, use_cache=True):
    started=time.perf_counter()
    result = copy.deepcopy(view)
    events = result["events"]
    if len(events) <= top_k + recent:
        result["memory"] = {"mode": "retrieved", "original_events": len(events), "selected_events": len(events)}
        return result
    question = view.get("question")
    if query is None and question:
        query = question["text"] + " " + question["actor"] + " " + question["target"]
    if query is None:
        query = "목격 조사 투표 의심 " + " ".join(json.dumps(e["data"], ensure_ascii=False) for e in events[-3:])
    q = set(terms(query))
    documents=[];cache_hits=0
    namespace=(view.get('room_id',''),view['self']['id'])
    for event in events:
        text=json.dumps(event['data'],ensure_ascii=False,sort_keys=True)
        if use_cache:
            counts,hit=TOKEN_CACHE.get(namespace,event['sequence'],text,terms);cache_hits+=int(hit)
        else:counts=Counter(terms(text))
        documents.append(counts)
    frequencies = Counter(t for doc in documents for t in doc)
    def score(i):
        return (3 if question and events[i]["data"].get("actor") == question["target"] else 0) + sum((1 + math.log(documents[i][t])) * math.log(1 + len(events)/(1+frequencies[t])) for t in q if documents[i][t])
    ranked = sorted(range(len(events)), key=lambda i: (score(i), i), reverse=True)[:top_k]
    # 개인 조사 결과 및 현재 밤 협의는 검색 순위와 무관하게 보존한다.
    pinned = [i for i,e in enumerate(events) if e["type"] in ("조사", "추적", "검시", "공격 목격", "시체 신고") or (e["type"] == "비밀 협의" and e["data"].get("day") == view["day"])]
    if question:
        required = set(question.get("evidence_ids", []) + [question["sequence"]])
        pinned += [i for i,e in enumerate(events) if e["sequence"] in required]
    selected = sorted(set(ranked + pinned + list(range(max(0,len(events)-recent),len(events)))))
    result["events"] = [events[i] for i in selected]
    result["memory"] = {"mode": "retrieved", "original_events": len(events), "selected_events": len(selected), "search_seconds":time.perf_counter()-started,"cache_hits":cache_hits,"cache_misses":len(events)-cache_hits,"cache":TOKEN_CACHE.stats() if use_cache else None}
    return result
