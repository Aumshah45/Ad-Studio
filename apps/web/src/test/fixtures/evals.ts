/**
 * EvalSummary and EvalItem fixtures. The numbers are the dry-run report
 * (`services/api/var/golden-dryrun/reports/latest.json`, evaluator ev-0.3), trimmed to what the
 * summary API returns, so tests check the UI against real report values.
 */
import type { DimensionSummary, EvalItem, EvalSummary, GoldenSources, RunSummary, VersionSummary } from "@/lib/api";

function dim(tp: number, fp: number, fn: number, tn: number, p: number | null, r: number | null, f1: number | null): DimensionSummary {
  return { n: tp + fp + fn + tn, confusion: { tp, fp, fn, tn }, precision: p, recall: r, f1 };
}

export const SUMMARY: EvalSummary = {
  report_id: "5c1d8a4e-3f7b-4e1a-9a55-0c6f2b7d9e10",
  created_at: "2026-09-25T05:23:49.706812Z",
  evaluator_version: "ev-0.3",
  dataset_version: "0acdb3d68e72",
  git_sha: "01a5a4e7d517",
  dry_run: true,
  report_file: "20260925T052349Z.md",
  per_dimension: {
    technical: dim(4, 0, 0, 26, 1.0, 1.0, 1.0),
    text: dim(11, 2, 0, 13, 0.8461538461538461, 1.0, 0.9166666666666666),
    product: dim(6, 0, 5, 15, 1.0, 0.5454545454545454, 0.7058823529411764),
    context: dim(0, 0, 6, 26, null, 0.0, null),
  },
  per_split: {
    calibration: { text: dim(6, 1, 0, 9, 0.8571428571428571, 1.0, 0.923076923076923) },
    heldout: { text: dim(5, 1, 0, 4, 0.8333333333333334, 1.0, 0.9090909090909091) },
  },
  human_agreement: null,
  human_agreement_n: 0,
  human_kappa: null,
  first_attempt_pass_rate: 0.85,
  after_repair_pass_rate: 1.0,
  native_text_rate: 0.9473684210526315,
  text_exact_rate: 1.0,
  cost_per_approved_ad: 0.0,
  p50_latency_to_approved_ms: 1910.0,
  p95_latency_to_approved_ms: 3284.2000000000003,
  judge_stability_flip_rate: 0.0,
  planted: [
    { mutation: "context_season", expected: ["context"], n: 4, caught: 0, rate: 0.0, missed: ["PL31", "PL32", "PL33", "PL34"] },
    { mutation: "product_recolour", expected: ["product"], n: 4, caught: 3, rate: 0.75, missed: ["PL15-product_recolour"] },
    { mutation: "product_swap", expected: ["product"], n: 3, caught: 2, rate: 0.6666666666666666, missed: ["PL17"] },
    { mutation: "technical", expected: ["technical"], n: 4, caught: 4, rate: 1.0, missed: [] },
    { mutation: "text_typo", expected: ["text"], n: 6, caught: 6, rate: 1.0, missed: [] },
  ],
  known_good: { n: 4, ok: 4, rate: 1.0 },
  criteria: [
    { id: "recall.text", criterion: "Evaluator catches failures", metric: "Recall, text", target: ">= 90%", now: 1.0, met: true, note: "n=26" },
    { id: "recall.product", criterion: "Evaluator catches failures", metric: "Recall, product", target: ">= 90%", now: 0.5454545454545454, met: false, note: "n=26" },
    { id: "recall.context", criterion: "Evaluator catches failures", metric: "Recall, context", target: ">= 90%", now: 0.0, met: false, note: "n=32" },
    { id: "precision.text", criterion: "Evaluator doesn't cry wolf", metric: "Precision, text", target: ">= 85%", now: 0.8461538461538461, met: false, note: "n=26" },
    { id: "precision.product", criterion: "Evaluator doesn't cry wolf", metric: "Precision, product", target: ">= 85%", now: 1.0, met: true, note: "n=26" },
    { id: "precision.context", criterion: "Evaluator doesn't cry wolf", metric: "Precision, context", target: ">= 85%", now: null, met: null, note: "n=32" },
    { id: "human_agreement", criterion: "Evaluator agrees with humans", metric: "Overall agreement", target: ">= 85%", now: null, met: null, note: "no human labels yet" },
    { id: "first_attempt_pass_rate", criterion: "Quality gate works", metric: "First-attempt pass rate", target: "report", now: 0.85, met: null, note: "reported" },
    { id: "after_repair_pass_rate", criterion: "Quality gate works", metric: "After-repair pass rate", target: ">= 90%", now: 1.0, met: true, note: "20/20" },
    { id: "text_exact_rate", criterion: "Text is guaranteed", metric: "Shipped ads with exact text", target: ">= 100%", now: 1.0, met: true, note: "native-text rate 95%, B20 excluded" },
    { id: "resolution_ok_rate", criterion: "Resolution constraint", metric: "Outputs with long edge ≤ 1024 px", target: ">= 100%", now: 1.0, met: true, note: "40 images" },
    { id: "hemisphere_accuracy", criterion: "Context refinement is correct", metric: "Planner effective season, hemisphere cases", target: ">= 100%", now: 1.0, met: true, note: "12/12" },
    { id: "network_calls", criterion: "Evaluator tests are reproducible", metric: "Network connections attempted during `make eval`", target: "0", now: 0.0, met: true, note: "cached verdicts, sockets blocked" },
    { id: "cost_per_approved_ad", criterion: "Cost", metric: "$ per approved ad (ledger)", target: "<= $0.25", now: 0.0, met: true, note: "" },
    { id: "p50_latency_ms", criterion: "Latency", metric: "p50 time to an approved ad", target: "<= 60 s", now: 1910.0, met: true, note: "" },
  ],
  targets: {
    "recall.text": ">= 90%",
    "recall.product": ">= 90%",
    "recall.context": ">= 90%",
    "precision.text": ">= 85%",
    "precision.product": ">= 85%",
    "precision.context": ">= 85%",
    human_agreement: ">= 85%",
    first_attempt_pass_rate: "report",
    after_repair_pass_rate: ">= 90%",
    text_exact_rate: ">= 100%",
    resolution_ok_rate: ">= 100%",
    hemisphere_accuracy: ">= 100%",
    network_calls: "0",
    cost_per_approved_ad: "<= $0.25",
    p50_latency_ms: "<= 60 s",
  },
  counts: { nat: 20, final: 20, plant: 36, human_labelled: 0 },
  pipeline: {
    briefs: 20,
    status_counts: { passed: 19, needs_review: 1 },
    first_attempt_pass_rate: 0.85,
    after_repair_pass_rate: 1.0,
    shipped: 20,
    native_text_rate: 0.9473684210526315,
    native_text_n: 19,
    mean_repairs: 0.1,
    cost_per_approved_ad: 0.0,
    p50_latency_ms: 1761.5,
    p95_latency_ms: 2960.15,
    resolution_ok_rate: 1.0,
    images_checked: 40,
  },
  agreement_by_dimension: {
    nat: {
      technical: { n: 0, agree: 0, agreement: null, kappa: null },
      text: { n: 0, agree: 0, agreement: null, kappa: null },
      product: { n: 0, agree: 0, agreement: null, kappa: null },
      context: { n: 0, agree: 0, agreement: null, kappa: null },
    },
  },
  stability: { available: true, items: 10, checks: 181, flips: 0, flip_rate: 0.0 },
  hemisphere: { n: 12, ok: 12, rate: 1.0 },
  provenance: {
    evaluator_version: "ev-0.3",
    judge_client: "fake",
    judge_model: "test:fake-vision",
    ocr_engine: "tesseract",
    ocr_version: "5.5.3",
    ocr_languages: ["ara", "chi_sim", "deu", "eng", "fra", "hin", "jpn", "kor", "por", "spa"],
    image_clients: ["fake"],
    image_models: ["local:pillow-overlay", "test:fake-image"],
    image_prompt_versions: ["ad_generate@2", "ad_repair_text@2", "overlay@1"],
    prompt_versions: { ad_inspect: "1", context_judge: "1", product_profile: "1" },
    snapshot_file: "verdicts.json",
    snapshot_entries: 338,
    snapshot_recorded_at: "2026-09-25T05:55:55.827771+00:00",
    snapshot_evaluator_version: "ev-0.3",
    network_attempts: 0,
    assume_pass_labels: false,
  },
  warnings: [
    "FAKE DRY RUN: images and verdicts come from the fake image and vision clients; these numbers test the plumbing, not model quality.",
    "Missing labels: no natural output has a human label, so human agreement and natural-item precision/recall are not measured.",
    "30/30 pure planted items regenerated byte-identical",
  ],
};

