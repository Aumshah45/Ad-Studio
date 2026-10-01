"""Offline evaluation harness over the golden files (ai-design §9.5, ADR-004).

- `build_cases` turns `outputs/manifest.jsonl` (E-nat, E-final) and `planted/manifest.jsonl`
  (E-plant, regenerated from params) into evaluation cases: image bytes, the exact reference the
  pipeline used, the `EvalTarget` compiled from the stored spec and product facts, and labels.
- `replay_engine` evaluates from `cache/verdicts.json` only (`FileCache`: a miss is an error; OCR
  and the judge are never called, so `make eval` needs no key, no network and no Tesseract).
- `record_engine` evaluates live for missing keys and captures every key it touches
  (`SnapshotRecorder`), which `write_verdicts` saves as the snapshot (`make eval-record`, export).
"""

import asyncio
import dataclasses
import io
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.settings import Settings
from backend.db.models_calls import ModelCacheEntry
from backend.domain.adstudio.evaluator.core import (
    EVALUATOR_VERSION,
    JUDGE_CACHE_VERSION,
    EvalTarget,
    Evaluator,
)
from backend.domain.adstudio.evaluator.ocr import AppleVisionOcr, TesseractOcr, second_engine
from backend.domain.adstudio.evaluator.readback import NoReadback
from backend.domain.adstudio.evaluator.schemas import Evaluation
from backend.domain.adstudio.pipeline import eval_target
from backend.domain.adstudio.planner import parse_facts
from backend.domain.adstudio.spec import CreativeSpec
from backend.domain.adstudio.uploads import MAX_UPLOAD_BYTES, normalize_upload
from backend.domain.adstudio.vision import (
    FAKE_VISION_MODEL,
    AdInspection,
    CompositionVerdict,
    ContextCheck,
    ContextVerdict,
    FakeVisionClient,
    GatewayVisionClient,
    ProductProfile,
    VisionClient,
    rubric_json,
)
from backend.golden.dataset import (
    DIMENSIONS,
    GoldenPaths,
    GoldenSet,
    LabelRow,
    OutputItem,
    PlantedItem,
    Split,
    load_golden,
    read_labels,
    read_outputs,
    read_planted,
    split_of,
)
from backend.golden.mutations import apply_mutation
from backend.llm.cache import (
    CacheMissError,
    FileCache,
    cache_key,
    snapshot_entry,
    write_snapshot,
)
from backend.llm.calls import CallRuntime, guarded_call
from backend.llm.gateway import LlmGateway
from backend.llm.ledger import DbRecorder, MemoryRecorder
from backend.llm.prompts.ad_inspect import AD_INSPECT
from backend.llm.prompts.composition_judge import COMPOSITION_JUDGE
from backend.llm.prompts.context_judge import CONTEXT_JUDGE
from backend.llm.prompts.product_profile import PRODUCT_PROFILE
from backend.storage.blobs import sha256_hex

CaseSet = Literal["nat", "final", "plant"]
LabelSource = Literal["human", "planted", "assumed", "none"]


class GoldenDataError(RuntimeError):
    """The golden files are inconsistent (a missing file, a sha that doesn't match)."""


class IncompleteRecordingError(GoldenDataError):
    """`make eval-record` could not get every answer the evaluator needs (a live judge call
    failed: network error, open breaker, invalid answer). The evaluator turns such failures into
    `unverified` checks, so without this the recording would look complete and `make eval` would
    fail later with missing answers."""

    def __init__(self, snapshot: Path, failed: Sequence[str], *, kept: int) -> None:
        super().__init__(
            f"{len(failed)} live evaluator calls failed while recording {snapshot.name} "
            f"(first: {', '.join(k[:12] for k in failed[:5])}); the {kept} answers that "
            "succeeded were saved, so re-running `make eval-record` only retries the failed ones"
        )
        self.failed = list(failed)


