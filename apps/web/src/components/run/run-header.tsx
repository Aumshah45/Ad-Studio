import { ArrowLeft, Clock, Coins, Square } from "lucide-react";
import Link from "next/link";

import { VerdictBadge } from "@/components/run/verdict-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import type { StreamStatus } from "@/hooks/use-run-stream";
import type { BriefView } from "@/lib/api";
import { formatDuration, formatUsd } from "@/lib/format";

const STREAM_LABEL: Record<StreamStatus, string> = {
  connecting: "Connecting…",
  open: "Live",
  reconnecting: "Reconnecting…",
  closed: "Finished",
  error: "Disconnected",
};

/** Brief line, run status, and the running time and cost (live while the run streams). */
export function RunHeader({
  runId,
  brief,
  marketName,
  status,
  streamStatus,
  terminal,
  elapsedMs,
  costUsd,
  onCancel,
  cancelling = false,
}: {
  runId: string;
  brief: BriefView | null;
  marketName: string | null;
  status: string;
  streamStatus: StreamStatus;
  terminal: boolean;
  elapsedMs: number | null;
  costUsd: number | null;
  /** Shown as **Cancel run** while the run is active. */
  onCancel?: () => void;
  cancelling?: boolean;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
      <div className="min-w-0 space-y-1">
        <Link
          href="/"
          className="inline-flex items-center gap-1 rounded-sm text-sm text-muted-foreground outline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          <ArrowLeft className="size-3.5" aria-hidden /> New brief
        </Link>
        {brief ? (
          <h1 className="text-xl font-semibold tracking-tight break-words sm:text-2xl">
            {marketName ?? brief.geography_code} · {brief.season} ·{" "}
            <span className="whitespace-pre-line">&lsquo;{brief.required_text}&rsquo;</span>
          </h1>
        ) : (
          <>
            <h1 className="sr-only">Run {runId}</h1>
            <Skeleton className="h-8 w-72 max-w-full" />
          </>
        )}
        <p className="font-mono text-xs break-all text-muted-foreground">
          run {runId}
          {brief ? ` · ${brief.aspect_ratio}` : ""}
        </p>
      </div>
      <div className="flex flex-col items-start gap-1.5 sm:items-end">
        <VerdictBadge status={status} className="h-7 text-sm" />
        <dl className="flex items-center gap-3 text-sm tabular-nums">
          <div className="inline-flex items-center gap-1" title={terminal ? "Total time" : "Elapsed"}>
            <Clock className="size-3.5 text-muted-foreground" aria-hidden />
            <dt className="sr-only">{terminal ? "Total time" : "Elapsed"}</dt>
            <dd>{formatDuration(elapsedMs)}</dd>
          </div>
          <div className="inline-flex items-center gap-1" title={terminal ? "Cost" : "Cost so far"}>
            <Coins className="size-3.5 text-muted-foreground" aria-hidden />
            <dt className="sr-only">{terminal ? "Cost" : "Cost so far"}</dt>
            <dd>{formatUsd(costUsd)}</dd>
          </div>
        </dl>
        <p className="text-xs text-muted-foreground">{terminal ? "Run ended" : STREAM_LABEL[streamStatus]}</p>
        {!terminal && onCancel ? (
          <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={cancelling}>
            {cancelling ? <Spinner aria-hidden /> : <Square aria-hidden />}
            {cancelling ? "Cancelling…" : "Cancel run"}
          </Button>
        ) : null}
      </div>
    </header>
  );
}
