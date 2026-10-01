import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CandidateEvidence } from "@/components/run/candidate-evidence";
import { EvidenceImage } from "@/components/run/evidence-image";
import { TextDiff } from "@/components/run/text-diff";
import { evidenceFor } from "@/lib/evidence";
import type { CandidateState } from "@/lib/run-events";
import { CAND_A, EVIDENCE_CHECKS, EVIDENCE_SIZE } from "@/test/fixtures/run-events";

afterEach(cleanup);

const REQUIRED = "Summer Sale — 30% OFF";
const ocr = EVIDENCE_CHECKS.find((c) => c.check_name === "ocr_cer")!;

const candidate: CandidateState = {
  id: CAND_A,
  slot: 0,
  attempt: 1,
  kind: "initial",
  parentId: null,
  model: "gemini-flash-lite-image",
  requestedModel: "gemini-flash-lite-image",
  imageUrl: "/v1/images/img-a",
  width: EVIDENCE_SIZE.width,
  height: EVIDENCE_SIZE.height,
  cached: false,
  status: "failed",
  reason: null,
  verdict: "fail",
  dimensions: {
    text: { passed: false, reasons: [] },
    product: { passed: false, reasons: [] },
    context: { passed: false, reasons: [] },
  },
  checks: EVIDENCE_CHECKS,
  repairInstruction: null,
  createdAt: null,
  evaluatedAt: null,
};

function overlays(container: HTMLElement) {
  return [...container.querySelectorAll("[data-overlay]")];
}

describe("EvidenceImage", () => {
  it("draws the check's boxes at their normalised position, with chips on misread words", () => {
    const evidence = evidenceFor(ocr, EVIDENCE_CHECKS, EVIDENCE_SIZE, REQUIRED);
    const { container } = render(
      <EvidenceImage src="/a.png" width={825} height={1024} alt="Candidate A" evidence={evidence} />,
    );
    const boxes = overlays(container);
    expect(boxes).toHaveLength(6);
    const summr = container.querySelector<HTMLElement>('[data-overlay="word-0"]')!;
    expect(summr.dataset.tone).toBe("fail");
    expect(parseFloat(summr.style.left)).toBeCloseTo((165 / 825) * 100, 2);
    expect(parseFloat(summr.style.width)).toBeCloseTo((176 / 825) * 100, 2);
    expect(summr).toHaveTextContent("Summr");
    // The overlay layer is decorative; the image keeps its alt text.
    expect(container.querySelector("[data-slot=evidence-overlays]")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByAltText("Candidate A")).toBeInTheDocument();
    expect(container.querySelector("[data-slot=text-diff]")).not.toBeNull();
  });

  it("draws nothing without an active check", () => {
    const { container } = render(<EvidenceImage src="/a.png" width={825} height={1024} alt="Candidate A" />);
    expect(overlays(container)).toHaveLength(0);
    expect(container.querySelector("[data-slot=evidence-caption]")).toBeNull();
  });
});

describe("CandidateEvidence", () => {
  it("shows a check's evidence on focus and hides it on blur", () => {
    const { container } = render(<CandidateEvidence candidate={candidate} label="A" requiredText={REQUIRED} />);
    const row = container.querySelector<HTMLElement>('li[data-check="ocr_cer"]')!;
    expect(overlays(container)).toHaveLength(0);

    act(() => row.focus());
    expect(container.querySelector("[data-slot=evidence-image]")).toHaveAttribute("data-evidence", "text:ocr_cer");
    expect(overlays(container).length).toBeGreaterThan(0);

    act(() => row.blur());
    expect(overlays(container)).toHaveLength(0);
    expect(container.querySelector("[data-slot=evidence-image]")).not.toHaveAttribute("data-evidence");
  });

  it("follows hover, pins on Enter and moves with the arrow keys", () => {
    const { container } = render(<CandidateEvidence candidate={candidate} label="A" requiredText={REQUIRED} />);
    const colour = container.querySelector<HTMLElement>('li[data-check="color_delta_e"]')!;
    fireEvent.mouseEnter(colour);
    expect(container.querySelector('[data-overlay="product"]')).toHaveTextContent("ΔE 41.8");
    fireEvent.mouseLeave(colour);
    expect(overlays(container)).toHaveLength(0);

    const ocrRow = container.querySelector<HTMLElement>('li[data-check="ocr_cer"]')!;
    act(() => ocrRow.focus());
    fireEvent.keyDown(ocrRow, { key: "Enter" });
    expect(ocrRow).toHaveAttribute("data-pinned", "true");
    act(() => ocrRow.blur());
    // Pinned evidence stays after focus leaves.
    expect(container.querySelector("[data-slot=evidence-image]")).toHaveAttribute("data-evidence", "text:ocr_cer");

    act(() => ocrRow.focus());
    fireEvent.keyDown(ocrRow, { key: "ArrowDown" });
    expect(document.activeElement).not.toBe(ocrRow);
    expect(document.activeElement).toHaveAttribute("data-check");
  });

  it("shows the vision judge's quote for a context check (no region)", () => {
    const { container } = render(<CandidateEvidence candidate={candidate} label="A" requiredText={REQUIRED} />);
    fireEvent.mouseEnter(container.querySelector<HTMLElement>('li[data-check="ctx.no_season_contradiction"]')!);
    const caption = container.querySelector("[data-slot=evidence-caption]")!;
    expect(caption).toHaveTextContent("Snow covers the ground and people wear coats.");
    expect(caption).toHaveTextContent(/Asked: Does the scene contain/);
  });
});

describe("TextDiff", () => {
  it("highlights exactly the characters that differ", () => {
    const { container } = render(<TextDiff expected={REQUIRED} read="Summr Sale — 30% OF" engine="tesseract" />);
    const marks = [...container.querySelectorAll("mark")].map((m) => [m.dataset.diff, m.textContent]);
    expect(marks).toEqual([
      ["delete", "e"],
      ["delete", "F"],
      ["gap", "‸"],
      ["gap", "‸"],
    ]);
    expect(screen.getByText("Missing 'e'; missing 'F'.")).toBeInTheDocument();
  });

  it("marks substituted characters on both lines", () => {
    const { container } = render(<TextDiff expected="30% OFF" read="3O% OFF" />);
    const marks = [...container.querySelectorAll("mark")].map((m) => [m.dataset.diff, m.textContent]);
    expect(marks).toEqual([
      ["replace", "0"],
      ["replace", "O"],
    ]);
  });
});
