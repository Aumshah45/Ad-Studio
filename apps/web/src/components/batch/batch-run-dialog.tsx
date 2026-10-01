"use client";

import { Check, Copy, Play } from "lucide-react";
import { useState } from "react";

import { CommandLine } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { batchEstimate } from "@/lib/batch";
import { formatDuration, formatUsd } from "@/lib/format";

export const BATCH_COMMANDS = [
  "make golden-run        # 20 briefs through the pipeline (idempotent, cached)",
  "make golden-export plant",
  "make eval              # offline: cached verdicts, sockets blocked, $0",
  "make golden-import     # load the report and images for this dashboard",
].join("\n");

/**
 * Batch runs are CLI-driven (there is no batch endpoint), so this explains what a run costs and
 * how to start it rather than triggering one.
 */
export function BatchRunDialog({
  briefCount = 20,
  costPerAd,
  p50Ms,
  estimateFromDryRun = false,
}: {
  briefCount?: number;
  costPerAd: number | null | undefined;
  p50Ms: number | null | undefined;
  estimateFromDryRun?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const est = batchEstimate(briefCount, costPerAd, p50Ms);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(BATCH_COMMANDS.split("\n").map((l) => l.split("#")[0]!.trim()).join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked; the commands stay visible to copy by hand.
    }
  };
  return (
    <Dialog>
      <DialogTrigger render={<Button />}>
        <Play aria-hidden /> Run batch ({briefCount} briefs)
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg" data-slot="batch-run-dialog">
        <DialogHeader>
          <DialogTitle>Run {briefCount} briefs?</DialogTitle>
          <DialogDescription>
            {est.usdIsCap ? "At most " : "Estimated "}
            <span className="font-medium text-foreground tabular-nums">{formatUsd(est.usd)}</span>
            {est.seconds != null ? (
              <>
                {" "}
                and about <span className="font-medium text-foreground tabular-nums">{formatDuration(est.seconds * 1000)}</span>
              </>
            ) : null}
            . Cached steps are free.
          </DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-3 text-sm">
          <p className="text-xs text-muted-foreground">
            {est.usdIsCap
              ? `Cost bound: ${briefCount} × the $0.25 per-ad budget cap (no cost in the last report).`
              : `From the last report: ${formatUsd(costPerAd)} per approved ad × ${briefCount}.`}{" "}
            {est.seconds != null ? "Time: p50 per ad, 3 briefs at a time." : ""}
            {estimateFromDryRun ? " The last report is a fake dry run, so real runs will cost and take more." : ""}
          </p>
          <p>
            Batch runs are started from the command line, not from this page. Run these from the repo root; the
            gallery and the Evaluator update after the import.
          </p>
          <CommandLine command={BATCH_COMMANDS} />
        </div>
        <DialogFooter showCloseButton>
          <Button variant="outline" onClick={copy}>
            {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
            {copied ? "Copied" : "Copy commands"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
