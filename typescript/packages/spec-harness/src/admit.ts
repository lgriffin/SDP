// Adapter admission (Integration v0.4.0 §6, INT-016, INT-002). `admit`: every input a manifest names as
// acquired through an adapter needs a passing compatibility record for its product type. `claims`: a
// descriptor's science claims stand only where such a record exists. The harness re-derives "passing"
// from the four results rather than trusting the record's own flag.
import type { Contract } from "@sdp/contracts";

import type { Manifest } from "./conform.ts";

export type CompatibilityRecord = Contract<"adapter-compatibility">;
export type CapabilityDescriptor = Contract<"capability-descriptor">;

export const CONFORMANCE_KINDS = ["contract", "operational", "semantic", "scientific"] as const;

/** Previews, thumbnails and auxiliary files are never evidence (INT-008), so they need no admission. */
export const NON_SCIENCE = new Set(["preview", "thumbnail", "auxiliary"]);

export interface Unadmitted {
  adapter: string;
  productType: string;
  reason: string;
}

/** The kinds that did not pass, from the record's results alone. */
export function failingKinds(record: CompatibilityRecord): string[] {
  const passed = new Map(record.results.map((r) => [r.kind, r.passed]));
  return CONFORMANCE_KINDS.filter((k) => passed.get(k) !== true);
}

function reasonAgainst(records: readonly CompatibilityRecord[], adapter: string, productType: string, version?: string): string | null {
  const matching = records.filter((r) => r.adapter === adapter && r.product_type === productType);
  if (!matching.length) return "no compatibility record";
  const versioned = version === undefined ? matching : matching.filter((r) => r.adapter_version === version);
  if (!versioned.length) return `records exist only for versions ${matching.map((r) => r.adapter_version).join(", ")}, not ${version}`;
  const reasons: string[] = [];
  for (const record of versioned) {
    const failing = failingKinds(record);
    if (!failing.length) return null;
    reasons.push(`${record.adapter_version}: ${failing.join(", ")} did not pass${record.passed ? " although the record says passed" : ""}`);
  }
  return reasons.join("; ");
}

/** Every adapter and product type a manifest's inputs used without a passing record in its environment. */
export function admit(manifest: Manifest): Unadmitted[] {
  const records = manifest.environment.adapter_compatibility ?? [];
  const used = new Map<string, { adapter: string; productType: string }>();
  for (const input of manifest.inputs) {
    if (input.adapter && input.product_type) used.set(`${input.adapter}\u0000${input.product_type}`, { adapter: input.adapter, productType: input.product_type });
  }
  const out: Unadmitted[] = [];
  for (const { adapter, productType } of [...used.values()].sort((a, b) => `${a.adapter}/${a.productType}`.localeCompare(`${b.adapter}/${b.productType}`))) {
    const reason = reasonAgainst(records, adapter, productType);
    if (reason) out.push({ adapter, productType, reason });
  }
  return out;
}

export interface ClaimCheck {
  sourceId: string;
  adapter: string | null;
  /** Science product types claimed without a passing record for the descriptor's adapter and version. */
  removed: { productType: string; reason: string }[];
  /** The descriptor with those claims taken out. */
  stripped: CapabilityDescriptor;
}

/** Strip every science claim that no passing compatibility record backs (INT-002). */
export function checkClaims(descriptor: CapabilityDescriptor, records: readonly CompatibilityRecord[]): ClaimCheck {
  const adapter = descriptor.adapter ?? null;
  const removed: ClaimCheck["removed"] = [];
  for (const productType of [...descriptor.product_types].sort()) {
    if (NON_SCIENCE.has(productType)) continue;
    const reason = adapter ? reasonAgainst(records, adapter.name, productType, adapter.version) : "no SDP adapter serves this source";
    if (reason) removed.push({ productType, reason });
  }
  const gone = new Set(removed.map((r) => r.productType));
  return {
    sourceId: descriptor.source_id,
    adapter: adapter ? `${adapter.name} ${adapter.version}` : null,
    removed,
    stripped: { ...descriptor, product_types: descriptor.product_types.filter((t) => !gone.has(t)) },
  };
}
