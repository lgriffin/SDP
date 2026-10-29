// One contract suite per harness port, run against its fake and its filesystem adapter (TST-003): both
// return the same documents in the same order, and the adapter skips what is not its kind of file.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  EarsDirectory,
  FeatureDirectory,
  JsonFiles,
  JUnitFiles,
  LedgerFiles,
  ManifestFiles,
  PredeclarationFiles,
  walk,
} from "../src/adapters/fs.ts";
import { admitAll, conformAll, predeclaredGates, rederiveAll, rederivePlanAll, runTrace } from "../src/harness.ts";
import type { LedgerEntryDocument } from "../src/ports.ts";
import {
  InMemoryLedger,
  InMemoryManifests,
  InMemoryPredeclarations,
  InMemoryRequirements,
  InMemoryTestReports,
  testResult,
} from "../src/testing/fakes.ts";
import { fixture } from "./helpers.ts";

let root = "";
const at = (...parts: string[]) => path.join(root, ...parts);
/** A path under the scratch root, with forward slashes whatever the platform. */
const rel = (file: string) => path.relative(root, file).split(path.sep).join("/");
const write = (rel: string, text: string) => {
  fs.mkdirSync(path.dirname(at(rel)), { recursive: true });
  fs.writeFileSync(at(rel), text);
};
const entry = (sequence: number, kind: LedgerEntryDocument["kind"] = "predeclaration"): LedgerEntryDocument => ({
  entry_id: `e${sequence}`,
  sequence,
  kind,
  subject_id: `s${sequence}`,
  subject_hash: `h${sequence}`,
  signer: "leigh",
});
const junit = (name: string) =>
  `<?xml version="1.0"?><testsuites><testsuite name="s"><testcase classname="c" name="${name}"></testcase></testsuite></testsuites>`;

beforeAll(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "harness-ports-"));
  write("tree/b.txt", "b");
  write("tree/a/c.txt", "c");
  write("tree/a/skip.md", "no");
  write("tree/node_modules/x.txt", "no");
  write("tree/.hidden/x.txt", "no");
  write("tree/.dot.txt", "no");
  write("ears/k.ears", "capability: K\n");
  write("ears/k.ears.bak", "no");
  write("features/a.feature", "Feature: A\n");
  write("features/a.feature.txt", "no");
  write("junit/b.xml", junit("second"));
  write("junit/a.xml", junit("first"));
  write("junit/a.xml.txt", "no");
  write("runs/r2/manifest.json", '{"n": 2}');
  write("runs/r1/manifest.json", '{"n": 1}');
  write("runs/r1/other.json", '{"n": 0}');
  write("docs/b.json", '{"n": 2}');
  write("docs/a.json", '{"n": 1}');
  write("docs/a.jsonl", "no");
  write("pre/x/predeclaration.json", '{"predeclaration_id": "pre-x"}');
  write("pre/x/notes.json", "{}");
  write("ledger/b.jsonl", `${JSON.stringify(entry(3))}\n\n  \n${JSON.stringify(entry(1))}\n`);
  write("ledger/a.jsonl", `${JSON.stringify(entry(2))}\n`);
  write("ledger/a.json", JSON.stringify(entry(9)));
});
afterAll(() => fs.rmSync(root, { recursive: true, force: true }));

describe("[TST-003] the filesystem walk", () => {
  it("[TST-003] lists accepted files in name order, skipping node_modules and dot entries", async () => {
    const files = await walk(at("tree"), (f) => f.endsWith(".txt"));
    expect(files.map((f) => rel(f))).toEqual(["tree/a/c.txt", "tree/b.txt"]);
  });

  it("[TST-003] a missing target holds nothing, and a file target is accepted or not by itself", async () => {
    expect(await walk(at("missing"), () => true)).toEqual([]);
    expect(await walk(at("tree/b.txt"), (f) => f.endsWith(".txt"))).toEqual([at("tree/b.txt")]);
    expect(await walk(at("tree/b.txt"), () => false)).toEqual([]);
  });
});

