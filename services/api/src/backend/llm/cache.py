"""Model response cache: in-memory for tests, Postgres (`model_cache`) for the app, and a read-only
JSON snapshot (`FileCache`) for offline evals and tests, where a miss is an error."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models_calls import ModelCacheEntry


def cache_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class CacheStore(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def put(
        self, key: str, *, kind: str, model: str, version: str | None, value: Any
    ) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any | None:
        return self.data.get(key)

    async def put(
        self, key: str, *, kind: str, model: str, version: str | None, value: Any
    ) -> None:
        self.data[key] = value


class DbCache:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def get(self, key: str) -> Any | None:
        async with self.sessionmaker() as session:
            entry = await session.get(ModelCacheEntry, key)
            if entry is None:
                return None
            await session.execute(
                update(ModelCacheEntry)
                .where(ModelCacheEntry.key == key)
                .values(hit_count=ModelCacheEntry.hit_count + 1)
            )
            await session.commit()
            return entry.output

    async def put(
        self, key: str, *, kind: str, model: str, version: str | None, value: Any
    ) -> None:
        # An upsert, not select-then-insert: parallel calls (two candidates evaluated at once can
        # OCR identical pixels) may store the same key concurrently.
        stmt = insert(ModelCacheEntry).values(
            key=key, kind=kind, model=model, version=version, output=value, hit_count=0
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ModelCacheEntry.key],
            set_={"kind": kind, "model": model, "version": version, "output": stmt.excluded.output},
        )
        async with self.sessionmaker() as session:
            await session.execute(stmt)
            await session.commit()


SNAPSHOT_FORMAT = 1


class CacheMissError(LookupError):
    """A replay-only cache was asked for a key it does not hold (the snapshot is stale)."""

    def __init__(self, key: str, source: str) -> None:
        super().__init__(f"cache miss for key {key[:12]}… in replay-only cache {source}")
        self.key = key


class ReadOnlyCacheError(RuntimeError):
    pass


def snapshot_entry(*, kind: str, model: str, version: str | None, output: Any) -> dict[str, Any]:
    return {"kind": kind, "model": model, "version": version, "output": output}


def write_snapshot(
    path: Path,
    entries: Mapping[str, Mapping[str, Any]],
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Write a `FileCache` snapshot.

    Shape: `{"format": 1, "meta": {...}, "entries": {key: {kind, model, version, output}}}`.
    `meta` describes how it was recorded (OCR engine and packs, vision client and model), so a
    replay can rebuild the same cache keys without the live engines.

    Keys are sorted so the committed file diffs cleanly.
    """
    payload: dict[str, Any] = {
        "format": SNAPSHOT_FORMAT,
        "entries": {k: dict(entries[k]) for k in sorted(entries)},
    }
    if meta:
        payload["meta"] = dict(meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


class FileCache:
    """Replay-only `CacheStore` over a JSON snapshot. `get` raises on a miss; `put` is refused.

    Used by `make eval` and tests together with the socket guard, so "no network calls" is enforced:
    any model call whose answer is not in the snapshot fails loudly instead of going live.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        raw = cast(dict[str, Any], json.loads(path.read_text())) if path.exists() else {}
        if raw and raw.get("format") != SNAPSHOT_FORMAT:
            raise ValueError(f"unsupported cache snapshot format in {path}")
        entries: Any = raw.get("entries", {})
        if not isinstance(entries, dict):
            raise ValueError(f"malformed cache snapshot {path}")
        self.entries = cast(dict[str, dict[str, Any]], entries)
        meta: Any = raw.get("meta", {})
        self.meta = cast(dict[str, Any], meta) if isinstance(meta, dict) else {}

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    async def get(self, key: str) -> Any | None:
        entry = self.entries.get(key)
        if entry is None:
            raise CacheMissError(key, str(self.path))
        return entry.get("output")

    async def put(
        self, key: str, *, kind: str, model: str, version: str | None, value: Any
    ) -> None:
        raise ReadOnlyCacheError(
            f"FileCache {self.path} is replay-only; refusing to store {key[:12]}"
        )
