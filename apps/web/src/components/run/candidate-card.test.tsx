import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CandidateCard } from "@/components/run/candidate-card";
import { FallbackCard, reasonText } from "@/components/run/repair-fallback-cards";
import { Scorecard } from "@/components/run/scorecard";
import type { CandidateState } from "@/lib/run-events";
import { CHECKS } from "@/test/fixtures/run-events";

const ocrFail = CHECKS[1]!;

afterEach(cleanup);

function cand(over: Partial<CandidateState>): CandidateState {
  return {
    id: "c1",
    slot: 0,
    attempt: 0,
    kind: "initial",
    parentId: null,
    model: "test:fake-image",
    requestedModel: null,
    imageUrl: null,
    width: 825,
    height: 1024,
    cached: false,
    status: "evaluated",
    reason: null,
    verdict: "fail",
    dimensions: {},
    checks: [],
    repairInstruction: null,
    createdAt: null,
    evaluatedAt: null,
    ...over,
  };
}

describe("CandidateCard lineage", () => {
  it("numbers attempts and slots from 1", () => {
    render(<CandidateCard candidate={cand({ slot: 1 })} label="B" />);
    expect(screen.getByText(/Attempt 1 · slot 2 · candidate/)).toBeInTheDocument();
  });

  it("drops the slot for repairs", () => {
    render(<CandidateCard candidate={cand({ attempt: 1, kind: "repair_text" })} label="B′" parentLabel="B" />);
    expect(screen.getByText(/Attempt 2 · repair text · from B/)).toBeInTheDocument();
  });
});

describe("critical-token evidence", () => {
  it("shows stored casefolded tokens as the brief wrote them", () => {
    render(
      <Scorecard
        requiredText="Summer Sale — 30% OFF"
        checks={[
          { ...ocrFail, check_name: "critical_tokens", evidence: "Missing or altered: 'off'", value: 1, threshold: 0 },
        ]}
      />,
    );
    expect(screen.getByText("Missing or altered: 'OFF'")).toBeInTheDocument();
  });
});

describe("FallbackCard", () => {
  it("reads a free-text reason as a sentence and discloses the overlay", () => {
    expect(reasonText("text still failing (or unverified): deterministic overlay")).toBe(
      "Text still failing (or unverified): deterministic overlay.",
    );
    render(<FallbackCard reason="text still failing" />);
    expect(screen.getByText(/Why: Text still failing\./)).toHaveTextContent("Text drawn by Ad Studio, not the model");
  });
});