/** The same report after 20 human labels (agreement 18/20). */
export const SUMMARY_LABELLED: EvalSummary = {
  ...SUMMARY,
  dry_run: false,
  human_agreement: 0.9,
  human_agreement_n: 20,
  human_kappa: 0.7368421052631579,
  agreement_by_dimension: {
    nat: {
      technical: { n: 20, agree: 20, agreement: 1.0, kappa: null },
      text: { n: 20, agree: 19, agreement: 0.95, kappa: 0.8 },
      product: { n: 20, agree: 19, agreement: 0.95, kappa: 0.6428571428571429 },
      context: { n: 20, agree: 20, agreement: 1.0, kappa: 1.0 },
    },
    final: {
      technical: { n: 0, agree: 0, agreement: null, kappa: null },
      text: { n: 0, agree: 0, agreement: null, kappa: null },
      product: { n: 0, agree: 0, agreement: null, kappa: null },
      context: { n: 0, agree: 0, agreement: null, kappa: null },
    },
  },
  warnings: ["30/30 pure planted items regenerated byte-identical"],
  criteria: SUMMARY.criteria.map((c) =>
    c.id === "human_agreement" ? { ...c, now: 0.9, met: true, note: "18/20" } : c,
  ),
};

const ALL_PASS = { technical: true, text: true, product: true, context: true };

