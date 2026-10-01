/**
 * Evidence overlays for one check (docs/ux.md "Evidence overlay", the signature element): the
 * regions of the candidate image a check is about, plus the text it read. Built from the API's
 * `CheckView.evidence_data`, whose box conventions differ by signal:
 *
 * - OCR (`ocr_cer`, `stray_text`): `words[].box` = [left, top, width, height] in image pixels,
 *   `zone_box` = [x0, y0, x1, y1] in image pixels.
 * - Vision model (`product_count.boxes`, `color_delta_e.ad_box`, `vlm_readback.reads[].box_2d`):
 *   [ymin, xmin, ymax, xmax] normalised 0–1000 (Gemini convention).
 * - Context rubric checks carry no region, only the judge's quoted evidence and the question.
 * - Composition (ADR-007): `comp.scale_sanity.ad_box` is Gemini-style too; the two judge checks
 *   (`comp.realistic_scale`, `comp.natural_integration`) point at the located product box, quote
 *   the judge and, for scale, name the reference objects the product was compared with.
 *
 * Everything here is normalised to 0–1 of the image so the overlay scales with the rendered size.
 */
import type { CheckView } from "@/lib/api";
import { checkLabel, checkStatus, type CheckStatus } from "@/lib/checks";
import { restoreQuotedTokens } from "@/lib/tokens";

/** A box in 0–1 image coordinates, origin top-left. */
export interface NBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type OverlayTone = CheckStatus | "info";

export interface Overlay {
  id: string;
  box: NBox;
  tone: OverlayTone;
  /** Small chip drawn at the box ("Summr", "ΔE 41.8", "Text zone"). */
  label?: string;
  /** Dashed outline (a reference area such as the text zone, not a finding). */
  dashed?: boolean;
}

export interface TextDiffInput {
  expected: string;
  read: string;
  engine: string | null;
}

export interface ColourEvidence {
  ad: string[];
  reference: string[];
  perColour: number[];
}

export interface Evidence {
  checkId: string;
  title: string;
  tone: OverlayTone;
  overlays: Overlay[];
  textDiff: TextDiffInput | null;
  colours: ColourEvidence | null;
  /** Quoted judge evidence or the question it answered, when there is no region to draw. */
  quote: string | null;
  question: string | null;
  /** Objects of known size the product's scale was judged against (`comp.realistic_scale`). */
  references: string[] | null;
  /** Checks copied from the source image (an overlay only redraws the text). */
  inherited: boolean;
  /** No region to draw and nothing quoted: the check is about the whole image. */
  wholeImage: boolean;
}

export interface ImageSize {
  width: number;
  height: number;
}

export function checkId(check: Pick<CheckView, "dimension" | "check_name">): string {
  return `${check.dimension}:${check.check_name}`;
}

