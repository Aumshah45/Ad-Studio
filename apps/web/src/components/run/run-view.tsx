"use client";

import { useState } from "react";
import { toast } from "sonner";

import { ApprovalCard } from "@/components/run/approval-card";
import { CandidateCard, aspectCss } from "@/components/run/candidate-card";
import { PlanSpecCard } from "@/components/run/plan-spec-card";
import { FallbackCard, RepairCard } from "@/components/run/repair-fallback-cards";
import {
  DegradationBanner,
  RunErrorBanner,
  StreamDroppedBanner,
  type DegradationNotice,
} from "@/components/run/run-banners";
import { RunHeader } from "@/components/run/run-header";
import { RunTimeline } from "@/components/run/run-timeline";
import { countryByCode } from "@/components/studio/geography-combobox";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/hooks/use-now";
import { useRunStream } from "@/hooks/use-run-stream";
import type { DecisionView } from "@/lib/api";
import { checkLabel, isVlmCheck } from "@/lib/checks";
import { adBasename, cancelRun, rerunHref } from "@/lib/decision";
import {
  TERMINAL_STATUSES,
  imageModelFallback,
  planExtras,
  type CandidateState,
  type RunEvent,
  type RunState,
} from "@/lib/run-events";
import { friendlyReasons } from "@/lib/run-reasons";
import { candidateLabels, deriveTimeline, type TimelineStep } from "@/lib/run-timeline";
import { restoreQuotedTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";

/** Any sign the vision judge couldn't run: unverified verdicts or VLM checks with no result. */
export function judgeDegraded(state: RunState): boolean {
  if (/judge|vision/.test(state.reason ?? "")) return true;
  return state.candidates.some(
    (c) =>
      c.verdict === "unverified" ||
      c.dimensions.context?.passed === null ||
      c.dimensions.composition?.passed === null ||
      (c.checks ?? []).some((ch) => isVlmCheck(ch) && ch.passed === null),
  );
}

function topFailure(c: CandidateState, requiredText: string | null): string | null {
  const failing = (c.checks ?? []).find((ch) => ch.passed === false);
  if (failing) {
    const ev = failing.evidence ? restoreQuotedTokens(failing.evidence, requiredText) : null;
    return `${checkLabel(failing.check_name)}${ev ? `: ${ev}` : ""}`;
  }
  const dim = Object.entries(c.dimensions).find(([, d]) => d.passed === false);
  return dim ? (dim[1].reasons?.[0] ?? `${dim[0]} failed`) : null;
}

function CandidateSkeleton({ aspect }: { aspect: string | null | undefined }) {
  return (
    <div className="space-y-3 rounded-xl p-3 ring-1 ring-foreground/10">
      <Skeleton className="h-4 w-24 motion-reduce:animate-none" />
      <Skeleton
        className="mx-auto max-h-[280px] w-full motion-reduce:animate-none"
        style={{ aspectRatio: aspectCss(aspect) }}
      />
      <Skeleton className="h-9 w-full motion-reduce:animate-none" />
    </div>
  );
}

function EventList({ events }: { events: RunEvent[] }) {
  return (
    <details className="rounded-lg border text-sm">
      <summary className="cursor-pointer rounded-lg px-3 py-2 text-muted-foreground outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">
        Raw events ({events.length})
      </summary>
      <ol className="divide-y border-t px-3">
        {events.map((ev) => (
          <li key={ev.seq} className="py-1.5">
            <details>
              <summary className="flex cursor-pointer items-baseline gap-3 rounded-sm outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">
                <span className="w-6 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">{ev.seq}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{ev.type}</span>
                <time className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground" dateTime={ev.at}>
                  {new Date(ev.at).toLocaleTimeString()}
                </time>
              </summary>
              <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-muted p-2 font-mono text-xs">
                {JSON.stringify(ev, null, 2)}
              </pre>
            </details>
          </li>
        ))}
      </ol>
    </details>
  );
}

const DECIDABLE = new Set(["passed", "needs_review", "approved", "rejected"]);

export function RunView({ runId }: { runId: string }) {
  const stream = useRunStream(runId);
  const { events, status, error, reconnect } = stream;
  // A decision made here updates the view at once (the stream has already closed).
  const [decision, setDecision] = useState<DecisionView | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [mountedAt] = useState(() => Date.now());
  const state: RunState = decision
    ? {
        ...stream.state,
        status: decision.status,
        approvedCandidateId: decision.approved_candidate_id ?? stream.state.approvedCandidateId,
      }
    : stream.state;
  const terminal = TERMINAL_STATUSES.has(state.status);
  const now = useNow(!terminal);
  const country = state.brief ? countryByCode(state.brief.geography_code) : null;
  const aspect = state.brief?.aspect_ratio ?? "4:5";
  const steps = deriveTimeline(state);
  const labels = candidateLabels(state);
  const byId = new Map(state.candidates.map((c) => [c.id, c]));
  const extras = planExtras(state.spec);
  const reasons = friendlyReasons(state.reason);
  const budgetStop =
    /budget|deadline|wallclock/.test(state.reason ?? "") ||
    (terminal && ["exhausted", "deadline"].includes(state.budgetWarning?.reason ?? ""));
  const notices: DegradationNotice[] = [];
  if (judgeDegraded(state)) notices.push({ kind: "judge" });
  const swap = imageModelFallback(state);
  if (swap) notices.push({ kind: "image_fallback", ...swap });
  if (state.budgetWarning || budgetStop) {
    notices.push({
      kind: "budget",
      spentUsd: state.budgetWarning?.spentUsd ?? state.costUsd ?? 0,
      capUsd: state.budgetWarning?.capUsd ?? 0.25,
      stopped: budgetStop,
      deadline: /deadline|wallclock/.test(state.reason ?? "") || state.budgetWarning?.reason === "deadline",
    });
  }

  async function onCancel() {
    setCancelling(true);
    const res = await cancelRun(runId);
    if (!res.ok) {
      toast.error(res.message);
      setCancelling(false);
    } else if (!res.cancelled) {
      toast.info("The run had already finished.");
    }
  }
  const loading = state.brief === null && events.length === 0 && !error;

  const elapsedMs = terminal
    ? (state.latencyMs ??
      (state.startedAt && state.finished ? Date.parse(state.finished.at) - Date.parse(state.startedAt) : null))
    : state.startedAt
      ? Math.max(0, now - Date.parse(state.startedAt))
      : null;
  const costUsd = state.costUsd ?? state.budgetWarning?.spentUsd ?? null;

  const card = (id: string, className?: string) => {
    const c = byId.get(id);
    if (!c) return null;
    const parent = c.parentId ?? state.repairs.find((r) => r.resultCandidateId === id)?.fromCandidateId ?? null;
    return (
      <CandidateCard
        key={id}
        candidate={c}
        label={labels.get(id) ?? "?"}
        parentLabel={parent ? labels.get(parent) : null}
        aspect={aspect}
        approved={id === state.approvedCandidateId}
        requiredText={state.brief?.required_text ?? null}
        runEnded={terminal}
        className={className}
      />
    );
  };

  const renderStep = (step: TimelineStep) => {
    switch (step.kind) {
      case "plan":
        if (state.plan) return <PlanSpecCard plan={state.plan} extras={extras} aspect={aspect} />;
        return step.state === "running" ? (
          <div className="space-y-2 rounded-lg border p-4">
            <Skeleton className="h-5 w-56 motion-reduce:animate-none" />
            <Skeleton className="h-4 w-full max-w-md motion-reduce:animate-none" />
            <Skeleton className="h-4 w-40 motion-reduce:animate-none" />
          </div>
        ) : null;
      case "generate": {
        const n = Math.max(step.candidateIds.length, step.state === "running" ? 1 : 0);
        if (n === 0) return null;
        return (
          <div className={cn("grid gap-4", n > 1 && "sm:grid-cols-2")}>
            {step.candidateIds.map((id) => card(id))}
            {step.state === "running" && step.candidateIds.length === 0 ? <CandidateSkeleton aspect={aspect} /> : null}
          </div>
        );
      }
      case "evaluate": {
        const rejected = step.candidateIds
          .map((id) => byId.get(id))
          .filter((c): c is CandidateState => Boolean(c && c.verdict && c.verdict !== "pass"));
        if (rejected.length === 0) return null;
        return (
          <ul className="space-y-1 text-sm">
            {rejected.map((c) => (
              <li key={c.id} className="break-words">
                <span className="font-medium">{labels.get(c.id)}</span>{" "}
                <span className="text-muted-foreground">
                  {c.verdict === "unverified" ? "Not verified" : "Rejected"}
                  {topFailure(c, state.brief?.required_text ?? null)
                    ? `: ${topFailure(c, state.brief?.required_text ?? null)}`
                    : "."}
                </span>
              </li>
            ))}
          </ul>
        );
      }
      case "repair": {
        const r = step.index !== undefined ? state.repairs[step.index] : undefined;
        if (!r) return null;
        const result = r.resultCandidateId ? byId.get(r.resultCandidateId) : undefined;
        return (
          <div className="space-y-3">
            <RepairCard
              fromLabel={labels.get(r.fromCandidateId) ?? "?"}
              targetDimension={r.targetDimension}
              action={r.action}
              instruction={r.instruction}
              model={r.model ?? result?.model}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              {result ? card(result.id) : step.state === "running" ? <CandidateSkeleton aspect={aspect} /> : null}
            </div>
          </div>
        );
      }
      case "fallback": {
        const f = step.index !== undefined ? state.fallbacks[step.index] : undefined;
        if (!f) return null;
        const overlay = f.resultCandidateId ? byId.get(f.resultCandidateId) : undefined;
        const native = f.sourceCandidateId ? byId.get(f.sourceCandidateId) : undefined;
        return (
          <div className="space-y-3">
            <FallbackCard reason={f.reason} native={native} overlay={overlay} />
            <div className="grid gap-4 sm:grid-cols-2">
              {overlay ? card(overlay.id) : step.state === "running" ? <CandidateSkeleton aspect={aspect} /> : null}
            </div>
          </div>
        );
      }
      case "terminal": {
        if (DECIDABLE.has(state.status)) {
          const bestId = state.approvedCandidateId ?? state.bestCandidateId;
          const best = bestId ? byId.get(bestId) : undefined;
          return (
            <ApprovalCard
              runId={runId}
              status={state.status as "passed" | "needs_review" | "approved" | "rejected"}
              gateStatus={state.finished?.status ?? null}
              candidate={best}
              label={best ? labels.get(best.id) : undefined}
              reasons={reasons}
              requiredText={state.brief?.required_text ?? null}
              aspect={aspect}
              outcome={state.outcome}
              repairs={state.repairs.length}
              costUsd={state.costUsd}
              latencyMs={state.latencyMs}
              rerunHref={rerunHref(state.brief, state.productId ?? state.brief?.product_id ?? null)}
              fileBase={adBasename(state.brief, runId)}
              decision={decision}
              onDecided={setDecision}
              autoFocus={Boolean(state.finished && Date.parse(state.finished.at) >= mountedAt - 2000)}
            />
          );
        }
        return reasons.length ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ) : null;
      }
    }
  };

  return (
    <div className="mx-auto max-w-[880px] space-y-6">
      <RunHeader
        runId={runId}
        brief={state.brief}
        marketName={country?.name ?? null}
        status={state.status}
        streamStatus={status}
        terminal={terminal}
        elapsedMs={elapsedMs}
        costUsd={costUsd}
        onCancel={onCancel}
        cancelling={cancelling}
      />

      <div className="space-y-3 empty:hidden">
        {error ? <StreamDroppedBanner message={error} onReconnect={reconnect} /> : null}
        {state.error && state.status !== "needs_review" ? <RunErrorBanner problem={state.error} /> : null}
        {notices.map((n) => (
          <DegradationBanner key={n.kind} notice={n} />
        ))}
      </div>

      {loading ? (
        <div className="space-y-4" aria-busy="true" aria-label="Loading run">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex gap-3">
              <Skeleton className="size-5 rounded-full motion-reduce:animate-none" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-40 motion-reduce:animate-none" />
                <Skeleton className="h-16 w-full motion-reduce:animate-none" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <RunTimeline steps={steps} now={now} renderStep={renderStep} />
      )}

      {events.length > 0 ? <EventList events={events} /> : null}
    </div>
  );
}
