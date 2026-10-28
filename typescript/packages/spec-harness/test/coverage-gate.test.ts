import { describe, expect, it } from "vitest";

import type { Requirement } from "../src/ears.ts";
import { runTrace } from "../src/harness.ts";
import { InMemoryLedger, InMemoryPredeclarations, InMemoryRequirements, InMemoryTestReports, testResult } from "../src/testing/fakes.ts";
import { renderTraceabilityMarkdown, trace } from "../src/trace.ts";
import { hashDocument } from "@sdp/contracts";

const requirement = (id: string, overrides: Partial<Requirement> = {}): Requirement => ({
  id,
  pattern: "ubiquitous",
  text: "The kernel shall work.",
  rationale: "",
  verification: "test",
  tiers: ["T1"],
  gates: [],
  status: "approved",
  traces: [],
  capability: "X",
  owner: "kernel",
  source: "x.ears",
  ...overrides,
});

describe("[TST-001] requirement coverage gate", () => {
  it("[TST-001] fails an approved test requirement with no passing marker", () => {
    const t = trace([requirement("KRN-001"), requirement("KRN-002")], [testResult({ name: "a", requirements: ["KRN-001"] })]);
    expect(t.matrix.map((m) => [m.id, m.coverage])).toEqual([
      ["KRN-001", "covered"],
      ["KRN-002", "untested"],
    ]);
    expect(t.violations.map((v) => [v.kind, v.subject])).toEqual([["untested-requirement", "KRN-002"]]);
  });

  it("[TST-001] counts failing and skipped tests as not covering", () => {
    const t = trace(
      [requirement("KRN-001"), requirement("KRN-002")],
      [
        testResult({ name: "a", requirements: ["KRN-001"], outcome: "failed" }),
        testResult({ name: "b", requirements: ["KRN-002"], outcome: "skipped" }),
      ],
    );
    expect(t.violations.map((v) => v.kind).sort()).toEqual(["failing-requirement", "untested-requirement"]);
  });

  it("[TST-001] fails a marker that names no requirement", () => {
    const t = trace([requirement("KRN-001")], [testResult({ name: "a", requirements: ["KRN-001", "KRN-777"] })]);
    expect(t.violations).toEqual([expect.objectContaining({ kind: "unknown-marker" })]);
  });

  it("[TST-001] ignores drafts, retired and non-test requirements", () => {
    const t = trace(
      [requirement("KRN-001", { status: "draft" }), requirement("KRN-002", { status: "retired" }), requirement("KRN-003", { verification: "inspection" })],
      [],
    );
    expect(t.violations).toEqual([]);
    expect(t.matrix.map((m) => m.coverage)).toEqual(["draft", "retired", "not-test"]);
  });

  it("[TST-001] scopes the gate to the tiers that ran", () => {
    const reqs = [requirement("KRN-006", { tiers: ["T5"] }), requirement("KRN-013", { tiers: ["T5-dgx"] }), requirement("KRN-001")];
    const fast = trace(reqs, [testResult({ name: "a", requirements: ["KRN-001"] })], [], { tiers: ["T0", "T1", "T2"] });
    expect(fast.violations).toEqual([]);
    expect(fast.summary["out-of-scope"]).toBe(2);
    const merge = trace(reqs, [testResult({ name: "a", requirements: ["KRN-001"] })], [], { tiers: ["T5"] });
    expect(merge.violations.map((v) => v.subject)).toEqual(["KRN-006", "KRN-013"]);
  });

  it("[TST-001] requires a passing evaluating test for every gate of a frozen predeclaration", async () => {
    const draft = { predeclaration_id: "draft", gates: [{ gate_id: "REL-G-01" }] };
    const frozen = { predeclaration_id: "frozen", gates: [{ gate_id: "SYN-G-01" }, { gate_id: "SYN-G-02" }] };
    const { traceability } = await runTrace({
      requirements: new InMemoryRequirements([]),
      tests: new InMemoryTestReports([testResult({ name: "g", gates: ["SYN-G-01"] })]),
      predeclarations: new InMemoryPredeclarations([
        { path: "a/predeclaration.json", document: draft },
        { path: "b/predeclaration.json", document: frozen },
      ]),
      ledger: new InMemoryLedger([
        { entry_id: "e1", sequence: 0, kind: "predeclaration", subject_id: "frozen", subject_hash: hashDocument(frozen), signer: "leigh" },
      ]),
    });
    expect(traceability.violations.map((v) => v.subject)).toEqual(["SYN-G-02"]);
    expect(traceability.warnings.map((v) => v.subject)).toEqual(["REL-G-01"]);
    expect(renderTraceabilityMarkdown(traceability)).toContain("| SYN-G-01 | frozen | yes | 1 |");
  });
});
