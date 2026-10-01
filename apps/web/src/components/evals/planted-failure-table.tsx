"use client";

import { ChevronLeft, ChevronRight, CircleCheck, CircleX, ImageOff } from "lucide-react";
import { useState } from "react";

import { CodeText } from "@/components/code-text";
import { MetBadge } from "@/components/evals/primitives";
import { DimensionDots } from "@/components/run/scorecard";
import { VerdictBadge } from "@/components/run/verdict-badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { EvalItem, PlantedClassMetrics, RateMetric } from "@/lib/api";
import { DIMENSION_LABEL, checkStatus } from "@/lib/checks";
import { MUTATION_INFO, exact, fmtPct, mutationLabel, plantedCaught } from "@/lib/evals";
import { apiUrl } from "@/lib/run-events";
import { cn } from "@/lib/utils";

function AdImage({
  url,
  alt,
  caption,
  missing = "Image not in the store. Run `make golden-import`.",
}: {
  url: string | null;
  alt: string;
  caption: string;
  missing?: string;
}) {
  return (
    <figure className="space-y-1.5">
      <figcaption className="text-xs font-medium text-muted-foreground">{caption}</figcaption>
      <div className="relative mx-auto aspect-[4/5] w-full max-w-[280px] overflow-hidden rounded-md border bg-muted">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element -- API-served, already ≤ 1024 px
          <img src={apiUrl(url)} alt={alt} className="absolute inset-0 size-full object-contain" />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-1 p-3 text-center text-xs text-muted-foreground">
            <ImageOff className="size-5" aria-hidden />
            <CodeText>{missing}</CodeText>
          </div>
        )}
      </div>
    </figure>
  );
}

