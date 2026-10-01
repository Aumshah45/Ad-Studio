import { ArrowRight } from "lucide-react";

import { MetBadge, SmallNNote } from "@/components/evals/primitives";
import type { EvalSummary } from "@/lib/api";
import { DIMENSION_HINT, DIMENSION_LABEL, NOT_CHECKED } from "@/lib/checks";
import {
  EVAL_DIMENSIONS,
  criteriaTally,
  criterion,
  exact,
  fmtPct,
  fmtRatio,
  metState,
  scoreRows,
  targetOf,
  type ScoreRow,
} from "@/lib/evals";

function Metric({ row }: { row: ScoreRow }) {
  const name = row.metric === "recall" ? "Recall" : "Precision";
  return (
    <div className="flex items-center justify-between gap-2" data-metric={`${row.metric}.${row.dimension}`}>
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{name}</div>
        <div className="text-xl font-semibold text-primary tabular-nums" title={exact(row.value)}>
          {fmtRatio(row.value)}
        </div>
        <div className="text-xs text-muted-foreground tabular-nums">
          {row.target ? `target ${row.target}` : "no target"}
        </div>
      </div>
      <MetBadge state={row.state} />
    </div>
  );
}

function Stat({
  label,
  value,
  title,
  target,
  state,
  hint,
}: {
  label: string;
  value: string;
  title?: string;
  target?: string;
  state: ReturnType<typeof metState>;
  hint?: string;
}) {
  return (
    <div className="space-y-1 rounded-lg border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xl font-semibold text-primary tabular-nums" title={title}>
            {value}
          </span>
          {target ? <span className="text-xs text-muted-foreground tabular-nums">target {target}</span> : null}
        </div>
        <MetBadge state={state} />
      </div>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/**
 * The headline: per-dimension recall and precision against the report's targets, with the met flag
 * the report computed, plus human agreement, exact text and first-attempt → after-repair.
 */
export function EvalScoreboard({ summary }: { summary: EvalSummary }) {
  const rows = scoreRows(summary);
  const tally = criteriaTally(summary);
  const byDim = new Map<string, ScoreRow[]>();
  for (const r of rows) byDim.set(r.dimension, [...(byDim.get(r.dimension) ?? []), r]);
  const agreement = criterion(summary, "human_agreement");
  const exactText = criterion(summary, "text_exact_rate");
  const total = tally.met + tally.notMet + tally.notMeasured;

  return (
    <div className="space-y-4" data-slot="eval-scoreboard">
      <p className="text-sm" aria-live="polite">
        <span className="font-semibold tabular-nums">
          {tally.met} of {total}
        </span>{" "}
        targets met
        {tally.notMet ? (
          <>
            {" · "}
            <span className="tabular-nums">{tally.notMet}</span> below target
          </>
        ) : null}
        {tally.notMeasured ? (
          <>
            {" · "}
            <span className="tabular-nums">{tally.notMeasured}</span> not measured yet
          </>
        ) : null}
      </p>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {EVAL_DIMENSIONS.filter((d) => byDim.has(d) || d === "composition").map((dim) => {
          const dimRows = byDim.get(dim);
          return (
            <li
              key={dim}
              className={dimRows ? "space-y-3 rounded-lg border p-3" : "space-y-2 rounded-lg border border-dashed p-3"}
              data-dimension={dim}
              data-status={dimRows ? undefined : "not_checked"}
            >
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-medium">{DIMENSION_LABEL[dim] ?? dim}</h3>
                {dimRows ? (
                  <span className="text-xs text-muted-foreground tabular-nums">n = {dimRows[0]?.n ?? 0}</span>
                ) : null}
              </div>
              {dim === "composition" ? (
                <p className="text-xs text-muted-foreground">{DIMENSION_HINT.composition}</p>
              ) : null}
              {dimRows ? (
                <>
                  {dimRows.map((r) => (
                    <Metric key={r.metric} row={r} />
                  ))}
                  {dimRows[0]?.n ? (
                    <SmallNNote n={dimRows[0].n} />
                  ) : (
                    <p className="text-xs text-muted-foreground">No human labels for this dimension yet.</p>
                  )}
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {NOT_CHECKED}. This report predates the composition dimension.
                </p>
              )}
            </li>
          );
        })}
      </ul>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Stat
          label="Human agreement (E-nat)"
          value={fmtPct(summary.human_agreement)}
          title={exact(summary.human_agreement)}
          target={targetOf(summary, "human_agreement")}
          state={metState(agreement)}
          hint={summary.human_agreement_n ? `n = ${summary.human_agreement_n}` : "No human labels yet"}
        />
        <Stat
          label="Exact text on shipped ads"
          value={fmtPct(summary.text_exact_rate)}
          title={exact(summary.text_exact_rate)}
          target={targetOf(summary, "text_exact_rate")}
          state={metState(exactText)}
          hint={exactText?.note || undefined}
        />
        <div className="space-y-1 rounded-lg border p-3 sm:col-span-2 lg:col-span-1">
          <div className="text-xs text-muted-foreground">Pass rate: first attempt → after repair</div>
          <div className="flex items-center gap-2 text-xl font-semibold tabular-nums">
            <span title={exact(summary.first_attempt_pass_rate)}>{fmtPct(summary.first_attempt_pass_rate)}</span>
            <ArrowRight className="size-4 text-muted-foreground" aria-label="to" />
            <span className="text-primary" title={exact(summary.after_repair_pass_rate)}>
              {fmtPct(summary.after_repair_pass_rate)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            After-repair target {targetOf(summary, "after_repair_pass_rate") ?? "—"}
          </p>
        </div>
      </div>
    </div>
  );
}
