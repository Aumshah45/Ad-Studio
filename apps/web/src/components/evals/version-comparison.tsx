import { ArrowRight, GitCompareArrows } from "lucide-react";

import { CommandLine, EmptyState } from "@/components/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { GoldenComparison, VersionSummary } from "@/lib/api";
import { DIMENSION_HINT, DIMENSION_LABEL } from "@/lib/checks";
import {
  EVAL_DIMENSIONS,
  comparisonState,
  exact,
  fmtPct,
  fmtRate,
  fmtRatio,
  outputCount,
  ppDelta,
} from "@/lib/evals";

const SET_NAME: Record<string, string> = { nat: "First attempt (E-nat)", final: "Shipped (E-final)" };
const V2_COMMANDS =
  "make golden-run      # the 20 briefs on orchestrator-4\nmake golden-export   # to data/golden/v2 (the default version)\nmake golden-sheet    # label v2 with rubric v2 (composition column)\nmake eval            # every version + the v1 → v2 comparison\nmake golden-import";

function DimensionName({ dim }: { dim: string }) {
  return (
    <>
      {DIMENSION_LABEL[dim] ?? dim}
      {dim === "composition" ? (
        <span className="block text-xs font-normal text-muted-foreground">{DIMENSION_HINT.composition}</span>
      ) : null}
    </>
  );
}

