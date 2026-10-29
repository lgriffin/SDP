// The mutation-score ratchet (§11.5, TST-004): a package may not drop below its high-water mark minus 2 points.

export const ALLOWANCE = 2;
/** The score every package works towards (docs/testing.md). Below it is reported, never failed: the
 * high-water mark is the gate, and it rises as packages climb towards the goal. */
export const GOAL = 90;

export interface RatchetResult {
  ok: boolean;
  violations: { pkg: string; score: number; highWater: number }[];
  highWater: Record<string, number>;
  /** Packages with a high-water mark but no score in this run; a missing score is a failure. */
  missing: string[];
  /** Packages scoring below the goal, lowest first: the work list, not a failure. */
  belowGoal: { pkg: string; score: number }[];
}

export function ratchet(
  scores: Record<string, number>,
  highWater: Record<string, number>,
  goal: number = GOAL,
): RatchetResult {
  const violations: RatchetResult["violations"] = [];
  const next: Record<string, number> = { ...highWater };
  const missing = Object.keys(highWater).filter((pkg) => !(pkg in scores)).sort();
  for (const [pkg, score] of Object.entries(scores).sort(([a], [b]) => a.localeCompare(b))) {
    const mark = highWater[pkg];
    if (mark !== undefined && score < mark - ALLOWANCE) violations.push({ pkg, score, highWater: mark });
    next[pkg] = Math.max(score, mark ?? score);
  }
  const belowGoal = Object.entries(scores)
    .filter(([, score]) => score < goal)
    .map(([pkg, score]) => ({ pkg, score }))
    .sort((a, b) => a.score - b.score || a.pkg.localeCompare(b.pkg));
  return { ok: violations.length === 0 && missing.length === 0, violations, highWater: next, missing, belowGoal };
}
