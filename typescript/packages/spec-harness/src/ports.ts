// The harness hexagon's driven ports (§7 transport): it reads the kernel's output as files, so its tests
// run on fakes without the Python side.
import type { EarsFile } from "./ears.ts";
import type { Check, FindingValue, Gate, OracleRecord, VerdictDecision } from "./gates.ts";
import type { TestResult } from "./junit.ts";

export interface Located<T = unknown> {
  path: string;
  document: T;
}

export interface LedgerEntryDocument {
  entry_id: string;
  sequence: number;
  kind: "predeclaration" | "approval";
  subject_id: string;
  subject_hash: string;
  signer: string;
  payload?: Record<string, unknown>;
}

export interface RequirementSource {
  earsFiles(): Promise<EarsFile[]>;
}

export interface TestReportSource {
  results(): Promise<TestResult[]>;
}

export interface ManifestSource {
  manifests(): Promise<Located[]>;
}

export interface PredeclarationSource {
  predeclarations(): Promise<Located[]>;
}

/** Any JSON documents of one contract, e.g. capability descriptors or compatibility records. */
export interface DocumentSource {
  documents(): Promise<Located[]>;
}

export interface LedgerSource {
  entries(): Promise<LedgerEntryDocument[]>;
}

export interface GateEvaluator {
  evaluate(gate: Gate, findings: readonly FindingValue[], oracles: readonly OracleRecord[]): Check;
  decide(checks: readonly Check[]): VerdictDecision;
}
