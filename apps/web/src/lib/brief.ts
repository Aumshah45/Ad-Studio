/** The Studio brief draft, its demo default, and the prefill read from the Studio URL. */
import type { RunCreate } from "@/lib/api";

export type Aspect = NonNullable<RunCreate["aspect_ratio"]>;

export interface BriefDraft {
  /** Preselected stored product (a rerun keeps the same product). */
  productId?: string | null;
  geographyCode: string;
  season: string;
  requiredText: string;
  aspect: Aspect;
}

/** The demo brief (golden B01): the form opens populated. */
export const DEFAULT_BRIEF: BriefDraft = {
  geographyCode: "AU",
  season: "December",
  requiredText: "Summer Sale — 30% OFF",
  aspect: "4:5",
};

const ASPECTS = new Set<string>(["4:5", "1:1"]);

/**
 * A brief from the Studio URL (`?product=&geo=&season=&text=&aspect=`), as written by "Rerun for
 * another market" / "Rerun with edits"; missing fields fall back to the demo brief.
 */
export function briefFromParams(params: Record<string, string | string[] | undefined>): BriefDraft {
  const one = (k: string) => {
    const v = params[k];
    return typeof v === "string" ? v : undefined;
  };
  const aspect = one("aspect");
  return {
    productId: one("product") ?? null,
    geographyCode: one("geo")?.toUpperCase() || DEFAULT_BRIEF.geographyCode,
    season: one("season") ?? DEFAULT_BRIEF.season,
    requiredText: one("text") ?? DEFAULT_BRIEF.requiredText,
    aspect: aspect && ASPECTS.has(aspect) ? (aspect as Aspect) : DEFAULT_BRIEF.aspect,
  };
}
