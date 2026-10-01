import { describe, expect, it } from "vitest";

import { measureRequiredText, requiredTextError } from "@/components/studio/required-text-field";

import { briefErrorsFromProblem, isProblem } from "./problem";

describe("briefErrorsFromProblem", () => {
  it("puts domain errors next to their field", () => {
    expect(
      briefErrorsFromProblem({
        type: "unknown-season",
        title: "Unknown season",
        status: 422,
        detail: "We couldn't read 'Atlantis' as a season.",
      }),
    ).toEqual({ season: "We couldn't read 'Atlantis' as a season." });
    expect(
      briefErrorsFromProblem({ type: "invalid-text", title: "Invalid required text", status: 422, detail: "" }),
    ).toEqual({ required_text: "Invalid required text" });
    expect(
      briefErrorsFromProblem({ type: "unknown-geography", title: "Unknown geography", status: 422, detail: "x" }),
    ).toEqual({ geography_code: "x" });
  });

  it("maps request validation errors by loc and keeps the rest on the form", () => {
    expect(
      briefErrorsFromProblem({
        type: "validation-error",
        title: "Invalid request",
        status: 422,
        errors: [{ loc: ["body", "season"], msg: "String should have at most 40 characters" }],
      }),
    ).toEqual({ season: "String should have at most 40 characters" });
    expect(
      briefErrorsFromProblem({ type: "run-queue-full", title: "Busy", status: 429, detail: "Try again in 5 s." }),
    ).toEqual({ form: "Try again in 5 s." });
  });

  it("recognises problem bodies", () => {
    expect(isProblem({ type: "x", title: "y", status: 400 })).toBe(true);
    expect(isProblem("nope")).toBe(false);
  });
});

describe("required text rules", () => {
  it("counts like the API: NFC, trimmed, code points and lines", () => {
    expect(measureRequiredText("  Summer Sale — 30% OFF \n")).toEqual({ chars: 21, lines: 1 });
    expect(measureRequiredText("a\r\nb\nc")).toEqual({ chars: 5, lines: 3 });
    expect(requiredTextError("x".repeat(81))).toMatch(/under 80 characters.*\(now 81\)/);
    expect(requiredTextError("a\nb\nc\nd")).toMatch(/at most 3 lines/);
    expect(requiredTextError(`Sale${String.fromCodePoint(0x200b)}`)).toMatch(/invisible/);
    expect(requiredTextError("Summer Sale — 30% OFF")).toBeNull();
  });
});
