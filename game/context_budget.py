"""Bound model observation bytes without truncating individual evidence records.

The budget is UTF-8 JSON bytes, not a tokenizer estimate or an API cost ceiling.
Policy objects are immutable; all selected context and accounting are per decision.
"""
import copy
import json
from dataclasses import dataclass
from .prompt_context import compact
from .providers import ProviderError


class ContextBudgetExceeded(ProviderError):
    pass


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def required_ids(view):
    ids = {e['sequence'] for e in view['events'] if e['type'] in
           ('조사', '검시', '공격 목격', '추적', '시체 신고') or
           (e['type'] == '비밀 협의' and e['data'].get('day') == view['day'])}
    question = view.get('question') or {}
    ids.update(question.get('evidence_ids', []))
    if question.get('sequence') is not None:ids.add(question['sequence'])
    ctx = view.get('discussion_context', {})
    for key in ('own_recent', 'public_recent'):
        ids.update(e['sequence'] if isinstance(e, dict) else e for e in ctx.get(key, []))
    ledger = view.get('belief_memory', {})
    for entry in ledger.get('entries', {}).values():
        ids.update(entry.get('evidence_ids', []))
    # Historical references need not still exist after a correction/deletion;
    # if present, preserve them too. Current entries/question refs must resolve.
    available = {e['sequence'] for e in view['events']}
    for item in ledger.get('history', []):
        ids.update(i for i in item.get('evidence_ids', []) if i in available)
    return ids


@dataclass(frozen=True)
class ContextBudget:
    max_bytes: int = 32768

    def __post_init__(self):
        if type(self.max_bytes) is not int or self.max_bytes < 1:
            raise ValueError('AI 문맥 바이트 예산은 양의 정수여야 합니다')

    def apply(self, view):
        selected = copy.deepcopy(view)
        packed = compact(selected)
        before = size = encoded_size(packed)
        protected = required_ids(selected)
        available = {e['sequence'] for e in selected['events']}
        if protected - available:
            raise ContextBudgetExceeded('필수 근거 참조가 빠져 AI 판단을 중단했습니다. 기억 검색 상태를 확인하세요.')
        ranked = selected.get('memory', {}).get('ranked_event_ids', [])
        priority = {seq: i for i, seq in enumerate(ranked)}
        # Drop the lowest search rank first; without ranks retain newer records.
        optional = sorted((e for e in selected['events'] if e['sequence'] not in protected),
                          key=lambda e: (priority.get(e['sequence'], len(priority)), -e['sequence']),
                          reverse=True)
        dropped = set()
        remaining_count = len(selected['events'])
        # JSON list delimiters are two ASCII bytes between items. This avoids
        # serializing the entire context after each removal (quadratic work).
        for event in optional:
            if size <= self.max_bytes:break
            size -= encoded_size(event) + (2 if remaining_count > 1 else 0)
            remaining_count -= 1
            dropped.add(event['sequence'])
        if dropped:
            selected['events'] = [e for e in selected['events'] if e['sequence'] not in dropped]
        after = encoded_size(compact(selected))
        if after > self.max_bytes:
            raise ContextBudgetExceeded('필수 근거가 AI 문맥 예산을 넘었습니다. 근거를 자르지 않고 일시 중지합니다. GAME_CONTEXT_BYTES를 검토하세요.')
        if 'memory' in selected:selected['memory']['selected_events'] = len(selected['events'])
        return selected, {'limit_bytes':self.max_bytes, 'before_bytes':before, 'after_bytes':after,
                          'dropped_events':len(dropped), 'protected_events':len(protected),
                          'selected_events':len(selected['events'])}


DEFAULT_CONTEXT_BUDGET = ContextBudget()
