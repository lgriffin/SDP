// Generated from contracts/schemas/ledger-entry.schema.json by scripts/generate-zod.mjs. Do not edit.
import { z } from "zod";
import { also, contains, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";
import * as common from "./common.ts";

export const SCHEMA_ID = "https://github.com/lgriffin/SDP/contracts/schemas/v1/ledger-entry.schema.json";
export const SCHEMA_VERSION = "1.0.0";

/** One signed, append-only, hash-chained entry in ledger/*.jsonl: a frozen predeclaration or a reviewer approval of a verdict. */
export const LedgerEntry = z.object({
    "schema_version": common.SchemaVersion,
    "entry_id": z.string().min(1),
    "sequence": z.number().int().gte(0),
    "kind": z.enum(["predeclaration","approval"]),
    "subject_id": z.string().min(1),
    "subject_hash": common.Sha256Id,
    "recorded_at": common.Timestamp,
    "signer": z.string().min(1),
    "signature": z.object({
      "method": z.enum(["ssh","gpg","test-hmac","unsigned"]),
      "value": z.string(),
    }).strict(),
    "previous_entry_hash": common.Sha256Id.nullable(),
    "payload": z.object({}).passthrough(),
  }).strict();
export type LedgerEntry = z.infer<typeof LedgerEntry>;
// silence unused-import checks in files without refinements
void also; void contains; void ifObject; void requireKeys; void uniqueItems; void when;
