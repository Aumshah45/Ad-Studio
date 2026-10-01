"use client";

import { ClipboardCheck, RotateCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { CodeText } from "@/components/code-text";
import { CommandLine, EmptyState } from "@/components/empty-state";
import { AgreementPanel } from "@/components/evals/agreement-panel";
import { CostLatencyPanel } from "@/components/evals/cost-latency-panel";
import { EvalScoreboard } from "@/components/evals/eval-scoreboard";
import { PlantedFailureTable } from "@/components/evals/planted-failure-table";
import { PrecisionRecallTable } from "@/components/evals/precision-recall-table";
import { DryRunBadge, SectionHeading } from "@/components/evals/primitives";
import { ReportProvenance, formatReportTime } from "@/components/evals/report-provenance";
import { ReportWarnings } from "@/components/evals/report-warnings";
import { TestResultsCard } from "@/components/evals/test-results-card";
import { GoldenVersionSwitcher, syncVersionParam } from "@/components/evals/golden-version-switcher";
import { VersionComparison } from "@/components/evals/version-comparison";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { EvalItem, EvalSummary, GoldenComparison } from "@/lib/api";
import { asGoldenVersion, loadItems, loadSummary, type GoldenVersion, type Loaded } from "@/lib/evals";

const SECTIONS = [
  ["scoreboard", "Scoreboard"],
  ["comparison", "v1 → v2"],
  ["precision-recall", "Precision / recall"],
  ["planted", "Planted failures"],
  ["agreement", "Human agreement"],
  ["cost-latency", "Cost and latency"],
  ["tests", "Offline checks"],
  ["provenance", "Provenance"],
] as const;

function Header({
  summary,
  version,
  onVersion,
}: {
  summary?: EvalSummary;
  version: GoldenVersion | null;
  onVersion: (v: GoldenVersion) => void;
}) {
  const natural = summary?.counts.nat;
  const planted = summary?.counts.plant;
  const labelled = summary?.counts.human_labelled ?? 0;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Evaluator</h1>
          {summary?.dry_run ? <DryRunBadge /> : null}
        </div>
        <GoldenVersionSwitcher value={version ?? asGoldenVersion(summary?.golden_version)} onChange={onVersion} />
      </div>
      <p className="max-w-3xl text-sm text-muted-foreground">
        Is the evaluator right?{" "}
        {natural != null && planted != null
          ? `Measured on ${natural} natural outputs (${labelled ? `${labelled} human-labelled` : "no human labels yet"}), plus ${planted} planted failures with known answers.`
          : "Measured on the natural golden outputs with human labels, plus planted failures with known answers."}
      </p>
      {summary ? (
        <p className="text-xs text-muted-foreground">
          Report <span className="font-mono">{summary.report_file}</span>
          {summary.golden_version ? (
            <>
              {" "}
              · golden <span className="font-mono">{summary.golden_version}</span>
            </>
          ) : null}{" "}
          · evaluator {summary.evaluator_version} ·{" "}
          {formatReportTime(summary.created_at)}
        </p>
      ) : null}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading the evaluator report">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-36 motion-reduce:animate-none" />
        ))}
      </div>
      <Skeleton className="h-48 motion-reduce:animate-none" />
      <Skeleton className="h-64 motion-reduce:animate-none" />
    </div>
  );
}

export function NoReportState({ version = null }: { version?: GoldenVersion | null }) {
  return (
    <EmptyState
      icon={<ClipboardCheck aria-hidden />}
      title={version ? `No ${version} evaluation report yet` : "No evaluation report yet"}
      body={
        <CodeText>
          {"The dashboard reads the latest report from `make eval`. Build the golden set, run the evaluation offline (cached verdicts, $0), then import it for the dashboard."}
        </CodeText>
      }
    >
      <CommandLine command={"make golden-run      # 20 briefs through the pipeline\nmake golden-export plant\nmake eval            # offline, cached verdicts\nmake golden-import"} />
      <p className="text-xs text-muted-foreground">
        No API key? <code className="font-mono">make golden-dryrun</code> runs the whole chain on fake clients.
      </p>
    </EmptyState>
  );
}

