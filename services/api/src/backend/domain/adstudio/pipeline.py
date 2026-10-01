"""The run orchestrator: a fixed state machine (ADR-001) driven by `execute_run(run_id)`.

Flow (ai-design §2, §4; ADR-005): product profile (lazily, if the upload had no judge) -> plan
(resolver + planner -> CreativeSpec) -> N=2 candidates on the candidate model (Flash-Lite) in
parallel -> evaluate -> select the best passing one (§4.2), or route the best base candidate
through the repair table (§4.3): targeted Flash repairs (<= MAX_REPAIRS, a clean plate or a text
edit counts), then the deterministic overlay into the text zone, re-evaluated (OCR must pass, or
`construction` for scripts without an OCR pack). Terminal: `passed` (outcome native|overlay,
awaiting human approval) or `needs_review` (exhausted repairs, budget, deadline, judge down);
`failed` on a system error, `interrupted` on shutdown, `cancelled` on request.

Every image call reserves its list price in the per-run `RunBudget` first ($0.25, <= 5 image
calls, 150 s); a call that doesn't fit ends the loop at the overlay (text-only failure) or at
`needs_review`. Injection-flagged text is overlay-only: candidates are clean plates and the text
never reaches the image model. Every state change is written with its `run_events` row in the
same transaction, so the SSE tail and `GET /v1/runs/{id}` always agree.

The same `execute_run` is used by the API runner and (later) the golden CLI.
"""

import asyncio
import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.errors import AppError
from backend.core.settings import Settings
from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.adprompt import prompt_fields
from backend.domain.adstudio.budget import RunBudget
from backend.domain.adstudio.evaluator.composition import CompositionTarget
from backend.domain.adstudio.evaluator.config import load_config
from backend.domain.adstudio.evaluator.context import compile_context_checks
from backend.domain.adstudio.evaluator.core import EvalTarget, Evaluator
from backend.domain.adstudio.evaluator.schemas import Dimension, Evaluation
from backend.domain.adstudio.evaluator.technical import image_similarity
from backend.domain.adstudio.events import (
    BudgetWarningEvent,
    CandidateCreatedEvent,
    CandidateKind,
    DimensionView,
    EvaluationDoneEvent,
    FallbackAppliedEvent,
    PlanDoneEvent,
    RepairStartedEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStatus,
    RunStatusEvent,
    TerminalStatus,
    event_payload,
)
from backend.domain.adstudio.generator import GeneratedImage, Generator
from backend.domain.adstudio.image_clients import ImageClient, ImageRequest
from backend.domain.adstudio.overlay import Overlay, OverlayRefusedError, OverlayZone
from backend.domain.adstudio.planner import Planner, ReferenceFacts, Resolution, parse_facts
from backend.domain.adstudio.profile import ensure_reference_facts
from backend.domain.adstudio.prompting import render_ad_prompt
from backend.domain.adstudio.repair import Repairer, RepairPlan, RepairState
from backend.domain.adstudio.schemas import image_url
from backend.domain.adstudio.spec import CreativeSpec
from backend.domain.adstudio.vision import valid_box
from backend.llm.cache import CacheMissError
from backend.llm.calls import CallRuntime, SafetyBlockedError
from backend.llm.gateway import LlmGateway
from backend.llm.ledger import bind_run
from backend.llm.prompts.ad_generate import AD_GENERATE
from backend.storage.blobs import BlobStore

# Part of every golden key and batch label: bump it when a prompt or evaluator change must give the
# golden briefs new runs (orchestrator-2: ad_generate v3 / ad_repair_text v3 / evaluator ev-0.4;
# orchestrator-3: ad_inspect v2, label-subtext notes, stalled-repair stop, evaluator ev-0.5;
# orchestrator-4: ADR-007 realistic scale + photographed-in-scene prompt (ad_generate v4,
# creative_planner v2, product_profile v2), composition dimension and repair (evaluator ev-0.6);
# orchestrator-5: the headline copy on one prompt line (ad_generate v5 / ad_repair_text v4, the
# v4 line break was drawn as " / "), evaluator ev-0.7. The committed golden v1/v2 images are not
# regenerated for it.)
PIPELINE_VERSION = "orchestrator-6"  # ad_generate v6 / ad_repair_product v3 (colours)
log = structlog.get_logger(__name__)


@dataclass
class PipelineDeps:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    blobs: BlobStore
    runtime: CallRuntime
    image_client: ImageClient
    gateway: LlmGateway
    evaluator: Evaluator
    planner: Planner
    # Runs a user asked to cancel: their task's CancelledError ends them `cancelled`, not
    # `interrupted` (process shutdown).
    cancel_requests: set[uuid.UUID] = field(default_factory=set[uuid.UUID])