export function item(over: Partial<EvalItem> & Pick<EvalItem, "id" | "brief_id">): EvalItem {
  return {
    origin: "natural",
    set: "nat",
    product_id: "P1",
    split: "calibration",
    image_id: `img-${over.id}`,
    image_url: `/v1/images/img-${over.id}`,
    source_image_url: null,
    mutation: null,
    label_source: "none",
    label: null,
    verdict: "pass",
    dimensions: { ...ALL_PASS },
    outcomes: { technical: null, text: null, product: null, context: null },
    evidence: [],
    ...over,
  };
}

export const PLANTED_ITEMS: EvalItem[] = [
  item({
    id: "PL01-text_typo",
    brief_id: "B05",
    product_id: "P2",
    origin: "planted",
    set: "plant",
    mutation: "text_typo",
    source_image_url: "/v1/images/img-B05-nat",
    label_source: "planted",
    label: { ...ALL_PASS, text: false },
    verdict: "fail",
    dimensions: { ...ALL_PASS, text: false },
    outcomes: { technical: "tn", text: "tp", product: "tn", context: "tn" },
    evidence: ["Rendered «Oktoberfest dition»; required «Oktoberfest Edition»."],
  }),
  item({
    id: "PL15-product_recolour",
    brief_id: "B09",
    product_id: "P3",
    origin: "planted",
    set: "plant",
    mutation: "product_recolour",
    source_image_url: "/v1/images/img-B09-nat",
    label_source: "planted",
    label: { ...ALL_PASS, product: false },
    verdict: "pass",
    dimensions: { ...ALL_PASS },
    outcomes: { technical: "tn", text: "tn", product: "fn", context: "tn" },
  }),
];

/** The golden briefs' fields the items carry (from `data/golden/briefs.yaml`). */
const BRIEF = {
  B01: { product_name: "Mug with red logo panel", geography_code: "AU", season: "December", required_text: "Summer Sale — 30% OFF", aspect_ratio: "4:5", tags: ["south", "counter_intuitive", "percent", "demo"] },
  B02: { product_name: "Mug with red logo panel", geography_code: "CA", season: "December", required_text: "Winter Warmers", aspect_ratio: "4:5", tags: ["north", "winter"], run_id: "22222222-2222-2222-2222-222222222222" },
  B05: { product_name: "Labelled glass bottle", geography_code: "DE", season: "October", required_text: "Oktoberfest Edition", aspect_ratio: "4:5", tags: ["autumn"], run_id: "55555555-5555-5555-5555-555555555555" },
  B09: { product_name: "Red and white sneakers", geography_code: "US", season: "July", required_text: "4th of July — 25% OFF", aspect_ratio: "1:1", tags: ["summer", "percent"] },
} satisfies Record<string, Partial<EvalItem>>;

/**
 * Natural items for four briefs: E-nat and E-final, mixed verdicts, products, splits and labels.
 * B02's run is listed in `GOLDEN_RUNS`; B05's run id is known but not listed; B01 and B09 have no run.
 */
