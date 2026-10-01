/** ADR-007: the composition dimension in the run views (dots, scorecard, evidence, plan, repair). */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { dimensionSummary } from "@/components/run/approval-card";
import { EvidenceImage } from "@/components/run/evidence-image";
import { PlanSpecCard, TextZoneDiagram } from "@/components/run/plan-spec-card";
import { RepairCard } from "@/components/run/repair-fallback-cards";
import { DimensionDots, Scorecard } from "@/components/run/scorecard";
import type { SpecSummary } from "@/lib/api";
import { measured } from "@/lib/checks";
import { evidenceFor, referencesFromQuestion } from "@/lib/evidence";
import { applyRunEvent, emptyRunState, planExtras, planFromSpec, type CandidateState } from "@/lib/run-events";
import { deriveTimeline } from "@/lib/run-timeline";
import { CHECKS, COMPOSITION_CHECKS, COMPOSITION_REPAIR, PRODUCT_SCALE, RUN_ID } from "@/test/fixtures/run-events";

afterEach(cleanup);

const [sanity, scale, integration] = COMPOSITION_CHECKS as [
  (typeof COMPOSITION_CHECKS)[number],
  (typeof COMPOSITION_CHECKS)[number],
  (typeof COMPOSITION_CHECKS)[number],
];
const SIZE = { width: 800, height: 1000 };

describe("DimensionDots with composition", () => {
  it("lists composition as the fourth dimension with its state", () => {
    render(<DimensionDots verdicts={{ text: "pass", product: "pass", context: "pass", composition: "fail" }} />);
    const list = screen.getByRole("list", { name: "Dimensions" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.getAttribute("data-dimension"))).toEqual(["text", "product", "context", "composition"]);
    expect(list).toHaveTextContent("Composition: fail");
    expect(items[3]).toHaveAttribute("title", "Composition — realistic scale and natural integration: fail");
  });

  it("shows 'Not checked' in neutral styling for an older evaluation without composition", () => {
    render(<DimensionDots verdicts={{ text: "pass", product: "pass", context: "pass" }} />);
    const comp = screen.getByRole("list", { name: "Dimensions" }).querySelector("[data-dimension=composition]")!;
    expect(comp).toHaveAttribute("data-status", "not_checked");
    expect(comp).toHaveTextContent(/^Composition\s·\snot checked$/);
    expect(comp).toHaveClass("text-muted-foreground");
    expect(comp.querySelector(".text-red-700")).toBeNull();
  });

  it("says 'not checked yet' (not 'Not checked') before any evaluation or when technical failed", () => {
    render(<DimensionDots verdicts={{ technical: "fail" }} />);
    const comp = screen.getByRole("list", { name: "Dimensions" }).querySelector("[data-dimension=composition]")!;
    expect(comp).toHaveAttribute("data-status", "pending");
    expect(comp).toHaveTextContent("Composition: not checked yet");
  });
});

describe("Scorecard with composition", () => {
  it("renders composition checks with labels, the dimension and the scale-sanity measurement", () => {
    render(<Scorecard checks={[...CHECKS, ...COMPOSITION_CHECKS]} dimensions={{ composition: { passed: false } }} />);
    const card = screen.getByLabelText("Scorecard");
    const scaleRow = card.querySelector("li[data-check='comp.realistic_scale']")!;
    expect(scaleRow).toHaveAttribute("data-status", "fail");
    expect(scaleRow).toHaveTextContent("Realistic scale");
    expect(scaleRow).toHaveTextContent("Composition");
    expect(scaleRow).toHaveTextContent("gigantic on the floor");
    const sanityRow = card.querySelector("li[data-check='comp.scale_sanity']")!;
    expect(sanityRow).toHaveTextContent("Product size vs expected");
    expect(sanityRow).toHaveTextContent("area 30% (expected 5%–20%)");
    expect(sanityRow).toHaveTextContent("Note (not a failure)");
    expect(card.querySelector("[data-status=not_checked]")).toBeNull();
  });

  it("adds a neutral 'Not checked' composition row to an older evaluation", () => {
    render(
      <Scorecard
        checks={CHECKS}
        dimensions={{ text: { passed: false }, product: { passed: true }, context: { passed: null } }}
      />,
    );
    const row = screen.getByLabelText("Scorecard").querySelector("[data-status=not_checked]")!;
    expect(row).toHaveAttribute("data-dimension", "composition");
    expect(row).toHaveTextContent("Not checked");
    expect(row).toHaveTextContent("Composition — realistic scale and natural integration");
    expect(row.className).not.toMatch(/red/);
  });

  it("adds the same row to the per-dimension fallback (SSE summary only)", () => {
    render(<Scorecard checks={null} dimensions={{ text: { passed: true }, product: { passed: true }, context: { passed: true } }} />);
    expect(screen.getByLabelText("Scorecard")).toHaveTextContent("Not checked");
  });

  it("formats scale sanity as area vs the expected range, and nothing when not applicable", () => {
    expect(measured(sanity)).toBe("area 30% (expected 5%–20%)");
    expect(measured({ ...sanity, value: null, evidence_data: { applicable: false } })).toBeNull();
  });
});