class StaleSnapshotError(RuntimeError):
    """`make eval` needed an OCR or judge answer the snapshot does not hold."""

    def __init__(self, misses: Sequence[str]) -> None:
        super().__init__(
            f"{len(misses)} evaluator answers are missing from cache/verdicts.json "
            "(run `make eval-record`); first: " + ", ".join(m[:12] for m in misses[:5])
        )
        self.misses = list(misses)


# --- cases ----------------------------------------------------------------------------------------


@dataclass
class EvalCase:
    id: str
    set: CaseSet
    brief_id: str
    product_id: str
    image_sha: str
    image: bytes
    reference: bytes
    target: EvalTarget
    labels: dict[str, bool | None] | None
    label_source: LabelSource
    output: OutputItem | None = None
    planted: PlantedItem | None = None
    exclude_native_text: bool = False

    @property
    def split(self) -> Split:
        return split_of(self.product_id)

    @property
    def mutation(self) -> str | None:
        return self.planted.mutation if self.planted else None


async def reference_images(paths: GoldenPaths, golden: GoldenSet) -> dict[str, bytes]:
    """The normalised reference PNG per product, byte-identical to what an upload stores (the
    evaluator's cache keys include its sha256)."""
    refs: dict[str, bytes] = {}
    for pid in golden.products:
        raw = paths.product_file(golden, pid).read_bytes()
        refs[pid] = (await normalize_upload(raw, max_bytes=MAX_UPLOAD_BYTES)).data
    return refs


def target_for(item: OutputItem) -> EvalTarget:
    """The pipeline's own target for this candidate: a clean plate expects no text yet, an overlay
    is judged as native text (pipeline `_target_for` / `apply_overlay`)."""
    spec = CreativeSpec.model_validate(item.spec)
    target = eval_target(spec, parse_facts(item.facts))
    if item.candidate_kind == "clean_plate":
        return dataclasses.replace(target, text_mode="overlay_only")
    if item.candidate_kind == "overlay":
        return dataclasses.replace(target, text_mode="native")
    return target


def product_loader(refs: Mapping[str, bytes]) -> Callable[[str], bytes]:
    def load(pid: str) -> bytes:
        return refs[pid]

    return load


def materialize(item: PlantedItem, paths: GoldenPaths, refs: Mapping[str, bytes]) -> bytes:
    """A planted image: generated ones are files; pure mutations are regenerated from params."""
    if item.kind == "generated":
        if not item.file:
            raise GoldenDataError(f"{item.id}: generated item without a file")
        return (paths.planted / item.file).read_bytes()
    source = paths.outputs / f"{item.source_id}.png"
    if not source.exists():
        raise GoldenDataError(f"{item.id}: source image {source.name} is missing")
    return apply_mutation(item.mutation, source.read_bytes(), item.params, product_loader(refs))


def _label_dims(row: LabelRow) -> dict[str, bool | None]:
    return {d: getattr(row, d) for d in DIMENSIONS}


@dataclass
class CaseBatch:
    cases: list[EvalCase]
    warnings: list[str] = field(default_factory=list[str])
    reproduced: int = 0  # pure planted items whose regenerated sha matches the manifest
    pure: int = 0


