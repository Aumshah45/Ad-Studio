/**
 * Terminal reason codes (from `run.finished.reason`, `run.error.problem.type`) → sentences a
 * marketer can act on (docs/ux.md "Copy": say what happened, then what to do).
 */

const REASONS: Record<string, string> = {
  evaluation_failed:
    "The best candidate still failed a quality check, so it wasn't approved. Review the failing checks below.",
  repairs_exhausted:
    "Targeted repairs didn't fix every failing check within the repair limit, so no ad was approved.",
  max_repairs: "Targeted repairs didn't fix every failing check within the repair limit, so no ad was approved.",
  text_exhausted:
    "The required text still didn't read exactly after the native attempts and the Ad Studio text fallback.",
  overlay_failed: "Ad Studio couldn't draw the required text legibly in the reserved zone.",
  text_does_not_fit:
    "The text doesn't fit the reserved zone at a readable size. Shorten it or choose the 1:1 placement.",
  generation_blocked:
    "The image model returned no image (safety filter). Try rewording the brief or using another product photo.",
  judge_unavailable:
    "The vision judge was unavailable, so Context couldn't be checked. Nothing is auto-approved without it.",
  vision_unavailable:
    "The vision judge was unavailable, so Context couldn't be checked. Nothing is auto-approved without it.",
  unverified: "Some checks couldn't be verified, and an unverified check never counts as a pass.",
  budget_exceeded: "Stopped at the budget for this ad. The best candidate is held for review.",
  // Repair routing's "no repair left" row (services/api repair.py), not the budget.
  exhausted:
    "Targeted repairs didn't fix every failing check within the repair limit, so the best candidate is held for review.",
  budget_stop: "Stopped at the budget for this ad. The best candidate is held for review.",
  daily_budget_exceeded: "Today's generation budget is used up. Recorded runs still open.",
  deadline_exceeded: "Stopped at the time limit for this ad. The best candidate is held for review.",
  wallclock_exceeded: "Stopped at the time limit for this ad. The best candidate is held for review.",
  timeout: "The run took too long and was stopped. Try again; cached steps are free.",
  model_unavailable: "The image model is unavailable right now. Try again in a minute; recorded runs still open.",
  rate_limited: "The image model's quota was reached. Try again shortly.",
  run_interrupted: "The API restarted during this run. Run the brief again; cached steps are free.",
  interrupted: "The API restarted during this run. Run the brief again; cached steps are free.",
  internal_error: "Something went wrong on the server. Run the brief again; cached steps are free.",
  storage_error: "Ad Studio couldn't save an image. Try again.",
  cancelled: "The run was cancelled.",
  failed: "The run stopped because of a system error.",
  needs_review: "The quality gate couldn't pass this ad, so it's held for a person to review.",
};

function normalise(code: string): string {
  return code.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
}

/** One reason code (optionally `code: detail`) as a sentence; unknown codes are humanised. */
export function friendlyReason(raw: string): string {
  const [head = "", ...rest] = raw.split(":");
  const code = normalise(head);
  const known = REASONS[code];
  const detail = rest.join(":").trim();
  if (known) return detail && code === "generation_blocked" ? `${known} (${detail})` : known;
  const words = head.trim().replaceAll(/[_-]+/g, " ");
  if (!words) return "The run ended without a reason.";
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}${detail ? `: ${detail}` : ""}.`;
}

/** A reason field may list several codes (`a, b` or `a; b`); each becomes one sentence. */
export function friendlyReasons(raw: string | null | undefined): string[] {
  if (!raw) return [];
  // A `code: free text` reason may contain commas, so only `;` separates codes then.
  const parts = raw
    .split(raw.includes(":") ? ";" : /[;,]/)
    .map((p) => p.trim())
    .filter(Boolean);
  return [...new Set(parts.map(friendlyReason))];
}
