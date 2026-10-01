"""Per-model circuit breaker: open after N consecutive failures, half-open after a cool-down."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

BreakerState = Literal["closed", "open", "half_open"]


@dataclass
class CircuitBreaker:
    threshold: int = 5
    reset_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = None

    @property
    def state(self) -> BreakerState:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at >= self.reset_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.threshold:
            self.opened_at = self.clock()


@dataclass
class BreakerRegistry:
    threshold: int = 5
    reset_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _breakers: dict[str, CircuitBreaker] = field(default_factory=dict[str, CircuitBreaker])

    def get(self, model: str) -> CircuitBreaker:
        if model not in self._breakers:
            self._breakers[model] = CircuitBreaker(self.threshold, self.reset_s, self.clock)
        return self._breakers[model]

    def states(self) -> dict[str, BreakerState]:
        return {name: b.state for name, b in self._breakers.items()}
