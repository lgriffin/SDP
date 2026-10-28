// The UI's driven port: everything it reads about the evidence. The GraphQL adapter is the real
// implementation; the in-memory fixture source is the fake (ADR-006). Types come from the client
// generated from the committed schema.graphql (CTR-005).
import type { graphql } from "@sdp/contracts";

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
  "id" | "fieldId" | "profileId" | "profileVersion" | "profileHash" | "seed" | "startedAt" | "complete" | "objectsExamined" | "investigationIds" | "label" | "stages"
>;
export type InvestigationSet = Omit<graphql.InvestigationSet, "manifest" | "investigations"> & {
  investigations: InvestigationSummary[];
};

export interface EvidenceSource {
  experiments(state?: string): Promise<ExperimentSummary[]>;
  experiment(id: string): Promise<Experiment | null>;
  lineage(entityId: string): Promise<string[]>;
  ledger(): Promise<LedgerEntry[]>;
  events(after?: number, limit?: number): Promise<DomainEvent[]>;
  investigationSets(): Promise<InvestigationSetSummary[]>;
  investigationSet(id: string): Promise<InvestigationSet | null>;
  investigation(id: string): Promise<Investigation | null>;
}

export class EvidenceUnavailable extends Error {
  override name = "EvidenceUnavailable";
}
