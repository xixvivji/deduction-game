"""권한 필터링된 개인 관측에서만 검색하는 경량 어휘 기반 RAG."""
import copy
import json
import math
import re
from collections import Counter


def terms(text):
    words = re.findall(r"[가-힣a-zA-Z0-9]+", text.lower())
    return words + [word[i:i+2] for word in words if len(word) > 2 for i in range(len(word)-1)]


def retrieve(view, query=None, top_k=8, recent=6):
    result = copy.deepcopy(view)
    events = result["events"]
    if len(events) <= top_k + recent:
        result["memory"] = {"mode": "retrieved", "original_events": len(events), "selected_events": len(events)}
        return result
    if query is None:
        query = "목격 조사 투표 의심 " + " ".join(json.dumps(e["data"], ensure_ascii=False) for e in events[-3:])
    q = set(terms(query))
    documents = [Counter(terms(json.dumps(e["data"], ensure_ascii=False))) for e in events]
    frequencies = Counter(t for doc in documents for t in doc)
    def score(i):
        return sum((1 + math.log(documents[i][t])) * math.log(1 + len(events)/(1+frequencies[t])) for t in q if documents[i][t])
    ranked = sorted(range(len(events)), key=lambda i: (score(i), i), reverse=True)[:top_k]
    # 개인 조사 결과 및 현재 밤 협의는 검색 순위와 무관하게 보존한다.
    pinned = [i for i,e in enumerate(events) if e["type"] == "조사" or (e["type"] == "비밀 협의" and e["data"].get("day") == view["day"])]
    selected = sorted(set(ranked + pinned + list(range(max(0,len(events)-recent),len(events)))))
    result["events"] = [events[i] for i in selected]
    result["memory"] = {"mode": "retrieved", "original_events": len(events), "selected_events": len(selected)}
    return result
