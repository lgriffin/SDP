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
  ArchivesStatus,
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
  archives: ArchivesStatus;
}

/** What a server that has registered its archives lists: the packaged catalogues and MAST. */
export const REGISTERED_ARCHIVES: ArchivesStatus = {
  sources: [
    { sourceId: "2mass", origin: "hand-written", verifiedOn: "2026-10-29", productTypes: ["catalogue"] },
    { sourceId: "gaia", origin: "hand-written", verifiedOn: "2026-10-29", productTypes: ["catalogue"] },
    { sourceId: "mast", origin: "registry", verifiedOn: "2026-10-29", productTypes: ["cube", "image", "spectrum", "timeseries"] },
  ],
  registration: null,
};

/** How long a demonstration plan's background run takes before it reads as finished. */
export const DEMO_RUN_MS = 2500;

export const FIXTURES = { ...data, plans, archives: REGISTERED_ARCHIVES } as unknown as FixtureData;

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
    const p = this.data.plans.find((x) => x.id === id) ?? null;
    if (p) this.settle(p);
    return p;
  }

  async archives(): Promise<ArchivesStatus | null> {
    return this.data.archives;
  }

  /** A demonstration run finishes a moment after it starts, so the page shows it running first. */
  settle(p: Plan, now = Date.now()): void {
    if (p.run?.state !== "running" || now - Date.parse(p.run.started_at) < DEMO_RUN_MS) return;
    p.state = "executed";
    p.execution = { plan_id: p.id, acquired: p.document.items.map((i) => `synthetic:${i.item_id}`), unavailable: [] };
    p.setId = this.data.investigationSets[0]?.id ?? null;
    p.run = { ...p.run, state: "finished", finished_at: new Date(now).toISOString() };
  }
}