/** The source image next to its planted version, the mutation, and the evaluator's reason. */
export function PlantedExampleDialog({
  mutation,
  expected,
  items,
  open,
  onOpenChange,
}: {
  mutation: string | null;
  expected: string[];
  items: EvalItem[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [index, setIndex] = useState(0);
  const item = items[Math.min(index, Math.max(0, items.length - 1))];
  const info = mutation ? MUTATION_INFO[mutation] : undefined;
  const caught = item ? plantedCaught(item, expected) : false;
  const dims = item ? Object.fromEntries(Object.entries(item.dimensions).map(([d, v]) => [d, checkStatus(v)])) : {};

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl" data-slot="planted-example">
        <DialogHeader>
          <DialogTitle>Planted failure: {mutationLabel(mutation)}</DialogTitle>
          <DialogDescription>
            {info ? `${info.how}. ` : ""}Made on purpose to test the evaluator; never shipped.
          </DialogDescription>
        </DialogHeader>
        {!item ? (
          <p className="text-sm text-muted-foreground">No items of this class in the report.</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="font-mono text-xs">{item.id}</span>
              <span className="flex items-center gap-2">
                {caught ? (
                  <VerdictBadge status="pass" label="Caught" />
                ) : (
                  <VerdictBadge status="fail" label="Missed" />
                )}
                <span className="text-xs text-muted-foreground">
                  expected to fail: {expected.map((d) => DIMENSION_LABEL[d] ?? d).join(", ") || "none"}
                </span>
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <AdImage url={item.source_image_url} alt={`Source ad for brief ${item.brief_id}`} caption="Source (E-nat)"
                missing="No source image for this item (generated from the brief, or not imported)."
              />
              <AdImage
                url={item.image_url}
                alt={`Planted ${mutationLabel(mutation)} version of brief ${item.brief_id}`}
                caption="Planted"
              />
            </div>
            <div className="space-y-2 rounded-md border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">Evaluator verdict</span>
                <VerdictBadge status={item.verdict} />
              </div>
              <DimensionDots verdicts={dims} />
              {item.evidence.length ? (
                <ul className="space-y-1 text-sm">
                  {item.evidence.map((e, i) => (
                    <li key={i} className="font-mono text-xs break-words">
                      {e}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">No evidence returned for this item.</p>
              )}
            </div>
            {items.length > 1 ? (
              <div className="flex items-center justify-between gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={index === 0}
                  onClick={() => setIndex((i) => Math.max(0, i - 1))}
                >
                  <ChevronLeft aria-hidden /> Previous
                </Button>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {index + 1} of {items.length}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={index >= items.length - 1}
                  onClick={() => setIndex((i) => Math.min(items.length - 1, i + 1))}
                >
                  Next <ChevronRight aria-hidden />
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** One row per planted class: how it is made, n, caught, catch rate, and an example. */
export function PlantedFailureTable({
  planted,
  knownGood,
  items,
}: {
  planted: PlantedClassMetrics[];
  knownGood?: RateMetric;
  /** Planted items of the report, for the example dialog; null while loading or unavailable. */
  items: EvalItem[] | null;
}) {
  // The chosen class outlives `open` so the dialog keeps its content while it animates closed.
  const [selected, setSelected] = useState<PlantedClassMetrics | null>(null);
  const [open, setOpen] = useState(false);
  const examples = selected && items ? items.filter((i) => i.mutation === selected.mutation) : [];
  return (
    <>
      <Table data-slot="planted-failure-table">
        <TableHeader>
          <TableRow>
            <TableHead>Planted class</TableHead>
            <TableHead className="hidden md:table-cell">How it is made</TableHead>
            <TableHead>Should fail</TableHead>
            <TableHead className="text-right">n</TableHead>
            <TableHead className="text-right">Caught</TableHead>
            <TableHead className="text-right">Catch rate</TableHead>
            <TableHead className="text-right">
              <span className="sr-only">Example</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {planted.map((p) => {
            const full = p.n > 0 && p.caught === p.n;
            return (
              <TableRow key={p.mutation} data-mutation={p.mutation}>
                <TableCell className="font-medium">
                  {mutationLabel(p.mutation)}
                  <span className="block font-mono text-[11px] font-normal text-muted-foreground">{p.mutation}</span>
                </TableCell>
                <TableCell className="hidden max-w-64 text-xs whitespace-normal text-muted-foreground md:table-cell">
                  {MUTATION_INFO[p.mutation]?.how ?? "—"}
                </TableCell>
                <TableCell className="text-xs">{p.expected.map((d) => DIMENSION_LABEL[d] ?? d).join(", ")}</TableCell>
                <TableCell className="text-right tabular-nums" data-col="n">
                  {p.n}
                </TableCell>
                <TableCell className="text-right tabular-nums" data-col="caught">
                  {p.caught}
                </TableCell>
                <TableCell className="text-right" data-col="rate">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 tabular-nums",
                      full ? "text-emerald-700 dark:text-emerald-400" : "text-amber-800 dark:text-amber-400",
                    )}
                    title={exact(p.rate)}
                  >
                    {full ? <CircleCheck className="size-3.5" aria-hidden /> : <CircleX className="size-3.5" aria-hidden />}
                    {fmtPct(p.rate)}
                  </span>
                  {p.missed?.length ? (
                    <span className="block text-[11px] text-muted-foreground">missed {p.missed.length}</span>
                  ) : null}
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="outline" size="sm" onClick={() => {
                      setSelected(p);
                      setOpen(true);
                    }} disabled={items === null}>
                    View example
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
          {knownGood && knownGood.n ? (
            <TableRow data-mutation="control_good">
              <TableCell className="font-medium">
                {mutationLabel("control_good")}
                <span className="block font-mono text-[11px] font-normal text-muted-foreground">control_good</span>
              </TableCell>
              <TableCell className="hidden max-w-64 text-xs whitespace-normal text-muted-foreground md:table-cell">
                {MUTATION_INFO.control_good!.how}
              </TableCell>
              <TableCell className="text-xs">Nothing (should pass)</TableCell>
              <TableCell className="text-right tabular-nums">{knownGood.n}</TableCell>
              <TableCell className="text-right tabular-nums">{knownGood.ok ?? 0} passed</TableCell>
              <TableCell className="text-right">
                <MetBadge state={knownGood.ok === knownGood.n ? "met" : "not_met"} />
              </TableCell>
              <TableCell />
            </TableRow>
          ) : null}
        </TableBody>
      </Table>
      <PlantedExampleDialog
        key={selected?.mutation ?? "none"}
        mutation={selected?.mutation ?? null}
        expected={selected?.expected ?? []}
        items={examples}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}
