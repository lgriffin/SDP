import fs from "node:fs";
import path from "node:path";

import { hashDocument } from "@sdp/contracts";
import { describe, expect, it } from "vitest";

import { JsonFiles } from "../src/adapters/fs.ts";
import { rederivePlanAll } from "../src/harness.ts";
import { rederivePlan, selectItems, type PlanDocument, type PlanItem } from "../src/rederive-plan.ts";
import { clone, repo } from "./helpers.ts";

const dir = path.join(repo, "contracts/fixtures/valid/acquisition-plan");
const plan = (name: string): PlanDocument => JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), "utf8")) as PlanDocument;
/** Re-address a tampered plan so only the tampering, not a stale id, disagrees. */
const rehash = (p: PlanDocument): PlanDocument => {
  const { plan_id: _, approval, ...intent } = p;
  const planId = hashDocument(intent);
  return { ...intent, plan_id: planId, approval: approval ? { ...approval, plan_id: planId } : approval } as PlanDocument;
};

describe("[INT-013] the harness re-derives a plan from what it records", () => {
  it("[INT-013] agrees with every committed plan, re-deriving items where candidates are recorded", async () => {
    const results = await rederivePlanAll(new JsonFiles([dir]));
    expect(results.map((r) => [path.basename(r.path), r.state, r.itemsRederived])).toEqual([
      ["synthetic-plan-drafted.json", "agree", false],
      ["synthetic-plan-signed-approval.json", "agree", false],
      ["synthetic-plan-with-candidates.json", "agree", true],
      ["synthetic-plan-with-gap.json", "agree", true],
    ]);
    expect(results.flatMap((r) => r.disagreements)).toEqual([]);
  });

  it("[INT-013] a plan whose content does not hash to its id disagrees", () => {
    const p = clone(plan("synthetic-plan-with-candidates"));
    p.items[0]!.licence = "proprietary";
    expect(rederivePlan(p).disagreements.map((d) => d.subject)).toContain("plan_id");
  });

  it("[INT-013] an item the candidates do not justify, or one dropped, disagrees", () => {
    const p = clone(plan("synthetic-plan-with-candidates"));
    const dropped = p.items.pop()!;
    p.cost.total_bytes -= dropped.expected_bytes;
    if (--p.cost.requests_per_source[dropped.source_id]! === 0) delete p.cost.requests_per_source[dropped.source_id];
    const r = rederivePlan(rehash(p));
    expect(r.state).toBe("disagree");
    expect(r.disagreements.map((d) => d.subject)).toEqual([`item ${dropped.item_id}`]);
  });

  it("[INT-013] a gap with another reason disagrees", () => {
    const p = clone(plan("synthetic-plan-with-gap"));
    p.gaps[0]!.reason = "no source in the profile lists anything for it";
    expect(rederivePlan(rehash(p)).disagreements.map((d) => d.subject)).toEqual([`gap ${p.gaps[0]!.requirement_id}`]);
  });

  it("[INT-013] a cost that is not the sum of its items disagrees", () => {
    const p = clone(plan("synthetic-plan-signed-approval"));
    p.cost.total_bytes += 1;
    p.cost.requests_per_source["synthetic-gaia"] = 2;
    expect(rederivePlan(rehash(p)).disagreements.map((d) => d.subject)).toEqual(["cost.total_bytes", "cost.requests_per_source"]);
  });

  it("[INT-014] an automatic approval above the threshold, or an approval without its ledger entry, disagrees", () => {
    const auto = clone(plan("synthetic-plan-signed-approval"));
    auto.approval = { ...auto.approval!, decision: "auto_approved", ledger_entry_id: null };
    expect(rederivePlan(auto).disagreements.map((d) => d.subject)).toEqual(["approval"]);
    const unsigned = clone(plan("synthetic-plan-signed-approval"));
    unsigned.approval!.ledger_entry_id = null;
    expect(rederivePlan(unsigned).disagreements.map((d) => d.subject)).toEqual(["approval"]);
    const other = clone(plan("synthetic-plan-signed-approval"));
    other.approval = { ...other.approval!, plan_id: "sha256:" + "0".repeat(64), threshold_bytes: 1 };
    expect(rederivePlan(other).disagreements.map((d) => d.subject)).toEqual(["approval.plan_id", "approval.threshold_bytes"]);
    const refused = clone(plan("synthetic-plan-signed-approval"));
    refused.approval = { ...refused.approval!, decision: "refused", ledger_entry_id: null, reason: " " };
    expect(rederivePlan(refused).disagreements.map((d) => d.subject)).toEqual(["approval.reason"]);
  });

  it("[INT-013] null candidates, or an item recorded twice, disagree", () => {
    const nulled = clone(plan("synthetic-plan-with-candidates"));
    (nulled.constraints as Record<string, unknown>).candidates = null;
    const r = rederivePlan(rehash(nulled));
    expect([r.itemsRederived, r.disagreements.map((d) => d.subject)]).toEqual([false, ["constraints.candidates"]]);
    const twice = clone(plan("synthetic-plan-with-candidates"));
    twice.items.push(twice.items[0]!);
    expect(rederivePlan(rehash(twice)).disagreements.map((d) => d.subject)).toContain(`item ${twice.items[0]!.item_id}`);
  });

  it("[INT-013] counts requests for any source name", () => {
    const p = clone(plan("synthetic-plan-signed-approval"));
    const [source, n] = Object.entries(p.cost.requests_per_source)[0]!;
    for (const i of p.items) if (i.source_id === source) i.source_id = "__proto__";
    const rest = Object.entries(p.cost.requests_per_source).filter(([s]) => s !== source);
    p.cost.requests_per_source = Object.fromEntries([["__proto__", n], ...rest]) as Record<string, number>;
    expect(rederivePlan(rehash(p)).disagreements).toEqual([]);
  });
});

