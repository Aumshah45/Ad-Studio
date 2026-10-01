import { describe, expect, it } from "vitest";

import { criticalTokens, displayTokens, restoreQuotedTokens } from "@/lib/tokens";

describe("criticalTokens", () => {
  it("finds numbers, prices, percentages and short caps words as written", () => {
    expect(criticalTokens("Summer Sale — 30% OFF")).toEqual(["30%", "OFF"]);
    expect(criticalTokens("Holiday Deals from $19.90")).toEqual(["$19.90"]);
    expect(criticalTokens("Stay Cool. Only AED 49")).toEqual(["49", "AED"]);
    expect(criticalTokens("Built for −20°C")).toEqual(["-20"]);
    expect(criticalTokens("Winter Warmers")).toEqual([]);
  });
});

describe("displayTokens", () => {
  it("restores the brief's case for casefolded stored tokens", () => {
    expect(displayTokens(["30%", "off"], "Summer Sale — 30% OFF")).toEqual(["30%", "OFF"]);
    expect(displayTokens(["aed"], null)).toEqual(["aed"]);
    expect(displayTokens(["xyz"], "Nothing here")).toEqual(["xyz"]);
  });
});

describe("restoreQuotedTokens", () => {
  it("rewrites quoted casefolded tokens in check evidence", () => {
    expect(restoreQuotedTokens("Missing or altered: 'off'", "Summer Sale — 30% OFF")).toBe("Missing or altered: 'OFF'");
    expect(restoreQuotedTokens("Missing or altered: 'off', '30%'", "30% OFF")).toBe("Missing or altered: 'OFF', '30%'");
    expect(restoreQuotedTokens("Read 'summer'", "Summer Sale")).toBe("Read 'summer'");
  });
});
