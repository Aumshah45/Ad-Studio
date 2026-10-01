/**
 * RunEvent fixture sequences for the run view. Shapes follow the architecture's RunEvent union;
 * `candidate.created` includes the lineage fields (`parent_candidate_id`, `kind`) the orchestrator
 * is adding, so they are built untyped and cast (the generated client doesn't have them yet).
 */
import type { CheckView } from "@/lib/api";
import type { RunEvent } from "@/lib/run-events";

export const RUN_ID = "11111111-1111-1111-1111-111111111111";
export const CAND_A = "aaaaaaaa-0000-0000-0000-000000000001";
export const CAND_B = "bbbbbbbb-0000-0000-0000-000000000002";
export const CAND_A1 = "aaaaaaaa-0000-0000-0000-000000000011";
export const CAND_A2 = "aaaaaaaa-0000-0000-0000-000000000012";

const T0 = Date.parse("2026-09-24T14:10:00.000Z");
const at = (sec: number) => new Date(T0 + sec * 1000).toISOString();

let seq = 0;
function ev(sec: number, body: Record<string, unknown>): RunEvent {
  seq += 1;
  return { seq, run_id: RUN_ID, at: at(sec), ...body } as unknown as RunEvent;
}

function candidate(sec: number, id: string, extra: Record<string, unknown>): RunEvent {
  return ev(sec, {
    type: "candidate.created",
    candidate_id: id,
    status: "pending",
    image_id: `img-${id}`,
    image_url: `/v1/images/img-${id}`,
    width: 819,
    height: 1024,
    native_width: 896,
    native_height: 1120,
    model: "gemini-flash-lite-image",
    cached: false,
    reason: null,
    parent_candidate_id: null,
    ...extra,
  });
}

const PASS = { passed: true, reasons: [] };

/** B01 (Australia · December): A misspells, B shows snow, A is repaired, then text falls back. */
function buildRepairThenFallback(): RunEvent[] {
  seq = 0;
  return [
    ev(0, { type: "run.status", status: "planning" }),
    ev(1.8, {
      type: "plan.done",
      spec_summary: {
        source: "planner",
        effective_season: "summer",
        hemisphere: "south",
        country_name: "Australia",
        rationale: "Australia is in the southern hemisphere; December is summer.",
        locale_cues: ["beach", "eucalyptus light"],
        text_lines: ["Summer Sale — 30% OFF"],
        text_mode: "native",
        text_zone: { x0: 0.06, y0: 0.04, x1: 0.94, y1: 0.24 },
      },
    }),
    ev(1.9, { type: "run.status", status: "generating" }),
    candidate(9, CAND_A, { attempt: 1, slot: 0, kind: "candidate" }),
    candidate(10, CAND_B, { attempt: 1, slot: 1, kind: "candidate" }),
    ev(10.1, { type: "run.status", status: "evaluating" }),
    ev(14, {
      type: "evaluation.done",
      candidate_id: CAND_A,
      evaluation_id: null,
      verdict: "fail",
      dimensions: {
        technical: PASS,
        text: { passed: false, reasons: ["Read 'Summr Sale — 30% OFF' (CER 0.05)."] },
        product: PASS,
        context: PASS,
        composition: PASS,
      },
    }),
    ev(15, {
      type: "evaluation.done",
      candidate_id: CAND_B,
      evaluation_id: null,
      verdict: "fail",
      dimensions: {
        technical: PASS,
        text: PASS,
        product: PASS,
        context: { passed: false, reasons: ["Snow on the ground contradicts the summer spec."] },
        composition: PASS,
      },
    }),
    ev(15.1, { type: "run.status", status: "repairing" }),
    ev(15.2, {
      type: "repair.started",
      from_candidate_id: CAND_A,
      target_dimension: "text",
      instruction: "Fix only the headline text to read exactly «Summer Sale — 30% OFF». Keep product, scene and layout.",
    }),
    candidate(24, CAND_A1, { attempt: 2, slot: 0, kind: "repair", parent_candidate_id: CAND_A, model: "gemini-flash-image" }),
    ev(27, {
      type: "evaluation.done",
      candidate_id: CAND_A1,
      evaluation_id: null,
      verdict: "fail",
      dimensions: {
        technical: PASS,
        text: { passed: false, reasons: ["Read 'Summer Sale — 3O% OFF' (CER 0.05)."] },
        product: PASS,
        context: PASS,
        composition: PASS,
      },
    }),
    ev(27.1, { type: "run.status", status: "fallback" }),
    ev(27.2, { type: "fallback.applied", candidate_id: CAND_A1, reason: "text_exhausted" }),
    candidate(28, CAND_A2, { attempt: 3, slot: 0, kind: "overlay", parent_candidate_id: CAND_A1, model: "ad-studio-overlay" }),
    ev(30, {
      type: "evaluation.done",
      candidate_id: CAND_A2,
      evaluation_id: null,
      verdict: "pass",
      dimensions: { technical: PASS, text: PASS, product: PASS, context: PASS, composition: PASS },
    }),
    ev(30.5, {
      type: "run.finished",
      status: "passed",
      outcome: "overlay",
      approved_candidate_id: CAND_A2,
      cost_usd: 0.14,
      latency_ms: 30500,
      reason: null,
    }),
  ];
}

