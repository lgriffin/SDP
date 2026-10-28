// Generated from contracts/schemas/association-graph.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/association-graph.schema.json";
export const SCHEMA_VERSION = "1.0.0";

export const Association = z.object({
    "record_id": z.string().min(1),
    "hypothesis_id": z.string().min(1),
    "method": z.string().min(1),
    "method_version": common.SchemaVersion,
    "prior": z.number().gte(0).lte(1),
    "likelihood": z.number(),
    "posterior": z.number().gte(0).lte(1),
    "bayes_factor": z.number().nullable(),
    "competing": z.array(z.string().min(1)),
    "evidence": z.object({}).passthrough(),
    "state": z.enum(["proposed","accepted","rejected","superseded"]),
    "ledger_entry_id": z.string().nullable(),
    "reviewer": z.string().nullable(),
    "reason": z.string(),
  }).strict()
  .superRefine(when(z.unknown().superRefine(ifObject(z.object({
      "state": z.literal("accepted"),
    }).passthrough())), z.unknown().superRefine(ifObject(z.object({
      "ledger_entry_id": z.string().min(1).optional(),
    }).passthrough()))));
export type Association = z.infer<typeof Association>;

export const Covariance = z.object({
    "sigma_ra_arcsec": z.number().gt(0),
    "sigma_dec_arcsec": z.number().gt(0),
    "rho": z.number().gte(-1).lte(1),
  }).strict();
export type Covariance = z.infer<typeof Covariance>;

export const Motion = z.object({
    "pmra_mas_yr": z.number(),
    "pmdec_mas_yr": z.number(),
    "parallax_mas": z.number().nullable(),
    "sigma_pmra_mas_yr": z.number(),
    "sigma_pmdec_mas_yr": z.number(),
    "sigma_parallax_mas": z.number().nullable(),
  }).strict();
export type Motion = z.infer<typeof Motion>;

export const Photometry = z.object({
    "band": z.string().min(1),
    "magnitude": z.number(),
    "sigma": z.number(),
  }).strict();
export type Photometry = z.infer<typeof Photometry>;

export const CatalogueSource = z.object({
    "kind": z.literal("catalogue_source"),
    "record_id": z.string().min(1),
    "catalogue": z.string().min(1),
    "release_label": z.string().min(1),
    "source_key": z.string().min(1),
    "ra_deg": z.number(),
    "dec_deg": z.number(),
    "covariance": Covariance,
    "epoch_jyear": z.number(),
    "motion": Motion.nullable(),
    "resolution_arcsec": z.number().gte(0),
    "photometry": z.array(Photometry),
  }).strict();
export type CatalogueSource = z.infer<typeof CatalogueSource>;

export const Detection = z.object({
    "kind": z.literal("detection"),
    "record_id": z.string().min(1),
    "product_id": z.string().min(1),
    "extension": z.string().min(1),
    "ra_deg": z.number(),
    "dec_deg": z.number(),
    "covariance": Covariance,
    "epoch_jyear": z.number(),
    "resolution_arcsec": z.number().gt(0),
    "instrument": z.string().min(1),
    "photometry": z.array(Photometry),
  }).strict();
export type Detection = z.infer<typeof Detection>;

/** Detections, catalogue sources, object hypotheses and every retained association with its posterior (v0.4.0 §4, ADR-019). */
export const AssociationGraph = z.object({
    "schema_version": common.SchemaVersion,
    "graph_id": common.Sha256Id,
    "method": z.string().min(1),
    "method_version": common.SchemaVersion,
    "policy": z.object({
      "policy_id": z.string().min(1),
      "version": common.SchemaVersion,
      "common_epoch_jyear": z.number(),
      "search_radius_arcsec": z.number().gt(0),
      "min_posterior": z.number().gte(0).lt(0.5),
      "photometric_terms": z.boolean(),
      "counterpart_prior": z.number().gt(0).lt(1),
      "anchor_catalogue": z.string().min(1),
    }).strict(),
    "source_density_per_arcsec2": z.number().gte(0),
    "records": z.array(z.union([Detection, CatalogueSource])),
    "hypotheses": z.array(z.object({
      "hypothesis_id": z.string().min(1),
      "members": z.array(z.string().min(1)).min(1).superRefine(uniqueItems),
    }).strict()),
    "associations": z.array(Association),
  }).strict();
export type AssociationGraph = z.infer<typeof AssociationGraph>;
// silence unused-import checks in files without refinements
void also; void ifObject; void requireKeys; void uniqueItems; void when;
