import type { ReactNode } from "react";

import { DryRunBadge } from "@/components/evals/primitives";
import type { EvalSummary } from "@/lib/api";

export function formatReportTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

function list(v: readonly string[] | null | undefined): string | null {
  return v && v.length ? v.join(", ") : null;
}

/** Tesseract packs can number 100+; show the count and the first few, all of them on hover. */
function languages(v: readonly string[] | null | undefined): ReactNode {
  if (!v || !v.length) return null;
  if (v.length <= 8) return v.join(", ");
  return <span title={v.join(", ")}>{`${v.length} packs (${v.slice(0, 6).join(", ")}, …)`}</span>;
}

type Row = [string, ReactNode];

/** Report rows first, then the models and engines behind the numbers (`provenance`), when present. */
export function provenanceRows(summary: EvalSummary): { report: Row[]; sources: Row[] } {
  const counts = Object.entries(summary.counts);
  const p = summary.provenance;
  const report: Row[] = [
    ["Report id", summary.report_id],
    ["Report file", `evals/reports/${summary.report_file}`],
    ["Created at", <time key="t" dateTime={summary.created_at}>{formatReportTime(summary.created_at)}</time>],
    ["Evaluator version", summary.evaluator_version],
    ["Dataset version", summary.dataset_version],
    ["Commit", summary.git_sha ?? p?.git_sha ?? "—"],
    ["Items", counts.length ? counts.map(([k, v]) => `${k} ${v}`).join(" · ") : "—"],
  ];
  if (!p) return { report, sources: [] };
  const judge = [p.judge_client, p.judge_model].filter(Boolean).join(" · ");
  const ocr = [p.ocr_engine, p.ocr_version].filter(Boolean).join(" ");
  const prompts = Object.entries(p.prompt_versions ?? {})
    .map(([k, v]) => `${k}@${v}`)
    .join(", ");
  const snapshot = p.snapshot_file
    ? [
        p.snapshot_file,
        p.snapshot_entries != null ? `${p.snapshot_entries} verdicts` : null,
        p.snapshot_evaluator_version,
        p.snapshot_recorded_at ? formatReportTime(p.snapshot_recorded_at) : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  const candidates: [string, ReactNode | null][] = [
    ["Vision judge", judge || null],
    ["OCR engine", ocr || null],
    ["OCR languages", languages(p.ocr_languages)],
    ["Image clients", list(p.image_clients)],
    ["Image models", list(p.image_models)],
    ["Image prompts", list(p.image_prompt_versions)],
    ["Evaluator prompts", prompts || null],
    ["Verdict snapshot", snapshot],
    ["Network attempts", p.network_attempts != null ? String(p.network_attempts) : null],
    ["Unlabelled = all-pass", p.assume_pass_labels ? "yes (dry run only)" : null],
  ];
  return { report, sources: candidates.filter((r): r is Row => r[1] != null && r[1] !== "") };
}

function Rows({ rows, label }: { rows: Row[]; label: string }) {
  return (
    <dl aria-label={label} className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_minmax(0,1fr)]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 font-mono text-xs break-all sm:pt-0.5">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Where the numbers came from: report, versions, commit, counts, models, OCR, snapshot, dry run. */
export function ReportProvenance({ summary }: { summary: EvalSummary }) {
  const { report, sources } = provenanceRows(summary);
  return (
    <div className="space-y-4 rounded-lg border p-4" data-slot="report-provenance">
      {summary.dry_run ? (
        <div className="flex flex-wrap items-center gap-2">
          <DryRunBadge />
          <span className="text-xs text-muted-foreground">
            Images and verdicts came from fake clients. These numbers test the pipeline, not model quality.
          </span>
        </div>
      ) : null}
      <Rows rows={report} label="Report" />
      {sources.length ? (
        <div className="space-y-2 border-t pt-3">
          <h3 className="text-sm font-semibold">Models and engines</h3>
          <Rows rows={sources} label="Models and engines" />
        </div>
      ) : (
        <p className="border-t pt-3 text-xs text-muted-foreground">
          This report predates model provenance. Re-run <code className="font-mono">make eval</code> to record the judge,
          OCR and image models.
        </p>
      )}
    </div>
  );
}
