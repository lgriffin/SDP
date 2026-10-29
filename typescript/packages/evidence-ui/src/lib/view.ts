// Pure view logic, unit-tested without a browser.
import type { GateCheck, LedgerEntry } from "./ports.ts";

export type Tone = "good" | "bad" | "warn" | "neutral";

export function verdictTone(status: string | null | undefined): Tone {
  switch (status) {
    case "VERIFIED":
      return "good";
    case "REJECTED":
      return "bad";
    case "CANDIDATE":
    case "INCONCLUSIVE":
      return "warn";
    default:
      return "neutral";
  }
}

export function stateTone(state: string): Tone {
  if (state === "failed" || state === "refused") return "bad";
  if (state === "approved" || state === "published") return "good";
  return "neutral";
}

const SEVERITY_ORDER: Record<string, number> = { blocking: 0, warning: 1 };
const OUTCOME_ORDER: Record<string, number> = { fail: 0, missing: 1, oracle_unverified: 2, pass: 3 };

/** Blocking gates first, then the ones that did not pass, then by id: what a reviewer reads first. */
export function orderChecks(checks: readonly GateCheck[]): GateCheck[] {
  return [...checks].sort(
    (a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) ||
      (OUTCOME_ORDER[a.outcome] ?? 9) - (OUTCOME_ORDER[b.outcome] ?? 9) ||
      a.gateId.localeCompare(b.gateId),
  );
}

/**
 * The gates that decided the verdict, by the rule in §9: a failing blocking gate rejects; a blocking
 * gate that is missing or whose oracle is unverified makes it inconclusive; a warning gate that did not
 * pass makes it a candidate; otherwise every gate passed.
 */
export function decisiveChecks(checks: readonly GateCheck[], status: string): GateCheck[] {
  const blocking = checks.filter((c) => c.severity === "blocking");
  switch (status) {
    case "REJECTED":
      return blocking.filter((c) => c.outcome === "fail");
    case "INCONCLUSIVE":
      return blocking.filter((c) => c.outcome === "missing" || c.outcome === "oracle_unverified");
    case "CANDIDATE":
      return checks.filter((c) => c.severity === "warning" && c.outcome !== "pass");
    default:
      return [...checks];
  }
}

const COMPARATORS: Record<string, string> = { lt: "<", le: "≤", gt: ">", ge: "≥", eq: "=", ne: "≠" };

export function formatNumber(value: unknown): string {
  if (typeof value === "number") {
    if (Number.isInteger(value)) return String(value);
    const abs = Math.abs(value);
    return abs !== 0 && (abs < 1e-3 || abs >= 1e6) ? value.toExponential(3) : String(Number(value.toPrecision(4)));
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}

export function describeGate(check: GateCheck): string {
  const comparator = COMPARATORS[check.comparator] ?? check.comparator;
  const unit = check.unit ? ` ${check.unit}` : "";
  return `${check.finding} ${comparator} ${formatNumber(check.threshold)}${unit}`;
}

export function shortHash(value: string | null | undefined, length = 12): string {
  if (!value) return "—";
  const [prefix, hex] = value.includes(":") ? [value.slice(0, value.lastIndexOf(":") + 1), value.slice(value.lastIndexOf(":") + 1)] : ["", value];
  return hex.length > length ? `${prefix}${hex.slice(0, length)}…` : value;
}

export interface ChainProblem {
  entry: string;
  message: string;
}

/**
 * What the UI can check without the signatures: sequences are consecutive from 0 and every entry but
 * the first names a predecessor. Hash and signature verification belong to `sdp ledger verify`.
 */
export function chainProblems(entries: readonly LedgerEntry[]): ChainProblem[] {
  const problems: ChainProblem[] = [];
  const ordered = [...entries].sort((a, b) => a.sequence - b.sequence);
  ordered.forEach((entry, i) => {
    if (entry.sequence !== i) problems.push({ entry: entry.id, message: `sequence ${entry.sequence} at position ${i}` });
    if (i === 0 && entry.previousEntryHash) problems.push({ entry: entry.id, message: "first entry names a predecessor" });
    if (i > 0 && !entry.previousEntryHash) problems.push({ entry: entry.id, message: "no previous entry hash" });
  });
  return problems;
}

export function isSelfApproval(entry: LedgerEntry): boolean {
  const payload = entry.payload as { self_approval?: unknown } | null;
  return entry.kind === "approval" && payload?.self_approval === true;
}

/** Triage states: promoted is the only one that leaves exploration; parked waits; closed is final. */
export function investigationTone(state: string): Tone {
  switch (state) {
    case "promoted":
      return "good";
    case "parked":
      return "warn";
    default:
      return "neutral";
  }
}

export function stageTone(state: string): Tone {
  if (state === "completed") return "good";
  if (state === "failed") return "bad";
  return "neutral";
}

export interface TestResultDoc {
  test_name: string;
  test_version: string;
  statistic: number;
  p_value: number | null;
  effect_size: number | null;
  uncertainty: number | null;
  seed: number;
  subject: { object_key: string | null; structure_id: string | null; members: string[] };
  details: Record<string, unknown>;
  null?: NullResultDoc | null;
}

export interface NullResultDoc {
  test_name: string;
  test_version: string;
  trials: number;
  examined: number;
  false_positives: number;
  false_positive_rate: number;
  alpha: number;
  null_model: string;
}

/** A result is below the profile's alpha; the null comparison says how often the field alone does that. */
export function belowAlpha(result: TestResultDoc, alpha: number | undefined): boolean {
  return alpha !== undefined && result.p_value !== null && result.p_value < alpha;
}

export function formatPercent(rate: number): string {
  return `${formatNumber(rate * 100)}%`;
}

/** Every source whose read filled its row cap in any stage, with the cap (#59): the field held more than was read. */
export function rowCapReached(stages: readonly { summary?: unknown }[]): [string, number][] {
  const reached: Record<string, number> = {};
  for (const stage of stages) {
    const summary = stage.summary as Record<string, unknown> | null | undefined;
    const caps = summary?.row_cap_reached;
    if (caps && typeof caps === "object") Object.assign(reached, caps as Record<string, number>);
  }
  return Object.entries(reached).sort(([a], [b]) => a.localeCompare(b));
}
