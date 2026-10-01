import { describe, expect, it } from "vitest";

import { friendlyReason, friendlyReasons } from "@/lib/run-reasons";

describe("friendlyReason", () => {
  it("maps known codes to sentences", () => {
    expect(friendlyReason("evaluation_failed")).toMatch(/still failed a quality check/);
    expect(friendlyReason("budget_exceeded")).toMatch(/^Stopped at the budget/);
    expect(friendlyReason("judge_unavailable")).toMatch(/Nothing is auto-approved/);
    expect(friendlyReason("repairs_exhausted")).toMatch(/repair limit/);
  });

  it("accepts problem types with hyphens and any case", () => {
    expect(friendlyReason("run-interrupted")).toMatch(/API restarted/);
    expect(friendlyReason("Budget-Exceeded")).toMatch(/^Stopped at the budget/);
  });

  it("keeps the detail of a blocked generation", () => {
    expect(friendlyReason("generation_blocked: SAFETY")).toMatch(/safety filter.*\(SAFETY\)$/);
  });

  it("humanises unknown codes instead of showing snake_case", () => {
    expect(friendlyReason("some_new_code")).toBe("Some new code.");
  });

  it("splits lists into unique sentences", () => {
    expect(friendlyReasons("evaluation_failed, budget_exceeded")).toHaveLength(2);
    expect(friendlyReasons("max_repairs; repairs_exhausted")).toHaveLength(1);
    expect(friendlyReasons(null)).toEqual([]);
  });
});
