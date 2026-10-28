// The harness GateEvaluator: an independent TypeScript implementation of the verdict rule (§9, TST-007).
// It mirrors the documented semantics of sdp.kernel.domain.gates and .verdict, not their code.
import type { generated } from "@sdp/contracts";

export type Gate = generated.common.Gate;
export type Check = generated.common.Check;
export type CheckOutcome = generated.common.CheckOutcome;
export type VerdictStatus = generated.common.VerdictStatus;

export interface FindingValue {
  name: string;
  value: unknown;
  unit?: string | null;
}

export interface OracleRecord {
  oracle_id: string;
  verified: boolean;
}

const COMPARE: Record<Gate["comparator"], (a: number, b: number) => boolean> = {
  lt: (a, b) => a < b,
  le: (a, b) => a <= b,
  gt: (a, b) => a > b,
  ge: (a, b) => a >= b,
  eq: (a, b) => a === b,
  ne: (a, b) => a !== b,
};

/** Only numbers and booleans are gate-able; anything else counts as missing. */
function scalar(value: unknown): number | boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return null;
}

/**
 * Evaluate one gate, in the order the spec fixes:
 * 1. an absent or non-scalar finding is `missing`;
 * 2. a gate using any oracle without a verification record is `oracle_unverified`;
 * 3. a boolean threshold only matches a boolean value (and a number only a number), else `fail`;
 * 4. otherwise the comparator decides `pass` or `fail`.
 */
export function evaluateGate(gate: Gate, findings: readonly FindingValue[], oracles: readonly OracleRecord[]): Check {
  const finding = findings.find((f) => f.name === gate.finding);
  const value = finding ? scalar(finding.value) : null;
  const verified = new Map(oracles.map((o) => [o.oracle_id, o.verified]));
  const oraclesVerified = gate.oracle_ids.every((id) => verified.get(id) === true);
  let outcome: CheckOutcome;
  if (finding === undefined || value === null) outcome = "missing";
  else if (!oraclesVerified) outcome = "oracle_unverified";
  else if (typeof gate.threshold === "boolean" !== (typeof value === "boolean")) outcome = "fail";
  else outcome = COMPARE[gate.comparator](Number(value), Number(gate.threshold)) ? "pass" : "fail";
  return {
    gate_id: gate.gate_id,
    finding: gate.finding,
    value,
    unit: finding?.unit ?? null,
    comparator: gate.comparator,
    threshold: gate.threshold,
    severity: gate.severity,
    oracle_ids: [...gate.oracle_ids],
    oracles_verified: oraclesVerified,
    outcome,
  };
}

export interface VerdictDecision {
  status: VerdictStatus;
  reasons: string[];
}

/** blocking fail -> REJECTED; blocking missing/unverified -> INCONCLUSIVE; warning not passing -> CANDIDATE; else VERIFIED. */
export function decideVerdict(checks: readonly Check[]): VerdictDecision {
  if (!checks.length) return { status: "INCONCLUSIVE", reasons: ["no gates were evaluated"] };
  const blocking = checks.filter((c) => c.severity === "blocking");
  const failed = blocking.filter((c) => c.outcome === "fail");
  if (failed.length) return { status: "REJECTED", reasons: failed.map((c) => `blocking gate ${c.gate_id} failed`) };
  const open = blocking.filter((c) => c.outcome === "missing" || c.outcome === "oracle_unverified");
  if (open.length) {
    return {
      status: "INCONCLUSIVE",
      reasons: open.map((c) =>
        `blocking gate ${c.gate_id} ${c.outcome === "missing" ? "has no finding" : "uses an unverified oracle"}`,
      ),
    };
  }
  const warnings = checks.filter((c) => c.severity === "warning" && c.outcome !== "pass");
  if (warnings.length) return { status: "CANDIDATE", reasons: warnings.map((c) => `warning gate ${c.gate_id} did not pass`) };
  return { status: "VERIFIED", reasons: ["all gates passed"] };
}
