/**
 * Evaluator report helpers for `/evals` and `/batch`. Every number shown comes straight from the
 * report (`GET /v1/evals/summary`); these helpers only format it and look up the report's own
 * targets and met flags. Nothing is recomputed here (docs/ai-design.md §9.2 owns the formulas).
 */
import {
  evalsEvalItems,
  evalsEvalSummary,
  type Criterion,
  type DimensionSummary,
  type EvalItem,
  type EvalSummary,
  type GoldenComparison,
  type RateMetric,
  type VersionSummary,
} from "@/lib/api";
import { isProblem, problemCode, problemMessage } from "@/lib/problem";

/**
 * Display order: the three brief dimensions, composition (ADR-007), then technical (docs/ux.md
 * Evaluator layout). Reports, items and labels made before composition have no composition key.
 */
export const EVAL_DIMENSIONS = ["text", "product", "context", "composition", "technical"] as const;
export type EvalDimension = (typeof EVAL_DIMENSIONS)[number];
export type Outcome = "tp" | "fp" | "fn" | "tn";

/** Below this n a metric is shown as indicative (docs/ux.md states table). */
export const SMALL_N = 10;

// --- formatting -----------------------------------------------------------------------------

/** 0.8461… → "0.846"; em dash when the report has no value (undefined ratio). */
export function fmtRatio(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? "—" : v.toFixed(3);
}

/** 0.85 → "85%", 0.947… → "94.7%"; em dash when unknown. */
export function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const p = v * 100;
  return Math.abs(p - Math.round(p)) < 1e-9 ? `${Math.round(p)}%` : `${p.toFixed(1)}%`;
}

/** The exact stored value, for `title` tooltips next to rounded numbers. */
export function exact(v: number | null | undefined): string | undefined {
  return v == null ? undefined : String(v);
}

// --- report lookups -------------------------------------------------------------------------

export function criterion(summary: EvalSummary, id: string): Criterion | undefined {
  return summary.criteria.find((c) => c.id === id);
}

/** The report's target text for a metric id (e.g. `recall.text` → "≥ 90%"), with ≥ / ≤ glyphs. */
export function targetOf(summary: EvalSummary, id: string): string | undefined {
  const t = summary.targets[id] ?? criterion(summary, id)?.target;
  return t?.replace(/>=\s*/g, "≥ ").replace(/<=\s*/g, "≤ ");
}

/**
 * The number in a target string, for drawing a target tick: ">= 90%" → 0.9, "<= $0.25" → 0.25,
 * "<= 60 s" → 60. Null for "report" and anything without a number.
 */
export function targetNumber(target: string | undefined): number | null {
  const m = target?.match(/(-?\d+(?:\.\d+)?)\s*(%?)/);
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] === "%" ? n / 100 : n;
}

export type MetState = "met" | "not_met" | "not_measured" | "reported";

/** Met / not met as the report says it; "reported" for metrics with no target. */
export function metState(c: Pick<Criterion, "met" | "target"> | undefined): MetState {
  if (!c) return "reported";
  if (c.met === true) return "met";
  if (c.met === false) return "not_met";
  return c.target === "report" ? "reported" : "not_measured";
}

export const MET_LABEL: Record<MetState, string> = {
  met: "Met",
  not_met: "Not met",
  not_measured: "Not measured",
  reported: "Reported",
};

/** Positives (label FAIL) and negatives (label PASS) for one dimension. */
export function posNeg(d: DimensionSummary): { pos: number; neg: number } {
  const c = d.confusion;
  return { pos: (c.tp ?? 0) + (c.fn ?? 0), neg: (c.fp ?? 0) + (c.tn ?? 0) };
}

export interface ScoreRow {
  dimension: EvalDimension;
  metric: "recall" | "precision";
  value: number | null;
  target: string | undefined;
  state: MetState;
  n: number;
  note?: string;
}

