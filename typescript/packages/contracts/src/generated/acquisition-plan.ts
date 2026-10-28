// Generated from contracts/schemas/acquisition-plan.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/acquisition-plan.schema.json";
export const SCHEMA_VERSION = "1.0.0";

export const CapabilityMatch = z.object({
    "source_id": z.string().min(1),
    "requirement_id": z.string().min(1),
    "status": z.enum(["satisfied","partial","unsatisfied"]),
    "reasons": z.array(z.object({
      "field": z.string().min(1),
      "outcome": z.enum(["satisfied","partial","unsatisfied"]),
      "reason": z.string().min(1),
    }).strict()).min(1),
    "descriptor_verified_on": z.string().regex(new RegExp("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", "u")),
  }).strict();
export type CapabilityMatch = z.infer<typeof CapabilityMatch>;

export const EvidenceRequirement = z.object({
    "requirement_id": z.string().min(1),
    "test_kind": z.string().min(1),
    "product_types": z.array(z.enum(["image","spectrum","cube","timeseries","measurements","catalogue"])).min(1).superRefine(uniqueItems),
    "min_calibration_level": z.number().int().gte(0).lte(4).nullable(),
    "missions": z.array(z.string().min(1)),
    "instruments": z.array(z.string().min(1)),
    "epoch_min_mjd": z.number().nullable(),
    "epoch_max_mjd": z.number().nullable(),
    "em_min_m": z.number().nullable(),
    "em_max_m": z.number().nullable(),
    "operations": z.array(z.enum(["cone","region","criteria","product_listing","download","cutout"])),
    "needs_bytes": z.boolean(),
    "public_only": z.boolean(),
  }).strict();
export type EvidenceRequirement = z.infer<typeof EvidenceRequirement>;

export const PlanApproval = z.object({
    "plan_id": common.Sha256Id,
    "decision": z.enum(["auto_approved","approved","refused"]),
    "actor": z.string().min(1),
    "threshold_bytes": z.number().int().gte(0),
    "ledger_entry_id": z.string().nullable(),
    "reason": z.string(),
  }).strict()
  .superRefine(when(z.unknown().superRefine(ifObject(z.object({
      "decision": z.literal("approved"),
    }).passthrough())), z.unknown().superRefine(ifObject(z.object({
      "ledger_entry_id": z.string().min(1).optional(),
    }).passthrough()))));
export type PlanApproval = z.infer<typeof PlanApproval>;

export const PlanItem = z.object({
    "item_id": z.string().min(1),
    "source_id": z.string().min(1),
    "operation": z.enum(["cone","region","criteria","product_listing","download","cutout"]),
    "target": z.string().min(1),
    "expected_bytes": z.number().int().gte(0),
    "expected_rows": z.number().int().nullable(),
    "calibration_level": z.number().int().nullable(),
    "access_status": z.literal("public"),
    "licence": z.string().min(1),
    "requirement_id": z.string().min(1),
    "test_kind": z.string().min(1),
  }).strict();
export type PlanItem = z.infer<typeof PlanItem>;

/** The content-addressed plan between PlanInvestigation and ApprovePlan: intent, discovery, items, cost, constraints, gaps and approval (v0.4.0 §5, ADR-020). */
export const AcquisitionPlan = z.object({
    "schema_version": common.SchemaVersion,
    "plan_id": common.Sha256Id,
    "intent": z.object({
      "field": z.object({}).passthrough(),
      "profile_hash": common.Sha256Id,
      "planner_id": z.string().min(1),
      "planner_version": common.SchemaVersion,
      "requirements": z.array(EvidenceRequirement),
    }).strict(),
    "discovery": z.array(CapabilityMatch),
    "items": z.array(PlanItem),
    "cost": z.object({
      "total_bytes": z.number().int().gte(0),
      "requests_per_source": z.record(z.string(), z.number().int().gte(0)),
      "estimated_wall_seconds": z.number().gte(0),
      "nas_headroom_after_bytes": z.number().int().nullable(),
    }).strict(),
    "constraints": z.object({
      "budget": z.object({
        "max_bytes": z.number().int().gte(0),
        "max_requests_per_source": z.number().int().gte(0),
        "max_wall_seconds": z.number().gt(0),
        "min_nas_headroom_bytes": z.number().int().gte(0),
        "approval_threshold_bytes": z.number().int().gte(0),
      }).strict(),
    }).passthrough(),
    "gaps": z.array(z.object({
      "requirement_id": z.string().min(1),
      "reason": z.string().min(1),
    }).strict()),
    "approval": PlanApproval.nullable(),
  }).strict();
export type AcquisitionPlan = z.infer<typeof AcquisitionPlan>;
// silence unused-import checks in files without refinements
void also; void ifObject; void requireKeys; void uniqueItems; void when;
