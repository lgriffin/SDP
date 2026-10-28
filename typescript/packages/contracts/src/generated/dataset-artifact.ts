// Generated from contracts/schemas/dataset-artifact.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/dataset-artifact.schema.json";
export const SCHEMA_VERSION = "1.0.0";

export const AcquisitionPart = z.object({
    "part": z.number().int().gte(0),
    "artifact_id": common.Sha256Id,
    "byte_count": z.number().int().gte(0),
    "row_count": z.number().int().gte(0),
  }).strict();
export type AcquisitionPart = z.infer<typeof AcquisitionPart>;

export const AcquisitionRecord = z.object({
    "rendered_query": z.string().min(1),
    "idempotency_key": common.Sha256Id,
    "source_release": z.string().min(1),
    "acquired_at": common.Timestamp,
    "checksum": common.Sha256Id,
    "byte_count": z.number().int().gte(0),
    "parts": z.array(AcquisitionPart),
  }).strict();
export type AcquisitionRecord = z.infer<typeof AcquisitionRecord>;

export const DialectRef = z.object({
    "dialect_id": z.string().min(1),
    "dialect_version": common.SchemaVersion,
  }).strict();
export type DialectRef = z.infer<typeof DialectRef>;

/** Metadata for one content-addressed dataset: raw artifacts are immutable, curated artifacts cite their raw parents and the dialect that produced them. */
export const DatasetArtifact = z.object({
    "schema_version": common.SchemaVersion,
    "artifact_id": common.Sha256Id,
    "kind": z.enum(["raw","curated","derived"]),
    "status": z.enum(["acquired","rejected","curated"]),
    "media_type": z.string().min(1),
    "byte_count": z.number().int().gte(0),
    "source_ref": common.SourceRef,
    "dialect": DialectRef.nullable(),
    "parents": z.array(common.Sha256Id),
    "acquisition": AcquisitionRecord.nullable(),
    "rejection_reason": z.string().nullable().optional(),
  }).strict();
export type DatasetArtifact = z.infer<typeof DatasetArtifact>;
// silence unused-import checks in files without refinements
void also; void ifObject; void requireKeys; void uniqueItems; void when;
