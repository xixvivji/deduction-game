"""Validate process-level execution settings before opening runtime resources."""
from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class RuntimeSettings:
    concurrency: int
    decision_seconds: float

    @classmethod
    def from_env(cls):
        try:
            concurrency = int(os.environ.get('GAME_AI_CONCURRENCY', '2'))
            seconds = float(os.environ.get('GAME_DECISION_SECONDS', '120'))
        except ValueError:
            raise ValueError('GAME_AI_CONCURRENCY와 GAME_DECISION_SECONDS는 숫자여야 합니다') from None
        if not 1 <= concurrency <= 8:
            raise ValueError('GAME_AI_CONCURRENCY는 1~8이어야 합니다')
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('GAME_DECISION_SECONDS는 유한한 양수여야 합니다')
        return cls(concurrency, seconds)