export const REPAIR_THEN_FALLBACK: RunEvent[] = buildRepairThenFallback();

/** Nothing is repaired: A passes on the first attempt (API's current `kind: "initial"`). */
function buildFirstPass(): RunEvent[] {
  seq = 0;
  return [
    ev(0, { type: "run.status", status: "planning" }),
    ev(1, { type: "plan.done", spec_summary: { source: "default_table", effective_season: "winter", hemisphere: "north" } }),
    ev(1.1, { type: "run.status", status: "generating" }),
    candidate(8, CAND_A, { attempt: 1, slot: 0, kind: "initial" }),
    ev(8.1, { type: "run.status", status: "evaluating" }),
    ev(12, {
      type: "evaluation.done",
      candidate_id: CAND_A,
      evaluation_id: null,
      verdict: "pass",
      dimensions: { technical: PASS, text: PASS, product: PASS, context: PASS, composition: PASS },
    }),
    ev(12.2, {
      type: "run.finished",
      status: "passed",
      outcome: "native",
      approved_candidate_id: CAND_A,
      cost_usd: 0.05,
      latency_ms: 12200,
      reason: null,
    }),
  ];
}

export const FIRST_PASS: RunEvent[] = buildFirstPass();

/** Vision judge down: context unverified, the gate fails closed and the run is held. */
function buildJudgeDown(): RunEvent[] {
  seq = 0;
  return [
    ev(0, { type: "run.status", status: "planning" }),
    ev(1, { type: "plan.done", spec_summary: { source: "planner", effective_season: "summer", hemisphere: "south" } }),
    candidate(8, CAND_A, { attempt: 1, slot: 0, kind: "initial" }),
    ev(9, { type: "budget.warning", spent_usd: 0.2, cap_usd: 0.25 }),
    ev(12, {
      type: "evaluation.done",
      candidate_id: CAND_A,
      evaluation_id: null,
      verdict: "unverified",
      dimensions: { technical: PASS, text: PASS, product: PASS, context: { passed: null, reasons: [] }, composition: { passed: null, reasons: [] } },
    }),
    ev(12.5, {
      type: "run.finished",
      status: "needs_review",
      outcome: null,
      approved_candidate_id: null,
      cost_usd: 0.07,
      latency_ms: 12500,
      reason: "evaluation_failed",
    }),
  ];
}

export const JUDGE_DOWN: RunEvent[] = buildJudgeDown();

