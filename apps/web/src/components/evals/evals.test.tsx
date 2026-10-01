import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgreementPanel } from "@/components/evals/agreement-panel";
import { CostLatencyPanel } from "@/components/evals/cost-latency-panel";
import { ReportWarnings } from "@/components/evals/report-warnings";
import { TestResultsCard } from "@/components/evals/test-results-card";
import type { EvalSummary } from "@/lib/api";
import { EvalDashboard } from "@/components/evals/eval-dashboard";
import { EvalScoreboard } from "@/components/evals/eval-scoreboard";
import { PlantedFailureTable } from "@/components/evals/planted-failure-table";
import { PrecisionRecallTable } from "@/components/evals/precision-recall-table";
import { ReportProvenance } from "@/components/evals/report-provenance";
import { fmtPct, fmtRatio, targetNumber } from "@/lib/evals";
import { NATURAL_ITEMS, PLANTED_ITEMS, SUMMARY, SUMMARY_LABELLED } from "@/test/fixtures/evals";

const api = vi.hoisted(() => ({ evalsEvalSummary: vi.fn(), evalsEvalItems: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("formatting", () => {
  it("formats ratios and percents without inventing precision", () => {
    expect(fmtRatio(0.8461538461538461)).toBe("0.846");
    expect(fmtRatio(null)).toBe("—");
    expect(fmtPct(0.85)).toBe("85%");
    expect(fmtPct(0.9473684210526315)).toBe("94.7%");
    expect(targetNumber(">= 90%")).toBe(0.9);
    expect(targetNumber("<= $0.25")).toBe(0.25);
    expect(targetNumber("<= 60 s")).toBe(60);
    expect(targetNumber("report")).toBeNull();
  });
});

describe("EvalScoreboard", () => {
  it("shows met / not met exactly as the report says", () => {
    render(<EvalScoreboard summary={SUMMARY} />);
    const met = (id: string) =>
      document.querySelector(`[data-metric="${id}"] [data-met]`)?.getAttribute("data-met");
    expect(met("recall.text")).toBe("met");
    expect(met("recall.product")).toBe("not_met");
    expect(met("precision.text")).toBe("not_met"); // 0.846 < 0.85
    expect(met("precision.product")).toBe("met");
    expect(met("precision.context")).toBe("not_measured"); // null precision, met: null
    // Technical has no criterion in the report: reported, not judged.
    expect(met("recall.technical")).toBe("reported");

    const text = document.querySelector('[data-metric="precision.text"]')!;
    expect(within(text as HTMLElement).getByText("0.846")).toHaveAttribute("title", "0.8461538461538461");
    expect(within(text as HTMLElement).getByText("target ≥ 85%")).toBeInTheDocument();
    // 9 criteria met, 3 below target, 2 not measured (precision.context, human_agreement); the
    // first-attempt rate has no target and is not counted.
    expect(screen.getByText("9 of 14")).toBeInTheDocument();
    expect(screen.getByTitle("0.85")).toHaveTextContent("85%");
  });
});

describe("PrecisionRecallTable", () => {
  it("renders the report's P / R / F1 / n per dimension", () => {
    render(<PrecisionRecallTable summary={SUMMARY} />);
    const row = (d: string) => document.querySelector(`tr[data-dimension="${d}"]`) as HTMLElement;
    const col = (d: string, c: string) => row(d).querySelector(`[data-col="${c}"]`)?.textContent;
    expect(col("text", "precision")).toBe("0.846");
    expect(col("text", "recall")).toBe("1.000");
    expect(col("text", "f1")).toBe("0.917");
    expect(col("text", "n")).toBe("26 (11 / 15)");
    expect(col("product", "recall")).toBe("0.545");
    expect(col("context", "precision")).toBe("—");
    expect(col("context", "n")).toBe("32 (6 / 26)");
    expect(row("technical").querySelector("[data-met]")?.getAttribute("data-met")).toBe("reported");
    expect(row("product").querySelector("[data-met]")?.getAttribute("data-met")).toBe("not_met");
  });

  it("opens the confusion matrix with the positive class FAIL", async () => {
    render(<PrecisionRecallTable summary={SUMMARY} />);
    fireEvent.click(screen.getByRole("button", { name: "Text confusion matrix" }));
    const tp = await screen.findByText("Label fail · evaluator fail");
    expect(tp.parentElement).toHaveTextContent("TP11");
    expect(screen.getByText("Label pass · evaluator fail (false alarm)").parentElement).toHaveTextContent("FP2");
    expect(screen.getByText(/Positive class = FAIL/)).toBeInTheDocument();
    expect(screen.getByText(/calibration/).closest("li")).toHaveTextContent("calibration: P 0.857 · R 1.000 · n = 16");
  });
});

describe("PlantedFailureTable", () => {
  it("shows n, caught and catch rate per class, plus the known-good controls", () => {
    render(<PlantedFailureTable planted={SUMMARY.planted} knownGood={SUMMARY.known_good} items={PLANTED_ITEMS} />);
    const cells = (m: string) => {
      const r = document.querySelector(`tr[data-mutation="${m}"]`) as HTMLElement;
      return ["n", "caught", "rate"].map((c) => r.querySelector(`[data-col="${c}"]`)?.textContent);
    };
    expect(cells("text_typo")).toEqual(["6", "6", "100%"]);
    expect(cells("product_recolour")).toEqual(["4", "3", "75%missed 1"]);
    expect(cells("product_swap")).toEqual(["3", "2", "66.7%missed 1"]);
    expect(cells("context_season")).toEqual(["4", "0", "0%missed 4"]);
    const controls = document.querySelector('tr[data-mutation="control_good"]') as HTMLElement;
    expect(controls).toHaveTextContent("4 passed");
  });

  it("opens the source vs planted example with the evaluator's evidence", async () => {
    render(<PlantedFailureTable planted={SUMMARY.planted} knownGood={SUMMARY.known_good} items={PLANTED_ITEMS} />);
    const row = document.querySelector('tr[data-mutation="text_typo"]') as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "View example" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Planted failure: Typo'd text")).toBeInTheDocument();
    expect(within(dialog).getByAltText("Source ad for brief B05")).toHaveAttribute(
      "src",
      expect.stringContaining("/v1/images/img-B05-nat"),
    );
    expect(within(dialog).getByAltText(/Planted Typo'd text version/)).toBeInTheDocument();
    expect(within(dialog).getByText("Caught")).toBeInTheDocument();
    expect(within(dialog).getByText(/Oktoberfest dition/)).toBeInTheDocument();
  });

  it("marks a planted item the evaluator passed as missed", async () => {
    render(<PlantedFailureTable planted={SUMMARY.planted} items={PLANTED_ITEMS} />);
    const row = document.querySelector('tr[data-mutation="product_recolour"]') as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "View example" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Missed")).toBeInTheDocument();
  });
});

describe("AgreementPanel", () => {
  it("explains the empty state when there are no labels yet", () => {
    render(<AgreementPanel summary={SUMMARY} natural={NATURAL_ITEMS} />);
    expect(screen.getByText("No human labels yet")).toBeInTheDocument();
    expect(screen.getByText(/make golden-sheet/)).toBeInTheDocument();
  });

  it("shows agreement, kappa and the disagreements", () => {
    render(<AgreementPanel summary={SUMMARY_LABELLED} natural={NATURAL_ITEMS} />);
    expect(screen.getByText("90%")).toBeInTheDocument();
    expect(screen.getByText(/Agrees on 18 of 20 ads/)).toBeInTheDocument();
    expect(screen.getByText("0.737")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Disagreements" });
    // B02: human pass, evaluator fail on text. B05 agrees (both fail product).
    expect(within(list).getByText("B02")).toBeInTheDocument();
    expect(within(list).queryByText("B05")).toBeNull();
    expect(within(list).getByText("Differs on: Text")).toBeInTheDocument();
  });
});

/** The same report as the summary API returned it before the optional fields existed. */
function bare(summary: EvalSummary): EvalSummary {
  const out = { ...summary };
  for (const k of ["pipeline", "agreement_by_dimension", "stability", "hemisphere", "provenance", "warnings"] as const) {
    delete out[k];
  }
  return out;
}

describe("CostLatencyPanel", () => {
  it("shows mean repairs, run outcomes and judge stability from the report", () => {
    render(<CostLatencyPanel summary={SUMMARY} />);
    expect(document.querySelector('[data-stat="mean-repairs"]')).toHaveTextContent("0.10");
    expect(document.querySelector('[data-stat="flip-rate"]')).toHaveTextContent("0% (0 of 181 checks, 10 items)");
    const outcomes = document.querySelector('[data-slot="status-counts"]') as HTMLElement;
    expect(within(outcomes).getByText("Passed · 19")).toBeInTheDocument();
    expect(within(outcomes).getByText("Held for review · 1")).toBeInTheDocument();
    expect(outcomes).toHaveTextContent("20 golden runs");
  });

  it("degrades to dashes and a hint when the optional fields are missing", () => {
    render(<CostLatencyPanel summary={bare(SUMMARY)} />);
    expect(document.querySelector('[data-stat="mean-repairs"]')).toHaveTextContent("—");
    // Falls back to the top-level flip rate.
    expect(document.querySelector('[data-stat="flip-rate"]')).toHaveTextContent("0%");
    expect(screen.getByText(/Not in this report/)).toBeInTheDocument();
  });
});

describe("AgreementPanel per dimension", () => {
  it("shows agreement and κ per dimension for each labelled image set", () => {
    render(<AgreementPanel summary={SUMMARY_LABELLED} natural={NATURAL_ITEMS} />);
    const table = screen.getByRole("table", { name: "Agreement per dimension" });
    const cell = (d: string, c: string) =>
      table.querySelector(`tr[data-dimension="${d}"] [data-col="${c}"]`)?.textContent;
    expect(cell("text", "nat-agree")).toBe("95% (19/20)");
    expect(cell("text", "nat-kappa")).toBe("0.800");
    expect(cell("technical", "nat-kappa")).toBe("—");
    // The E-final set has no labels, so it has no columns.
    expect(within(table).queryByText("Shipped (E-final)")).toBeNull();
  });

  it("says so when the report has no per-dimension agreement", () => {
    render(<AgreementPanel summary={{ ...bare(SUMMARY_LABELLED) }} natural={NATURAL_ITEMS} />);
    expect(screen.getByText("Per-dimension agreement is not in this report.")).toBeInTheDocument();
  });
});

describe("ReportWarnings", () => {
  it("lists the report's warnings and drops the dry-run line when its banner is shown", () => {
    render(<ReportWarnings warnings={SUMMARY.warnings} dryRunShown />);
    const strip = screen.getByRole("note", { name: "Report warnings" });
    expect(within(strip).getByText("2 things to know about this report")).toBeInTheDocument();
    expect(within(strip).getByText(/Missing labels/)).toBeInTheDocument();
    expect(within(strip).queryByText(/FAKE DRY RUN/)).toBeNull();
  });

  it("renders nothing without warnings", () => {
    const { container } = render(<ReportWarnings warnings={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("TestResultsCard", () => {
  it("falls back to the hemisphere metric when the report has no criterion for it", () => {
    const s = { ...SUMMARY, criteria: SUMMARY.criteria.filter((c) => c.id !== "hemisphere_accuracy") };
    render(<TestResultsCard summary={s} />);
    const row = document.querySelector('[data-test="hemisphere_accuracy"]') as HTMLElement;
    expect(row).toHaveTextContent("12 of 12");
    expect(row.querySelector("[data-met]")?.getAttribute("data-met")).toBe("met");
  });
});

describe("ReportProvenance", () => {
  it("lists the judge, OCR, image models, prompts and snapshot", () => {
    render(<ReportProvenance summary={SUMMARY} />);
    const models = document.querySelector('[aria-label="Models and engines"]') as HTMLElement;
    expect(within(models).getByText("fake · test:fake-vision")).toBeInTheDocument();
    expect(within(models).getByText("tesseract 5.5.3")).toBeInTheDocument();
    expect(within(models).getByText("10 packs (ara, chi_sim, deu, eng, fra, hin, …)")).toBeInTheDocument();
    expect(within(models).getByText("local:pillow-overlay, test:fake-image")).toBeInTheDocument();
    expect(within(models).getByText("ad_inspect@1, context_judge@1, product_profile@1")).toBeInTheDocument();
    expect(within(models).getByText(/^verdicts\.json · 338 verdicts · ev-0\.3/)).toBeInTheDocument();
  });

  it("explains an older report without model provenance", () => {
    render(<ReportProvenance summary={bare(SUMMARY)} />);
    expect(screen.getByText(/predates model provenance/)).toBeInTheDocument();
  });

  it("shows the FAKE DRY RUN badge and the versions", () => {
    render(<ReportProvenance summary={SUMMARY} />);
    expect(screen.getByText("Fake dry run")).toBeInTheDocument();
    expect(screen.getByText("ev-0.3")).toBeInTheDocument();
    expect(screen.getByText("0acdb3d68e72")).toBeInTheDocument();
    expect(screen.getByText(SUMMARY.report_id)).toBeInTheDocument();
  });

  it("has no dry-run badge on a real report", () => {
    render(<ReportProvenance summary={SUMMARY_LABELLED} />);
    expect(screen.queryByText("Fake dry run")).toBeNull();
  });
});

describe("EvalDashboard", () => {
  it("explains `make eval` on 404 no-eval-report", async () => {
    api.evalsEvalSummary.mockResolvedValue({
      data: undefined,
      error: { type: "no-eval-report", title: "No eval report yet", status: 404 },
      response: { status: 404 },
    });
    render(<EvalDashboard />);
    expect(await screen.findByText("No evaluation report yet")).toBeInTheDocument();
    expect(screen.getByText(/make eval /)).toBeInTheDocument();
    expect(api.evalsEvalItems).not.toHaveBeenCalled();
  });

  it("renders every section from the summary and flags the dry run", async () => {
    api.evalsEvalSummary.mockResolvedValue({ data: SUMMARY, response: { status: 200 } });
    api.evalsEvalItems.mockImplementation(async ({ query }: { query: { origin: string } }) => ({
      data: { items: query.origin === "planted" ? PLANTED_ITEMS : NATURAL_ITEMS, next_cursor: null },
      response: { status: 200 },
    }));
    render(<EvalDashboard />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Scoreboard" })).toBeInTheDocument());
    expect(screen.getAllByText("Fake dry run").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Not a real evaluation")).toBeInTheDocument();
    expect(screen.getByRole("note", { name: "Report warnings" })).toHaveTextContent(/Missing labels/);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "View example" })[0]).toBeEnabled());
  });
});
