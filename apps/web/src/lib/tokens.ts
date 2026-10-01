/**
 * Critical tokens: numbers, prices, percentages, dates/times and short all-caps words that must
 * appear exactly on the ad. Mirrors `backend.domain.adstudio.textnorm.critical_tokens`, but keeps
 * the brief's own spelling for display (the API stores them casefolded, e.g. `off`).
 */

const DASHES = /[‐-―−﹘﹣－⸺⸻]/g;
const NUMBER = /(?<![\p{L}\p{N}])[-+]?\p{Sc}?\d[\d.,:/]*%?\p{Sc}?/gu;
const CAPS = /(?<![\p{L}\p{N}])\p{Lu}[\p{Lu}\p{N}]{1,3}(?![\p{L}\p{N}])/gu;

/** The API's comparison key: NFKC, casefolded, dashes unified. */
export function tokenKey(token: string): string {
  return token.normalize("NFKC").replace(DASHES, "-").toLowerCase().trim();
}

/** Critical tokens of the required text, in order, as written. */
export function criticalTokens(raw: string): string[] {
  const text = raw.normalize("NFKC").replace(DASHES, "-");
  const found: string[] = [];
  for (const m of text.matchAll(NUMBER)) found.push(m[0].replace(/[.,:/]+$/, ""));
  for (const m of text.matchAll(CAPS)) if (!/\d/.test(m[0])) found.push(m[0]);
  const seen = new Set<string>();
  return found.filter((t) => {
    const k = tokenKey(t);
    if (!k || seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/**
 * Show stored (casefolded) tokens the way the brief wrote them: `off` → `OFF` when the required
 * text has `OFF`. Tokens not found in the text are shown as stored.
 */
export function displayTokens(stored: readonly string[], requiredText: string | null | undefined): string[] {
  if (!requiredText) return [...stored];
  const written = new Map(criticalTokens(requiredText).map((t) => [tokenKey(t), t]));
  return stored.map((t) => written.get(tokenKey(t)) ?? t);
}

/** `Missing or altered: 'off'` → `Missing or altered: 'OFF'` when the brief wrote `OFF`. */
export function restoreQuotedTokens(text: string, requiredText: string | null | undefined): string {
  if (!requiredText) return text;
  const written = new Map(criticalTokens(requiredText).map((t) => [tokenKey(t), t]));
  return text.replace(/(['\u2018\u2019])([^'\u2018\u2019]{1,24})(['\u2018\u2019])/g, (all, open: string, tok: string, close: string) => {
    const w = written.get(tokenKey(tok));
    return w ? `${open}${w}${close}` : all;
  });
}
