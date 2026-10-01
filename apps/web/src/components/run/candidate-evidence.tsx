"use client";

import type { ReactNode } from "react";

import { EvidenceImage, useEvidenceFocus } from "@/components/run/evidence-image";
import { DimensionDots, Scorecard, dimensionVerdicts } from "@/components/run/scorecard";
import { evidenceFor, checkId } from "@/lib/evidence";
import { apiUrl, type CandidateState } from "@/lib/run-events";
import { cn } from "@/lib/utils";

export function aspectSize(aspect: string | null | undefined): { width: number; height: number } {
  return aspect === "1:1" ? { width: 1, height: 1 } : { width: 4, height: 5 };
}

/**
 * A candidate image wired to its scorecard: hovering, focusing or pinning a check draws that
 * check's evidence on the image (EvidenceImage). Used by candidate cards and the approval card.
 */
export function CandidateEvidence({
  candidate,
  label,
  requiredText,
  aspect,
  scaleReferences,
  maxHeight = 280,
  pending = false,
  placeholder,
  layout = "stack",
  className,
}: {
  candidate: CandidateState;
  label: string;
  requiredText: string | null;
  aspect?: string | null;
  /** The plan's scale reference objects, named on the realistic-scale evidence. */
  scaleReferences?: readonly string[] | null;
  maxHeight?: number;
  /** The candidate is still being evaluated (skeleton rows). */
  pending?: boolean;
  /** Shown instead of the image while it doesn't exist (skeleton, blocked message). */
  placeholder?: ReactNode;
  /** `split` puts the scorecard beside the image from the `md` breakpoint. */
  layout?: "stack" | "split";
  className?: string;
}) {
  const c = candidate;
  const focus = useEvidenceFocus();
  const size = c.width && c.height ? { width: c.width, height: c.height } : aspectSize(aspect);
  const checks = c.checks ?? [];
  const active = focus.activeId ? checks.find((ch) => checkId(ch) === focus.activeId) : undefined;
  const evidence =
    active && c.width && c.height ? evidenceFor(active, checks, { width: c.width, height: c.height }, requiredText, { scaleReferences })
      : null;
  const hasDims = Object.keys(c.dimensions).length > 0;

  return (
    <div className={cn(layout === "split" ? "grid gap-4 md:grid-cols-2 md:items-start" : "space-y-3", className)}>
      {c.imageUrl ? (
        <EvidenceImage
          src={apiUrl(c.imageUrl)}
          width={size.width}
          height={size.height}
          alt={`Candidate ${label}, generated ad (no description available).`}
          evidence={evidence}
          pinned={Boolean(focus.pinnedId && focus.pinnedId === evidence?.checkId)}
          maxHeight={maxHeight}
          className={layout === "split" ? "md:sticky md:top-4" : undefined}
        />
      ) : (
        <div
          className="mx-auto flex w-full items-center justify-center overflow-hidden rounded-md border bg-muted"
          style={{ aspectRatio: `${size.width} / ${size.height}`, maxWidth: Math.round((maxHeight * size.width) / size.height) }}
        >
          {placeholder}
        </div>
      )}
      <div className="min-w-0 space-y-2">
        {hasDims ? <DimensionDots verdicts={dimensionVerdicts(c.dimensions)} /> : null}
        {checks.length > 0 && c.imageUrl ? (
          <p className="text-[11px] text-muted-foreground">
            Hover or focus a check to see its evidence on the image. Click or press Enter to pin it.
          </p>
        ) : null}
        <Scorecard
          checks={c.checks}
          dimensions={c.dimensions}
          pending={pending}
          focus={c.imageUrl ? focus : undefined}
          requiredText={requiredText}
        />
      </div>
    </div>
  );
}
