import { TriangleAlert } from "lucide-react";

import { CodeText } from "@/components/code-text";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/** The dry-run notice has its own banner, so the strip drops the report's FAKE DRY RUN line. */
export function visibleWarnings(warnings: readonly string[] | null | undefined, dryRunShown: boolean): string[] {
  return (warnings ?? []).filter((w) => !(dryRunShown && /^FAKE DRY RUN\b/i.test(w)));
}

/** What a reader must know before trusting the numbers: missing labels, stale snapshot or report. */
export function ReportWarnings({ warnings, dryRunShown = false }: { warnings?: string[] | null; dryRunShown?: boolean }) {
  const shown = visibleWarnings(warnings, dryRunShown);
  if (!shown.length) return null;
  return (
    <Alert role="note" aria-label="Report warnings" className="border-amber-700/40 bg-amber-700/5" data-slot="report-warnings">
      <TriangleAlert aria-hidden />
      <AlertTitle>
        {shown.length === 1 ? "1 thing to know about this report" : `${shown.length} things to know about this report`}
      </AlertTitle>
      <AlertDescription>
        <ul className="list-disc space-y-0.5 pl-4">
          {shown.map((w) => (
            <li key={w}>
              <CodeText>{w}</CodeText>
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