def pipeline_config(
    settings: Settings,
    client: ImageClient,
    *,
    fresh: bool,
    resolution: Resolution | None = None,
    input_flags: list[str] | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "image_client": client.name,
        "models": {
            "candidate": client.model_for(settings.image_model_candidate),
            "repair": client.model_for(settings.image_model_repair),
        },
        "n_candidates": settings.n_candidates,
        "max_repairs": settings.max_repairs,
        "text_repair_attempts": settings.text_repair_attempts,
        "budget": {
            "ad_usd": settings.ad_budget_usd,
            "max_image_calls": settings.max_image_calls,
            "wallclock_s": settings.ad_wallclock_s,
        },
        "fresh": fresh,
        "input_flags": input_flags or [],
    }
    if resolution is not None:
        config["resolution"] = resolution.to_json()
    return config


def composition_target(spec: CreativeSpec, facts: ReferenceFacts | None) -> CompositionTarget:
    """ADR-007: the product's real-world size and the spec's framing / scale range. Specs before
    cs-2 carry no size: it comes from the profile (or its category) and the default framing."""
    c = spec.composition
    size = facts.size() if facts else None
    return CompositionTarget(
        category=(facts.category or "") if facts else "",
        size_cm=c.size_cm if c.size_cm is not None else (size.cm if size else None),
        size_class=c.size_class or (size.size_class if size else None),
        framing=c.framing,
        scale_min=c.scale_min,
        scale_max=c.scale_max,
        scale_references=tuple(c.scale_references),
        resting_surface=c.resting_surface or (size.surface if size else None),
    )


def eval_target(spec: CreativeSpec, facts: ReferenceFacts | None = None) -> EvalTarget:
    box = valid_box(facts.box_2d) if facts and facts.box_2d else None
    summary = ""
    if facts and facts.category:
        colours = ", ".join(facts.dominant_colors[:4])
        summary = f"{facts.category}" + (f"; colours {colours}" if colours else "")
    return EvalTarget(
        aspect_ratio=spec.aspect_ratio,
        required_text=spec.required_text.raw,
        lines=tuple(spec.required_text.lines),
        text_zone=spec.text_zone.box,
        script=spec.required_text.script,
        country_code=spec.geo.country_code,
        ocr_langs=tuple(spec.geo.ocr_langs),
        text_mode=spec.required_text.mode,
        reference_box=box,
        reference_labels=tuple(facts.visible_text) if facts else (),
        product_summary=summary,
        context_checks=tuple(
            compile_context_checks(
                spec.rubric,
                spec.policy.avoid,
                avoid_severity=load_config().context.avoid_check_severity,
                people_severity=load_config().context.people_check_severity,
            )
        ),
        composition=composition_target(spec, facts),
    )


