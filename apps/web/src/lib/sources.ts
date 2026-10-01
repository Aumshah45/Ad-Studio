/**
 * Golden reference-photo provenance (`GET /v1/golden/sources`): per product, where the photo came
 * from, its author and licence, plus `data/golden/SOURCES.md` verbatim.
 */
import { goldenGoldenSources, type GoldenProductSource, type GoldenSources } from "@/lib/api";
import { isProblem, problemMessage } from "@/lib/problem";

import { API_DOWN, type Loaded } from "@/lib/evals";

export const SOURCES_HREF = "/batch/sources";

/** Link to one product's row on the sources view. */
export function sourceHref(productId: string): string {
  return `${SOURCES_HREF}#source-${productId}`;
}

let cached: Promise<Loaded<GoldenSources>> | null = null;

async function fetchSources(): Promise<Loaded<GoldenSources>> {
  try {
    const r = await goldenGoldenSources();
    if (r.data) return { kind: "ok", data: r.data };
    if (r.response?.status === 404) {
      return { kind: "no-report", message: "data/golden/SOURCES.md is not in this checkout." };
    }
    return { kind: "error", message: isProblem(r.error) ? problemMessage(r.error) : "The API returned an error." };
  } catch {
    return { kind: "error", message: API_DOWN };
  }
}

/** The sources, fetched once per page load (a failed load is retried on the next call). */
export function loadSources(): Promise<Loaded<GoldenSources>> {
  if (!cached) {
    cached = fetchSources().then((r) => {
      if (r.kind !== "ok") cached = null;
      return r;
    });
  }
  return cached;
}

/** Test hook: forget the cached response. */
export function resetSourcesCache(): void {
  cached = null;
}

export function sourceFor(sources: GoldenSources | null, productId: string): GoldenProductSource | null {
  return sources?.products.find((p) => p.id === productId) ?? null;
}
