import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalCard, type ApprovalCardProps } from "@/components/run/approval-card";
import type { DecisionView } from "@/lib/api";
import type { CandidateState } from "@/lib/run-events";
import { CAND_A, EVIDENCE_CHECKS, RUN_ID } from "@/test/fixtures/run-events";

const api = vi.hoisted(() => ({ runsDecideRun: vi.fn(), runsCancelRun: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }));

const download = vi.hoisted(() => vi.fn());
vi.mock("@/lib/decision", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  downloadAd: download,
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const candidate: CandidateState = {
  id: CAND_A,
  slot: 0,
  attempt: 1,
  kind: "overlay",
  parentId: null,
  model: "ad-studio-overlay",
  requestedModel: null,
  imageUrl: "/v1/images/img-a",
  width: 825,
  height: 1024,
  cached: false,
  status: "passed",
  reason: null,
  verdict: "pass",
  dimensions: {
    text: { passed: true, reasons: [] },
    product: { passed: true, reasons: [] },
    context: { passed: true, reasons: [] },
  },
  checks: EVIDENCE_CHECKS.map((c) => ({ ...c, passed: true })),
  repairInstruction: null,
  createdAt: null,
  evaluatedAt: null,
};

function decision(over: Partial<DecisionView>): DecisionView {
  return {
    id: "d1",
    run_id: RUN_ID,
    action: "approve",
    actor: "demo",
    approved_candidate_id: CAND_A,
    created_at: "2026-09-24T14:11:00Z",
    is_override: false,
    previous_status: "passed",
    reason: null,
    status: "approved",
    ...over,
  };
}

function props(over: Partial<ApprovalCardProps> = {}): ApprovalCardProps {
  return {
    runId: RUN_ID,
    status: "passed",
    candidate,
    label: "A″",
    requiredText: "Summer Sale — 30% OFF",
    aspect: "4:5",
    outcome: "overlay",
    repairs: 1,
    costUsd: 0.12,
    latencyMs: 38000,
    rerunHref: "/?product=p1&geo=AU",
    fileBase: "ad-studio-au-december-11111111",
    ...over,
  };
}

beforeEach(() => {
  api.runsDecideRun.mockReset();
  download.mockReset();
  download.mockResolvedValue({ filename: "ad.png", format: "PNG" });
});
afterEach(cleanup);

describe("ApprovalCard (passed)", () => {
  it("summarises the gate: dimensions, text source, repairs, cost and time", () => {
    render(<ApprovalCard {...props()} />);
    expect(screen.getByRole("heading", { name: /Passed all quality gates/ })).toBeInTheDocument();
    expect(screen.getByText(/Text ✓ Product ✓ Context ✓ Composition not checked · Text: drawn by Ad Studio · 1 repair · \$0\.12 · 38 s/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Rerun for another market/ })).toHaveAttribute("href", "/?product=p1&geo=AU");
  });

  it("Approve and download records the decision, then downloads the approved image", async () => {
    const onDecided = vi.fn();
    api.runsDecideRun.mockResolvedValue({ data: decision({}), response: { status: 200 } });
    render(<ApprovalCard {...props({ onDecided })} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve and download/ }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    expect(api.runsDecideRun).toHaveBeenCalledWith({
      path: { run_id: RUN_ID },
      body: { action: "approve", reason: null },
    });
    expect(onDecided).toHaveBeenCalledWith(expect.objectContaining({ status: "approved" }));
    expect(download).toHaveBeenCalledWith(expect.stringMatching(/\/v1\/images\/img-a$/), "ad-studio-au-december-11111111");
  });

  it("doesn't download when the decision fails, and says why", async () => {
    api.runsDecideRun.mockResolvedValue({
      error: { type: "run-not-decidable", title: "Run can't be decided", status: 409, detail: "Already approved." },
      response: { status: 409 },
    });
    render(<ApprovalCard {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve and download/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Already approved.");
    expect(download).not.toHaveBeenCalled();
  });
});

describe("ApprovalCard (held)", () => {
  const held = () =>
    props({
      status: "needs_review",
      outcome: null,
      reasons: ["The vision judge was unavailable, so Context couldn't be checked."],
      candidate: { ...candidate, dimensions: { ...candidate.dimensions, context: { passed: null, reasons: [] } } },
    });

  it("explains why it was held and offers an override and a rerun", () => {
    render(<ApprovalCard {...held()} />);
    expect(screen.getByRole("heading", { name: /Held for review/ })).toBeInTheDocument();
    expect(screen.getByText("Not approved because Context is unsure.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Rerun with edits/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve and download/ })).toBeNull();
  });

  it("the override needs a reason before submit is enabled, and sends it", async () => {
    api.runsDecideRun.mockResolvedValue({
      data: decision({ is_override: true, previous_status: "needs_review", reason: "Beach is right for Sydney." }),
      response: { status: 200 },
    });
    render(<ApprovalCard {...held()} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve anyway with reason/ }));
    const submit = screen.getByRole("button", { name: /Approve with override/ });
    const reason = screen.getByLabelText(/Reason for approving/);
    expect(reason).toBeRequired();
    expect(submit).toBeDisabled();

    fireEvent.change(reason, { target: { value: "  ok " } });
    expect(submit).toBeDisabled(); // whitespace doesn't count: at least 3 characters

    fireEvent.change(reason, { target: { value: "Beach is right for Sydney." } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    expect(api.runsDecideRun).toHaveBeenCalledWith({
      path: { run_id: RUN_ID },
      body: { action: "approve", reason: "Beach is right for Sydney." },
    });
  });

  it("shows the API's override-reason-required error on the reason field", async () => {
    api.runsDecideRun.mockResolvedValue({
      error: { type: "override-reason-required", title: "Reason required", status: 422, detail: "Say why." },
      response: { status: 422 },
    });
    render(<ApprovalCard {...held()} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve anyway with reason/ }));
    fireEvent.change(screen.getByLabelText(/Reason for approving/), { target: { value: "because" } });
    fireEvent.click(screen.getByRole("button", { name: /Approve with override/ }));
    expect(await screen.findByText("Say why.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Reason for approving/)).toHaveAttribute("aria-invalid", "true");
    expect(download).not.toHaveBeenCalled();
  });
});

describe("ApprovalCard (decided)", () => {
  it("shows an override approval after reload, from the gate's status", () => {
    render(<ApprovalCard {...props({ status: "approved", gateStatus: "needs_review" })} />);
    expect(screen.getByRole("heading", { name: /Approved with override/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download ad/ })).toBeInTheDocument();
  });

  it("shows a rejection with a rerun", () => {
    render(<ApprovalCard {...props({ status: "rejected" })} />);
    expect(screen.getByRole("heading", { name: /Rejected/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Rerun with edits/ })).toBeInTheDocument();
  });
});
