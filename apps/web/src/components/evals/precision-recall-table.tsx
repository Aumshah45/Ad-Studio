"use client";

import { Grid2x2 } from "lucide-react";

import { MetBadge, SmallNNote } from "@/components/evals/primitives";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverDescription, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Confusion, EvalSummary } from "@/lib/api";
import { DIMENSION_HINT, DIMENSION_LABEL, NOT_CHECKED } from "@/lib/checks";
import { EVAL_DIMENSIONS, criterion, exact, fmtRatio, metState, posNeg, targetOf, type MetState } from "@/lib/evals";

function Cell({ label, value, hint, tone }: { label: string; value: number; hint: string; tone: "good" | "bad" }) {
  return (
    <div
      className={
        tone === "good"
          ? "rounded-md border border-emerald-700/25 bg-emerald-700/5 p-2"
          : "rounded-md border border-red-700/25 bg-red-700/5 p-2"
      }
      data-cell={label}
    >
      <div className="text-xs font-medium">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      <div className="text-[11px] leading-tight text-muted-foreground">{hint}</div>
    </div>
  );
}

/** TP / FP / FN / TN for one dimension. The positive class is FAIL (ai-design §9.2). */
export function ConfusionPopover({
  dimension,
  confusion,
  splits,
}: {
  dimension: string;
  confusion: Confusion;
  splits?: { name: string; precision: number | null; recall: number | null; n: number }[];
}) {
  const name = DIMENSION_LABEL[dimension] ?? dimension;
  return (
    <Popover>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" aria-label={`${name} confusion matrix`} />}
      >
        <Grid2x2 aria-hidden />
        <span className="hidden sm:inline">Matrix</span>
      </PopoverTrigger>
      <PopoverContent className="w-80" align="end">
        <PopoverTitle>{name}: confusion matrix</PopoverTitle>
        <PopoverDescription>Positive class = FAIL. A true positive is a failure the evaluator caught.</PopoverDescription>
        <div className="grid grid-cols-2 gap-2">
          <Cell label="TP" value={confusion.tp ?? 0} hint="Label fail · evaluator fail" tone="good" />
          <Cell label="FN" value={confusion.fn ?? 0} hint="Label fail · evaluator pass (missed)" tone="bad" />
          <Cell label="FP" value={confusion.fp ?? 0} hint="Label pass · evaluator fail (false alarm)" tone="bad" />
          <Cell label="TN" value={confusion.tn ?? 0} hint="Label pass · evaluator pass" tone="good" />
        </div>
        {splits?.length ? (
          <ul className="space-y-0.5 text-xs text-muted-foreground">
            {splits.map((s) => (
              <li key={s.name} className="tabular-nums">
                <span className="capitalize">{s.name}</span>: P {fmtRatio(s.precision)} · R {fmtRatio(s.recall)} · n = {s.n}
              </li>
            ))}
          </ul>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function worst(a: MetState, b: MetState): MetState {
  const rank: MetState[] = ["not_met", "not_measured", "met", "reported"];
  return rank.indexOf(a) <= rank.indexOf(b) ? a : b;
}

/** One row per dimension: precision, recall, F1, n (positives / negatives), targets and met. */
export function PrecisionRecallTable({ summary }: { summary: EvalSummary }) {
  const dims = EVAL_DIMENSIONS.filter((d) => summary.per_dimension[d]);
  const compositionMissing = !summary.per_dimension.composition;
  return (
    <Table data-slot="precision-recall-table">
      <TableHeader>
        <TableRow>
          <TableHead>Dimension</TableHead>
          <TableHead className="text-right">Precision</TableHead>
          <TableHead className="text-right">Recall</TableHead>
          <TableHead className="text-right">F1</TableHead>
          <TableHead className="text-right">n (fail / pass)</TableHead>
          <TableHead>Target (P / R)</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="text-right">
            <span className="sr-only">Confusion matrix</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {dims.map((d) => {
          const m = summary.per_dimension[d]!;
          const { pos, neg } = posNeg(m);
          const pT = targetOf(summary, `precision.${d}`);
          const rT = targetOf(summary, `recall.${d}`);
          const hasTarget = Boolean(pT || rT);
          const state = hasTarget
            ? worst(metState(criterion(summary, `precision.${d}`)), metState(criterion(summary, `recall.${d}`)))
            : "reported";
          const splits = Object.entries(summary.per_split).flatMap(([name, s]) =>
            s[d] ? [{ name, precision: s[d].precision, recall: s[d].recall, n: s[d].n }] : [],
          );
          return (
            <TableRow key={d} data-dimension={d}>
              <TableCell className="font-medium">
                {DIMENSION_LABEL[d] ?? d}
                {d === "composition" ? (
                  <span className="block text-xs font-normal text-muted-foreground">{DIMENSION_HINT.composition}</span>
                ) : null}
                {m.n ? (
                  <SmallNNote n={m.n} className="block font-normal" />
                ) : (
                  <span className="block text-xs font-normal text-muted-foreground">No labels yet</span>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums" title={exact(m.precision)} data-col="precision">
                {fmtRatio(m.precision)}
              </TableCell>
              <TableCell className="text-right tabular-nums" title={exact(m.recall)} data-col="recall">
                {fmtRatio(m.recall)}
              </TableCell>
              <TableCell className="text-right tabular-nums" title={exact(m.f1)} data-col="f1">
                {fmtRatio(m.f1)}
              </TableCell>
              <TableCell className="text-right tabular-nums" data-col="n">
                {m.n} ({pos} / {neg})
              </TableCell>
              <TableCell className="text-muted-foreground tabular-nums">
                {hasTarget ? `${pT ?? "—"} / ${rT ?? "—"}` : "reported"}
              </TableCell>
              <TableCell>
                <MetBadge state={state} />
              </TableCell>
              <TableCell className="text-right">
                <ConfusionPopover dimension={d} confusion={m.confusion} splits={splits} />
              </TableCell>
            </TableRow>
          );
        })}
        {compositionMissing ? (
          <TableRow data-dimension="composition" data-status="not_checked">
            <TableCell className="font-medium">
              {DIMENSION_LABEL.composition}
              <span className="block text-xs font-normal text-muted-foreground">{DIMENSION_HINT.composition}</span>
            </TableCell>
            <TableCell colSpan={7} className="text-sm text-muted-foreground">
              {NOT_CHECKED}. This report predates the composition dimension.
            </TableCell>
          </TableRow>
        ) : null}
      </TableBody>
    </Table>
  );
}
