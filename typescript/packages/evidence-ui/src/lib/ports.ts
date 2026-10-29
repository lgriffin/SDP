// The UI's driven ports. EvidenceSource is everything it reads, over GraphQL; Workbench is the few
// commands it may send, over sdp-api's REST surface (ADR-016); ProfileCatalogue lists the Investigate
// test profiles a scientist can pick. Each has a real adapter and an in-memory fake (ADR-006). Types
// come from the clients generated from the committed contracts (CTR-005).
import type { generated, graphql } from "@sdp/contracts";

export type Experiment = graphql.Experiment;
export type GateCheck = graphql.GateCheck;
export type LedgerEntry = graphql.LedgerEntry;
export type DomainEvent = graphql.Event;
export type Dossier = graphql.Dossier;

export type ExperimentSummary = Pick<
  graphql.Experiment,
  "id" | "state" | "protocol" | "protocolVersion" | "backend" | "seed" | "verdict" | "startedAt" | "finishedAt"
>;

export type InvestigationStage = graphql.InvestigationStage;
export type Investigation = graphql.Investigation;
export type InvestigationSummary = Pick<graphql.Investigation, "id" | "candidateKey" | "score" | "state" | "label" | "rankingPolicy">;
export type InvestigationSetSummary = Pick<
  graphql.InvestigationSet,
  "id" | "fieldId" | "profileId" | "profileVersion" | "profileHash" | "seed" | "startedAt" | "complete" | "objectsExamined" | "investigationIds" | "label" | "planArtifactId" | "stages"
>;
export type InvestigationSet = Omit<graphql.InvestigationSet, "manifest" | "investigations"> & {
  investigations: InvestigationSummary[];
};

export type AcquisitionPlanDoc = generated.acquisitionPlan.AcquisitionPlan;

/** The plan's state, derived from its document (ADR-020): drafted until decided, executed once run. */
export type PlanState = "drafted" | "auto_approved" | "approved" | "refused" | "executed";

export interface PlanExecutionDoc {
  plan_id: string;
  /** Artifact ids, one per item fetched. */
  acquired: string[];
  /** Item ids that could not be fetched. */
  unavailable: string[];
}

/** A long command sdp-api runs in the background (INV-018, INV-019). */
export interface JobRun {
  state: "running" | "finished" | "failed";
  started_at: string;
  finished_at: string | null;
  error: string | null;
}

export interface Plan {
  id: string;
  setId: string | null;
  state: PlanState;
  document: AcquisitionPlanDoc;
  execution: PlanExecutionDoc | null;
  /** The execution started from the UI, while it runs and after; null when none was started. */
  run?: JobRun | null;
}

export interface ArchiveSource {
  sourceId: string;
  origin: string;
  verifiedOn: string;
  productTypes: string[];
}

/** The archives plans are matched against, and the last registration started on the server. */
export interface ArchivesStatus {
  sources: ArchiveSource[];
  registration: JobRun | null;
}

export interface EvidenceSource {
  experiments(state?: string): Promise<ExperimentSummary[]>;
  experiment(id: string): Promise<Experiment | null>;
  lineage(entityId: string): Promise<string[]>;
  ledger(): Promise<LedgerEntry[]>;
  events(after?: number, limit?: number): Promise<DomainEvent[]>;
  investigationSets(): Promise<InvestigationSetSummary[]>;
  investigationSet(id: string): Promise<InvestigationSet | null>;
  investigation(id: string): Promise<Investigation | null>;
  /** Throws PlannerUnavailable when the server does not offer the planner yet. */
  plans(): Promise<Plan[]>;
  plan(id: string): Promise<Plan | null>;
  /** Null when the server cannot register archives (an older sdp-api). */
  archives(): Promise<ArchivesStatus | null>;
}

export type TriageAction = "park" | "reopen" | "close" | "annotate";

export interface InvestigateRequest {
  /** DS9-style region text, e.g. icrs;circle(120.0,-10.0,0.5), in degrees. */
  region: string;
  epochJyear: number;
  label: string;
  profile: Record<string, unknown>;
  seed: number;
  actor: string;
}

export interface Workbench {
  runExperiment(id: string): Promise<{ experimentId: string; state: string }>;
  validateExperiment(id: string): Promise<{ experimentId: string; verdict: string }>;
  investigate(request: InvestigateRequest): Promise<{ setId: string }>;
  triage(investigationId: string, action: TriageAction, actor: string, reason: string): Promise<{ state: string }>;
  planInvestigation(request: InvestigateRequest): Promise<{ planId: string }>;
  approvePlan(planId: string, actor: string): Promise<{ state: PlanState }>;
  refusePlan(planId: string, actor: string, reason: string): Promise<{ state: PlanState }>;
  /** Starts the run and answers at once; the plan's run says how it goes (INV-018). */
  executePlan(planId: string, actor: string): Promise<{ state: PlanState; run: JobRun | null }>;
  /** Starts reading what each archive offers; takes minutes the first time (INV-019). */
  registerArchives(): Promise<ArchivesStatus>;
}

export interface ProfileOption {
  /** profile_id@version */
  key: string;
  description: string;
  document: Record<string, unknown>;
}

export interface ProfileCatalogue {
  profiles(): Promise<ProfileOption[]>;
}

export class EvidenceUnavailable extends Error {
  override name = "EvidenceUnavailable";
}

export class PlannerUnavailable extends Error {
  override name = "PlannerUnavailable";
}

/** The server understood the command and said no: a 400, 404 or 409 with its reason. */
export class CommandRefused extends Error {
  override name = "CommandRefused";
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
