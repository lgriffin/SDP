// The release report's exact records: every blocking line, every level reason and every rendered section,
// so a changed word in the report a release is judged by fails a test (TST-006, TST-007).
import { describe, expect, it } from "vitest";

import type { ClaimCheck } from "../src/admit.ts";
import type { RatchetResult } from "../src/ratchet.ts";
import type { Rederivation } from "../src/rederive.ts";
import { buildReleaseReport, LEVEL_NAMES, LEVEL_TIERS, renderReleaseReport, tierStatuses, type ReleaseInputs } from "../src/report.ts";
import { testResult } from "../src/testing/fakes.ts";
import { trace, type Traceability } from "../src/trace.ts";

const emptyTrace = trace([], []);

const rederivation = (partial: Partial<Rederivation> & { experimentId: string }): Rederivation => ({
  state: "agree",
  kernelVerdict: "VERIFIED",
  harnessVerdict: "VERIFIED",
  checks: [],
  disagreements: [],
  unadmitted: [],
  publishable: true,
  ...partial,
});

const claim = (sourceId: string, removed: ClaimCheck["removed"]): ClaimCheck =>
  ({ sourceId, adapter: null, removed, stripped: {} }) as unknown as ClaimCheck;

const inputs = (partial: Partial<ReleaseInputs> = {}): ReleaseInputs => ({
  version: "1.2.3",
  claimedLevels: [],
  tests: [],
  traceability: emptyTrace,
  rederivations: [],
  ...partial,
});

const passing = (...tiers: string[]) => tiers.map((t) => testResult({ name: t, tiers: [t] }));

describe("[TST-006] the levels and their names", () => {
  it("[TST-006] maps each level to the tiers it needs and its name", () => {
    expect(LEVEL_TIERS).toEqual({ 1: ["T9"], 2: ["T7", "T8"], 3: ["T8"], 4: [] });
    expect(LEVEL_NAMES).toEqual({
      1: "computational",
      2: "analytical (independent implementation)",
      3: "scientific (survives alternative assumptions)",
      4: "independent evidence",
    });
  });
});

describe("[TST-006] tier statuses", () => {
  it("[TST-006] ignores skipped tests, and a failure wins in either order", () => {
    expect(
      tierStatuses([
        testResult({ name: "s", tiers: ["T1"], outcome: "skipped" }),
        testResult({ name: "f", tiers: ["T2"], outcome: "failed" }),
        testResult({ name: "p", tiers: ["T2", "T3"] }),
      ]),
    ).toEqual({ T2: "failed", T3: "passed" });
  });

  it("[TST-006] an external status never overrides a failed tier but sets the others", () => {
    const tests = [testResult({ name: "f", tiers: ["T8"], outcome: "failed" }), testResult({ name: "p", tiers: ["T7"] })];
    expect(tierStatuses(tests, { T7: "not-run", T8: "passed", T9: "passed" })).toEqual({ T7: "not-run", T8: "failed", T9: "passed" });
  });
});

describe("[TST-006] level claims", () => {
  it("[TST-006] sorts and de-duplicates the claimed levels, giving each its exact reason", () => {
    const report = buildReleaseReport(
      inputs({ claimedLevels: [3, 1, 2, 3], tests: [...passing("T8"), testResult({ name: "x", tiers: ["T7"], outcome: "failed" })] }),
    );
    expect(report.levels).toEqual([
      { level: 1, name: "computational", granted: false, reason: "needs T9 (not-run)" },
      { level: 2, name: "analytical (independent implementation)", granted: false, reason: "needs T7 (failed)" },
      { level: 3, name: "scientific (survives alternative assumptions)", granted: true, reason: "T8 passed in this run" },
    ]);
    expect(report.blocking).toEqual(["Level 1 claimed but needs T9 (not-run)", "Level 2 claimed but needs T7 (failed)"]);
  });

  it("[TST-006] names every tier a level still needs, and every tier that passed", () => {
    expect(buildReleaseReport(inputs({ claimedLevels: [2] })).levels[0]?.reason).toBe("needs T7 (not-run), T8 (not-run)");
    expect(buildReleaseReport(inputs({ claimedLevels: [2], tests: passing("T7", "T8") })).levels[0]?.reason).toBe(
      "T7, T8 passed in this run",
    );
  });

  it("[TST-006] states how many independent confirmations Level 4 cites, or that none is", () => {
    const granted = buildReleaseReport(inputs({ claimedLevels: [4], independentEvidence: ["A 2027", "B 2028"] }));
    expect(granted.levels).toEqual([{ level: 4, name: "independent evidence", granted: true, reason: "cites 2 independent confirmation(s)" }]);
    const refused = buildReleaseReport(inputs({ claimedLevels: [4], independentEvidence: [] }));
    expect(refused.levels[0]).toMatchObject({ granted: false, reason: "no independent evidence is cited" });
    expect(refused.blocking).toEqual(["Level 4 claimed but no independent evidence is cited"]);
  });
});

