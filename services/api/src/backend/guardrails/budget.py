"""Per-request budget on model calls, units and wall-clock time."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.core.errors import AppError


class BudgetExceeded(AppError):
    def __init__(self, detail: str, status: int = 429) -> None:
        super().__init__(status, "budget-exceeded", "Budget exceeded", detail)


@dataclass
class Budget:
    max_calls: int | None = None
    max_units: float | None = None
    deadline_s: float | None = None
    clock: Callable[[], float] = time.monotonic
    calls: int = 0
    units: float = 0.0
    started: float = field(default=0.0)

    def __post_init__(self) -> None:
        self.started = self.clock()

    def charge(self, calls: int = 1, units: float = 0.0) -> None:
        if self.deadline_s is not None and self.clock() - self.started > self.deadline_s:
            raise BudgetExceeded(f"Deadline of {self.deadline_s}s passed.", status=503)
        if self.max_calls is not None and self.calls + calls > self.max_calls:
            raise BudgetExceeded(f"More than {self.max_calls} model calls.")
        if self.max_units is not None and self.units + units > self.max_units:
            raise BudgetExceeded(f"More than {self.max_units} units.")
        self.calls += calls
        self.units += units
