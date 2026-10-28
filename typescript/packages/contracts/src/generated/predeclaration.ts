// Generated from contracts/schemas/predeclaration.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, contains, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/predeclaration.schema.json";
export const SCHEMA_VERSION = "1.0.0";

export const Prediction = z.object({
    "model_id": z.string().min(1),
    "model_version": common.SchemaVersion,
    "parameters": z.object({}).passthrough(),
    "predicted": common.Quantity.nullable().optional(),
    "geometry_assumptions": z.array(z.string()),
  }).strict();
export type Prediction = z.infer<typeof Prediction>;

export const HypothesisRef = z.object({
    "hypothesis_id": z.string().regex(new RegExp("^[A-Za-z0-9][A-Za-z0-9._-]*$", "u")),
    "version": z.number().int().gte(1),
    "statement": z.string().min(1),
    "predictions": z.array(Prediction),
    "evaluation_criteria": z.array(z.string()),
  }).strict();
export type HypothesisRef = z.infer<typeof HypothesisRef>;

export const OracleTolerance = z.object({
    "oracle_id": z.string().min(1),
    "rtol": z.number().gte(0),
    "atol": z.number().gte(0),
    "unit": z.string().nullable().optional(),
  }).strict();
export type OracleTolerance = z.infer<typeof OracleTolerance>;

export const Target = z.object({
    "target_id": z.string().min(1),
    "kind": z.enum(["region","source"]),
    "reason": z.string().min(1),
    "spec": z.object({}).passthrough(),
  }).strict();
export type Target = z.infer<typeof Target>;

/** Hypotheses, gates, oracle tolerances and targets, hashed over canonical JSON and frozen before any data are touched (ADR-004). */
export const Predeclaration = z.object({
    "schema_version": common.SchemaVersion,
    "predeclaration_id": z.string().regex(new RegExp("^[a-z0-9][a-z0-9._-]*$", "u")),
    "protocol_id": z.string().regex(new RegExp("^[a-z0-9][a-z0-9._-]*$", "u")),
    "protocol_version": common.SchemaVersion,
    "title": z.string().min(1),
    "hypotheses": z.array(HypothesisRef).min(1),
    "gates": z.array(common.Gate).min(1),
    "oracle_tolerances": z.array(OracleTolerance),
    "targets": z.array(Target),
    "parameters": z.object({}).passthrough(),
    "notes": z.string().nullable().optional(),
  }).strict();
export type Predeclaration = z.infer<typeof Predeclaration>;
// silence unused-import checks in files without refinements
void also; void contains; void ifObject; void requireKeys; void uniqueItems; void when;
