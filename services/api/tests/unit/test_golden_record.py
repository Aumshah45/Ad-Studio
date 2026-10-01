"""`make eval-record` -> `make eval` on a planted + labelled fixture: whatever the recording stores,
the replay must find (0 misses), and a live judge call that fails during recording must fail the
recording loudly instead of leaving a hole that only `make eval` discovers.

Regression: the v2 recording hit a network blip (3 ConnectErrors, then the breaker open for 29
calls); the evaluator turned those into `unverified` checks, `record` reported success, and
`make eval` then failed with 32 missing ad_inspect answers. No network: the fake judge."""

import json
import shutil
from pathlib import Path

import pytest

from backend.domain.adstudio.vision import AdInspection, FakeVisionClient
from backend.golden import harness
from backend.golden.dataset import GOLDEN_DIR, GoldenPaths, read_planted
from backend.golden.harness import (
    IncompleteRecordingError,
    build_cases,
    evaluate_cases,
    replay_engine,
)
from backend.llm.cache import FileCache
from backend.storage.blobs import sha256_hex
from evals.golden import record
from tests.conftest import make_settings
from tests.golden_fixtures import tesseract_available

pytestmark = pytest.mark.skipif(
    not tesseract_available(), reason="recording needs the tesseract binary"
)

SOURCE = GOLDEN_DIR / "v2"
BRIEF = "B01"
# Two pure mutations of B01-nat (regenerated at replay) and the generated context plant that has
# a human `plant` row in labels.csv.
PLANTED = ("PL03-text_typo", "PL36-comp_pasted", "PL37-context_season")


def _fixture(tmp_path: Path) -> GoldenPaths:
    out = tmp_path / "golden"
    (out / "outputs").mkdir(parents=True)
    (out / "planted").mkdir()
    outputs = [
        line
        for line in (SOURCE / "outputs" / "manifest.jsonl").read_text().splitlines()
        if json.loads(line)["brief_id"] == BRIEF
    ]
    for line in outputs:
        name = json.loads(line)["file"]
        shutil.copy(SOURCE / "outputs" / name, out / "outputs" / name)
    (out / "outputs" / "manifest.jsonl").write_text("\n".join(outputs) + "\n")
    planted = [
        line
        for line in (SOURCE / "planted" / "manifest.jsonl").read_text().splitlines()
        if json.loads(line)["id"] in PLANTED
    ]
    for line in planted:
        if name := json.loads(line).get("file"):
            shutil.copy(SOURCE / "planted" / name, out / "planted" / name)
    (out / "planted" / "manifest.jsonl").write_text("\n".join(planted) + "\n")
    labels = (SOURCE / "labels.csv").read_text().splitlines()
    (out / "labels.csv").write_text(
        "\n".join([labels[0], *(r for r in labels[1:] if f",{BRIEF}," in r)]) + "\n"
    )
    return GoldenPaths(source=GOLDEN_DIR, out=out)


async def _replay_misses(paths: GoldenPaths) -> int:
    cases = (await build_cases(paths)).cases
    try:
        await evaluate_cases(replay_engine(paths.verdicts, make_settings()), cases)
    except harness.StaleSnapshotError as exc:
        return len(exc.misses)
    return 0


async def test_record_then_replay_has_no_misses_on_planted_and_labelled_items(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    batch = await build_cases(paths)
    sets = {c.set for c in batch.cases}
    assert sets == {"nat", "final", "plant"} and len(read_planted(paths)) == len(PLANTED)
    assert any(c.set == "plant" and c.label_source == "human" for c in batch.cases)
    assert batch.reproduced == batch.pure == 2

    out = await record(paths, make_settings(vision_client="fake"), sessionmaker=None)
    assert out.entries == len(FileCache(paths.verdicts).entries) > 0
    assert await _replay_misses(paths) == 0


class _FlakyJudge(FakeVisionClient):
    """While `down`, inspecting a planted image fails (a connection error, then an open breaker)."""

    down: frozenset[str] = frozenset()

    async def inspect(self, reference: bytes, ad: bytes, *, summary: str) -> AdInspection:
        if sha256_hex(ad) in _FlakyJudge.down:
            raise ConnectionError("simulated network blip")
        return await super().inspect(reference, ad, summary=summary)


async def test_a_failed_live_call_fails_the_recording_and_a_rerecord_fills_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    settings = make_settings(vision_client="fake")
    monkeypatch.setattr(harness, "FakeVisionClient", _FlakyJudge)
    planted = {c.image_sha for c in (await build_cases(paths)).cases if c.set == "plant"}
    _FlakyJudge.down = frozenset(planted)
    with pytest.raises(IncompleteRecordingError) as err:
        await record(paths, settings, sessionmaker=None)
    assert len(err.value.failed) == len(planted)
    assert len(FileCache(paths.verdicts).entries) > 0  # the answers that succeeded are saved
    assert await _replay_misses(paths) == len(planted)  # what `make eval` used to hit

    _FlakyJudge.down = frozenset()  # the judge is healthy again
    out = await record(paths, settings, sessionmaker=None)
    assert out.live == len(planted) and out.hits_snapshot > 0  # only the gaps went live
    assert await _replay_misses(paths) == 0


async def test_apple_vision_reads_are_recorded_and_replay_without_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ev-0.8: the second OCR engine's reads go into cache/verdicts.json like Tesseract's (meta
    `ocr2`), and `make eval` replays them with no Apple Vision (e.g. on Linux CI)."""
    from backend.domain.adstudio.evaluator import ocr as ocr_module
    from backend.domain.adstudio.evaluator.ocr import AppleVisionOcr

    paths = _fixture(tmp_path)
    runs: list[str] = []

    class _StandIn(AppleVisionOcr):  # stands in for macOS: reads every image as blank
        def available(self) -> bool:
            return True

        @property
        def version(self) -> str:
            return "test-r3"

        def languages(self) -> frozenset[str]:
            return frozenset({"en-US", "fr-FR"})

        def _run(self, png: bytes, lang: str) -> dict[str, object]:
            runs.append(lang)
            return {"engine": "apple-vision-test-r3", "lang": lang, "psm": 0, "words": []}

    monkeypatch.setattr(harness, "second_engine", lambda runtime, enabled=True: _StandIn(runtime))
    await record(
        paths, make_settings(vision_client="fake", ocr_apple_vision=True), sessionmaker=None
    )
    snapshot = FileCache(paths.verdicts)
    assert snapshot.meta["ocr2"]["version"] == "test-r3" and runs
    recorded = [e for e in snapshot.entries.values() if "apple-vision" in str(e.get("model"))]
    assert len(recorded) >= len(set(runs))

    def gone(*_: object, **__: object) -> dict[str, object]:
        raise AssertionError("replay must not run Apple Vision")

    monkeypatch.setattr(AppleVisionOcr, "_run", gone)
    monkeypatch.setattr(ocr_module, "_vision_info", lambda: None)  # not macOS
    assert await _replay_misses(paths) == 0
