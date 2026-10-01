import type {
  BriefView,
  CandidateView,
  CheckView,
  DimensionView,
  EvaluationDoneEvent,
  MetaRunEventsResponse,
  ProductScaleView,
  RunDetail,
  RunFinishedEvent,
  SpecSummary,
} from "@/lib/api";
import { API_URL } from "@/lib/api-config";

/** One event of the API's `RunEvent` union (see GET /v1/meta/run-events). */
export type RunEvent = MetaRunEventsResponse[number];
export type RunEventType = RunEvent["type"];

/**
 * `evaluation.done` may carry the per-check rows so the scorecard fills without a refetch. The
 * generated event has only the per-dimension summary today, so `checks` is read defensively.
 */
type EvaluationDoneCompat = EvaluationDoneEvent & { checks?: CheckView[] | null };

const EVENT_TYPES = new Set<string>([
  "run.status",
  "plan.done",
  "candidate.created",
  "evaluation.done",
  "repair.started",
  "fallback.applied",
  "budget.warning",
  "run.finished",
  "run.error",
] satisfies RunEventType[]);

export const TERMINAL_STATUSES = new Set([
  "passed",
  "needs_review",
  "failed",
  "interrupted",
  "cancelled",
  "approved",
  "rejected",
]);

export type Verdict = "pass" | "fail" | "unverified";
/** Display group for a candidate `kind`. */
export type CandidateRole = "candidate" | "repair" | "overlay";

export function candidateRole(kind: string): CandidateRole {
  if (kind === "repair" || kind === "clean_plate") return "repair";
  if (kind === "overlay") return "overlay";
  return "candidate"; // "initial" (current API) and "candidate"
}

/** Human word for a `kind` (the API calls first-round candidates "initial"). */
/**
 * The pipeline numbers the first round 0 and each repair 1, 2…; people count from 1, so the first
 * round reads "Attempt 1" and repair n reads "Attempt n + 1".
 */
export function attemptTitle(c: { attempt: number }): string {
  return `Attempt ${c.attempt + 1}`;
}

export function kindLabel(kind: string): string {
  switch (kind) {
    case "initial":
    case "candidate":
      return "candidate";
    case "clean_plate":
      return "clean plate";
    default:
      return kind.replaceAll("_", " ");
  }
}

export interface CandidateState {
  id: string;
  slot: number;
  attempt: number;
  kind: string;
  parentId: string | null;
  model: string | null;
  /** The model the pipeline asked for (snapshot only); differs from `model` after a fallback. */
  requestedModel: string | null;
  imageUrl: string | null;
  width: number | null;
  height: number | null;
  cached: boolean;
  status: string;
  reason: string | null;
  verdict: Verdict | null;
  dimensions: Record<string, DimensionView>;
  /** Per-check rows (snapshot, or `evaluation.done` once it carries them). Null = not loaded yet. */
  checks: CheckView[] | null;
  /** The instruction that produced this candidate, when it is a repair (snapshot). */
  repairInstruction: string | null;
  createdAt: string | null;
  evaluatedAt: string | null;
}

export interface RepairState {
  /** Null when rebuilt from the snapshot before the event log replays. */
  seq: number | null;
  at: string | null;
  fromCandidateId: string;
  targetDimension: string | null;
  /** Routing action (`repair_product`, `repair_composition`, `regenerate`…); null from the snapshot. */
  action: string | null;
  instruction: string | null;
  model: string | null;
  resultCandidateId: string | null;
}

export interface FallbackState {
  seq: number | null;
  at: string | null;
  /** The native candidate whose text failed, when known. */
  sourceCandidateId: string | null;
  /** The overlay candidate drawn by Ad Studio, when it exists. */
  resultCandidateId: string | null;
  reason: string | null;
}

