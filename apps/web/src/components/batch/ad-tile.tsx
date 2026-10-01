import { ImageOff } from "lucide-react";

import { countryByCode } from "@/components/studio/geography-combobox";
import { DimensionDots } from "@/components/run/scorecard";
import { VerdictBadge } from "@/components/run/verdict-badge";
import type { BatchAd } from "@/lib/batch";
import { checkStatus } from "@/lib/checks";
import { apiUrl } from "@/lib/run-events";
import { cn } from "@/lib/utils";

export function marketLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return countryByCode(code)?.name ?? code;
}

const VERDICT_WORD: Record<string, string> = { pass: "pass", fail: "fail", unverified: "unsure" };

export function tileVerdicts(ad: BatchAd): Record<string, ReturnType<typeof checkStatus>> {
  return Object.fromEntries(Object.entries(ad.item.dimensions).map(([d, v]) => [d, checkStatus(v)]));
}

/** "Australia · December", or null when the report has no brief fields. */
export function briefLine(ad: BatchAd): string | null {
  const parts = [marketLabel(ad.brief.geographyCode), ad.brief.season].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/** One golden ad: image, brief id, market and season, required text and per-dimension verdicts. */
export function AdTile({ ad, onOpen }: { ad: BatchAd; onOpen: (id: string) => void }) {
  const line = briefLine(ad);
  const text = ad.brief.requiredText;
  const held = ad.item.verdict === "unverified";
  return (
    <button
      type="button"
      onClick={() => onOpen(ad.id)}
      data-ad={ad.id}
      className={cn(
        "group flex w-full flex-col gap-2 rounded-xl border bg-card p-2 text-left outline-offset-2 transition-colors hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none",
        held && "border-dashed border-zinc-500/60",
      )}
      aria-label={`Open ${ad.briefId}${line ? `, ${line.replace(" · ", " ")}` : ""}: ${VERDICT_WORD[ad.item.verdict] ?? ad.item.verdict}`}
    >
      <div className="relative aspect-[4/5] w-full overflow-hidden rounded-md border bg-muted">
        {ad.item.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- API-served, already ≤ 1024 px
          <img
            src={apiUrl(ad.item.image_url)}
            alt=""
            loading="lazy"
            className="absolute inset-0 size-full object-contain"
          />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-1 p-2 text-center text-xs text-muted-foreground">
            <ImageOff className="size-5" aria-hidden />
            Image not imported
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs font-medium">{ad.briefId}</span>
        <VerdictBadge status={ad.item.verdict} />
      </div>
      <div className="min-h-9 space-y-0.5 text-xs">
        <div className="truncate text-muted-foreground">{line ?? ad.brief.productName ?? ad.productId}</div>
        {text ? (
          <div className="line-clamp-2 font-medium break-words" title={text}>
            &ldquo;{text}&rdquo;
          </div>
        ) : null}
      </div>
      <DimensionDots verdicts={tileVerdicts(ad)} />
    </button>
  );
}
