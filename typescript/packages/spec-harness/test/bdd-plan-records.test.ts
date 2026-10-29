// The exact records of the Gherkin binder and the plan re-derivation: every parsed line, every refusal
// and every disagreement, with the edges of each pattern (TST-010, INT-013, INT-014).
import fs from "node:fs";
import path from "node:path";

import { hashDocument } from "@sdp/contracts";
import { describe, expect, it } from "vitest";

import { checkFeatures, parseFeature } from "../src/bdd.ts";
import type { Requirement } from "../src/ears.ts";
import { rederivePlan, selectItems, type PlanDocument, type PlanItem } from "../src/rederive-plan.ts";
import { clone, repo } from "./helpers.ts";

describe("[TST-010] feature parsing", () => {
  it("[TST-010] records each Rule and scenario with its line, trimmed, and skips comments", () => {
    const text = [
      "Feature: F",
      "  # Rule: KRN-001: commented out",
      "  Scenario:orphan  ",
      "  Rule:KRN-001: The kernel shall work.   ",
      "    Scenario: one  ",
      "    Example:two",
      "    Scenario Outline: three",
      "    Scenario Template: four",
      "    #   Scenario: commented",
      "  Rule: see KRN-002: not a title",
      "    Scenario: five",
    ].join("\n");
    expect(parseFeature({ path: "f.feature", text })).toEqual({
      hasFeature: true,
      orphans: [{ name: "orphan", line: 3 }],
      rules: [
        {
          source: "f.feature",
          line: 4,
          title: "KRN-001: The kernel shall work.",
          id: "KRN-001",
          scenarios: [
            { name: "one", line: 5 },
            { name: "two", line: 6 },
            { name: "three", line: 7 },
            { name: "four", line: 8 },
          ],
        },
        { source: "f.feature", line: 10, title: "see KRN-002: not a title", scenarios: [{ name: "five", line: 11 }] },
      ],
    });
  });

  it("[TST-010] a Feature: keyword counts only at the start of a line", () => {
    expect(parseFeature({ path: "f", text: "Not a Feature: x\nxFeature: y\n" }).hasFeature).toBe(false);
    expect(parseFeature({ path: "f", text: "\r\n   Feature: x\r\n" }).hasFeature).toBe(true);
  });

  it("[TST-010] reports problems file by file in path order", () => {
    const requirement: Requirement = {
      id: "KRN-001",
      pattern: "ubiquitous",
      text: "The kernel shall work.",
      rationale: "",
      verification: "test",
      tiers: [],
      gates: [],
      status: "approved",
      traces: [],
      capability: "K",
      owner: "kernel",
      source: "k.ears",
    };
    const report = checkFeatures(
      [
        { path: "b.feature", text: "Feature: B\n  Rule: KRN-001: The kernel shall work.\n" },
        { path: "a.feature", text: "  Rule: KRN-001: The kernel shall work.\n    Scenario: s\n" },
      ],
      [requirement],
    );
    expect(report.problems).toEqual([
      { source: "a.feature", line: 1, message: "no Feature: line" },
      { source: "b.feature", line: 2, message: "Rule KRN-001 has no scenario, so it verifies nothing" },
    ]);
    expect(report.rules.map((r) => r.source)).toEqual(["a.feature", "b.feature"]);
    expect(report.coverage).toEqual({ "KRN-001": 1 });
  });
});

describe("[INT-013] plan selection ranks by requirement order", () => {
  const item = (requirement_id: string): PlanItem => ({
    item_id: "candidate",
    source_id: "s",
    operation: "download",
    target: "t",
    expected_bytes: 1,
    requirement_id,
  });
  const requirements = Array.from({ length: 11 }, (_, n) => ({ requirement_id: `r${n}` }));
  const matches = requirements.map((r) => ({ source_id: "s", requirement_id: r.requirement_id, status: "satisfied" }));

  it("[INT-013] an acquisition two requirements share is planned under the earlier one, by number not text", () => {
    expect(selectItems(requirements, matches, [item("r10"), item("r9")]).items.map((i) => i.requirement_id)).toEqual(["r9"]);
    expect(selectItems(requirements, matches, [item("r2"), item("r1")]).items.map((i) => i.requirement_id)).toEqual(["r1"]);
  });
});

