import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RunDetail } from "@/lib/api";
import type { RunEvent } from "@/lib/run-events";

import { useRunStream } from "./use-run-stream";

const RUN = "11111111-1111-1111-1111-111111111111";
const CAND = "22222222-2222-2222-2222-222222222222";
const BASE = "http://api.test";
const NO_WAIT = [0, 0, 0];
const AT = "2026-09-24T14:10:00Z";

function frame(ev: RunEvent): string {
  return `id: ${ev.seq}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`;
}

function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close(); // the server (or network) ends the stream here
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

const snapshot: RunDetail = {
  run: {
    id: RUN,
    status: "planning",
    config: {},
    cost_usd: 0,
    created_at: AT,
    error: null,
    finished_at: null,
    first_attempt_pass: null,
    latency_ms: null,
    origin: "ui",
    outcome: null,
    repair_count: 0,
  },
  brief: {
    id: "b",
    product_id: "p",
    geography_code: "AU",
    geography_detail: null,
    golden_key: null,
    season: "December",
    required_text: "Summer Sale — 30% OFF",
    aspect_ratio: "4:5",
  },
  candidates: [],
  approved_candidate_id: null,
  cost_usd: 0,
  events_url: `/v1/runs/${RUN}/events`,
  latency_ms: null,
  product_id: "p",
  spec: null,
  spec_version: null,
};

const firstBatch: RunEvent[] = [
  { type: "run.status", seq: 1, run_id: RUN, at: AT, status: "planning" },
  {
    type: "plan.done",
    seq: 2,
    run_id: RUN,
    at: AT,
    spec_summary: {
      source: "planner",
      effective_season: "summer",
      hemisphere: "south",
      rationale: "Australia is in the southern hemisphere; December is summer.",
    },
  },
];

const secondBatch: RunEvent[] = [
  {
    type: "candidate.created",
    seq: 3,
    run_id: RUN,
    at: AT,
    candidate_id: CAND,
    attempt: 1,
    slot: 0,
    kind: "initial",
    model: "fake-image",
    cached: false,
    image_id: "img",
    image_url: "/v1/images/img",
    width: 819,
    height: 1024,
    native_width: 819,
    native_height: 1024,
    reason: null,
    status: "pending",
  },
  {
    type: "evaluation.done",
    seq: 4,
    run_id: RUN,
    at: AT,
    candidate_id: CAND,
    evaluation_id: null,
    verdict: "pass",
    dimensions: { technical: { passed: true, reasons: [] }, text: { passed: true, reasons: [] } },
  },
  {
    type: "run.finished",
    seq: 5,
    run_id: RUN,
    at: AT,
    status: "passed",
    outcome: "native",
    approved_candidate_id: CAND,
    cost_usd: 0.04,
    latency_ms: 1234,
    reason: null,
  },
];

function urlOf(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input);
}

function headerOf(input: RequestInfo | URL, init: RequestInit | undefined, name: string) {
  if (input instanceof Request) return input.headers.get(name);
  return new Headers(init?.headers).get(name);
}

describe("useRunStream", () => {
  it("parses events and reconnects with Last-Event-ID after a dropped stream", async () => {
    const streamCalls: (string | null)[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = urlOf(input);
      if (url.endsWith(`/v1/runs/${RUN}`)) return json(snapshot);
      if (url.endsWith(`/v1/runs/${RUN}/events`)) {
        streamCalls.push(headerOf(input, init, "Last-Event-ID"));
        if (streamCalls.length === 1) {
          // Keep-alive comment, two events, then the connection drops before run.finished.
          return sseResponse([": keep-alive\n\n", frame(firstBatch[0]!), frame(firstBatch[1]!)]);
        }
        // The server replays seq > Last-Event-ID; send a duplicate too, which must be ignored.
        return sseResponse([frame(firstBatch[1]!), ...secondBatch.map(frame)]);
      }
      throw new Error(`unexpected fetch ${url}`);
    }) as unknown as typeof fetch;

    const { result } = renderHook(() =>
      useRunStream(RUN, { fetchImpl, baseUrl: BASE, retryDelaysMs: NO_WAIT }),
    );

    await waitFor(() => expect(result.current.status).toBe("closed"));

    expect(streamCalls).toEqual([null, "2"]);
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3, 4, 5]);
    expect(result.current.error).toBeNull();

    const { state } = result.current;
    expect(state.status).toBe("passed");
    expect(state.plan?.effective_season).toBe("summer");
    expect(state.plan?.hemisphere).toBe("south");
    expect(state.candidates).toHaveLength(1);
    expect(state.candidates[0]).toMatchObject({
      id: CAND,
      imageUrl: "/v1/images/img",
      verdict: "pass",
      width: 819,
      height: 1024,
    });
    expect(state.candidates[0]?.dimensions.text?.passed).toBe(true);
    expect(state.approvedCandidateId).toBe(CAND);
    expect(state.costUsd).toBe(0.04);
  });

  it("rebuilds from the run snapshot and gives up after the retry budget", async () => {
    const detail: RunDetail = {
      ...snapshot,
      run: { ...snapshot.run, status: "generating" },
      spec: {
        draft_source: "llm",
        geo: { country_name: "Australia", hemisphere: "south" },
        season: { effective_season: "summer", rationale: "Dec-Feb is summer in the south." },
      },
      candidates: [
        {
          id: CAND,
          slot: 0,
          attempt: 1,
          kind: "initial",
          status: "evaluated",
          created_at: AT,
          image_id: "img",
          image_url: "/v1/images/img",
          width: 819,
          height: 1024,
          parent_candidate_id: null,
          prompt_version: null,
          repair_instruction: null,
          requested_model: "fake-image",
          served_model: "fake-image",
          evaluation: {
            id: "e",
            evaluator_version: "v1",
            latency_ms: 10,
            overall_pass: false,
            verdict: "fail",
            dimensions: { text: false },
            checks: [
              {
                check_name: "ocr_exact",
                dimension: "text",
                evidence: "Read 'Summr Sale'",
                method: "tesseract",
                passed: false,
                threshold: 0.02,
                value: 0.05,
              },
            ],
          },
        },
      ],
    };
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.endsWith(`/v1/runs/${RUN}`)) return json(detail);
      throw new TypeError("network down");
    }) as unknown as typeof fetch;

    const { result } = renderHook(() =>
      useRunStream(RUN, { fetchImpl, baseUrl: BASE, retryDelaysMs: [0, 0] }),
    );

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toMatch(/Lost connection/);
    const { state } = result.current;
    expect(state.status).toBe("generating");
    expect(state.plan).toMatchObject({ effective_season: "summer", hemisphere: "south" });
    expect(state.candidates[0]).toMatchObject({ verdict: "fail", imageUrl: "/v1/images/img" });
    expect(state.candidates[0]?.dimensions.text).toEqual({
      passed: false,
      reasons: ["Read 'Summr Sale'"],
    });
    // One snapshot call plus the first attempt and two retries.
    expect(fetchImpl).toHaveBeenCalledTimes(4);
  });
});