/** Headline per-dimension recall and precision against the report's targets. */
export function scoreRows(summary: EvalSummary): ScoreRow[] {
  const rows: ScoreRow[] = [];
  for (const dimension of EVAL_DIMENSIONS) {
    const d = summary.per_dimension[dimension];
    if (!d) continue;
    for (const metric of ["recall", "precision"] as const) {
      const c = criterion(summary, `${metric}.${dimension}`);
      rows.push({
        dimension,
        metric,
        value: d[metric],
        target: targetOf(summary, `${metric}.${dimension}`),
        state: metState(c),
        n: d.n,
        note: c?.note,
      });
    }
  }
  return rows;
}

/** Criteria counted in the "N of M targets met" headline (those with a real target). */
export function criteriaTally(summary: EvalSummary): { met: number; notMet: number; notMeasured: number } {
  let met = 0;
  let notMet = 0;
  let notMeasured = 0;
  for (const c of summary.criteria) {
    const s = metState(c);
    if (s === "met") met += 1;
    else if (s === "not_met") notMet += 1;
    else if (s === "not_measured") notMeasured += 1;
  }
  return { met, notMet, notMeasured };
}

// --- planted failures -----------------------------------------------------------------------

export const MUTATION_INFO: Record<string, { label: string; how: string }> = {
  text_typo: { label: "Typo'd text", how: "Text zone inpainted and re-rendered with one character edit" },
  text_missing: { label: "Missing text", how: "Text boxes inpainted away" },
  text_stray: { label: "Stray text", how: "A gibberish word added outside the text zone" },
  product_recolour: { label: "Recoloured product", how: "Hue rotated 60–180° inside the product mask" },
  product_swap: { label: "Swapped product", how: "Product replaced with another golden product" },
  product_duplicate: { label: "Duplicated product", how: "A second scaled copy of the product pasted in" },
  product_logo_erased: { label: "Logo erased", how: "Logo or label region inpainted" },
  context_season: { label: "Season contradiction", how: "Generated with a contradicting season" },
  context_geo: { label: "Wrong market", how: "Generated with a wrong locale" },
  comp_oversize: { label: "Oversized product", how: "Product enlarged about 1.8× in place (capped below the text zone)" },
  comp_pasted: { label: "Pasted product", how: "Product cut out and pasted back without its contact shadow" },
  technical: { label: "Technical defect", how: "Oversized, wrong aspect, blank or truncated file" },
  control_good: { label: "Known-good control", how: "Unmodified or lightly re-encoded all-pass image" },
};

export function mutationLabel(m: string | null | undefined): string {
  if (!m) return "Unknown";
  return MUTATION_INFO[m]?.label ?? m.replace(/_/g, " ");
}

/** Whether the evaluator flagged every expected dimension on a planted item. */
export function plantedCaught(item: EvalItem, expected: readonly string[]): boolean {
  return expected.length > 0 && expected.every((d) => item.dimensions[d] === false);
}

// --- human agreement ------------------------------------------------------------------------

/** Overall human verdict: fail if any labelled dimension fails, pass if all labelled pass. */
export function humanOverall(label: EvalItem["label"]): "pass" | "fail" | null {
  if (!label) return null;
  const values = Object.values(label).filter((v): v is boolean => v !== null);
  if (values.length === 0) return null;
  return values.every(Boolean) ? "pass" : "fail";
}

/** Human-labelled natural items whose overall label differs from the evaluator's verdict. */
export function disagreements(items: readonly EvalItem[]): EvalItem[] {
  return items.filter((i) => {
    if (i.label_source !== "human") return false;
    const human = humanOverall(i.label);
    return human !== null && human !== i.verdict;
  });
}

// --- loading --------------------------------------------------------------------------------

export type Loaded<T> =
  | { kind: "ok"; data: T }
  | { kind: "no-report"; message: string }
  | { kind: "error"; message: string };

function fromError(error: unknown, status: number | undefined): Loaded<never> {
  if (isProblem(error) && problemCode(error) === "no-eval-report") {
    return { kind: "no-report", message: problemMessage(error) };
  }
  if (status === 404) return { kind: "no-report", message: "No eval report yet." };
  return { kind: "error", message: isProblem(error) ? problemMessage(error) : "The API returned an error." };
}