/** `/evals`: the latest evaluator report, one scroll with anchored sections (docs/ux.md). */
export function EvalDashboard({ initialVersion = null }: { initialVersion?: GoldenVersion | null } = {}) {
  const [version, setVersion] = useState<GoldenVersion | null>(initialVersion);
  const [summary, setSummary] = useState<Loaded<EvalSummary> | null>(null);
  const [planted, setPlanted] = useState<EvalItem[] | null>(null);
  const [natural, setNatural] = useState<EvalItem[] | null>(null);
  /** The v1 → v2 comparison lives on the newest report; fetched too when an older version is shown. */
  const [comparison, setComparison] = useState<GoldenComparison | null | undefined>(undefined);

  const load = useCallback(async () => {
    const s = await loadSummary(version);
    setSummary(s);
    if (s.kind !== "ok") return;
    const newest = s.data.comparison || !version ? null : loadSummary();
    const [p, n, latest] = await Promise.all([loadItems("planted", version), loadItems("natural", version), newest]);
    setPlanted(p.kind === "ok" ? p.data : null);
    setNatural(n.kind === "ok" ? n.data : null);
    setComparison(s.data.comparison ?? (latest?.kind === "ok" ? latest.data.comparison : null) ?? null);
  }, [version]);

  const changeVersion = useCallback((v: GoldenVersion) => {
    setVersion(v);
    setSummary(null);
    setPlanted(null);
    setNatural(null);
    setComparison(undefined);
    syncVersionParam(v);
  }, []);

  useEffect(() => {
    const t = setTimeout(load, 0);
    return () => clearTimeout(t);
  }, [load]);

  if (summary === null) {
    return (
      <div className="mx-auto max-w-6xl space-y-6">
        <Header version={version} onVersion={changeVersion} />
        <LoadingState />
      </div>
    );
  }
  if (summary.kind === "no-report") {
    return (
      <div className="mx-auto max-w-6xl space-y-6">
        <Header version={version} onVersion={changeVersion} />
        <NoReportState version={version} />
      </div>
    );
  }
  if (summary.kind === "error") {
    return (
      <div className="mx-auto max-w-6xl space-y-6">
        <Header version={version} onVersion={changeVersion} />
        <Alert variant="destructive">
          <AlertTitle>Couldn&apos;t load the evaluator report</AlertTitle>
          <AlertDescription>
            <CodeText>{summary.message}</CodeText>
          </AlertDescription>
        </Alert>
        <Button
          variant="outline"
          onClick={() => {
            setSummary(null);
            void load();
          }}
        >
          <RotateCw aria-hidden /> Retry
        </Button>
      </div>
    );
  }

  const s = summary.data;
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <Header summary={s} version={version} onVersion={changeVersion} />
      {s.dry_run ? (
        <Alert className="border-2 border-amber-700/60 bg-amber-700/5">
          <AlertTitle className="flex flex-wrap items-center gap-2">
            <DryRunBadge /> Not a real evaluation
          </AlertTitle>
          <AlertDescription>
            This report was built with the fake image and vision clients (<code className="font-mono">make golden-dryrun</code>).
            It proves the pipeline and the metrics wiring end to end; it says nothing about model quality.
          </AlertDescription>
        </Alert>
      ) : null}
      <ReportWarnings warnings={s.warnings} dryRunShown={s.dry_run} />
      <nav aria-label="Sections" className="-mx-1 flex gap-1 overflow-x-auto pb-1 text-sm">
        {SECTIONS.map(([id, label]) => (
          <a
            key={id}
            href={`#${id}`}
            className="shrink-0 rounded-md px-2 py-1 whitespace-nowrap text-muted-foreground outline-offset-2 hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          >
            {label}
          </a>
        ))}
      </nav>

      <section aria-labelledby="scoreboard" className="space-y-3">
        <SectionHeading id="scoreboard" title="Scoreboard" description="Positive class = FAIL: recall is the share of real failures the evaluator caught." />
        <EvalScoreboard summary={s} />
      </section>

      <section aria-labelledby="comparison" className="space-y-3">
        <SectionHeading
          id="comparison"
          title="Golden v1 → v2"
          description="What realistic product scale and natural integration (ADR-007) changed: human pass rates per dimension, the composition pass rate and the evaluator's precision / recall per version."
        />
        {comparison === undefined ? (
          <Skeleton className="h-40 motion-reduce:animate-none" />
        ) : (
          <VersionComparison comparison={comparison} />
        )}
      </section>

      <section aria-labelledby="precision-recall" className="space-y-3">
        <SectionHeading
          id="precision-recall"
          title="Precision and recall per dimension"
          description="E-nat ∪ E-plant. Open a matrix for TP / FP / FN / TN and the per-split numbers."
        />
        <PrecisionRecallTable summary={s} />
      </section>

      <section aria-labelledby="planted" className="space-y-3">
        <SectionHeading
          id="planted"
          title="Planted failures"
          description="Each planted item breaks exactly one dimension of an approved ad, so its answer is known."
        />
        <PlantedFailureTable planted={s.planted} knownGood={s.known_good} items={planted} />
      </section>

      <section aria-labelledby="agreement" className="space-y-3">
        <SectionHeading id="agreement" title="Human agreement" />
        <AgreementPanel summary={s} natural={natural} />
      </section>

      <section aria-labelledby="cost-latency" className="space-y-3">
        <SectionHeading id="cost-latency" title="Cost and latency per approved ad" />
        <CostLatencyPanel summary={s} />
      </section>

      <section aria-labelledby="tests" className="space-y-3">
        <SectionHeading id="tests" title="Offline checks" />
        <TestResultsCard summary={s} />
      </section>

      <section aria-labelledby="provenance" className="space-y-3">
        <SectionHeading id="provenance" title="Report provenance" />
        <ReportProvenance summary={s} />
      </section>
    </div>
  );
}
