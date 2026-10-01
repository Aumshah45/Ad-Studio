import { CircleCheck, CircleDashed, CircleMinus, FlaskConical, TriangleAlert, type LucideIcon } from "lucide-react";

import { MET_LABEL, SMALL_N, type MetState } from "@/lib/evals";
import { cn } from "@/lib/utils";

const MET_SPEC: Record<MetState, { icon: LucideIcon; className: string }> = {
  met: { icon: CircleCheck, className: "border-emerald-700/25 bg-emerald-700/10 text-emerald-700 dark:text-emerald-400" },
  // Below target is amber, not red (docs/ux.md visual direction).
  not_met: { icon: TriangleAlert, className: "border-amber-700/25 bg-amber-700/10 text-amber-800 dark:text-amber-400" },
  not_measured: { icon: CircleDashed, className: "border-dashed border-zinc-500/60 text-zinc-600 dark:text-zinc-300" },
  reported: { icon: CircleMinus, className: "border-border text-muted-foreground" },
};

/** Met / not met against the report's target: an icon and a word, never colour alone. */
export function MetBadge({ state, className }: { state: MetState; className?: string }) {
  const spec = MET_SPEC[state];
  const Icon = spec.icon;
  return (
    <span
      data-met={state}
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-md border px-2 text-xs font-medium whitespace-nowrap",
        spec.className,
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      {MET_LABEL[state]}
    </span>
  );
}

/** Shown on every surface fed by a report built with the fake image and vision clients. */
export function DryRunBadge({ className }: { className?: string }) {
  return (
    <span
      data-slot="dry-run-badge"
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-md border-2 border-amber-700 bg-amber-700/10 px-2.5 text-xs font-bold tracking-wide text-amber-800 uppercase dark:border-amber-500 dark:text-amber-300",
        className,
      )}
    >
      <FlaskConical className="size-3.5" aria-hidden />
      Fake dry run
    </span>
  );
}

export function SmallNNote({ n, className }: { n: number; className?: string }) {
  if (n >= SMALL_N) return null;
  return <span className={cn("text-xs text-muted-foreground", className)}>n = {n}. Treat as indicative.</span>;
}

/**
 * A plain CSS bar (no chart library): the value as a filled track, with an optional target tick.
 * `value` and `target` are fractions of `max`.
 */
export function MetricBar({
  label,
  value,
  display,
  max = 1,
  target,
  tone = "primary",
  className,
}: {
  label: string;
  value: number | null | undefined;
  display: string;
  max?: number;
  target?: number;
  tone?: "primary" | "muted" | "pass" | "warn";
  className?: string;
}) {
  const pct = value == null || max <= 0 ? 0 : Math.max(0, Math.min(1, value / max)) * 100;
  const targetPct = target == null || max <= 0 ? null : Math.max(0, Math.min(1, target / max)) * 100;
  const fill = {
    primary: "bg-primary",
    muted: "bg-muted-foreground/50",
    pass: "bg-emerald-700 dark:bg-emerald-500",
    warn: "bg-amber-700 dark:bg-amber-500",
  }[tone];
  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium tabular-nums">{display}</span>
      </div>
      <div
        className="relative h-2 rounded-full bg-muted"
        role="img"
        aria-label={`${label}: ${display}`}
      >
        <div className={cn("h-full rounded-full", fill)} style={{ width: `${pct}%` }} />
        {targetPct != null ? (
          <div
            className="absolute -top-1 h-4 w-0.5 rounded bg-foreground/70"
            style={{ left: `calc(${targetPct}% - 1px)` }}
            aria-hidden
          />
        ) : null}
      </div>
    </div>
  );
}

/** A section heading with an anchor id (the Evaluator is one scroll with anchored sections). */
export function SectionHeading({ id, title, description }: { id: string; title: string; description?: string }) {
  return (
    <div className="space-y-1">
      <h2 id={id} className="scroll-mt-20 text-lg font-semibold tracking-tight">
        {title}
      </h2>
      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
    </div>
  );
}
