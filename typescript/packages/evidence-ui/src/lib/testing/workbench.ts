// The fake Workbench (ADR-006): commands change the in-memory evidence the same way the kernel's
// rules would, so pages and browser tests see the result of a button. It is a demonstration server:
// validation issues no real verdict, and Investigate reopens the one synthetic set.
import {
  type ArchivesStatus,
  CommandRefused,
  type InvestigateRequest,
  type JobRun,
  type Investigation,
  type PlanState,
  type ReproductionRun,
  type TriageAction,
  type Workbench,
} from "../ports.ts";
import { EXAMPLES } from "../examples.ts";
import { coneRegion } from "../plain.ts";
import { type InMemoryEvidenceSource, REGISTERED_ARCHIVES } from "./fixtures.ts";

const TRIAGE: Record<Exclude<TriageAction, "annotate">, { to: string; from: string[] }> = {
  park: { to: "parked", from: ["open"] },
  reopen: { to: "open", from: ["parked"] },
  close: { to: "closed", from: ["open", "parked", "promoted"] },
};

export interface RecordedCommand {
  command: string;
  args: unknown[];
}

export class InMemoryWorkbench implements Workbench {
  readonly source: InMemoryEvidenceSource;
  readonly commands: RecordedCommand[] = [];
  readonly now: () => string;

  constructor(source: InMemoryEvidenceSource, now: () => string = () => new Date().toISOString()) {
    this.source = source;
    this.now = now;
  }

  experimentOrRefuse(id: string) {
    const e = this.source.data.experiment[id];
    if (!e) throw new CommandRefused(`no experiment ${id}`, 404);
    return e;
  }

  setState(id: string, state: string, patch: Record<string, unknown> = {}): void {
    Object.assign(this.experimentOrRefuse(id), { state, ...patch });
    const summary = this.source.data.experiments.find((e) => e.id === id);
    if (summary) Object.assign(summary, { state, ...patch });
  }

  async runExperiment(id: string): Promise<{ experimentId: string; state: string }> {
    this.commands.push({ command: "run", args: [id] });
    const e = this.experimentOrRefuse(id);
    if (e.state !== "defined" && e.state !== "failed") {
      throw new CommandRefused(`experiment ${id} is ${e.state}; only a defined or failed experiment can run`, 409);
    }
    const at = this.now();
    this.setState(id, "completed", { startedAt: e.startedAt ?? at, finishedAt: at, failure: null });
    return { experimentId: id, state: "completed" };
  }

  async validateExperiment(id: string): Promise<{ experimentId: string; verdict: string }> {
    this.commands.push({ command: "validate", args: [id] });
    const e = this.experimentOrRefuse(id);
    if (e.state !== "completed") throw new CommandRefused(`experiment ${id} is ${e.state}; only a completed run can be validated`, 409);
    const verdict = { status: "INCONCLUSIVE", protocolVersion: e.protocolVersion, reasons: ["demonstration server: no gates were evaluated"] };
    this.setState(id, "validated", { verdict });
    return { experimentId: id, verdict: verdict.status };
  }

  async investigate(request: InvestigateRequest): Promise<{ setId: string }> {
    this.commands.push({ command: "investigate", args: [request] });
    const first = this.source.data.investigationSets[0];
    if (!first) throw new CommandRefused("the demonstration server has no field to investigate", 409);
    return { setId: first.id };
  }

  investigationOrRefuse(id: string): Investigation {
    const i = this.source.data.investigation[id];
    if (!i) throw new CommandRefused(`no investigation ${id}`, 404);
    return i;
  }

  async triage(investigationId: string, action: TriageAction, actor: string, reason: string): Promise<{ state: string }> {
    this.commands.push({ command: "triage", args: [investigationId, action, actor, reason] });
    const i = this.investigationOrRefuse(investigationId);
    if (!actor.trim()) throw new CommandRefused("every triage step records who took it", 400);
    if (!reason.trim()) throw new CommandRefused(action === "annotate" ? "a note needs some text" : "every triage step records its reason", 400);
    const at = this.now();
    if (action === "annotate") {
      (i.notes as unknown[]).push({ text: reason, author: actor, at });
      return { state: i.state };
    }
    const rule = TRIAGE[action];
    if (!rule.from.includes(i.state)) {
      throw new CommandRefused(`investigation ${investigationId} cannot move from ${i.state} to ${rule.to}`, 409);
    }
    i.transitions.push({ fromState: i.state, toState: rule.to, reason, actor, at });
    i.state = rule.to;
    const summary = this.source.data.investigationSet[i.setId]?.investigations.find((s) => s.id === investigationId);
    if (summary) summary.state = rule.to;
    return { state: rule.to };
  }

