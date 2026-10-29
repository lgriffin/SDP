// Workbench over sdp-api's command endpoints (ADR-016). Signed steps are not here: they stay on the
// CLI, and the pages show the command to copy.
import {
  CommandRefused,
  EvidenceUnavailable,
  PlannerUnavailable,
  type InvestigateRequest,
  type PlanState,
  type TriageAction,
  type Workbench,
} from "../ports.ts";

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** A fetch that adds sdp-api's bearer token (SDP_API_TOKEN, set by the desktop app) to every request. */
export function withToken(fetchImpl: Fetch, token: string | undefined): Fetch {
  if (!token) return fetchImpl;
  return (input, init) =>
    fetchImpl(input, { ...init, headers: { ...(init?.headers as Record<string, string> | undefined), authorization: `Bearer ${token}` } });
}

/** The REST base for a GraphQL endpoint: http://host:8000/graphql serves commands at http://host:8000. */
export function commandBase(graphqlEndpoint: string): string {
  return graphqlEndpoint.replace(/\/graphql\/?$/, "");
}

function investigateBody(r: InvestigateRequest): Record<string, unknown> {
  return { region: r.region, epoch_jyear: r.epochJyear, label: r.label, profile: r.profile, seed: r.seed, actor: r.actor };
}

function planState(body: Record<string, unknown>): PlanState {
  if (typeof body.state === "string") return body.state as PlanState;
  const approval = (body.approval ?? null) as { decision?: PlanState } | null;
  return approval?.decision ?? "drafted";
}

export class RestWorkbench implements Workbench {
  readonly base: string;
  readonly fetch: Fetch;

  constructor(base: string, fetchImpl: Fetch = globalThis.fetch) {
    this.base = base.replace(/\/$/, "");
    this.fetch = fetchImpl;
  }

  async post(path: string, body: Record<string, unknown> = {}, planner = false): Promise<Record<string, unknown>> {
    let response: Response;
    try {
      response = await this.fetch(this.base + path, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify(body),
      });
    } catch (cause) {
      throw new EvidenceUnavailable(`cannot reach ${this.base}: ${(cause as Error).message}`);
    }
    const text = await response.text();
    let parsed: Record<string, unknown> | null = null;
    try {
      parsed = JSON.parse(text) as Record<string, unknown>;
    } catch {
      parsed = null;
    }
    if (response.ok && parsed) return parsed;
    // An unknown route answers in plain text; a refused command answers in JSON with its reason.
    // A server that has the routes but no planner wired says so with a 503 typed PlannerUnavailable.
    const unwired = response.status === 503 && parsed?.type === "PlannerUnavailable";
    if (planner && (unwired || ((response.status === 404 || response.status === 405) && !parsed?.error))) {
      throw new PlannerUnavailable("this server does not offer the planner yet");
    }
    if (response.status >= 400 && response.status < 500 && parsed?.error) {
      throw new CommandRefused(String(parsed.error), response.status);
    }
    throw new EvidenceUnavailable(`${this.base}${path} answered ${response.status}`);
  }

  async runExperiment(id: string): Promise<{ experimentId: string; state: string }> {
    const r = await this.post(`/experiments/${encodeURIComponent(id)}/run`);
    return { experimentId: String(r.experiment_id), state: String(r.state) };
  }

  async validateExperiment(id: string): Promise<{ experimentId: string; verdict: string }> {
    const r = await this.post(`/experiments/${encodeURIComponent(id)}/validate`);
    return { experimentId: String(r.experiment_id), verdict: String(r.verdict) };
  }

  async investigate(request: InvestigateRequest): Promise<{ setId: string }> {
    const r = await this.post("/investigations", investigateBody(request));
    return { setId: String(r.set_id) };
  }

  async triage(investigationId: string, action: TriageAction, actor: string, reason: string): Promise<{ state: string }> {
    const r = await this.post(`/investigations/${encodeURIComponent(investigationId)}/triage`, { action, actor, reason });
    return { state: String(r.state) };
  }

  async planInvestigation(request: InvestigateRequest): Promise<{ planId: string }> {
    const r = await this.post("/plans", investigateBody(request), true);
    return { planId: String(r.plan_id) };
  }

  async approvePlan(planId: string, actor: string): Promise<{ state: PlanState }> {
    return { state: planState(await this.post(`/plans/${encodeURIComponent(planId)}/approve`, { actor }, true)) };
  }

  async refusePlan(planId: string, actor: string, reason: string): Promise<{ state: PlanState }> {
    return { state: planState(await this.post(`/plans/${encodeURIComponent(planId)}/refuse`, { actor, reason }, true)) };
  }

  async executePlan(planId: string): Promise<{ state: PlanState }> {
    await this.post(`/plans/${encodeURIComponent(planId)}/execute`, {}, true);
    return { state: "executed" };
  }
}
