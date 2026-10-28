// Plain-language view logic for scientists: what a state means, where a piece of work is on its path,
// the one next step, and the geometry of the gate ruler. Pure, and unit-tested without a browser.
import type { ExperimentSummary, GateCheck, Investigation, InvestigationSetSummary, Plan, PlanState } from "./ports.ts";
import type { Tone } from "./view.ts";

const PROGRAMMES: Record<string, string> = {
  "uc01-stellar-clusters": "Stellar clusters",
  "uc02-einstein-1919": "Einstein 1919 light deflection",
  "synthetic-linear": "Synthetic test protocol",
};

/** The programme a protocol belongs to, in words. Unknown protocols keep their registered name. */
export function programmeName(protocol: string): string {
  return PROGRAMMES[protocol] ?? protocol;
}

const EXPERIMENT_STATES: Record<string, string> = {
  defined: "Ready to run",
  running: "Running",
  failed: "Run failed",
  refused: "Refused",
  completed: "Ready to check",
  validated: "Checked",
  approved: "Approved",
  published: "Published",
};

export function experimentStateLabel(state: string): string {
  return EXPERIMENT_STATES[state] ?? state;
}

const VERDICTS: Record<string, { title: string; sentence: string }> = {
  VERIFIED: {
    title: "Verified",
    sentence: "Every check set before the run passed. The result holds under the rules that were frozen in advance.",
  },
  REJECTED: {
    title: "Rejected",
    sentence: "A required check failed, so the hypothesis is rejected under the rules that were frozen in advance.",
  },
  INCONCLUSIVE: {
    title: "Inconclusive",
    sentence:
      "A required check could not be decided, because a measurement is missing or its reference values are not yet verified. Nothing is concluded either way.",
  },
  CANDIDATE: {
    title: "Candidate",
    sentence: "Every required check passed but an advisory one did not. Treat this as promising, and in need of more evidence.",
  },
};

export function verdictTitle(status: string): string {
  return VERDICTS[status]?.title ?? status;
}

export function verdictSentence(status: string): string {
  return VERDICTS[status]?.sentence ?? "The kernel issued a verdict this page does not know how to describe.";
}

const CANDIDATE_STATES: Record<string, string> = {
  open: "To review",
  parked: "Parked",
  closed: "Closed",
  promoted: "Promoted",
};

export function candidateStateLabel(state: string): string {
  return CANDIDATE_STATES[state] ?? state;
}

const PLAN_STATES: Record<PlanState, string> = {
  drafted: "Waiting for a decision",
  auto_approved: "Approved (within budget)",
  approved: "Approved (signed)",
  refused: "Refused",
  executed: "Downloaded",
};

export function planStateLabel(state: PlanState): string {
  return PLAN_STATES[state] ?? state;
}

export function planTone(state: PlanState): Tone {
  if (state === "refused") return "bad";
  if (state === "drafted") return "warn";
  return "good";
}

/** Above the budget's threshold, approval is a signed ledger entry made from the CLI (INT-014). */
export function needsSignature(plan: Plan): boolean {
  return plan.document.cost.total_bytes > plan.document.constraints.budget.approval_threshold_bytes;
}

export function withinBudget(plan: Plan): boolean {
  const { cost, constraints } = plan.document;
  return cost.total_bytes <= constraints.budget.max_bytes && cost.estimated_wall_seconds <= constraints.budget.max_wall_seconds;
}

// --- the journey --------------------------------------------------------------------------------

export type StepStatus = "done" | "current" | "todo" | "failed";

export interface JourneyStep {
  label: string;
  status: StepStatus;
}

const EXPERIMENT_PATH = ["Planned", "Run", "Checked", "Approved", "Published"] as const;
const EXPERIMENT_REACHED: Record<string, number> = {
  defined: 0,
  running: 1,
  failed: 1,
  refused: 1,
  completed: 1,
  validated: 2,
  approved: 3,
  published: 4,
};

/**
 * An experiment's path: planned (defined against a frozen predeclaration), run, checked against its
 * gates, approved by a reviewer, published. The step it is on is current; a failed run fails its step.
 */
export function experimentJourney(state: string): JourneyStep[] {
  const reached = EXPERIMENT_REACHED[state] ?? 0;
  const busy = state === "running" || state === "failed" || state === "refused";
  return EXPERIMENT_PATH.map((label, i) => {
    if (busy && i === reached) return { label, status: state === "running" ? "current" : "failed" };
    if (busy && i > reached) return { label, status: "todo" };
    if (i <= reached) return { label, status: "done" };
    if (i === reached + 1) return { label, status: "current" };
    return { label, status: "todo" };
  });
}

