"""Bounded exponential retry with deterministic injection points."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay: float = 0.5
    multiplier: float = 2.0
    max_delay: float = 8.0
    jitter: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")

    def run(
        self,
        operation: Callable[[], T],
        *,
        is_retryable: Callable[[Exception], bool],
        sleeper: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> T:
        delay = self.initial_delay
        for attempt in range(1, self.max_attempts + 1):
            try:
                return operation()
            except Exception as exc:
                if attempt == self.max_attempts or not is_retryable(exc):
                    raise
                spread = delay * self.jitter * ((random_value() * 2) - 1)
                sleeper(max(0.0, min(self.max_delay, delay + spread)))
                delay = min(self.max_delay, delay * self.multiplier)
        raise AssertionError("retry loop exhausted unexpectedly")
