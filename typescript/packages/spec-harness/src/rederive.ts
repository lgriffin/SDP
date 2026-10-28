// Independent verdict re-derivation (TST-007): recompute every check and the verdict from the manifest's
// frozen gates, findings and oracle records, and compare with what the kernel recorded.
import { hashDocument } from "@sdp/contracts";

import type { Manifest } from "./conform.ts";
import { decideVerdict, evaluateGate, type Check, type VerdictStatus } from "./gates.ts";

export interface Disagreement {
  subject: string;
  kernel: unknown;
  harness: unknown;
}

export interface Rederivation {
  experimentId: string;
  /** "unvalidated" when the kernel has issued no verdict yet: nothing to compare, nothing to publish. */
  state: "agree" | "disagree" | "unvalidated";
  kernelVerdict: VerdictStatus | null;
  harnessVerdict: VerdictStatus;
  checks: Check[];
  disagreements: Disagreement[];
  /** Publication is blocked unless both sides agree on a validated verdict. */
  publishable: boolean;
}

const sameCheck = (a: Check, b: Check): boolean =>
  a.gate_id === b.gate_id &&
  a.finding === b.finding &&
  a.value === b.value &&
  (a.unit ?? null) === (b.unit ?? null) &&
  a.comparator === b.comparator &&
  a.threshold === b.threshold &&
  a.severity === b.severity &&
  a.oracles_verified === b.oracles_verified &&
  a.outcome === b.outcome &&
  JSON.stringify(a.oracle_ids) === JSON.stringify(b.oracle_ids);

export function rederive(manifest: Manifest): Rederivation {
  const disagreements: Disagreement[] = [];
  const predeclaration = manifest.intent.predeclaration;
  if (manifest.intent.ledger_entry_id !== "none") {
    const hash = hashDocument(predeclaration);
    if (hash !== manifest.intent.predeclaration_hash) {
      disagreements.push({ subject: "predeclaration_hash", kernel: manifest.intent.predeclaration_hash, harness: hash });
    }
  }
  const oracles = (manifest as { oracles?: { oracle_id: string; verified: boolean }[] }).oracles ?? [];
  const checks = predeclaration.gates.map((gate) => evaluateGate(gate, manifest.findings, oracles));
  const harness = decideVerdict(checks);
  const kernelVerdict = manifest.verdict?.status ?? null;

  if (kernelVerdict !== null) {
    const recorded = new Map(manifest.checks.map((c) => [c.gate_id, c]));
    for (const check of checks) {
      const kernel = recorded.get(check.gate_id);
      if (!kernel || !sameCheck(kernel, check)) disagreements.push({ subject: check.gate_id, kernel: kernel ?? null, harness: check });
      recorded.delete(check.gate_id);
    }
    for (const [gateId, kernel] of recorded) {
      disagreements.push({ subject: gateId, kernel, harness: "gate not in the frozen predeclaration" });
    }
    if (kernelVerdict !== harness.status) disagreements.push({ subject: "verdict", kernel: kernelVerdict, harness: harness.status });
  }

  const state = kernelVerdict === null ? "unvalidated" : disagreements.length ? "disagree" : "agree";
  return {
    experimentId: manifest.identity.experiment_id,
    state,
    kernelVerdict,
    harnessVerdict: harness.status,
    checks,
    disagreements,
    publishable: state === "agree",
  };
}
