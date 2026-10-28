// The fake EvidenceSource (ADR-006). fixtures.json holds real answers from sdp-api over the in-memory
// kernel: one approved VERIFIED experiment with a dossier, one REJECTED, one FAILED, one only defined,
// and one investigation set over the synthetic field with a parked and a closed candidate. plans.json
// holds three synthetic acquisition plans in the shape agreed for the Stage E planner: one executed,
// one small drafted plan and one drafted above the signed-approval threshold.
import type {
  DomainEvent,
  EvidenceSource,
  Experiment,
  ExperimentSummary,
  Investigation,
  InvestigationSet,
  InvestigationSetSummary,
  LedgerEntry,
  Plan,
} from "../ports.ts";
import data from "./fixtures.json" with { type: "json" };
import plans from "./plans.json" with { type: "json" };

export interface FixtureData {
  experiments: ExperimentSummary[];
  experiment: Record<string, Experiment>;
  ledger: LedgerEntry[];
  events: DomainEvent[];
  lineage: Record<string, string[]>;
  investigationSets: InvestigationSetSummary[];
  investigationSet: Record<string, InvestigationSet>;
  investigation: Record<string, Investigation>;
  plans: Plan[];
}

export const FIXTURES = { ...data, plans } as unknown as FixtureData;

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

  async investigationSets(): Promise<InvestigationSetSummary[]> {
    return this.data.investigationSets;
  }

  async investigationSet(id: string): Promise<InvestigationSet | null> {
    return this.data.investigationSet[id] ?? null;
  }

  async investigation(id: string): Promise<Investigation | null> {
    return this.data.investigation[id] ?? null;
  }

  async plans(): Promise<Plan[]> {
    return this.data.plans;
  }

  async plan(id: string): Promise<Plan | null> {
    return this.data.plans.find((p) => p.id === id) ?? null;
  }
}
