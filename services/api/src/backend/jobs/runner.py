"""In-process run runner (ADR-002): admission control, asyncio tasks, heartbeat, recovery.

Generic over the work function: `submit(run_id)` schedules `execute(run_id)` under a global
semaphore. Runs survive client disconnects; they die with the process and are marked
`interrupted` at the next startup (`recover_interrupted`). The upgrade path is swapping `submit`
for a queue job; `execute` does not change.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.errors import AppError
from backend.db import repositories as repo

log = structlog.get_logger(__name__)
HEARTBEAT_S = 5.0
STALE_AFTER_S = 60.0


class RunQueueFullError(AppError):
    def __init__(self, retry_after_s: int = 30) -> None:
        super().__init__(
            429,
            "run-queue-full",
            "Too many runs queued",
            "The run queue is full; try again shortly.",
            retry_after=retry_after_s,
        )
        self.retry_after_s = retry_after_s


class Reservation:
    """A queue slot held from admission until `submit` (or `release`), so the capacity check and
    the enqueue cannot be split by the awaits that commit the run row."""

    def __init__(self, on_release: Callable[[], None]) -> None:
        self._on_release: Callable[[], None] | None = on_release

    def release(self) -> None:
        if self._on_release is not None:
            self._on_release()
            self._on_release = None


class RunRunner:
    def __init__(
        self,
        execute: Callable[[uuid.UUID], Awaitable[None]],
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        max_concurrent: int = 4,
        queue_cap: int = 20,
        heartbeat_s: float = HEARTBEAT_S,
    ) -> None:
        self.execute = execute
        self.sessionmaker = sessionmaker
        self.queue_cap = queue_cap
        self.heartbeat_s = heartbeat_s
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._running = 0
        self._reserved = 0

    @property
    def active(self) -> int:
        return self._running

    @property
    def queued(self) -> int:
        return len(self._tasks) - self._running + self._reserved

    def can_accept(self) -> bool:
        return self.queued < self.queue_cap

    def ensure_capacity(self) -> None:
        if not self.can_accept():
            raise RunQueueFullError()

    def reserve(self) -> Reservation:
        """Take a queue slot now (429 run-queue-full if none); `submit` consumes it."""
        self.ensure_capacity()
        self._reserved += 1
        return Reservation(self._unreserve)

    def _unreserve(self) -> None:
        self._reserved -= 1

    def submit(self, run_id: uuid.UUID, reservation: Reservation | None = None) -> None:
        if reservation is not None:
            reservation.release()
        if run_id in self._tasks:
            return
        if reservation is None:
            self.ensure_capacity()
        task = asyncio.create_task(self._run(run_id), name=f"run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    async def _heartbeat(self, run_id: uuid.UUID) -> None:
        if self.sessionmaker is None:
            return
        while True:
            await asyncio.sleep(self.heartbeat_s)
            try:
                async with self.sessionmaker() as session:
                    await repo.update_run(session, run_id, heartbeat_at=datetime.now(UTC))
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 - a missed beat must not kill the run
                log.warning("run_heartbeat_failed", run_id=str(run_id), error=type(exc).__name__)

    async def _run(self, run_id: uuid.UUID) -> None:
        async with self._semaphore:
            self._running += 1
            beat = asyncio.create_task(self._heartbeat(run_id))
            try:
                await self.execute(run_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - execute should never raise; log if it does
                log.error("run_task_crashed", run_id=str(run_id), error_type=type(exc).__name__)
            finally:
                beat.cancel()
                self._running -= 1

    async def join(self, run_id: uuid.UUID, timeout_s: float = 30.0) -> None:
        """Wait for one run's task (tests and the CLI)."""
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)

    def has_task(self, run_id: uuid.UUID) -> bool:
        return run_id in self._tasks

    async def cancel(self, run_id: uuid.UUID, timeout_s: float = 10.0) -> bool:
        """Cancel one run's task and wait for it to wind down. False if no task was tracked."""
        task = self._tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.wait({task}, timeout=timeout_s)
        return True

    async def drain(self, timeout_s: float = 30.0) -> None:
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def recover_interrupted(
    sessionmaker: async_sessionmaker[AsyncSession],
    mark: Callable[[uuid.UUID], Awaitable[None]],
    *,
    stale_after_s: float = STALE_AFTER_S,
) -> list[uuid.UUID]:
    """Startup recovery: every non-terminal run with a stale heartbeat is handed to `mark`."""
    before = datetime.now(UTC) - timedelta(seconds=stale_after_s)
    async with sessionmaker() as session:
        runs = await repo.stale_active_runs(session, before=before)
    ids = [run.id for run in runs]
    for run_id in ids:
        await mark(run_id)
    if ids:
        log.info("runs_interrupted_on_startup", count=len(ids))
    return ids