describe("composition evidence", () => {
  it("realistic scale: the product box, the judge's words and the named reference objects", () => {
    const ev = evidenceFor(scale, COMPOSITION_CHECKS, SIZE, null);
    expect(ev.title).toBe("Realistic scale");
    expect(ev.tone).toBe("fail");
    expect(ev.overlays).toHaveLength(1);
    expect(ev.overlays[0]).toMatchObject({ id: "product", tone: "fail", label: "Product" });
    const box = ev.overlays[0]!.box;
    expect([box.x, box.y, box.w, box.h].map((v) => Number(v.toFixed(3)))).toEqual([0.25, 0.3, 0.5, 0.6]);
    expect(ev.quote).toMatch(/gigantic on the floor/);
    expect(ev.references).toEqual(["a pair of sunglasses", "a beach towel"]);
    expect(ev.question).toBeNull();
    expect(ev.wholeImage).toBe(false);
  });

  it("prefers the plan's scale references over the question's", () => {
    const ev = evidenceFor(scale, COMPOSITION_CHECKS, SIZE, null, { scaleReferences: ["a café chair"] });
    expect(ev.references).toEqual(["a café chair"]);
  });

  it("natural integration: the product box and the judge's evidence, no references", () => {
    const ev = evidenceFor(integration, COMPOSITION_CHECKS, SIZE, null);
    expect(ev.overlays.map((o) => o.id)).toEqual(["product"]);
    expect(ev.quote).toMatch(/contact shadow/);
    expect(ev.references).toBeNull();
  });

  it("scale sanity: the measured box with its area", () => {
    const ev = evidenceFor(sanity, COMPOSITION_CHECKS, SIZE, null);
    expect(ev.overlays[0]).toMatchObject({ id: "product", tone: "pass", label: "30% of image" });
  });

  it("reads references from the question and draws them in the caption", () => {
    expect(referencesFromQuestion(scale.evidence_data!.question as string)).toEqual([
      "a pair of sunglasses",
      "a beach towel",
    ]);
    expect(referencesFromQuestion("No list here.")).toEqual([]);
    render(<EvidenceImage src="/x.png" width={800} height={1000} alt="ad" evidence={evidenceFor(scale, COMPOSITION_CHECKS, SIZE, null)} />);
    expect(screen.getByText("Compared with: a pair of sunglasses, a beach towel")).toBeInTheDocument();
  });
});

const PLAN: SpecSummary = {
  source: "planner",
  effective_season: "summer",
  text_zone: { x0: 0.06, y0: 0.04, x1: 0.94, y1: 0.24 },
  product_scale: PRODUCT_SCALE,
};
const EXTRAS = { ...planExtras(null), productAnchor: "lower_center", productScale: 0.24 };

describe("PlanSpecCard product scale", () => {
  it("shows framing, the real size, the scale range, the resting surface and the references", () => {
    render(<PlanSpecCard plan={PLAN} extras={EXTRAS} aspect="4:5" />);
    expect(screen.getByText("Framing").nextSibling).toHaveTextContent("Close-up (tabletop");
    expect(screen.getByText("Product size").nextSibling).toHaveTextContent("about 15 cm at its largest (small)");
    expect(screen.getByText("Product scale").nextSibling).toHaveTextContent(
      "18–30% of the image height · rests on a beach towel",
    );
    const refs = screen.getByText("Sized next to").nextSibling as HTMLElement;
    expect(within(refs).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "a pair of sunglasses",
      "a beach towel",
    ]);
  });

  it("draws the product-scale band in the text-zone diagram", () => {
    const { container } = render(
      <TextZoneDiagram aspect="4:5" zone={PLAN.text_zone!} scaleBand={{ anchor: "lower_center", min: 0.18, max: 0.3 }} />,
    );
    const band = container.querySelector("[data-slot=scale-band]")!;
    const [outer, inner] = band.querySelectorAll("rect");
    expect(Number(outer!.getAttribute("height"))).toBeCloseTo(30);
    expect(Number(inner!.getAttribute("height"))).toBeCloseTo(18);
    expect(screen.getByText("Product band 18–30% of height")).toBeInTheDocument();
  });

  it("leaves the rows out for an older plan without a product scale", () => {
    render(<PlanSpecCard plan={{ ...PLAN, product_scale: null }} extras={EXTRAS} aspect="4:5" />);
    expect(screen.queryByText("Framing")).toBeNull();
    expect(screen.queryByText(/Product band/)).toBeNull();
  });

  it("rebuilds the product scale from a stored cs-2 spec, and null from an older spec", () => {
    const comp = {
      aspect_ratio: "4:5",
      product_anchor: "lower_center",
      product_scale: 0.24,
      ...PRODUCT_SCALE,
      size_source: "profile",
    };
    expect(planFromSpec({ composition: comp })!.product_scale).toEqual(PRODUCT_SCALE);
    expect(planFromSpec({ composition: { aspect_ratio: "4:5", product_anchor: "center", product_scale: 0.5 } })!.product_scale).toBeNull();
  });
});

describe("composition repair", () => {
  const state = COMPOSITION_REPAIR.reduce(applyRunEvent, emptyRunState(RUN_ID));

  it("keeps the routing action and names the step after composition", () => {
    expect(state.plan?.product_scale).toEqual(PRODUCT_SCALE);
    expect(state.repairs[0]).toMatchObject({ targetDimension: "composition", action: "repair_composition" });
    const repair = deriveTimeline(state).find((s) => s.kind === "repair")!;
    expect(repair.title).toBe("Repair A · Composition");
  });

  it("RepairCard explains a composition repair", () => {
    render(<RepairCard fromLabel="A" targetDimension={null} action="repair_composition" instruction="Resize the product." model="gemini-flash-image" />);
    const card = document.querySelector("[data-slot=repair-card]")!;
    expect(card).toHaveAttribute("data-action", "repair_composition");
    expect(card).toHaveTextContent("Repairing candidate A · fix Composition only");
    expect(card).toHaveTextContent("Composition repair:");
    expect(card).toHaveTextContent(/realistic scale .* contact shadow/);
  });

  it("the approval summary lists composition", () => {
    const c = state.candidates.find((x) => x.verdict === "pass") as CandidateState;
    expect(dimensionSummary(c)).toBe("Text ✓ Product ✓ Context ✓ Composition ✓");
  });
});
