import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import repositories as repo
from backend.db.session import create_database
from tests.conftest import TEST_DB_URL


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    db = create_database(TEST_DB_URL)
    async with db.sessionmaker() as s:
        yield s
        await s.rollback()
    await db.dispose()


async def _product(session: AsyncSession, tenant: str, n: int) -> None:
    image, created = await repo.get_or_create_image(
        session,
        tenant,
        sha256=f"{n:064x}",
        source="upload",
        mime="image/png",
        width=512,
        height=512,
        size=100,
    )
    assert created
    await repo.create_product(session, tenant, name=f"p{n}", image_id=image.id)


async def test_products_page_by_cursor_and_tenant(session: AsyncSession) -> None:
    for n in range(5):
        await _product(session, "t-page", n)
    await _product(session, "t-other", 99)
    first = await repo.list_products(session, "t-page", limit=2)
    assert len(first.items) == 2 and first.next_cursor
    second = await repo.list_products(session, "t-page", limit=2, cursor=first.next_cursor)
    third = await repo.list_products(session, "t-page", limit=2, cursor=second.next_cursor)
    names = [p.name for page in (first, second, third) for p in page.items]
    assert sorted(names) == [f"p{n}" for n in range(5)] and third.next_cursor is None
    again, created = await repo.get_or_create_image(
        session,
        "t-page",
        sha256=f"{0:064x}",
        source="upload",
        mime="image/png",
        width=1,
        height=1,
        size=1,
    )
    assert not created and again.width == 512


async def test_get_or_create_image_concurrent_insert_returns_the_winner() -> None:
    db = create_database(TEST_DB_URL)
    tenant = f"t-race-{uuid.uuid4()}"
    fields: dict[str, Any] = {
        "sha256": uuid.uuid4().hex * 2,
        "source": "generated",
        "mime": "image/png",
        "width": 8,
        "height": 8,
        "size": 1,
    }
    try:
        async with db.sessionmaker() as first, db.sessionmaker() as second:
            winner, created = await repo.get_or_create_image(first, tenant, **fields)
            assert created
            racer = asyncio.create_task(repo.get_or_create_image(second, tenant, **fields))
            await asyncio.sleep(0.3)  # the racer's INSERT now waits on the uncommitted row
            assert not racer.done()
            await first.commit()
            loser, created = await racer
            assert not created and loser.id == winner.id
            await second.commit()  # the conflict rolled back only the savepoint
    finally:
        await db.dispose()


async def test_run_events_append_and_replay(session: AsyncSession) -> None:
    await _product(session, "t-run", 1)
    product = (await repo.list_products(session, "t-run")).items[0]
    brief = await repo.create_brief(
        session,
        "t-run",
        product_id=product.id,
        geography_code="AU",
        season="December",
        required_text="Summer Sale 20% off",
        aspect_ratio="4:5",
    )
    run = await repo.create_run(session, "t-run", brief_id=brief.id, idempotency_key="k1")
    assert run.status == "queued" and run.config == {}
    for kind in ("run.status", "plan.done", "run.finished"):
        await repo.append_run_event(session, run.id, kind, {"type": kind})
    events = await repo.list_run_events(session, run.id, after_seq=1)
    assert [(e.seq, e.type) for e in events] == [(2, "plan.done"), (3, "run.finished")]
    assert await repo.get_run_by_idempotency_key(session, "t-run", "k1") is not None
    assert await repo.get_run(session, "other-tenant", run.id) is None


async def test_bad_cursor_is_422(session: AsyncSession) -> None:
    from backend.core.errors import AppError

    with pytest.raises(AppError) as err:
        await repo.list_products(session, "t", cursor="!!notacursor")
    assert err.value.status == 422
