"use client";

import { CircleCheck, CircleX, Download, PauseCircle, RotateCcw, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";

import { CandidateEvidence } from "@/components/run/candidate-evidence";
import { Button, buttonVariants } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import type { DecisionView } from "@/lib/api";
import { DIMENSION_LABEL, NOT_CHECKED } from "@/lib/checks";
import { decideRun, downloadAd } from "@/lib/decision";
import { formatDuration, formatUsd } from "@/lib/format";
import { apiUrl, type CandidateState } from "@/lib/run-events";
import { cn } from "@/lib/utils";

/** The API's minimum for an override reason (`decisions.MIN_REASON_CHARS`). */
export const MIN_REASON_CHARS = 3;

export type ApprovalStatus = "passed" | "needs_review" | "approved" | "rejected";

type Panel = "none" | "reject" | "override";
type Busy = "none" | "approve" | "reject" | "override" | "download";

export interface ApprovalCardProps {
  runId: string;
  status: ApprovalStatus;
  /** The gate's own result (`run.finished.status`), to tell an override from a normal approval. */
  gateStatus?: string | null;
  candidate?: CandidateState;
  label?: string;
  /** Sentences for why a held run wasn't approved. */
  reasons?: string[];
  requiredText: string | null;
  aspect?: string | null;
  outcome: string | null;
  repairs: number;
  costUsd: number | null;
  latencyMs: number | null;
  /** Prefilled Studio link (same product and text). */
  rerunHref: string;
  /** Download file name without extension. */
  fileBase: string;
  /** The decision made in this session (reason and override flag aren't in the snapshot). */
  decision?: DecisionView | null;
  onDecided?: (decision: DecisionView) => void;
  /** Move focus to the heading on mount (the run just finished while watched). */
  autoFocus?: boolean;
}

export function dimensionSummary(c: CandidateState | undefined): string {
  if (!c) return "";
  const parts = ["text", "product", "context"]
    .filter((d) => d in c.dimensions)
    .map((d) => {
      const p = c.dimensions[d]?.passed;
      return `${DIMENSION_LABEL[d]} ${p === true ? "✓" : p === false ? "✗" : "?"}`;
    });
  if (parts.length) {
    const comp = c.dimensions.composition;
    // Evaluations before ADR-007 have no composition key: "Not checked", never a failure.
    parts.push(
      comp
        ? `${DIMENSION_LABEL.composition} ${comp.passed === true ? "✓" : comp.passed === false ? "✗" : "?"}`
        : `${DIMENSION_LABEL.composition} ${NOT_CHECKED.toLowerCase()}`,
    );
  }
  return parts.join(" ");
}

function heldBecause(c: CandidateState | undefined): string | null {
  if (!c) return null;
  const failed: string[] = [];
  const unsure: string[] = [];
  for (const [d, v] of Object.entries(c.dimensions)) {
    if (v.passed === false) failed.push(DIMENSION_LABEL[d] ?? d);
    else if (v.passed === null) unsure.push(DIMENSION_LABEL[d] ?? d);
  }
  const parts = [
    failed.length ? `${failed.join(" and ")} failed` : null,
    unsure.length ? `${unsure.join(" and ")} ${unsure.length > 1 ? "are" : "is"} unsure` : null,
  ].filter(Boolean);
  return parts.length ? `Not approved because ${parts.join(", and ")}.` : null;
}

/**
 * The human decision on a finished run (docs/ux.md ApprovalCard). The gate proposes; a person
 * approves the export. `passed` → Approve and download / Reject / Rerun for another market.
 * `needs_review` ("Held") → Approve anyway with a required reason (an override, stored as a human
 * label) / Rerun with edits. `approved` / `rejected` show the recorded decision.
 */
export function ApprovalCard(props: ApprovalCardProps) {
  const {
    runId,
    status,
    gateStatus,
    candidate,
    label,
    reasons = [],
    requiredText,
    aspect,
    outcome,
    repairs,
    costUsd,
    latencyMs,
    rerunHref,
    fileBase,
    decision,
    onDecided,
    autoFocus = false,
  } = props;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const reasonId = useId();
  const reasonHelpId = useId();
  const reasonErrId = useId();
  const [panel, setPanel] = useState<Panel>("none");
  const [busy, setBusy] = useState<Busy>("none");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reasonError, setReasonError] = useState<string | null>(null);

  useEffect(() => {
    if (autoFocus) headingRef.current?.focus();
  }, [autoFocus]);

  const imageUrl = candidate?.imageUrl ? apiUrl(candidate.imageUrl) : null;
  const size = candidate?.width && candidate?.height ? `${candidate.width}×${candidate.height} ` : "";
  const textSource =
    outcome === "overlay"
      ? "Text: drawn by Ad Studio"
      : outcome === "native" || status === "passed"
        ? "Text: native"
        : null;
  const reasonOk = reason.trim().length >= MIN_REASON_CHARS;

  async function download() {
    if (!imageUrl) return;
    setBusy("download");
    try {
      const file = await downloadAd(imageUrl, fileBase);
      toast.success(`Downloaded ad (${size}${file.format}).`);
    } catch {
      toast.error("The approval is saved, but the download failed. Use Download ad to try again.");
    } finally {
      setBusy("none");
    }
  }

  async function decide(action: "approve" | "reject", kind: Busy, why?: string) {
    setError(null);
    setReasonError(null);
    setBusy(kind);
    const res = await decideRun(runId, action, why);
    if (!res.ok) {
      setBusy("none");
      if (res.code === "override-reason-required") setReasonError(res.message);
      else setError(res.message);
      return;
    }
    setPanel("none");
    onDecided?.(res.decision);
    if (action === "approve") {
      await download();
    } else {
      setBusy("none");
      toast.success("Rejected. Your reason is saved with the run.");
    }
  }

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    // `A` approves only while the approval card has focus, never as a global key (docs/ux.md).
    const target = e.target as HTMLElement;
    if (
      status === "passed" &&
      busy === "none" &&
      e.key.toLowerCase() === "a" &&
      !e.metaKey &&
      !e.ctrlKey &&
      !e.altKey &&
      !["TEXTAREA", "INPUT"].includes(target.tagName)
    ) {
      e.preventDefault();
      void decide("approve", "approve");
    }
  };

  const decided = status === "approved" || status === "rejected";
  const override = decision?.is_override ?? (status === "approved" && gateStatus === "needs_review");
  const tone =
    status === "passed" || status === "approved"
      ? "border-emerald-700/30 bg-emerald-700/5"
      : status === "rejected"
        ? "border-red-700/30 bg-red-700/5"
        : "border-dashed border-zinc-500/60";
  const Icon = status === "needs_review" ? PauseCircle : status === "rejected" ? CircleX : CircleCheck;
  const iconClass =
    status === "needs_review"
      ? "text-zinc-600 dark:text-zinc-300"
      : status === "rejected"
        ? "text-red-700 dark:text-red-400"
        : "text-emerald-700 dark:text-emerald-400";
  const title =
    status === "passed"
      ? "Passed all quality gates"
      : status === "needs_review"
        ? "Held for review"
        : status === "approved"
          ? override
            ? "Approved with override"
            : "Approved"
          : "Rejected";

  const metaLine = [
    dimensionSummary(candidate),
    textSource,
    `${repairs} repair${repairs === 1 ? "" : "s"}`,
    formatUsd(costUsd),
    formatDuration(latencyMs),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section
      aria-labelledby={`${reasonId}-title`}
      data-slot="approval-card"
      data-status={status}
      onKeyDown={onKeyDown}
      className={cn("space-y-4 rounded-xl border p-4", tone)}
    >
      <div className="space-y-1">
        <h2
          id={`${reasonId}-title`}
          ref={headingRef}
          tabIndex={-1}
          className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-base font-semibold outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
        >
          <Icon className={cn("size-5", iconClass)} aria-hidden />
          {title}
          {label ? <span className="text-sm font-normal whitespace-nowrap text-muted-foreground">· candidate {label}</span> : null}
        </h2>
        <p className="text-sm text-muted-foreground tabular-nums">{metaLine}</p>
        {status === "needs_review" ? (
          <>
            {heldBecause(candidate) ? <p className="text-sm">{heldBecause(candidate)}</p> : null}
            {reasons.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : null}
          </>
        ) : null}
        {status === "passed" ? (
          <p className="text-xs text-muted-foreground">
            The quality gate proposes this ad. A person approves it before it can be exported.
          </p>
        ) : null}
        {decided ? (
          <p className="text-sm">
            {status === "approved"
              ? override
                ? "Approved by a person although the gate held it. The override is stored as a human label."
                : "Approved by a person after it passed the quality gate."
              : "Rejected by a person. It won't be exported."}
            {decision?.reason ? (
              <>
                {" "}
                Reason: <q className="italic">{decision.reason}</q>
              </>
            ) : null}
          </p>
        ) : null}
      </div>

      {candidate ? (
        <CandidateEvidence
          candidate={candidate}
          label={label ?? "?"}
          requiredText={requiredText}
          aspect={aspect}
          maxHeight={512}
          layout="split"
        />
      ) : status === "needs_review" ? (
        <p className="text-sm text-muted-foreground">No candidate image to review. Rerun the brief with edits.</p>
      ) : null}

      {error ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      ) : null}

      {panel === "override" ? (
        <form
          className="space-y-2 rounded-lg border bg-background p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!reasonOk) {
              setReasonError(`Say why in at least ${MIN_REASON_CHARS} characters.`);
              return;
            }
            void decide("approve", "override", reason);
          }}
        >
          <label htmlFor={reasonId} className="flex items-center gap-1.5 text-sm font-medium">
            <ShieldAlert className="size-4 text-amber-700 dark:text-amber-400" aria-hidden />
            Reason for approving (required)
          </label>
          <p id={reasonHelpId} className="text-xs text-muted-foreground">
            You&rsquo;re approving an ad the gate didn&rsquo;t pass. Your reason is saved with the ad.
          </p>
          <Textarea
            id={reasonId}
            value={reason}
            required
            minLength={MIN_REASON_CHARS}
            maxLength={500}
            onChange={(e) => {
              setReason(e.target.value);
              setReasonError(null);
            }}
            aria-invalid={reasonError ? true : undefined}
            aria-describedby={[reasonHelpId, reasonError ? reasonErrId : null].filter(Boolean).join(" ")}
            placeholder="e.g. Beach scene is correct for Sydney in December; the judge misread the sky."
            autoFocus
          />
          {reasonError ? (
            <p id={reasonErrId} role="alert" className="text-xs text-red-700 dark:text-red-400">
              {reasonError}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!reasonOk || busy !== "none"}>
              {busy === "override" || busy === "download" ? <Spinner aria-hidden /> : <Download aria-hidden />}
              Approve with override
            </Button>
            <Button type="button" variant="ghost" onClick={() => setPanel("none")} disabled={busy !== "none"}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}

      {panel === "reject" ? (
        <form
          className="space-y-2 rounded-lg border bg-background p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void decide("reject", "reject", reason);
          }}
        >
          <label htmlFor={`${reasonId}-reject`} className="text-sm font-medium">
            Why are you rejecting this ad? (optional)
          </label>
          <Textarea
            id={`${reasonId}-reject`}
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Product colour looks off next to our brand red."
            autoFocus
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="destructive" disabled={busy !== "none"}>
              {busy === "reject" ? <Spinner aria-hidden /> : <CircleX aria-hidden />}
              Reject and save
            </Button>
            <Button type="button" variant="ghost" onClick={() => setPanel("none")} disabled={busy !== "none"}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}

      {panel === "none" ? (
        <div className="flex flex-wrap gap-2">
          {status === "passed" ? (
            <>
              <Button
                type="button"
                onClick={() => void decide("approve", "approve")}
                disabled={busy !== "none" || !candidate}
                aria-keyshortcuts="A"
              >
                {busy === "approve" || busy === "download" ? <Spinner aria-hidden /> : <Download aria-hidden />}
                Approve and download
              </Button>
              <Button type="button" variant="outline" onClick={() => setPanel("reject")} disabled={busy !== "none"}>
                <CircleX aria-hidden />
                Reject
              </Button>
              <Link href={rerunHref} className={buttonVariants({ variant: "outline" })}>
                <RotateCcw aria-hidden />
                Rerun for another market
              </Link>
            </>
          ) : null}
          {status === "needs_review" ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => setPanel("override")}
                disabled={busy !== "none" || !candidate?.imageUrl}
              >
                <ShieldAlert aria-hidden />
                Approve anyway with reason
              </Button>
              <Link href={rerunHref} className={buttonVariants({ variant: "outline" })}>
                <RotateCcw aria-hidden />
                Rerun with edits
              </Link>
              <Button type="button" variant="ghost" onClick={() => setPanel("reject")} disabled={busy !== "none"}>
                Reject
              </Button>
            </>
          ) : null}
          {status === "approved" ? (
            <>
              <Button type="button" onClick={() => void download()} disabled={busy !== "none" || !imageUrl}>
                {busy === "download" ? <Spinner aria-hidden /> : <Download aria-hidden />}
                Download ad
              </Button>
              <Link href={rerunHref} className={buttonVariants({ variant: "outline" })}>
                <RotateCcw aria-hidden />
                Rerun for another market
              </Link>
            </>
          ) : null}
          {status === "rejected" ? (
            <Link href={rerunHref} className={buttonVariants({ variant: "outline" })}>
              <RotateCcw aria-hidden />
              Rerun with edits
            </Link>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
