import { Type, Wrench } from "lucide-react";

import { apiUrl, type CandidateState } from "@/lib/run-events";
import { DIMENSION_LABEL } from "@/lib/checks";
import { friendlyReason } from "@/lib/run-reasons";

const SUMMARY_CHARS = 220;

/** What each routing action does, in a marketer's words (`repair.started.action`). */
export const REPAIR_ACTION: Record<string, { dimension: string | null; label: string; what: string }> = {
  regenerate: { dimension: null, label: "Regenerate", what: "Generating a fresh candidate from the brief." },
  repair_text: { dimension: "text", label: "Text repair", what: "Redrawing the required text only." },
  repair_product: { dimension: "product", label: "Product repair", what: "Restoring the product to match the reference photo." },
  repair_context: { dimension: "context", label: "Context repair", what: "Adjusting the scene to the market and season." },
  repair_composition: {
    dimension: "composition",
    label: "Composition repair",
    what: "Resizing the product to a realistic scale next to the scene's objects and grounding it in the scene: contact shadow, the scene's light, perspective and depth of field, no cut-out edge.",
  },
  clean_plate: { dimension: "text", label: "Clean plate", what: "Clearing the text zone so the exact text can be drawn." },
};

/** The dimension a repair targets: the event's, else the one its action implies. */
export function repairTarget(targetDimension: string | null, action?: string | null): string | null {
  return targetDimension ?? (action ? (REPAIR_ACTION[action]?.dimension ?? null) : null);
}

/** The targeted repair: which dimension, from which candidate, and the instruction fed back. */
export function RepairCard({
  fromLabel,
  targetDimension,
  action,
  instruction,
  model,
}: {
  fromLabel: string;
  targetDimension: string | null;
  /** Routing action (`repair.started.action`), e.g. `repair_composition`. */
  action?: string | null;
  instruction: string | null;
  model?: string | null;
}) {
  const dim = repairTarget(targetDimension, action);
  const target = dim ? (DIMENSION_LABEL[dim] ?? dim) : null;
  const known = action ? REPAIR_ACTION[action] : undefined;
  const text = instruction?.trim() ?? "";
  const long = text.length > SUMMARY_CHARS;
  return (
    <div
      className="space-y-2 rounded-lg border border-amber-700/25 bg-amber-700/5 p-3 text-sm"
      data-slot="repair-card"
      data-action={action ?? undefined}
    >
      <p className="flex items-center gap-2 font-medium">
        <Wrench className="size-4 text-amber-700 dark:text-amber-400" aria-hidden />
        <span>
          Repairing candidate {fromLabel}
          {target ? ` · fix ${target} only` : ""}
          {model ? <span className="font-mono text-xs font-normal text-muted-foreground"> with {model}</span> : null}
        </span>
      </p>
      {known ? (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{known.label}:</span> {known.what}
        </p>
      ) : null}
      {text ? (
        <div className="text-xs">
          <p className="mb-1 text-muted-foreground">Instruction sent:</p>
          {long ? (
            <details>
              <summary className="cursor-pointer rounded-sm font-mono break-words outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">
                {text.slice(0, SUMMARY_CHARS).trimEnd()}… <span className="font-sans text-primary">Show all</span>
              </summary>
              <p className="mt-1 font-mono break-words whitespace-pre-wrap">{text}</p>
            </details>
          ) : (
            <p className="font-mono break-words whitespace-pre-wrap">{text}</p>
          )}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">The repair instruction wasn&rsquo;t recorded.</p>
      )}
    </div>
  );
}

/** Reason codes read as sentences; free-text reasons keep their words, as one sentence. */
export function reasonText(reason: string): string {
  const t = reason.trim();
  if (/^[a-z][a-z0-9_-]*(:|$)/.test(t)) return friendlyReason(t);
  const s = t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?]$/.test(s) ? s : `${s}.`;
}

function Thumb({ c, caption }: { c: CandidateState; caption: string }) {
  return (
    <figure className="space-y-1">
      <div
        className="flex w-full items-center justify-center overflow-hidden rounded-md border bg-muted"
        style={{ aspectRatio: c.width && c.height ? `${c.width} / ${c.height}` : "4 / 5" }}
      >
        {c.imageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- API-served, already ≤ 1024 px
          <img src={apiUrl(c.imageUrl)} alt={caption} className="size-full object-contain" />
        ) : null}
      </div>
      <figcaption className="text-center text-xs text-muted-foreground">{caption}</figcaption>
    </figure>
  );
}

/** Disclosure that the exact text was drawn by Ad Studio, with the reason and before/after. */
export function FallbackCard({
  reason,
  native,
  overlay,
}: {
  reason: string | null;
  native?: CandidateState | null;
  overlay?: CandidateState | null;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-amber-700/25 bg-amber-700/5 p-3 text-sm">
      <p className="flex items-center gap-2 font-medium">
        <Type className="size-4 text-amber-700 dark:text-amber-400" aria-hidden />
        Text rendered by Ad Studio
      </p>
      <p className="text-muted-foreground">
        {reason ? `Why: ${reasonText(reason)}` : "Native text didn't pass, so the exact text was drawn in the reserved zone."}{" "}
        This ad is disclosed as &ldquo;Text drawn by Ad Studio, not the model&rdquo;.
      </p>
      {native?.imageUrl && overlay?.imageUrl ? (
        <div className="grid max-w-md grid-cols-2 gap-3">
          <Thumb c={native} caption="Native attempt" />
          <Thumb c={overlay} caption="Text drawn by Ad Studio" />
        </div>
      ) : null}
    </div>
  );
}
