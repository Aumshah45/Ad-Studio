"""Per-IP admission limits for the expensive routes (production-readiness T3).

`POST /v1/runs`: at most `RUNS_PER_HOUR_PER_IP` runs in a sliding hour and
`RUNS_CONCURRENT_PER_IP` unfinished runs at once. `POST /v1/products`: at most
`PRODUCTS_PER_HOUR_PER_IP` uploads in a sliding hour. A limit of 0 is off. Over a limit the
request is a 429 problem+json with `Retry-After`.

A slot is reserved before any spend and released when the request fails, so a 422 or a 503 does
not use up the hour. A run stays "concurrent" while the runner still has its task. In-process
state, like the global token bucket: a multi-instance deploy moves it to Redis.
The client address is the socket peer (`request.client`); `X-Forwarded-For` is not trusted.
"""

import math
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Request

from backend.core.errors import AppError

WINDOW_S = 3600.0
CONCURRENT_RETRY_S = 30


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _never_active(_run_id: uuid.UUID) -> bool:
    return False


@dataclass(eq=False)  # identity: two slots taken in the same tick are still distinct
class Slot:
    at: float
    run_id: uuid.UUID | None = None
    pending: bool = True


@dataclass
class _IpState:
    slots: deque[Slot] = field(default_factory=deque[Slot])


class IpRateLimiter:
    """Sliding-window hourly cap plus an optional concurrency cap, keyed by client IP."""

    def __init__(
        self,
        *,
        name: str,
        per_hour: int,
        concurrent: int = 0,
        is_active: Callable[[uuid.UUID], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        window_s: float = WINDOW_S,
    ) -> None:
        self.name = name
        self.per_hour = per_hour
        self.concurrent = concurrent
        self.is_active: Callable[[uuid.UUID], bool] = is_active or _never_active
        self.clock = clock
        self.window_s = window_s
        self._ips: dict[str, _IpState] = {}

    def _prune(self, state: _IpState, now: float) -> None:
        while state.slots and now - state.slots[0].at >= self.window_s:
            state.slots.popleft()

    def _active(self, state: _IpState) -> int:
        return sum(
            1
            for s in state.slots
            if s.pending or (s.run_id is not None and self.is_active(s.run_id))
        )

    def reserve(self, ip: str) -> Slot:
        """Take a slot or raise 429. Synchronous: no await between the check and the take."""
        now = self.clock()
        state = self._ips.setdefault(ip, _IpState())
        self._prune(state, now)
        if self.per_hour and len(state.slots) >= self.per_hour:
            retry = max(1, math.ceil(self.window_s - (now - state.slots[0].at)))
            raise AppError(
                429,
                "rate-limited",
                "Too many requests",
                f"At most {self.per_hour} {self.name} per hour from one address.",
                retry_after=retry,
            )
        if self.concurrent and self._active(state) >= self.concurrent:
            raise AppError(
                429,
                "rate-limited",
                "Too many requests",
                f"At most {self.concurrent} {self.name} at once from one address; "
                "wait for one to finish.",
                retry_after=CONCURRENT_RETRY_S,
            )
        slot = Slot(at=now)
        state.slots.append(slot)
        return slot

    def bind(self, slot: Slot, run_id: uuid.UUID | None = None) -> None:
        """The request succeeded: the slot counts for the hour (and while `run_id` is active)."""
        slot.run_id = run_id
        slot.pending = False

    def release(self, ip: str, slot: Slot) -> None:
        """The request failed or was a replay: give the slot back."""
        state = self._ips.get(ip)
        if state is not None and slot in state.slots:
            state.slots.remove(slot)


def get_run_limiter(request: Request) -> IpRateLimiter:
    return request.app.state.run_limiter


def get_product_limiter(request: Request) -> IpRateLimiter:
    return request.app.state.product_limiter
