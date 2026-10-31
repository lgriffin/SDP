// Generated from contracts/schemas/canonical-astrometry.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, contains, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/canonical-astrometry.schema.json";
export const SCHEMA_VERSION = "1.1.0";

/** One row of the canonical astrometry table (§8). Curated Parquet carries exactly these columns; x-unit names the astropy unit of each physical column. */
export const CanonicalAstrometryRow = z.object({
    "source_key": z.string().regex(new RegExp("^[a-z0-9]+:([A-Za-z0-9._-]+:)?[^\\s]+$", "u")),
    "ra_deg": z.number().gte(0).lt(360),
    "dec_deg": z.number().gte(-90).lte(90),
    "epoch_jyear": z.number(),
    "parallax_mas": z.number().nullable(),
    "parallax_mas_err": z.number().gte(0).nullable(),
    "pm_ra_cosdec_masyr": z.number().nullable(),
    "pm_ra_cosdec_masyr_err": z.number().gte(0).nullable(),
    "pm_dec_masyr": z.number().nullable(),
    "pm_dec_masyr_err": z.number().gte(0).nullable(),
    "astrometric_corr": z.array(z.number().gte(-1).lte(1).nullable()).min(10).max(10).nullable(),
    "g_mag": z.number().nullable(),
    "bp_rp": z.number().nullable(),
    "j_mag": z.number().nullable().optional(),
    "h_mag": z.number().nullable().optional(),
    "k_mag": z.number().nullable().optional(),
    "ruwe": z.number().gte(0).nullable(),
    "quality_flags": z.object({}).passthrough(),
    "quality_class": z.enum(["good","marginal","poor","unknown"]),
    "zero_point_corrected": z.boolean(),
    "source_release": z.string().regex(new RegExp("^[a-z0-9]+:[A-Za-z0-9._-]+$", "u")),
  }).strict();
export type CanonicalAstrometryRow = z.infer<typeof CanonicalAstrometryRow>;
// silence unused-import checks in files without refinements
void also; void contains; void ifObject; void requireKeys; void uniqueItems; void when;
