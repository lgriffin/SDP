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
export type InvestigationSet = Omit<graphql.InvestigationSet, "manifest" | "investigations" | "images"> & {
  investigations: InvestigationSummary[];
  /** Absent from sets recorded before images were listed. */
  images?: FieldImage[];
};

/** A Gaia star the image-counterpart test placed on an image (INV-020). Pixels are 0-based FITS x, y. */
export interface FieldStar {
  object_key: string;
  investigation_id: string | null;
  predicted_px: [number, number];
  counterpart_px: [number, number] | null;
  d2: number | null;
  p_value: number;
  flagged: boolean;
  ambiguous: boolean;
  /** Every retained hypothesis for the image the star's result describes; null for its other images. */
  counterparts: CounterpartDoc[] | null;
}

export interface CounterpartDoc {
  record_id: string;
  posterior: number;
  option_posterior: number;
  no_counterpart: number;
  role: string | null;
  blend_of: string[];
  competing: string[];
}

export interface EnvelopeCheck {
  field: string;
  passed: boolean;
  detail: string;
}

/** An image a field investigation read, with its envelope's checks and the stars on it (INV-020). */
export interface FieldImage {
  product_id: string;
  archive: string;
  artifact_id: string;
  raw_artifact_id: string | null;
  envelope_artifact_id: string | null;
  read_from: string | null;
  width: number | null;
  height: number | null;
  summary: Record<string, unknown>;
  checks: EnvelopeCheck[] | null;
  passed: boolean | null;
  stars: FieldStar[];
}

export interface ArtifactBytes {
  body: Uint8Array;
  mediaType: string;
}

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

/** A catalogue a candidate can be compared against (INV-024). */
export interface ReferenceCatalogueOption {
  sourceId: string;
  title: string;
  releaseLabel: string;
  citation: string;
  description: string;
}

/** A row in canonical names: ra_deg, dec_deg, epoch_jyear, g_mag, j_mag, object_type, ... */
export type ReferenceRow = Record<string, unknown> & { source_key?: string | null; separation_arcsec?: number };

/** One catalogue's answer to one cone around a candidate, as kept with the investigation (INV-024). */
export interface ReferencePull {
  source_id: string;
  title: string;
  release_label: string;
  citation: string;
  radius_arcsec: number;
  target: {
    ra_deg: number;
    dec_deg: number;
    epoch_jyear: number | null;
    pm_ra_cosdec_masyr: number | null;
    pm_dec_masyr: number | null;
  };
  asked_at: string;
  actor: string;
  status: "found" | "none" | "failed";
  /** Nearest first, each with its separation_arcsec from the candidate at the row's epoch. */
  matches: ReferenceRow[];
  raw_artifact_id: string | null;
  error: string | null;
}

/** What the comparison page reads: the catalogues on offer and every pull made for the candidate. */
export interface Comparison {
  catalogues: ReferenceCatalogueOption[];
  pulls: ReferencePull[];
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
  /** A stored artifact's bytes, e.g. a FITS cutout; null when there is none. */
  artifact(artifactId: string): Promise<ArtifactBytes | null>;
  /** A FITS image drawn as a PNG, pixel y upward (INV-020); null when there is none. */
  preview(artifactId: string): Promise<ArtifactBytes | null>;
  /** The set's evidence dossier, one RO-Crate zip (INV-021); null when there is no such set or file. */
  dossier(setId: string): Promise<ArtifactBytes | null>;
  /** The last check that the set reproduces from its dossier; null when none was started (INV-022). */
  reproduction(setId: string): Promise<ReproductionRun | null>;
  /** The catalogues on offer and the candidate's pulls (INV-024); null from an sdp-api before comparisons. */
  comparison(investigationId: string): Promise<Comparison | null>;
}

/** The Test stage re-run from a set's dossier alone and compared with what it records (INV-022). */
export interface Reproduction {
  ok: boolean;
  results_compared: number;
  seed: number;
  differences: string[];
}

/** A reproduction runs for minutes with images, so it is a job; ``result`` arrives when it finishes. */
export interface ReproductionRun extends JobRun {
  result?: Reproduction | null;
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
  /** Starts saving the set's dossier and re-running its tests from it alone (INV-022); answers at once. */
  reproduceSet(setId: string): Promise<ReproductionRun>;
  /** Asks each catalogue for one cone around the candidate and keeps every answer (INV-024); takes up to a minute. */
  pullReferences(investigationId: string, sources: string[], radiusArcsec: number, actor: string): Promise<PullSummary>;
}

export interface PullSummary {
  pulls: { source_id: string; status: ReferencePull["status"]; matches: number }[];
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
