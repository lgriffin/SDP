// The independent verdict rule's exact records: every comparator at its boundary, every check field,
// every verdict reason, every disagreement re-derivation records and every admission reason (TST-007, INT-016).
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { admit, checkClaims, NON_SCIENCE, type CapabilityDescriptor, type CompatibilityRecord } from "../src/admit.ts";
import { readManifest, type Manifest } from "../src/conform.ts";
import { decideVerdict, evaluateGate, type Check, type Gate } from "../src/gates.ts";
import { rederive } from "../src/rederive.ts";
import { clone, fixture, repo } from "./helpers.ts";

const gate = (partial: Partial<Gate> = {}): Gate => ({
  gate_id: "SYN-G-01",
  description: "d",
  finding: "x",
  comparator: "lt",
  threshold: 1,
  severity: "blocking",
  oracle_ids: [],
  ...partial,
});

const check = (gate_id: string, outcome: Check["outcome"], severity: Check["severity"] = "blocking"): Check => ({
  gate_id,
  finding: "x",
  value: 1,
  unit: null,
  comparator: "lt",
  threshold: 1,
  severity,
  oracle_ids: [],
  oracles_verified: true,
  outcome,
});

const outcome = (g: Gate, value: unknown): Check["outcome"] => evaluateGate(g, [{ name: "x", value }], []).outcome;

describe("[TST-007] gate evaluation", () => {
  it.each([
    ["lt", ["pass", "fail", "fail"]],
    ["le", ["pass", "pass", "fail"]],
    ["gt", ["fail", "fail", "pass"]],
    ["ge", ["fail", "pass", "pass"]],
    ["eq", ["fail", "pass", "fail"]],
    ["ne", ["pass", "fail", "pass"]],
  ] as const)("[TST-007] %s decides below, at and above its threshold", (comparator, expected) => {
    expect([0.5, 1, 1.5].map((v) => outcome(gate({ comparator }), v))).toEqual(expected);
  });

  it("[TST-007] a boolean threshold matches only a boolean value, and a number only a number", () => {
    expect(outcome(gate({ comparator: "eq", threshold: true }), true)).toBe("pass");
    expect(outcome(gate({ comparator: "eq", threshold: false }), false)).toBe("pass");
    expect(outcome(gate({ comparator: "eq", threshold: true }), 1)).toBe("fail");
    expect(outcome(gate({ comparator: "eq", threshold: 1 }), true)).toBe("fail");
  });

  it("[TST-007] a non-finite, non-scalar or absent finding is missing", () => {
    for (const value of [Number.POSITIVE_INFINITY, Number.NaN, "1", null, [1]]) expect(outcome(gate({ comparator: "ne" }), value)).toBe("missing");
    expect(evaluateGate(gate(), [{ name: "other", value: 0 }], []).outcome).toBe("missing");
  });

  it("[TST-007] records every field of the check, with the finding's unit", () => {
    const g = gate({ comparator: "ge", threshold: 2, severity: "warning", oracle_ids: ["o-1"] });
    const findings = [{ name: "y", value: 9 }, { name: "x", value: 3, unit: "mag" }];
    expect(evaluateGate(g, findings, [{ oracle_id: "o-1", verified: true }])).toEqual({
      gate_id: "SYN-G-01",
      finding: "x",
      value: 3,
      unit: "mag",
      comparator: "ge",
      threshold: 2,
      severity: "warning",
      oracle_ids: ["o-1"],
      oracles_verified: true,
      outcome: "pass",
    });
    expect(evaluateGate(g, [{ name: "x", value: 3 }], [{ oracle_id: "o-1", verified: false }])).toMatchObject({
      unit: null,
      oracles_verified: false,
      outcome: "oracle_unverified",
    });
  });
});

describe("[TST-007] verdict decision", () => {
  it("[TST-007] no checks is inconclusive", () => {
    expect(decideVerdict([])).toEqual({ status: "INCONCLUSIVE", reasons: ["no gates were evaluated"] });
  });

  it("[TST-007] names every failed blocking gate", () => {
    expect(decideVerdict([check("G-1", "fail"), check("G-2", "missing"), check("G-3", "fail"), check("G-4", "fail", "warning")])).toEqual({
      status: "REJECTED",
      reasons: ["blocking gate G-1 failed", "blocking gate G-3 failed"],
    });
  });

  it("[TST-007] names every open blocking gate and why it is open", () => {
    expect(decideVerdict([check("G-1", "missing"), check("G-2", "pass"), check("G-3", "oracle_unverified"), check("G-4", "missing", "warning")])).toEqual({
      status: "INCONCLUSIVE",
      reasons: ["blocking gate G-1 has no finding", "blocking gate G-3 uses an unverified oracle"],
    });
  });

  it("[TST-007] names every warning gate that did not pass", () => {
    expect(decideVerdict([check("G-1", "pass"), check("G-2", "fail", "warning"), check("G-3", "pass", "warning"), check("G-4", "missing", "warning")])).toEqual({
      status: "CANDIDATE",
      reasons: ["warning gate G-2 did not pass", "warning gate G-4 did not pass"],
    });
  });

  it("[TST-007] all gates passing is verified", () => {
    expect(decideVerdict([check("G-1", "pass"), check("G-2", "pass", "warning")])).toEqual({ status: "VERIFIED", reasons: ["all gates passed"] });
  });
});