class RunLog:
    """Writes run state and its events; one short transaction per step."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
        self.sessionmaker = sessionmaker
        self.run_id = run_id

    async def _append(self, session: AsyncSession, event: BaseModel) -> int:
        payload = event_payload(event)
        row = await repo.append_run_event(session, self.run_id, str(payload["type"]), payload)
        return row.seq

    async def emit(self, event: BaseModel, **run_fields: Any) -> int:
        async with self.sessionmaker() as session:
            if run_fields:
                await repo.update_run(session, self.run_id, **run_fields)
            seq = await self._append(session, event)
            await session.commit()
            return seq

    async def status(self, status: RunStatus, **run_fields: Any) -> None:
        now = datetime.now(UTC)
        await self.emit(
            RunStatusEvent(run_id=self.run_id, status=status),
            status=status,
            heartbeat_at=now,
            **run_fields,
        )


@dataclass
class _Loaded:
    run: m.Run
    brief: m.Brief
    product: m.Product
    reference: bytes


async def _load(deps: PipelineDeps, run_id: uuid.UUID) -> _Loaded:
    async with deps.sessionmaker() as session:
        run = await session.get(m.Run, run_id)
        if run is None:
            raise AppError(404, "not-found", "Run not found")
        brief = await repo.get_brief(session, run.tenant_id, run.brief_id)
        product = (
            await repo.get_product(session, run.tenant_id, brief.product_id) if brief else None
        )
        image = await repo.get_image(session, run.tenant_id, product.image_id) if product else None
    if brief is None or product is None or image is None:
        raise AppError(422, "validation-error", "Run is missing its brief or product")
    return _Loaded(run, brief, product, deps.blobs.read(image.sha256))


@dataclass
class Scored:
    """A candidate with its image bytes and evaluation (one entry of the selection pool)."""

    candidate: m.Candidate
    image: m.Image
    data: bytes
    evaluation: Evaluation
    order: int

    @property
    def kind(self) -> str:
        return self.candidate.kind


NON_TEXT: tuple[Dimension, ...] = ("technical", "product", "context", "composition")
ACTION_KIND: dict[str, CandidateKind] = {
    "regenerate": "initial",
    "clean_plate": "clean_plate",
    "repair_product": "repair",
    "repair_context": "repair",
    "repair_composition": "repair",
    "repair_text": "repair",
}
ACTION_TARGET: dict[str, Dimension] = {
    "regenerate": "technical",
    "clean_plate": "text",
    "repair_product": "product",
    "repair_context": "context",
    "repair_composition": "composition",
    "repair_text": "text",
}
IMAGE_SOURCE: dict[str, str] = {
    "initial": "generated",
    "clean_plate": "generated",
    "repair": "repair",
    "overlay": "overlay",
}
OVERLAY_MODEL = "local:pillow-overlay"
OVERLAY_VERSION = "overlay@1"
MAX_STEPS = 12  # hard guard on the repair loop; the repair and call caps end it far earlier


def select_best(pool: list[Scored]) -> Scored | None:
    """ai-design §4.2: among candidates passing every dimension, the highest composite; ties go to
    candidate order."""
    passing = [s for s in pool if s.evaluation.passed]
    if not passing:
        return None
    return max(passing, key=lambda s: (s.evaluation.composite, -s.order))


def _non_text_failures(evaluation: Evaluation) -> int:
    dims = evaluation.dimensions
    technical = dims.get("technical")
    if technical is None or technical.passed is not True:
        return len(NON_TEXT)  # nothing else could be judged
    return sum(1 for d in NON_TEXT if d in dims and dims[d].passed is False)


def choose_base(pool: list[Scored]) -> Scored:
    """ai-design §4.3: the base is the candidate with the fewest failed non-text dimensions, ties
    broken by composite, then by candidate order."""
    return min(
        pool,
        key=lambda s: (_non_text_failures(s.evaluation), -s.evaluation.composite, s.order),
    )


@dataclass
class _Outcome:
    status: TerminalStatus
    reason: str
    outcome: str | None = None
    approved: Scored | None = None
    best: Scored | None = None


class Orchestrator:
    """One run's state machine (ADR-001): plan -> N candidates -> evaluate -> select / repair ->
    overlay -> terminal. Every step writes its rows and events before the next starts."""

    def __init__(
        self,
        deps: PipelineDeps,
        log_: RunLog,
        loaded: _Loaded,
        spec: CreativeSpec,
        facts: ReferenceFacts | None,
    ) -> None:
        settings = deps.settings
        self.deps = deps
        self.log = log_
        self.loaded = loaded
        self.spec = spec
        self.facts = facts
        self.target = eval_target(spec, facts)
        self.run_id = loaded.run.id
        self.client = deps.image_client
        self.generator = Generator(
            deps.image_client, deps.blobs, deps.runtime, timeout_s=settings.image_timeout_s
        )
        self.repairer = Repairer.from_settings(settings)
        self.overlay = Overlay()
        self.budget = RunBudget(
            cap_usd=settings.ad_budget_usd,
            max_image_calls=settings.max_image_calls,
            deadline_s=settings.ad_wallclock_s,
        )
        self.salt = str(self.run_id) if bool(loaded.run.config.get("fresh")) else ""
        self.pool: list[Scored] = []
        self.order = 0
        self.repairs_used = 0
        self.text_repairs_used = 0
        self.budget_stop: str | None = None
        # Set when a repair's output is >= repair.stall_ssim similar to its parent: no further
        # image-model repairs (the overlay, which calls no model, may still run).
        self.stalled_ssim: float | None = None

    # --- budget ------------------------------------------------------------------------------

    async def _refresh_spend(self) -> None:
        async with self.deps.sessionmaker() as session:
            self.budget.other_usd = await repo.run_cost_usd(
                session, self.run_id, exclude_kind="image"
            )

    async def _budget_event(self, reason: Literal["threshold", "exhausted", "deadline"]) -> None:
        await self.log.emit(
            BudgetWarningEvent(
                run_id=self.run_id,
                spent_usd=self.budget.spent_usd,
                cap_usd=self.budget.cap_usd,
                reason=reason,
                image_calls=self.budget.image_calls,
                max_image_calls=self.budget.max_image_calls,
            )
        )

    async def _reserve(self, model: str) -> float | None:
        """Reserve one image call at list price, or record why it can't be made (None)."""
        await self._refresh_spend()
        blocker = self.budget.blocker(model)
        if blocker is not None:
            if self.budget_stop is None:
                self.budget_stop = blocker
                await self._budget_event("deadline" if blocker == "deadline" else "exhausted")
            return None
        price = self.budget.reserve(model)
        if self.budget.should_warn():
            await self._budget_event("threshold")
        return price

    # --- generation --------------------------------------------------------------------------

    async def _generate(
        self, request: ImageRequest, *, model: str, prompt_name: str, prompt_version: str, slot: int
    ) -> GeneratedImage:
        """One image call (price already reserved). If the model is unavailable (breaker open,
        retries exhausted) and the other image model fits the budget, it is tried once."""
        try:
            return await self.generator.generate(
                request,
                model=model,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                slot=slot,
                salt=self.salt,
            )
        except (SafetyBlockedError, CacheMissError, asyncio.CancelledError):
            raise
        except Exception as exc:
            settings = self.deps.settings
            other = (
                settings.image_model_repair
                if model == settings.image_model_candidate
                else settings.image_model_candidate
            )
            if other == model or await self._reserve(other) is None:
                raise
            log.warning("image_model_fallback", model=model, error=type(exc).__name__)
            return await self.generator.generate(
                request,
                model=other,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                slot=slot,
                salt=self.salt,
            )

    async def _store(
        self,
        *,
        sha256: str,
        width: int,
        height: int,
        size: int,
        mime: str,
        kind: CandidateKind,
        attempt: int,
        slot: int,
        parent: Scored | None,
        requested_model: str | None,
        served_model: str,
        prompt_version: str,
        instruction: str | None,
        cached: bool = False,
        native: tuple[int, int] | None = None,
    ) -> tuple[m.Candidate, m.Image]:
        async with self.deps.sessionmaker() as session:
            image, _ = await repo.get_or_create_image(
                session,
                self.loaded.run.tenant_id,
                sha256=sha256,
                source=IMAGE_SOURCE[kind],
                mime=mime,
                width=width,
                height=height,
                size=size,
            )
            candidate = await repo.create_candidate(
                session,
                self.run_id,
                image_id=image.id,
                parent_candidate_id=parent.candidate.id if parent else None,
                kind=kind,
                attempt=attempt,
                slot=slot,
                requested_model=requested_model,
                served_model=served_model,
                prompt_version=prompt_version,
                repair_instruction=instruction,
                status="pending",
            )
            await session.commit()
        await self.log.emit(
            CandidateCreatedEvent(
                run_id=self.run_id,
                candidate_id=candidate.id,
                attempt=attempt,
                slot=slot,
                kind=kind,
                parent_candidate_id=candidate.parent_candidate_id,
                image_id=image.id,
                image_url=image_url(image.id),
                width=width,
                height=height,
                native_width=native[0] if native else width,
                native_height=native[1] if native else height,
                model=served_model,
                cached=cached,
            )
        )
        return candidate, image

    async def _store_blocked(
        self,
        exc: SafetyBlockedError,
        *,
        model: str,
        kind: CandidateKind,
        attempt: int,
        slot: int,
        parent: Scored | None,
        prompt_version: str,
    ) -> None:
        async with self.deps.sessionmaker() as session:
            blocked = await repo.create_candidate(
                session,
                self.run_id,
                parent_candidate_id=parent.candidate.id if parent else None,
                kind=kind,
                attempt=attempt,
                slot=slot,
                requested_model=self.client.model_for(model),
                prompt_version=prompt_version,
                status="blocked",
            )
            await session.commit()
        await self.log.emit(
            CandidateCreatedEvent(
                run_id=self.run_id,
                candidate_id=blocked.id,
                attempt=attempt,
                slot=slot,
                kind=kind,
                parent_candidate_id=blocked.parent_candidate_id,
                status="blocked",
                model=blocked.requested_model or "",
                reason=f"generation_blocked: {exc.reason}"[:200],
            )
        )

    def _target_for(self, text_mode: str) -> EvalTarget:
        if text_mode == "overlay" and self.target.text_mode != "overlay_only":
            # A clean plate: no headline is expected until the overlay draws it.
            return dataclasses.replace(self.target, text_mode="overlay_only")
        return self.target

    async def _persist_evaluation(self, scored: Scored) -> None:
        await _store_evaluation(
            self.deps,
            self.log,
            self.run_id,
            scored.candidate,
            scored.image,
            scored.evaluation,
        )

    # --- steps -------------------------------------------------------------------------------

    async def first_round(self) -> None:
        """N candidates on the candidate model in parallel (each price reserved first), then
        their evaluations in parallel. Rows and events are written in slot order."""
        settings = self.deps.settings
        model = settings.image_model_candidate
        request = ImageRequest(
            prompt=render_ad_prompt(prompt_fields(self.spec, self.facts)),
            images=(self.loaded.reference,),
            aspect_ratio=self.spec.aspect_ratio,
            scene=self.spec.scene(),
            label="candidate",
        )
        prices: list[float] = []
        for _ in range(settings.n_candidates):
            price = await self._reserve(model)
            if price is None:
                break
            prices.append(price)
        await self.log.status("generating")
        results = await asyncio.gather(
            *(
                self._generate(
                    dataclasses.replace(request, slot=slot),
                    model=model,
                    prompt_name=AD_GENERATE.name,
                    prompt_version=AD_GENERATE.version,
                    slot=slot,
                )
                for slot in range(len(prices))
            ),
            return_exceptions=True,
        )
        stored: list[tuple[m.Candidate, m.Image, GeneratedImage]] = []
        prompt_version = f"{AD_GENERATE.name}@{AD_GENERATE.version}"
        for slot, result in enumerate(results):
            if isinstance(result, SafetyBlockedError):
                await self._store_blocked(
                    result,
                    model=model,
                    kind="initial",
                    attempt=0,
                    slot=slot,
                    parent=None,
                    prompt_version=prompt_version,
                )
                continue
            if isinstance(result, BaseException):
                raise result
            if result.cached:
                self.budget.release(prices[slot])
            candidate, image = await self._store(
                sha256=result.sha256,
                width=result.width,
                height=result.height,
                size=result.size,
                mime=result.mime,
                kind="initial",
                attempt=0,
                slot=slot,
                parent=None,
                requested_model=result.requested_model,
                served_model=result.served_model,
                prompt_version=f"{result.prompt_name}@{result.prompt_version}",
                instruction=None,
                cached=result.cached,
                native=(result.native_width, result.native_height),
            )
            stored.append((candidate, image, result))
        if not stored:
            return
        await self.log.status("evaluating")
        datas = [self.deps.blobs.read(g.sha256) for _, _, g in stored]
        evaluations = await asyncio.gather(
            *(
                self.deps.evaluator.evaluate(data, self.loaded.reference, self.target)
                for data in datas
            )
        )
        for (candidate, image, _), data, evaluation in zip(stored, datas, evaluations, strict=True):
            scored = Scored(candidate, image, data, evaluation, self.order)
            self.order += 1
            await self._persist_evaluation(scored)
            self.pool.append(scored)

    async def repair(self, plan: RepairPlan, base: Scored) -> None:
        """One routed image call (regenerate, targeted edit or clean plate), then evaluate it."""
        if plan.model is None or plan.prompt_name is None:
            raise ValueError(f"plan {plan.action!r} makes no image call")
        attempt = self.repairs_used + 1
        kind = ACTION_KIND[plan.action]
        await self.log.emit(
            RepairStartedEvent(
                run_id=self.run_id,
                from_candidate_id=base.candidate.id,
                target_dimension=ACTION_TARGET[plan.action],
                instruction=plan.reason,
                action=plan.action,
                row=plan.row,
                model=self.client.model_for(plan.model),
                attempt=attempt,
            )
        )
        await self.log.status("repairing", repair_count=attempt)
        price = await self._reserve(plan.model)
        self.repairs_used += 1
        self.text_repairs_used += int(plan.counts_as_text_repair)
        if price is None:  # checked by the caller; the deadline may have passed in between
            return
        request = Repairer.request(
            plan, self.spec, reference=self.loaded.reference, candidate=base.data
        )
        prompt_version = f"{plan.prompt_name}@{plan.prompt_version}"
        try:
            generated = await self._generate(
                request,
                model=plan.model,
                prompt_name=plan.prompt_name,
                prompt_version=plan.prompt_version or "",
                slot=attempt,
            )
        except SafetyBlockedError as exc:
            await self._store_blocked(
                exc,
                model=plan.model,
                kind=kind,
                attempt=attempt,
                slot=0,
                parent=base,
                prompt_version=prompt_version,
            )
            return
        if generated.cached:
            self.budget.release(price)
        candidate, image = await self._store(
            sha256=generated.sha256,
            width=generated.width,
            height=generated.height,
            size=generated.size,
            mime=generated.mime,
            kind=kind,
            attempt=attempt,
            slot=0,
            parent=base if plan.action != "regenerate" else None,
            requested_model=generated.requested_model,
            served_model=generated.served_model,
            prompt_version=prompt_version,
            instruction=plan.reason,
            cached=generated.cached,
            native=(generated.native_width, generated.native_height),
        )
        await self.log.status("evaluating")
        data = self.deps.blobs.read(generated.sha256)
        evaluation = await self.deps.evaluator.evaluate(
            data, self.loaded.reference, self._target_for(plan.text_mode)
        )
        scored = Scored(candidate, image, data, evaluation, self.order)
        self.order += 1
        await self._persist_evaluation(scored)
        self.pool.append(scored)
        if plan.action != "regenerate" and not evaluation.passed:
            similarity = image_similarity(data, base.data)
            stall = self.deps.evaluator.config.repair.stall_ssim
            if similarity is not None and similarity >= stall:
                self.stalled_ssim = round(similarity, 4)
                log.info(
                    "repair_stalled",
                    run_id=str(self.run_id),
                    candidate_id=str(candidate.id),
                    ssim=self.stalled_ssim,
                    threshold=stall,
                )

    async def apply_overlay(self, plan: RepairPlan, base: Scored) -> _Outcome:
        """Deterministic text overlay into the zone, OCR verification and re-evaluation."""
        await self.log.status("fallback")
        zone = self.spec.text_zone
        raw = self.spec.required_text.raw
        try:
            result = self.overlay.render(
                base.data,
                OverlayZone(
                    box=zone.box,
                    band_color=zone.band_color,
                    text_color=zone.text_color,
                    max_lines=zone.max_lines,
                ),
                raw,
            )
        except OverlayRefusedError as exc:
            return _Outcome("needs_review", exc.reason, best=base)
        verification = await self.overlay.verify(
            result,
            raw,
            zone.box,
            ocr=self.deps.evaluator.ocr,
            cfg=self.deps.evaluator.config.text,
            market_langs=tuple(self.spec.geo.ocr_langs),
            ocr2=self.deps.evaluator.ocr2,
        )
        stored = self.deps.blobs.put_generated(result.data)
        candidate, image = await self._store(
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
            size=stored.size,
            mime=stored.mime,
            kind="overlay",
            attempt=self.repairs_used + 1,
            slot=0,
            parent=base,
            requested_model=None,
            served_model=OVERLAY_MODEL,
            prompt_version=OVERLAY_VERSION,
            instruction=f"{plan.row}: {plan.reason}",
        )
        await self.log.emit(
            FallbackAppliedEvent(
                run_id=self.run_id,
                candidate_id=candidate.id,
                from_candidate_id=base.candidate.id,
                reason=plan.reason,
                verification=verification,
            )
        )
        await self.log.status("evaluating")
        evaluation = await self.deps.evaluator.evaluate_overlay(
            result.data,
            self.loaded.reference,
            dataclasses.replace(self.target, text_mode="native"),
            base=base.evaluation,
            verification=verification,
        )
        scored = Scored(candidate, image, result.data, evaluation, self.order)
        self.order += 1
        await self._persist_evaluation(scored)
        self.pool.append(scored)
        if verification != "failed" and evaluation.passed:
            return _Outcome("passed", "overlay", outcome="overlay", approved=scored)
        why = "overlay_verify_failed" if verification == "failed" else "overlay_evaluation_failed"
        return _Outcome("needs_review", why, best=scored)

    def _plan(self, base: Scored, *, can_afford: bool) -> RepairPlan:
        return self.repairer.plan(
            base.evaluation,
            self.spec,
            facts=self.facts,
            state=RepairState(
                repairs_used=self.repairs_used,
                text_repairs_used=self.text_repairs_used,
                can_afford_image_call=can_afford,
            ),
        )

    async def loop(self) -> _Outcome:
        """Select a passing candidate or route the best base to its next action (§4.2-4.3)."""
        for _ in range(MAX_STEPS):
            best = select_best(self.pool)
            if best is not None:
                outcome = "overlay" if best.kind == "overlay" else "native"
                return _Outcome("passed", "passed", outcome=outcome, approved=best)
            base = choose_base(self.pool)
            if self.budget.expired:
                if self.budget_stop is None:
                    self.budget_stop = "deadline"
                    await self._budget_event("deadline")
                return _Outcome("needs_review", "deadline", best=base)
            can_call = self.budget_stop is None and self.stalled_ssim is None
            plan = self._plan(base, can_afford=can_call)
            if plan.calls_model and plan.model is not None:
                await self._refresh_spend()
                if not self.budget.can_afford(plan.model):
                    await self._reserve(plan.model)  # records the stop and the warning
                    plan = self._plan(base, can_afford=False)
            if plan.action == "approve":  # unreachable: approve implies a passing candidate
                return _Outcome("passed", "passed", outcome="native", approved=base)
            if plan.action == "needs_review":
                reason = plan.row
                if plan.row == "exhausted" and self.budget_stop is not None:
                    reason = self.budget_stop  # budget | image_calls | deadline
                elif plan.row == "exhausted" and self.stalled_ssim is not None:
                    reason = "repair_stalled"
                return _Outcome("needs_review", reason, best=base)
            if plan.action == "overlay":
                return await self.apply_overlay(plan, base)
            await self.repair(plan, base)
        return _Outcome("needs_review", "step_limit", best=choose_base(self.pool))

    async def regenerate_after_block(self) -> None:
        """Every first-round call was blocked: one fresh candidate (counts as a repair)."""
        settings = self.deps.settings
        model = settings.image_model_candidate
        if self.repairs_used >= self.repairer.max_repairs:
            return
        await self._refresh_spend()
        if not self.budget.can_afford(model):
            await self._reserve(model)
            return
        request = ImageRequest(
            prompt=render_ad_prompt(prompt_fields(self.spec, self.facts)),
            images=(self.loaded.reference,),
            aspect_ratio=self.spec.aspect_ratio,
            scene=self.spec.scene(),
            label="regenerate",
        )
        plan = RepairPlan(
            action="regenerate",
            row="technical",
            reason="the model returned no image: generate a fresh candidate",
            base_image_sha="",
            model=model,
            prompt_name=AD_GENERATE.name,
            prompt_version=AD_GENERATE.version,
            prompt=request.prompt,
            inputs="reference",
            counts_as_repair=True,
        )
        self.repairs_used += 1
        attempt = self.repairs_used
        await self.log.status("repairing", repair_count=attempt)
        price = self.budget.reserve(model)
        try:
            generated = await self._generate(
                dataclasses.replace(request, slot=attempt),
                model=model,
                prompt_name=AD_GENERATE.name,
                prompt_version=AD_GENERATE.version,
                slot=attempt,
            )
        except SafetyBlockedError as exc:
            await self._store_blocked(
                exc,
                model=model,
                kind="initial",
                attempt=attempt,
                slot=0,
                parent=None,
                prompt_version=f"{AD_GENERATE.name}@{AD_GENERATE.version}",
            )
            return
        if generated.cached:
            self.budget.release(price)
        candidate, image = await self._store(
            sha256=generated.sha256,
            width=generated.width,
            height=generated.height,
            size=generated.size,
            mime=generated.mime,
            kind="initial",
            attempt=attempt,
            slot=0,
            parent=None,
            requested_model=generated.requested_model,
            served_model=generated.served_model,
            prompt_version=f"{AD_GENERATE.name}@{AD_GENERATE.version}",
            instruction=plan.reason,
            cached=generated.cached,
            native=(generated.native_width, generated.native_height),
        )
        await self.log.status("evaluating")
        data = self.deps.blobs.read(generated.sha256)
        evaluation = await self.deps.evaluator.evaluate(data, self.loaded.reference, self.target)
        scored = Scored(candidate, image, data, evaluation, self.order)
        self.order += 1
        await self._persist_evaluation(scored)
        self.pool.append(scored)

    async def run(self) -> _Outcome:
        await self.first_round()
        if not self.pool and self.budget_stop is None:
            await self.regenerate_after_block()
        if not self.pool:
            reason = self.budget_stop or "generation_blocked"
            return _Outcome("needs_review", reason)
        return await self.loop()


