import { CodeText } from "@/components/code-text";
import { MetBadge } from "@/components/evals/primitives";
import type { EvalSummary } from "@/lib/api";
import { criterion, exact, fmtPct, metState, type MetState } from "@/lib/evals";

interface CheckRow {
  id: string;
  name: string;
  result: string;
  title?: string;
  state: MetState;
}

/** Offline, deterministic checks recorded in the report (criteria, planted classes, known-good). */
export function testRows(summary: EvalSummary): CheckRow[] {
  const rows: CheckRow[] = [];
  for (const id of ["network_calls", "resolution_ok_rate", "hemisphere_accuracy"]) {
    const c = criterion(summary, id);
    if (!c) continue;
    const result = id === "network_calls" ? String(c.now ?? "—") : fmtPct(c.now);
    rows.push({
      id,
      name: c.metric,
      result: c.note ? `${result} (${c.note})` : result,
      title: exact(c.now),
      state: metState(c),
    });
  }
  const hemi = summary.hemisphere;
  if (!criterion(summary, "hemisphere_accuracy") && hemi?.n) {
    rows.push({
      id: "hemisphere_accuracy",
      name: "Planner effective season, hemisphere cases",
      result: `${hemi.ok ?? 0} of ${hemi.n}`,
      title: exact(hemi.rate),
      state: hemi.ok === hemi.n ? "met" : "not_met",
    });
  }
  if (summary.planted.length) {
    const n = summary.planted.reduce((a, p) => a + p.n, 0);
    const caught = summary.planted.reduce((a, p) => a + p.caught, 0);
    const full = summary.planted.filter((p) => p.n > 0 && p.caught === p.n).length;
    rows.push({
      id: "planted",
      name: `Planted failures caught (${full} of ${summary.planted.length} classes at 100%)`,
      result: `${caught} of ${n}`,
      state: caught === n ? "met" : "not_met",
    });
  }
  const kg = summary.known_good;
  if (kg.n) {
    rows.push({
      id: "known_good",
      name: "Known-good controls pass",
      result: `${kg.ok ?? 0} of ${kg.n}`,
      state: kg.ok === kg.n ? "met" : "not_met",
    });
  }
  return rows;
}

/**
 * The report's offline checks. `make eval` runs with sockets blocked and cached verdicts; pytest
 * results (junit) are not part of the report yet, so this lists what the report itself asserts.
 */
export function TestResultsCard({ summary }: { summary: EvalSummary }) {
  const rows = testRows(summary);
  const net = criterion(summary, "network_calls");
  const passed = rows.filter((r) => r.state === "met").length;
  return (
    <div className="space-y-3 rounded-lg border p-4" data-slot="test-results-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-semibold tabular-nums">
            {passed} of {rows.length}
          </span>{" "}
          checks passed
        </p>
        <span className="rounded-md border border-sky-700/25 bg-sky-700/10 px-2 py-0.5 text-xs text-sky-700 dark:text-sky-400">
          offline · deterministic · cached verdicts · {net?.now ?? "?"} network calls
        </span>
      </div>
      {rows.length ? (
        <ul className="divide-y">
          {rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm" data-test={r.id}>
              <span className="min-w-0">
                <CodeText>{r.name}</CodeText>
              </span>
              <span className="flex items-center gap-2">
                <span className="text-muted-foreground tabular-nums" title={r.title}>
                  {r.result}
                </span>
                <MetBadge state={r.state} />
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">This report has no offline check results.</p>
      )}
      <p className="text-xs text-muted-foreground">
        Pytest results live in <code className="font-mono">make check</code>; the per-test list is not in the report
        yet.
      </p>
    </div>
  );
}
