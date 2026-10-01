import {
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  Info,
  MinusCircle,
  PauseCircle,
  TriangleAlert,
  Type,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

type Tone = "pass" | "fail" | "warn" | "unsure" | "running" | "info" | "neutral";

const TONE_CLASS: Record<Tone, string> = {
  pass: "border-emerald-700/25 bg-emerald-700/10 text-emerald-700 dark:text-emerald-400",
  fail: "border-red-700/25 bg-red-700/10 text-red-700 dark:text-red-400",
  // amber-800: amber-700 on its own 10% tint is 4.39:1, below AA (docs/ux.md contrast list).
  warn: "border-amber-700/25 bg-amber-700/10 text-amber-800 dark:text-amber-400",
  unsure: "border-dashed border-zinc-500/60 text-zinc-600 dark:text-zinc-300",
  running: "border-primary/25 bg-primary/10 text-primary",
  info: "border-sky-700/25 bg-sky-700/10 text-sky-700 dark:text-sky-400",
  neutral: "border-border text-muted-foreground",
};

interface Spec {
  tone: Tone;
  label: string;
  icon?: LucideIcon;
}

const STATUS: Record<string, Spec> = {
  // run statuses
  queued: { tone: "neutral", label: "Queued", icon: CircleDashed },
  planning: { tone: "running", label: "Planning" },
  generating: { tone: "running", label: "Generating" },
  evaluating: { tone: "running", label: "Evaluating" },
  repairing: { tone: "running", label: "Repairing" },
  fallback: { tone: "running", label: "Text fallback" },
  passed: { tone: "pass", label: "Passed", icon: CircleCheck },
  needs_review: { tone: "unsure", label: "Held for review", icon: PauseCircle },
  failed: { tone: "fail", label: "Failed", icon: CircleX },
  interrupted: { tone: "fail", label: "Interrupted", icon: CircleX },
  cancelled: { tone: "neutral", label: "Cancelled", icon: CircleX },
  approved: { tone: "pass", label: "Approved", icon: CircleCheck },
  rejected: { tone: "fail", label: "Rejected", icon: CircleX },
  // candidate verdicts
  pass: { tone: "pass", label: "Pass", icon: CircleCheck },
  fail: { tone: "fail", label: "Fail", icon: CircleX },
  unverified: { tone: "unsure", label: "Unsure", icon: CircleHelp },
  repaired: { tone: "warn", label: "Repaired", icon: Wrench },
  pending: { tone: "neutral", label: "Pending", icon: CircleDashed },
  blocked: { tone: "fail", label: "Blocked", icon: CircleX },
  // timeline step states (docs/ux.md "Streaming step statuses")
  running: { tone: "running", label: "Running" },
  unsure: { tone: "unsure", label: "Unsure", icon: CircleHelp },
  held: { tone: "unsure", label: "Held", icon: PauseCircle },
  skipped: { tone: "neutral", label: "Skipped", icon: MinusCircle },
  overlay: { tone: "warn", label: "Text fallback", icon: Type },
  // informational (replay, cached, fake client)
  info: { tone: "info", label: "Info", icon: Info },
  degraded: { tone: "warn", label: "Degraded", icon: TriangleAlert },
  neutral: { tone: "neutral", label: "—" },
};

/** Status shown with an icon and a word, never by colour alone (docs/ux.md). */
export function VerdictBadge({ status, label, className }: { status: string; label?: string; className?: string }) {
  const spec = STATUS[status] ?? { tone: "neutral" as const, label: status };
  const Icon = spec.icon;
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-md border px-2 text-xs font-medium whitespace-nowrap",
        TONE_CLASS[spec.tone],
        className,
      )}
    >
      {spec.tone === "running" ? <Spinner className="size-3" aria-hidden /> : Icon ? <Icon className="size-3.5" aria-hidden /> : null}
      {label ?? spec.label}
    </span>
  );
}

/** Pass / fail / unsure for one evaluation dimension (`passed: null` = unverified). */
export function dimensionStatus(passed: boolean | null | undefined): "pass" | "fail" | "unverified" {
  return passed === true ? "pass" : passed === false ? "fail" : "unverified";
}