describe("[INT-013] selectItems mirrors the kernel's minimum product set", () => {
  const item = (source_id: string, requirement_id: string, target: string, operation = "download"): PlanItem => ({
    item_id: "candidate",
    source_id,
    operation,
    target,
    expected_bytes: 1,
    requirement_id,
  });
  const requirements = [{ requirement_id: "r-b" }, { requirement_id: "r-a" }, { requirement_id: "r-none" }];
  const matches = [
    { source_id: "s1", requirement_id: "r-b", status: "partial" },
    { source_id: "s1", requirement_id: "r-a", status: "satisfied" },
    { source_id: "s2", requirement_id: "r-a", status: "unsatisfied" },
  ];

  it("[INT-013] plans each acquisition once, under the first requirement, numbered by source, operation and target", () => {
    const { items, gaps } = selectItems(
      requirements,
      matches,
      [item("s1", "r-a", "t1"), item("s1", "r-b", "t1"), item("s1", "r-a", "T0"), item("s2", "r-a", "t9"), item("s1", "r-x", "t5")],
    );
    expect(items.map((i) => [i.item_id, i.target, i.requirement_id])).toEqual([
      ["item-001", "T0", "r-a"],
      ["item-002", "t1", "r-b"],
    ]);
    expect(gaps).toEqual([{ requirement_id: "r-none", reason: "no source in the profile lists anything for it" }]);
  });

  it("[INT-013] a gap names every source that listed something and why it did not serve", () => {
    const { items, gaps } = selectItems(requirements.slice(1, 2), matches.slice(2), [item("s3", "r-a", "t"), item("s2", "r-a", "t")]);
    expect(items).toEqual([]);
    expect(gaps).toEqual([{ requirement_id: "r-a", reason: "no source that lists it satisfies it: s2 (unsatisfied), s3 (no registered descriptor)" }]);
  });

  it("[INT-013] orders by code point and keeps tuple fields apart", () => {
    const m = [{ source_id: "s", requirement_id: "r-a", status: "satisfied" }];
    const { items } = selectItems([{ requirement_id: "r-a" }], m, [item("s", "r-a", "\u{10000}"), item("s", "r-a", "\uE000")]);
    expect(items.map((i) => i.target)).toEqual(["\uE000", "\u{10000}"]);
    const joined = selectItems(
      [{ requirement_id: "r" }],
      [{ source_id: "a\u0000b", requirement_id: "r", status: "satisfied" }],
      [item("a", "r", "x", "b\u0000download"), item("a\u0000b", "r", "x")],
    );
    expect(joined.items.map((i) => i.source_id)).toEqual(["a\u0000b"]);
  });
});