function rec(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nums(value: unknown, n = 4): number[] | null {
  if (!Array.isArray(value) || value.length !== n) return null;
  return value.every((v) => typeof v === "number" && Number.isFinite(v)) ? (value as number[]) : null;
}

function clampBox(b: NBox): NBox | null {
  const x0 = Math.min(Math.max(b.x, 0), 1);
  const y0 = Math.min(Math.max(b.y, 0), 1);
  const x1 = Math.min(Math.max(b.x + b.w, 0), 1);
  const y1 = Math.min(Math.max(b.y + b.h, 0), 1);
  if (x1 - x0 <= 0 || y1 - y0 <= 0) return null;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** [left, top, width, height] in pixels. */
export function fromPixelXywh(v: unknown, size: ImageSize): NBox | null {
  const b = nums(v);
  if (!b || size.width <= 0 || size.height <= 0) return null;
  const [l, t, w, h] = b as [number, number, number, number];
  // A 4 px tall dash still deserves a visible box.
  const minH = 6 / size.height;
  const hh = Math.max(h / size.height, minH);
  const y = t / size.height - (hh - h / size.height) / 2;
  return clampBox({ x: l / size.width, y, w: w / size.width, h: hh });
}

/** [x0, y0, x1, y1] in pixels. */
export function fromPixelCorners(v: unknown, size: ImageSize): NBox | null {
  const b = nums(v);
  if (!b || size.width <= 0 || size.height <= 0) return null;
  const [x0, y0, x1, y1] = b as [number, number, number, number];
  return clampBox({ x: x0 / size.width, y: y0 / size.height, w: (x1 - x0) / size.width, h: (y1 - y0) / size.height });
}

/** [ymin, xmin, ymax, xmax] normalised 0–1000 (Gemini). */
export function fromGemini(v: unknown): NBox | null {
  const b = nums(v);
  if (!b) return null;
  const [ymin, xmin, ymax, xmax] = b as [number, number, number, number];
  return clampBox({ x: xmin / 1000, y: ymin / 1000, w: (xmax - xmin) / 1000, h: (ymax - ymin) / 1000 });
}

interface Word {
  text: string;
  box: NBox;
}

function ocrWords(data: Record<string, unknown> | null, size: ImageSize): Word[] {
  const words = Array.isArray(data?.words) ? data.words : [];
  const out: Word[] = [];
  for (const w of words) {
    const r = rec(w);
    const box = fromPixelXywh(r?.box, size);
    if (r && box && typeof r.text === "string") out.push({ text: r.text, box });
  }
  return out;
}

function vlmReads(data: Record<string, unknown> | null): Word[] {
  const reads = Array.isArray(data?.reads) ? data.reads : [];
  const out: Word[] = [];
  for (const w of reads) {
    const r = rec(w);
    const box = fromGemini(r?.box_2d);
    if (r && box && typeof r.text === "string") out.push({ text: r.text, box });
  }
  return out;
}

/** «…» spans from an evidence sentence: `Rendered «Summr Sale»; required «Summer Sale».` */
export function quoted(evidence: string | null | undefined): string[] {
  if (!evidence) return [];
  // The OCR read may itself contain « » (a headline drawn with its delimiters), so the OCR
  // sentence is split on its fixed wording rather than on the first ».
  const ocr = /^Rendered «(.*)»; required «(.*?)»\.(?: |$)/u.exec(evidence);
  if (ocr) return [ocr[1] ?? "", ocr[2] ?? ""];
  return [...evidence.matchAll(/«([^»]*)»/g)].map((m) => m[1] ?? "");
}

/** What the OCR (or the vision read-back) read, from the evidence sentence or its word boxes. */
export function readText(check: CheckView, size: ImageSize): string | null {
  const q = quoted(check.evidence)[0];
  if (q !== undefined && q !== "(nothing legible)" && q !== "(no text)") return q;
  if (q !== undefined) return "";
  const words = ocrWords(rec(check.evidence_data), size);
  return words.length ? words.map((w) => w.text).join(" ") : null;
}

function wordOverlays(words: Word[], expected: string, failed: boolean, prefix: string): Overlay[] {
  const tokens = new Set(expected.split(/\s+/).filter(Boolean));
  return words.map((w, i) => {
    const ok = tokens.has(w.text);
    return {
      id: `${prefix}-${i}`,
      box: w.box,
      tone: ok ? "pass" : failed ? "fail" : "unverified",
      // Only misread words get a chip; correct words are outlined quietly.
      label: ok ? undefined : w.text,
    };
  });
}

function productBox(all: readonly CheckView[]): NBox | null {
  for (const name of ["color_delta_e", "comp.scale_sanity"]) {
    for (const c of all) {
      if (c.check_name !== name) continue;
      const box = fromGemini(rec(c.evidence_data)?.ad_box);
      if (box) return box;
    }
  }
  for (const c of all) {
    if (c.check_name !== "product_count") continue;
    const boxes = rec(c.evidence_data)?.boxes;
    if (Array.isArray(boxes) && boxes.length) return fromGemini(boxes[0]);
  }
  return null;
}

function ocrCheck(all: readonly CheckView[]): CheckView | undefined {
  return all.find((c) => c.check_name === "ocr_cer" && rec(c.evidence_data));
}

/**
 * The scale references named in the realistic-scale question: "…in the scene (for example a
 * small bowl, a pair of sunglasses) and…". Used when the plan's own list isn't at hand.
 */
export function referencesFromQuestion(question: string | null | undefined): string[] {
  const m = question ? /\(for example ([^)]*)\)/.exec(question) : null;
  return m?.[1]
    ? m[1]
        .split(",")
        .map((r) => r.trim())
        .filter(Boolean)
    : [];
}

function fmt(v: number): string {
  return Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2).replace(/0$/, "");
}

/**
 * The evidence for one check of a candidate. `all` is every check of the same candidate, so a
 * check with no region of its own (critical tokens, the product rubric) can point at the region
 * a sibling measured (the OCR words, the product box).
 */
