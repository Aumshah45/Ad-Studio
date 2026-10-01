"use client";

import { ArrowRight, ExternalLink, ImageOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { briefLine, marketLabel, tileVerdicts } from "@/components/batch/ad-tile";
import { DimensionDots } from "@/components/run/scorecard";
import { VerdictBadge } from "@/components/run/verdict-badge";
import { buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { runsGetRun, type CandidateView, type EvalSummary, type GoldenSources, type RunDetail } from "@/lib/api";
import { SET_LABEL, type BatchAd } from "@/lib/batch";
import { DIMENSION_LABEL, NOT_CHECKED, checkStatus, dimensionTitle } from "@/lib/checks";
import { EVAL_DIMENSIONS, type Loaded } from "@/lib/evals";
import { formatDuration, formatUsd } from "@/lib/format";
import { apiUrl, attemptTitle } from "@/lib/run-events";
import { loadSources, sourceFor, sourceHref } from "@/lib/sources";
import { cn } from "@/lib/utils";

const OUTCOME_LABEL: Record<string, string> = {
  tp: "TP · caught",
  fp: "FP · false alarm",
  fn: "FN · missed",
  tn: "TN · correct pass",
};

function labelStatus(v: boolean | null | undefined): string {
  return v === true ? "pass" : v === false ? "fail" : "unlabelled";
}

/** Human label next to the evaluator's verdict for each dimension, with the confusion outcome. */
export function LabelsVsVerdicts({ ad }: { ad: BatchAd }) {
  const { item } = ad;
  const hasLabel = item.label != null && item.label_source !== "none";
  return (
    <div className="space-y-2" data-slot="labels-vs-verdicts">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Dimension</TableHead>
            <TableHead>Human label</TableHead>
            <TableHead>Evaluator</TableHead>
            <TableHead>Outcome</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {EVAL_DIMENSIONS.map((d) => {
            const human = item.label?.[d];
            const evaluator = item.dimensions[d];
            // Older reports have no composition key: not checked, never "unsure".
            const notChecked = !(d in item.dimensions) && d === "composition";
            const outcome = item.outcomes[d];
            const disagree = human != null && evaluator != null && human !== evaluator;
            return (
              <TableRow key={d} data-dimension={d} className={cn(disagree && "bg-amber-700/5")}>
                <TableCell className="font-medium" title={dimensionTitle(d)}>
                  {DIMENSION_LABEL[d]}
                </TableCell>
                <TableCell data-col="human">
                  {human == null ? (
                    <span className="text-xs text-muted-foreground">{hasLabel ? "not labelled" : "—"}</span>
                  ) : (
                    <VerdictBadge status={labelStatus(human)} />
                  )}
                </TableCell>
                <TableCell data-col="evaluator">
                  {notChecked ? (
                    <VerdictBadge status="neutral" label={NOT_CHECKED} />
                  ) : (
                    <VerdictBadge status={checkStatus(evaluator)} />
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground" data-col="outcome">
                  {outcome ? OUTCOME_LABEL[outcome] : "—"}
                  {disagree ? <span className="block text-amber-800 dark:text-amber-400">disagrees</span> : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <p className="text-xs text-muted-foreground">
        {hasLabel
          ? `Label source: ${item.label_source}. Positive class = FAIL.`
          : (
            <>
              No human label on this image yet. Label the 20 natural outputs with{" "}
              <code className="font-mono">make golden-sheet</code>.
            </>
          )}
      </p>
    </div>
  );
}

function Thumb({ url, alt }: { url: string | null; alt: string }) {
  return (
    <div className="relative aspect-[4/5] w-20 shrink-0 overflow-hidden rounded border bg-muted">
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element -- API-served thumbnail
        <img src={apiUrl(url)} alt={alt} className="absolute inset-0 size-full object-contain" />
      ) : (
        <ImageOff className="absolute inset-0 m-auto size-4 text-muted-foreground" aria-hidden />
      )}
    </div>
  );
}

function candidateStatus(c: CandidateView): string {
  if (c.evaluation?.verdict) return c.evaluation.verdict;
  return c.status;
}

/**
 * Attempt lineage. With the golden run in this database: every candidate (initial → repair →
 * overlay) with its model and verdict. Without it: the brief's first attempt (E-nat) and its
 * shipped ad (E-final) from the report.
 */
export function AttemptHistory({
  ad,
  sibling,
  detail,
}: {
  ad: BatchAd;
  sibling: BatchAd | null;
  detail: RunDetail | null;
}) {
  if (detail && detail.candidates.length) {
    const ordered = [...detail.candidates].sort(
      (a, b) => a.attempt - b.attempt || a.slot - b.slot || a.created_at.localeCompare(b.created_at),
    );
    const approved = detail.approved_candidate_id;
    return (
      <ol className="space-y-2" data-slot="attempt-history">
        {ordered.map((c) => (
          <li key={c.id} className="flex gap-3 rounded-md border p-2">
            <Thumb url={c.image_url} alt={`${attemptTitle(c)} (${c.kind})`} />
            <div className="min-w-0 space-y-1 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">
                  {attemptTitle(c)} · {c.kind}
                </span>
                <VerdictBadge status={candidateStatus(c)} />
                {c.id === approved ? <VerdictBadge status="approved" label="Shipped" /> : null}
              </div>
              <div className="font-mono break-all text-muted-foreground">{c.served_model ?? c.requested_model ?? "—"}</div>
              {c.parent_candidate_id ? (
                <div className="text-muted-foreground">from {c.parent_candidate_id.slice(0, 8)}</div>
              ) : null}
              {c.repair_instruction ? <p className="line-clamp-3">{c.repair_instruction}</p> : null}
            </div>
          </li>
        ))}
      </ol>
    );
  }
  const nat = ad.set === "nat" ? ad : sibling;
  const fin = ad.set === "final" ? ad : sibling;
  const same = nat?.item.image_id != null && nat.item.image_id === fin?.item.image_id;
  return (
    <div className="space-y-2" data-slot="attempt-history">
      <ol className="flex flex-wrap items-center gap-3">
        {[nat, fin].map((a, i) =>
          a ? (
            <li key={a.id} className={cn("flex items-center gap-3", a.id === ad.id && "font-medium")}>
              {i === 1 ? <ArrowRight className="size-4 text-muted-foreground" aria-label="then" /> : null}
              <Thumb url={a.item.image_url} alt={SET_LABEL[a.set]} />
              <div className="space-y-1 text-xs">
                <div>{SET_LABEL[a.set]}</div>
                <VerdictBadge status={a.item.verdict} />
              </div>
            </li>
          ) : null,
        )}
      </ol>
      <p className="text-xs text-muted-foreground">
        {same
          ? "The first attempt shipped unchanged (same image)."
          : "Intermediate repairs are in the golden run, which is not in this database."}
      </p>
    </div>
  );
}

/** Label / value rows: models, prompt version, cost, latency, versions and sources. */
export function ProvenanceList({ items }: { items: { label: string; value: ReactNode; mono?: boolean }[] }) {
  return (
    <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm" data-slot="provenance-list">
      {items.map((i) => (
        <div key={i.label} className="contents">
          <dt className="text-muted-foreground">{i.label}</dt>
          <dd className={cn("min-w-0 break-words", i.mono && "font-mono text-xs sm:pt-0.5")}>{i.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The product photo's source, author and licence, linked to the sources view. */
export function ProductSource({ productId, sources }: { productId: string; sources: Loaded<GoldenSources> | null }) {
  const src = sources?.kind === "ok" ? sourceFor(sources.data, productId) : null;
  const link = (
    <Link
      href={sourceHref(productId)}
      className="inline-flex items-center gap-1 rounded-sm text-primary underline-offset-2 outline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
    >
      All sources and licences <ArrowRight className="size-3" aria-hidden />
    </Link>
  );
  if (!src) {
    return (
      <span className="space-y-0.5" data-slot="product-source">
        <span className="block">
          Product photo <span className="font-mono text-xs">{productId}</span>
          {sources === null ? " · loading source…" : sources.kind === "ok" ? " · not listed in SOURCES.md" : ""}
        </span>
        {link}
      </span>
    );
  }
  return (
    <span className="block space-y-0.5" data-slot="product-source">
      <span className="block">
        {src.source_url ? (
          <a href={src.source_url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
            {src.source_title}
          </a>
        ) : (
          src.source_title
        )}{" "}
        by {src.author}
      </span>
      <span className="flex flex-wrap items-center gap-1.5">
        <span>{src.licence}</span>
        {src.share_alike ? (
          <span className="rounded-md border border-amber-700/25 bg-amber-700/10 px-1.5 text-xs text-amber-800 dark:text-amber-300">
            share-alike
          </span>
        ) : null}
      </span>
      {link}
    </span>
  );
}

function provenanceItems(
  ad: BatchAd,
  detail: RunDetail | null,
  summary: EvalSummary | null,
  sources: Loaded<GoldenSources> | null,
) {
  const run = ad.run;
  const shipped = detail?.candidates.find((c) => c.id === (detail.approved_candidate_id ?? detail.best_candidate_id));
  const first = detail?.candidates.find((c) => c.attempt === Math.min(...detail.candidates.map((x) => x.attempt)));
  const cand = ad.set === "final" ? shipped : first;
  const missing = ad.runId ? "run not in this database" : "no golden run for this image";
  const cost = run?.cost_usd ?? detail?.cost_usd;
  const latency = run?.latency_ms ?? detail?.latency_ms;
  const repairs = run?.repair_count ?? detail?.run.repair_count;
  return [
    { label: "Product", value: ad.brief.productName ? `${ad.brief.productName} (${ad.productId})` : ad.productId },
    { label: "Image model", value: cand?.served_model ?? cand?.requested_model ?? missing, mono: Boolean(cand) },
    { label: "Prompt version", value: cand?.prompt_version ?? missing, mono: Boolean(cand?.prompt_version) },
    { label: "Run cost", value: cost != null ? formatUsd(cost) : missing },
    { label: "Run latency", value: latency != null ? formatDuration(latency) : missing },
    { label: "Repairs", value: repairs != null ? String(repairs) : missing },
    { label: "Evaluator", value: summary?.evaluator_version ?? "—", mono: true },
    { label: "Dataset", value: summary?.dataset_version ?? "—", mono: true },
    { label: "Split", value: ad.item.split },
    { label: "Photo source", value: <ProductSource productId={ad.productId} sources={sources} /> },
  ];
}

/** Full detail for one golden ad: image, verdicts vs human labels, attempt lineage, provenance. */
export function AdDetailSheet({
  ad,
  sibling,
  summary,
  open,
  onOpenChange,
}: {
  ad: BatchAd | null;
  sibling: BatchAd | null;
  summary: EvalSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const runId = ad?.runId ?? null;
  const [detail, setDetail] = useState<{ runId: string; data: RunDetail | null } | null>(null);
  const [sources, setSources] = useState<Loaded<GoldenSources> | null>(null);

  useEffect(() => {
    if (!open || sources?.kind === "ok") return;
    let live = true;
    void loadSources().then((r) => live && setSources(r));
    return () => {
      live = false;
    };
  }, [open, sources?.kind]);

  useEffect(() => {
    if (!open || !runId || detail?.runId === runId) return;
    let live = true;
    runsGetRun({ path: { run_id: runId } })
      .then((r) => live && setDetail({ runId, data: r.data ?? null }))
      .catch(() => live && setDetail({ runId, data: null }));
    return () => {
      live = false;
    };
  }, [open, runId, detail?.runId]);

  const runDetail = detail && detail.runId === runId ? detail.data : null;
  const loadingDetail = Boolean(runId) && detail?.runId !== runId;
  const line = ad ? briefLine(ad) : null;
  const market = marketLabel(ad?.brief.geographyCode);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-xl" data-slot="ad-detail-sheet">
        {ad ? (
          <>
            <SheetHeader>
              <SheetTitle className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{ad.briefId}</span>
                <span className="text-sm font-normal text-muted-foreground">{SET_LABEL[ad.set]}</span>
                <VerdictBadge status={ad.item.verdict} />
              </SheetTitle>
              <SheetDescription>
                {[line, ad.brief.requiredText ? `\u201c${ad.brief.requiredText}\u201d` : null, ad.brief.aspectRatio]
                  .filter(Boolean)
                  .join(" · ") || `Product ${ad.productId}`}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-6 px-4 pb-6">
              <div className="relative mx-auto aspect-[4/5] w-full max-w-[400px] overflow-hidden rounded-md border bg-muted">
                {ad.item.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element -- API-served, already ≤ 1024 px
                  <img
                    src={apiUrl(ad.item.image_url)}
                    alt={`Brief ${ad.briefId}${market ? `, ${market}` : ""} ad (no description available).`}
                    className="absolute inset-0 size-full object-contain"
                  />
                ) : (
                  <div className="flex size-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
                    Image not in the store. Run `make golden-import`.
                  </div>
                )}
              </div>
              <DimensionDots verdicts={tileVerdicts(ad)} />
              {ad.item.evidence.length ? (
                <ul className="space-y-1">
                  {ad.item.evidence.map((e, i) => (
                    <li key={i} className="font-mono text-xs break-words">
                      {e}
                    </li>
                  ))}
                </ul>
              ) : null}

              <section className="space-y-2" aria-labelledby="sheet-labels">
                <h3 id="sheet-labels" className="text-sm font-semibold">
                  Human labels vs evaluator
                </h3>
                <LabelsVsVerdicts ad={ad} />
              </section>

              <section className="space-y-2" aria-labelledby="sheet-attempts">
                <h3 id="sheet-attempts" className="text-sm font-semibold">
                  Attempt history
                </h3>
                {loadingDetail ? (
                  <Skeleton className="h-24 motion-reduce:animate-none" />
                ) : (
                  <AttemptHistory ad={ad} sibling={sibling} detail={runDetail} />
                )}
              </section>

              <section className="space-y-2" aria-labelledby="sheet-provenance">
                <h3 id="sheet-provenance" className="text-sm font-semibold">
                  Provenance
                </h3>
                <ProvenanceList items={provenanceItems(ad, runDetail, summary, sources)} />
              </section>

              {ad.tags.length ? (
                <ul className="flex flex-wrap gap-1.5" aria-label="Tags">
                  {ad.tags.map((t) => (
                    <li key={t} className="rounded-md border px-2 py-0.5 text-xs text-muted-foreground">
                      {t}
                    </li>
                  ))}
                </ul>
              ) : null}

              {ad.runId ? (
                <Link href={`/runs/${ad.runId}`} className={buttonVariants({ variant: "outline" })}>
                  <ExternalLink aria-hidden /> Open the run
                </Link>
              ) : null}
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
