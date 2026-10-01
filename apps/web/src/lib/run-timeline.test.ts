import { describe, expect, it } from "vitest";

import type { RunDetail } from "@/lib/api";
import { applyRunEvent, emptyRunState, runStateFromDetail, type RunEvent } from "@/lib/run-events";
import { candidateLabels, deriveTimeline } from "@/lib/run-timeline";
import {
  CAND_A,
  CAND_A1,
  CAND_A2,
  CAND_B,
  FIRST_PASS,
  JUDGE_DOWN,
  REPAIR_THEN_FALLBACK,
  RUN_ID,
} from "@/test/fixtures/run-events";

const fold = (events: RunEvent[]) => events.reduce(applyRunEvent, emptyRunState(RUN_ID));

describe("deriveTimeline", () => {
  it("orders plan → candidates → evaluate → repair → fallback → terminal with durations", () => {
    const state = fold(REPAIR_THEN_FALLBACK);
    const steps = deriveTimeline(state);

    expect(steps.map((s) => s.kind)).toEqual(["plan", "generate", "evaluate", "repair", "fallback", "terminal"]);
    expect(steps.every((s) => s.state === "done")).toBe(true);

    const [plan, generate, evaluate, repair, fallback, terminal] = steps;
    expect(plan!.summary).toBe("Effective season: summer");
    expect(plan!.durationMs).toBe(1800);
    expect(generate!.title).toBe("Generate ×2");
    expect(generate!.candidateIds).toEqual([CAND_A, CAND_B]);
    expect(generate!.durationMs).toBe(8100); // generating at 1.9 s → last candidate at 10 s
    expect(evaluate!.summary).toBe("A failed Text · B failed Context");
    expect(evaluate!.durationMs).toBe(4900);
    expect(repair!.title).toBe("Repair A · Text");
    expect(repair!.candidateIds).toEqual([CAND_A1]);
    expect(repair!.durationMs).toBe(11800); // 15.2 s → evaluated at 27 s
    expect(fallback!.summary).toMatch(/^Text drawn by Ad Studio/);
    expect(fallback!.candidateIds).toEqual([CAND_A2]);
    expect(terminal!.title).toBe("Passed all quality gates");
    expect(terminal!.durationMs).toBe(30500);

    expect(state.repairs[0]).toMatchObject({ fromCandidateId: CAND_A, resultCandidateId: CAND_A1 });
    expect(state.fallbacks[0]).toMatchObject({
      sourceCandidateId: CAND_A1,
      resultCandidateId: CAND_A2,
      reason: "text_exhausted",
    });
    expect(state.outcome).toBe("overlay");
  });

  it("labels lineage: A, B, then a prime per repair or overlay generation", () => {
    const labels = candidateLabels(fold(REPAIR_THEN_FALLBACK));
    expect(Object.fromEntries(labels)).toEqual({
      [CAND_A]: "A",
      [CAND_B]: "B",
      [CAND_A1]: "A′",
      [CAND_A2]: "A′′",
    });
  });

  it("shows running and pending steps mid-stream", () => {
    const upToRepair = REPAIR_THEN_FALLBACK.slice(0, REPAIR_THEN_FALLBACK.findIndex((e) => e.type === "repair.started") + 1);
    const steps = deriveTimeline(fold(upToRepair));
    expect(steps.map((s) => [s.kind, s.state])).toEqual([
      ["plan", "done"],
      ["generate", "done"],
      ["evaluate", "done"],
      ["repair", "running"],
      ["fallback", "pending"],
      ["terminal", "pending"],
    ]);
    const repair = steps[3]!;
    expect(repair.startedAt).not.toBeNull();
    expect(repair.durationMs).toBeNull();

    const planning = deriveTimeline(fold(REPAIR_THEN_FALLBACK.slice(0, 1)));
    expect(planning.map((s) => s.state)).toEqual(["running", "pending", "pending", "pending", "pending", "pending"]);
  });

  it("skips repair and fallback when the first candidate passes", () => {
    const steps = deriveTimeline(fold(FIRST_PASS));
    const byKind = Object.fromEntries(steps.map((s) => [s.kind, s]));
    expect(byKind.repair).toMatchObject({ state: "skipped", summary: "Not needed (a first-round candidate passed)" });
    expect(byKind.fallback).toMatchObject({ state: "skipped", summary: "Not needed (native text passed)" });
    expect(byKind.generate!.title).toBe("Generate");
    expect(byKind.terminal!.state).toBe("done");
  });

  it("ends held when the judge is down", () => {
    const state = fold(JUDGE_DOWN);
    const steps = deriveTimeline(state);
    expect(steps.at(-1)).toMatchObject({ kind: "terminal", state: "done", title: "Held for review" });
    expect(steps.find((s) => s.kind === "evaluate")!.summary).toBe("A unsure");
    expect(state.budgetWarning).toEqual({ spentUsd: 0.2, capUsd: 0.25, reason: null });
    expect(state.reason).toBe("evaluation_failed");
  });

  it("rebuilds the same timeline from a snapshot plus the replayed log without duplicates", () => {
    const detail: RunDetail = {
      run: {
        id: RUN_ID,
        status: "passed",
        config: {},
        cost_usd: 0.14,
        created_at: "2026-09-24T14:10:00.000Z",
        error: null,
        finished_at: "2026-09-24T14:10:30.500Z",
        first_attempt_pass: false,
        latency_ms: 30500,
        origin: "ui",
        outcome: "overlay",
        repair_count: 1,
      },
      brief: {
        id: "b",
        product_id: "p",
        geography_code: "AU",
        geography_detail: null,
        golden_key: "B01",
        season: "December",
        required_text: "Summer Sale — 30% OFF",
        aspect_ratio: "4:5",
      },
      candidates: [CAND_A, CAND_B, CAND_A1, CAND_A2].map((id, i) => ({
        id,
        slot: i === 1 ? 1 : 0,
        attempt: i < 2 ? 1 : i,
        kind: ["initial", "initial", "repair", "overlay"][i]!,
        status: "evaluated",
        created_at: "2026-09-24T14:10:10.000Z",
        image_id: `img-${id}`,
        image_url: `/v1/images/img-${id}`,
        width: 819,
        height: 1024,
        parent_candidate_id: [null, null, CAND_A, CAND_A1][i]!,
        prompt_version: null,
        repair_instruction: i === 2 ? "Fix only the headline text." : null,
        requested_model: "m",
        served_model: "m",
        evaluation: null,
      })),
      approved_candidate_id: CAND_A2,
      cost_usd: 0.14,
      events_url: "",
      latency_ms: 30500,
      product_id: "p",
      spec: null,
      spec_version: null,
    };
    const fromSnapshot = runStateFromDetail(detail);
    expect(fromSnapshot.repairs).toHaveLength(1);
    expect(fromSnapshot.fallbacks).toHaveLength(1);

    const merged = REPAIR_THEN_FALLBACK.reduce(applyRunEvent, fromSnapshot);
    expect(merged.candidates).toHaveLength(4);
    expect(merged.repairs).toHaveLength(1);
    expect(merged.repairs[0]).toMatchObject({ targetDimension: "text", resultCandidateId: CAND_A1 });
    expect(merged.fallbacks).toHaveLength(1);
    expect(merged.fallbacks[0]).toMatchObject({ reason: "text_exhausted", resultCandidateId: CAND_A2 });
    expect(deriveTimeline(merged).map((s) => [s.kind, s.state])).toEqual(
      deriveTimeline(fold(REPAIR_THEN_FALLBACK)).map((s) => [s.kind, s.state]),
    );
  });
});
