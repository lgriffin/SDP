// The fake EvidenceSource (ADR-006). fixtures.json holds real answers from sdp-api over the in-memory
// kernel: one approved VERIFIED experiment with a dossier, one REJECTED, one FAILED, one only defined.
import type { DomainEvent, EvidenceSource, Experiment, ExperimentSummary, LedgerEntry } from "../ports.ts";
import data from "./fixtures.json" with { type: "json" };

export interface FixtureData {
  experiments: ExperimentSummary[];
  experiment: Record<string, Experiment>;
  ledger: LedgerEntry[];
  events: DomainEvent[];
  lineage: Record<string, string[]>;
}

export const FIXTURES = data as unknown as FixtureData;

export class InMemoryEvidenceSource implements EvidenceSource {
  readonly data: FixtureData;

  constructor(data: FixtureData = FIXTURES) {
    this.data = structuredClone(data);
  }

  async experiments(state?: string): Promise<ExperimentSummary[]> {
    return this.data.experiments.filter((e) => state === undefined || e.state === state);
  }

  async experiment(id: string): Promise<Experiment | null> {
    return this.data.experiment[id] ?? null;
  }

  async lineage(entityId: string): Promise<string[]> {
    return this.data.lineage[entityId] ?? [];
  }

  async ledger(): Promise<LedgerEntry[]> {
    return this.data.ledger;
  }

  async events(after = 0, limit = 200): Promise<DomainEvent[]> {
    return this.data.events.filter((e) => e.sequence > after).slice(0, limit);
  }
}
