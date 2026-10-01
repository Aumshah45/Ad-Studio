/** ADR-007: composition in the evaluator dashboard and batch gallery, and the golden v1 → v2 views. */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdDetailSheet } from "@/components/batch/ad-detail-sheet";
import { BatchGallery } from "@/components/batch/batch-gallery";
import { AgreementByDimension } from "@/components/evals/agreement-panel";
import { EvalDashboard } from "@/components/evals/eval-dashboard";
import { EvalScoreboard } from "@/components/evals/eval-scoreboard";
import { PrecisionRecallTable } from "@/components/evals/precision-recall-table";
import { VersionComparison } from "@/components/evals/version-comparison";
import { buildAds, filterAds, ALL_FILTER } from "@/lib/batch";
import { comparisonState, mutationLabel, ppDelta } from "@/lib/evals";
import {
  COMPOSITION_ITEMS,
  NATURAL_ITEMS,
  SOURCES,
  SUMMARY,
  SUMMARY_LABELLED,
  SUMMARY_V2,
  V1_SUMMARY_ROW,
  V2_LABELLED_ROW,
  V2_SUMMARY_ROW,
} from "@/test/fixtures/evals";

const api = vi.hoisted(() => ({
  evalsEvalSummary: vi.fn(),
  evalsEvalItems: vi.fn(),
  runsListRuns: vi.fn(),
  runsGetRun: vi.fn(),
  goldenGoldenSources: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));

beforeEach(() => {
  api.goldenGoldenSources.mockResolvedValue({ data: SOURCES, response: { status: 200 } });
  api.runsListRuns.mockResolvedValue({ data: { items: [], next_cursor: null }, response: { status: 200 } });
  api.runsGetRun.mockResolvedValue({ data: undefined, error: {}, response: { status: 404 } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const NO_REPORT = {
  data: undefined,
  error: { type: "no-eval-report", title: "No eval report yet", status: 404 },
  response: { status: 404 },
};

describe("composition on the evaluator dashboard", () => {
  it("scoreboard: a composition card with its description", () => {
    render(<EvalScoreboard summary={SUMMARY_V2} />);
    const card = document.querySelector('[data-slot=eval-scoreboard] li[data-dimension="composition"]')!;
    expect(card).toHaveTextContent("Composition");
    expect(card).toHaveTextContent("realistic scale and natural integration");
    expect(card).toHaveTextContent("n = 20");
    expect(card).not.toHaveAttribute("data-status");
  });

  it("scoreboard and P/R table: 'Not checked' for a report before composition", () => {
    render(<EvalScoreboard summary={SUMMARY} />);
    const card = document.querySelector('li[data-dimension="composition"]')!;
    expect(card).toHaveAttribute("data-status", "not_checked");
    expect(card).toHaveTextContent("Not checked");
    cleanup();
    render(<PrecisionRecallTable summary={SUMMARY} />);
    const row = document.querySelector('tr[data-dimension="composition"]')!;
    expect(row).toHaveAttribute("data-status", "not_checked");
    expect(row).toHaveTextContent("Not checked");
  });

  it("P/R table: composition row with its numbers", () => {
    render(<PrecisionRecallTable summary={SUMMARY_V2} />);
    const row = document.querySelector('tr[data-dimension="composition"]') as HTMLElement;
    expect(row).toHaveTextContent("realistic scale and natural integration");
    expect(row.querySelector('[data-col="recall"]')).toHaveTextContent("1.000");
    expect(row.querySelector('[data-col="n"]')).toHaveTextContent("20 (2 / 18)");
  });

  it("agreement: composition row, 'Not checked' where the report has none", () => {
    render(<AgreementByDimension summary={SUMMARY_V2} />);
    expect(document.querySelector('tr[data-dimension="composition"] [data-col="nat-agree"]')).toHaveTextContent("100%");
    cleanup();
    // A rubric-1 report: agreement on four dimensions, no composition key.
    render(<AgreementByDimension summary={SUMMARY_LABELLED} />);
    const cell = document.querySelector('tr[data-dimension="composition"] [data-col="nat-agree"]')!;
    expect(cell).toHaveTextContent("Not checked");
    expect(cell).toHaveAttribute("colspan", "2");
  });

  it("names the planted composition classes", () => {
    expect(mutationLabel("comp_oversize")).toBe("Oversized product");
    expect(mutationLabel("comp_pasted")).toBe("Pasted product");
  });
});

describe("VersionComparison (v1 → v2)", () => {
  it("empty: no comparison yet explains how to run v2", () => {
    render(<VersionComparison comparison={null} />);
    expect(screen.getByText("No v1 → v2 comparison yet")).toBeInTheDocument();
    expect(screen.getByText(/make golden-export/)).toBeInTheDocument();
    expect(comparisonState({ versions: [V1_SUMMARY_ROW] })).toEqual({ kind: "none" });
  });

  it("empty: v2 without outputs", () => {
    const empty = { ...V2_SUMMARY_ROW, counts: { nat: 0, final: 0, plant: 0, human_labelled: 0 } };
    render(<VersionComparison comparison={{ versions: [V1_SUMMARY_ROW, empty] }} />);
    expect(document.querySelector("[data-slot=version-comparison]")).toHaveAttribute("data-state", "no-outputs");
    expect(screen.getByText("v2 has no outputs yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("v2 generated but not labelled: rates blank with a labelling note", () => {
    render(<VersionComparison comparison={{ versions: [V1_SUMMARY_ROW, V2_SUMMARY_ROW] }} />);
    expect(screen.getByText(/No human labels on v2 yet/)).toBeInTheDocument();
    const comp = document.querySelector('[data-slot=human-pass-nat] tr[data-dimension="composition"]') as HTMLElement;
    expect(comp.querySelector('[data-col="v1"]')).toHaveTextContent("55% (11/20)");
    expect(comp.querySelector('[data-col="v2"]')).toHaveTextContent("not labelled");
    expect(comp.querySelector('[data-col="change"]')).toHaveTextContent("—");
    // The evaluator's composition pass rate is there even without labels.
    expect(document.querySelector('[data-slot=composition-pass-rate] [data-version="v2"]')).toHaveTextContent(
      "evaluator 95% (19/20)",
    );
  });

  it("full: human pass rates, composition pass rate and evaluator P/R per version", () => {
    render(<VersionComparison comparison={SUMMARY_V2.comparison} />);
    expect(screen.queryByText(/No human labels on/)).toBeNull();
    const nat = document.querySelector("[data-slot=human-pass-nat]") as HTMLElement;
    const comp = nat.querySelector('tr[data-dimension="composition"]') as HTMLElement;
    expect(comp).toHaveTextContent("realistic scale and natural integration");
    expect(comp.querySelector('[data-col="v2"]')).toHaveTextContent("90% (18/20)");
    expect(comp.querySelector('[data-col="change"]')).toHaveTextContent("+35 pp");
    const rates = document.querySelector("[data-slot=composition-pass-rate]") as HTMLElement;
    expect(within(rates).getAllByText("55%").length).toBe(1);
    expect(within(rates).getAllByText("90%").length).toBe(1);
    const pr = document.querySelector('[data-slot=comparison-pr] tr[data-dimension="composition"]') as HTMLElement;
    expect(pr.querySelector('[data-col="v1"]')).toHaveTextContent("0.800 / 0.444n = 20");
    expect(pr.querySelector('[data-col="v2"]')).toHaveTextContent("1.000 / 1.000n = 20");
    expect(document.querySelector('[data-slot=comparison-pipeline] [data-version="v2"]')).toHaveTextContent(
      "First attempt 85% → after repair 100%",
    );
    expect(ppDelta({ n: 20, ok: 11, rate: 0.55 }, V2_LABELLED_ROW.human_pass_rates!.nat!.composition)).toBe("+35 pp");
  });
});

describe("golden version switcher", () => {
  it("evaluator: shows the newest report's version and reloads with ?golden_version=", async () => {
    api.evalsEvalSummary.mockImplementation(async (opts?: { query?: { golden_version?: string } }) =>
      opts?.query?.golden_version === "v1"
        ? { data: { ...SUMMARY, golden_version: "v1" }, response: { status: 200 } }
        : { data: SUMMARY_V2, response: { status: 200 } },
    );
    api.evalsEvalItems.mockResolvedValue({ data: { items: [], next_cursor: null }, response: { status: 200 } });
    render(<EvalDashboard />);
    await waitFor(() => expect(document.querySelector("[data-slot=version-comparison]")).toBeInTheDocument());
    const v2 = screen.getByRole("button", { name: /Golden v2/ });
    expect(v2).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Golden v1 → v2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Golden v1/ }));
    await waitFor(() =>
      expect(api.evalsEvalSummary).toHaveBeenCalledWith({ query: { golden_version: "v1" } }),
    );
    await waitFor(() =>
      expect(api.evalsEvalItems).toHaveBeenCalledWith({
        query: expect.objectContaining({ golden_version: "v1" }),
      }),
    );
    // v1's report has no comparison: it comes from the newest report.
    await waitFor(() => expect(document.querySelector("[data-slot=version-comparison]")).toHaveAttribute("data-state", "ok"));
    expect(window.location.search).toContain("golden_version=v1");
    window.history.replaceState(null, "", "/");
  });

  it("evaluator: a clear empty state when the chosen version has no report", async () => {
    api.evalsEvalSummary.mockResolvedValue(NO_REPORT);
    render(<EvalDashboard initialVersion="v2" />);
    expect(await screen.findByText("No v2 evaluation report yet")).toBeInTheDocument();
    expect(api.evalsEvalSummary).toHaveBeenCalledWith({ query: { golden_version: "v2" } });
  });

  it("batch: switches version and explains an empty v2", async () => {
    api.evalsEvalSummary.mockImplementation(async (opts?: { query?: { golden_version?: string } }) =>
      opts?.query?.golden_version === "v2" ? NO_REPORT : { data: { ...SUMMARY, golden_version: "v1" }, response: { status: 200 } },
    );
    api.evalsEvalItems.mockImplementation(async ({ query }: { query: { golden_version?: string } }) =>
      query.golden_version === "v2" ? NO_REPORT : { data: { items: NATURAL_ITEMS, next_cursor: null }, response: { status: 200 } },
    );
    render(<BatchGallery />);
    await screen.findByRole("list", { name: "Golden ads" });
    expect(screen.getByRole("button", { name: /Golden v1/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /Golden v2/ }));
    expect(await screen.findByText("No v2 batch outputs yet")).toBeInTheDocument();
    expect(screen.getByText(/hasn't been run and evaluated yet/)).toBeInTheDocument();
    window.history.replaceState(null, "", "/");
  });
});

describe("composition in the batch gallery", () => {
  const ads = buildAds(COMPOSITION_ITEMS, []);

  it("filters on a composition failure", () => {
    expect(ads.find((a) => a.id === "B17-final")!.failed).toEqual(["composition"]);
    expect(filterAds(ads, "final", { ...ALL_FILTER, dimension: "composition" }).map((a) => a.id)).toEqual(["B17-final"]);
  });

  it("detail sheet: composition label vs verdict, and 'Not checked' on an older item", async () => {
    render(<AdDetailSheet ad={ads.find((a) => a.id === "B17-final")!} sibling={null} summary={SUMMARY_V2} open onOpenChange={() => {}} />);
    const table = (await screen.findByText("Human labels vs evaluator")).closest("section")!;
    const row = table.querySelector('tr[data-dimension="composition"]') as HTMLElement;
    expect(row.querySelector('[data-col="evaluator"]')).toHaveTextContent("Fail");
    expect(row.querySelector('[data-col="outcome"]')).toHaveTextContent("TP · caught");
    cleanup();

    render(<AdDetailSheet ad={ads.find((a) => a.id === "B01-nat")!} sibling={null} summary={SUMMARY} open onOpenChange={() => {}} />);
    const old = (await screen.findByText("Human labels vs evaluator")).closest("section")!;
    const oldRow = old.querySelector('tr[data-dimension="composition"]') as HTMLElement;
    expect(oldRow.querySelector('[data-col="evaluator"]')).toHaveTextContent("Not checked");
    expect(oldRow.querySelector('[data-col="evaluator"]')).not.toHaveTextContent("Unsure");
    // The tile dots say the same.
    const dots = screen.getByRole("list", { name: "Dimensions" }).querySelector("[data-dimension=composition]")!;
    expect(dots).toHaveAttribute("data-status", "not_checked");
  });
});
