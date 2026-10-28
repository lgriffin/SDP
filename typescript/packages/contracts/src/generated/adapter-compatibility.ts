// Generated from contracts/schemas/adapter-compatibility.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, contains, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/adapter-compatibility.schema.json";
export const SCHEMA_VERSION = "1.0.1";

/** Four-kind conformance for one adapter and one product type, carried in the manifest's environment group and checked by the harness before any verdict (v0.4.0 §6, ADR-021). */
export const AdapterCompatibilityRecord = z.object({
    "schema_version": common.SchemaVersion,
    "adapter": z.string().min(1),
    "adapter_version": common.SchemaVersion,
    "product_type": z.enum(["image","spectrum","cube","timeseries","measurements","catalogue"]),
    "upstream_versions": z.record(z.string(), z.string()),
    "results": z.array(z.object({
      "kind": z.enum(["contract","operational","semantic","scientific"]),
      "passed": z.boolean(),
      "ci_run_id": z.string().min(1),
    }).strict())
    .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
        "kind": z.literal("contract"),
      }).passthrough())), 0, 1))))
    .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
        "kind": z.literal("operational"),
      }).passthrough())), 0, 1))))
    .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
        "kind": z.literal("semantic"),
      }).passthrough())), 0, 1))))
    .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
        "kind": z.literal("scientific"),
      }).passthrough())), 0, 1)))),
    "passed": z.boolean(),
    "live_smoke_on": z.string().regex(new RegExp("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", "u")).nullable(),
    "fixture_digests": z.array(common.Sha256Id),
  }).strict()
  .superRefine(when(z.unknown().superRefine(ifObject(z.object({
      "passed": z.literal(true),
    }).passthrough())), z.unknown().superRefine(ifObject(z.object({
      "results": z.unknown()
      .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
          "kind": z.literal("contract"),
          "passed": z.literal(true),
        }).passthrough())), 1, Infinity))))
      .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
          "kind": z.literal("operational"),
          "passed": z.literal(true),
        }).passthrough())), 1, Infinity))))
      .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
          "kind": z.literal("semantic"),
          "passed": z.literal(true),
        }).passthrough())), 1, Infinity))))
      .superRefine(also(z.unknown().superRefine(contains(z.unknown().superRefine(ifObject(z.object({
          "kind": z.literal("scientific"),
          "passed": z.literal(true),
        }).passthrough())), 1, Infinity)))).optional(),
    }).passthrough()))));
export type AdapterCompatibilityRecord = z.infer<typeof AdapterCompatibilityRecord>;
// silence unused-import checks in files without refinements
void also; void contains; void ifObject; void requireKeys; void uniqueItems; void when;