export interface RunState {
  runId: string;
  /** From the snapshot; events don't carry the brief. */
  brief: BriefView | null;
  status: string;
  /** Every `run.status` transition with its time, in order. */
  statusHistory: { status: string; at: string }[];
  startedAt: string | null;
  plan: SpecSummary | null;
  planAt: string | null;
  /** The stored CreativeSpec (snapshot only): palette, critical tokens, composition. */
  spec: Record<string, unknown> | null;
  candidates: CandidateState[];
  repairs: RepairState[];
  fallbacks: FallbackState[];
  finished: RunFinishedEvent | null;
  /** Terminal reason code(s) from `run.finished` (or the snapshot's run error). */
  reason: string | null;
  outcome: string | null;
  error: Record<string, unknown> | null;
  costUsd: number | null;
  latencyMs: number | null;
  approvedCandidateId: string | null;
  /** The candidate to review: the approved one, or the best one of a held run. */
  bestCandidateId: string | null;
  /** Product of the brief (snapshot), for "Rerun for another market". */
  productId: string | null;
  /** Image models the run was configured with (snapshot `run.config.models`). */
  models: { candidate: string | null; repair: string | null } | null;
  budgetWarning: { spentUsd: number; capUsd: number; reason: string | null } | null;
}

export function emptyRunState(runId: string): RunState {
  return {
    runId,
    brief: null,
    status: "queued",
    statusHistory: [],
    startedAt: null,
    plan: null,
    planAt: null,
    spec: null,
    candidates: [],
    repairs: [],
    fallbacks: [],
    finished: null,
    reason: null,
    outcome: null,
    error: null,
    costUsd: null,
    latencyMs: null,
    approvedCandidateId: null,
    bestCandidateId: null,
    productId: null,
    models: null,
    budgetWarning: null,
  };
}

/** Parse one SSE `data:` payload; unknown or malformed events are dropped (null). */
export function parseRunEvent(data: string): RunEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (
      typeof value === "object" &&
      value !== null &&
      EVENT_TYPES.has((value as { type?: string }).type ?? "") &&
      typeof (value as { seq?: unknown }).seq === "number"
    ) {
      return value as RunEvent;
    }
  } catch {
    // fall through
  }
  return null;
}

/** Absolute URL for an API-relative path such as `/v1/images/{id}`. */
export function apiUrl(path: string): string {
  return /^https?:\/\//.test(path) ? path : `${API_URL}${path}`;
}

function upsertCandidate(
  list: CandidateState[],
  id: string,
  patch: (current: CandidateState | undefined) => CandidateState,
): CandidateState[] {
  const index = list.findIndex((c) => c.id === id);
  if (index === -1) return [...list, patch(undefined)];
  const next = list.slice();
  next[index] = patch(list[index]);
  return next;
}

function blankCandidate(id: string): CandidateState {
  return {
    id,
    slot: 0,
    attempt: 0,
    kind: "initial",
    parentId: null,
    model: null,
    requestedModel: null,
    imageUrl: null,
    width: null,
    height: null,
    cached: false,
    status: "pending",
    reason: null,
    verdict: null,
    dimensions: {},
    checks: null,
    repairInstruction: null,
    createdAt: null,
    evaluatedAt: null,
  };
}

function earliest(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) <= Date.parse(b) ? a : b;
}

/** Link a new repair/overlay candidate to the open repair or fallback it answers. */
function linkResult(state: RunState, c: CandidateState): RunState {
  const role = candidateRole(c.kind);
  if (role === "repair") {
    if (state.repairs.some((r) => r.resultCandidateId === c.id)) return state;
    const open = state.repairs
      .map((r, i) => ({ r, i }))
      .filter(({ r }) => r.resultCandidateId === null);
    const match = open.find(({ r }) => c.parentId && r.fromCandidateId === c.parentId) ?? open.at(-1);
    if (!match) return state;
    const repairs = state.repairs.slice();
    repairs[match.i] = { ...match.r, resultCandidateId: c.id };
    return { ...state, repairs };
  }
  if (role === "overlay") {
    if (state.fallbacks.some((f) => f.resultCandidateId === c.id)) return state;
    const i = state.fallbacks.findIndex((f) => f.resultCandidateId === null);
    if (i === -1) {
      // The overlay arrived before (or without) its fallback.applied event.
      return {
        ...state,
        fallbacks: [
          ...state.fallbacks,
          {
            seq: null,
            at: c.createdAt,
            sourceCandidateId: c.parentId,
            resultCandidateId: c.id,
            reason: null,
          },
        ],
      };
    }
    const fallbacks = state.fallbacks.slice();
    const f = fallbacks[i]!;
    fallbacks[i] = { ...f, resultCandidateId: c.id, sourceCandidateId: f.sourceCandidateId ?? c.parentId };
    return { ...state, fallbacks };
  }
  return state;
}

