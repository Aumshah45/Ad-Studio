import { Users } from "lucide-react";

import { CommandLine, EmptyState } from "@/components/empty-state";
import { MetBadge, SmallNNote } from "@/components/evals/primitives";
import { VerdictBadge } from "@/components/run/verdict-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { EvalItem, EvalSummary } from "@/lib/api";
import { DIMENSION_LABEL, NOT_CHECKED } from "@/lib/checks";
import {
  EVAL_DIMENSIONS,
  criterion,
  disagreements,
  exact,
  fmtPct,
  fmtRatio,
  humanOverall,
  metState,
  targetOf,
} from "@/lib/evals";
import { apiUrl } from "@/lib/run-events";

const SET_NAME: Record<string, string> = { nat: "First attempt (E-nat)", final: "Shipped (E-final)" };

/** Agreement and κ per image set and dimension; null when the report has none. */
export function AgreementByDimension({ summary }: { summary: EvalSummary }) {
  const by = summary.agreement_by_dimension ?? {};
  const sets = ["nat", "final", ...Object.keys(by).filter((k) => k !== "nat" && k !== "final")].filter(
    (k) => by[k] && Object.values(by[k]).some((m) => (m.n ?? 0) > 0),
  );
  if (!sets.length) {
    return (
      <p className="text-sm text-muted-foreground" data-slot="agreement-by-dimension">
        Per-dimension agreement is not in this report.
      </p>
    );
  }
  return (
    <Table aria-label="Agreement per dimension" data-slot="agreement-by-dimension">
      <TableHeader>
        <TableRow>
          <TableHead>Dimension</TableHead>
          {sets.map((set) => (
            <TableHead key={set} colSpan={2} className="text-center">
              {SET_NAME[set] ?? set}
            </TableHead>
          ))}
        </TableRow>
        <TableRow>
          <TableHead>
            <span className="sr-only">Metric</span>
          </TableHead>
          {sets.flatMap((set) => [
            <TableHead key={`${set}-a`} className="text-right">
              Agree
            </TableHead>,
            <TableHead key={`${set}-k`} className="text-right">
              κ
            </TableHead>,
          ])}
        </TableRow>
      </TableHeader>
      <TableBody>
        {EVAL_DIMENSIONS.map((d) => (
          <TableRow key={d} data-dimension={d}>
            <TableCell className="font-medium">{DIMENSION_LABEL[d]}</TableCell>
            {sets.flatMap((set) => {
              const m = by[set]?.[d];
              if (d === "composition" && !m) {
                return [
                  <TableCell key={`${set}-a`} colSpan={2} className="text-right text-xs text-muted-foreground" data-col={`${set}-agree`}>
                    {NOT_CHECKED}
                  </TableCell>,
                ];
              }
              return [
                <TableCell key={`${set}-a`} className="text-right tabular-nums" data-col={`${set}-agree`}>
                  {m?.n ? (
                    <span title={exact(m.agreement)}>
                      {fmtPct(m.agreement)} <span className="text-xs text-muted-foreground">({m.agree ?? 0}/{m.n})</span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>,
                <TableCell key={`${set}-k`} className="text-right tabular-nums" data-col={`${set}-kappa`} title={exact(m?.kappa)}>
                  {fmtRatio(m?.kappa)}
                </TableCell>,
              ];
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Human agreement and Cohen's κ, with the disagreements listed; a clear empty state before labels. */
export function AgreementPanel({ summary, natural }: { summary: EvalSummary; natural: EvalItem[] | null }) {
  const n = summary.human_agreement_n;
  if (!n) {
    return (
      <EmptyState
        icon={<Users aria-hidden />}
        title="No human labels yet"
        body={
          <>
            Agreement and Cohen&apos;s κ need per-dimension human labels on the 20 natural outputs. Label them with the
            labelling sheet, save <code className="font-mono">labels.csv</code>, then re-run the evaluation.
          </>
        }
      >
        <CommandLine command={"make golden-sheet   # open the labelling sheet\nmake eval && make golden-import"} />
      </EmptyState>
    );
  }
  const agree = summary.human_agreement == null ? null : Math.round(summary.human_agreement * n);
  const list = natural ? disagreements(natural.filter((i) => i.set === "nat")) : [];
  return (
    <div className="space-y-4" data-slot="agreement-panel">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1 rounded-lg border p-3">
          <div className="text-xs text-muted-foreground">Overall agreement (E-nat)</div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xl font-semibold text-primary tabular-nums" title={exact(summary.human_agreement)}>
              {fmtPct(summary.human_agreement)}
            </span>
            <MetBadge state={metState(criterion(summary, "human_agreement"))} />
          </div>
          <p className="text-xs text-muted-foreground tabular-nums">
            {agree != null ? `Agrees on ${agree} of ${n} ads` : `n = ${n}`} · target{" "}
            {targetOf(summary, "human_agreement") ?? "—"}
          </p>
          <SmallNNote n={n} />
        </div>
        <div className="space-y-1 rounded-lg border p-3">
          <div className="text-xs text-muted-foreground">Cohen&apos;s κ</div>
          <span className="text-xl font-semibold tabular-nums" title={exact(summary.human_kappa)}>
            {fmtRatio(summary.human_kappa)}
          </span>
          <p className="text-xs text-muted-foreground">Chance-corrected agreement; ≥ 0.6 is desired.</p>
        </div>
      </div>
      <AgreementByDimension summary={summary} />
      {list.length ? (
        <ul className="divide-y rounded-lg border" aria-label="Disagreements">
          {list.map((i) => (
            <li key={i.id} className="flex items-start gap-3 p-3">
              {i.image_url ? (
                // eslint-disable-next-line @next/next/no-img-element -- API-served thumbnail
                <img
                  src={apiUrl(i.image_url)}
                  alt={`Brief ${i.brief_id} first attempt`}
                  className="h-16 w-13 shrink-0 rounded border bg-muted object-contain"
                />
              ) : null}
              <div className="min-w-0 space-y-1 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs">{i.brief_id}</span>
                  <span className="text-xs text-muted-foreground">Human</span>
                  <VerdictBadge status={humanOverall(i.label) ?? "unverified"} />
                  <span className="text-xs text-muted-foreground">Evaluator</span>
                  <VerdictBadge status={i.verdict} />
                </div>
                <p className="text-xs text-muted-foreground">
                  Differs on:{" "}
                  {Object.entries(i.label ?? {})
                    .filter(([d, v]) => v !== null && i.dimensions[d] !== undefined && i.dimensions[d] !== v)
                    .map(([d]) => DIMENSION_LABEL[d] ?? d)
                    .join(", ") || "overall verdict"}
                </p>
                {i.evidence[0] ? <p className="font-mono text-xs break-words">{i.evidence[0]}</p> : null}
              </div>
            </li>
          ))}
        </ul>
      ) : natural ? (
        <p className="text-sm text-muted-foreground">No disagreements on the labelled items.</p>
      ) : null}
      <p className="text-xs text-muted-foreground">One labeller (team); see Limitations.</p>
    </div>
  );
}
