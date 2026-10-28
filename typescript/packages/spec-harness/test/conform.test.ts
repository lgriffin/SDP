import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { MANIFEST_READERS, readManifest, SUPPORTED_MAJORS } from "../src/conform.ts";
import { conformAll } from "../src/harness.ts";
import { InMemoryManifests } from "../src/testing/fakes.ts";
import { clone, fixture, repo } from "./helpers.ts";

describe("[CTR-004] manifest conformance across schema versions", () => {
  it("[CTR-004] reads every valid manifest fixture, 1.0 and 1.1", () => {
    const dir = path.join(repo, "contracts/fixtures/valid/evidence-manifest");
    for (const name of fs.readdirSync(dir)) {
      const result = readManifest(JSON.parse(fs.readFileSync(path.join(dir, name), "utf8")));
      expect(result.ok, name).toBe(true);
    }
  });

  it("[CTR-004] supports the current major and declares the previous one", () => {
    expect(SUPPORTED_MAJORS).toEqual([1, 0]);
    expect([...MANIFEST_READERS.keys()]).toEqual([1]);
  });

  it("[CTR-004] refuses a major it does not support, naming the path", () => {
    const future = clone(fixture("kernel/verified.json")) as Record<string, unknown>;
    future.schema_version = "2.0.0";
    const result = readManifest(future);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors[0]?.path).toBe("/schema_version");
  });

  it("[CTR-004] reports schema violations as JSON pointers", async () => {
    const broken = clone(fixture("kernel/verified.json")) as { execution: { seed: unknown } };
    broken.execution.seed = "forty-two";
    const [report] = await conformAll(new InMemoryManifests([{ path: "m.json", document: broken }]));
    expect(report?.ok).toBe(false);
    expect(report?.errors.map((e) => e.path)).toContain("/execution/seed");
  });
});
