// Generated from contracts/schemas/capability-descriptor.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, contains, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/capability-descriptor.schema.json";
export const SCHEMA_VERSION = "1.1.0";

/** What a source can do, read from RegTAP, VOSI and ObsCore where published, or hand-written for a native service (v0.4.0 §2, ADR-017). */
export const SourceCapabilityDescriptor = z.object({
    "schema_version": common.SchemaVersion,
    "source_id": z.string().min(1),
    "ivoid": z.string().nullable(),
    "title": z.string().min(1),
    "publisher": z.string().min(1),
    "origin": z.enum(["registry","hand-written"]),
    "verified_on": z.string().regex(new RegExp("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", "u")),
    "protocols": z.array(z.object({
      "standard": z.string().min(1),
      "version": z.string().min(1),
      "base_url": z.string().min(1),
    }).strict()),
    "data_models": z.array(z.string().min(1)),
    "product_types": z.array(z.enum(["image","spectrum","cube","timeseries","measurements","catalogue","preview","thumbnail","auxiliary"])).superRefine(uniqueItems),
    "missions": z.array(z.string().min(1)),
    "instruments": z.array(z.string().min(1)),
    "calibration_levels": z.array(z.number().int().gte(0).lte(4)).superRefine(uniqueItems).optional(),
    "coverage": z.object({
      "all_sky": z.boolean().nullable(),
      "regions": z.array(z.object({
        "shape": z.literal("cone"),
        "ra_deg": z.number().gte(0).lt(360),
        "dec_deg": z.number().gte(-90).lte(90),
        "radius_deg": z.number().lte(10).gt(0),
      }).strict()),
      "t_min_mjd": z.number().nullable(),
      "t_max_mjd": z.number().nullable(),
      "em_min_m": z.number().nullable(),
      "em_max_m": z.number().nullable(),
    }).strict(),
    "operations": z.array(z.enum(["cone","region","criteria","product_listing","download","cutout"])).superRefine(uniqueItems),
    "cutout_support": z.record(z.string(), z.enum(["soda","astrocut","per-product","native","none"])),
    "access": z.enum(["public","exclusive_access","login"]),
    "limits": z.object({
      "max_rows": z.number().int().nullable(),
      "page_size": z.number().int().nullable(),
      "requests_per_second": z.number().nullable(),
      "max_concurrent": z.number().int().nullable(),
      "upload": z.boolean(),
      "throughput_bytes_per_s": z.number().nullable(),
    }).strict(),
    "rights": z.object({
      "licence": z.string().min(1),
      "citation": z.string().min(1),
      "acknowledgement": z.string(),
    }).strict(),
    "adapter": z.object({
      "name": z.string().min(1),
      "version": common.SchemaVersion,
      "last_compatibility_check": z.string().regex(new RegExp("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", "u")).nullable(),
      "conformance": z.record(z.string(), z.boolean()),
    }).strict().nullable(),
  }).strict();
export type SourceCapabilityDescriptor = z.infer<typeof SourceCapabilityDescriptor>;
// silence unused-import checks in files without refinements
void also; void contains; void ifObject; void requireKeys; void uniqueItems; void when;
