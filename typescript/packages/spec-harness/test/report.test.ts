import { describe, expect, it } from "vitest";

import { readManifest } from "../src/conform.ts";
import { approvals } from "../src/harness.ts";
import { rederive } from "../src/rederive.ts";
import { buildReleaseReport, renderReleaseReport, tierStatuses } from "../src/report.ts";
import { InMemoryLedger, testResult } from "../src/testing/fakes.ts";
import { trace } from "../src/trace.ts";
import { fixture } from "./helpers.ts";

const emptyTrace = trace([], []);
const verified = (() => {
  const r = readManifest(fixture("kernel/verified.json"));
  if (!r.ok) throw new Error("fixture");
  return rederive(r.manifest);
})();

describe("[TST-006] release report claims only levels whose tiers passed", () => {
  it("[TST-006] grants Level 1 when T9 passed and refuses Level 2 without T7 and T8", () => {
    const report = buildReleaseReport({
      version: "0.1.0",
      claimedLevels: [1, 2],
      tests: [testResult({ name: "t", tiers: ["T8"] })],
      externalTiers: { T9: "passed" },
      traceability: emptyTrace,
      rederivations: [verified],
    });
    expect(report.levels.map((l) => [l.level, l.granted])).toEqual([
      [1, true],
      [2, false],
    ]);
    expect(report.ok).toBe(false);
    expect(report.blocking[0]).toContain("T7 (not-run)");
  });

  it("[TST-006] a failing test fails its tier even if others passed", () => {
    const tiers = tierStatuses([testResult({ name: "a", tiers: ["T8"] }), testResult({ name: "b", tiers: ["T8"], outcome: "failed" })]);
    expect(tiers.T8).toBe("failed");
  });

  it("[TST-006] never grants Level 4 without cited independent evidence", () => {
    const tests = ["T7", "T8"].map((t) => testResult({ name: t, tiers: [t] }));
    const without = buildReleaseReport({ version: "x", claimedLevels: [4], tests, traceability: emptyTrace, rederivations: [] });
    const withEvidence = buildReleaseReport({
      version: "x",
      claimedLevels: [4],
      tests,
      traceability: emptyTrace,
      rederivations: [],
      independentEvidence: ["Someone et al. 2027"],
    });
    expect(without.ok).toBe(false);
    expect(withEvidence.ok).toBe(true);
  });

  it("[TST-007] blocks the release when a re-derived verdict disagrees", () => {
    const report = buildReleaseReport({
      version: "x",
      claimedLevels: [],
      tests: [],
      traceability: emptyTrace,
      rederivations: [{ ...verified, state: "disagree", kernelVerdict: "VERIFIED", harnessVerdict: "REJECTED", publishable: false }],
    });
    expect(report.ok).toBe(false);
    expect(renderReleaseReport(report)).toContain("**Status: blocked.**");
  });

  it("lists approvals from the ledger, flagging self-approval", async () => {
    const ledger = new InMemoryLedger([
      {
        entry_id: "e2",
        sequence: 1,
        kind: "approval",
        subject_id: "exp-1",
        subject_hash: "sha256:x",
        signer: "leigh",
        payload: { experiment_id: "exp-1", verdict_status: "VERIFIED", self_approval: true },
      },
    ]);
    expect(await approvals(ledger)).toEqual([
      { experimentId: "exp-1", reviewer: "leigh", verdict: "VERIFIED", selfApproval: true, entryId: "e2" },
    ]);
  });
});