async def build_cases(
    paths: GoldenPaths, *, assume_pass: bool = False, include_planted: bool = True
) -> CaseBatch:
    golden = load_golden(paths)
    refs = await reference_images(paths, golden)
    labels = read_labels(paths.labels)
    outputs = read_outputs(paths)
    result = CaseBatch(cases=[])
    by_id: dict[str, EvalCase] = {}
    for item in outputs:
        path = paths.outputs / item.file
        if not path.exists():
            raise GoldenDataError(f"{item.id}: {path.name} is missing")
        data = path.read_bytes()
        if sha256_hex(data) != item.image_sha:
            raise GoldenDataError(f"{item.id}: {path.name} does not match its manifest sha")
        ref = refs[item.product_id]
        if sha256_hex(ref) != item.reference_sha:
            raise GoldenDataError(
                f"{item.id}: products/{item.product_id} normalises to a different reference than "
                "the run used (Pillow version change?); re-run the golden export"
            )
        row = labels.get((item.image_sha, item.set))
        case_labels: dict[str, bool | None] | None
        if row is not None:
            case_labels, source = _label_dims(row), cast(LabelSource, "human")
        elif assume_pass:
            case_labels = {d: True for d in DIMENSIONS}
            source = cast(LabelSource, "assumed")
        else:
            case_labels, source = None, cast(LabelSource, "none")
        case = EvalCase(
            id=item.id,
            set=item.set,
            brief_id=item.brief_id,
            product_id=item.product_id,
            image_sha=item.image_sha,
            image=data,
            reference=ref,
            target=target_for(item),
            labels=case_labels,
            label_source=source,
            output=item,
            exclude_native_text=item.exclude_native_text,
        )
        by_id[item.id] = case
        result.cases.append(case)
    if not include_planted:
        return result
    for planted in read_planted(paths):
        base = by_id.get(planted.source_id)
        if base is None:
            result.warnings.append(f"{planted.id}: source {planted.source_id} not in outputs")
            continue
        data = materialize(planted, paths, refs)
        sha = sha256_hex(data)
        if planted.kind == "pure":
            result.pure += 1
            if sha == planted.image_sha:
                result.reproduced += 1
            else:
                result.warnings.append(
                    f"{planted.id}: regenerated image differs from the manifest sha "
                    "(different Pillow/font build); evaluated as regenerated"
                )
        elif sha != planted.image_sha:
            raise GoldenDataError(f"{planted.id}: {planted.file} does not match its manifest sha")
        plant_labels = dict(planted.labels)
        human = labels.get((sha, "plant"))
        if human is not None:  # generated items are human-verified through labels.csv
            plant_labels = _label_dims(human)
        result.cases.append(
            EvalCase(
                id=planted.id,
                set="plant",
                brief_id=planted.brief_id,
                product_id=planted.product_id,
                image_sha=sha,
                image=data,
                reference=base.reference,
                target=base.target,
                labels=plant_labels,
                label_source="human" if human is not None else "planted",
                planted=planted,
                exclude_native_text=base.exclude_native_text,
            )
        )
    return result


# --- engines --------------------------------------------------------------------------------------


class CachedVisionClient:
    """Routes a vision client that has no cache of its own (the fake) through `guarded_call` and
    the runtime cache, so its answers are recorded into and replayed from the verdict snapshot
    exactly like the gateway judge's."""

    name = "cached"

    def __init__(
        self,
        inner: VisionClient,
        runtime: CallRuntime,
        *,
        cache_version: str,
        model: str | None = None,
    ) -> None:
        self.inner = inner
        self.runtime = runtime
        self.cache_version = cache_version
        self._model = model

    def configured(self) -> bool:
        return isinstance(self.runtime.cache, FileCache) or self.inner.configured()

    @property
    def model(self) -> str:
        return self._model or self.inner.model

    async def _call[T: BaseModel](
        self, operation: str, key: str, output: type[T], fn: Callable[[], Any]
    ) -> T:
        value, _ = await guarded_call(
            "text",
            f"vision.{operation}",
            self.model,
            fn,
            version=self.cache_version,
            prompt_name=operation,
            cache_key=key,
            encode=lambda v: cast(BaseModel, v).model_dump(mode="json"),
            decode=output.model_validate,
            runtime=self.runtime,
        )
        return cast(T, value)

    async def profile(self, reference: bytes) -> ProductProfile:
        key = cache_key(
            "vision",
            PRODUCT_PROFILE.name,
            PRODUCT_PROFILE.version,
            self.model,
            sha256_hex(reference),
        )
        return await self._call(
            PRODUCT_PROFILE.name, key, ProductProfile, lambda: self.inner.profile(reference)
        )

    async def inspect(self, reference: bytes, ad: bytes, *, summary: str) -> AdInspection:
        key = cache_key(
            "vision",
            AD_INSPECT.name,
            AD_INSPECT.version,
            self.model,
            summary,
            [sha256_hex(reference), sha256_hex(ad)],
            [self.cache_version],
        )
        return await self._call(
            AD_INSPECT.name,
            key,
            AdInspection,
            lambda: self.inner.inspect(reference, ad, summary=summary),
        )

    async def judge_context(self, ad: bytes, checks: list[ContextCheck]) -> ContextVerdict:
        key = cache_key(
            "vision",
            CONTEXT_JUDGE.name,
            CONTEXT_JUDGE.version,
            self.model,
            rubric_json(checks),
            [sha256_hex(ad)],
            [self.cache_version],
        )
        return await self._call(
            CONTEXT_JUDGE.name,
            key,
            ContextVerdict,
            lambda: self.inner.judge_context(ad, checks),
        )

    async def judge_composition(self, ad: bytes, checks: list[ContextCheck]) -> CompositionVerdict:
        key = cache_key(
            "vision",
            COMPOSITION_JUDGE.name,
            COMPOSITION_JUDGE.version,
            self.model,
            rubric_json(checks),
            [sha256_hex(ad)],
            [self.cache_version],
        )
        return await self._call(
            COMPOSITION_JUDGE.name,
            key,
            CompositionVerdict,
            lambda: self.inner.judge_composition(ad, checks),
        )


