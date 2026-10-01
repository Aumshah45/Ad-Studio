import { MetBadge, MetricBar } from "@/components/evals/primitives";
import { VerdictBadge } from "@/components/run/verdict-badge";
import type { EvalSummary } from "@/lib/api";
import { criterion, exact, fmtPct, metState, targetNumber, targetOf } from "@/lib/evals";
import { formatDuration, formatUsd } from "@/lib/format";

const STATUS_WORD: Record<string, string> = {
  passed: "Passed",
  approved: "Approved",
  needs_review: "Held for review",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

/** "0% (0 of 181 checks, 10 items)", or why there is no number. */
export function stabilityText(summary: EvalSummary): string {
  const st = summary.stability;
  if (st && st.available === false) return "not recorded (make eval-record RERUN=10)";
  const rate = st?.flip_rate ?? summary.judge_stability_flip_rate;
  if (rate == null) return "—";
  if (st?.checks) return `${fmtPct(rate)} (${st.flips ?? 0} of ${st.checks} checks, ${st.items ?? "?"} items)`;
  return fmtPct(rate);
}

/**
 * Per-approved-ad economics from the report: $ per approved ad, p50/p95 time to approval,
 * first-attempt vs after-repair pass rate and the native-text rate, as plain CSS bars.
 */
export function CostLatencyPanel({ summary }: { summary: EvalSummary }) {
  const costTarget = targetOf(summary, "cost_per_approved_ad");
  const latencyTarget = targetOf(summary, "p50_latency_ms");
  const costCap = targetNumber(costTarget);
  const latencyCapS = targetNumber(latencyTarget);
  const p50 = summary.p50_latency_to_approved_ms;
  const p95 = summary.p95_latency_to_approved_ms;
  // Latency bars share one scale: the larger of p95 and twice the target.
  const latencyMax = Math.max(p95 ?? 0, p50 ?? 0, (latencyCapS ?? 60) * 1000 * 2);
  const afterTarget = targetNumber(targetOf(summary, "after_repair_pass_rate"));
  const pipeline = summary.pipeline;
  const statusCounts = Object.entries(pipeline?.status_counts ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="grid gap-4 lg:grid-cols-2" data-slot="cost-latency-panel">
      <div className="space-y-4 rounded-lg border p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-xs text-muted-foreground">$ per approved ad</div>
            <div className="text-2xl font-semibold text-primary tabular-nums" title={String(summary.cost_per_approved_ad)}>
              {formatUsd(summary.cost_per_approved_ad)}
            </div>
            <div className="text-xs text-muted-foreground">target {costTarget ?? "—"} · ledger, incl. failed attempts</div>
          </div>
          <MetBadge state={metState(criterion(summary, "cost_per_approved_ad"))} />
        </div>
        {costCap ? (
          <MetricBar
            label="Share of the per-ad cap"
            value={summary.cost_per_approved_ad}
            max={costCap}
            display={`${formatUsd(summary.cost_per_approved_ad)} of ${formatUsd(costCap)}`}
          />
        ) : null}
        <div className="flex flex-wrap items-start justify-between gap-2 pt-2">
          <div className="text-xs text-muted-foreground">Time to an approved ad · target p50 {latencyTarget ?? "—"}</div>
          <MetBadge state={metState(criterion(summary, "p50_latency_ms"))} />
        </div>
        <MetricBar
          label="p50"
          value={p50}
          max={latencyMax}
          target={latencyCapS != null ? latencyCapS * 1000 : undefined}
          display={formatDuration(p50)}
        />
        <MetricBar label="p95" value={p95} max={latencyMax} tone="muted" display={formatDuration(p95)} />
      </div>
      <div className="space-y-4 rounded-lg border p-4">
        <div className="text-xs text-muted-foreground">
          Quality gate{pipeline?.briefs ? ` (${pipeline.briefs} golden briefs)` : ""}
        </div>
        <MetricBar
          label="First-attempt pass rate"
          value={summary.first_attempt_pass_rate}
          tone="muted"
          display={fmtPct(summary.first_attempt_pass_rate)}
        />
        <MetricBar
          label="After-repair pass rate"
          value={summary.after_repair_pass_rate}
          target={afterTarget ?? undefined}
          display={fmtPct(summary.after_repair_pass_rate)}
        />
        <MetricBar
          label="Native-text rate (model drew the text)"
          value={summary.native_text_rate}
          tone="muted"
          display={fmtPct(summary.native_text_rate)}
        />
        <MetricBar
          label="Exact text on shipped ads"
          value={summary.text_exact_rate}
          tone="pass"
          target={targetNumber(targetOf(summary, "text_exact_rate")) ?? undefined}
          display={fmtPct(summary.text_exact_rate)}
        />
        <dl className="grid grid-cols-2 gap-3 border-t pt-3 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">Mean repairs per ad</dt>
            <dd className="tabular-nums" data-stat="mean-repairs" title={exact(pipeline?.mean_repairs)}>
              {pipeline?.mean_repairs == null ? "—" : pipeline.mean_repairs.toFixed(2)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Judge flip rate (re-run)</dt>
            <dd className="tabular-nums" data-stat="flip-rate">
              {stabilityText(summary)}
            </dd>
          </div>
        </dl>
        <div className="space-y-1.5 border-t pt-3" data-slot="status-counts">
          <div className="text-xs text-muted-foreground">
            Run outcomes{pipeline?.briefs ? ` (${pipeline.briefs} golden runs)` : ""}
          </div>
          {statusCounts.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {statusCounts.map(([status, n]) => (
                <li key={status} data-status={status}>
                  <VerdictBadge status={status} label={`${STATUS_WORD[status] ?? status.replace(/_/g, " ")} · ${n}`} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">Not in this report. Re-run `make eval` to add run outcomes.</p>
          )}
        </div>
        <p className="text-xs text-muted-foreground">The tick on a bar marks the report&apos;s target.</p>
      </div>
    </div>
  );
}
