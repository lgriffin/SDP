import fc from "fast-check";
import { describe, expect, it } from "vitest";

import { readManifest, type Manifest } from "../src/conform.ts";
import { decideVerdict, evaluateGate, type Check, type Gate } from "../src/gates.ts";
import { rederive } from "../src/rederive.ts";
import { clone, fixture } from "./helpers.ts";

const manifest = (name: string): Manifest => {
  const result = readManifest(fixture(`kernel/${name}.json`));
  if (!result.ok) throw new Error(JSON.stringify(result.errors));
  return result.manifest;
};

describe("[TST-007] independent verdict re-derivation", () => {
  it.each(["verified", "rejected", "candidate"])("[TST-007] agrees with the kernel on its %s manifest", (name) => {
    const r = rederive(manifest(name));
    expect(r.disagreements).toEqual([]);
    expect(r.state).toBe("agree");
    expect(r.harnessVerdict).toBe(name.toUpperCase());
    expect(r.publishable).toBe(true);
  });

  it("[TST-007] treats a failed run as unvalidated and unpublishable", () => {
    const r = rederive(manifest("failed"));
    expect(r.state).toBe("unvalidated");
    expect(r.publishable).toBe(false);
  });

  it("[TST-007] blocks publication when the kernel's verdict differs", () => {
    const tampered = clone(manifest("rejected"));
    if (tampered.verdict) tampered.verdict.status = "VERIFIED";
    const r = rederive(tampered);
    expect(r.state).toBe("disagree");
    expect(r.publishable).toBe(false);
    expect(r.disagreements.map((d) => d.subject)).toContain("verdict");
  });

  it("[TST-007] catches a finding changed after validation", () => {
    const tampered = clone(manifest("verified"));
    const finding = tampered.findings.find((f) => f.name === "slope_abs_error");
    if (finding) finding.value = 0.5;
    const r = rederive(tampered);
    expect(r.state).toBe("disagree");
    expect(r.harnessVerdict).toBe("REJECTED");
  });

  it("[TST-007] catches a predeclaration edited after freezing", () => {
    const tampered = clone(manifest("verified"));
    const gate = tampered.intent.predeclaration.gates[0];
    if (gate) gate.threshold = 1.0;
    expect(rederive(tampered).disagreements.map((d) => d.subject)).toContain("predeclaration_hash");
  });

  it("[TST-007] never trusts an oracle the manifest does not record as verified", () => {
    const tampered = clone(manifest("verified")) as Manifest & { oracles?: unknown[] };
    delete tampered.oracles;
    const r = rederive(tampered);
    expect(r.harnessVerdict).toBe("INCONCLUSIVE");
    expect(r.state).toBe("disagree");
  });
});

const gateArb: fc.Arbitrary<Gate> = fc.record({
  gate_id: fc.integer({ min: 1, max: 99 }).map((n) => `PRP-G-${String(n).padStart(2, "0")}`),
  description: fc.constant("property gate"),
  finding: fc.constantFrom("a", "b", "c"),
  comparator: fc.constantFrom("lt", "le", "gt", "ge", "eq", "ne"),
  threshold: fc.oneof(fc.double({ noNaN: true, noDefaultInfinity: true }), fc.boolean()),
  severity: fc.constantFrom("blocking", "warning"),
  oracle_ids: fc.uniqueArray(fc.constantFrom("o1", "o2"), { maxLength: 2 }),
});
const findingsArb = fc.array(
  fc.record({ name: fc.constantFrom("a", "b", "c"), value: fc.oneof(fc.double({ noNaN: true, noDefaultInfinity: true }), fc.boolean(), fc.string()) }),
  { maxLength: 3 },
);
const oraclesArb = fc.array(fc.record({ oracle_id: fc.constantFrom("o1", "o2"), verified: fc.boolean() }), { maxLength: 2 });

describe("GateEvaluator properties (T2)", () => {
  it("never issues VERIFIED when a blocking gate fails or uses an unverified oracle", () => {
    fc.assert(
      fc.property(fc.array(gateArb, { minLength: 1, maxLength: 5 }), findingsArb, oraclesArb, (gates, findings, oracles) => {
        const checks = gates.map((g) => evaluateGate(g, findings, oracles));
        const { status } = decideVerdict(checks);
        const blocking = checks.filter((c) => c.severity === "blocking");
        if (blocking.some((c) => c.outcome === "fail")) expect(status).toBe("REJECTED");
        if (blocking.some((c) => c.oracle_ids.length && !c.oracles_verified)) expect(status).not.toBe("VERIFIED");
      }),
    );
  });

  it("does not depend on gate order", () => {
    fc.assert(
      fc.property(fc.array(gateArb, { minLength: 1, maxLength: 5 }), findingsArb, oraclesArb, (gates, findings, oracles) => {
        const forward = decideVerdict(gates.map((g) => evaluateGate(g, findings, oracles))).status;
        const backward = decideVerdict([...gates].reverse().map((g) => evaluateGate(g, findings, oracles))).status;
        expect(backward).toBe(forward);
      }),
    );
  });

  it("treats a non-scalar finding as missing", () => {
    const gate: Gate = { gate_id: "PRP-G-01", description: "d", finding: "a", comparator: "lt", threshold: 1, severity: "blocking", oracle_ids: [] };
    const check: Check = evaluateGate(gate, [{ name: "a", value: "text" }], []);
    expect(check.outcome).toBe("missing");
  });
});
