import asyncio
import uuid

import pytest

from backend.jobs.runner import RunQueueFullError, RunRunner


def blocked_runner(gate: asyncio.Event, queue_cap: int) -> RunRunner:
    async def execute(_run_id: uuid.UUID) -> None:
        await gate.wait()

    return RunRunner(execute, max_concurrent=1, queue_cap=queue_cap)


async def test_reservation_holds_the_queue_slot_until_submit() -> None:
    gate = asyncio.Event()
    runner = blocked_runner(gate, queue_cap=1)
    held = runner.reserve()
    with pytest.raises(RunQueueFullError):
        runner.reserve()
    runner.submit(uuid.uuid4(), held)  # a reserved run is never refused after its row is committed
    held.release()  # already consumed by submit: a no-op
    assert runner.queued == 1
    gate.set()
    await runner.drain()
    assert runner.queued == 0


async def test_released_reservation_frees_the_slot() -> None:
    runner = blocked_runner(asyncio.Event(), queue_cap=1)
    held = runner.reserve()
    held.release()
    held.release()
    assert runner.queued == 0
    runner.reserve().release()


async def test_submit_without_reservation_still_checks_capacity() -> None:
    gate = asyncio.Event()
    runner = blocked_runner(gate, queue_cap=1)
    runner.reserve()
    with pytest.raises(RunQueueFullError):
        runner.submit(uuid.uuid4())
    gate.set()
