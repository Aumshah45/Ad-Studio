import { CircleCheck, CircleHelp, CircleX, Pin, type LucideIcon } from "lucide-react";

import type { EvidenceFocus } from "@/components/run/evidence-image";

import { VerdictBadge } from "@/components/run/verdict-badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { CheckView, DimensionView } from "@/lib/api";
import {
  DIMENSION_LABEL,
  DIMENSION_ORDER,
  NOT_CHECKED,
  checkLabel,
  dimensionTitle,
  checkStatus,
  isVlmCheck,
  measured,
  orderChecks,
  signalLabel,
  type CheckStatus,
} from "@/lib/checks";
import { checkId } from "@/lib/evidence";
import { restoreQuotedTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";

const ROW_TONE: Record<CheckStatus, string> = {
  pass: "border-transparent",
  fail: "border-red-700/30 bg-red-700/5",
  // Abstain is neutral with a dashed border, never red (docs/ux.md).
  unverified: "border-dashed border-zinc-500/60",
};

function moveFocus(from: HTMLElement, delta: number) {
  const root = from.closest("[data-slot=scorecard]");
  if (!root) return;
  // Skip rows folded into a closed "N more passed" disclosure.
  const rows = [...root.querySelectorAll<HTMLElement>("li[data-check][tabindex]")].filter(
    (el) => el === from || !el.closest("details:not([open])"),
  );
  const next = rows[rows.indexOf(from) + delta];
  next?.focus();
}

/**
 * One check: status (icon + word), name, measured value vs threshold, signal and evidence.
 * With `focus`, hovering or focusing the row draws its evidence on the image; click or Enter pins
 * it, Escape unpins, and ↑/↓ move between checks (docs/ux.md accessibility).
 */
export function CheckRow({
  check,
  focus,
  requiredText,
}: {
  check: CheckView;
  focus?: EvidenceFocus;
  /** The brief's text: critical-token evidence is stored casefolded and shown as written. */
  requiredText?: string | null;
}) {
  const status = checkStatus(check.passed);
  const value = measured(check);
  const raw = check.evidence?.trim();
  const evidence = raw && check.check_name === "critical_tokens" ? restoreQuotedTokens(raw, requiredText) : raw;
  const id = checkId(check);
  const active = focus?.activeId === id;
  const pinned = focus?.pinnedId === id;
  return (
    <li
      data-check={check.check_name}
      data-status={status}
      data-active={active || undefined}
      data-pinned={pinned || undefined}
      tabIndex={focus ? 0 : undefined}
      aria-keyshortcuts={focus ? "Enter ArrowUp ArrowDown Escape" : undefined}
      onMouseEnter={focus ? () => focus.hover(id) : undefined}
      onMouseLeave={focus ? () => focus.hover(undefined) : undefined}
      onFocus={focus ? () => focus.focus(id) : undefined}
      onBlur={focus ? () => focus.focus(undefined) : undefined}
      onClick={focus ? () => focus.togglePin(id) : undefined}
      onKeyDown={
        focus
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                focus.togglePin(id);
              } else if (e.key === "Escape" && focus.pinnedId) {
                e.preventDefault();
                focus.clearPin();
              } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                moveFocus(e.currentTarget, e.key === "ArrowDown" ? 1 : -1);
              }
            }
          : undefined
      }
      className={cn(
        "min-h-9 rounded-md border px-2 py-1.5 text-sm outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring",
        ROW_TONE[status],
        focus && "cursor-pointer hover:bg-muted/60",
        active && "ring-2 ring-ring/40",
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <VerdictBadge status={status} className="h-5 px-1.5" />
        <span className="font-medium">{checkLabel(check.check_name)}</span>
        <span className="text-xs text-muted-foreground">{DIMENSION_LABEL[check.dimension] ?? check.dimension}</span>
        {pinned ? (
          <span className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground">
            <Pin className="size-3" aria-hidden /> Pinned
          </span>
        ) : null}
        {value ? (
          <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">{value}</span>
        ) : null}
      </div>
      {evidence || status !== "pass" ? (
        <p className={cn("mt-1 text-xs break-words", status === "fail" ? "text-foreground" : "text-muted-foreground")}>
          {evidence ||
            (status === "unverified" ? "No evidence returned. Treated as unsure." : "No reason returned.")}
          {status === "unverified" && evidence ? " Treated as not passed." : null}
        </p>
      ) : null}
      <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{signalLabel(check)}</p>
    </li>
  );
}

