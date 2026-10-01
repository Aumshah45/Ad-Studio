import { DIMENSION_LABEL } from "@/lib/checks";
import {
  candidateRole,
  TERMINAL_STATUSES,
  type CandidateState,
  type RunState,
} from "@/lib/run-events";

export type StepKind = "plan" | "generate" | "evaluate" | "repair" | "fallback" | "terminal";
/** pending = hollow, running = spinner + elapsed, done = check + duration (docs/ux.md). */
export type StepState = "pending" | "running" | "done" | "skipped" | "failed";

export interface TimelineStep {
  id: string;
  kind: StepKind;
  state: StepState;
  title: string;
  summary: string | null;
  startedAt: string | null;
  endedAt: string | null;
  /** Set when the step has ended; running steps compute elapsed time from `startedAt`. */
  durationMs: number | null;
  candidateIds: string[];
  /** Index into `RunState.repairs` / `RunState.fallbacks` for those steps. */
  index?: number;
}

const FAILED_RUN = new Set(["failed", "interrupted", "cancelled"]);

function ms(from: string | null, to: string | null): number | null {
  if (!from || !to) return null;
  const d = Date.parse(to) - Date.parse(from);
  return Number.isFinite(d) ? Math.max(0, d) : null;
}

function latest(values: (string | null)[]): string | null {
  let best: string | null = null;
  for (const v of values) if (v && (!best || Date.parse(v) > Date.parse(best))) best = v;
  return best;
}

function earliestOf(values: (string | null)[]): string | null {
  let best: string | null = null;
  for (const v of values) if (v && (!best || Date.parse(v) < Date.parse(best))) best = v;
  return best;
}

function byCreation(a: CandidateState, b: CandidateState): number {
  return a.attempt - b.attempt || a.slot - b.slot;
}

/** Letters for first-round candidates (A, B…); repairs and overlays add a prime to their parent's. */
export function candidateLabels(state: RunState): Map<string, string> {
  const labels = new Map<string, string>();
  const byId = new Map(state.candidates.map((c) => [c.id, c]));
  const parentOf = (c: CandidateState): string | null =>
    c.parentId ??
    state.repairs.find((r) => r.resultCandidateId === c.id)?.fromCandidateId ??
    state.fallbacks.find((f) => f.resultCandidateId === c.id)?.sourceCandidateId ??
    null;
  let next = 0;
  const letter = () => String.fromCharCode(65 + next++);
  for (const c of state.candidates.filter((x) => candidateRole(x.kind) === "candidate").sort(byCreation)) {
    labels.set(c.id, letter());
  }
  const resolve = (c: CandidateState, depth: number): string => {
    const known = labels.get(c.id);
    if (known) return known;
    const parent = parentOf(c);
    const p = parent ? byId.get(parent) : undefined;
    let label = p && depth < 10 ? `${resolve(p, depth + 1)}′` : letter();
    // Two children of one parent (a repair and an overlay of the same original) stay distinct.
    const used = new Set(labels.values());
    while (used.has(label)) label += "′";
    labels.set(c.id, label);
    return label;
  };
  for (const c of state.candidates) resolve(c, 0);
  return labels;
}

function statusAt(state: RunState, status: string): string | null {
  return state.statusHistory.find((h) => h.status === status)?.at ?? null;
}

function verdictWord(c: CandidateState): string {
  if (c.status === "blocked") return "blocked";
  if (c.verdict === "pass") return "passed";
  if (c.verdict === "unverified") return "unsure";
  if (c.verdict === "fail") {
    const failing = Object.entries(c.dimensions)
      .filter(([, d]) => d.passed === false)
      .map(([dim]) => DIMENSION_LABEL[dim] ?? dim);
    return failing.length ? `failed ${failing.join(", ")}` : "failed";
  }
  return "checking";
}