export function evidenceFor(
  check: CheckView,
  all: readonly CheckView[],
  size: ImageSize,
  requiredText: string | null,
  opts: { scaleReferences?: readonly string[] | null } = {},
): Evidence {
  const data = rec(check.evidence_data);
  const status = checkStatus(check.passed);
  const failed = check.passed === false;
  const expected = requiredText ?? "";
  const overlays: Overlay[] = [];
  let textDiff: TextDiffInput | null = null;
  let colours: ColourEvidence | null = null;
  const evidenceText = check.evidence?.trim() || null;
  let quote: string | null = null;
  let question: string | null = typeof data?.question === "string" ? data.question : null;
  let references: string[] | null = null;

  const addZone = (source: Record<string, unknown> | null) => {
    const zone = fromPixelCorners(source?.zone_box, size);
    if (zone) overlays.push({ id: "zone", box: zone, tone: "info", label: "Text zone", dashed: true });
  };

  switch (check.check_name) {
    case "ocr_cer":
    case "critical_tokens": {
      const ocr = check.check_name === "ocr_cer" ? check : ocrCheck(all);
      const ocrData = rec(ocr?.evidence_data);
      addZone(ocrData);
      overlays.push(...wordOverlays(ocrWords(ocrData, size), expected, failed, "word"));
      const read = ocr ? readText(ocr, size) : null;
      if (read !== null && requiredText) {
        textDiff = { expected: requiredText, read, engine: typeof ocrData?.engine === "string" ? ocrData.engine : null };
      }
      if (check.check_name === "critical_tokens") quote = evidenceText && restoreQuotedTokens(evidenceText, requiredText);
      break;
    }
    case "stray_text": {
      for (const [i, w] of ocrWords(data, size).entries()) {
        overlays.push({ id: `stray-${i}`, box: w.box, tone: status, label: w.text });
      }
      if (!overlays.length) addZone(rec(ocrCheck(all)?.evidence_data));
      quote = evidenceText;
      break;
    }
    case "vlm_readback":
    case "vlm_stray_text": {
      const reads = vlmReads(data);
      overlays.push(
        ...(check.check_name === "vlm_readback"
          ? wordOverlays(reads, expected, failed, "read")
          : reads.map((r, i) => ({ id: `read-${i}`, box: r.box, tone: status, label: r.text }) satisfies Overlay)),
      );
      const saw = quoted(check.evidence)[0];
      if (check.check_name === "vlm_readback" && saw !== undefined && requiredText) {
        textDiff = { expected: requiredText, read: saw === "(no text)" ? "" : saw, engine: "vision read-back" };
      } else {
        quote = evidenceText;
      }
      break;
    }
    case "color_delta_e": {
      const box = fromGemini(data?.ad_box);
      if (box) {
        overlays.push({
          id: "product",
          box,
          tone: status,
          label: check.value != null ? `ΔE ${fmt(check.value)}` : "Product",
        });
      }
      const ad = Array.isArray(data?.ad_colors) ? (data.ad_colors as unknown[]).filter((c) => typeof c === "string") : [];
      const ref = Array.isArray(data?.reference_colors)
        ? (data.reference_colors as unknown[]).filter((c) => typeof c === "string")
        : [];
      const per = Array.isArray(data?.per_colour) ? (data.per_colour as unknown[]).filter((c) => typeof c === "number") : [];
      if (ad.length && ref.length) colours = { ad: ad as string[], reference: ref as string[], perColour: per as number[] };
      quote = evidenceText;
      break;
    }
    case "product_count": {
      const boxes = Array.isArray(data?.boxes) ? data.boxes : [];
      boxes.forEach((b, i) => {
        const box = fromGemini(b);
        if (box) {
          overlays.push({
            id: `product-${i}`,
            box,
            tone: status,
            label: boxes.length > 1 ? `Product ${i + 1}` : "Product",
          });
        }
      });
      quote = evidenceText;
      break;
    }
    case "comp.scale_sanity": {
      const box = fromGemini(data?.ad_box);
      const area = typeof data?.area === "number" ? data.area : null;
      if (box) {
        overlays.push({
          id: "product",
          box,
          tone: status,
          label: area != null ? `${Math.round(area * 100)}% of image` : "Product",
        });
      }
      quote = evidenceText;
      question = null;
      break;
    }
    default: {
      if (check.dimension === "composition") {
        // The judge looked at the whole ad; the product box shows what it judged.
        const box = productBox(all);
        if (box) overlays.push({ id: "product", box, tone: status, label: "Product" });
        if (check.check_name === "comp.realistic_scale") {
          const named = opts.scaleReferences?.filter((r) => r.trim()) ?? [];
          const refs = named.length ? [...named] : referencesFromQuestion(question);
          references = refs.length ? refs : null;
        }
        quote = evidenceText && evidenceText !== "(fake)" ? evidenceText : null;
        question = null;
        break;
      }
      if (check.dimension === "product") {
        // Product rubric (logo, shape, colours…): the judge looked at the located product.
        const box = productBox(all);
        if (box) overlays.push({ id: "product", box, tone: status, label: checkLabel(check.check_name) });
      } else if (check.dimension === "text") {
        addZone(rec(ocrCheck(all)?.evidence_data));
      }
      quote = evidenceText && evidenceText !== "(fake)" ? evidenceText : null;
      if (check.dimension !== "context") question = null;
    }
  }

  return {
    checkId: checkId(check),
    title: checkLabel(check.check_name),
    tone: status,
    overlays,
    textDiff,
    colours,
    quote,
    question,
    references,
    inherited: data?.inherited === true,
    wholeImage: overlays.length === 0 && !textDiff && !quote && !question && !references,
  };
}

