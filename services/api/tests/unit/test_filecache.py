from pathlib import Path

import pytest

from backend.llm.cache import (
    CacheMissError,
    FileCache,
    ReadOnlyCacheError,
    snapshot_entry,
    write_snapshot,
)
from backend.llm.calls import CallRuntime, guarded_call
from backend.llm.ledger import MemoryRecorder


def _snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "verdicts.json"
    write_snapshot(
        path,
        {"k1": snapshot_entry(kind="text", model="google:m", version="1", output={"pass": True})},
    )
    return path


async def test_filecache_hit_replays(tmp_path: Path) -> None:
    rt = CallRuntime(recorder=MemoryRecorder(), cache=FileCache(_snapshot(tmp_path)))

    async def live() -> dict[str, bool]:
        raise AssertionError("must not call the model on a hit")

    assert await guarded_call("text", "judge", "google:m", live, cache_key="k1", runtime=rt) == (
        {"pass": True},
        True,
    )


async def test_filecache_miss_raises(tmp_path: Path) -> None:
    cache = FileCache(_snapshot(tmp_path))
    rt = CallRuntime(recorder=MemoryRecorder(), cache=cache)
    called = False

    async def live() -> str:
        nonlocal called
        called = True
        return "live"

    with pytest.raises(CacheMissError):
        await guarded_call("text", "judge", "google:m", live, cache_key="nope", runtime=rt)
    assert called is False
    with pytest.raises(CacheMissError):
        await FileCache(tmp_path / "missing.json").get("k1")
    with pytest.raises(ReadOnlyCacheError):
        await cache.put("k2", kind="text", model="m", version=None, value=1)