async def _store_evaluation(
    deps: PipelineDeps,
    log_: RunLog,
    run_id: uuid.UUID,
    candidate: m.Candidate,
    image: m.Image,
    evaluation: Evaluation,
) -> uuid.UUID:
    dims = evaluation.dimensions
    async with deps.sessionmaker() as session:
        row = await repo.upsert_evaluation(
            session,
            image_id=image.id,
            run_id=run_id,
            candidate_id=candidate.id,
            evaluator_version=evaluation.evaluator_version,
            checks=[
                {
                    "dimension": c.dimension,
                    "check_name": c.name,
                    "method": c.method,
                    "value": c.value,
                    "threshold": c.threshold,
                    "passed": c.passed,
                    "evidence": c.evidence or None,
                    "evidence_data": c.data,
                }
                for c in evaluation.checks
            ],
            text_pass=dims["text"].passed if "text" in dims else None,
            product_pass=dims["product"].passed if "product" in dims else None,
            context_pass=dims["context"].passed if "context" in dims else None,
            composition_pass=dims["composition"].passed if "composition" in dims else None,
            technical_pass=dims["technical"].passed if "technical" in dims else None,
            overall_pass=evaluation.passed,
            verdict=evaluation.verdict,
            cost_usd=evaluation.cost_usd,
            latency_ms=evaluation.latency_ms,
        )
        await repo.update_candidate(
            session, candidate.id, status="passed" if evaluation.passed else "failed"
        )
        await session.commit()
        evaluation_id = row.id
    await log_.emit(
        EvaluationDoneEvent(
            run_id=run_id,
            candidate_id=candidate.id,
            evaluation_id=evaluation_id,
            verdict=evaluation.verdict,
            dimensions={
                name: DimensionView(passed=d.passed, reasons=d.reasons) for name, d in dims.items()
            },
        )
    )
    return evaluation_id