/** A field investigation's path: download plan, the search stages, then review of the candidates. */
export function setJourney(set: Pick<InvestigationSetSummary, "stages" | "complete">, plan: Plan | null, openCandidates: number): JourneyStep[] {
  const steps: JourneyStep[] = [];
  if (plan) steps.push({ label: "Plan", status: plan.state === "refused" ? "failed" : "done" });
  const searched = set.stages.every((s) => s.state === "completed");
  const failed = set.stages.some((s) => s.state === "failed");
  steps.push({ label: "Search", status: failed ? "failed" : searched ? "done" : "current" });
  steps.push({ label: "Test", status: failed ? "todo" : set.complete ? "done" : searched ? "current" : "todo" });
  steps.push({ label: "Review", status: !set.complete ? "todo" : openCandidates > 0 ? "current" : "done" });
  return steps;
}

export interface NextStep {
  /** A command the UI can send, a command to copy because it needs a signature, or nothing to do. */
  kind: "run" | "validate" | "copy" | "wait" | "none";
  label: string;
  explanation: string;
  command?: string;
}

export function experimentNextStep(e: Pick<ExperimentSummary, "id" | "state"> & { dossiers?: { id: string }[] }): NextStep {
  const dossier = e.dossiers?.[0]?.id;
  switch (e.state) {
    case "defined":
      return { kind: "run", label: "Run it", explanation: "The plan is frozen and the inputs are in place." };
    case "failed":
      return { kind: "run", label: "Run it again", explanation: "Finished steps are kept; the run picks up where it stopped." };
    case "running":
      return { kind: "wait", label: "Running", explanation: "Come back when it finishes." };
    case "completed":
      return { kind: "validate", label: "Check against the gates", explanation: "Compare the results with the checks frozen before the run." };
    case "validated":
      return dossier
        ? {
            kind: "copy",
            label: "Ask a reviewer to approve",
            explanation: "Approval is signed, so a reviewer runs this in a terminal.",
            command: `sdp approve ${e.id} --reviewer <your-signer-name>`,
          }
        : {
            kind: "copy",
            label: "Write up the limitations",
            explanation: "A dossier states what the result cannot tell you. Assemble it in a terminal.",
            command: `sdp dossier assemble ${e.id} --limitation "<what this result cannot show>"`,
          };
    case "approved":
      return {
        kind: "copy",
        label: "Publish",
        explanation: "Publishing mints a DOI for the bundle; it is done in a terminal.",
        command: `sdp publish ${dossier ?? "<dossier-id>"}`,
      };
    default:
      return { kind: "none", label: "Nothing to do", explanation: "This piece of work is finished." };
  }
}

// --- what is waiting -------------------------------------------------------------------------------

export interface WaitingItem {
  href: string;
  title: string;
  detail: string;
  action: string;
}

export function waitingItems(
  experiments: ExperimentSummary[],
  sets: InvestigationSetSummary[],
  openBySet: Record<string, number>,
  plans: Plan[],
): WaitingItem[] {
  const items: WaitingItem[] = [];
  for (const p of plans.filter((x) => x.state === "drafted")) {
    items.push({
      href: planHref(p.id),
      title: `Download plan for ${fieldLabel(p.document.intent.field)}`,
      detail: `${humanBytes(p.document.cost.total_bytes)} from ${Object.keys(p.document.cost.requests_per_source).length} archives`,
      action: needsSignature(p) ? "Needs a signed approval" : "Approve or refuse",
    });
  }
  for (const e of experiments) {
    const step = experimentNextStep(e);
    if (step.kind === "run" || step.kind === "validate") {
      items.push({ href: `/experiments/${e.id}`, title: `${programmeName(e.protocol)}, ${e.id}`, detail: experimentStateLabel(e.state), action: step.label });
    }
  }
  for (const s of sets) {
    const open = openBySet[s.id] ?? 0;
    if (open > 0) {
      items.push({
        href: `/investigations/${s.id}`,
        title: `Candidates from ${s.profileId}`,
        detail: `${open} candidate${open === 1 ? "" : "s"} not yet reviewed`,
        action: "Review",
      });
    }
  }
  return items;
}

// --- formatting ------------------------------------------------------------------------------------

export function planHref(planId: string): string {
  return `/plans/${encodeURIComponent(planId)}`;
}

