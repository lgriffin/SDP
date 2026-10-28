// The release report (§11.6, TST-006, TST-007): gates, re-derived verdicts, mutation scores, reproduction
// result and approvals. It refuses to claim a reproducibility level whose tiers did not pass in this run.
import type { TestResult } from "./junit.ts";
import type { RatchetResult } from "./ratchet.ts";
import type { Rederivation } from "./rederive.ts";
import type { Traceability } from "./trace.ts";

export type TierStatus = "passed" | "failed" | "not-run";

/** Level 1 computational, 2 analytical, 3 scientific, 4 independent evidence (0.2.0 §13.4). */
export const LEVEL_TIERS: Record<1 | 2 | 3 | 4, readonly string[]> = {
  1: ["T9"],
  2: ["T7", "T8"],
  3: ["T8"],
  4: [],
};

export const LEVEL_NAMES: Record<1 | 2 | 3 | 4, string> = {
  1: "computational",
  2: "analytical (independent implementation)",
  3: "scientific (survives alternative assumptions)",
  4: "independent evidence",
};

export type Level = keyof typeof LEVEL_TIERS;

export interface Approval {
  experimentId: string;
  reviewer: string;
  verdict: string;
  selfApproval: boolean;
  entryId: string;
}

export interface ReleaseInputs {
  version: string;
  claimedLevels: readonly Level[];
  tests: readonly TestResult[];
  /** Results from stages that do not write JUnit, e.g. {"T9": "passed"} from reproduce.sh. */
  externalTiers?: Record<string, TierStatus>;
  traceability: Traceability;
  rederivations: readonly Rederivation[];
  mutation?: RatchetResult;
  approvals?: readonly Approval[];
  /** Citations of independent confirmations; Level 4 needs at least one and is never inferred from tiers. */
  independentEvidence?: readonly string[];
}

export interface LevelClaim {
  level: Level;
  name: string;
  granted: boolean;
  reason: string;
}

export interface ReleaseReport {
  version: string;
  ok: boolean;
  tiers: Record<string, TierStatus>;
  levels: LevelClaim[];
  blocking: string[];
  rederivations: readonly Rederivation[];
  traceability: Traceability["summary"];
  mutation: RatchetResult | null;
  approvals: readonly Approval[];
}

export function tierStatuses(tests: readonly TestResult[], external: Record<string, TierStatus> = {}): Record<string, TierStatus> {
  const out: Record<string, TierStatus> = {};
  for (const test of tests) {
    if (test.outcome === "skipped") continue;
    for (const tier of test.tiers) {
      if (test.outcome === "failed") out[tier] = "failed";
      else if (out[tier] !== "failed") out[tier] = "passed";
    }
  }
  for (const [tier, status] of Object.entries(external)) {
    if (out[tier] !== "failed") out[tier] = status;
  }
  return out;
}

export function buildReleaseReport(inputs: ReleaseInputs): ReleaseReport {
  const tiers = tierStatuses(inputs.tests, inputs.externalTiers);
  const blocking: string[] = [];
  const levels: LevelClaim[] = [...new Set(inputs.claimedLevels)].sort().map((level) => {
    const needed = LEVEL_TIERS[level];
    if (level === 4) {
      const granted = (inputs.independentEvidence ?? []).length > 0;
      return {
        level,
        name: LEVEL_NAMES[level],
        granted,
        reason: granted ? `cites ${inputs.independentEvidence?.length} independent confirmation(s)` : "no independent evidence is cited",
      };
    }
    const missing = needed.filter((t) => tiers[t] !== "passed");
    return {
      level,
      name: LEVEL_NAMES[level],
      granted: missing.length === 0,
      reason: missing.length
        ? `needs ${missing.map((t) => `${t} (${tiers[t] ?? "not-run"})`).join(", ")}`
        : `${needed.join(", ")} passed in this run`,
    };
  });
  for (const claim of levels) if (!claim.granted) blocking.push(`Level ${claim.level} claimed but ${claim.reason}`);
  for (const r of inputs.rederivations) {
    if (r.state === "disagree") blocking.push(`${r.experimentId}: harness re-derived ${r.harnessVerdict}, kernel issued ${r.kernelVerdict}`);
  }
  for (const v of inputs.traceability.violations) blocking.push(`traceability: ${v.message}`);
  if (inputs.mutation && !inputs.mutation.ok) {
    for (const v of inputs.mutation.violations) blocking.push(`mutation: ${v.pkg} scored ${v.score}, high-water ${v.highWater}`);
    for (const pkg of inputs.mutation.missing) blocking.push(`mutation: no score for ${pkg}`);
  }
  return {
    version: inputs.version,
    ok: blocking.length === 0,
    tiers,
    levels,
    blocking,
    rederivations: inputs.rederivations,
    traceability: inputs.traceability.summary,
    mutation: inputs.mutation ?? null,
    approvals: inputs.approvals ?? [],
  };
}

export function renderReleaseReport(report: ReleaseReport): string {
  const lines = [
    `# Release report ${report.version}`,
    "",
    report.ok ? "**Status: releasable.**" : "**Status: blocked.**",
    "",
  ];
  if (report.blocking.length) lines.push("## Blocking", "", ...report.blocking.map((b) => `- ${b}`), "");
  lines.push("## Reproducibility levels", "", "| Level | Name | Claimed | Why |", "|---|---|---|---|");
  for (const l of report.levels) lines.push(`| ${l.level} | ${l.name} | ${l.granted ? "granted" : "refused"} | ${l.reason} |`);
  lines.push("", "## Tiers", "", "| Tier | Result |", "|---|---|");
  for (const [tier, status] of Object.entries(report.tiers).sort(([a], [b]) => a.localeCompare(b, "en", { numeric: true }))) {
    lines.push(`| ${tier} | ${status} |`);
  }
  lines.push("", "## Verdicts", "", "| Experiment | Kernel | Harness | State |", "|---|---|---|---|");
  for (const r of report.rederivations) lines.push(`| ${r.experimentId} | ${r.kernelVerdict ?? "—"} | ${r.harnessVerdict} | ${r.state} |`);
  if (report.mutation) {
    lines.push("", "## Mutation scores (high-water marks)", "", "| Package | High-water |", "|---|---|");
    for (const [pkg, score] of Object.entries(report.mutation.highWater).sort()) lines.push(`| ${pkg} | ${score} |`);
  }
  if (report.approvals.length) {
    lines.push("", "## Approvals", "", "| Experiment | Reviewer | Verdict | Self-approval |", "|---|---|---|---|");
    for (const a of report.approvals) lines.push(`| ${a.experimentId} | ${a.reviewer} | ${a.verdict} | ${a.selfApproval ? "yes" : "no"} |`);
  }
  const t = report.traceability;
  lines.push("", "## Requirements", "", `Covered ${t.covered}, failing ${t.failing}, untested ${t.untested}, draft ${t.draft}.`);
  return lines.join("\n") + "\n";
}
