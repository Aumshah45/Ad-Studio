import { describeDiff, diffChars } from "@/lib/evidence";
import { cn } from "@/lib/utils";

const MARK = "rounded-[2px] bg-red-700/15 text-red-700 underline decoration-2 underline-offset-2 dark:bg-red-400/20 dark:text-red-300";

/**
 * Expected vs read, character by character: what's missing or wrong is marked on the expected
 * line, and what the engine read instead is marked on the read line. The marks are paired with a
 * sentence for screen readers ("Missing 'e'."), never colour alone.
 */
export function TextDiff({
  expected,
  read,
  engine,
  className,
}: {
  expected: string;
  read: string;
  engine?: string | null;
  className?: string;
}) {
  const ops = diffChars(expected, read);
  const exact = ops.every((op) => op.op === "equal");
  return (
    <div className={cn("space-y-0.5 font-mono text-[11px] leading-snug", className)} data-slot="text-diff">
      <p className="break-words">
        <span className="mr-1.5 font-sans text-muted-foreground">Expected</span>
        <span aria-hidden>
          {ops.map((op, i) =>
            op.op === "equal" ? (
              <span key={i}>{op.expected}</span>
            ) : op.op === "insert" ? null : (
              <mark key={i} data-diff={op.op} className={MARK}>
                {op.expected}
              </mark>
            ),
          )}
        </span>
        <span className="sr-only">{expected}</span>
      </p>
      <p className="break-words">
        <span className="mr-1.5 font-sans text-muted-foreground">Read</span>
        <span aria-hidden>
          {read === "" ? (
            <span className="font-sans text-muted-foreground italic">nothing legible</span>
          ) : (
            ops.map((op, i) =>
              op.op === "equal" ? (
                <span key={i}>{op.read}</span>
              ) : op.op === "delete" ? (
                // A caret where a character is missing, so the gap is visible.
                <mark key={i} data-diff="gap" className={cn(MARK, "px-px no-underline")}>
                  ‸
                </mark>
              ) : (
                <mark key={i} data-diff={op.op} className={MARK}>
                  {op.read}
                </mark>
              ),
            )
          )}
        </span>
        <span className="sr-only">{read || "nothing legible"}</span>
        {engine ? <span className="ml-1.5 font-sans text-muted-foreground">· {engine}</span> : null}
      </p>
      <p className={cn("font-sans", exact ? "text-emerald-700 dark:text-emerald-400" : "text-foreground")}>
        {describeDiff(ops)}
      </p>
    </div>
  );
}
