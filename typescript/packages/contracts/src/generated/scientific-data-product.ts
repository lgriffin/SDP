// Generated from contracts/schemas/scientific-data-product.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/scientific-data-product.schema.json";
export const SCHEMA_VERSION = "1.0.0";

export const ProductMetadata = z.object({
    "product_id": z.string().min(1),
    "product_type": z.enum(["image","spectrum","cube","timeseries","measurements","catalogue","preview","thumbnail","auxiliary"]),
    "mission": z.string().min(1),
    "instrument": z.string().nullable(),
    "detector": z.string().nullable(),
    "filter_or_grating": z.string().nullable(),
    "observation_id": z.string().min(1),
    "programme_id": z.string().nullable(),
    "target_position": z.object({
      "ra_deg": z.number().nullable(),
      "dec_deg": z.number().nullable(),
      "frame": z.string().min(1),
    }).strict(),
    "s_region": z.string().nullable(),
    "time": z.object({
      "start_mjd": z.number().nullable(),
      "end_mjd": z.number().nullable(),
      "scale": z.string().min(1),
    }).strict(),
    "spectral_range_m": z.array(z.number().nullable()).min(2).max(2),
    "spatial_resolution_arcsec": z.number().nullable(),
    "field_of_view_deg": z.number().nullable(),
    "calibration_level": z.number().int().gte(0).lte(4).nullable(),
    "pipeline_version": z.string().nullable(),
    "format": z.string().min(1),
    "dimensions": z.array(z.number().int().gte(0)),
    "units": z.string().nullable(),
    "extensions": z.array(z.object({
      "name": z.string().min(1),
      "role": z.enum(["data","error","quality","variance","other"]),
      "unit": z.string().nullable(),
    }).strict()),
    "has_wcs": z.boolean(),
    "uncertainty_present": z.boolean(),
    "quality_present": z.boolean(),
    "access_status": z.enum(["public","exclusive_access","login"]),
    "exclusive_access_ends": z.string().regex(new RegExp("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", "u")).nullable(),
    "rights": z.object({
      "licence": z.string().min(1),
      "citation": z.string().min(1),
      "acknowledgement": z.string(),
    }).strict(),
    "source_id": z.string().min(1),
    "query": z.string(),
    "checksum": common.Sha256Id,
    "size_bytes": z.number().int().gte(0),
    "relationships": z.array(z.object({
      "semantics": z.enum(["#this","#progenitor","#auxiliary","#preview","#calibration","#derivation"]),
      "product_id": z.string().min(1),
      "description": z.string(),
    }).strict()),
    "calibration_references": z.array(z.object({
      "keyword": z.string().min(1),
      "file": z.string().min(1),
    }).strict()),
  }).strict();
export type ProductMetadata = z.infer<typeof ProductMetadata>;

export const SemanticValidationReport = z.object({
    "product_id": z.string().min(1),
    "validator_id": z.string().min(1),
    "validator_version": common.SchemaVersion,
    "passed": z.boolean(),
    "checks": z.array(z.object({
      "field": z.string().min(1),
      "passed": z.boolean(),
      "detail": z.string(),
    }).strict()),
  }).strict();
export type SemanticValidationReport = z.infer<typeof SemanticValidationReport>;

/** A raw artifact kept byte for byte plus its ObsCore 1.1-shaped envelope and semantic validation report (v0.4.0 §3, ADR-018). */
export const ScientificDataProduct = z.object({
    "schema_version": common.SchemaVersion,
    "raw_artifact_id": common.Sha256Id,
    "curated_artifact_id": common.Sha256Id.nullable(),
    "metadata": ProductMetadata,
    "validation": SemanticValidationReport.nullable(),
  }).strict();
export type ScientificDataProduct = z.infer<typeof ScientificDataProduct>;
// silence unused-import checks in files without refinements
void also; void ifObject; void requireKeys; void uniqueItems; void when;
