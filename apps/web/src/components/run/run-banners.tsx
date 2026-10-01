import { CircleX, Eye, RefreshCw, Shuffle, WifiOff, Wallet } from "lucide-react";
import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { formatUsd } from "@/lib/format";

/** SSE gave up after its retries; the run keeps going on the server. */
export function StreamDroppedBanner({ message, onReconnect }: { message: string; onReconnect: () => void }) {
  return (
    <Alert variant="destructive">
      <WifiOff aria-hidden />
      <AlertTitle>{message}</AlertTitle>
      <AlertDescription>
        <Button type="button" size="sm" variant="outline" className="mt-2" onClick={onReconnect}>
          <RefreshCw aria-hidden /> Reconnect
        </Button>
      </AlertDescription>
    </Alert>
  );
}

export type DegradationNotice =
  | { kind: "judge" }
  | { kind: "image_fallback"; requested: string | null; served: string }
  | { kind: "budget"; spentUsd: number; capUsd: number; stopped: boolean; deadline?: boolean };

/**
 * Graceful-degradation banner (docs/ux.md): what degraded, and what it means for the gate.
 * Amber, with an icon and a sentence; never red, because nothing is wrong with the ad itself.
 */
export function DegradationBanner({ notice }: { notice: DegradationNotice }) {
  let icon = <Eye className="text-amber-700 dark:text-amber-400" aria-hidden />;
  let title: string;
  let body: string;
  switch (notice.kind) {
    case "judge":
      title = "Vision judge unavailable";
      body =
        "Context can’t be checked, so no ad will be approved until it’s back. The deterministic checks still ran; the best candidate is held for review.";
      break;
    case "image_fallback":
      icon = <Shuffle className="text-amber-700 dark:text-amber-400" aria-hidden />;
      title = `${notice.requested ?? "The requested image model"} unavailable`;
      body = `Images came from ${notice.served} instead. The quality gate is unchanged.`;
      break;
    case "budget":
      icon = <Wallet className="text-amber-700 dark:text-amber-400" aria-hidden />;
      title = notice.stopped
        ? notice.deadline
          ? "Stopped at the time limit for this ad"
          : `Stopped at the ${formatUsd(notice.capUsd)} budget for this ad`
        : `Budget: ${formatUsd(notice.spentUsd)} of ${formatUsd(notice.capUsd)} spent`;
      body = notice.stopped
        ? "Best candidate held for review."
        : "The run stops at the cap and holds the best candidate for review.";
      break;
  }
  return (
    <Alert className="border-amber-700/30 bg-amber-700/5" role="status" data-degradation={notice.kind}>
      {icon}
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{body}</AlertDescription>
    </Alert>
  );
}

export function RunErrorBanner({ problem }: { problem: Record<string, unknown> }) {
  const title = typeof problem.title === "string" ? problem.title : "The run failed";
  const detail = typeof problem.detail === "string" ? problem.detail : null;
  return (
    <Alert variant="destructive">
      <CircleX aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        {detail ? <p>{detail}</p> : null}
        <Link href="/" className={buttonVariants({ size: "sm", variant: "outline", className: "mt-2" })}>
          Run the brief again
        </Link>
      </AlertDescription>
    </Alert>
  );
}
