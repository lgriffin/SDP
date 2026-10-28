import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { admit, checkClaims, type CapabilityDescriptor, type CompatibilityRecord } from "../src/admit.ts";
import { readManifest, type Manifest } from "../src/conform.ts";
import { claimsAll } from "../src/harness.ts";
import { rederive } from "../src/rederive.ts";
import { buildReleaseReport, renderReleaseReport } from "../src/report.ts";
import { trace } from "../src/trace.ts";
import { clone, repo } from "./helpers.ts";

const contract = (name: string): unknown =>
  JSON.parse(fs.readFileSync(path.join(repo, "contracts/fixtures/valid", name), "utf8"));

const admitted = (): Manifest => {
  const r = readManifest(contract("evidence-manifest/synthetic-verified-1.3-admitted.json"));
  if (!r.ok) throw new Error(JSON.stringify(r.errors));
  return r.manifest;
};
const record = (): CompatibilityRecord => contract("adapter-compatibility/mast-image-passing.json") as CompatibilityRecord;
const mast = (): CapabilityDescriptor => contract("capability-descriptor/synthetic-mast.json") as CapabilityDescriptor;
const records = (m: Manifest) => m.environment.adapter_compatibility ?? [];

describe("[INT-016] the harness checks adapter admission before accepting a verdict", () => {
  it("[INT-016] accepts a verdict whose adapter has a passing record for the product type used", () => {
    const r = rederive(admitted());
    expect(r.unadmitted).toEqual([]);
    expect(r.state).toBe("agree");
    expect(r.publishable).toBe(true);
  });

  it("[INT-016] refuses a verdict when the record is missing", () => {
    const m = clone(admitted());
    m.environment.adapter_compatibility = [];
    const r = rederive(m);
    expect(r.unadmitted).toEqual([{ adapter: "mast-observations", productType: "image", reason: "no compatibility record" }]);
    expect(r.state).toBe("disagree");
    expect(r.publishable).toBe(false);
    const report = buildReleaseReport({ version: "t", claimedLevels: [], tests: [], traceability: trace([], []), rederivations: [r] });
    expect(report.ok).toBe(false);
    expect(report.blocking).toEqual([
      "exp-0001: verdict issued with mast-observations not admitted for image (no compatibility record)",
    ]);
    expect(renderReleaseReport(report)).toContain("## Adapter admission");
  });

  it("[INT-016] re-derives passing from the four results, not the record's own flag", () => {
    const m = clone(admitted());
    const rec = records(m)[0];
    if (!rec) throw new Error("fixture has no record");
    rec.results = rec.results.map((x) => (x.kind === "semantic" ? { ...x, passed: false } : x));
    expect(rec.passed).toBe(true);
    expect(admit(m)).toEqual([
      { adapter: "mast-observations", productType: "image", reason: "1.0.0: semantic did not pass although the record says passed" },
    ]);
  });

  it("[INT-016] a record for another product type does not admit this one", () => {
    const m = clone(admitted());
    const input = m.inputs[0];
    if (!input) throw new Error("fixture has no input");
    input.product_type = "spectrum";
    expect(admit(m).map((u) => u.productType)).toEqual(["spectrum"]);
  });

  it("[INT-016] an unvalidated manifest with an unadmitted adapter is unpublishable but not a disagreement", () => {
    const m = clone(admitted());
    m.environment.adapter_compatibility = [];
    m.verdict = null;
    m.checks = [];
    const r = rederive(m);
    expect(r.state).toBe("unvalidated");
    expect(r.unadmitted).toHaveLength(1);
    expect(r.publishable).toBe(false);
  });

  it("[INT-016] inputs not acquired through an adapter need no record", () => {
    const m = clone(admitted());
    for (const input of m.inputs) {
      delete input.adapter;
      delete input.product_type;
    }
    m.environment.adapter_compatibility = [];
    expect(admit(m)).toEqual([]);
  });
});

describe("[INT-002] the harness strips descriptor claims without a passing conformance suite", () => {
  it("[INT-002] keeps the image claim the record backs and removes the rest", () => {
    const check = checkClaims(mast(), [record()]);
    expect(check.removed.map((r) => r.productType)).toEqual(["cube", "spectrum", "timeseries"]);
    expect(check.stripped.product_types).toEqual(["image"]);
  });

  it("[INT-002] ignores the descriptor's own conformance map", () => {
    const d = clone(mast());
    if (d.adapter) d.adapter.conformance = { image: true, cube: true, spectrum: true, timeseries: true };
    expect(checkClaims(d, []).removed).toHaveLength(4);
  });

  it("[INT-002] needs the record for the descriptor's adapter version", () => {
    const d = clone(mast());
    if (d.adapter) d.adapter.version = "2.0.0";
    const removed = checkClaims(d, [record()]).removed.find((r) => r.productType === "image");
    expect(removed?.reason).toBe("records exist only for versions 1.0.0, not 2.0.0");
  });

  it("[INT-002] never asks previews or auxiliaries for admission, and a source with no adapter proves nothing", () => {
    const d = { ...clone(mast()), product_types: ["image", "preview", "auxiliary"], adapter: null } as CapabilityDescriptor;
    const check = checkClaims(d, [record()]);
    expect(check.removed).toEqual([{ productType: "image", reason: "no SDP adapter serves this source" }]);
    expect(check.stripped.product_types).toEqual(["preview", "auxiliary"]);
  });

  it("[INT-002] fails the release report when a claim is removed", async () => {
    const located = (document: unknown) => ({ documents: async () => [{ path: "x.json", document }] });
    const claims = await claimsAll(located(mast()), located(record()));
    const report = buildReleaseReport({ version: "t", claimedLevels: [], tests: [], traceability: trace([], []), rederivations: [], claims });
    expect(report.ok).toBe(false);
    expect(report.blocking).toHaveLength(3);
    expect(renderReleaseReport(report)).toContain("## Claims removed");
  });

  it("[INT-002] rejects a descriptor that does not conform to its contract", async () => {
    const bad = { documents: async () => [{ path: "bad.json", document: { source_id: "x" } }] };
    await expect(claimsAll(bad, { documents: async () => [] })).rejects.toThrow("bad.json is not a valid capability-descriptor");
  });
});
