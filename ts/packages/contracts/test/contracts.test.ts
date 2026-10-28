// T6 contract tests, TypeScript side. The Python suite runs the same fixtures through Pydantic.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { canonicalJson, hashDocument, parse, SCHEMAS, schemaMajor, validate, type ContractName } from "../src/index.ts";

const contracts = path.resolve(import.meta.dirname, "../../../../contracts");

function fixtures(kind: "valid" | "invalid"): [ContractName, string][] {
  const root = path.join(contracts, "fixtures", kind);
  return fs
    .readdirSync(root)
    .sort()
    .flatMap((name) =>
      fs
        .readdirSync(path.join(root, name))
        .filter((f) => f.endsWith(".json") && !f.endsWith(".expect.json"))
        .sort()
        .map((f): [ContractName, string] => [name as ContractName, path.join(root, name, f)]),
    );
}

const read = (file: string): unknown => JSON.parse(fs.readFileSync(file, "utf8"));

describe("[CTR-001] one source of truth", () => {
  it("[CTR-001] has a generated schema for every contract", () => {
    const names = fs
      .readdirSync(path.join(contracts, "schemas"))
      .map((f) => f.replace(".schema.json", ""))
      .filter((n) => n !== "common");
    expect(Object.keys(SCHEMAS).sort()).toEqual(names.sort());
  });
});

describe("[CTR-003] fixtures", () => {
  it.each(fixtures("valid"))("[CTR-003] accepts valid %s %s", (name, file) => {
    expect(validate(name, read(file))).toEqual([]);
    expect(() => parse(name, read(file))).not.toThrow();
  });

  it.each(fixtures("invalid"))("[CTR-003] rejects invalid %s %s at the expected path", (name, file) => {
    const expected = (read(file.replace(/\.json$/, ".expect.json")) as { path: string }).path;
    const paths = validate(name, read(file)).map((e) => e.path);
    expect(paths).toContain(expected);
  });
});

describe("[CTR-004] schema versions", () => {
  it("[CTR-004] reads the major version of every manifest fixture", () => {
    for (const [name, file] of fixtures("valid")) {
      if (name === "evidence-manifest") expect(schemaMajor(read(file) as { schema_version: string })).toBe(1);
    }
  });
});

describe("canonical JSON matches the Python kernel", () => {
  const vectors = read(path.join(contracts, "fixtures/canonical/vectors.json")) as {
    name: string;
    input: unknown;
    canonical: string;
    sha256: string;
  }[];
  it.each(vectors.map((v) => [v.name, v] as const))("reproduces vector %s", (_, v) => {
    expect(canonicalJson(v.input)).toBe(v.canonical);
    expect(hashDocument(v.input)).toBe(v.sha256);
  });
});

describe("[CTR-002] generated code is fresh", () => {
  it("[CTR-002] matches a fresh generation byte for byte", async () => {
    const { execFileSync } = await import("node:child_process");
    const os = await import("node:os");
    const out = fs.mkdtempSync(path.join(os.tmpdir(), "sdp-zod-"));
    const pkg = path.resolve(import.meta.dirname, "..");
    execFileSync(process.execPath, [path.join(pkg, "scripts/generate-zod.mjs")], { env: { ...process.env, SDP_ZOD_OUT: out } });
    const committed = path.join(pkg, "src/generated");
    const names = fs.readdirSync(committed).sort();
    expect(fs.readdirSync(out).sort()).toEqual(names);
    for (const name of names) {
      expect(fs.readFileSync(path.join(out, name), "utf8")).toBe(fs.readFileSync(path.join(committed, name), "utf8"));
    }
  });
});