describe("[TST-003] RequirementSource", () => {
  it("[TST-003] the fake and the directory both return the .ears files with their text", async () => {
    const real = await new EarsDirectory(at("ears")).earsFiles();
    expect(real).toEqual([{ path: path.relative(process.cwd(), at("ears/k.ears")), text: "capability: K\n" }]);
    expect(await new InMemoryRequirements(real).earsFiles()).toEqual(real);
    expect(await new InMemoryRequirements().earsFiles()).toEqual([]);
  });

  it("[TST-001] the feature directory returns only .feature files", async () => {
    expect(await new FeatureDirectory(at("features")).featureFiles()).toEqual([
      { path: path.relative(process.cwd(), at("features/a.feature")), text: "Feature: A\n" },
    ]);
  });
});

describe("[TST-003] TestReportSource", () => {
  it("[TST-003] the fake and the JUnit files both return every result, report by report", async () => {
    const real = await new JUnitFiles([at("junit"), at("missing")]).results();
    expect(real.map((r) => [r.name, r.report])).toEqual([
      ["first", "a.xml"],
      ["second", "b.xml"],
    ]);
    expect(await new InMemoryTestReports(real).results()).toEqual(real);
    expect(await new InMemoryTestReports().results()).toEqual([]);
  });

  it("[TST-003] a fake test result defaults to a passing, unmarked test in the fake report", () => {
    expect(testResult({ name: "t" })).toEqual({
      id: "suite::t",
      name: "t",
      classname: "suite",
      outcome: "passed",
      requirements: [],
      gates: [],
      tiers: [],
      report: "fake.xml",
    });
    expect(testResult({ name: "t", classname: "k" })).toMatchObject({ id: "k::t", classname: "k" });
  });
});

describe("[TST-003] ManifestSource and DocumentSource", () => {
  it("[TST-003] manifest files are every manifest.json in a directory, or a file named directly", async () => {
    const real = await new ManifestFiles([at("runs"), at("runs/r1/other.json")]).manifests();
    expect(real.map((m) => [rel(m.path), m.document])).toEqual([
      ["runs/r1/manifest.json", { n: 1 }],
      ["runs/r2/manifest.json", { n: 2 }],
      ["runs/r1/other.json", { n: 0 }],
    ]);
    expect(await new InMemoryManifests(real).manifests()).toEqual(real);
    expect(await new InMemoryManifests().manifests()).toEqual([]);
  });

  it("[TST-003] JSON files are every .json under the targets", async () => {
    const docs = await new JsonFiles([at("docs"), at("missing")]).documents();
    expect(docs.map((d) => [rel(d.path), d.document])).toEqual([
      ["docs/a.json", { n: 1 }],
      ["docs/b.json", { n: 2 }],
    ]);
  });
});

describe("[TST-003] PredeclarationSource", () => {
  it("[TST-003] the fake and the files both return each predeclaration.json", async () => {
    const real = await new PredeclarationFiles(at("pre")).predeclarations();
    expect(real.map((p) => [rel(p.path), p.document])).toEqual([["pre/x/predeclaration.json", { predeclaration_id: "pre-x" }]]);
    expect(await new InMemoryPredeclarations(real).predeclarations()).toEqual(real);
    expect(await new InMemoryPredeclarations().predeclarations()).toEqual([]);
  });
});

describe("[TST-003] LedgerSource", () => {
  it("[TST-003] the fake and the JSON Lines files both return entries in sequence order, skipping blank lines", async () => {
    const real = await new LedgerFiles(at("ledger")).entries();
    expect(real.map((e) => e.sequence)).toEqual([1, 2, 3]);
    expect((await new InMemoryLedger([entry(3), entry(1), entry(2)]).entries()).map((e) => e.sequence)).toEqual([1, 2, 3]);
    expect(await new InMemoryLedger().entries()).toEqual([]);
  });
});

