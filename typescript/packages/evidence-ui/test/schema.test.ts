// The UI's queries are valid against the committed schema.graphql, so a schema change that breaks the
// UI fails here before it reaches a browser (CTR-005).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { buildSchema, parse, validate } from "graphql";
import { describe, expect, it } from "vitest";

import { QUERIES } from "../src/lib/adapters/graphql.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const schema = buildSchema(fs.readFileSync(path.resolve(here, "../../../../contracts/graphql/schema.graphql"), "utf8"));

describe("UI queries against schema.graphql [tier:T6]", () => {
  for (const [name, query] of Object.entries(QUERIES)) {
    it(`[CTR-005] ${name} query validates`, () => {
      expect(validate(schema, parse(query)).map((e) => e.message)).toEqual([]);
    });
  }
});
