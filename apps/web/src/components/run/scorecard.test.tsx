import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CheckRow, DimensionDots, Scorecard } from "@/components/run/scorecard";
import { CHECKS } from "@/test/fixtures/run-events";

type Check = (typeof CHECKS)[number];
const [vlmUnsure, ocrFail, , deltaEPass] = CHECKS as [Check, Check, Check, Check];

afterEach(cleanup);

function row(check: Check) {
  const { container } = render(
    <ul>
      <CheckRow check={check} />
    </ul>,
  );
  return container.querySelector("li")!;
}

describe("CheckRow", () => {
  it("renders a pass with the measured value against its threshold", () => {
    const li = row(deltaEPass);
    expect(li).toHaveAttribute("data-status", "pass");
    expect(within(li).getByText("Pass")).toBeInTheDocument();
    expect(within(li).getByText("Product colour")).toBeInTheDocument();
    expect(within(li).getByText("ΔE 3.1 ≤ 10")).toBeInTheDocument();
    expect(within(li).getByText("deterministic")).toBeInTheDocument();
  });

  it("renders a fail with its evidence", () => {
    const li = row(ocrFail);
    expect(li).toHaveAttribute("data-status", "fail");
    expect(within(li).getByText("Fail")).toBeInTheDocument();
    expect(within(li).getByText("CER 0.05 > 0.02")).toBeInTheDocument();
    expect(within(li).getByText(/Read 'Summr Sale/)).toBeInTheDocument();
    expect(li.className).toMatch(/red-700/);
  });

  it("renders unverified as neutral 'Unsure' with a dashed border, never red", () => {
    const li = row(vlmUnsure);
    expect(li).toHaveAttribute("data-status", "unverified");
    expect(within(li).getByText("Unsure")).toBeInTheDocument();
    expect(within(li).getByText("No evidence returned. Treated as unsure.")).toBeInTheDocument();
    expect(within(li).getByText("vision judge")).toBeInTheDocument();
    expect(li.className).toMatch(/border-dashed/);
    expect(li.className).not.toMatch(/red/);
  });
});

describe("Scorecard", () => {
  it("lists deterministic checks first, then vision-judge checks", () => {
    const { container } = render(<Scorecard checks={CHECKS} />);
    const order = [...container.querySelectorAll("li[data-check]")].map((li) => li.getAttribute("data-check"));
    // Within each group, failing/unsure rows lead and passes fold into "N more passed".
    expect(order).toEqual(["ocr_cer", "resolution", "color_delta_e", "context_judge"]);
    expect(screen.getByText("2 more passed")).toBeInTheDocument();
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["Deterministic checks", "Vision judge"]);
  });

  it("falls back to dimension rows when only the streamed summary is known", () => {
    render(
      <Scorecard
        checks={null}
        dimensions={{ context: { passed: false, reasons: ["Snow on the ground."] }, text: { passed: true } }}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Text");
    expect(rows[0]).toHaveTextContent("Pass");
    expect(screen.getByText("Snow on the ground.")).toBeInTheDocument();
  });

  it("shows skeleton rows while checking", () => {
    render(<Scorecard checks={null} pending />);
    expect(screen.getByLabelText("Checking")).toBeInTheDocument();
  });
});

describe("DimensionDots", () => {
  it("names every dimension with its state for screen readers", () => {
    render(<DimensionDots verdicts={{ text: "pass", product: "fail", context: "unverified" }} />);
    const list = screen.getByRole("list", { name: "Dimensions" });
    expect(list).toHaveTextContent("Text: pass");
    expect(list).toHaveTextContent("Product: fail");
    expect(list).toHaveTextContent("Context: unsure");
  });
});