  planOrRefuse(id: string) {
    const p = this.source.data.plans.find((x) => x.id === id);
    if (!p) throw new CommandRefused(`no plan ${id}`, 404);
    return p;
  }

  async planInvestigation(request: InvestigateRequest): Promise<{ planId: string }> {
    this.commands.push({ command: "plan", args: [request] });
    // The JWST example gets a fresh draft of the synthetic JWST plan, so the demonstration walks the
    // whole JWST journey: plan, approve, download, images, candidates and dossier. Any other field
    // that reads MAST is refused, rather than answered with a plan made for a different field.
    const archives = (request.profile.archives ?? []) as { source_id?: string }[];
    if (archives.some((a) => a.source_id === "mast")) {
      const example = EXAMPLES.find((e) => e.key === "jwst-m92")!;
      const asked = request.region === coneRegion(Number(example.ra), Number(example.dec), Number(example.radius));
      const template = this.source.data.plans.find((p) => p.setId && p.document.items.some((i) => i.source_id.endsWith("mast")));
      if (!asked || request.profile.profile_id !== example.profile || !template) {
        throw new CommandRefused("The demonstration has one JWST field: choose the example \"JWST: the outskirts of M92\" as it is.", 409);
      }
      const plan = structuredClone(template);
      const n = this.source.data.plans.length;
      plan.id = plan.document.plan_id = `sha256:${n.toString(16).padStart(8, "0")}${"d".repeat(56)}`;
      Object.assign(plan, { state: "drafted", setId: null, execution: null, run: null });
      plan.document.approval = null;
      this.source.data.plans.push(plan);
      this.source.demoSets[plan.id] = template.setId!;
      return { planId: plan.id };
    }
    const drafted = this.source.data.plans.find((p) => p.state === "drafted");
    if (!drafted) throw new CommandRefused("the demonstration server has no plan to draft", 409);
    return { planId: drafted.id };
  }

  async approvePlan(planId: string, actor: string): Promise<{ state: PlanState }> {
    this.commands.push({ command: "approve-plan", args: [planId, actor] });
    const p = this.planOrRefuse(planId);
    if (p.state !== "drafted") throw new CommandRefused(`plan is ${p.state}; only a drafted plan can be decided`, 409);
    const threshold = p.document.constraints.budget.approval_threshold_bytes;
    if (p.document.cost.total_bytes > threshold) throw new CommandRefused("a signed ledger approval is required (INT-014)", 409);
    p.state = "auto_approved";
    p.document.approval = { plan_id: p.id, decision: "auto_approved", actor, threshold_bytes: threshold, ledger_entry_id: null, reason: "" };
    return { state: p.state };
  }

  async refusePlan(planId: string, actor: string, reason: string): Promise<{ state: PlanState }> {
    this.commands.push({ command: "refuse-plan", args: [planId, actor, reason] });
    const p = this.planOrRefuse(planId);
    if (p.state !== "drafted") throw new CommandRefused(`plan is ${p.state}; only a drafted plan can be decided`, 409);
    if (!reason.trim()) throw new CommandRefused("a refusal records why", 400);
    const threshold = p.document.constraints.budget.approval_threshold_bytes;
    p.state = "refused";
    p.document.approval = { plan_id: p.id, decision: "refused", actor, threshold_bytes: threshold, ledger_entry_id: null, reason };
    return { state: p.state };
  }

  async executePlan(planId: string, actor: string): Promise<{ state: PlanState; run: JobRun | null }> {
    this.commands.push({ command: "execute-plan", args: [planId, actor] });
    const p = this.planOrRefuse(planId);
    if (p.run?.state === "running") return { state: p.state, run: p.run };
    if (p.state !== "approved" && p.state !== "auto_approved") throw new CommandRefused(`plan ${planId} is ${p.state}; only an approved plan runs`, 409);
    p.run = { state: "running", started_at: this.now(), finished_at: null, error: null };
    return { state: p.state, run: p.run };
  }

  async registerArchives(): Promise<ArchivesStatus> {
    this.commands.push({ command: "register-archives", args: [] });
    const now = this.now();
    this.source.data.archives = { sources: REGISTERED_ARCHIVES.sources, registration: { state: "finished", started_at: now, finished_at: now, error: null } };
    return this.source.data.archives;
  }

  /** A demonstration check reads as running, then reproduced: its results are recorded, not recomputed. */
  async reproduceSet(setId: string): Promise<ReproductionRun> {
    this.commands.push({ command: "reproduce", args: [setId] });
    const set = this.source.data.investigationSet[setId];
    if (!set) throw new CommandRefused(`no investigation set ${setId}`, 404);
    const current = this.source.data.reproductions[setId];
    if (current?.state === "running") return current;
    const run: ReproductionRun = { state: "running", started_at: this.now(), finished_at: null, error: null };
    this.source.data.reproductions[setId] = run;
    return run;
  }
}
