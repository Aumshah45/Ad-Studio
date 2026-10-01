import { CandidateEvidence } from "@/components/run/candidate-evidence";
import { VerdictBadge } from "@/components/run/verdict-badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { attemptTitle, kindLabel, type CandidateState } from "@/lib/run-events";
import { friendlyReason } from "@/lib/run-reasons";
import { cn } from "@/lib/utils";

export function aspectCss(aspect: string | null | undefined): string {
  return aspect === "1:1" ? "1 / 1" : "4 / 5";
}

export function candidateBadge(c: CandidateState): string {
  if (c.status === "blocked") return "blocked";
  if (c.verdict) return c.verdict;
  return c.imageUrl ? "evaluating" : "pending";
}

/** One generated image with its lineage (attempt, slot, kind, parent) and scorecard. */
export function CandidateCard({
  candidate,
  label,
  parentLabel,
  aspect,
  requiredText,
  approved = false,
  runEnded = false,
  className,
}: {
  candidate: CandidateState;
  label: string;
  parentLabel?: string | null;
  aspect?: string | null;
  /** The brief's exact text, for the OCR TextDiff. */
  requiredText?: string | null;
  approved?: boolean;
  /** The run ended (e.g. cancelled) before this candidate was evaluated. */
  runEnded?: boolean;
  className?: string;
}) {
  const c = candidate;
  const pending = c.status !== "blocked" && !c.verdict && !runEnded;
  return (
    <Card
      size="sm"
      className={cn(approved && "ring-2 ring-emerald-700/50", className)}
      data-candidate={c.id}
      aria-label={`Candidate ${label}`}
    >
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="tabular-nums">
            {label}
            {approved ? <span className="ml-2 text-xs font-normal text-emerald-700 dark:text-emerald-400">best</span> : null}
          </span>
          {pending || c.verdict || c.status === "blocked" ? (
            <VerdictBadge status={candidateBadge(c)} />
          ) : (
            <VerdictBadge status="skipped" label="Not evaluated" />
          )}
        </CardTitle>
        <CardDescription className="font-mono text-[11px] leading-relaxed break-words">
          {attemptTitle(c)}
          {c.attempt === 0 ? ` · slot ${c.slot + 1}` : ""} · {kindLabel(c.kind)}
          {parentLabel ? ` · from ${parentLabel}` : ""}
          <br />
          {c.model ?? "model pending"}
          {c.cached ? " · cached" : ""}
          {c.width && c.height ? ` · ${c.width}×${c.height}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <CandidateEvidence
          candidate={c}
          label={label}
          requiredText={requiredText ?? null}
          aspect={aspect}
          pending={pending && Boolean(c.imageUrl)}
          placeholder={
            c.status === "blocked" ? (
              <p className="p-4 text-center text-sm text-muted-foreground">
                Model returned no image. {c.reason ? friendlyReason(c.reason) : null}
              </p>
            ) : (
              <Skeleton className="size-full rounded-none motion-reduce:animate-none" />
            )
          }
        />
      </CardContent>
    </Card>
  );
}