class SnapshotOcr(TesseractOcr):
    """Tesseract as recorded in the snapshot: its version and packs come from the snapshot meta,
    so the cache keys match and no binary is needed. A miss is collected (the text evaluator turns
    OCR errors into `unverified`, so the harness checks `misses` to fail loudly)."""

    def __init__(self, runtime: CallRuntime, *, version: str, langs: Sequence[str]) -> None:
        super().__init__(runtime)
        self._version = version
        self._langs = frozenset(langs)
        self.misses: list[str] = []

    def available(self) -> bool:
        return bool(self._version)

    @property
    def version(self) -> str:
        return self._version

    def languages(self) -> frozenset[str]:
        return self._langs

    async def read(self, image: Any, *, lang: str, psm: int) -> Any:
        try:
            return await super().read(image, lang=lang, psm=psm)
        except CacheMissError as exc:
            self.misses.append(exc.key)
            raise


class SnapshotAppleVisionOcr(AppleVisionOcr):
    """Apple Vision as recorded in the snapshot (meta `ocr2`): replay needs no macOS."""

    def __init__(self, runtime: CallRuntime, *, version: str, langs: Sequence[str]) -> None:
        super().__init__(runtime)
        self._version = version
        self._langs = frozenset(langs)
        self.misses: list[str] = []

    def available(self) -> bool:
        return bool(self._version)

    @property
    def version(self) -> str:
        return self._version

    def languages(self) -> frozenset[str]:
        return self._langs

    async def read(self, image: Any, *, lang: str, psm: int = 0) -> Any:
        try:
            return await super().read(image, lang=lang, psm=psm)
        except CacheMissError as exc:
            self.misses.append(exc.key)
            raise


