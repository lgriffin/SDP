// The traceability gate's exact records: every violation message, every matrix row and the rendered
// markdown, so a changed classification or a dropped line fails a test (TST-001).
import { describe, expect, it } from "vitest";

import type { Requirement } from "../src/ears.ts";
import { testResult } from "../src/testing/fakes.ts";
import { renderTraceabilityMarkdown, trace } from "../src/trace.ts";

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

describe("[TST-001] traceability records", () => {
  it("[TST-001] names a marker no .ears file defines and still counts the known one", () => {
    const t = trace([requirement("KRN-001")], [testResult({ name: "a", requirements: ["NOPE-1", "KRN-001"] })]);
    expect(t.violations).toEqual([{ kind: "unknown-marker", subject: "suite::a", message: "suite::a marks NOPE-1, which no .ears file defines" }]);
    expect(t.matrix[0]?.coverage).toBe("covered");
  });

  it("[TST-001] keeps every marking test but the skipped ones, in order, with its outcome and report", () => {
    const tests = [
      testResult({ name: "a", requirements: ["KRN-001"], report: "a.xml" }),
      testResult({ name: "s", requirements: ["KRN-001"], outcome: "skipped" }),
      testResult({ name: "f", requirements: ["KRN-001"], outcome: "failed", report: "f.xml" }),
      testResult({ name: "b", requirements: ["KRN-001"], outcome: "failed", report: "b.xml" }),
    ];
    const t = trace([requirement("KRN-001", { tiers: ["T1", "T2"], gates: ["G-1"], traces: ["§3"], source: "k.ears" })], tests);
    expect(t.matrix).toEqual([
      {
        id: "KRN-001",
        status: "approved",
        verification: "test",
        tiers: ["T1", "T2"],
        gates: ["G-1"],
        coverage: "failing",
        tests: [
          { id: "suite::a", outcome: "passed", report: "a.xml" },
          { id: "suite::f", outcome: "failed", report: "f.xml" },
          { id: "suite::b", outcome: "failed", report: "b.xml" },
        ],
        traces: ["§3"],
        source: "k.ears",
      },
    ]);
    expect(t.violations).toEqual([{ kind: "failing-requirement", subject: "KRN-001", message: "KRN-001 has failing tests: suite::f, suite::b" }]);
  });

  it("[TST-001] a requirement marked only by skipped tests is untested, naming its tiers or none", () => {
    const skipped = testResult({ name: "s", requirements: ["KRN-001", "KRN-002"], outcome: "skipped" });
    const t = trace([requirement("KRN-001", { tiers: ["T1", "T3"] }), requirement("KRN-002", { tiers: [] })], [skipped]);
    expect(t.matrix.map((m) => [m.coverage, m.tests])).toEqual([
      ["untested", []],
      ["untested", []],
    ]);
    expect(t.violations.map((v) => v.message)).toEqual([
      "KRN-001 is approved with verification test but no passing test marks it (tiers T1, T3)",
      "KRN-002 is approved with verification test but no passing test marks it (tiers none)",
    ]);
  });

  it("[TST-001] a requirement with no tiers is in scope of every stage; one with any tier that ran is too", () => {
    const t = trace([requirement("KRN-001", { tiers: [] }), requirement("KRN-002", { tiers: ["T9", "T1"] })], [], [], { tiers: ["T1"] });
    expect(t.matrix.map((m) => m.coverage)).toEqual(["untested", "untested"]);
  });

  it("[TST-001] counts only passing tests toward a gate and orders the reports", () => {
    const tests = [
      testResult({ name: "p", gates: ["G-1"], report: "z.xml" }),
      testResult({ name: "f", gates: ["G-1", "G-2"], outcome: "failed", report: "a.xml" }),
      testResult({ name: "q", gates: ["G-1"], report: "z.xml" }),
    ];
    const t = trace([], tests, [
      { predeclarationId: "pre-1", hash: "h", gates: ["G-1", "G-2"], frozen: true },
      { predeclarationId: "pre-2", hash: "h", gates: ["G-3"], frozen: false },
    ], { tiers: ["T1", "T2"] });
    expect(t.gates).toEqual([
      { gateId: "G-1", predeclarationId: "pre-1", frozen: true, tests: ["suite::p", "suite::q"] },
      { gateId: "G-2", predeclarationId: "pre-1", frozen: true, tests: [] },
      { gateId: "G-3", predeclarationId: "pre-2", frozen: false, tests: [] },
    ]);
    expect(t.violations).toEqual([{ kind: "untested-gate", subject: "G-2", message: "gate G-2 in pre-1 has no passing evaluating test" }]);
    expect(t.warnings).toEqual([{ kind: "untested-gate", subject: "G-3", message: "gate G-3 in pre-2 has no passing evaluating test" }]);
    expect(t.generatedFrom).toEqual({ requirements: 0, tests: 3, reports: ["a.xml", "z.xml"], tiers: ["T1", "T2"] });
  });

  it("[TST-001] records no tier filter as null", () => {
    expect(trace([], []).generatedFrom).toEqual({ requirements: 0, tests: 0, reports: [], tiers: null });
  });
});