describe("[TST-001] harness use cases over the ports", () => {
  const verified = fixture("kernel/verified.json");

  it("[TST-001] a predeclaration is frozen only by a predeclaration entry for its hash", async () => {
    const pre = { predeclaration_id: "pre-1", gates: [{ gate_id: "G-1" }, { gate_id: "G-2" }] };
    const { hashDocument } = await import("@sdp/contracts");
    const hash = hashDocument(pre);
    const approval = { ...entry(1, "approval"), subject_hash: hash };
    const located = new InMemoryPredeclarations([
      { path: "p/predeclaration.json", document: pre },
      { path: "q/predeclaration.json", document: {} },
    ]);
    expect(await predeclaredGates(located, new InMemoryLedger([approval]))).toEqual([
      { predeclarationId: "pre-1", hash, gates: ["G-1", "G-2"], frozen: false },
      { predeclarationId: "q/predeclaration.json", hash: hashDocument({}), gates: [], frozen: false },
    ]);
    const frozen = await predeclaredGates(located, new InMemoryLedger([{ ...entry(2), subject_hash: hash }]));
    expect(frozen.map((g) => g.frozen)).toEqual([true, false]);
  });

  it("[TST-001] the trace reads gates only when both predeclarations and the ledger are given", async () => {
    const predeclarations = new InMemoryPredeclarations([{ path: "p", document: { predeclaration_id: "pre-1", gates: [{ gate_id: "G-1" }] } }]);
    const base = { requirements: new InMemoryRequirements(), tests: new InMemoryTestReports() };
    expect((await runTrace({ ...base, predeclarations })).traceability.gates).toEqual([]);
    expect((await runTrace({ ...base, ledger: new InMemoryLedger() })).traceability.gates).toEqual([]);
    expect((await runTrace({ ...base, predeclarations, ledger: new InMemoryLedger() })).traceability.gates).toHaveLength(1);
  });

  it("[TST-007] conforms, re-derives and admits every manifest with its path", async () => {
    const source = new InMemoryManifests([{ path: "v.json", document: verified }]);
    expect(await conformAll(source)).toEqual([{ path: "v.json", ok: true, major: 1, errors: [] }]);
    const [r] = await rederiveAll(source);
    expect(r).toMatchObject({ path: "v.json", state: "agree", experimentId: expect.any(String) });
    const [a] = await admitAll(source);
    expect(a).toEqual({ path: "v.json", experimentId: r?.experimentId, verdict: "VERIFIED", unadmitted: [] });
    const failed = new InMemoryManifests([{ path: "f.json", document: fixture("kernel/failed.json") }]);
    expect((await admitAll(failed))[0]?.verdict).toBeNull();
  });

  it("[TST-007] refuses a manifest that does not conform, naming its path and errors", async () => {
    const bad = new InMemoryManifests([{ path: "bad.json", document: { schema_version: "1.0.0" } }]);
    const [report] = await conformAll(bad);
    expect(report).toMatchObject({ path: "bad.json", ok: false });
    expect(report?.errors.length).toBeGreaterThan(0);
    const first = report?.errors[0];
    const expected = `bad.json does not conform: ${report?.errors.map((e) => `${e.path} ${e.message}`).join("; ")}`;
    expect(first).toBeDefined();
    await expect(rederiveAll(bad)).rejects.toThrow(expected);
    await expect(admitAll(bad)).rejects.toThrow(expected);
  });

  it("[INT-013] refuses an acquisition plan that is not valid, naming its path", async () => {
    await expect(rederivePlanAll({ documents: async () => [{ path: "plan.json", document: { nope: 1 } }] })).rejects.toThrow(
      /^plan\.json is not a valid acquisition-plan: {2}Unrecognized key\(s\) in object: 'nope'; \/approval Required; /,
    );
    expect(await rederivePlanAll({ documents: async () => [] })).toEqual([]);
  });
});
