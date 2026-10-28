# Contracts changelog

Schemas are versioned with semver. The `$id` carries the major version (`.../schemas/v1/<name>.schema.json`) and `x-version` carries the full version; every manifest carries `schema_version`. The harness reads the current major and the previous one (CTR-004). A breaking change needs an ADR and a migration note here.

## 1.4.0 (2026-10-28) — review fixes to the integration contracts

- `evidence-manifest` 1.4.0: inputs may name the `adapter_version` that acquired them, and `adapter` and `product_type` now come together. Admission is per adapter version: the kernel writes the version and the harness treats an input naming an adapter without one as unadmitted (INT-016).
- `dataset-artifact` 1.2.0: `product` carries `adapter_version`. Only artifacts with a product use changed shape, and none were stored before this release.
- `scientific-data-product` 1.1.0: the semantic report carries `envelope_digest`, the hash of the envelope it checked, and a curated product needs a science product type and a passing report with that digest (INT-007, INT-008).
- `capability-descriptor` 1.1.0: optional `calibration_levels` (the ObsCore `calib_level` values served), and a cone's radius is now greater than 0 and at most 10 degrees, as the domain requires.
- `adapter-compatibility` 1.0.1: at most one result per conformance kind, and `passed: true` needs a passing result for each of the four.
- `association-graph` 1.0.1: correlation lies strictly between -1 and 1, record positions are in range, and a rejected association states its reason. The two methods' per-association evidence calls the record's epoch `comparison_epoch_jyear`.
- The Zod generator now supports `contains`, `minContains` and `maxContains`.

## 1.3.0 (2026-10-28) — adapter admission

- `evidence-manifest` 1.3.0: each input may name the `adapter` that acquired it and the `product_type` it was used as, so the harness can check admission from the manifest alone (INT-016). Additive; inputs without them need no admission. The kernel now writes 1.3.0 manifests, and `environment.adapter_compatibility` only when an input came through an adapter.
- `dataset-artifact` 1.1.0: optional `product` (`adapter`, `product_type`). Documents without it keep `schema_version` 1.0.0 and their exact shape, so re-registering an existing artifact still compares equal (KRN-014).

## 1.2.0 (2026-10-28) — Integration Architecture v0.4.0

- `evidence-manifest` 1.2.0: optional `intent.plan_artifact_id` (the `AcquisitionPlan` an investigation's evidence came from) and optional `environment.adapter_compatibility[]` (one `AdapterCompatibilityRecord` per adapter and product type used). Additive; 1.0.0 and 1.1.0 manifests stay valid.
- Five new schemas at 1.0.0: `capability-descriptor`, `scientific-data-product`, `association-graph`, `acquisition-plan`, `adapter-compatibility`, each with valid and invalid fixtures.
- The Zod generator now supports `exclusiveMinimum`.

## 1.1.0 (2026-10-28)

- `evidence-manifest`: optional `oracles` array (`oracle_id`, `verified`, `note`) recording each oracle's verification status as the findings saw it, so the harness can re-derive every check independently (TST-007). Additive; 1.0.0 manifests remain valid and are read as having no oracle records.
- New fixture `fixtures/valid/evidence-manifest/synthetic-verified-1.1.json`.

## 1.0.0 (2026-10-28)

- Initial contract set: `common`, `predeclaration`, `ledger-entry`, `validation-report`, `dataset-artifact`, `canonical-astrometry`, `evidence-manifest`.
- Golden fixtures under `fixtures/valid` and `fixtures/invalid` (with the expected error pointer in `*.expect.json`).
- Canonical JSON (RFC 8785 style: sorted keys, ECMAScript number formatting, no whitespace) with golden vectors in `fixtures/canonical/vectors.json`; every hash in SDP is `sha256:` over these bytes.