describe("[TST-001] traceability markdown", () => {
  it("[TST-001] renders a clean run with only the summary and the matrix", () => {
    const t = trace([requirement("KRN-001", { tiers: [] }), requirement("KRN-002", { status: "draft" })], [
      testResult({ name: "a", requirements: ["KRN-001"] }),
    ]);
    expect(renderTraceabilityMarkdown(t)).toBe(
      [
        "# Traceability",
        "",
        "Generated by `spec-harness trace` from 2 requirements and 1 test results.",
        "",
        "Covered 1 · failing 0 · untested 0 · out of scope 0 · not test-verified 0 · draft 1 · retired 0",
        "",
        "## Matrix",
        "",
        "| Requirement | Status | Tiers | Coverage | Tests |",
        "|---|---|---|---|---|",
        "| KRN-001 | approved | — | covered | `suite::a` |",
        "| KRN-002 | draft | T1 | draft | — |",
        "",
      ].join("\n"),
    );
  });

  it("[TST-001] renders violations, warnings, every coverage count, test outcomes and gates", () => {
    const requirements = [
      requirement("A-1", { tiers: ["T1", "T2"] }),
      requirement("A-2"),
      requirement("A-3", { tiers: ["T5"] }),
      requirement("A-4", { verification: "analysis" }),
      requirement("A-5", { status: "retired" }),
      requirement("A-6", { status: "draft" }),
      requirement("A-7"),
    ];
    const tests = [
      testResult({ name: "ok", requirements: ["A-1", "A-7"], gates: ["G-1"] }),
      testResult({ name: "bad", requirements: ["A-1"], outcome: "failed" }),
    ];
    const t = trace(requirements, tests, [{ predeclarationId: "pre-1", hash: "h", gates: ["G-1", "G-2"], frozen: false }], {
      tiers: ["T1", "T2"],
    });
    expect(renderTraceabilityMarkdown(t)).toBe(
      [
        "# Traceability",
        "",
        "Generated by `spec-harness trace` from 7 requirements and 2 test results (tiers T1, T2).",
        "",
        "Covered 1 · failing 1 · untested 1 · out of scope 1 · not test-verified 1 · draft 1 · retired 1",
        "",
        "## Violations",
        "",
        "- **failing-requirement** A-1 has failing tests: suite::bad",
        "- **untested-requirement** A-2 is approved with verification test but no passing test marks it (tiers T1)",
        "",
        "## Warnings",
        "",
        "- untested-gate: gate G-2 in pre-1 has no passing evaluating test",
        "",
        "## Matrix",
        "",
        "| Requirement | Status | Tiers | Coverage | Tests |",
        "|---|---|---|---|---|",
        "| A-1 | approved | T1, T2 | failing | `suite::ok`<br>`suite::bad` (failed) |",
        "| A-2 | approved | T1 | untested | — |",
        "| A-3 | approved | T5 | out-of-scope | — |",
        "| A-4 | approved | T1 | not-test | — |",
        "| A-5 | retired | T1 | retired | — |",
        "| A-6 | draft | T1 | draft | — |",
        "| A-7 | approved | T1 | covered | `suite::ok` |",
        "",
        "## Gates",
        "",
        "| Gate | Predeclaration | Frozen | Tests |",
        "|---|---|---|---|",
        "| G-1 | pre-1 | no | 1 |",
        "| G-2 | pre-1 | no | — |",
        "",
      ].join("\n"),
    );
  });

  it("[TST-001] marks a frozen gate yes", () => {
    const t = trace([], [], [{ predeclarationId: "pre-1", hash: "h", gates: ["G-1"], frozen: true }]);
    expect(renderTraceabilityMarkdown(t)).toContain("| G-1 | pre-1 | yes | — |");
  });
});
