"""Async engine, session factory and FastAPI dependency."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass
class Database:
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database(url: str) -> Database:
    engine = create_async_engine(url, pool_pre_ping=True)
    return Database(engine, async_sessionmaker(engine, expire_on_commit=False))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.sessionmaker() as session:
        yield session