export function humanBytes(n: number): string {
  if (n < 1000) return `${n} bytes`;
  const units = ["kB", "MB", "GB", "TB", "PB"];
  let value = n;
  let unit = -1;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value >= 100 ? Math.round(value) : Number(value.toPrecision(3))} ${units[unit]}`;
}

export function humanDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** "28 Oct 2026, 12:00" in UTC: a timestamp a person can read. */
export function humanTime(iso: string | null | undefined): string {
  if (!iso) return "not yet";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `${date}, ${time} UTC`;
}

interface RegionDoc {
  shape?: string;
  ra_deg?: number;
  dec_deg?: number;
  radius_deg?: number;
}

/** A field in words: its label if it has one, else its centre and size. */
export function fieldLabel(field: unknown): string {
  const f = (field ?? {}) as { label?: string; region?: RegionDoc };
  if (f.label) return f.label;
  return regionLabel(f.region);
}

export function regionLabel(region: RegionDoc | undefined): string {
  if (!region) return "an unnamed field";
  const ra = region.ra_deg !== undefined ? `RA ${region.ra_deg}°` : "";
  const dec = region.dec_deg !== undefined ? `Dec ${region.dec_deg}°` : "";
  const size = region.radius_deg !== undefined ? `, radius ${region.radius_deg}°` : "";
  return `${[ra, dec].filter(Boolean).join(", ")}${size}` || "an unnamed field";
}

/** The DS9-style region text the kernel parses, from a centre and radius in degrees. */
export function coneRegion(ra: number, dec: number, radius: number): string {
  return `icrs;circle(${ra},${dec},${radius})`;
}

export function openCandidates(investigations: Pick<Investigation, "state">[]): number {
  return investigations.filter((i) => i.state === "open").length;
}

// --- the gate ruler --------------------------------------------------------------------------------

export interface Ruler {
  /** Positions along the scale, 0 to 100. */
  threshold: number;
  value: number;
  /** The passing side of the threshold, or null when the comparator has no side. */
  passFrom: number;
  passTo: number;
  log: boolean;
  min: number;
  max: number;
}

/**
 * Where the measured value and the threshold fall on one scale. Values spanning more than two decades
 * use a log scale, so a p-value of 2e-4 against 0.05 is still readable. Non-numeric gates get no ruler.
 */
export function gateRuler(check: Pick<GateCheck, "value" | "threshold" | "comparator">): Ruler | null {
  const v = check.value;
  const t = check.threshold;
  if (typeof v !== "number" || typeof t !== "number" || !Number.isFinite(v) || !Number.isFinite(t)) return null;
  const log = v > 0 && t > 0 && Math.max(v, t) / Math.min(v, t) > 100;
  const f = log ? Math.log10 : (x: number) => x;
  let lo = Math.min(f(v), f(t));
  let hi = Math.max(f(v), f(t));
  if (!log) lo = Math.min(lo, 0);
  const span = hi - lo || Math.abs(hi) || 1;
  lo -= span * (log ? 0.25 : 0);
  hi += span * 0.25;
  const at = (x: number) => Math.round(((f(x) - lo) / (hi - lo)) * 1000) / 10;
  const threshold = at(t);
  const below = check.comparator === "lt" || check.comparator === "le";
  const above = check.comparator === "gt" || check.comparator === "ge";
  return {
    threshold,
    value: at(v),
    passFrom: below ? 0 : above ? threshold : threshold,
    passTo: below ? threshold : above ? 100 : threshold,
    log,
    min: lo,
    max: hi,
  };
}

const COMPARATOR_WORDS: Record<string, string> = {
  lt: "below",
  le: "at most",
  gt: "above",
  ge: "at least",
  eq: "exactly",
  ne: "anything but",
};

/** "slope abs error must be below 0.05": a gate as a sentence. */
export function gateSentence(check: Pick<GateCheck, "finding" | "comparator" | "threshold" | "unit">, format: (v: unknown) => string): string {
  const words = COMPARATOR_WORDS[check.comparator] ?? check.comparator;
  const unit = check.unit ? ` ${check.unit}` : "";
  return `${humanFinding(check.finding)} must be ${words} ${format(check.threshold)}${unit}`;
}

export function humanFinding(finding: string): string {
  const words = finding.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const OUTCOMES: Record<string, string> = {
  pass: "Passed",
  fail: "Failed",
  missing: "No measurement",
  oracle_unverified: "Reference not verified",
};

export function outcomeLabel(outcome: string): string {
  return OUTCOMES[outcome] ?? outcome;
}

export function outcomeTone(outcome: string): Tone {
  return outcome === "pass" ? "good" : outcome === "fail" ? "bad" : "warn";
}