// ---------------------------------------------------------------------------------------------
// Character diff (TextDiff)

export type DiffOp =
  | { op: "equal"; expected: string; read: string }
  | { op: "replace"; expected: string; read: string }
  | { op: "delete"; expected: string }
  | { op: "insert"; read: string };

const MAX_DIFF = 240;

/**
 * Character-level alignment of `expected` against `read` (Levenshtein, unit costs). Adjacent
 * operations of the same kind are merged, so "Summer" vs "Summr" is equal «Summ», delete «e»,
 * equal «r».
 */
export function diffChars(expected: string, read: string): DiffOp[] {
  const a = Array.from(expected.slice(0, MAX_DIFF));
  const b = Array.from(read.slice(0, MAX_DIFF));
  const n = a.length;
  const m = b.length;
  const d: number[][] = Array.from({ length: n + 1 }, (_, i) => {
    const row = new Array<number>(m + 1).fill(0);
    row[0] = i;
    return row;
  });
  for (let j = 0; j <= m; j++) d[0]![j] = j;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i]![j] = Math.min(d[i - 1]![j]! + 1, d[i]![j - 1]! + 1, d[i - 1]![j - 1]! + cost);
    }
  }
  const raw: DiffOp[] = [];
  let i = n;
  let j = m;
  while (i > 0 || j > 0) {
    const here = d[i]![j]!;
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1] && here === d[i - 1]![j - 1]!) {
      raw.push({ op: "equal", expected: a[i - 1]!, read: b[j - 1]! });
      i--;
      j--;
    } else if (i > 0 && j > 0 && here === d[i - 1]![j - 1]! + 1) {
      raw.push({ op: "replace", expected: a[i - 1]!, read: b[j - 1]! });
      i--;
      j--;
    } else if (i > 0 && here === d[i - 1]![j]! + 1) {
      raw.push({ op: "delete", expected: a[i - 1]! });
      i--;
    } else {
      raw.push({ op: "insert", read: b[j - 1]! });
      j--;
    }
  }
  raw.reverse();
  const merged: DiffOp[] = [];
  for (const op of raw) {
    const last = merged[merged.length - 1];
    if (last && last.op === op.op) {
      if ("expected" in last && "expected" in op) last.expected += op.expected;
      if ("read" in last && "read" in op) last.read += op.read;
    } else {
      merged.push({ ...op });
    }
  }
  return merged;
}

/** One sentence for screen readers: "Missing 'e'; 'OF' instead of 'OFF'." */
export function describeDiff(ops: DiffOp[]): string {
  const parts: string[] = [];
  for (const op of ops) {
    if (op.op === "delete") parts.push(`missing '${op.expected}'`);
    else if (op.op === "insert") parts.push(`extra '${op.read}'`);
    else if (op.op === "replace") parts.push(`'${op.read}' instead of '${op.expected}'`);
  }
  if (!parts.length) return "Exact match.";
  const s = parts.join("; ");
  return `${s.charAt(0).toUpperCase()}${s.slice(1)}.`;
}