class SnapshotRecorder:
    """A `CacheStore` for recording: answers from the existing snapshot, then from the DB cache
    (a golden run already paid for most answers), else lets the call go live (`allow_live`).
    Every key touched is captured in `touched` with its full entry.

    A key that went live and never came back through `put` is a failed call (`failed`): the
    evaluator swallows judge errors as `unverified`, so this is the only place they are visible."""

    def __init__(
        self,
        base: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        allow_live: bool = True,
    ) -> None:
        self.base = dict(base or {})
        self.sessionmaker = sessionmaker
        self.allow_live = allow_live
        self.touched: dict[str, dict[str, Any]] = {}
        self.hits_snapshot = 0
        self.hits_db = 0
        self._pending: set[str] = set()  # went live, no answer stored yet
        self._live_keys: set[str] = set()

    @property
    def live(self) -> int:
        """Live calls that returned an answer (stored through `put`)."""
        return sum(1 for k in self.touched if k in self._live_keys)

    @property
    def failed(self) -> list[str]:
        """Keys whose live call never produced an answer."""
        return sorted(self._pending)

    async def get(self, key: str) -> Any | None:
        if key in self.base:
            self.hits_snapshot += 1
            self.touched[key] = dict(self.base[key])
            return self.base[key].get("output")
        if self.sessionmaker is not None:
            async with self.sessionmaker() as session:
                row = await session.get(ModelCacheEntry, key)
            if row is not None:
                self.hits_db += 1
                self.touched[key] = snapshot_entry(
                    kind=row.kind, model=row.model, version=row.version, output=row.output
                )
                return row.output
        if not self.allow_live:
            raise CacheMissError(key, "record (live calls disabled)")
        self._pending.add(key)
        self._live_keys.add(key)
        return None

    async def put(
        self, key: str, *, kind: str, model: str, version: str | None, value: Any
    ) -> None:
        self._pending.discard(key)
        self.touched[key] = snapshot_entry(kind=kind, model=model, version=version, output=value)


@dataclass
class EvalEngine:
    evaluator: Evaluator
    runtime: CallRuntime
    meta: dict[str, Any]
    ocr: TesseractOcr
    recorder: SnapshotRecorder | None = None
    ocr2: AppleVisionOcr | None = None

    @property
    def mode(self) -> str:
        return "record" if self.recorder is not None else "replay"


def without_readback(engine: EvalEngine) -> EvalEngine:
    """The same engine (runtime, cache, OCR, judge) with the VLM read-back off: ablation A1's
    deterministic text layer. Recording runs it too, so its extra OCR re-reads are in the
    snapshot and `make eval` replays it offline."""
    ev = engine.evaluator
    return dataclasses.replace(
        engine,
        evaluator=Evaluator(
            ev.ocr, vision=ev.vision, readback=NoReadback(), config=ev.config, ocr2=ev.ocr2
        ),
    )


def _vision(
    settings: Settings, runtime: CallRuntime, client: str, model: str
) -> tuple[VisionClient, str]:
    if client == "fake":
        return (
            CachedVisionClient(
                FakeVisionClient(), runtime, cache_version=JUDGE_CACHE_VERSION, model=model
            ),
            model,
        )
    judge_settings = settings.model_copy(update={"vision_judge": model}) if model else settings
    gateway = LlmGateway(judge_settings, runtime)
    vision = GatewayVisionClient(gateway, cache_version=JUDGE_CACHE_VERSION)
    return vision, vision.model


def replay_engine(snapshot: Path, settings: Settings) -> EvalEngine:
    cache = FileCache(snapshot)
    meta = cache.meta
    runtime = CallRuntime(cache=cache, recorder=MemoryRecorder(), max_concurrency=8)
    ocr_meta = cast(dict[str, Any], meta.get("ocr") or {})
    ocr = SnapshotOcr(
        runtime,
        version=str(ocr_meta.get("version", "")),
        langs=[str(x) for x in cast(list[Any], ocr_meta.get("langs") or [])],
    )
    ocr2_meta = cast(dict[str, Any], meta.get("ocr2") or {})
    ocr2 = (
        SnapshotAppleVisionOcr(
            runtime,
            version=str(ocr2_meta.get("version", "")),
            langs=[str(x) for x in cast(list[Any], ocr2_meta.get("langs") or [])],
        )
        if ocr2_meta
        else None
    )
    vision_meta = cast(dict[str, Any], meta.get("vision") or {})
    vision, _ = _vision(
        settings,
        runtime,
        str(vision_meta.get("client", "fake")),
        str(vision_meta.get("model", FAKE_VISION_MODEL)),
    )
    return EvalEngine(Evaluator(ocr, vision=vision, ocr2=ocr2), runtime, dict(meta), ocr, ocr2=ocr2)


