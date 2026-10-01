import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdDetailSheet } from "@/components/batch/ad-detail-sheet";
import { AdTile } from "@/components/batch/ad-tile";
import { BatchGallery, BatchGalleryView } from "@/components/batch/batch-gallery";
import { SourcesView } from "@/components/batch/sources-view";
import { ALL_FILTER, batchEstimate, buildAds, filterAds, filterOptions } from "@/lib/batch";
import { attemptTitle } from "@/lib/run-events";
import { resetSourcesCache } from "@/lib/sources";
import { GOLDEN_RUNS, NATURAL_ITEMS, SOURCES, SUMMARY } from "@/test/fixtures/evals";

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
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  resetSourcesCache();
});

const ADS = buildAds(NATURAL_ITEMS, GOLDEN_RUNS);
const ad = (id: string) => ADS.find((a) => a.id === id)!;

describe("buildAds", () => {
  it("takes the brief from the item and matches runs by run_id only", () => {
    expect(ad("B01-nat").brief).toEqual({
      geographyCode: "AU",
      season: "December",
      requiredText: "Summer Sale — 30% OFF",
      aspectRatio: "4:5",
      productName: "Mug with red logo panel",
    });
    expect(ad("B01-nat").runId).toBeNull();
    expect(ad("B01-nat").run).toBeNull();
    expect(ad("B02-nat").run?.id).toBe(GOLDEN_RUNS[0]!.id);
    // B05 names a run this database doesn't list: the id is kept for the sheet, no summary.
    expect(ad("B05-nat").runId).toBe("55555555-5555-5555-5555-555555555555");
    expect(ad("B05-nat").run).toBeNull();
    // A run whose golden_key names a brief but whose id no item carries is never joined.
    const stray = { ...GOLDEN_RUNS[0]!, id: "99999999-9999-9999-9999-999999999999", golden_key: "B01@x@live" };
    expect(buildAds(NATURAL_ITEMS, [stray]).find((a) => a.id === "B01-nat")!.run).toBeNull();
  });

  it("derives outcome tags and keeps the brief's own tags", () => {
    expect(ad("B02-nat").outcomeTags).toEqual(
      expect.arrayContaining(["human-labelled", "disagrees with human", "repaired", "text fallback"]),
    );
    expect(ad("B05-nat").outcomeTags).toEqual(["held-out split", "human-labelled"]);
    expect(ad("B01-nat").briefTags).toEqual(["south", "counter intuitive", "percent", "demo"]);
    expect(ad("B01-nat").tags).toEqual(expect.arrayContaining(["calibration split", "counter intuitive"]));
    expect(ad("B09-final").failed).toEqual(["context"]);
  });
});