describe("[TST-007] blocking lines", () => {
  it("[TST-007] blocks a disagreement unless every disagreement is about admission", () => {
    const admissionOnly = rederivation({
      experimentId: "exp-a",
      state: "disagree",
      disagreements: [{ subject: "admission gaia", kernel: true, harness: false }],
    });
    const mixed = rederivation({
      experimentId: "exp-b",
      state: "disagree",
      kernelVerdict: "VERIFIED",
      harnessVerdict: "REJECTED",
      disagreements: [
        { subject: "admission gaia", kernel: true, harness: false },
        { subject: "gate G-1", kernel: 1, harness: 2 },
      ],
    });
    const verdict = rederivation({
      experimentId: "exp-c",
      state: "disagree",
      kernelVerdict: null,
      harnessVerdict: "INCONCLUSIVE",
      disagreements: [{ subject: "verdict", kernel: null, harness: "INCONCLUSIVE" }],
    });
    const agreeing = rederivation({ experimentId: "exp-d", disagreements: [{ subject: "gate G-2", kernel: 1, harness: 2 }] });
    expect(buildReleaseReport(inputs({ rederivations: [admissionOnly, mixed, verdict, agreeing] })).blocking).toEqual([
      "exp-b: harness re-derived REJECTED, kernel issued VERIFIED",
      "exp-c: harness re-derived INCONCLUSIVE, kernel issued null",
    ]);
  });

  it("[TST-007] blocks a disagreement with no disagreements listed", () => {
    const r = rederivation({ experimentId: "exp-e", state: "disagree", harnessVerdict: "REJECTED" });
    expect(buildReleaseReport(inputs({ rederivations: [r] })).blocking).toEqual(["exp-e: harness re-derived REJECTED, kernel issued VERIFIED"]);
  });

  it("[INT-016] blocks every unadmitted adapter, naming its version or its lack of one", () => {
    const r = rederivation({
      experimentId: "exp-f",
      state: "disagree",
      disagreements: [{ subject: "admission gaia", kernel: true, harness: false }],
      unadmitted: [
        { adapter: "gaia", adapterVersion: "1.0", productType: "catalogue", reason: "no record" },
        { adapter: "mast", adapterVersion: null, productType: "image", reason: "record failed" },
      ],
    });
    expect(buildReleaseReport(inputs({ rederivations: [r] })).blocking).toEqual([
      "exp-f: verdict issued with gaia 1.0 not admitted for catalogue (no record)",
      "exp-f: verdict issued with mast (no version) not admitted for image (record failed)",
    ]);
  });

  it("[INT-002] blocks traceability violations, removed claims and a failed ratchet, in that order", () => {
    const traceability: Traceability = {
      ...emptyTrace,
      violations: [{ kind: "untested-requirement", subject: "KRN-001", message: "KRN-001 has no test" }],
    };
    const mutation: RatchetResult = {
      ok: false,
      violations: [{ pkg: "python/x", score: 80, highWater: 90 }],
      highWater: { "python/x": 90, "python/y": 95 },
      missing: ["python/y"],
      belowGoal: [],
    };
    const report = buildReleaseReport(
      inputs({ traceability, mutation, claims: [claim("gaia-dr3", [{ productType: "catalogue", reason: "no record" }])] }),
    );
    expect(report.blocking).toEqual([
      "traceability: KRN-001 has no test",
      "claims: gaia-dr3 claims catalogue without admission (no record); claim removed",
      "mutation: python/x scored 80, high-water 90",
      "mutation: no score for python/y",
    ]);
    expect(report.ok).toBe(false);
    expect(report.mutation).toBe(mutation);
  });

  it("[TST-004] a passing ratchet blocks nothing even if it lists violations", () => {
    const mutation: RatchetResult = { ok: true, violations: [{ pkg: "p", score: 1, highWater: 2 }], highWater: {}, missing: ["q"], belowGoal: [] };
    expect(buildReleaseReport(inputs({ mutation })).blocking).toEqual([]);
  });

  it("[TST-006] defaults to no mutation, approvals or claims and carries the rest through", () => {
    const rederivations = [rederivation({ experimentId: "exp-g" })];
    expect(buildReleaseReport(inputs({ rederivations }))).toEqual({
      version: "1.2.3",
      ok: true,
      tiers: {},
      levels: [],
      blocking: [],
      rederivations,
      traceability: emptyTrace.summary,
      mutation: null,
      approvals: [],
      claims: [],
    });
  });
});

