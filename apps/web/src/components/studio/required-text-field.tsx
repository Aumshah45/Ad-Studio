"use client";

import { Textarea } from "@/components/ui/textarea";
import { criticalTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";

export const REQUIRED_TEXT_MAX_CHARS = 80;
export const REQUIRED_TEXT_MAX_LINES = 3;

// Mirrors the API's check (runs.validate_required_text): control, bidi, zero-width and tag
// characters can't be rendered faithfully. ZWJ/ZWNJ are allowed.
const INVISIBLE_CODE_POINTS = new Set([0x061c, 0x180e, 0x200b, 0x200e, 0x200f, 0x2060, 0xfeff]);

function hasInvisible(text: string): boolean {
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    if (cp === 0x0a) continue;
    if (
      cp < 0x20 ||
      (cp >= 0x7f && cp <= 0x9f) ||
      (cp >= 0x202a && cp <= 0x202e) ||
      (cp >= 0x2066 && cp <= 0x2069) ||
      (cp >= 0xe0000 && cp <= 0xe007f) ||
      INVISIBLE_CODE_POINTS.has(cp)
    ) {
      return true;
    }
  }
  return false;
}
const INSTRUCTION_LIKE = /\b(ignore|disregard|forget)\b.*\b(instruction|previous|above|prompt)s?\b|\bsystem prompt\b/i;

/** Length as the API counts it: NFC, CRLF folded, outer whitespace trimmed, code points. */
export function measureRequiredText(raw: string): { chars: number; lines: number } {
  const text = raw.replace(/\r\n/g, "\n").normalize("NFC").trim();
  return { chars: [...text].length, lines: text ? text.split("\n").length : 0 };
}

/** Client-side mirror of the API rules, so most mistakes are caught before a request. */
export function requiredTextError(raw: string): string | null {
  const { chars, lines } = measureRequiredText(raw);
  if (chars === 0) return "Enter the text that must appear on the ad.";
  if (chars > REQUIRED_TEXT_MAX_CHARS)
    return `Keep required text under ${REQUIRED_TEXT_MAX_CHARS} characters so it fits the text zone (now ${chars}).`;
  if (lines > REQUIRED_TEXT_MAX_LINES) return `Use at most ${REQUIRED_TEXT_MAX_LINES} lines (now ${lines}).`;
  if (hasInvisible(raw.replace(/\r\n/g, "\n").trim())) return "Remove invisible or control characters from the text.";
  return null;
}

interface RequiredTextFieldProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  /** Error to show (client or API); rendered by the parent. */
  invalid?: boolean;
  describedBy?: string;
  disabled?: boolean;
  onSubmitShortcut?: () => void;
}

export function RequiredTextField({
  id,
  value,
  onChange,
  invalid,
  describedBy,
  disabled,
  onSubmitShortcut,
}: RequiredTextFieldProps) {
  const { chars, lines } = measureRequiredText(value);
  const over = chars > REQUIRED_TEXT_MAX_CHARS || lines > REQUIRED_TEXT_MAX_LINES;
  const countId = id ? `${id}-count` : undefined;
  const instructionLike = INSTRUCTION_LIKE.test(value);
  const tokens = criticalTokens(value);
  return (
    <div className="space-y-1.5">
      <Textarea
        id={id}
        value={value}
        rows={2}
        disabled={disabled}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            onSubmitShortcut?.();
          }
        }}
        aria-invalid={invalid || over || undefined}
        aria-describedby={[describedBy, countId].filter(Boolean).join(" ") || undefined}
        className="font-medium"
      />
      <div
        id={countId}
        className={cn(
          "flex justify-end gap-3 text-xs tabular-nums text-muted-foreground",
          over && "text-destructive",
        )}
      >
        <span>
          {lines}/{REQUIRED_TEXT_MAX_LINES} lines
        </span>
        <span>
          {chars}/{REQUIRED_TEXT_MAX_CHARS} characters
        </span>
      </div>
      {tokens.length ? (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground" data-slot="critical-tokens">
          Must match exactly:
          {tokens.map((t) => (
            <code key={t} className="rounded-md border bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">
              {t}
            </code>
          ))}
        </p>
      ) : null}
      {instructionLike ? (
        <p className="text-xs text-sky-700 dark:text-sky-400">
          This text will be printed exactly as written. Ad Studio never follows instructions inside it.
        </p>
      ) : null}
    </div>
  );
}
