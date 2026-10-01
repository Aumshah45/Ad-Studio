import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdStudioCard, DimensionPassRates, StatusView } from "@/components/status/status-view";
import type { AdStudioStatus, ReadyResponse } from "@/lib/api";

const api = vi.hoisted(() => ({ healthReady: vi.fn(), opsSummary: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ADSTUDIO: AdStudioStatus = {
  status: "ok",
  models: [
    { role: "image_candidate", spec: "google:gemini-3.1-flash-lite-image", client: "fake", recorded_as: "test:fake-image", configured: true, breaker: "closed" },
    { role: "image_repair", spec: "google:gemini-3.1-flash-image", client: "gemini", recorded_as: "google:gemini-3.1-flash-image", configured: false, breaker: "open" },
    { role: "vision_judge", spec: "google:gemini-3.8-flash", client: "fake", recorded_as: "test:fake-vision", configured: true, breaker: "closed" },
  ],
  ocr: { engine: "tesseract", available: true, version: "5.5.3", languages: 125, scripts: { Latn: true, Taml: false }, shaping: true },
};

describe("AdStudioCard", () => {
  it("shows a row per image model, the vision judge and OCR, with words not just colour", () => {
    render(<AdStudioCard adstudio={ADSTUDIO} />);
    const list = screen.getByRole("list", { name: "Ad Studio models" });
    const row = (role: string) => list.querySelector(`[data-role="${role}"]`) as HTMLElement;
    expect(within(row("image_candidate")).getByText("Image model · candidates")).toBeInTheDocument();
    expect(within(row("image_candidate")).getByText("fake client")).toBeInTheDocument();
    expect(row("image_candidate")).toHaveTextContent("recorded as test:fake-image");
    expect(within(row("image_repair")).getByText("no key")).toBeInTheDocument();
    expect(within(row("image_repair")).getByText("breaker open")).toBeInTheDocument();
    // Same spec and recorded id: not repeated.
    expect(row("image_repair")).not.toHaveTextContent("recorded as");
    expect(within(row("vision_judge")).getByText("Vision judge")).toBeInTheDocument();
    expect(row("ocr")).toHaveTextContent("tesseract 5.5.3 · 125 language packs");
    expect(within(row("ocr")).getByText("✗ overlay only")).toBeInTheDocument();
  });

  it("says so when the API has no adstudio block", () => {
    render(<AdStudioCard adstudio={null} />);
    expect(screen.getByText(/doesn't report Ad Studio models/)).toBeInTheDocument();
  });
});

describe("StatusView", () => {
  it("renders the health, ops and model cards from /ready", async () => {
    const ready: ReadyResponse = { status: "degraded", db: true, extensions: { vector: true }, providers: [], adstudio: ADSTUDIO };
    api.healthReady.mockResolvedValue({ data: ready, response: { status: 200 } });
    api.opsSummary.mockResolvedValue({ data: { count: 3, by_kind: {} }, response: { status: 200 } });
    render(<StatusView />);
    expect(await screen.findByText("Ad Studio models")).toBeInTheDocument();
    expect(screen.getByText("Degraded")).toBeInTheDocument();
  });

  it("shows an empty state with a retry when the API is down", async () => {
    api.healthReady.mockRejectedValue(new TypeError("fetch failed"));
    api.opsSummary.mockRejectedValue(new TypeError("fetch failed"));
    render(<StatusView />);
    expect(await screen.findByText("Can't reach the API")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });
});

describe("DimensionPassRates", () => {
  it("shows every dimension, composition included, and 'Not checked' without data", () => {
    render(<DimensionPassRates rates={{ technical: 1, text: 0.9, product: 0.85, context: 1, composition: null }} />);
    const tile = (d: string) => document.querySelector(`[data-slot=dimension-pass-rates] [data-dimension="${d}"]`)!;
    expect(tile("text")).toHaveTextContent("90.0%");
    expect(tile("composition")).toHaveTextContent("CompositionNot checked");
    expect(tile("composition")).toHaveAttribute("title", "Composition — realistic scale and natural integration");
  });

  it("renders nothing for an API without the field", () => {
    const { container } = render(<DimensionPassRates rates={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