export const NATURAL_ITEMS: EvalItem[] = [
  item({ id: "B01-nat", brief_id: "B01", product_id: "P1", ...BRIEF.B01 }),
  item({ id: "B01-final", brief_id: "B01", product_id: "P1", set: "final", ...BRIEF.B01 }),
  item({
    id: "B02-nat",
    brief_id: "B02",
    product_id: "P1",
    ...BRIEF.B02,
    verdict: "fail",
    dimensions: { ...ALL_PASS, text: false },
    label_source: "human",
    label: { ...ALL_PASS },
    outcomes: { technical: "tn", text: "fp", product: "tn", context: "tn" },
    evidence: ["Rendered «Winter Wamers»; required «Winter Warmers»."],
  }),
  item({ id: "B02-final", brief_id: "B02", product_id: "P1", set: "final", ...BRIEF.B02 }),
  item({
    id: "B05-nat",
    brief_id: "B05",
    product_id: "P2",
    ...BRIEF.B05,
    split: "heldout",
    verdict: "fail",
    dimensions: { ...ALL_PASS, product: false },
    label_source: "human",
    label: { ...ALL_PASS, product: false },
    outcomes: { technical: "tn", text: "tn", product: "tp", context: "tn" },
  }),
  item({ id: "B05-final", brief_id: "B05", product_id: "P2", set: "final", split: "heldout", ...BRIEF.B05 }),
  item({
    id: "B09-nat",
    brief_id: "B09",
    product_id: "P3",
    ...BRIEF.B09,
    verdict: "unverified",
    dimensions: { ...ALL_PASS, context: null },
  }),
  item({
    id: "B09-final",
    brief_id: "B09",
    product_id: "P3",
    ...BRIEF.B09,
    set: "final",
    verdict: "fail",
    dimensions: { ...ALL_PASS, context: false },
  }),
];

/** A golden run for B02 (repaired once, text fallback), matched to items by `run_id`. */
export const GOLDEN_RUNS: RunSummary[] = [
  {
    id: "22222222-2222-2222-2222-222222222222",
    golden_key: "B02@orchestrator-1@live",
    origin: "golden",
    status: "passed",
    outcome: "overlay",
    geography_code: "CA",
    season: "December",
    required_text: "Winter Warmers",
    aspect_ratio: "4:5",
    product_id: "9f1c0000-0000-0000-0000-000000000001",
    approved_candidate_id: null,
    best_candidate_id: "c-2",
    cost_usd: 0.118,
    latency_ms: 38_000,
    first_attempt_pass: false,
    repair_count: 1,
    created_at: "2026-09-25T05:00:00Z",
    finished_at: "2026-09-25T05:00:38Z",
    thumbnail_url: "/v1/images/img-B02-final",
  },
];

/** `GET /v1/golden/sources`, trimmed. */
export const SOURCES: GoldenSources = {
  path: "data/golden/SOURCES.md",
  markdown:
    "# Golden dataset — sources and licences\n\n| id | Author |\n|---|---|\n| P1 | Alf van Beem |\n\n**Share-alike.** P2 is CC BY-SA.\n\n<script>alert(1)</script>\n",
  products: [
    {
      id: "P1",
      file: "products/P1.jpg",
      role: "Mug with a printed red logo panel",
      source_title: "Droste beker foto6.JPG",
      source_url: "https://commons.wikimedia.org/wiki/File:Droste_beker_foto6.JPG",
      author: "Alf van Beem",
      licence: "CC0 1.0",
      licence_url: "http://creativecommons.org/publicdomain/zero/1.0/",
      share_alike: false,
      name: "Mug with red logo panel",
    },
    {
      id: "P2",
      file: "products/P2.jpg",
      role: "Glass bottle with a paper label",
      source_title: "Bottle of Emros groundnuts from Nigeria.jpg",
      source_url: "https://commons.wikimedia.org/wiki/File:Bottle_of_Emros_groundnuts_from_Nigeria.jpg",
      author: "Alex P. Kok",
      licence: "CC BY-SA 4.0",
      licence_url: "https://creativecommons.org/licenses/by-sa/4.0",
      share_alike: true,
      name: "Labelled glass bottle",
    },
  ],
};

// --- ADR-007: composition and the golden v1 -> v2 comparison ----------------------------------

const rate = (ok: number, n: number) => ({ n, ok, rate: n ? ok / n : null });
const ALL_DIMS = ["technical", "text", "product", "context", "composition"] as const;
const rates = (over: Partial<Record<(typeof ALL_DIMS)[number], ReturnType<typeof rate>>> = {}, base = rate(20, 20)) =>
  Object.fromEntries(ALL_DIMS.map((d) => [d, over[d] ?? base]));

