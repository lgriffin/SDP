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
  ArtifactBytes,
  LedgerEntry,
  Plan,
  ReproductionRun,
  Comparison,
  ReferencePull,
} from "../ports.ts";
import { DEMO_CATALOGUES } from "./references.ts";
import data from "./fixtures.json" with { type: "json" };
import jwst from "./jwst.json" with { type: "json" };
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
  /** Pictures of the fixture images, base64 PNG by artifact id (scripts/jwst-fixtures.py). */
  previews: Record<string, string>;
  /** Checks that a set reproduces from its dossier, by set id (INV-022). */
  reproductions: Record<string, ReproductionRun>;
  /** Reference pulls by investigation id (INV-024). */
  references: Record<string, ReferencePull[]>;
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

// jwst.json is the synthetic JWST field, planned and executed through the planner, with its image.
export const FIXTURES = {
  ...data,
  investigationSets: [...data.investigationSets, ...jwst.investigationSets],
  investigationSet: { ...data.investigationSet, ...jwst.investigationSet },
  investigation: { ...data.investigation, ...jwst.investigation },
  plans: [...plans, ...jwst.plans],
  archives: REGISTERED_ARCHIVES,
  previews: jwst.previews,
  reproductions: {},
  references: {},
} as unknown as FixtureData;

export class InMemoryEvidenceSource implements EvidenceSource {
  readonly data: FixtureData;
  /** The set a demonstration plan opens when it has run, by plan id; others open the first set. */
  readonly demoSets: Record<string, string> = {};

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

  async artifact(_artifactId: string): Promise<ArtifactBytes | null> {
    return null; // the demonstration keeps pictures, not the files themselves
  }

  async preview(artifactId: string): Promise<ArtifactBytes | null> {
    const b64 = this.data.previews[artifactId];
    return b64 ? { body: new Uint8Array(Buffer.from(b64, "base64")), mediaType: "image/png" } : null;
  }

  async dossier(_setId: string): Promise<ArtifactBytes | null> {
    return null; // nor a dossier: it would need those files
  }

  async comparison(investigationId: string): Promise<Comparison | null> {
    if (!this.data.investigation[investigationId]) return { catalogues: DEMO_CATALOGUES, pulls: [] };
    return { catalogues: DEMO_CATALOGUES, pulls: this.data.references[investigationId] ?? [] };
  }

  async reproduction(setId: string): Promise<ReproductionRun | null> {
    const run = this.data.reproductions[setId];
    const set = this.data.investigationSet[setId];
    if (!run || !set) return null;
    if (run.state === "running" && Date.now() - Date.parse(run.started_at) >= DEMO_RUN_MS) {
      const tested = set.stages.some((s) => s.stage === "test" && s.state === "completed");
      Object.assign(run, { finished_at: new Date().toISOString() });
      if (tested) Object.assign(run, { state: "finished", result: { ok: true, results_compared: set.objectsExamined, seed: set.seed, differences: [] } });
      else Object.assign(run, { state: "failed", error: "IntegrityError: the dossier has no stages/test.json; only a set whose Test stage completed is reproduced" });
    }
    return run;
  }

  /** A demonstration run finishes a moment after it starts, so the page shows it running first. */
  settle(p: Plan, now = Date.now()): void {
    if (p.run?.state !== "running" || now - Date.parse(p.run.started_at) < DEMO_RUN_MS) return;
    p.state = "executed";
    p.execution = { plan_id: p.id, acquired: p.document.items.map((i) => `synthetic:${i.item_id}`), unavailable: [] };
    p.setId = this.demoSets[p.id] ?? this.data.investigationSets[0]?.id ?? null;
    p.run = { ...p.run, state: "finished", finished_at: new Date(now).toISOString() };
  }
}
