import { CircleCheck, CircleDashed, CircleX, MinusCircle } from "lucide-react";
import type { ReactNode } from "react";

import { Spinner } from "@/components/ui/spinner";
import { formatDuration } from "@/lib/format";
import type { StepState, TimelineStep } from "@/lib/run-timeline";
import { cn } from "@/lib/utils";

const STATE_WORD: Record<StepState, string> = {
  pending: "Pending",
  running: "Running",
  done: "Done",
  skipped: "Skipped",
  failed: "Stopped",
};

function StepIcon({ state }: { state: StepState }) {
  switch (state) {
    case "running":
      return <Spinner className="size-4 text-primary" aria-hidden />;
    case "done":
      return <CircleCheck className="size-4 text-emerald-700 dark:text-emerald-400" aria-hidden />;
    case "failed":
      return <CircleX className="size-4 text-red-700 dark:text-red-400" aria-hidden />;
    case "skipped":
      return <MinusCircle className="size-4 text-muted-foreground" aria-hidden />;
    default:
      return <CircleDashed className="size-4 text-muted-foreground/70" aria-hidden />;
  }
}

function stepTime(step: TimelineStep, now: number): string | null {
  if (step.state === "running" && step.startedAt) {
    return formatDuration(Math.max(0, now - Date.parse(step.startedAt)));
  }
  if (step.durationMs != null && (step.state === "done" || step.state === "failed")) {
    return step.kind === "terminal" ? `${formatDuration(step.durationMs)} total` : formatDuration(step.durationMs);
  }
  return null;
}

/**
 * Vertical run timeline. Step states: pending (hollow), running (spinner + elapsed),
 * done (check + duration), skipped, stopped. Announces step transitions politely.
 */
export function RunTimeline({
  steps,
  now,
  renderStep,
}: {
  steps: TimelineStep[];
  now: number;
  renderStep?: (step: TimelineStep) => ReactNode;
}) {
  const current = steps.findLast((s) => s.state === "running") ?? steps.findLast((s) => s.state === "done");
  return (
    <section aria-label="Run timeline">
      <p className="sr-only" aria-live="polite">
        {current ? `${current.title}: ${STATE_WORD[current.state].toLowerCase()}` : ""}
      </p>
      <ol className="relative">
        {steps.map((step, i) => {
          const time = stepTime(step, now);
          const body = step.state === "pending" ? null : renderStep?.(step);
          const last = i === steps.length - 1;
          return (
            <li key={step.id} data-step={step.kind} data-state={step.state} className="relative flex gap-3 pb-5 last:pb-0">
              {!last ? (
                <span className="absolute top-6 bottom-0 left-[9px] w-px bg-border" aria-hidden />
              ) : null}
              <span className="relative z-10 mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-background">
                <StepIcon state={step.state} />
              </span>
              <div className="min-w-0 flex-1 space-y-3">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                  <h3 className={cn("font-medium", step.state === "pending" && "text-muted-foreground")}>
                    {step.title}
                  </h3>
                  <span className="sr-only">{STATE_WORD[step.state]}</span>
                  {step.summary ? (
                    <span className="min-w-0 text-sm break-words text-muted-foreground">{step.summary}</span>
                  ) : null}
                  {time ? (
                    <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">{time}</span>
                  ) : null}
                </div>
                {body}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