describe("gallery filtering", () => {
  it("filters by set, verdict, failed dimension, product and tag", () => {
    const ids = (f: Partial<typeof ALL_FILTER>, set: "nat" | "final" = "nat") =>
      filterAds(ADS, set, { ...ALL_FILTER, ...f }).map((a) => a.id);
    expect(ids({})).toEqual(["B01-nat", "B02-nat", "B05-nat", "B09-nat"]);
    expect(ids({}, "final")).toEqual(["B01-final", "B02-final", "B05-final", "B09-final"]);
    expect(ids({ verdict: "fail" })).toEqual(["B02-nat", "B05-nat"]);
    expect(ids({ verdict: "unverified" })).toEqual(["B09-nat"]);
    expect(ids({ dimension: "product" })).toEqual(["B05-nat"]);
    expect(ids({ dimension: "context" }, "final")).toEqual(["B09-final"]);
    expect(ids({ product: "P1" })).toEqual(["B01-nat", "B02-nat"]);
    expect(ids({ tag: "disagrees with human" })).toEqual(["B02-nat"]);
    expect(ids({ tag: "percent" })).toEqual(["B01-nat", "B09-nat"]);
    expect(ids({ verdict: "fail", product: "P3" })).toEqual([]);
    const opts = filterOptions(ADS);
    expect(opts.products).toEqual([
      { id: "P1", name: "Mug with red logo panel" },
      { id: "P2", name: "Labelled glass bottle" },
      { id: "P3", name: "Red and white sneakers" },
    ]);
    expect(opts.outcomeTags).toContain("disagrees with human");
    expect(opts.briefTags).toContain("counter intuitive");
    expect(opts.briefTags).not.toContain("disagrees with human");
  });

  it("switches between E-final and E-nat, with no missing-brief notice", () => {
    render(<BatchGalleryView summary={SUMMARY} items={NATURAL_ITEMS} runs={[]} />);
    expect(screen.queryByText(/Brief details/)).toBeNull();
    const tiles = () => [...document.querySelectorAll("[data-ad]")].map((e) => e.getAttribute("data-ad"));
    expect(tiles()).toEqual(["B01-final", "B02-final", "B05-final", "B09-final"]);
    expect(screen.getByText("Showing 4 of 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "First attempt (E-nat)" }));
    expect(tiles()).toEqual(["B01-nat", "B02-nat", "B05-nat", "B09-nat"]);
    // Arrow keys move between tiles.
    const first = document.querySelector('[data-ad="B01-nat"]') as HTMLElement;
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(document.activeElement).toHaveAttribute("data-ad", "B02-nat");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
    expect(document.activeElement).toHaveAttribute("data-ad", "B01-nat");
  });
});

describe("batchEstimate", () => {
  it("uses the report's cost and p50, or the per-ad cap", () => {
    expect(batchEstimate(20, 0.12, 30_000)).toEqual({ usd: 2.4, usdIsCap: false, seconds: 7 * 30 });
    expect(batchEstimate(20, 0, null)).toEqual({ usd: 5, usdIsCap: true, seconds: null });
  });
});

describe("AdTile", () => {
  it("shows the image, brief id, market and season, required text and dimension dots", () => {
    const onOpen = vi.fn();
    render(<AdTile ad={ad("B02-nat")} onOpen={onOpen} />);
    const tile = screen.getByRole("button", { name: /Open B02, Canada December: fail/ });
    expect(within(tile).getByText("B02")).toBeInTheDocument();
    expect(within(tile).getByText("Canada · December")).toBeInTheDocument();
    expect(within(tile).getByText("“Winter Warmers”")).toBeInTheDocument();
    expect(within(tile).getByText("Fail")).toBeInTheDocument();
    expect(within(tile).getByText(": fail")).toBeInTheDocument(); // text dimension, screen-reader word
    expect(tile.querySelector("img")).toHaveAttribute("src", expect.stringContaining("/v1/images/img-B02-nat"));
    fireEvent.click(tile);
    expect(onOpen).toHaveBeenCalledWith("B02-nat");
  });

  it("shows the brief from the item when there is no run", () => {
    render(<AdTile ad={ad("B01-nat")} onOpen={() => {}} />);
    const tile = screen.getByRole("button", { name: /Open B01, Australia December: pass/ });
    expect(within(tile).getByText("Australia · December")).toBeInTheDocument();
    expect(within(tile).getByText("“Summer Sale — 30% OFF”")).toBeInTheDocument();
    expect(screen.queryByText(/not in this database/)).toBeNull();
  });

  it("uses a dashed neutral border for an unsure verdict", () => {
    render(<AdTile ad={ad("B09-nat")} onOpen={() => {}} />);
    const tile = screen.getByRole("button", { name: /B09.*: unsure/ });
    expect(tile.className).toContain("border-dashed");
    expect(screen.getByText("Unsure")).toBeInTheDocument();
  });
});

describe("AdDetailSheet", () => {
  it("shows human labels next to evaluator verdicts with the outcome", async () => {
    api.runsGetRun.mockResolvedValue({ data: undefined, error: {}, response: { status: 404 } });
    render(
      <AdDetailSheet ad={ad("B02-nat")} sibling={ad("B02-final")} summary={SUMMARY} open onOpenChange={() => {}} />,
    );
    const view = await screen.findByText("Human labels vs evaluator");
    const table = view.closest("section")!;
    const row = (d: string) => table.querySelector(`tr[data-dimension="${d}"]`) as HTMLElement;
    expect(within(row("text")).getByText("Pass")).toBeInTheDocument(); // human
    expect(within(row("text")).getByText("Fail")).toBeInTheDocument(); // evaluator
    expect(within(row("text")).getByText("FP · false alarm")).toBeInTheDocument();
    expect(within(row("text")).getByText("disagrees")).toBeInTheDocument();
    expect(within(row("product")).queryByText("disagrees")).toBeNull();
    expect(screen.getByText(/Label source: human/)).toBeInTheDocument();
    // Brief from the item, provenance from the run summary.
    expect(screen.getByText("Canada · December · “Winter Warmers” · 4:5")).toBeInTheDocument();
    expect(screen.getByText("$0.12")).toBeInTheDocument();
    expect(screen.getByText("38 s")).toBeInTheDocument();
    await waitFor(() => expect(api.runsGetRun).toHaveBeenCalledWith({ path: { run_id: GOLDEN_RUNS[0]!.id } }));
  });

  it("links the product photo's source and licence to the sources view", async () => {
    api.runsGetRun.mockResolvedValue({ data: undefined, error: {}, response: { status: 404 } });
    render(<AdDetailSheet ad={ad("B05-nat")} sibling={null} summary={SUMMARY} open onOpenChange={() => {}} />);
    const src = await screen.findByText(/by Alex P. Kok/);
    const cell = src.closest('[data-slot="product-source"]') as HTMLElement;
    expect(within(cell).getByRole("link", { name: "Bottle of Emros groundnuts from Nigeria.jpg" })).toHaveAttribute(
      "href",
      SOURCES.products[1]!.source_url,
    );
    expect(within(cell).getByText("CC BY-SA 4.0")).toBeInTheDocument();
    expect(within(cell).getByText("share-alike")).toBeInTheDocument();
    expect(within(cell).getByRole("link", { name: /All sources and licences/ })).toHaveAttribute(
      "href",
      "/batch/sources#source-P2",
    );
    // B05's run id comes from the item even though the runs list doesn't have it.
    await waitFor(() =>
      expect(api.runsGetRun).toHaveBeenCalledWith({ path: { run_id: "55555555-5555-5555-5555-555555555555" } }),
    );
    expect(screen.getAllByText("run not in this database").length).toBeGreaterThan(0);
  });

  it("lists every attempt from the run detail", async () => {
    api.runsGetRun.mockResolvedValue({
      data: {
        approved_candidate_id: "c-2",
        best_candidate_id: "c-2",
        candidates: [
          { id: "c-1", attempt: 0, slot: 0, kind: "initial", status: "rejected", served_model: "gemini-flash-lite-image", requested_model: null, image_url: "/v1/images/c1", image_id: "c1", parent_candidate_id: null, prompt_version: "ad_generate@3", repair_instruction: null, created_at: "2026-09-25T05:00:05Z", width: 825, height: 1024, evaluation: null },
          { id: "c-2", attempt: 1, slot: 0, kind: "repair", status: "passed", served_model: "gemini-flash-image", requested_model: null, image_url: "/v1/images/c2", image_id: "c2", parent_candidate_id: "c-1", prompt_version: "ad_repair@2", repair_instruction: "Fix only the headline text.", created_at: "2026-09-25T05:00:20Z", width: 825, height: 1024, evaluation: null },
        ],
      },
      response: { status: 200 },
    });
    render(<AdDetailSheet ad={ad("B02-final")} sibling={ad("B02-nat")} summary={SUMMARY} open onOpenChange={() => {}} />);
    // attempt 0 = the first round, shown 1-based.
    expect(await screen.findByText("Attempt 2 · repair")).toBeInTheDocument();
    expect(screen.getByText("Attempt 1 · initial")).toBeInTheDocument();
    expect(attemptTitle({ attempt: 0 })).toBe("Attempt 1");
    expect(screen.getByText("Fix only the headline text.")).toBeInTheDocument();
    expect(screen.getByText("Shipped")).toBeInTheDocument();
    // The shipped candidate's model and prompt version in the provenance list.
    expect(screen.getAllByText("gemini-flash-image").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("ad_repair@2")).toBeInTheDocument();
  });

  it("falls back to E-nat → E-final lineage without a run", () => {
    render(<AdDetailSheet ad={ad("B01-final")} sibling={ad("B01-nat")} summary={SUMMARY} open onOpenChange={() => {}} />);
    const history = document.querySelector('[data-slot="attempt-history"]') as HTMLElement;
    expect(within(history).getByText("First attempt (E-nat)")).toBeInTheDocument();
    expect(within(history).getByText("Shipped (E-final)")).toBeInTheDocument();
    expect(screen.getAllByText("no golden run for this image").length).toBeGreaterThan(0);
    expect(api.runsGetRun).not.toHaveBeenCalled();
  });
});

describe("BatchGallery", () => {
  it("explains the CLI when there is no report", async () => {
    const noReport = {
      data: undefined,
      error: { type: "no-eval-report", title: "No eval report yet", status: 404 },
      response: { status: 404 },
    };
    api.evalsEvalSummary.mockResolvedValue(noReport);
    api.evalsEvalItems.mockResolvedValue(noReport);
    api.runsListRuns.mockResolvedValue({ data: { items: [], next_cursor: null }, response: { status: 200 } });
    render(<BatchGallery />);
    expect(await screen.findByText("No batch outputs yet")).toBeInTheDocument();
    expect(screen.getAllByText(/make golden-run/).length).toBeGreaterThan(0);
  });

  it("opens the deep-linked ad and flags the dry run", async () => {
    api.evalsEvalSummary.mockResolvedValue({ data: SUMMARY, response: { status: 200 } });
    api.evalsEvalItems.mockResolvedValue({ data: { items: NATURAL_ITEMS, next_cursor: null }, response: { status: 200 } });
    api.runsListRuns.mockResolvedValue({ data: { items: GOLDEN_RUNS, next_cursor: null }, response: { status: 200 } });
    api.runsGetRun.mockResolvedValue({ data: undefined, error: {}, response: { status: 404 } });
    render(<BatchGallery initialAd="B05-nat" />);
    expect(await screen.findByText("Human labels vs evaluator")).toBeInTheDocument();
    expect(screen.getByText("Fake dry run")).toBeInTheDocument();
    expect(api.runsListRuns).toHaveBeenCalledWith({ query: { origin: "golden", limit: 100, cursor: undefined } });
  });
});

describe("SourcesView", () => {
  it("lists each product's source, author, licence and share-alike flag, and sanitises SOURCES.md", async () => {
    render(<SourcesView />);
    const row = (await screen.findByText("Alex P. Kok")).closest("tr") as HTMLElement;
    expect(row).toHaveAttribute("id", "source-P2");
    expect(within(row).getByRole("link", { name: "CC BY-SA 4.0" })).toHaveAttribute(
      "href",
      "https://creativecommons.org/licenses/by-sa/4.0",
    );
    expect(within(row).getByText("Yes")).toBeInTheDocument();
    const p1 = document.getElementById("source-P1") as HTMLElement;
    expect(p1.querySelector('[data-col="share-alike"]')).toHaveTextContent("No");
    expect(screen.getByText("data/golden/SOURCES.md")).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });

  it("explains a failed load with a retry", async () => {
    api.goldenGoldenSources.mockRejectedValue(new TypeError("fetch failed"));
    render(<SourcesView />);
    expect(await screen.findByText("Couldn't load the sources")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });
});