describe("[INT-013] plan re-derivation records", () => {
  const dir = path.join(repo, "contracts/fixtures/valid/acquisition-plan");
  const plan = (name: string): PlanDocument => clone(JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), "utf8")) as PlanDocument);

  it("[INT-013] records an item recorded twice, a missing one and an extra one, with each side", () => {
    const p = plan("synthetic-plan-with-candidates");
    const [first, ...rest] = p.items;
    if (!first) throw new Error("fixture has items");
    const extra = { ...first, item_id: "item-999" };
    p.items = [first, first, ...rest.slice(1), extra];
    const r = rederivePlan(p);
    const missing = rest[0];
    expect(r.disagreements.filter((d) => d.subject.startsWith("item "))).toEqual([
      { subject: `item ${first.item_id}`, kernel: first, harness: "recorded more than once" },
      ...(missing ? [{ subject: `item ${missing.item_id}`, kernel: null, harness: missing }] : []),
      { subject: "item item-999", kernel: extra, harness: "not in the re-derived set" },
    ]);
  });

  it("[INT-013] records candidates that are not a list", () => {
    const p = plan("synthetic-plan-with-candidates");
    (p.constraints as Record<string, unknown>).candidates = { not: "a list" };
    expect(rederivePlan(p).disagreements).toContainEqual({
      subject: "constraints.candidates",
      kernel: { not: "a list" },
      harness: "a list of candidate items, or absent",
    });
  });

  it("[INT-014] records each approval disagreement with both sides", () => {
    const unsigned = plan("synthetic-plan-signed-approval");
    unsigned.approval = { ...unsigned.approval!, ledger_entry_id: null };
    expect(rederivePlan(unsigned).disagreements).toEqual([
      { subject: "approval", kernel: "approved", harness: "an approval above the threshold names its signed ledger entry" },
    ]);
    const refused = plan("synthetic-plan-signed-approval");
    const { reason: _, ...withoutReason } = refused.approval!;
    refused.approval = { ...withoutReason, decision: "refused", ledger_entry_id: null } as PlanDocument["approval"];
    expect(rederivePlan(refused).disagreements).toEqual([{ subject: "approval.reason", kernel: null, harness: "a refusal states its reason" }]);
    const stated = plan("synthetic-plan-signed-approval");
    stated.approval = { ...stated.approval!, decision: "refused", ledger_entry_id: null, reason: "too big" };
    expect(rederivePlan(stated).disagreements).toEqual([]);
    const auto = plan("synthetic-plan-signed-approval");
    auto.approval = { ...auto.approval!, decision: "auto_approved", ledger_entry_id: null };
    expect(rederivePlan(auto).disagreements).toEqual([
      {
        subject: "approval",
        kernel: "auto_approved",
        harness: `${auto.cost.total_bytes} bytes is above the approval threshold of 10000000; a signed approval is required`,
      },
    ]);
  });

  it("[INT-014] an automatic approval exactly at the threshold stands", () => {
    const p = plan("synthetic-plan-signed-approval");
    const threshold = p.cost.total_bytes;
    p.constraints.budget.approval_threshold_bytes = threshold;
    const { plan_id: _, approval, ...intent } = p;
    const planId = hashDocument(intent);
    const rehashed = { ...intent, plan_id: planId, approval: { ...approval!, plan_id: planId, decision: "auto_approved", ledger_entry_id: null, threshold_bytes: threshold } };
    expect(rederivePlan(rehashed as PlanDocument).disagreements).toEqual([]);
  });
});
