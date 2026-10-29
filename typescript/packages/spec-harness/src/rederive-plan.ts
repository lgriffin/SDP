// Independent plan re-derivation (INT-013, INT-014): recompute a plan's id, its minimum product set and
// gaps from the requirements, matches and candidates it records, its cost, and the approval rule, and
// compare with what the kernel recorded. Mirrors the kernel's select_items exactly.
import { hashDocument } from "@sdp/contracts";

import type { Disagreement } from "./rederive.ts";

export interface PlanItem {
  item_id: string;
  source_id: string;
  operation: string;
  target: string;
  expected_bytes: number;
  requirement_id: string;
  [key: string]: unknown;
}

export interface PlanGap {
  requirement_id: string;
  reason: string;
}

export interface PlanDocument {
  plan_id: string;
  intent: { requirements: { requirement_id: string }[]; [key: string]: unknown };
  discovery: { source_id: string; requirement_id: string; status: string }[];
  items: PlanItem[];
  cost: { total_bytes: number; requests_per_source: Record<string, number>; [key: string]: unknown };
  constraints: { budget: { approval_threshold_bytes: number }; candidates?: PlanItem[]; [key: string]: unknown };
  gaps: PlanGap[];
  approval?: {
    plan_id: string;
    decision: "auto_approved" | "approved" | "refused";
    threshold_bytes: number;
    ledger_entry_id: string | null;
  } | null;
  [key: string]: unknown;
}

export interface PlanRederivation {
  planId: string;
  state: "agree" | "disagree";
  /** False when the plan records no candidates, so its items and gaps cannot be re-derived. */
  itemsRederived: boolean;
  disagreements: Disagreement[];
}

const USABLE = new Set(["satisfied", "partial"]);
// Python compares strings by code point; localeCompare would not.
const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
const cmpTuple = (a: readonly (string | number)[], b: readonly (string | number)[]): number => {
  for (let i = 0; i < a.length; i++) {
    const x = a[i]!;
    const y = b[i]!;
    const c = typeof x === "number" && typeof y === "number" ? x - y : cmp(String(x), String(y));
    if (c) return c;
  }
  return 0;
};

/** The minimum product set and gaps, as the kernel's select_items computes them. */
export function selectItems(
  requirements: readonly { requirement_id: string }[],
  matches: readonly { source_id: string; requirement_id: string; status: string }[],
  candidates: readonly PlanItem[],
): { items: PlanItem[]; gaps: PlanGap[] } {
  const order = new Map(requirements.map((r, n) => [r.requirement_id, n]));
  const status = new Map(matches.map((m) => [`${m.source_id}\u0000${m.requirement_id}`, m.status]));
  const rank = (c: PlanItem): number => order.get(c.requirement_id) ?? order.size;
  const chosen = new Map<string, { key: string[]; item: PlanItem }>();
  const served = new Set<string>();
  const sorted = [...candidates].sort((a, b) => cmpTuple([rank(a), a.source_id, a.target], [rank(b), b.source_id, b.target]));
  for (const c of sorted) {
    if (!order.has(c.requirement_id) || !USABLE.has(status.get(`${c.source_id}\u0000${c.requirement_id}`) ?? "")) continue;
    served.add(c.requirement_id);
    const key = [c.source_id, c.operation, c.target];
    const id = key.join("\u0000");
    if (!chosen.has(id)) chosen.set(id, { key, item: c });
  }
  const items = [...chosen.values()]
    .sort((a, b) => cmpTuple(a.key, b.key))
    .map(({ item }, n) => ({ ...item, item_id: `item-${String(n + 1).padStart(3, "0")}` }));
  const gaps: PlanGap[] = [];
  for (const r of requirements) {
    if (served.has(r.requirement_id)) continue;
    const offered = [...new Set(candidates.filter((c) => c.requirement_id === r.requirement_id).map((c) => c.source_id))].sort(cmp);
    const reason = offered.length
      ? "no source that lists it satisfies it: " +
        offered
          .map((src) => {
            const s = status.get(`${src}\u0000${r.requirement_id}`);
            return s === undefined ? `${src} (no registered descriptor)` : `${src} (${s})`;
          })
          .join(", ")
      : "no source in the profile lists anything for it";
    gaps.push({ requirement_id: r.requirement_id, reason });
  }
  return { items, gaps };
}

const same = (a: unknown, b: unknown): boolean => hashDocument(a) === hashDocument(b);

function compareList<T>(subject: string, kernel: readonly T[], harness: readonly T[], key: (t: T) => string, out: Disagreement[]): void {
  const recorded = new Map(kernel.map((k) => [key(k), k]));
  for (const h of harness) {
    const k = recorded.get(key(h));
    if (!k || !same(k, h)) out.push({ subject: `${subject} ${key(h)}`, kernel: k ?? null, harness: h });
    recorded.delete(key(h));
  }
  for (const [id, k] of recorded) out.push({ subject: `${subject} ${id}`, kernel: k, harness: "not in the re-derived set" });
}

export function rederivePlan(plan: PlanDocument): PlanRederivation {
  const disagreements: Disagreement[] = [];
  const { plan_id: planId, approval, ...intent } = plan;
  const hash = hashDocument(intent);
  if (hash !== planId) disagreements.push({ subject: "plan_id", kernel: planId, harness: hash });

  const candidates = plan.constraints.candidates;
  if (candidates) {
    const { items, gaps } = selectItems(plan.intent.requirements, plan.discovery, candidates);
    compareList("item", plan.items, items, (i) => i.item_id, disagreements);
    compareList("gap", plan.gaps, gaps, (g) => g.requirement_id, disagreements);
  }

  const total = plan.items.reduce((n, i) => n + i.expected_bytes, 0);
  if (total !== plan.cost.total_bytes) disagreements.push({ subject: "cost.total_bytes", kernel: plan.cost.total_bytes, harness: total });
  const requests: Record<string, number> = {};
  for (const i of plan.items) requests[i.source_id] = (requests[i.source_id] ?? 0) + 1;
  if (!same(requests, plan.cost.requests_per_source)) {
    disagreements.push({ subject: "cost.requests_per_source", kernel: plan.cost.requests_per_source, harness: requests });
  }

  if (approval) {
    const threshold = plan.constraints.budget.approval_threshold_bytes;
    if (approval.plan_id !== planId) disagreements.push({ subject: "approval.plan_id", kernel: approval.plan_id, harness: planId });
    if (approval.threshold_bytes !== threshold) {
      disagreements.push({ subject: "approval.threshold_bytes", kernel: approval.threshold_bytes, harness: threshold });
    }
    if (approval.decision === "approved" && !approval.ledger_entry_id) {
      disagreements.push({ subject: "approval", kernel: "approved", harness: "an approval above the threshold names its signed ledger entry" });
    }
    if (approval.decision === "auto_approved" && plan.cost.total_bytes > threshold) {
      disagreements.push({
        subject: "approval",
        kernel: "auto_approved",
        harness: `${plan.cost.total_bytes} bytes is above the approval threshold of ${threshold}; a signed approval is required`,
      });
    }
  }

  return { planId, state: disagreements.length ? "disagree" : "agree", itemsRederived: candidates !== undefined, disagreements };
}