describe("[TST-006] the rendered report", () => {
  it("[TST-006] renders a releasable report with only its fixed sections", () => {
    const report = buildReleaseReport(inputs({ claimedLevels: [3], tests: passing("T8", "T10", "T2") }));
    const t = report.traceability;
    expect(renderReleaseReport(report)).toBe(
      [
        "# Release report 1.2.3",
        "",
        "**Status: releasable.**",
        "",
        "## Reproducibility levels",
        "",
        "| Level | Name | Claimed | Why |",
        "|---|---|---|---|",
        "| 3 | scientific (survives alternative assumptions) | granted | T8 passed in this run |",
        "",
        "## Tiers",
        "",
        "| Tier | Result |",
        "|---|---|",
        "| T2 | passed |",
        "| T8 | passed |",
        "| T10 | passed |",
        "",
        "## Verdicts",
        "",
        "| Experiment | Kernel | Harness | State |",
        "|---|---|---|---|",
        "",
        "## Requirements",
        "",
        `Covered ${t.covered}, failing ${t.failing}, untested ${t.untested}, draft ${t.draft}.`,
        "",
      ].join("\n"),
    );
  });

  it("[TST-007] renders every optional section of a blocked report", () => {
    const mutation: RatchetResult = { ok: true, violations: [], highWater: { "python/b": 91, "python/a": 93 }, missing: [], belowGoal: [] };
    const report = buildReleaseReport(
      inputs({
        claimedLevels: [1],
        traceability: { ...emptyTrace, summary: { ...emptyTrace.summary, covered: 4, failing: 3, untested: 2, draft: 1 } },
        rederivations: [
          rederivation({ experimentId: "exp-1", kernelVerdict: null, harnessVerdict: "INCONCLUSIVE", state: "unvalidated", publishable: false }),
          rederivation({
            experimentId: "exp-2",
            state: "disagree",
            disagreements: [{ subject: "admission gaia", kernel: true, harness: false }],
            unadmitted: [{ adapter: "gaia", adapterVersion: "1.0", productType: "catalogue", reason: "no record" }],
          }),
        ],
        mutation,
        approvals: [
          { experimentId: "exp-2", reviewer: "ada", verdict: "VERIFIED", selfApproval: false, entryId: "e1" },
          { experimentId: "exp-2", reviewer: "leigh", verdict: "VERIFIED", selfApproval: true, entryId: "e2" },
        ],
        claims: [claim("gaia-dr3", [{ productType: "image", reason: "record failed" }]), claim("mast", [])],
      }),
    );
    expect(renderReleaseReport(report)).toBe(
      [
        "# Release report 1.2.3",
        "",
        "**Status: blocked.**",
        "",
        "## Blocking",
        "",
        "- Level 1 claimed but needs T9 (not-run)",
        "- exp-2: verdict issued with gaia 1.0 not admitted for catalogue (no record)",
        "- claims: gaia-dr3 claims image without admission (record failed); claim removed",
        "",
        "## Reproducibility levels",
        "",
        "| Level | Name | Claimed | Why |",
        "|---|---|---|---|",
        "| 1 | computational | refused | needs T9 (not-run) |",
        "",
        "## Tiers",
        "",
        "| Tier | Result |",
        "|---|---|",
        "",
        "## Verdicts",
        "",
        "| Experiment | Kernel | Harness | State |",
        "|---|---|---|---|",
        "| exp-1 | — | INCONCLUSIVE | unvalidated |",
        "| exp-2 | VERIFIED | VERIFIED | disagree |",
        "",
        "## Mutation scores (high-water marks)",
        "",
        "| Package | High-water |",
        "|---|---|",
        "| python/a | 93 |",
        "| python/b | 91 |",
        "",
        "## Approvals",
        "",
        "| Experiment | Reviewer | Verdict | Self-approval |",
        "|---|---|---|---|",
        "| exp-2 | ada | VERIFIED | no |",
        "| exp-2 | leigh | VERIFIED | yes |",
        "",
        "## Adapter admission",
        "",
        "| Experiment | Adapter | Product type | Why not admitted |",
        "|---|---|---|---|",
        "| exp-2 | gaia | catalogue | no record |",
        "",
        "## Claims removed",
        "",
        "| Source | Product type | Why |",
        "|---|---|---|",
        "| gaia-dr3 | image | record failed |",
        "",
        "## Requirements",
        "",
        "Covered 4, failing 3, untested 2, draft 1.",
        "",
      ].join("\n"),
    );
  });
});
