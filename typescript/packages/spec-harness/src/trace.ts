// The traceability gate (§13, TST-001): .ears files + JUnit from both toolchains + predeclared gates
// -> traceability.json and traceability.md, and a list of violations that fail the build.
import type { Requirement } from "./ears.ts";
import type { TestResult } from "./junit.ts";

export type Coverage = "covered" | "failing" | "untested" | "out-of-scope" | "not-test" | "draft" | "retired";

export interface PredeclaredGates {
  predeclarationId: string;
  hash: string;
  gates: string[];
  /** Frozen predeclarations (hash in the ledger) must have an evaluating test for every gate. */
  frozen: boolean;
}

export interface TraceOptions {
  /** Tiers that ran in this CI stage; requirements none of whose tiers ran are out of scope. Omit for all. */
  tiers?: readonly string[];
}

export interface MatrixRow {
  id: string;
  status: Requirement["status"];
  verification: Requirement["verification"];
  tiers: string[];
  gates: string[];
  coverage: Coverage;
  tests: { id: string; outcome: TestResult["outcome"]; report: string }[];
  traces: string[];
  source: string;
}

export interface GateRow {
  gateId: string;
  predeclarationId: string;
  frozen: boolean;
  tests: string[];
}

export type ViolationKind = "untested-requirement" | "failing-requirement" | "unknown-marker" | "untested-gate";

export interface Violation {
  kind: ViolationKind;
  subject: string;
  message: string;
}

export interface Traceability {
  generatedFrom: { requirements: number; tests: number; reports: string[]; tiers: string[] | null };
  matrix: MatrixRow[];
  gates: GateRow[];
  violations: Violation[];
  warnings: Violation[];
  summary: Record<Coverage, number>;
}

/** Tiers whose names include a suffix (T5-dgx) count when their base tier (T5) ran, and vice versa. */
function tierRan(tier: string, ran: ReadonlySet<string>): boolean {
  return ran.has(tier) || ran.has(tier.split("-")[0] ?? tier);
}

export function trace(
  requirements: readonly Requirement[],
  tests: readonly TestResult[],
  predeclarations: readonly PredeclaredGates[] = [],
  options: TraceOptions = {},
): Traceability {
  const ran = options.tiers ? new Set(options.tiers) : null;
  const known = new Set(requirements.map((r) => r.id));
  const byRequirement = new Map<string, TestResult[]>();
  const violations: Violation[] = [];
  const warnings: Violation[] = [];

  for (const test of tests) {
    for (const id of test.requirements) {
      if (!known.has(id)) {
        violations.push({ kind: "unknown-marker", subject: test.id, message: `${test.id} marks ${id}, which no .ears file defines` });
        continue;
      }
      byRequirement.set(id, [...(byRequirement.get(id) ?? []), test]);
    }
  }

  const matrix: MatrixRow[] = requirements.map((r) => {
    const marked = (byRequirement.get(r.id) ?? []).filter((t) => t.outcome !== "skipped");
    const inScope = ran === null || r.tiers.length === 0 || r.tiers.some((t) => tierRan(t, ran));
    let coverage: Coverage;
    if (r.status === "retired") coverage = "retired";
    else if (r.status === "draft") coverage = "draft";
    else if (r.verification !== "test") coverage = "not-test";
    else if (marked.some((t) => t.outcome === "failed")) coverage = "failing";
    else if (marked.some((t) => t.outcome === "passed")) coverage = "covered";
    else coverage = inScope ? "untested" : "out-of-scope";

    if (coverage === "untested") {
      violations.push({
        kind: "untested-requirement",
        subject: r.id,
        message: `${r.id} is approved with verification test but no passing test marks it (tiers ${r.tiers.join(", ") || "none"})`,
      });
    }
    if (coverage === "failing") {
      const failing = marked.filter((t) => t.outcome === "failed").map((t) => t.id);
      violations.push({ kind: "failing-requirement", subject: r.id, message: `${r.id} has failing tests: ${failing.join(", ")}` });
    }
    return {
      id: r.id,
      status: r.status,
      verification: r.verification,
      tiers: r.tiers,
      gates: r.gates,
      coverage,
      tests: marked.map((t) => ({ id: t.id, outcome: t.outcome, report: t.report })),
      traces: r.traces,
      source: r.source,
    };
  });

  const gateTests = new Map<string, string[]>();
  for (const test of tests) {
    if (test.outcome !== "passed") continue;
    for (const g of test.gates) gateTests.set(g, [...(gateTests.get(g) ?? []), test.id]);
  }
  const gates: GateRow[] = predeclarations.flatMap((p) =>
    p.gates.map((gateId) => ({ gateId, predeclarationId: p.predeclarationId, frozen: p.frozen, tests: gateTests.get(gateId) ?? [] })),
  );
  for (const g of gates) {
    if (g.tests.length) continue;
    const v: Violation = {
      kind: "untested-gate",
      subject: g.gateId,
      message: `gate ${g.gateId} in ${g.predeclarationId} has no passing evaluating test`,
    };
    (g.frozen ? violations : warnings).push(v);
  }

  const summary = Object.fromEntries(
    (["covered", "failing", "untested", "out-of-scope", "not-test", "draft", "retired"] as Coverage[]).map((c) => [
      c,
      matrix.filter((m) => m.coverage === c).length,
    ]),
  ) as Record<Coverage, number>;

  return {
    generatedFrom: {
      requirements: requirements.length,
      tests: tests.length,
      reports: [...new Set(tests.map((t) => t.report))].sort(),
      tiers: options.tiers ? [...options.tiers] : null,
    },
    matrix,
    gates,
    violations,
    warnings,
    summary,
  };
}

export function renderTraceabilityMarkdown(t: Traceability): string {
  const lines = [
    "# Traceability",
    "",
    `Generated by \`spec-harness trace\` from ${t.generatedFrom.requirements} requirements and ${t.generatedFrom.tests} test results` +
      (t.generatedFrom.tiers ? ` (tiers ${t.generatedFrom.tiers.join(", ")}).` : "."),
    "",
    `Covered ${t.summary.covered} · failing ${t.summary.failing} · untested ${t.summary.untested} · out of scope ${t.summary["out-of-scope"]} · not test-verified ${t.summary["not-test"]} · draft ${t.summary.draft} · retired ${t.summary.retired}`,
    "",
  ];
  if (t.violations.length) {
    lines.push("## Violations", "");
    for (const v of t.violations) lines.push(`- **${v.kind}** ${v.message}`);
    lines.push("");
  }
  if (t.warnings.length) {
    lines.push("## Warnings", "");
    for (const v of t.warnings) lines.push(`- ${v.kind}: ${v.message}`);
    lines.push("");
  }
  lines.push("## Matrix", "", "| Requirement | Status | Tiers | Coverage | Tests |", "|---|---|---|---|---|");
  for (const row of t.matrix) {
    const tests = row.tests.map((x) => `\`${x.id}\`${x.outcome === "passed" ? "" : ` (${x.outcome})`}`).join("<br>") || "—";
    lines.push(`| ${row.id} | ${row.status} | ${row.tiers.join(", ") || "—"} | ${row.coverage} | ${tests} |`);
  }
  if (t.gates.length) {
    lines.push("", "## Gates", "", "| Gate | Predeclaration | Frozen | Tests |", "|---|---|---|---|");
    for (const g of t.gates) lines.push(`| ${g.gateId} | ${g.predeclarationId} | ${g.frozen ? "yes" : "no"} | ${g.tests.length || "—"} |`);
  }
  return lines.join("\n") + "\n";
}