const verified = (): Manifest => {
  const r = readManifest(fixture("kernel/verified.json"));
  if (!r.ok) throw new Error(JSON.stringify(r.errors));
  return clone(r.manifest);
};

describe("[TST-007] re-derivation disagreements", () => {
  const recorded = (m: Manifest): Check => {
    const c = m.checks[0];
    if (!c) throw new Error("fixture has no check");
    return c;
  };

  it.each([
    ["gate_id", (c: Check) => void (c.gate_id = "SYN-G-09")],
    ["finding", (c: Check) => void (c.finding = "other")],
    ["value", (c: Check) => void (c.value = 0.004)],
    ["unit", (c: Check) => void (c.unit = "mag")],
    ["comparator", (c: Check) => void (c.comparator = "le")],
    ["threshold", (c: Check) => void (c.threshold = 0.06)],
    ["severity", (c: Check) => void (c.severity = "warning")],
    ["oracles_verified", (c: Check) => void (c.oracles_verified = false)],
    ["outcome", (c: Check) => void (c.outcome = "fail")],
    ["oracle_ids", (c: Check) => void (c.oracle_ids = ["planted-slope", "x"])],
  ])("[TST-007] a recorded check with a different %s disagrees with the harness", (_field, change) => {
    const m = verified();
    change(recorded(m));
    const r = rederive(m);
    expect(r.state).toBe("disagree");
    expect(r.disagreements.map((d) => d.subject)).toContain("SYN-G-01");
  });

  it("[TST-007] a recorded unit of null and an absent one are the same", () => {
    const m = verified();
    delete (recorded(m) as { unit?: string | null }).unit;
    expect(rederive(m).disagreements).toEqual([]);
  });

  it("[TST-007] records a missing check and an extra one, with what each side has", () => {
    const m = verified();
    const [first, second] = m.checks;
    if (!first || !second) throw new Error("fixture has two checks");
    m.checks = [{ ...second, gate_id: "SYN-G-07" }, second];
    const r = rederive(m);
    expect(r.disagreements).toEqual([
      { subject: "SYN-G-01", kernel: null, harness: r.checks[0] },
      { subject: "SYN-G-07", kernel: { ...second, gate_id: "SYN-G-07" }, harness: "gate not in the frozen predeclaration" },
    ]);
    expect(first.gate_id).toBe("SYN-G-01");
  });

  it("[TST-007] records the verdict both sides reached", () => {
    const m = verified();
    if (m.verdict) m.verdict.status = "CANDIDATE";
    expect(rederive(m).disagreements).toEqual([{ subject: "verdict", kernel: "CANDIDATE", harness: "VERIFIED" }]);
  });

  it("[TST-007] records both predeclaration hashes, unless the experiment was never frozen in the ledger", () => {
    const m = verified();
    const frozenHash = m.intent.predeclaration_hash;
    m.intent.predeclaration_hash = "sha256:" + "0".repeat(64);
    const r = rederive(m);
    expect(r.disagreements).toEqual([{ subject: "predeclaration_hash", kernel: "sha256:" + "0".repeat(64), harness: frozenHash }]);
    m.intent.ledger_entry_id = "none";
    expect(rederive(m).disagreements).toEqual([]);
  });

  it("[TST-007] an unvalidated run records no disagreement however its checks look", () => {
    const m = verified();
    m.verdict = null;
    m.checks = [];
    expect(rederive(m)).toMatchObject({ state: "unvalidated", kernelVerdict: null, harnessVerdict: "VERIFIED", disagreements: [], publishable: false });
  });
});

const contract = (name: string): unknown => JSON.parse(fs.readFileSync(path.join(repo, "contracts/fixtures/valid", name), "utf8"));
const admitted = (): Manifest => {
  const r = readManifest(contract("evidence-manifest/synthetic-verified-1.4-admitted.json"));
  if (!r.ok) throw new Error(JSON.stringify(r.errors));
  return clone(r.manifest);
};
const input = (m: Manifest) => {
  const i = m.inputs[0];
  if (!i) throw new Error("fixture has no input");
  return i;
};
const rec = (m: Manifest): CompatibilityRecord => {
  const r = m.environment.adapter_compatibility?.[0];
  if (!r) throw new Error("fixture has no record");
  return r;
};