/** Pure reducer: fold one RunEvent into the run state. Applying the same event twice is harmless. */
export function applyRunEvent(state: RunState, ev: RunEvent): RunState {
  const startedAt = earliest(state.startedAt, ev.at);
  const base = startedAt === state.startedAt ? state : { ...state, startedAt };
  switch (ev.type) {
    case "run.status": {
      const seen = base.statusHistory.some((h) => h.status === ev.status && h.at === ev.at);
      return {
        ...base,
        status: ev.status,
        statusHistory: seen ? base.statusHistory : [...base.statusHistory, { status: ev.status, at: ev.at }],
      };
    }
    case "plan.done":
      return { ...base, plan: ev.spec_summary, planAt: ev.at };
    case "candidate.created": {
      const e = ev;
      let created: CandidateState | undefined;
      const candidates = upsertCandidate(base.candidates, e.candidate_id, (c) => {
        created = {
          ...(c ?? blankCandidate(e.candidate_id)),
          slot: e.slot,
          attempt: e.attempt,
          kind: e.kind,
          parentId: e.parent_candidate_id ?? c?.parentId ?? null,
          model: e.model,
          imageUrl: e.image_url,
          width: e.width,
          height: e.height,
          cached: e.cached,
          // Keep the snapshot's later status (e.g. "evaluated") when the log replays.
          status: c && c.status !== "pending" && e.status === "pending" ? c.status : e.status,
          reason: e.reason,
          createdAt: e.at,
        };
        return created;
      });
      return linkResult({ ...base, candidates }, created!);
    }
    case "evaluation.done": {
      const e = ev as EvaluationDoneCompat;
      return {
        ...base,
        candidates: upsertCandidate(base.candidates, e.candidate_id, (c) => ({
          ...(c ?? blankCandidate(e.candidate_id)),
          verdict: e.verdict,
          dimensions: e.dimensions,
          checks: e.checks ?? c?.checks ?? null,
          evaluatedAt: e.at,
        })),
      };
    }
    case "repair.started": {
      if (base.repairs.some((r) => r.seq === ev.seq)) return base;
      const fromSnapshot = base.repairs.findIndex(
        (r) => r.seq === null && r.fromCandidateId === ev.from_candidate_id,
      );
      const entry: RepairState = {
        seq: ev.seq,
        at: ev.at,
        fromCandidateId: ev.from_candidate_id,
        targetDimension: ev.target_dimension,
        action: ev.action ?? null,
        instruction: ev.instruction,
        model: ev.model ?? null,
        resultCandidateId: null,
      };
      if (fromSnapshot === -1) return { ...base, repairs: [...base.repairs, entry] };
      const repairs = base.repairs.slice();
      const prior = repairs[fromSnapshot]!;
      repairs[fromSnapshot] = { ...entry, resultCandidateId: prior.resultCandidateId };
      return { ...base, repairs };
    }
    case "fallback.applied": {
      if (base.fallbacks.some((f) => f.seq === ev.seq)) return base;
      const target = base.candidates.find((c) => c.id === ev.candidate_id);
      const isOverlay = target ? candidateRole(target.kind) === "overlay" : false;
      const i = base.fallbacks.findIndex(
        (f) =>
          f.seq === null &&
          (f.resultCandidateId === ev.candidate_id || f.sourceCandidateId === ev.candidate_id),
      );
      const entry: FallbackState = {
        seq: ev.seq,
        at: ev.at,
        sourceCandidateId:
          ev.from_candidate_id ??
          (isOverlay ? (target?.parentId ?? null) : ev.candidate_id),
        resultCandidateId: isOverlay ? ev.candidate_id : null,
        reason: ev.reason,
      };
      if (i === -1) return { ...base, fallbacks: [...base.fallbacks, entry] };
      const fallbacks = base.fallbacks.slice();
      const prior = fallbacks[i]!;
      fallbacks[i] = {
        ...entry,
        sourceCandidateId: entry.sourceCandidateId ?? prior.sourceCandidateId,
        resultCandidateId: entry.resultCandidateId ?? prior.resultCandidateId,
      };
      return { ...base, fallbacks };
    }
    case "budget.warning":
      return {
        ...base,
        budgetWarning: {
          spentUsd: ev.spent_usd,
          capUsd: ev.cap_usd,
          reason: ev.reason ?? null,
        },
      };
    case "run.finished":
      return {
        ...base,
        status: ev.status,
        finished: ev,
        reason: ev.reason,
        outcome: ev.outcome,
        costUsd: ev.cost_usd,
        latencyMs: ev.latency_ms,
        // A human decision after the run keeps the snapshot's approved candidate.
        approvedCandidateId: ev.approved_candidate_id ?? base.approvedCandidateId,
        bestCandidateId: ev.best_candidate_id ?? ev.approved_candidate_id ?? base.bestCandidateId,
      };
    case "run.error":
      return { ...base, error: ev.problem };
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}
function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** Best-effort plan summary from the stored CreativeSpec (the SSE `plan.done` replaces it). */
export function planFromSpec(spec: RunDetail["spec"]): SpecSummary | null {
  const root = asRecord(spec);
  if (!root) return null;
  const season = asRecord(root.season);
  const geo = asRecord(root.geo);
  const draft = asRecord(root.draft);
  const text = asRecord(root.required_text);
  const box = asRecord(asRecord(root.text_zone)?.box);
  const zone =
    box && ["x0", "y0", "x1", "y1"].every((k) => asNumber(box[k]) !== null)
      ? { x0: box.x0 as number, y0: box.y0 as number, x1: box.x1 as number, y1: box.y1 as number }
      : null;
  return {
    source: root.draft_source === "llm" ? "planner" : "default_table",
    effective_season: asString(season?.effective_season),
    hemisphere: asString(geo?.hemisphere),
    rationale: asString(season?.rationale),
    country_name: asString(geo?.country_name),
    planner_model: asString(root.planner_model),
    locale_cues: asStrings(draft?.locale_cues),
    avoid: asStrings(root.negatives),
    text_lines: asStrings(text?.lines),
    text_mode: text?.mode === "overlay_only" ? "overlay_only" : "native",
    text_zone: zone,
    product_scale: productScaleFromSpec(asRecord(root.composition)),
  };
}

/** `SpecSummary.product_scale` from a cs-2 spec's composition; null for older specs (no size). */
function productScaleFromSpec(comp: Record<string, unknown> | null): ProductScaleView | null {
  const framing = asString(comp?.framing);
  const min = asNumber(comp?.scale_min);
  const max = asNumber(comp?.scale_max);
  const size = asNumber(comp?.size_cm);
  const sizeClass = asString(comp?.size_class);
  if (!framing || min === null || max === null || size === null || !sizeClass) return null;
  return {
    framing,
    scale_min: min,
    scale_max: max,
    size_cm: size,
    size_class: sizeClass,
    resting_surface: asString(comp?.resting_surface),
    scale_references: asStrings(comp?.scale_references),
  };
}

/** Plan details only the stored spec has (the SSE summary omits them). */
export interface PlanExtras {
  aspect: string | null;
  palette: string[];
  criticalTokens: string[];
  setting: string | null;
  bandColor: string | null;
  productAnchor: string | null;
  productScale: number | null;
}

export function planExtras(spec: Record<string, unknown> | null): PlanExtras {
  const root = asRecord(spec);
  const draft = asRecord(root?.draft);
  const text = asRecord(root?.required_text);
  const zone = asRecord(root?.text_zone);
  const comp = asRecord(root?.composition);
  return {
    aspect: asString(comp?.aspect_ratio),
    palette: asStrings(draft?.palette),
    criticalTokens: asStrings(text?.critical_tokens),
    setting: asString(draft?.setting),
    bandColor: asString(zone?.band_color),
    productAnchor: asString(comp?.product_anchor),
    productScale: asNumber(comp?.product_scale),
  };
}

function candidateFromView(view: CandidateView): CandidateState {
  const dimensions: Record<string, DimensionView> = {};
  const evaluation = view.evaluation;
  if (evaluation) {
    for (const [dim, passed] of Object.entries(evaluation.dimensions)) {
      const reasons = evaluation.checks
        .filter((c) => c.dimension === dim && c.passed === false && c.evidence)
        .map((c) => c.evidence as string);
      dimensions[dim] = { passed, reasons };
    }
  }
  const verdict = evaluation?.verdict;
  return {
    ...blankCandidate(view.id),
    slot: view.slot,
    attempt: view.attempt,
    kind: view.kind,
    parentId: view.parent_candidate_id,
    model: view.served_model ?? view.requested_model,
    requestedModel: view.requested_model,
    imageUrl: view.image_url,
    width: view.width,
    height: view.height,
    status: view.status,
    verdict: verdict === "pass" || verdict === "fail" || verdict === "unverified" ? verdict : null,
    dimensions,
    checks: evaluation ? evaluation.checks : null,
    repairInstruction: view.repair_instruction,
    createdAt: view.created_at,
  };
}

function configModels(config: Record<string, unknown>): RunState["models"] {
  const models = asRecord(config.models);
  if (!models) return null;
  return { candidate: asString(models.candidate), repair: asString(models.repair) };
}

/** Rebuild the run state from the `GET /v1/runs/{id}` snapshot (reloads, dropped streams). */
export function runStateFromDetail(detail: RunDetail): RunState {
  const terminal = TERMINAL_STATUSES.has(detail.run.status);
  let state: RunState = {
    ...emptyRunState(detail.run.id),
    brief: detail.brief,
    status: detail.run.status,
    startedAt: detail.run.created_at,
    plan: planFromSpec(detail.spec),
    spec: asRecord(detail.spec),
    error: detail.run.error,
    reason: asString(detail.run.error?.type),
    outcome: detail.run.outcome,
    costUsd: detail.cost_usd,
    latencyMs: terminal ? detail.latency_ms : null,
    approvedCandidateId: detail.approved_candidate_id,
    bestCandidateId: detail.best_candidate_id ?? detail.approved_candidate_id,
    productId: detail.product_id,
    models: configModels(detail.run.config),
  };
  // Repairs and fallbacks are rebuilt from lineage; the event log (replayed on top) fills in the
  // target dimension, reason and timings.
  for (const view of detail.candidates) {
    const c = candidateFromView(view);
    const role = candidateRole(c.kind);
    if (role === "repair" && c.parentId) {
      state = {
        ...state,
        repairs: [
          ...state.repairs,
          {
            seq: null,
            at: c.createdAt,
            fromCandidateId: c.parentId,
            targetDimension: null,
            action: null,
            instruction: c.repairInstruction,
            model: c.model,
            resultCandidateId: null,
          },
        ],
      };
    }
    state = { ...state, candidates: [...state.candidates, c] };
    state = linkResult(state, c);
  }
  return state;
}

/**
 * The first image-model fallback in a run: a candidate served by a different model than the one
 * requested (snapshot), or than the run's configured model for its role (events).
 */
export function imageModelFallback(state: RunState): { requested: string | null; served: string } | null {
  for (const c of state.candidates) {
    const role = candidateRole(c.kind);
    if (role === "overlay" || !c.model) continue;
    if (c.requestedModel && c.requestedModel !== c.model) return { requested: c.requestedModel, served: c.model };
    const configured = role === "repair" ? state.models?.repair : state.models?.candidate;
    // Streamed candidates carry only the served model until the snapshot refresh adds the request.
    if (configured && c.model !== configured && !c.requestedModel) {
      return { requested: configured, served: c.model };
    }
  }
  return null;
}