async def _finish(
    deps: PipelineDeps,
    log_: RunLog,
    run: m.Run,
    status: TerminalStatus,
    *,
    outcome: str | None = None,
    approved_candidate_id: uuid.UUID | None = None,
    best_candidate_id: uuid.UUID | None = None,
    reason: str | None = None,
    error: dict[str, Any] | None = None,
    **run_fields: Any,
) -> None:
    now = datetime.now(UTC)
    latency_ms = int((now - run.created_at).total_seconds() * 1000)
    async with deps.sessionmaker() as session:
        cost = await repo.run_cost_usd(session, run.id)
    await log_.emit(
        RunFinishedEvent(
            run_id=run.id,
            status=status,
            outcome="overlay" if outcome == "overlay" else ("native" if outcome else None),
            approved_candidate_id=approved_candidate_id,
            best_candidate_id=best_candidate_id,
            cost_usd=round(cost, 6),
            latency_ms=latency_ms,
            reason=reason,
        ),
        status=status,
        outcome=outcome,
        approved_candidate_id=approved_candidate_id,
        best_candidate_id=best_candidate_id,
        cost_usd=cost,
        latency_ms=latency_ms,
        finished_at=now,
        heartbeat_at=now,
        error=error,
        **run_fields,
    )


async def _plan_run(
    deps: PipelineDeps, log_: RunLog, loaded: _Loaded
) -> tuple[CreativeSpec, ReferenceFacts | None]:
    run, brief = loaded.run, loaded.brief
    await log_.status("planning")
    # Product profile: computed at upload when a judge was configured, else lazily here.
    async with deps.sessionmaker() as session:
        product = await session.get(m.Product, loaded.product.id)
        raw_facts = (
            await ensure_reference_facts(session, product, loaded.reference, deps.evaluator.vision)
            if product is not None
            else None
        )
    facts = parse_facts(raw_facts)
    stored: Any = run.config.get("resolution")
    resolution = (
        Resolution.from_json(cast(dict[str, Any], stored))
        if isinstance(stored, dict)
        else await deps.planner.resolve(brief.geography_code, brief.geography_detail, brief.season)
    )
    spec = await deps.planner.plan(
        resolution=resolution,
        required_text=brief.required_text,
        aspect_ratio=brief.aspect_ratio,
        facts=facts,
        product_key=str(loaded.product.id),
    )
    await log_.emit(
        PlanDoneEvent(run_id=run.id, spec_summary=spec.summary()),
        spec=spec.model_dump(mode="json"),
        spec_version=spec.spec_version,
    )
    return spec, facts


