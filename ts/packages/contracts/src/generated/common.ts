// Generated from contracts/schemas/common.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/common.schema.json";
export const SCHEMA_VERSION = "1.0.0";

export const CheckOutcome = z.enum(["pass","fail","missing","oracle_unverified"]);
export type CheckOutcome = z.infer<typeof CheckOutcome>;

export const Comparator = z.enum(["lt","le","gt","ge","eq","ne"]);
export type Comparator = z.infer<typeof Comparator>;

export const GateId = z.string().regex(new RegExp("^[A-Z]{2,4}-G-[0-9]{2}$", "u"));
export type GateId = z.infer<typeof GateId>;

export const Severity = z.enum(["blocking","warning"]);
export type Severity = z.infer<typeof Severity>;

/** A gate evaluated against a finding value. */
export const Check = z.object({
    "gate_id": GateId,
    "finding": z.string(),
    "value": z.union([z.number(), z.boolean()]).nullable(),
    "unit": z.string().nullable().optional(),
    "comparator": Comparator,
    "threshold": z.union([z.number(), z.boolean()]),
    "severity": Severity,
    "oracle_ids": z.array(z.string()),
    "oracles_verified": z.boolean(),
    "outcome": CheckOutcome,
  }).strict();
export type Check = z.infer<typeof Check>;

/** A predeclared check: finding <comparator> threshold. */
export const Gate = z.object({
    "gate_id": GateId,
    "description": z.string().min(1),
    "finding": z.string().min(1),
    "comparator": Comparator,
    "threshold": z.union([z.number(), z.boolean()]),
    "severity": Severity,
    "oracle_ids": z.array(z.string()).superRefine(uniqueItems),
  }).strict();
export type Gate = z.infer<typeof Gate>;

export const NumericalPolicy = z.object({
    "dtype": z.enum(["float32","float64","int64","bool","object"]),
    "rtol": z.number().gte(0),
    "atol": z.number().gte(0),
    "determinism": z.enum(["bitwise","tolerance","statistical"]),
    "seed_handling": z.enum(["none","explicit","derived"]),
  }).strict();
export type NumericalPolicy = z.infer<typeof NumericalPolicy>;

/** A physical value as it crosses the boundary: value plus astropy unit string. */
export const Quantity = z.object({
    "value": z.number(),
    "unit": z.string(),
  }).strict();
export type Quantity = z.infer<typeof Quantity>;

export const SchemaVersion = z.string().regex(new RegExp("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$", "u"));
export type SchemaVersion = z.infer<typeof SchemaVersion>;

export const Sha256Id = z.string().regex(new RegExp("^sha256:[0-9a-f]{64}$", "u"));
export type Sha256Id = z.infer<typeof Sha256Id>;

/** Identity of an archive release, a document, a local store or a synthetic generator. Archives must name a release; documents must name a DOI or a bibliographic reference. */
export const SourceRef = z.object({
    "kind": z.enum(["archive","document","local","synthetic"]),
    "identity": z.string().min(1),
    "release_label": z.string().regex(new RegExp("^[a-z0-9]+:[A-Za-z0-9._-]+$", "u")).nullable().optional(),
    "query_text": z.string().nullable().optional(),
    "doi": z.string().nullable().optional(),
    "bibcode": z.string().nullable().optional(),
    "licence": z.string().min(1),
    "citation": z.string().min(1),
  }).strict()
  .superRefine(when(z.unknown().superRefine(ifObject(z.object({
      "kind": z.literal("archive"),
    }).passthrough())), z.unknown().superRefine(ifObject(z.object({
      "release_label": z.string(),
    }).passthrough()))))
  .superRefine(when(z.unknown().superRefine(ifObject(z.object({
      "kind": z.literal("document"),
    }).passthrough())), z.union([z.unknown().superRefine(ifObject(z.object({
      "doi": z.string(),
    }).passthrough())), z.unknown().superRefine(ifObject(z.object({
      "bibcode": z.string(),
    }).passthrough()))])));
export type SourceRef = z.infer<typeof SourceRef>;

export const Timestamp = z.string().regex(new RegExp("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$", "u"));
export type Timestamp = z.infer<typeof Timestamp>;

export const VerdictStatus = z.enum(["VERIFIED","CANDIDATE","REJECTED","INCONCLUSIVE"]);
export type VerdictStatus = z.infer<typeof VerdictStatus>;

export const Verdict = z.object({
    "status": VerdictStatus,
    "protocol_version": SchemaVersion,
    "reasons": z.array(z.string()),
  }).strict();
export type Verdict = z.infer<typeof Verdict>;
// silence unused-import checks in files without refinements
void also; void ifObject; void requireKeys; void uniqueItems; void when;
