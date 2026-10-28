// The mutation-score ratchet (§11.5, TST-004): a package may not drop below its high-water mark minus 2 points.

export const ALLOWANCE = 2;

export interface RatchetResult {
  ok: boolean;
  violations: { pkg: string; score: number; highWater: number }[];
  highWater: Record<string, number>;
  /** Packages with a high-water mark but no score in this run; a missing score is a failure. */
  missing: string[];
}

export function ratchet(scores: Record<string, number>, highWater: Record<string, number>): RatchetResult {
  const violations: RatchetResult["violations"] = [];
  const next: Record<string, number> = { ...highWater };
  const missing = Object.keys(highWater).filter((pkg) => !(pkg in scores)).sort();
  for (const [pkg, score] of Object.entries(scores).sort(([a], [b]) => a.localeCompare(b))) {
    const mark = highWater[pkg];
    if (mark !== undefined && score < mark - ALLOWANCE) violations.push({ pkg, score, highWater: mark });
    next[pkg] = Math.max(score, mark ?? score);
  }
  return { ok: violations.length === 0 && missing.length === 0, violations, highWater: next, missing };
}
