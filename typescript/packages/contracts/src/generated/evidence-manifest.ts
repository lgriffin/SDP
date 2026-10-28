// Generated from contracts/schemas/evidence-manifest.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, contains, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as adapterCompatibility from "./adapter-compatibility.ts";
import * as common from "./common.ts";
import * as predeclaration from "./predeclaration.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/evidence-manifest.schema.json";
export const SCHEMA_VERSION = "1.4.0";

export const Approval = z.object({
    "ledger_entry_id": z.string(),
    "reviewer": z.string(),
    "recorded_at": common.Timestamp,
  }).strict();
export type Approval = z.infer<typeof Approval>;

export const Environment = z.object({
    "code_revision": z.string().min(1),
    "lock_digest": z.string().min(1),
    "image_digest": z.string().nullable().optional(),
    "backend": z.enum(["cpu","cuda"]),
    "driver_version": z.string().nullable().optional(),
    "cuda_version": z.string().nullable().optional(),
    "agreement_test_id": z.string().nullable().optional(),
    "python_version": z.string(),
    "platform": z.string(),
    "constants_version": z.string().nullable().optional(),
    "packages": z.record(z.string(), z.string()).optional(),
    "adapter_compatibility": z.array(adapterCompatibility.AdapterCompatibilityRecord).optional(),
  }).strict()
  .superRefine(when(z.unknown().superRefine(ifObject(z.object({
      "backend": z.literal("cuda"),
    }).passthrough())), z.unknown().superRefine(ifObject(z.object({
      "driver_version": z.string(),
      "cuda_version": z.string(),
      "agreement_test_id": z.string(),
    }).passthrough()))));
export type Environment = z.infer<typeof Environment>;

export const Task = z.object({
    "task_id": z.string().min(1),
    "task_kind": z.string().min(1),
    "depends_on": z.array(z.string()),
    "policy": common.NumericalPolicy,
    "status": z.enum(["pending","completed","failed","skipped"]),
    "checkpoint_artifact_id": common.Sha256Id.nullable().optional(),
    "duration_s": z.number().gte(0).nullable().optional(),
  }).strict();
export type Task = z.infer<typeof Task>;

export const Execution = z.object({
    "seed": z.number().int(),
    "state": z.enum(["defined","refused","running","failed","completed","validated","approved","published"]),
    "parameters": z.object({}).passthrough().optional(),
    "tasks": z.array(Task),
    "started_at": common.Timestamp.nullable().optional(),
    "finished_at": common.Timestamp.nullable().optional(),
    "failure": z.object({
      "task_id": z.string().nullable(),
      "reason": z.string(),
    }).strict().nullable().optional(),
  }).strict();
export type Execution = z.infer<typeof Execution>;

export const Finding = z.object({
    "name": z.string().min(1),
    "value": z.union([z.number(), z.boolean(), z.string(), z.array(z.unknown()), z.object({}).passthrough()]).nullable(),
    "uncertainty": z.number().nullable().optional(),
    "unit": z.string().nullable().optional(),
  }).strict();
export type Finding = z.infer<typeof Finding>;

export const Input = z.object({
    "role": z.string().min(1),
    "artifact_id": common.Sha256Id,
    "media_type": z.string(),
    "source_ref": common.SourceRef,
    "dialect_id": z.string().nullable().optional(),
    "acquisition_key": common.Sha256Id.nullable().optional(),
    "adapter": z.string().min(1).nullable().optional(),
    "adapter_version": common.SchemaVersion.nullable().optional(),
    "product_type": z.enum(["image","spectrum","cube","timeseries","measurements","catalogue"]).nullable().optional(),
  }).strict()
  .superRefine(when(z.unknown().superRefine(requireKeys(["adapter"])), z.unknown().superRefine(requireKeys(["product_type"]))))
  .superRefine(when(z.unknown().superRefine(requireKeys(["product_type"])), z.unknown().superRefine(requireKeys(["adapter"]))))
  .superRefine(when(z.unknown().superRefine(requireKeys(["adapter_version"])), z.unknown().superRefine(requireKeys(["adapter"]))));
export type Input = z.infer<typeof Input>;

export const ManifestVerdict = z.object({
    "status": common.VerdictStatus,
    "protocol_version": common.SchemaVersion,
    "reasons": z.array(z.string()),
    "limitations": z.array(z.string()),
    "approvals": z.array(Approval),
  }).strict();
export type ManifestVerdict = z.infer<typeof ManifestVerdict>;

export const OracleRecord = z.object({
    "oracle_id": z.string().min(1),
    "verified": z.boolean(),
    "note": z.string().optional(),
  }).strict();
export type OracleRecord = z.infer<typeof OracleRecord>;

export const Output = z.object({
    "name": z.string().min(1),
    "artifact_id": common.Sha256Id,
    "media_type": z.string(),
  }).strict();
export type Output = z.infer<typeof Output>;

/** A W3C PROV-JSON document (subset: prefix, entity, activity, agent and the five relations SDP uses). */
export const ProvDocument = z.object({
    "prefix": z.record(z.string(), z.string()),
    "entity": z.record(z.string(), z.object({}).passthrough()),
    "activity": z.record(z.string(), z.object({}).passthrough()),
    "agent": z.record(z.string(), z.object({}).passthrough()),
    "used": z.record(z.string(), z.object({}).passthrough()).optional(),
    "wasGeneratedBy": z.record(z.string(), z.object({}).passthrough()).optional(),
    "wasAssociatedWith": z.record(z.string(), z.object({}).passthrough()).optional(),
    "wasDerivedFrom": z.record(z.string(), z.object({}).passthrough()).optional(),
    "wasAttributedTo": z.record(z.string(), z.object({}).passthrough()).optional(),
  }).strict();
export type ProvDocument = z.infer<typeof ProvDocument>;

/** The durable contract between the kernel and the harness: one document per experiment, written atomically to the NAS and mirrored in PostgreSQL (§7). */
export const EvidenceManifest = z.object({
    "schema_version": common.SchemaVersion,
    "identity": z.object({
      "experiment_id": z.string().min(1),
      "protocol_id": z.string().min(1),
      "protocol_version": common.SchemaVersion,
    }).strict(),
    "intent": z.object({
      "predeclaration_hash": common.Sha256Id,
      "ledger_entry_id": z.string().min(1),
      "predeclaration": predeclaration.Predeclaration,
      "plan_artifact_id": common.Sha256Id.nullable().optional(),
    }).strict(),
    "inputs": z.array(Input),
    "environment": Environment,
    "execution": Execution,
    "outputs": z.array(Output),
    "findings": z.array(Finding),
    "oracles": z.array(OracleRecord).optional(),
    "checks": z.array(common.Check),
    "verdict": ManifestVerdict.nullable(),
    "provenance": ProvDocument,
  }).strict();
export type EvidenceManifest = z.infer<typeof EvidenceManifest>;
// silence unused-import checks in files without refinements
void also; void contains; void ifObject; void requireKeys; void uniqueItems; void when;