async def _run_pipeline(deps: PipelineDeps, run_id: uuid.UUID) -> None:
    loaded = await _load(deps, run_id)
    log_ = RunLog(deps.sessionmaker, run_id)
    spec, facts = await _plan_run(deps, log_, loaded)
    orchestrator = Orchestrator(deps, log_, loaded, spec, facts)
    result = await orchestrator.run()
    first_round = [s for s in orchestrator.pool if s.candidate.attempt == 0]
    first_attempt_pass = any(s.evaluation.passed for s in first_round)
    fields: dict[str, Any] = {
        "first_attempt_pass": first_attempt_pass,
        "repair_count": orchestrator.repairs_used,
    }
    if result.status == "passed" and result.approved is not None:
        await _finish(
            deps,
            log_,
            loaded.run,
            "passed",
            outcome=result.outcome,
            approved_candidate_id=result.approved.candidate.id,
            best_candidate_id=result.approved.candidate.id,
            reason=result.reason,
            **fields,
        )
        return
    best = result.best
    await _finish(
        deps,
        log_,
        loaded.run,
        "needs_review",
        best_candidate_id=best.candidate.id if best else None,
        reason=result.reason,
        **fields,
    )


def _problem(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, AppError):
        return {"type": exc.type, "title": exc.title, "status": exc.status, "detail": exc.detail}
    return {
        "type": "internal-error",
        "title": "Run failed",
        "status": 500,
        "detail": f"The run stopped with {type(exc).__name__}.",
    }


