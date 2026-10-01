import { describe, expect, it } from "vitest";

import { describeDiff, diffChars, evidenceFor, fromGemini, fromPixelCorners, quoted } from "@/lib/evidence";
import { EVIDENCE_CHECKS, EVIDENCE_SIZE } from "@/test/fixtures/run-events";

const REQUIRED = "Summer Sale — 30% OFF";
const byName = (name: string) => EVIDENCE_CHECKS.find((c) => c.check_name === name)!;

describe("diffChars", () => {
  it("aligns a dropped character and a truncated word", () => {
    const ops = diffChars("Summer Sale — 30% OFF", "Summr Sale — 30% OF");
    expect(ops.filter((o) => o.op !== "equal")).toEqual([
      { op: "delete", expected: "e" },
      { op: "delete", expected: "F" },
    ]);
    expect(describeDiff(ops)).toBe("Missing 'e'; missing 'F'.");
  });

  it("reports substitutions and exact matches", () => {
    expect(diffChars("30% OFF", "3O% OFF").filter((o) => o.op === "replace")).toEqual([
      { op: "replace", expected: "0", read: "O" },
    ]);
    expect(describeDiff(diffChars("OFF", "OFF"))).toBe("Exact match.");
  });
});

describe("quoted", () => {
  it("keeps guillemets the OCR read inside the rendered span", () => {
    const ev =
      "Rendered ««Summer Sale — 30% OFF»»; required «Summer Sale — 30% OFF». Not in the required text: '«', '»'.";
    expect(quoted(ev)).toEqual(["«Summer Sale — 30% OFF»", "Summer Sale — 30% OFF"]);
    expect(quoted("Rendered «Summr Sale»; required «Summer Sale».")).toEqual(["Summr Sale", "Summer Sale"]);
    expect(quoted("Vision read-back saw «SALE» in the headline zone.")).toEqual(["SALE"]);
  });
});

describe("box conversions", () => {
  it("normalises Gemini [ymin, xmin, ymax, xmax] / 1000 and pixel corners", () => {
    const box = fromGemini([440, 201, 940, 798])!;
    expect(box.x).toBeCloseTo(0.201);
    expect(box.y).toBeCloseTo(0.44);
    expect(box.w).toBeCloseTo(0.597);
    expect(box.h).toBeCloseTo(0.5);
    const zone = fromPixelCorners([8, 0, 817, 276], EVIDENCE_SIZE)!;
    expect(zone.x).toBeCloseTo(8 / 825);
    expect(zone.h).toBeCloseTo(276 / 1024);
  });
});

describe("evidenceFor", () => {
  it("OCR: text zone, one box per word, misread words flagged, and a TextDiff", () => {
    const ev = evidenceFor(byName("ocr_cer"), EVIDENCE_CHECKS, EVIDENCE_SIZE, REQUIRED);
    expect(ev.overlays.map((o) => o.id)).toEqual(["zone", "word-0", "word-1", "word-2", "word-3", "word-4"]);
    const flagged = ev.overlays.filter((o) => o.tone === "fail").map((o) => o.label);
    expect(flagged).toEqual(["Summr", "OF"]);
    const summr = ev.overlays.find((o) => o.label === "Summr")!;
    expect(summr.box.x).toBeCloseTo(165 / 825);
    expect(summr.box.w).toBeCloseTo(176 / 825);
    expect(ev.textDiff).toEqual({ expected: REQUIRED, read: "Summr Sale — 30% OF", engine: "tesseract-5.5.3" });
  });

  it("critical tokens borrow the OCR words when they carry no boxes", () => {
    const ev = evidenceFor(byName("critical_tokens"), EVIDENCE_CHECKS, EVIDENCE_SIZE, REQUIRED);
    expect(ev.overlays.some((o) => o.label === "OF")).toBe(true);
    // Stored casefolded ('off'), shown as the brief wrote it.
    expect(ev.quote).toBe("Missing or altered: 'OFF'");
  });

  it("colour: the product box labelled with ΔE, plus swatches", () => {
    const ev = evidenceFor(byName("color_delta_e"), EVIDENCE_CHECKS, EVIDENCE_SIZE, REQUIRED);
    expect(ev.overlays).toHaveLength(1);
    expect(ev.overlays[0]).toMatchObject({ id: "product", tone: "fail", label: "ΔE 41.8" });
    expect(ev.colours?.ad[1]).toBe("#1FA3A0");
  });

  it("context: no region, the judge's quote and question", () => {
    const ev = evidenceFor(byName("ctx.no_season_contradiction"), EVIDENCE_CHECKS, EVIDENCE_SIZE, REQUIRED);
    expect(ev.overlays).toEqual([]);
    expect(ev.quote).toBe("Snow covers the ground and people wear coats.");
    expect(ev.question).toMatch(/contradicting summer/);
  });

  it("technical checks are whole-image", () => {
    expect(evidenceFor(byName("resolution"), EVIDENCE_CHECKS, EVIDENCE_SIZE, REQUIRED).wholeImage).toBe(true);
  });
});
