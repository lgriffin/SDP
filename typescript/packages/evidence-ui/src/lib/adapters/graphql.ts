// EvidenceSource over the sdp-api GraphQL endpoint. Queries name exactly the fields the pages use.
import type { graphql } from "@sdp/contracts";

import {
  EvidenceUnavailable,
  type EvidenceSource,
  type Experiment,
  type ExperimentSummary,
  type Investigation,
  type InvestigationSet,
  type InvestigationSetSummary,
  type Plan,
  PlannerUnavailable,
} from "../ports.ts";

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

const SUMMARY = "id state protocol protocolVersion backend seed startedAt finishedAt verdict { status protocolVersion reasons }";
const SET_SUMMARY =
  "id fieldId profileId profileVersion profileHash seed startedAt complete objectsExamined investigationIds label stages { stage state artifactId finishedAt error summary }";

export const QUERIES = {
  experiments: `query Experiments($state: String) { experiments(state: $state) { ${SUMMARY} } }`,
  experiment: `query Experiment($id: ID!) { experiment(id: $id) {
    ${SUMMARY} predeclarationHash parameters inputs failure refusal limitations manifestId bundleDoi manifest
    tasks { taskId taskKind dependsOn status checkpoint durationS }
    checks { gateId finding value unit comparator threshold severity oracleIds oraclesVerified outcome }
    dossiers { id experimentId manifestId verdict findings limitations sections approvals } } }`,
  lineage: `query Lineage($entityId: ID!) { lineage(entityId: $entityId) }`,
  ledger: `query Ledger { ledger { id sequence kind subjectId subjectHash recordedAt signer signatureMethod previousEntryHash payload } }`,
  events: `query Events($after: Int!, $limit: Int!) { events(after: $after, limit: $limit) { sequence name aggregateId occurredAt payload } }`,
  investigationSets: `query InvestigationSets { investigationSets { ${SET_SUMMARY} } }`,
  investigationSet: `query InvestigationSet($id: ID!) { investigationSet(id: $id) {
    ${SET_SUMMARY} field profile testsApplied archiveReleases nullResults manifestId
    investigations { id candidateKey score state label rankingPolicy } } }`,
  investigation: `query Investigation($id: ID!) { investigation(id: $id) {
    id setId fieldId candidateKey candidate state label score rankingPolicy testResults evidenceBundleIds evidenceUsed
    transitions { fromState toState reason actor at } notes links dossier } }`,
} as const;

/**
 * The planner's queries (ADR-020), in the shape agreed with Stage E. They are kept apart from QUERIES
 * because the committed schema does not carry them until the planner lands; the schema test checks
 * them against that agreed extension instead.
 */
const PLAN = "id setId state document execution";
export const PLAN_QUERIES = {
  plans: `query Plans { plans { ${PLAN} } }`,
  plan: `query Plan($id: ID!) { plan(id: $id) { ${PLAN} } }`,
} as const;

// sdp-api (graphql-core) quotes names with ' and graphql-js with ", so either is accepted.
const MISSING_PLANNER = /Cannot query field ["']plans?["'] on type ["']Query["']/;

interface GraphQLResponse<T> {
  data?: T;
  errors?: { message: string }[];
}

export class GraphQLEvidenceSource implements EvidenceSource {
  readonly endpoint: string;
  readonly fetch: Fetch;

  constructor(endpoint: string, fetchImpl: Fetch = globalThis.fetch) {
    this.endpoint = endpoint;
    this.fetch = fetchImpl;
  }

  async request<T>(query: string, variables: Record<string, unknown> = {}): Promise<T> {
    let response: Response;
    try {
      response = await this.fetch(this.endpoint, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ query, variables }),
      });
    } catch (cause) {
      throw new EvidenceUnavailable(`cannot reach ${this.endpoint}: ${(cause as Error).message}`);
    }
    if (!response.ok) throw new EvidenceUnavailable(`${this.endpoint} answered ${response.status}`);
    const body = (await response.json()) as GraphQLResponse<T>;
    if (body.errors?.length) throw new EvidenceUnavailable(body.errors.map((e) => e.message).join("; "));
    if (!body.data) throw new EvidenceUnavailable(`${this.endpoint} returned no data`);
    return body.data;
  }

  async experiments(state?: string): Promise<ExperimentSummary[]> {
    const data = await this.request<{ experiments: ExperimentSummary[] }>(QUERIES.experiments, { state: state ?? null });
    return data.experiments;
  }

  async experiment(id: string): Promise<Experiment | null> {
    const data = await this.request<{ experiment: Experiment | null }>(QUERIES.experiment, { id });
    return data.experiment;
  }

  async lineage(entityId: string): Promise<string[]> {
    return (await this.request<{ lineage: string[] }>(QUERIES.lineage, { entityId })).lineage;
  }

  async ledger(): Promise<graphql.LedgerEntry[]> {
    return (await this.request<{ ledger: graphql.LedgerEntry[] }>(QUERIES.ledger)).ledger;
  }

  async events(after = 0, limit = 200): Promise<graphql.Event[]> {
    return (await this.request<{ events: graphql.Event[] }>(QUERIES.events, { after, limit })).events;
  }

  async investigationSets(): Promise<InvestigationSetSummary[]> {
    return (await this.request<{ investigationSets: InvestigationSetSummary[] }>(QUERIES.investigationSets)).investigationSets;
  }

  async investigationSet(id: string): Promise<InvestigationSet | null> {
    return (await this.request<{ investigationSet: InvestigationSet | null }>(QUERIES.investigationSet, { id })).investigationSet;
  }

  async investigation(id: string): Promise<Investigation | null> {
    return (await this.request<{ investigation: Investigation | null }>(QUERIES.investigation, { id })).investigation;
  }

  async plans(): Promise<Plan[]> {
    return (await this.planRequest<{ plans: Plan[] }>(PLAN_QUERIES.plans)).plans;
  }

  async plan(id: string): Promise<Plan | null> {
    return (await this.planRequest<{ plan: Plan | null }>(PLAN_QUERIES.plan, { id })).plan;
  }

  async planRequest<T>(query: string, variables: Record<string, unknown> = {}): Promise<T> {
    try {
      return await this.request<T>(query, variables);
    } catch (e) {
      if (e instanceof EvidenceUnavailable && MISSING_PLANNER.test(e.message)) {
        throw new PlannerUnavailable("this server does not offer the planner yet");
      }
      throw e;
    }
  }
}
