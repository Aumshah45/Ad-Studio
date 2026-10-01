import type { CheckView } from "@/lib/api";

export const DIMENSION_ORDER = ["technical", "text", "product", "context", "composition"] as const;
export const DIMENSION_LABEL: Record<string, string> = {
  technical: "Technical",
  text: "Text",
  product: "Product",
  context: "Context",
  composition: "Composition",
};

/** What a dimension checks, shown next to its name where there is room (ADR-007 for composition). */
export const DIMENSION_HINT: Record<string, string> = {
  technical: "file, size and aspect",
  text: "exact required text",
  product: "product fidelity",
  context: "season and market",
  composition: "realistic scale and natural integration",
};

/** "Composition — realistic scale and natural integration". */
export function dimensionTitle(dim: string): string {
  const label = DIMENSION_LABEL[dim] ?? dim;
  const hint = DIMENSION_HINT[dim];
  return hint ? `${label} — ${hint}` : label;
}

/**
 * Composition (ADR-007, evaluator ev-0.6) is newer than the other dimensions: evaluations and
 * labels made before it have no `composition` key. Those show "Not checked", never a failure.
 */
export const NOT_CHECKED = "Not checked";

export type CheckStatus = "pass" | "fail" | "unverified";

export function checkStatus(passed: boolean | null | undefined): CheckStatus {
  return passed === true ? "pass" : passed === false ? "fail" : "unverified";
}

/** Vision-model checks; everything else is a deterministic signal (OCR, ΔE, pixels). */
export function isVlmCheck(check: Pick<CheckView, "method">): boolean {
  return check.method === "vlm" || check.method.startsWith("gemini") || check.method.includes("vision");
}

function dimensionRank(dim: string): number {
  const i = (DIMENSION_ORDER as readonly string[]).indexOf(dim);
  return i === -1 ? DIMENSION_ORDER.length : i;
}

/** Deterministic checks first, then VLM checks; each group by dimension order (stable). */
export function orderChecks<T extends Pick<CheckView, "method" | "dimension">>(checks: readonly T[]): T[] {
  return checks
    .map((c, i) => ({ c, i }))
    .sort(
      (a, b) =>
        Number(isVlmCheck(a.c)) - Number(isVlmCheck(b.c)) ||
        dimensionRank(a.c.dimension) - dimensionRank(b.c.dimension) ||
        a.i - b.i,
    )
    .map(({ c }) => c);
}

const CHECK_LABEL: Record<string, string> = {
  decodes: "Image decodes",
  resolution: "Resolution ≤ 1024 px",
  aspect: "Aspect ratio",
  not_blank: "Not blank",
  not_copy: "Not a copy of the reference",
  not_placeholder: "Not a placeholder",
  ocr: "OCR read",
  ocr_cer: "Exact text (OCR)",
  critical_tokens: "Critical tokens",
  stray_text: "No stray text",
  vlm_readback: "Text read-back",
  vlm_stray_text: "Stray text (vision)",
  overlay_pending: "Text via fallback",
  product_count: "Product present once",
  product_locate: "Product located",
  color_delta_e: "Product colour",
  context_judge: "Season and market",
  "comp.realistic_scale": "Realistic scale",
  "comp.natural_integration": "Natural integration",
  "comp.scale_sanity": "Product size vs expected",
  composition_judge: "Composition judge",
};

export function checkLabel(name: string): string {
  const known = CHECK_LABEL[name];
  if (known) return known;
  // Rubric ids look like `ctx.season_cues` / `prod.logo_preserved`; the dimension is shown beside.
  const words = name
    .replace(/^vlm_/, "")
    .replace(/^[a-z]+\./, "")
    .replaceAll(/[_.]/g, " ");
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

/** Checks where a lower value is better (value ≤ threshold passes). */
const LOWER_IS_BETTER = new Set(["ocr_cer", "color_delta_e", "stray_text"]);
const VALUE_NAME: Record<string, string> = { ocr_cer: "CER", color_delta_e: "ΔE" };

function num(v: number): string {
  if (Number.isInteger(v)) return String(v);
  return String(Number(Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)));
}

function pctOf(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** `comp.scale_sanity`: "area 12% (expected 5–20%)" from its evidence data. */
function scaleSanityValue(check: Pick<CheckView, "value" | "evidence_data">): string | null {
  const data = check.evidence_data as Record<string, unknown> | null | undefined;
  if (!data || data.applicable === false || check.value == null) return null;
  const range = Array.isArray(data.expected_area) ? data.expected_area : null;
  const [lo, hi] = range && range.length === 2 && range.every((v) => typeof v === "number") ? (range as number[]) : [];
  return lo != null && hi != null
    ? `area ${pctOf(check.value)} (expected ${pctOf(lo)}–${pctOf(hi)})`
    : `area ${pctOf(check.value)}`;
}

/** "CER 0.05 > 0.02", "ΔE 3.1 ≤ 10", or "0.40 (threshold 0.50)" when the direction is unknown. */
export function measured(
  check: Pick<CheckView, "check_name" | "value" | "threshold" | "passed"> & Partial<Pick<CheckView, "evidence_data">>,
): string | null {
  if (check.check_name === "comp.scale_sanity") return scaleSanityValue(check);
  if (check.value == null) return null;
  const name = VALUE_NAME[check.check_name];
  const value = name ? `${name} ${num(check.value)}` : num(check.value);
  if (check.threshold == null) return value;
  if (LOWER_IS_BETTER.has(check.check_name) && check.passed !== null) {
    const within = check.value <= check.threshold;
    // A check can fail on a second limit (ΔE on the worst colour); don't print a passing "≤" then.
    if (within === check.passed) return `${value} ${within ? "≤" : ">"} ${num(check.threshold)}`;
  }
  return `${value} (threshold ${num(check.threshold)})`;
}

export function signalLabel(check: Pick<CheckView, "method">): string {
  if (isVlmCheck(check)) return check.method === "vlm" ? "vision judge" : check.method;
  return check.method === "deterministic" ? "deterministic" : check.method;
}
