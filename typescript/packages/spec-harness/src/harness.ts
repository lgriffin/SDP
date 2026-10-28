// Harness use cases: each composes ports and pure functions; the CLI only parses arguments.
import { hashDocument, type ContractError } from "@sdp/contracts";

import { readManifest } from "./conform.ts";
import { compileEars, type CompiledRequirements } from "./ears.ts";
import { guard, type FileChange, type GateSnapshot, type GuardResult } from "./guard.ts";
import type {
  LedgerSource,
  ManifestSource,
  PredeclarationSource,
  RequirementSource,
  TestReportSource,
} from "./ports.ts";
import { rederive, type Rederivation } from "./rederive.ts";
import type { Approval } from "./report.ts";
import { trace, type PredeclaredGates, type Traceability, type TraceOptions } from "./trace.ts";

export async function compileRequirements(source: RequirementSource): Promise<CompiledRequirements> {
  return compileEars(await source.earsFiles());
}

export async function predeclaredGates(
  predeclarations: PredeclarationSource,
  ledger: LedgerSource,
): Promise<PredeclaredGates[]> {
  const frozen = new Set((await ledger.entries()).filter((e) => e.kind === "predeclaration").map((e) => e.subject_hash));
  return (await predeclarations.predeclarations()).map(({ path, document }) => {
    const doc = document as { predeclaration_id?: string; gates?: { gate_id: string }[] };
    const hash = hashDocument(document);
    return {
      predeclarationId: doc.predeclaration_id ?? path,
      hash,
      gates: (doc.gates ?? []).map((g) => g.gate_id),
      frozen: frozen.has(hash),
    };
  });
}

export async function runTrace(
  sources: {
    requirements: RequirementSource;
    tests: TestReportSource;
    predeclarations?: PredeclarationSource;
    ledger?: LedgerSource;
  },
  options: TraceOptions = {},
): Promise<{ compiled: CompiledRequirements; traceability: Traceability }> {
  const compiled = await compileRequirements(sources.requirements);
  const gates =
    sources.predeclarations && sources.ledger ? await predeclaredGates(sources.predeclarations, sources.ledger) : [];
  const traceability = trace(compiled.requirements, await sources.tests.results(), gates, options);
  return { compiled, traceability };
}

export interface ConformReport {
  path: string;
  ok: boolean;
  major: number | null;
  errors: ContractError[];
}

export async function conformAll(source: ManifestSource): Promise<ConformReport[]> {
  return (await source.manifests()).map(({ path, document }) => {
    const result = readManifest(document);
    return result.ok
      ? { path, ok: true, major: result.major, errors: [] }
      : { path, ok: false, major: result.major, errors: result.errors };
  });
}

export async function rederiveAll(source: ManifestSource): Promise<(Rederivation & { path: string })[]> {
  const out: (Rederivation & { path: string })[] = [];
  for (const { path, document } of await source.manifests()) {
    const result = readManifest(document);
    if (!result.ok) throw new Error(`${path} does not conform: ${result.errors.map((e) => `${e.path} ${e.message}`).join("; ")}`);
    out.push({ ...rederive(result.manifest), path });
  }
  return out;
}

export async function approvals(ledger: LedgerSource): Promise<Approval[]> {
  return (await ledger.entries())
    .filter((e) => e.kind === "approval")
    .map((e) => ({
      experimentId: String(e.payload?.experiment_id ?? e.subject_id),
      reviewer: e.signer,
      verdict: String(e.payload?.verdict_status ?? "unknown"),
      selfApproval: Boolean(e.payload?.self_approval),
      entryId: e.entry_id,
    }));
}

export function guardChanges(changes: readonly FileChange[], gates: readonly GateSnapshot[]): GuardResult {
  return guard(changes, gates);
}
