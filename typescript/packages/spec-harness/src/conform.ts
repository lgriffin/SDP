// Manifest conformance (CTR-004): read a manifest of the current or previous schema major, or refuse it.
import { SCHEMA_MAJOR, schemaMajor, validate, type Contract, type ContractError } from "@sdp/contracts";

export type Manifest = Contract<"evidence-manifest">;

export interface ManifestReader {
  major: number;
  /** Errors against this major's schema; empty when the document conforms. */
  validate(document: unknown): ContractError[];
  /** Lift a conforming document to the current shape. */
  upgrade(document: unknown): Manifest;
}

const current: ManifestReader = {
  major: SCHEMA_MAJOR,
  validate: (document) => validate("evidence-manifest", document),
  upgrade: (document) => document as Manifest,
};

/**
 * One reader per supported major: the current one and, once a 2.x exists, the previous one.
 * Contracts started at 1.0.0, so today the previous slot (0.x) has no schema and stays empty.
 */
export const MANIFEST_READERS: ReadonlyMap<number, ManifestReader> = new Map([[current.major, current]]);

export const SUPPORTED_MAJORS = [SCHEMA_MAJOR, SCHEMA_MAJOR - 1] as const;

export type ConformResult =
  | { ok: true; major: number; manifest: Manifest }
  | { ok: false; major: number | null; errors: ContractError[] };

export function readManifest(
  document: unknown,
  readers: ReadonlyMap<number, ManifestReader> = MANIFEST_READERS,
): ConformResult {
  if (typeof document !== "object" || document === null || Array.isArray(document)) {
    return { ok: false, major: null, errors: [{ path: "", message: "a manifest is a JSON object" }] };
  }
  const major = schemaMajor(document as { schema_version?: unknown });
  if (!SUPPORTED_MAJORS.includes(major as (typeof SUPPORTED_MAJORS)[number])) {
    return {
      ok: false,
      major,
      errors: [
        {
          path: "/schema_version",
          message: `major ${major} is not supported; the harness reads majors ${SUPPORTED_MAJORS.join(" and ")}`,
        },
      ],
    };
  }
  const reader = readers.get(major);
  if (!reader) {
    return { ok: false, major, errors: [{ path: "/schema_version", message: `no schema exists for major ${major}` }] };
  }
  const errors = reader.validate(document);
  return errors.length ? { ok: false, major, errors } : { ok: true, major, manifest: reader.upgrade(document) };
}