export const API_DOWN = "Can't reach Ad Studio's API. Is `make dev` running?";

/** Golden dataset versions the switchers offer (ADR-007: v1 baseline, v2 composition run). */
export const GOLDEN_VERSIONS = ["v1", "v2"] as const;
export type GoldenVersion = (typeof GOLDEN_VERSIONS)[number];

export function asGoldenVersion(v: unknown): GoldenVersion | null {
  return typeof v === "string" && (GOLDEN_VERSIONS as readonly string[]).includes(v) ? (v as GoldenVersion) : null;
}

/** The latest report, of one golden version when given (else the newest of any version). */
export async function loadSummary(goldenVersion?: string | null): Promise<Loaded<EvalSummary>> {
  try {
    const r = await evalsEvalSummary(goldenVersion ? { query: { golden_version: goldenVersion } } : undefined);
    if (r.data) return { kind: "ok", data: r.data };
    return fromError(r.error, r.response?.status);
  } catch {
    return { kind: "error", message: API_DOWN };
  }
}

/** Every item of the latest report for an origin (pages of 100 until `next_cursor` is null). */
export async function loadItems(
  origin: "natural" | "planted",
  goldenVersion?: string | null,
): Promise<Loaded<EvalItem[]>> {
  const out: EvalItem[] = [];
  let cursor: string | null | undefined;
  try {
    for (let page = 0; page < 20; page += 1) {
      const r = await evalsEvalItems({
        query: { origin, limit: 100, cursor: cursor ?? undefined, golden_version: goldenVersion ?? undefined },
      });
      if (!r.data) return fromError(r.error, r.response?.status);
      out.push(...r.data.items);
      cursor = r.data.next_cursor;
      if (!cursor) break;
    }
    return { kind: "ok", data: out };
  } catch {
    return { kind: "error", message: API_DOWN };
  }
}

// --- golden v1 -> v2 comparison (ADR-007) ---------------------------------------------------

export type ComparisonState =
  /** No comparison in the report (v2 not evaluated yet). */
  | { kind: "none" }
  /** v2 is in the comparison but has no natural outputs. */
  | { kind: "no-outputs"; versions: VersionSummary[] }
  | { kind: "ok"; versions: VersionSummary[]; labelled: Record<string, boolean> };

/** Whether a version has any human-labelled rate (n > 0) in either image set. */
export function hasHumanLabels(v: VersionSummary): boolean {
  return Object.values(v.human_pass_rates ?? {}).some((dims) => Object.values(dims).some((m) => (m.n ?? 0) > 0));
}

export function outputCount(v: VersionSummary): number {
  return (v.counts?.nat ?? 0) + (v.counts?.final ?? 0);
}

export function comparisonState(c: GoldenComparison | null | undefined): ComparisonState {
  const versions = c?.versions ?? [];
  if (versions.length < 2) return { kind: "none" };
  if (versions.slice(1).every((v) => outputCount(v) === 0)) return { kind: "no-outputs", versions };
  return { kind: "ok", versions, labelled: Object.fromEntries(versions.map((v) => [v.version, hasHumanLabels(v)])) };
}

/** "45% (9/20)" or an em dash when the rate has no items. */
export function fmtRate(m: RateMetric | null | undefined): string {
  if (!m || !m.n || m.rate == null) return "—";
  return `${fmtPct(m.rate)} (${m.ok ?? 0}/${m.n})`;
}

/** Percentage-point change between two rates, "+25 pp"; null when either is missing. */
export function ppDelta(from: RateMetric | null | undefined, to: RateMetric | null | undefined): string | null {
  if (!from?.n || !to?.n || from.rate == null || to.rate == null) return null;
  const d = Math.round((to.rate - from.rate) * 100);
  return `${d > 0 ? "+" : d < 0 ? "−" : "±"}${Math.abs(d)} pp`;
}
