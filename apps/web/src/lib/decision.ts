/** The human release decision, cancel, download and "rerun" links for a run (slice 21). */
import { runsCancelRun, runsDecideRun, type BriefView, type DecisionView } from "@/lib/api";
import { isProblem, problemCode, problemMessage } from "@/lib/problem";

export type DecisionResult = { ok: true; decision: DecisionView } | { ok: false; code: string | null; message: string };

/** `POST /v1/runs/{id}/decision`. Approving a held run needs a reason (422 override-reason-required). */
export async function decideRun(
  runId: string,
  action: "approve" | "reject",
  reason?: string | null,
): Promise<DecisionResult> {
  try {
    const res = await runsDecideRun({
      path: { run_id: runId },
      body: { action, reason: reason?.trim() ? reason.trim() : null },
    });
    if (res.data) return { ok: true, decision: res.data };
    if (isProblem(res.error)) return { ok: false, code: problemCode(res.error), message: problemMessage(res.error) };
    return { ok: false, code: null, message: `The API answered ${res.response?.status ?? "with an error"}. Try again.` };
  } catch {
    return { ok: false, code: null, message: "Can't reach Ad Studio's API. Your decision wasn't saved." };
  }
}

export type CancelResult = { ok: true; cancelled: boolean; status: string } | { ok: false; message: string };

/** `POST /v1/runs/{id}/cancel`; `cancelled: false` when the run ended on its own first. */
export async function cancelRun(runId: string): Promise<CancelResult> {
  try {
    const res = await runsCancelRun({ path: { run_id: runId } });
    if (res.data) return { ok: true, cancelled: res.data.cancelled, status: res.data.status };
    return {
      ok: false,
      message: isProblem(res.error) ? problemMessage(res.error) : "The run couldn't be cancelled. Try again.",
    };
  } catch {
    return { ok: false, message: "Can't reach Ad Studio's API. The run may still be going." };
  }
}

const EXT: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };

export interface Downloaded {
  filename: string;
  format: string;
}

/** Fetch the approved image and save it through a temporary object URL. */
export async function downloadAd(url: string, basename: string): Promise<Downloaded> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const ext = EXT[blob.type] ?? "png";
  const filename = `${basename}.${ext}`;
  const href = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = href;
    a.download = filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    // Let the click start the download before the URL goes away.
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }
  return { filename, format: ext.toUpperCase() };
}

/** `ad-studio-au-december-1a2b3c4d`: readable, filesystem-safe. */
export function adBasename(brief: Pick<BriefView, "geography_code" | "season"> | null, runId: string): string {
  const parts = ["ad-studio", brief?.geography_code, brief?.season, runId.slice(0, 8)]
    .filter((p): p is string => Boolean(p))
    .map((p) =>
      p
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, ""),
    )
    .filter(Boolean);
  return parts.join("-");
}

/** Studio link prefilled with this run's brief ("Rerun for another market" / "Rerun with edits"). */
export function rerunHref(
  brief: Pick<BriefView, "geography_code" | "season" | "required_text" | "aspect_ratio"> | null,
  productId: string | null,
): string {
  if (!brief) return "/";
  const q = new URLSearchParams();
  if (productId) q.set("product", productId);
  q.set("geo", brief.geography_code);
  q.set("season", brief.season);
  q.set("text", brief.required_text);
  q.set("aspect", brief.aspect_ratio);
  return `/?${q.toString()}`;
}
