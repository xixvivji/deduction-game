"""Cooperative decision deadline, propagated into blocking calls via contextvars."""
from contextvars import ContextVar
import math
import time

_deadline = ContextVar('decision_deadline', default=None)


def remaining(cap=None):
    deadline = _deadline.get()
    if deadline is None:return cap
    left = deadline-time.monotonic()
    if left <= 0:
        from .providers import ProviderError
        raise ProviderError('AI 판단 시간 예산을 초과했습니다. 게임을 재개하세요.')
    return left if cap is None else min(cap,left)


def run_with_budget(seconds, function, *args, **kwargs):
    if not math.isfinite(seconds) or seconds <= 0:raise ValueError('판단 시간 예산은 양수여야 합니다')
    token = _deadline.set(time.monotonic()+seconds)
    try:
        result = function(*args, **kwargs)
        remaining()  # Never commit a result that arrived after the deadline.
        return result
    finally:
        _deadline.reset(token)