/** Per-dimension human pass rate per version, per image set, with the change first → last. */
function HumanPassRates({ versions, set }: { versions: VersionSummary[]; set: string }) {
  const first = versions[0]!;
  const last = versions[versions.length - 1]!;
  return (
    <Table aria-label={`Human pass rate, ${SET_NAME[set] ?? set}`} data-slot={`human-pass-${set}`}>
      <TableHeader>
        <TableRow>
          <TableHead>{SET_NAME[set] ?? set}</TableHead>
          {versions.map((v) => (
            <TableHead key={v.version} className="text-right font-mono">
              {v.version}
            </TableHead>
          ))}
          <TableHead className="text-right">Change</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {EVAL_DIMENSIONS.map((d) => (
          <TableRow key={d} data-dimension={d}>
            <TableCell className="font-medium">
              <DimensionName dim={d} />
            </TableCell>
            {versions.map((v) => {
              const m = v.human_pass_rates?.[set]?.[d];
              return (
                <TableCell key={v.version} className="text-right tabular-nums" data-col={v.version} title={exact(m?.rate)}>
                  {m?.n ? fmtRate(m) : <span className="text-xs text-muted-foreground">not labelled</span>}
                </TableCell>
              );
            })}
            <TableCell className="text-right text-xs text-muted-foreground tabular-nums" data-col="change">
              {ppDelta(first.human_pass_rates?.[set]?.[d], last.human_pass_rates?.[set]?.[d]) ?? "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** The composition pass rate per version: human-labelled and the evaluator's, per image set. */
function CompositionRates({ versions }: { versions: VersionSummary[] }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2" data-slot="composition-pass-rate">
      {(["nat", "final"] as const).map((set) => (
        <li key={set} className="space-y-2 rounded-lg border p-3">
          <div className="text-xs text-muted-foreground">Composition pass rate · {SET_NAME[set]}</div>
          <div className="flex flex-wrap items-center gap-2 tabular-nums">
            {versions.map((v, i) => {
              const human = v.human_pass_rates?.[set]?.composition;
              const judged = v.evaluator_pass_rates?.[set]?.composition;
              return (
                <span key={v.version} className="inline-flex items-center gap-2" data-version={v.version}>
                  {i > 0 ? <ArrowRight className="size-4 text-muted-foreground" aria-label="to" /> : null}
                  <span>
                    <span className="font-mono text-xs text-muted-foreground">{v.version} </span>
                    <span className="text-xl font-semibold text-primary" title={exact(human?.rate)}>
                      {human?.n ? fmtPct(human.rate) : "—"}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {human?.n ? `human ${human.ok ?? 0}/${human.n}` : "not labelled"} · evaluator {fmtRate(judged)}
                    </span>
                  </span>
                </span>
              );
            })}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Evaluator precision / recall per dimension per version (positive = FAIL). */
function EvaluatorPR({ versions }: { versions: VersionSummary[] }) {
  return (
    <Table aria-label="Evaluator precision and recall per version" data-slot="comparison-pr">
      <TableHeader>
        <TableRow>
          <TableHead>Dimension</TableHead>
          {versions.map((v) => (
            <TableHead key={v.version} className="text-right">
              <span className="font-mono">{v.version}</span> P / R
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {EVAL_DIMENSIONS.map((d) => (
          <TableRow key={d} data-dimension={d}>
            <TableCell className="font-medium">
              <DimensionName dim={d} />
            </TableCell>
            {versions.map((v) => {
              const m = v.per_dimension?.[d];
              return (
                <TableCell key={v.version} className="text-right tabular-nums" data-col={v.version}>
                  {m?.n ? (
                    <>
                      {fmtRatio(m.precision)} / {fmtRatio(m.recall)}
                      <span className="block text-xs text-muted-foreground">n = {m.n}</span>
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground">no labels</span>
                  )}
                </TableCell>
              );
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Pipeline headline per version: first attempt, after repair, $/ad, evaluator and pipeline. */
function PipelineRow({ versions }: { versions: VersionSummary[] }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2" data-slot="comparison-pipeline">
      {versions.map((v) => (
        <li key={v.version} className="space-y-1 rounded-lg border p-3 text-sm" data-version={v.version}>
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-mono font-medium">{v.version}</span>
            <span className="font-mono text-xs text-muted-foreground">
              {v.evaluator_version}
              {v.pipeline_versions?.length ? ` · ${v.pipeline_versions.join(", ")}` : ""}
            </span>
          </div>
          <p className="tabular-nums">
            First attempt {fmtPct(v.first_attempt_pass_rate)} → after repair {fmtPct(v.after_repair_pass_rate)}
          </p>
          <p className="text-xs text-muted-foreground tabular-nums">
            {outputCount(v)} outputs · {v.counts?.human_labelled ?? 0} human-labelled
            {v.cost_per_approved_ad != null ? ` · $${v.cost_per_approved_ad.toFixed(3)} per approved ad` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}

/**
 * Golden v1 → v2 (ADR-007): what the composition work changed. Human pass rates per dimension,
 * the composition pass rate and the evaluator's precision / recall, per version. Empty states when
 * v2 hasn't been evaluated, has no outputs, or has no labels yet.
 */
export function VersionComparison({ comparison }: { comparison: GoldenComparison | null | undefined }) {
  const state = comparisonState(comparison);
  if (state.kind === "none") {
    return (
      <EmptyState
        icon={<GitCompareArrows aria-hidden />}
        title="No v1 → v2 comparison yet"
        body="Golden v2 (realistic product scale and natural integration) hasn't been evaluated. The comparison appears on the newest report once both versions have one."
      >
        <CommandLine command={V2_COMMANDS} />
      </EmptyState>
    );
  }
  if (state.kind === "no-outputs") {
    return (
      <div className="space-y-3" data-slot="version-comparison" data-state="no-outputs">
        <EmptyState
          icon={<GitCompareArrows aria-hidden />}
          title="v2 has no outputs yet"
          body="The comparison is ready, but golden v2 has no generated ads to compare. Run the 20 briefs into v2, then re-evaluate."
        >
          <CommandLine command={V2_COMMANDS} />
        </EmptyState>
        <PipelineRow versions={state.versions} />
      </div>
    );
  }
  const unlabelled = state.versions.filter((v) => !state.labelled[v.version]).map((v) => v.version);
  return (
    <div className="space-y-4" data-slot="version-comparison" data-state="ok">
      {unlabelled.length ? (
        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground" data-slot="comparison-unlabelled">
          No human labels on {unlabelled.join(" and ")} yet, so its human pass rates and precision / recall are
          blank. Label it with rubric v2 (<code className="font-mono">make golden-sheet</code>) and re-run{" "}
          <code className="font-mono">make eval</code>.
        </p>
      ) : null}
      <CompositionRates versions={state.versions} />
      <div className="grid gap-4 lg:grid-cols-2">
        <HumanPassRates versions={state.versions} set="nat" />
        <HumanPassRates versions={state.versions} set="final" />
      </div>
      <EvaluatorPR versions={state.versions} />
      <PipelineRow versions={state.versions} />
      <p className="text-xs text-muted-foreground">
        Human rates count human-labelled images only; composition is labelled from rubric v2.
      </p>
    </div>
  );
}