async def _mark_ended(
    deps: PipelineDeps, run_id: uuid.UUID, status: TerminalStatus, exc: BaseException | None
) -> None:
    log_ = RunLog(deps.sessionmaker, run_id)
    problem = _problem(exc) if exc is not None else None
    async with deps.sessionmaker() as session:
        run = await session.get(m.Run, run_id)
    if run is None or run.status in m.RUN_TERMINAL_STATUSES:
        return
    if problem is not None:
        await log_.emit(RunErrorEvent(run_id=run_id, problem=problem))
    await _finish(
        deps, log_, run, status, reason=problem["type"] if problem else status, error=problem
    )


async def mark_interrupted(deps: PipelineDeps, run_id: uuid.UUID) -> None:
    """Startup recovery: the process that owned this run died mid-run."""
    exc = AppError(
        503, "run-interrupted", "Run interrupted", "The API restarted during this run; re-run it."
    )
    await _mark_ended(deps, run_id, "interrupted", exc)


async def mark_cancelled(deps: PipelineDeps, run_id: uuid.UUID) -> None:
    """A user cancelled the run (`POST /v1/runs/{id}/cancel`); no-op once it is terminal."""
    await _mark_ended(deps, run_id, "cancelled", None)


async def execute_run(run_id: uuid.UUID, deps: PipelineDeps) -> None:
    """Run the pipeline for one run; never raises (except cancellation), always ends terminal."""
    structlog.contextvars.bind_contextvars(run_id=str(run_id))
    with bind_run(run_id):
        try:
            await _run_pipeline(deps, run_id)
        except asyncio.CancelledError:
            # A user cancel ends `cancelled`; a process shutdown `interrupted`. Record it, then
            # let the cancellation propagate.
            status: TerminalStatus = (
                "cancelled" if run_id in deps.cancel_requests else "interrupted"
            )
            await asyncio.shield(_mark_ended(deps, run_id, status, None))
            raise
        except Exception as exc:  # noqa: BLE001 - every failure ends the run as `failed`
            log.warning("run_failed", error_type=type(exc).__name__, exc_info=exc)
            try:
                await _mark_ended(deps, run_id, "failed", exc)
            except Exception as inner:  # noqa: BLE001 - the DB may be the thing that failed
                log.error("run_fail_record_failed", error_type=type(inner).__name__)
        finally:
            structlog.contextvars.unbind_contextvars("run_id")
