// RFC 8785 canonical JSON (JCS), matching sdp.kernel.domain.canonical byte for byte (§7).
import { createHash } from "node:crypto";

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

const MAX_SAFE = 2 ** 53;

export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("non-finite numbers have no JSON form");
    if (Number.isInteger(value) && Math.abs(value) > MAX_SAFE) {
      // ECMAScript formatting is still well defined; the kernel only refuses integers it cannot round-trip.
      return String(value);
    }
    return Object.is(value, -0) ? "0" : String(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined);
    // RFC 8785 orders keys by UTF-16 code units, which is JavaScript's default string order.
    entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(",")}}`;
  }
  throw new Error(`${typeof value} has no JSON form`);
}

export function sha256Id(data: string | Uint8Array): string {
  return `sha256:${createHash("sha256").update(data).digest("hex")}`;
}

export function hashDocument(value: unknown): string {
  return sha256Id(canonicalJson(value));
}
