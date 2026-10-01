/**
 * The batch gallery's view model. Each natural item of the latest report (`GET /v1/evals/items`)
 * carries its verdicts, human labels and the golden brief (market, season, required text, aspect,
 * tags) plus `run_id`, the golden run that produced the image. Golden runs
 * (`GET /v1/runs?origin=golden`) are matched by that id only to add cost, latency and repairs.
 */
import { runsListRuns, type EvalItem, type RunSummary } from "@/lib/api";
import { EVAL_DIMENSIONS, humanOverall, type EvalDimension } from "@/lib/evals";

export type AdSet = "nat" | "final";

export const SET_LABEL: Record<AdSet, string> = {
  nat: "First attempt (E-nat)",
  final: "Shipped (E-final)",
};

/** The golden brief behind an ad, from the eval item (`data/golden/briefs.yaml`). */
export interface AdBrief {
  geographyCode: string | null;
  season: string | null;
  requiredText: string | null;
  aspectRatio: string | null;
  productName: string | null;
}

export interface BatchAd {
  /** The eval item id, e.g. `B01-final`; also the `?ad=` deep link. */
  id: string;
  briefId: string;
  set: AdSet;
  productId: string;
  item: EvalItem;
  brief: AdBrief;
  /** The golden run that produced this image (from the item), when known. */
  runId: string | null;
  /** That run's summary, when this database lists it (cost, latency, repairs). */
  run: RunSummary | null;
  /** Dimensions the evaluator failed (false), in display order. */
  failed: EvalDimension[];
  /** Outcome tags derived from the verdicts, labels and run. */
  outcomeTags: string[];
  /** The golden brief's own tags (`south`, `non latin`…), humanised. */
  briefTags: string[];
  /** Both, for the tag filter. */
  tags: string[];
}

/** `counter_intuitive` → `counter intuitive`. */
export function humaniseTag(tag: string): string {
  return tag.replace(/_/g, " ");
}

/** Tags derived from the item and its run, used by the tag filter and shown on the sheet. */
export function deriveTags(item: EvalItem, run: RunSummary | null): string[] {
  const tags: string[] = [item.split === "heldout" ? "held-out split" : "calibration split"];
  if (item.label_source === "human") {
    tags.push("human-labelled");
    const human = humanOverall(item.label);
    if (human && human !== item.verdict) tags.push("disagrees with human");
  }
  if (run) {
    if (run.first_attempt_pass) tags.push("first-attempt pass");
    if (run.repair_count > 0) tags.push("repaired");
    if (run.outcome === "overlay") tags.push("text fallback");
    if (run.status === "needs_review") tags.push("held for review");
  }
  return tags;
}

export function briefOf(item: EvalItem): AdBrief {
  return {
    geographyCode: item.geography_code ?? null,
    season: item.season ?? null,
    requiredText: item.required_text ?? null,
    aspectRatio: item.aspect_ratio ?? null,
    productName: item.product_name ?? null,
  };
}

/** Natural items (nat + final) with their brief, and the run named by `run_id` when listed. */
export function buildAds(items: readonly EvalItem[], runs: readonly RunSummary[]): BatchAd[] {
  const runById = new Map(runs.map((r) => [r.id, r]));
  return items
    .filter((i): i is EvalItem & { set: AdSet } => i.set === "nat" || i.set === "final")
    .map((item) => {
      const runId = item.run_id ?? null;
      const run = runId ? (runById.get(runId) ?? null) : null;
      const outcomeTags = deriveTags(item, run);
      const briefTags = (item.tags ?? []).map(humaniseTag);
      return {
        id: item.id,
        briefId: item.brief_id,
        set: item.set,
        productId: item.product_id,
        item,
        brief: briefOf(item),
        runId,
        run,
        failed: EVAL_DIMENSIONS.filter((d) => item.dimensions[d] === false),
        outcomeTags,
        briefTags,
        tags: [...new Set([...outcomeTags, ...briefTags])],
      };
    })
    .sort((a, b) => a.briefId.localeCompare(b.briefId, undefined, { numeric: true }));
}

export interface GalleryFilter {
  verdict: "all" | EvalItem["verdict"];
  dimension: "all" | EvalDimension;
  product: string;
  tag: string;
}

export const ALL_FILTER: GalleryFilter = { verdict: "all", dimension: "all", product: "all", tag: "all" };

export function filterAds(ads: readonly BatchAd[], set: AdSet, f: GalleryFilter): BatchAd[] {
  return ads.filter(
    (a) =>
      a.set === set &&
      (f.verdict === "all" || a.item.verdict === f.verdict) &&
      (f.dimension === "all" || a.failed.includes(f.dimension)) &&
      (f.product === "all" || a.productId === f.product) &&
      (f.tag === "all" || a.tags.includes(f.tag)),
  );
}

export interface FilterOptions {
  /** Product ids with their names when the report has them. */
  products: { id: string; name: string | null }[];
  outcomeTags: string[];
  briefTags: string[];
}

/** Distinct products and tags for the filter menus, in a stable order. */
export function filterOptions(ads: readonly BatchAd[]): FilterOptions {
  const names = new Map<string, string | null>();
  for (const a of ads) if (!names.get(a.productId)) names.set(a.productId, a.brief.productName);
  const products = [...names.keys()]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .map((id) => ({ id, name: names.get(id) ?? null }));
  const outcomeTags = [...new Set(ads.flatMap((a) => a.outcomeTags))].sort();
  const briefTags = [...new Set(ads.flatMap((a) => a.briefTags))].filter((t) => !outcomeTags.includes(t)).sort();
  return { products, outcomeTags, briefTags };
}

/** Every golden run (pages of 100); empty when the API has none or is unreachable. */
export async function loadGoldenRuns(): Promise<RunSummary[]> {
  const out: RunSummary[] = [];
  let cursor: string | null | undefined;
  try {
    for (let page = 0; page < 5; page += 1) {
      const r = await runsListRuns({ query: { origin: "golden", limit: 100, cursor: cursor ?? undefined } });
      if (!r.data) break;
      out.push(...r.data.items);
      cursor = r.data.next_cursor;
      if (!cursor) break;
    }
  } catch {
    // Runs only add cost, latency and repairs; the gallery works from the eval items alone.
  }
  return out;
}

/**
 * Estimate for a 20-brief batch from the last report: $ per approved ad × briefs, and p50 time per
 * ad × briefs / the CLI's concurrency (3). Falls back to the per-ad budget cap when there is no
 * report number.
 */
export function batchEstimate(
  briefs: number,
  costPerAd: number | null | undefined,
  p50Ms: number | null | undefined,
  concurrency = 3,
): { usd: number; usdIsCap: boolean; seconds: number | null } {
  const PER_AD_CAP = 0.25;
  const usdIsCap = costPerAd == null || costPerAd <= 0;
  const usd = (usdIsCap ? PER_AD_CAP : costPerAd) * briefs;
  const seconds = p50Ms == null ? null : Math.ceil(briefs / concurrency) * (p50Ms / 1000);
  return { usd, usdIsCap, seconds };
}
