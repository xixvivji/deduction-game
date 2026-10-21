"""권한 필터링된 개인 관측에서만 검색하는 경량 어휘 기반 RAG."""
import copy
import json
import math
import os
import time
from collections import Counter
from .runtime_cache import TOKEN_CACHE
from .search_language import VERSION, terms, legacy_terms, expanded_terms


def rank_events(events, query, *, target=None, use_cache=False, namespace=(), expand=True):
    """Rank already-authorized events. Does not pin facts or append recent events.

    expand=False preserves the pre-expansion data-only tokenizer/scoring baseline.
    Keeping this stage separate lets evaluation exclude always-included records.
    """
    tokenizer = expanded_terms if expand else legacy_terms
    q = set(tokenizer(query))
    documents = []
    cache_hits = 0
    namespace = (*namespace, VERSION if expand else 'legacy-v1')
    for event in events:
        text = json.dumps(event['data'], ensure_ascii=False, sort_keys=True)
        if expand:
            text = event['type'] + ' ' + text
        if use_cache:
            counts, hit = TOKEN_CACHE.get(namespace, event['sequence'], text, tokenizer)
            cache_hits += int(hit)
        else:
            counts = Counter(tokenizer(text))
        documents.append(counts)
    frequencies = Counter(t for doc in documents for t in doc)

    def score(i):
        lexical = sum((1 + math.log(documents[i][t])) *
                      math.log(1 + len(events)/(1+frequencies[t])) *
                      (2 if t.startswith('@concept:') else 1)
                      for t in sorted(q) if documents[i][t])
        return (3 if target and events[i]['data'].get('actor') == target else 0) + lexical

    ranked = sorted(range(len(events)), key=lambda i: (score(i), i), reverse=True)
    return ranked, cache_hits


def retrieve(view, query=None, top_k=8, recent=6, use_cache=None, *, expand=True):
    if use_cache is None:
        use_cache = os.environ.get('GAME_MEMORY_TOKEN_CACHE', 'false').lower() == 'true'
    started = time.perf_counter()
    result = copy.deepcopy(view)
    events = result['events']
    strategy = VERSION if expand else 'legacy-v1'
    if len(events) <= top_k + recent:
        result['memory'] = {'mode': 'retrieved', 'strategy': strategy,
                            'original_events': len(events), 'selected_events': len(events)}
        return result
    question = view.get('question')
    if query is None and question:
        query = question['text'] + ' ' + question['actor'] + ' ' + question['target']
    if query is None:
        query = '목격 조사 투표 의심 ' + ' '.join(
            json.dumps(e['data'], ensure_ascii=False) for e in events[-3:])
    ranked, cache_hits = rank_events(
        events, query, target=question['target'] if question else None,
        use_cache=use_cache, namespace=(view.get('room_id', ''), view['self']['id']),
        expand=expand)
    # 개인 조사 결과 및 현재 밤 협의는 검색 순위와 무관하게 보존한다.
    pinned = [i for i,e in enumerate(events) if e['type'] in
              ('조사', '추적', '검시', '공격 목격', '시체 신고') or
              (e['type'] == '비밀 협의' and e['data'].get('day') == view['day'])]
    if question:
        required = set(question.get('evidence_ids', []) + [question['sequence']])
        pinned += [i for i,e in enumerate(events) if e['sequence'] in required]
    selected = sorted(set(ranked[:top_k] + pinned +
                          list(range(max(0, len(events)-recent), len(events)))))
    result['events'] = [events[i] for i in selected]
    result['memory'] = {
        'mode': 'retrieved', 'strategy': strategy,
        'original_events': len(events), 'selected_events': len(selected),
        'search_seconds': time.perf_counter()-started, 'cache_hits': cache_hits,
        'cache_misses': len(events)-cache_hits,
        'cache': TOKEN_CACHE.stats() if use_cache else None}
    return result