/** v1: the labelled orchestrator-3 run; composition re-scored by ev-0.6 (5 of 20 fail). */
export const V1_SUMMARY_ROW: VersionSummary = {
  version: "v1",
  evaluator_version: "ev-0.6",
  pipeline_versions: ["orchestrator-3"],
  counts: { nat: 20, final: 20, plant: 0, human_labelled: 40 },
  human_pass_rates: {
    nat: rates({ composition: rate(11, 20), text: rate(17, 20) }),
    final: rates({ composition: rate(13, 20) }),
  },
  evaluator_pass_rates: { nat: rates({ composition: rate(16, 20) }), final: rates({ composition: rate(15, 20) }) },
  per_dimension: {
    text: { n: 20, precision: 0.75, recall: 1.0, f1: 0.857, confusion: { tp: 3, fp: 1, fn: 0, tn: 16 } },
    composition: { n: 20, precision: 0.8, recall: 0.444, f1: 0.571, confusion: { tp: 4, fp: 1, fn: 5, tn: 10 } },
  },
  human_agreement: { n: 20, agree: 16, agreement: 0.8, kappa: 0.5 },
  first_attempt_pass_rate: 0.7,
  after_repair_pass_rate: 0.9,
  cost_per_approved_ad: 0.11,
};

/** v2: orchestrator-4, generated and evaluated, not labelled yet. */
export const V2_SUMMARY_ROW: VersionSummary = {
  version: "v2",
  evaluator_version: "ev-0.6",
  pipeline_versions: ["orchestrator-4"],
  counts: { nat: 20, final: 20, plant: 0, human_labelled: 0 },
  human_pass_rates: { nat: rates({}, rate(0, 0)), final: rates({}, rate(0, 0)) },
  evaluator_pass_rates: { nat: rates({ composition: rate(19, 20) }), final: rates() },
  per_dimension: { composition: { n: 0, precision: null, recall: null, f1: null, confusion: { tp: 0, fp: 0, fn: 0, tn: 0 } } },
  human_agreement: { n: 0, agree: 0, agreement: null, kappa: null },
  first_attempt_pass_rate: 0.85,
  after_repair_pass_rate: 1.0,
  cost_per_approved_ad: 0.09,
};

/** v2 labelled with rubric v2: composition passes 18 of 20 first attempts. */
export const V2_LABELLED_ROW: VersionSummary = {
  ...V2_SUMMARY_ROW,
  counts: { ...V2_SUMMARY_ROW.counts, human_labelled: 40 },
  human_pass_rates: { nat: rates({ composition: rate(18, 20) }), final: rates({ composition: rate(20, 20) }) },
  per_dimension: {
    composition: { n: 20, precision: 1.0, recall: 1.0, f1: 1.0, confusion: { tp: 2, fp: 0, fn: 0, tn: 18 } },
  },
};

/** The newest (v2) report with composition in every dimension map and the comparison. */
export const SUMMARY_V2: EvalSummary = {
  ...SUMMARY_LABELLED,
  golden_version: "v2",
  evaluator_version: "ev-0.6",
  report_file: "20260926T080000Z-v2.md",
  per_dimension: {
    ...SUMMARY_LABELLED.per_dimension,
    composition: dim(2, 0, 0, 18, 1.0, 1.0, 1.0),
  },
  agreement_by_dimension: {
    nat: { ...SUMMARY_LABELLED.agreement_by_dimension!.nat, composition: { n: 20, agree: 20, agreement: 1.0, kappa: 1.0 } },
  },
  comparison: { versions: [V1_SUMMARY_ROW, V2_LABELLED_ROW] },
};

/** Composition on natural items: B17 fails composition (oversized tube); B01 has no key (older). */
export const COMPOSITION_ITEMS: EvalItem[] = [
  item({
    id: "B17-final",
    brief_id: "B17",
    product_id: "P5",
    set: "final",
    verdict: "fail",
    dimensions: { ...ALL_PASS, composition: false },
    label_source: "human",
    label: { ...ALL_PASS, composition: false },
    outcomes: { technical: "tn", text: "tn", product: "tn", context: "tn", composition: "tp" },
    evidence: ["The sunscreen tube appears gigantic on the floor next to the chairs."],
  }),
  item({ id: "B01-nat", brief_id: "B01", product_id: "P1", ...BRIEF.B01 }),
];
