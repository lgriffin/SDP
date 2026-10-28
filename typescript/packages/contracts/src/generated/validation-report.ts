// Generated from contracts/schemas/validation-report.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/validation-report.schema.json";
export const SCHEMA_VERSION = "1.0.0";

/** Every predeclared gate evaluated against the findings, and the verdict that follows from them. */
export const ValidationReport = z.object({
    "schema_version": common.SchemaVersion,
    "experiment_id": z.string().min(1),
    "protocol_id": z.string().min(1),
    "protocol_version": common.SchemaVersion,
    "predeclaration_hash": common.Sha256Id,
    "checks": z.array(common.Check),
    "verdict": common.Verdict,
  }).strict();
export type ValidationReport = z.infer<typeof ValidationReport>;
// silence unused-import checks in files without refinements
void also; void ifObject; void requireKeys; void uniqueItems; void when;
