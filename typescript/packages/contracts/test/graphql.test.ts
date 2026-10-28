import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { graphql } from "../src/index.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../../..");
const schemaFile = path.join(root, "contracts/graphql/schema.graphql");

describe("GraphQL client types [tier:T6]", () => {
  it("[CTR-005] were generated from the committed schema.graphql", () => {
    const digest = createHash("sha256").update(fs.readFileSync(schemaFile)).digest("hex");
    expect(graphql.SCHEMA_SHA256).toBe(digest);
  });

  it("[CTR-005] regenerate byte for byte from the committed schema", () => {
    const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "sdp-graphql-")), "generated.ts");
    execFileSync(process.execPath, [path.join(here, "../scripts/generate-graphql.mjs")], {
      env: { ...process.env, SDP_GRAPHQL_OUT: out },
      stdio: "ignore",
    });
    expect(fs.readFileSync(out, "utf8")).toBe(fs.readFileSync(path.join(here, "../src/graphql/generated.ts"), "utf8"));
  });

  it("types the experiment the UI reads", () => {
    const experiment: graphql.Experiment = {
      id: "exp-1",
      state: "validated",
      protocol: "synthetic-linear",
      protocolVersion: "1.0.0",
      predeclarationHash: "sha256:" + "0".repeat(64),
      seed: 42,
      backend: "cpu",
      parameters: {},
      inputs: {},
      tasks: [],
      verdict: { status: "VERIFIED", protocolVersion: "1.0.0", reasons: [] },
      checks: [],
      limitations: [],
      manifest: null,
      dossiers: [],
    };
    expect(experiment.verdict?.status).toBe("VERIFIED");
  });
});
