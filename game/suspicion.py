"""Evidence eligibility and age policy shared by verification and private memory."""
from dataclasses import dataclass

VILLAGE_ROLES = frozenset({'citizen', 'seer', 'doctor', 'tracker', 'coroner'})


@dataclass(frozen=True)
class SuspicionPolicy:
    # Evidence from day d is fresh on d, d+1 and d+2. Confirmations never expire.
    max_age_days: int = 2

    def __post_init__(self):
        if type(self.max_age_days) is not int or self.max_age_days < 0:
            raise ValueError('의심 근거 유효 일수는 0 이상의 정수여야 합니다')

    def supports(self, event, subject):
        data = event['data'];kind = event['type']
        if kind in ('목격', '탐색 목격'):
            return data.get('player') == subject and bool(data.get('location'))
        if kind == '추적':
            return data.get('target') == subject and data.get('location') not in (None, '', '기록 없음')
        if kind == '공격 목격':
            return data.get('actor') == subject and bool(data.get('target'))
        if kind == '조사':
            return data.get('target') == subject and data.get('team') == 'wolf'
        if kind == '검시':
            return data.get('target') == subject and data.get('role') == 'wolf'
        # Public accusations, ordinary speech, victims and reporters are not proof.
        return False

    def fresh(self, event, day):
        then = event['data'].get('day')
        return type(then) is int and type(day) is int and 0 <= day - then <= self.max_age_days

    def evidence(self, events, subject, day, *, include_stale=False):
        return [e for e in events if self.supports(e, subject)
                and (include_stale or self.fresh(e, day))]


DEFAULT_SUSPICION_POLICY = SuspicionPolicy()