export const CHECKS: CheckView[] = [
  {
    check_name: "context_judge",
    dimension: "context",
    method: "vlm",
    passed: null,
    value: null,
    threshold: null,
    evidence: "",
  },
  {
    check_name: "ocr_cer",
    dimension: "text",
    method: "deterministic",
    passed: false,
    value: 0.05,
    threshold: 0.02,
    evidence: "Read 'Summr Sale — 30% OFF'. Expected 'Summer'.",
  },
  {
    check_name: "resolution",
    dimension: "technical",
    method: "deterministic",
    passed: true,
    value: 1024,
    threshold: 1024,
    evidence: "",
  },
  {
    check_name: "color_delta_e",
    dimension: "product",
    method: "deterministic",
    passed: true,
    value: 3.1,
    threshold: 10,
    evidence: "",
  },
];

/**
 * Checks with `evidence_data` in the API's real shapes (captured from GET /v1/runs/{id} with the
 * fake image client, 825×1024 candidate): OCR boxes are pixel [left, top, width, height], the
 * text zone is pixel [x0, y0, x1, y1], vision boxes are Gemini [ymin, xmin, ymax, xmax] / 1000.
 */
export const EVIDENCE_SIZE = { width: 825, height: 1024 };

export const EVIDENCE_CHECKS: CheckView[] = [
  {
    check_name: "ocr_cer",
    dimension: "text",
    method: "deterministic",
    passed: false,
    value: 0.0952,
    threshold: 0.02,
    evidence: "Rendered «Summr Sale — 30% OF»; required «Summer Sale — 30% OFF».",
    evidence_data: {
      lang: "eng",
      engine: "tesseract-5.5.3",
      source: "zone",
      zone_box: [8, 0, 817, 276],
      words: [
        { box: [165, 82, 176, 41], conf: 92.0, text: "Summr" },
        { box: [358, 82, 106, 41], conf: 92.0, text: "Sale" },
        { box: [480, 106, 56, 4], conf: 91.0, text: "—" },
        { box: [554, 82, 104, 42], conf: 95.0, text: "30%" },
        { box: [377, 152, 71, 41], conf: 96.0, text: "OF" },
      ],
    },
  },
  {
    check_name: "critical_tokens",
    dimension: "text",
    method: "deterministic",
    passed: false,
    value: 1,
    threshold: 0,
    evidence: "Missing or altered: 'off'",
    evidence_data: null,
  },
  {
    check_name: "color_delta_e",
    dimension: "product",
    method: "deterministic",
    passed: false,
    value: 41.8,
    threshold: 10,
    evidence: "Colour drift ΔE 41.8 (worst colour ΔE 44.0): #1FA3A0 vs reference #DD1728.",
    evidence_data: {
      ad_box: [440, 201, 940, 798],
      reference_box: [40, 40, 960, 960],
      ad_colors: ["#EDEBEA", "#1FA3A0", "#6D5251"],
      reference_colors: ["#EBEAE9", "#DD1728", "#704E4B"],
      per_colour: [0.17, 44.0, 2.68],
    },
  },
  {
    check_name: "ctx.no_season_contradiction",
    dimension: "context",
    method: "vlm",
    passed: false,
    value: null,
    threshold: null,
    evidence: "Snow covers the ground and people wear coats.",
    evidence_data: {
      severity: "must",
      pass_on: "no",
      verdict: "yes",
      question: "Does the scene contain anything contradicting summer, such as snow, bare trees, winter coats, fireplace?",
    },
  },
  {
    check_name: "resolution",
    dimension: "technical",
    method: "deterministic",
    passed: true,
    value: 1024,
    threshold: 1024,
    evidence: "",
    evidence_data: null,
  },
];

/**
 * Composition checks (ADR-007, evaluator ev-0.6) in the API's shapes: the two judge checks carry
 * the question (with the plan's scale references) and the verdict; `comp.scale_sanity` carries the
 * product box (Gemini) and the area vs the expected range.
 */