def record_engine(
    settings: Settings,
    *,
    base: Path | None,
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    allow_live: bool = True,
    use_db_cache: bool = True,
) -> EvalEngine:
    """`sessionmaker` records every call in the `model_calls` ledger and, with `use_db_cache`,
    also answers from the DB cache (off for the judge-stability rerun, which must go live)."""
    existing = FileCache(base).entries if base is not None and base.exists() else {}
    recorder = SnapshotRecorder(
        existing,
        sessionmaker=sessionmaker if use_db_cache else None,
        allow_live=allow_live,
    )
    runtime = CallRuntime(
        cache=recorder,
        recorder=DbRecorder(sessionmaker) if sessionmaker is not None else MemoryRecorder(),
        max_concurrency=2,
        timeout_s=settings.llm_timeout_s,
    )
    ocr = TesseractOcr(runtime)
    if not ocr.available():
        raise GoldenDataError("recording verdicts needs the tesseract binary on PATH")
    client = "fake" if settings.vision_client == "fake" else "gateway"
    vision, model = _vision(
        settings, runtime, client, FAKE_VISION_MODEL if client == "fake" else ""
    )
    if not vision.configured():
        raise GoldenDataError(
            "no vision judge is configured (set GOOGLE_API_KEY and VISION_JUDGE, or "
            "VISION_CLIENT=fake for a dry run)"
        )
    # ev-0.8: Apple Vision, the ensemble's second engine, when this machine has it (macOS). A
    # snapshot recorded without it replays as Tesseract + read-back only.
    ocr2 = second_engine(runtime, settings.ocr_apple_vision)
    meta: dict[str, Any] = {
        "ocr": {"version": ocr.version, "langs": sorted(ocr.languages())},
        "vision": {"client": client, "model": model},
        "evaluator_version": EVALUATOR_VERSION,
    }
    if ocr2 is not None:
        meta["ocr2"] = {
            "engine": ocr2.name,
            "version": ocr2.version,
            "langs": sorted(ocr2.languages()),
        }
    return EvalEngine(
        Evaluator(ocr, vision=vision, ocr2=ocr2), runtime, meta, ocr, recorder, ocr2=ocr2
    )


# --- evaluation -----------------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: EvalCase
    evaluation: Evaluation


async def evaluate_cases(
    engine: EvalEngine, cases: Sequence[EvalCase], *, concurrency: int = 2
) -> list[CaseResult]:
    """Evaluate every case (at most `concurrency` at once); a stale snapshot fails loudly."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    misses: list[str] = []

    async def one(case: EvalCase) -> CaseResult | None:
        async with semaphore:
            try:
                evaluation = await engine.evaluator.evaluate(
                    case.image, case.reference, case.target
                )
            except CacheMissError as exc:
                misses.append(exc.key)
                return None
            return CaseResult(case, evaluation)

    results = await asyncio.gather(*(one(c) for c in cases))
    ocr_misses = [
        *getattr(engine.ocr, "misses", []),
        *getattr(engine.ocr2, "misses", []),
    ]
    if misses or ocr_misses:
        raise StaleSnapshotError([*misses, *ocr_misses])
    return [r for r in results if r is not None]


def write_verdicts(
    path: Path, engine: EvalEngine, *, keep_existing: bool, extra_meta: Mapping[str, Any] = {}
) -> int:
    """Save the keys the recording touched (plus the old snapshot's when `keep_existing`)."""
    recorder = engine.recorder
    if recorder is None:
        raise ValueError("write_verdicts needs a record engine")
    entries: dict[str, Mapping[str, Any]] = dict(recorder.base) if keep_existing else {}
    entries.update(recorder.touched)
    meta = {**engine.meta, **extra_meta, "recorded_at": datetime.now(UTC).isoformat()}
    write_snapshot(path, entries, meta)
    return len(entries)


def image_size(data: bytes) -> tuple[int, int] | None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:  # noqa: BLE001 - a planted truncated/broken image has no size
        return None