describe("[INT-016] admission records", () => {
  it("[INT-016] records the admission disagreement with its adapter, version and product type", () => {
    const m = admitted();
    m.environment.adapter_compatibility = [];
    expect(rederive(m).disagreements).toEqual([
      { subject: "admission mast-observations/1.0.0/image", kernel: "verdict issued", harness: "not admitted: no compatibility record" },
    ]);
  });

  it("[INT-016] names an input with no version ? in the disagreement", () => {
    const m = admitted();
    delete input(m).adapter_version;
    expect(rederive(m).disagreements.map((d) => d.subject)).toEqual(["admission mast-observations/?/image"]);
  });

  it("[INT-016] a record for another adapter does not admit this one", () => {
    const m = admitted();
    rec(m).adapter = "other-adapter";
    expect(admit(m).map((u) => u.reason)).toEqual(["no compatibility record"]);
  });

  it("[INT-016] lists every version with a record, and every failing record's reasons", () => {
    const m = admitted();
    const r = rec(m);
    m.environment.adapter_compatibility = [
      { ...r, adapter_version: "0.9.0" },
      { ...r, adapter_version: "0.8.0" },
    ];
    expect(admit(m).map((u) => u.reason)).toEqual(["records exist only for versions 0.9.0, 0.8.0, not 1.0.0"]);
    m.environment.adapter_compatibility = [
      { ...r, passed: false, results: r.results.map((x) => ({ ...x, passed: x.kind === "contract" })) },
      { ...r, passed: false, results: r.results.filter((x) => x.kind !== "scientific") },
    ];
    expect(admit(m).map((u) => u.reason)).toEqual(["1.0.0: operational, semantic, scientific did not pass; 1.0.0: scientific did not pass"]);
  });

  it("[INT-016] one passing record admits whatever else is recorded", () => {
    const m = admitted();
    const r = rec(m);
    m.environment.adapter_compatibility = [{ ...r, passed: false, results: [] }, r];
    expect(admit(m)).toEqual([]);
  });

  it("[INT-016] reports each unadmitted triple once, in adapter/version/product order", () => {
    const m = admitted();
    m.environment.adapter_compatibility = [];
    const first = input(m);
    m.inputs = [
      { ...first, adapter: "zeta", product_type: "image" },
      { ...first, adapter: "alpha", product_type: "spectrum" },
      { ...first, adapter: "alpha", product_type: "image" },
      { ...first, adapter: "alpha", product_type: "image" },
      { ...first, adapter: "alpha", adapter_version: "0.1.0", product_type: "image" },
    ];
    expect(admit(m).map((u) => `${u.adapter}/${u.adapterVersion}/${u.productType}`)).toEqual([
      "alpha/0.1.0/image",
      "alpha/1.0.0/image",
      "alpha/1.0.0/spectrum",
      "zeta/1.0.0/image",
    ]);
  });

  it("[INT-016] an input that names only an adapter version is unadmitted", () => {
    const m = admitted();
    const i = input(m);
    delete i.adapter;
    delete i.product_type;
    expect(admit(m)).toEqual([
      { adapter: "", adapterVersion: "1.0.0", productType: "", reason: "input names a product type or version but no adapter" },
    ]);
  });

  it("[INT-016] an input that names only a product type is unadmitted", () => {
    const m = admitted();
    const i = input(m);
    delete i.adapter;
    delete i.adapter_version;
    expect(admit(m)).toEqual([
      { adapter: "", adapterVersion: null, productType: "image", reason: "input names a product type or version but no adapter" },
    ]);
  });
});

describe("[INT-002] claim records", () => {
  const mast = (): CapabilityDescriptor => clone(contract("capability-descriptor/synthetic-mast.json") as CapabilityDescriptor);
  const record = (): CompatibilityRecord => contract("adapter-compatibility/mast-image-passing.json") as CompatibilityRecord;

  it("[INT-002] previews, thumbnails and auxiliaries are never science", () => {
    expect([...NON_SCIENCE].sort()).toEqual(["auxiliary", "preview", "thumbnail"]);
  });

  it("[INT-002] checks claims in product-type order and names the adapter and its version", () => {
    const d = { ...mast(), product_types: ["timeseries", "image", "cube"] } as CapabilityDescriptor;
    const c = checkClaims(d, [record()]);
    expect(c.removed.map((r) => r.productType)).toEqual(["cube", "timeseries"]);
    expect(c.adapter).toBe(`${d.adapter?.name} ${d.adapter?.version}`);
    expect(c.adapter).toMatch(/^\S+ \S+$/);
    expect(c.stripped.product_types).toEqual(["image"]);
  });
});
