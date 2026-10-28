// The UI's queries are valid against the committed schema.graphql, so a schema change that breaks the
// UI fails here before it reaches a browser (CTR-005).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { buildSchema, parse, validate } from "graphql";
import { describe, expect, it } from "vitest";

import { PLAN_QUERIES, QUERIES } from "../src/lib/adapters/graphql.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const schema = buildSchema(fs.readFileSync(path.resolve(here, "../../../../contracts/graphql/schema.graphql"), "utf8"));

describe("UI queries against schema.graphql [tier:T6]", () => {
  for (const [name, query] of Object.entries(QUERIES)) {
    it(`[CTR-005] ${name} query validates`, () => {
      expect(validate(schema, parse(query)).map((e) => e.message)).toEqual([]);
    });
  }
});

// The planner's queries in the shape agreed with Stage E (ADR-020). When the planner lands in the
// committed schema this extension becomes a no-op and the queries move into QUERIES.
const PLANNER_EXTENSION = `
  type Plan { id: ID! setId: String state: String! document: JSON! execution: JSON }
  extend type Query { plans: [Plan!]! plan(id: ID!): Plan }
`;
const committed = fs.readFileSync(path.resolve(here, "../../../../contracts/graphql/schema.graphql"), "utf8");
const planned = /\bplans\b/.test(committed) ? schema : buildSchema(committed + PLANNER_EXTENSION);

describe("planner queries against the agreed Stage E shape [tier:T6]", () => {
  for (const [name, query] of Object.entries(PLAN_QUERIES)) {
    it(`[CTR-005] ${name} query validates`, () => {
      expect(validate(planned, parse(query)).map((e) => e.message)).toEqual([]);
    });
  }
});