export const COMPOSITION_CHECKS: CheckView[] = [
  {
    check_name: "comp.scale_sanity",
    dimension: "composition",
    method: "deterministic",
    passed: true,
    value: 0.3,
    threshold: 0.2,
    evidence:
      "Note (not a failure): Product box covers 30% of the image (longest side 60% of the height); expected about 5%-20% (18%-30%) for a 15 cm product in a close-up shot; somewhat too large.",
    evidence_data: {
      applicable: true,
      area: 0.3,
      expected_area: [0.05, 0.2],
      long_side: 0.6,
      expected_scale: [0.18, 0.3],
      framing: "close_up",
      size_cm: 15,
      direction: "over",
      note: true,
      ad_box: [300, 250, 900, 750],
    },
  },
  {
    check_name: "comp.realistic_scale",
    dimension: "composition",
    method: "vlm",
    passed: false,
    value: null,
    threshold: null,
    evidence: "The sunscreen tube appears gigantic on the floor, taller than the chairs beside it.",
    evidence_data: {
      severity: "must",
      pass_on: "yes",
      verdict: "no",
      question:
        "The product is a sunscreen tube, about 15 cm at its largest in real life. Compare its apparent size with the other objects in the scene (for example a pair of sunglasses, a beach towel) and anything else of known size.",
    },
  },
  {
    check_name: "comp.natural_integration",
    dimension: "composition",
    method: "vlm",
    passed: true,
    value: null,
    threshold: null,
    evidence: "Lit from the same side as the chairs, with a soft contact shadow on the sand.",
    evidence_data: { severity: "must", pass_on: "yes", verdict: "yes", question: "Does the product look photographed in this scene rather than pasted onto it?" },
  },
];

/** `plan.done` product scale for a 15 cm tube in a close-up (creative_planner v2, spec cs-2). */
export const PRODUCT_SCALE = {
  framing: "close_up",
  scale_min: 0.18,
  scale_max: 0.3,
  size_cm: 15,
  size_class: "small",
  resting_surface: "a beach towel",
  scale_references: ["a pair of sunglasses", "a beach towel"],
};

/** B17: the first candidate's product is oversized; a composition repair on Flash fixes it. */
function buildCompositionRepair(): RunEvent[] {
  seq = 0;
  return [
    ev(0, { type: "run.status", status: "planning" }),
    ev(1, {
      type: "plan.done",
      spec_summary: {
        source: "planner",
        effective_season: "summer",
        hemisphere: "south",
        text_zone: { x0: 0.06, y0: 0.04, x1: 0.94, y1: 0.24 },
        product_scale: PRODUCT_SCALE,
      },
    }),
    candidate(8, CAND_A, { attempt: 0, slot: 0, kind: "initial" }),
    ev(12, {
      type: "evaluation.done",
      candidate_id: CAND_A,
      evaluation_id: null,
      verdict: "fail",
      dimensions: {
        technical: PASS,
        text: PASS,
        product: PASS,
        context: PASS,
        composition: { passed: false, reasons: ["The tube appears gigantic next to the chairs."] },
      },
    }),
    ev(12.2, {
      type: "repair.started",
      from_candidate_id: CAND_A,
      target_dimension: "composition",
      action: "repair_composition",
      model: "gemini-flash-image",
      instruction: "Resize the product to about 15 cm (18-30% of the image height) next to a pair of sunglasses; add a contact shadow.",
    }),
    candidate(20, CAND_A1, { attempt: 1, slot: 0, kind: "repair", parent_candidate_id: CAND_A, model: "gemini-flash-image" }),
    ev(24, {
      type: "evaluation.done",
      candidate_id: CAND_A1,
      evaluation_id: null,
      verdict: "pass",
      dimensions: { technical: PASS, text: PASS, product: PASS, context: PASS, composition: PASS },
    }),
    ev(24.5, {
      type: "run.finished",
      status: "passed",
      outcome: "native",
      approved_candidate_id: CAND_A1,
      cost_usd: 0.09,
      latency_ms: 24500,
      reason: null,
    }),
  ];
}

export const COMPOSITION_REPAIR: RunEvent[] = buildCompositionRepair();
