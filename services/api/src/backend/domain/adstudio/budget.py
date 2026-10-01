"""Per-run budget (ai-design §4.3/§11, production-readiness D4, reconciliation: $0.25, <= 5 image
calls, <= 150 s) and the daily ledger cap ($3/day) that gates new runs.

Image calls are charged in **USD at list price, reserved before the call** (the provider may bill
a generation we then abandon). The reservation is released only when the call was served from the
cache (it cost nothing). Text/vision spend comes from the ledger rows stamped with the run id, so
the projection before each image call is `reserved image spend + ledger non-image spend + price`.
If the next call does not fit, the router gets `can_afford_image_call=False` and goes to the
overlay (text-only failure) or to `needs_review`.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppError
from backend.db.models_calls import ModelCall
from backend.llm.pricing import estimate_cost

WARN_FRACTION = 0.8  # budget.warning once the projected spend passes 80% of the cap
StopReason = Literal["budget", "image_calls", "deadline"]


def image_price(model: str) -> float:
    """List price of one 1K image on `model` (0 for unknown or test models)."""
    return estimate_cost(model, 0, 1, "images")


@dataclass
class RunBudget:
    cap_usd: float
    max_image_calls: int
    deadline_s: float
    clock: Callable[[], float] = time.monotonic
    reserved_usd: float = 0.0
    other_usd: float = 0.0  # ledger spend on text/vision calls, refreshed by the pipeline
    image_calls: int = 0
    warned: bool = False
    started: float = field(default=0.0)

    def __post_init__(self) -> None:
        self.started = self.clock()

    @property
    def spent_usd(self) -> float:
        return round(self.reserved_usd + self.other_usd, 6)

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self.started

    @property
    def expired(self) -> bool:
        return self.elapsed_s > self.deadline_s

    def blocker(self, model: str) -> StopReason | None:
        """Why the next image call on `model` can't be made, or None if it fits."""
        if self.expired:
            return "deadline"
        if self.image_calls + 1 > self.max_image_calls:
            return "image_calls"
        if self.spent_usd + image_price(model) > self.cap_usd + 1e-9:
            return "budget"
        return None

    def can_afford(self, model: str) -> bool:
        return self.blocker(model) is None

    def reserve(self, model: str) -> float:
        """Charge one image call at list price; raises if it does not fit (callers check first)."""
        reason = self.blocker(model)
        if reason is not None:
            raise AppError(429, "budget-exceeded", "Run budget exceeded", f"Blocked by {reason}.")
        price = image_price(model)
        self.image_calls += 1
        self.reserved_usd += price
        return price

    def release(self, amount: float) -> None:
        """Refund a reservation (the call was a cache hit and cost nothing)."""
        self.reserved_usd = max(0.0, self.reserved_usd - amount)

    def should_warn(self) -> bool:
        if self.warned or self.spent_usd < self.cap_usd * WARN_FRACTION:
            return False
        self.warned = True
        return True


# --- daily cap -----------------------------------------------------------------------------------


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def spent_today_usd(session: AsyncSession, now: datetime | None = None) -> float:
    """Ledger spend since 00:00 UTC (list-price estimates, cached calls cost 0)."""
    start = _day_start(now or datetime.now(UTC))
    total = await session.scalar(
        select(func.coalesce(func.sum(ModelCall.est_cost_usd), 0.0)).where(
            ModelCall.created_at >= start
        )
    )
    return float(total or 0.0)


class DailyBudgetExceededError(AppError):
    def __init__(self, spent: float, cap: float, retry_after_s: int) -> None:
        super().__init__(
            429,
            "daily-budget-exceeded",
            "Daily model budget reached",
            f"Today's model spend is ${spent:.2f} of the ${cap:.2f} daily cap "
            "(DAILY_BUDGET_USD); new runs are refused until 00:00 UTC.",
            retry_after=retry_after_s,
        )


async def ensure_daily_budget(session: AsyncSession, cap_usd: float) -> None:
    now = datetime.now(UTC)
    spent = await spent_today_usd(session, now)
    if spent >= cap_usd:
        tomorrow = _day_start(now) + timedelta(days=1)
        raise DailyBudgetExceededError(
            spent, cap_usd, max(1, int((tomorrow - now).total_seconds()))
        )
