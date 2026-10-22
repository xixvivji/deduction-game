"""Validate process-level execution settings before opening runtime resources."""
from dataclasses import dataclass
import math
import os
from .context_budget import ContextBudget, DEFAULT_CONTEXT_BUDGET
from .suspicion import SuspicionPolicy, DEFAULT_SUSPICION_POLICY


@dataclass(frozen=True)
class RuntimeSettings:
    concurrency: int
    decision_seconds: float
    context_budget: ContextBudget = DEFAULT_CONTEXT_BUDGET
    suspicion_policy: SuspicionPolicy = DEFAULT_SUSPICION_POLICY

    @classmethod
    def from_env(cls):
        try:
            concurrency = int(os.environ.get('GAME_AI_CONCURRENCY', '2'))
            seconds = float(os.environ.get('GAME_DECISION_SECONDS', '120'))
            context_bytes = int(os.environ.get('GAME_CONTEXT_BYTES', '32768'))
            suspicion_days = int(os.environ.get('GAME_SUSPICION_DAYS', '2'))
        except ValueError:
            raise ValueError('GAME_AI_CONCURRENCY, GAME_DECISION_SECONDS, GAME_CONTEXT_BYTES, GAME_SUSPICION_DAYS는 숫자여야 합니다') from None
        if not 1 <= concurrency <= 8:
            raise ValueError('GAME_AI_CONCURRENCY는 1~8이어야 합니다')
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('GAME_DECISION_SECONDS는 유한한 양수여야 합니다')
        if not 1024 <= context_bytes <= 262144:
            raise ValueError('GAME_CONTEXT_BYTES는 1024~262144여야 합니다')
        if not 0 <= suspicion_days <= 30:
            raise ValueError('GAME_SUSPICION_DAYS는 0~30이어야 합니다')
        return cls(concurrency, seconds, ContextBudget(context_bytes), SuspicionPolicy(suspicion_days))
