// Boundary schemas generated from contracts/schemas (ADR-007), GraphQL query types generated from
// contracts/graphql/schema.graphql (CTR-005), and canonical JSON.
import type { z } from "zod";

import * as generated from "./generated/index.ts";
import * as graphql from "./graphql/generated.ts";
import { leafIssues } from "./refinements.ts";

export * from "./canonical.ts";
export { generated, graphql };
export { leafIssues } from "./refinements.ts";

export const SCHEMA_MAJOR = 1;

export const SCHEMAS = {
  "canonical-astrometry": generated.canonicalAstrometry.CanonicalAstrometryRow,
  "dataset-artifact": generated.datasetArtifact.DatasetArtifact,
  "evidence-manifest": generated.evidenceManifest.EvidenceManifest,
  "ledger-entry": generated.ledgerEntry.LedgerEntry,
  predeclaration: generated.predeclaration.Predeclaration,
  "validation-report": generated.validationReport.ValidationReport,
} as const;

export type ContractName = keyof typeof SCHEMAS;
export type Contract<N extends ContractName> = z.infer<(typeof SCHEMAS)[N]>;

export interface ContractError {
  path: string;
  message: string;
}

export const pointer = (path: readonly (string | number)[]): string =>
  path.length ? `/${path.map((p) => String(p).replace(/~/g, "~0").replace(/\//g, "~1")).join("/")}` : "";

/** Every violation of the named contract, as JSON pointers into `document`. */
export function validate(name: ContractName, document: unknown): ContractError[] {
  const result = SCHEMAS[name].safeParse(document);
  if (result.success) return [];
  const seen = new Map<string, ContractError>();
  for (const issue of leafIssues(result.error)) {
    const error = { path: pointer(issue.path), message: issue.message };
    seen.set(`${error.path}\u0000${error.message}`, error);
  }
  return [...seen.values()].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

export class ContractViolation extends Error {
  readonly contract: ContractName;
  readonly errors: ContractError[];

  constructor(contract: ContractName, errors: ContractError[]) {
    super(`${contract}: ${errors.map((e) => `${e.path || "/"}: ${e.message}`).join("; ")}`);
    this.contract = contract;
    this.errors = errors;
  }
}

export function parse<N extends ContractName>(name: N, document: unknown): Contract<N> {
  const errors = validate(name, document);
  if (errors.length) throw new ContractViolation(name, errors);
  return SCHEMAS[name].parse(document) as Contract<N>;
}

export function schemaMajor(document: { schema_version?: unknown }): number {
  return Number.parseInt(String(document.schema_version ?? "0").split(".")[0] ?? "0", 10);
}