/** Per-dimension fallback while the check rows are loading (SSE carries dimensions only). */
function DimensionRow({ dimension, view }: { dimension: string; view: DimensionView }) {
  const status = checkStatus(view.passed);
  return (
    <li className={cn("min-h-9 rounded-md border px-2 py-1.5 text-sm", ROW_TONE[status])}>
      <div className="flex items-center gap-2">
        <VerdictBadge status={status} className="h-5 px-1.5" />
        <span className="font-medium">{DIMENSION_LABEL[dimension] ?? dimension}</span>
      </div>
      {view.reasons?.length ? (
        <ul className="mt-1 space-y-0.5 text-xs">
          {view.reasons.map((r, i) => (
            <li key={i} className="break-words">
              {r}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

const DOT: Record<CheckStatus, { icon: LucideIcon; className: string; word: string }> = {
  pass: { icon: CircleCheck, className: "text-emerald-700 dark:text-emerald-400", word: "pass" },
  fail: { icon: CircleX, className: "text-red-700 dark:text-red-400", word: "fail" },
  unverified: { icon: CircleHelp, className: "text-zinc-600 dark:text-zinc-300", word: "unsure" },
};

/**
 * Compact per-dimension verdicts: an icon and the dimension's name, never colour alone. Composition
 * is always listed; `null` (no evaluation yet) reads "not checked yet", and a missing composition
 * key on an older evaluation reads "Not checked" in neutral styling (see `NOT_CHECKED`).
 */
export function DimensionDots({
  verdicts,
  className,
}: {
  verdicts: Record<string, CheckStatus | null | undefined>;
  className?: string;
}) {
  const dims = ["text", "product", "context", "composition"].concat(
    verdicts.technical && verdicts.technical !== "pass" ? ["technical"] : [],
  );
  // Technical failed: the other dimensions weren't evaluated, so composition isn't "older data".
  const olderData = Object.keys(verdicts).some((d) => d !== "technical") && !("composition" in verdicts);
  return (
    <ul className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 text-xs", className)} aria-label="Dimensions">
      {dims.map((dim) => {
        const status = verdicts[dim];
        const spec = status ? DOT[status] : null;
        const Icon = spec?.icon;
        const notChecked = dim === "composition" && olderData;
        return (
          <li
            key={dim}
            className={cn("inline-flex items-center gap-1", notChecked && "text-muted-foreground")}
            data-dimension={dim}
            data-status={status ?? (notChecked ? "not_checked" : "pending")}
            title={`${dimensionTitle(dim)}: ${spec?.word ?? (notChecked ? NOT_CHECKED.toLowerCase() : "not checked yet")}`}
          >
            {Icon ? (
              <Icon className={cn("size-3.5", spec.className)} aria-hidden />
            ) : (
              <span className="inline-block size-3 rounded-full border border-dashed border-muted-foreground/60" aria-hidden />
            )}
            <span>{DIMENSION_LABEL[dim]}</span>
            {notChecked ? (
              <span>&nbsp;&middot; {NOT_CHECKED.toLowerCase()}</span>
            ) : (
              <span className="sr-only">: {spec?.word ?? "not checked yet"}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** A neutral row for composition on an evaluation made before it existed. */
function NotCheckedRow({ dimension }: { dimension: string }) {
  return (
    <li
      className="min-h-9 rounded-md border border-dashed border-border px-2 py-1.5 text-sm text-muted-foreground"
      data-dimension={dimension}
      data-status="not_checked"
    >
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge status="neutral" label={NOT_CHECKED} className="h-5 px-1.5" />
        <span className="font-medium text-foreground">{dimensionTitle(dimension)}</span>
      </div>
      <p className="mt-1 text-xs">Evaluated before this dimension existed; re-evaluate to score it.</p>
    </li>
  );
}

/** An older evaluation: other dimensions were judged but composition has no result. */
export function compositionNotChecked(
  dimensions: Record<string, unknown>,
  checks: readonly Pick<CheckView, "dimension">[] | null,
): boolean {
  if ("composition" in dimensions || (checks ?? []).some((c) => c.dimension === "composition")) return false;
  const judged = [...Object.keys(dimensions), ...(checks ?? []).map((c) => c.dimension)];
  return judged.some((d) => d === "product" || d === "context");
}

export function dimensionVerdicts(dimensions: Record<string, DimensionView>): Record<string, CheckStatus> {
  return Object.fromEntries(Object.entries(dimensions).map(([d, v]) => [d, checkStatus(v.passed)]));
}

/**
 * The checks for one candidate: deterministic signals first, then vision-judge checks. Falls back to
 * per-dimension rows when only the SSE summary has arrived, and to skeleton rows while checking.
 */
export function Scorecard({
  checks,
  dimensions = {},
  pending = false,
  focus,
  requiredText,
  className,
}: {
  checks: CheckView[] | null;
  dimensions?: Record<string, DimensionView>;
  /** The candidate is still being evaluated. */
  pending?: boolean;
  /** Evidence hover/focus/pin state; without it the rows are static. */
  focus?: EvidenceFocus;
  requiredText?: string | null;
  className?: string;
}) {
  if (checks && checks.length > 0) {
    const ordered = orderChecks(checks);
    const deterministic = ordered.filter((c) => !isVlmCheck(c));
    const vlm = ordered.filter((c) => isVlmCheck(c));
    const renderRows = (rows: CheckView[]) =>
      rows.map((c) => <CheckRow key={checkId(c)} check={c} focus={focus} requiredText={requiredText} />);
    // Failing and unsure checks stay visible; passes fold into one expandable row.
    const group = (title: string, rows: CheckView[]) => {
      if (!rows.length) return null;
      const open = rows.filter((c) => c.passed !== true);
      const passed = rows.filter((c) => c.passed === true);
      return (
        <section className="space-y-1">
          <h4 className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{title}</h4>
          {open.length ? <ul className="space-y-1">{renderRows(open)}</ul> : null}
          {passed.length ? (
            <details className="group/passed">
              <summary className="flex min-h-8 cursor-pointer items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground outline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
                <CircleCheck className="size-3.5 text-emerald-700 dark:text-emerald-400" aria-hidden />
                {open.length ? `${passed.length} more passed` : `All ${passed.length} passed`}
                <span className="group-open/passed:hidden">· show</span>
              </summary>
              <ul className="mt-1 space-y-1">{renderRows(passed)}</ul>
            </details>
          ) : null}
        </section>
      );
    };
    return (
      <div className={cn("space-y-2", className)} aria-label="Scorecard" data-slot="scorecard">
        {group("Deterministic checks", deterministic)}
        {group("Vision judge", vlm)}
        {compositionNotChecked(dimensions, checks) ? (
          <ul>
            <NotCheckedRow dimension="composition" />
          </ul>
        ) : null}
      </div>
    );
  }
  const dims = [
    ...DIMENSION_ORDER.filter((d) => d in dimensions),
    ...Object.keys(dimensions).filter((d) => !(DIMENSION_ORDER as readonly string[]).includes(d)),
  ];
  if (dims.length > 0) {
    return (
      <ul className={cn("space-y-1", className)} aria-label="Scorecard">
        {dims.map((d) => (
          <DimensionRow key={d} dimension={d} view={dimensions[d]!} />
        ))}
        {compositionNotChecked(dimensions, null) ? <NotCheckedRow dimension="composition" /> : null}
      </ul>
    );
  }
  if (pending) {
    return (
      <div className={cn("space-y-1", className)} aria-label="Checking">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-9 w-full motion-reduce:animate-none" />
        ))}
      </div>
    );
  }
  return null;
}