/**
 * Derive the run timeline (plan → candidates → evaluate → repair → fallback → terminal) from the
 * folded run state. Pure, so the same function renders live streams, reloads and replays.
 */
export function deriveTimeline(state: RunState): TimelineStep[] {
  const terminal = TERMINAL_STATUSES.has(state.status);
  const runFailed = FAILED_RUN.has(state.status);
  const labels = candidateLabels(state);
  const steps: TimelineStep[] = [];

  // Plan
  const planStart = statusAt(state, "planning") ?? state.startedAt;
  const planDone = state.plan !== null;
  steps.push({
    id: "plan",
    kind: "plan",
    state: planDone ? "done" : terminal ? (runFailed ? "failed" : "skipped") : state.status === "queued" ? "pending" : "running",
    title: "Plan",
    summary: planDone
      ? state.plan?.effective_season
        ? `Effective season: ${state.plan.effective_season}`
        : "Plan ready"
      : terminal
        ? "No plan was recorded"
        : "Resolving season and market…",
    startedAt: planStart,
    endedAt: state.planAt,
    durationMs: planDone ? ms(planStart, state.planAt) : null,
    candidateIds: [],
  });

  // Generate (first-round candidates)
  const initials = state.candidates.filter((c) => candidateRole(c.kind) === "candidate").sort(byCreation);
  const laterPhase = ["evaluating", "repairing", "fallback"].includes(state.status);
  const genStart = statusAt(state, "generating") ?? state.planAt;
  const genDone = initials.length > 0 && (terminal || laterPhase || initials.some((c) => c.verdict !== null));
  const genEnd = genDone ? latest(initials.map((c) => c.createdAt)) : null;
  const models = [...new Set(initials.map((c) => c.model).filter(Boolean))];
  steps.push({
    id: "generate",
    kind: "generate",
    state: genDone
      ? "done"
      : terminal
        ? runFailed
          ? "failed"
          : "skipped"
        : planDone || state.status === "generating"
          ? "running"
          : "pending",
    title: initials.length > 1 ? `Generate ×${initials.length}` : "Generate",
    summary: initials.length
      ? `${initials.length} candidate${initials.length === 1 ? "" : "s"}${models.length ? ` with ${models.join(", ")}` : ""}`
      : planDone && !terminal
        ? "Generating candidates…"
        : null,
    startedAt: genStart,
    endedAt: genEnd,
    durationMs: genDone ? ms(genStart, genEnd) : null,
    candidateIds: initials.map((c) => c.id),
  });

  // Evaluate (first-round candidates; repairs are evaluated inside their own step)
  const targets = initials.filter((c) => c.status !== "blocked" && c.imageUrl);
  const evaluated = targets.filter((c) => c.verdict !== null);
  const evalStart = statusAt(state, "evaluating") ?? earliestOf(targets.map((c) => c.createdAt));
  const evalDone = targets.length > 0 && evaluated.length === targets.length;
  const evalEnd = evalDone ? latest(evaluated.map((c) => c.evaluatedAt)) : null;
  steps.push({
    id: "evaluate",
    kind: "evaluate",
    state: evalDone
      ? "done"
      : terminal
        ? targets.length === 0
          ? "skipped"
          : "failed"
        : targets.length > 0 || state.status === "evaluating"
          ? "running"
          : "pending",
    title: "Evaluate",
    summary: initials.length
      ? initials.map((c) => `${labels.get(c.id)} ${verdictWord(c)}`).join(" · ")
      : terminal
        ? "Nothing to evaluate"
        : null,
    startedAt: evalStart,
    endedAt: evalEnd,
    durationMs: evalDone ? ms(evalStart, evalEnd) : null,
    candidateIds: targets.map((c) => c.id),
  });

  // Repair (one step per targeted repair)
  if (state.repairs.length === 0) {
    const firstPass = initials.some((c) => c.verdict === "pass");
    steps.push({
      id: "repair",
      kind: "repair",
      state: terminal ? "skipped" : "pending",
      title: "Repair",
      summary: terminal ? (firstPass ? "Not needed (a first-round candidate passed)" : "Not attempted") : null,
      startedAt: null,
      endedAt: null,
      durationMs: null,
      candidateIds: [],
    });
  } else {
    state.repairs.forEach((r, i) => {
      const result = r.resultCandidateId
        ? state.candidates.find((c) => c.id === r.resultCandidateId)
        : undefined;
      const done = Boolean(result && (result.verdict !== null || result.status === "blocked"));
      const start = r.at ?? result?.createdAt ?? null;
      const end = done ? (result?.evaluatedAt ?? result?.createdAt ?? null) : null;
      const from = labels.get(r.fromCandidateId) ?? "candidate";
      const dim = r.targetDimension ?? (r.action === "repair_composition" ? "composition" : null);
      const target = dim ? (DIMENSION_LABEL[dim] ?? dim) : null;
      steps.push({
        id: `repair-${r.seq ?? r.resultCandidateId ?? i}`,
        kind: "repair",
        state: done ? "done" : terminal ? "failed" : "running",
        title: `Repair ${from}${target ? ` · ${target}` : ""}`,
        summary: result
          ? `${labels.get(result.id)} ${verdictWord(result)}`
          : terminal
            ? "Stopped before the repair finished"
            : "Repairing…",
        startedAt: start,
        endedAt: end,
        durationMs: done ? ms(start, end) : null,
        candidateIds: result ? [result.id] : [],
        index: i,
      });
    });
  }

  // Text fallback (Ad Studio draws the exact text into the reserved zone)
  if (state.fallbacks.length === 0) {
    steps.push({
      id: "fallback",
      kind: "fallback",
      state: terminal ? "skipped" : "pending",
      title: "Text fallback",
      summary: terminal ? (state.outcome === "native" ? "Not needed (native text passed)" : "Not used") : null,
      startedAt: null,
      endedAt: null,
      durationMs: null,
      candidateIds: [],
    });
  } else {
    state.fallbacks.forEach((f, i) => {
      const result = f.resultCandidateId
        ? state.candidates.find((c) => c.id === f.resultCandidateId)
        : undefined;
      const done = Boolean(result && result.verdict !== null);
      const start = f.at ?? statusAt(state, "fallback") ?? result?.createdAt ?? null;
      const end = done ? (result?.evaluatedAt ?? null) : null;
      steps.push({
        id: `fallback-${f.seq ?? f.resultCandidateId ?? i}`,
        kind: "fallback",
        state: done ? "done" : terminal ? "failed" : "running",
        title: "Text fallback",
        summary: result ? `Text drawn by Ad Studio · ${labels.get(result.id)} ${verdictWord(result)}` : "Drawing the exact text…",
        startedAt: start,
        endedAt: end,
        durationMs: done ? ms(start, end) : null,
        candidateIds: result ? [result.id] : [],
        index: i,
      });
    });
  }

  // Terminal
  const finishedAt = state.finished?.at ?? null;
  steps.push({
    id: "terminal",
    kind: "terminal",
    state: terminal ? (runFailed ? "failed" : "done") : "pending",
    title: terminal ? terminalTitle(state.status) : "Result",
    summary: null,
    startedAt: finishedAt,
    endedAt: finishedAt,
    durationMs: terminal ? state.latencyMs : null,
    candidateIds: state.approvedCandidateId ? [state.approvedCandidateId] : [],
  });

  return steps;
}

export function terminalTitle(status: string): string {
  switch (status) {
    case "passed":
      return "Passed all quality gates";
    case "needs_review":
      return "Held for review";
    case "approved":
      return "Approved";
    case "rejected":
      return "Rejected";
    case "failed":
      return "Run failed";
    case "interrupted":
      return "Interrupted";
    case "cancelled":
      return "Cancelled";
    default:
      return status;
  }
}
