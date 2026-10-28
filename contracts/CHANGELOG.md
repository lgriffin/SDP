# Contracts changelog

Schemas are versioned with semver. The `$id` carries the major version (`.../schemas/v1/<name>.schema.json`) and `x-version` carries the full version; every manifest carries `schema_version`. The harness reads the current major and the previous one (CTR-004). A breaking change needs an ADR and a migration note here.

## 1.0.0 (2026-10-28)

- Initial contract set: `common`, `predeclaration`, `ledger-entry`, `validation-report`, `dataset-artifact`, `canonical-astrometry`, `evidence-manifest`.
- Golden fixtures under `fixtures/valid` and `fixtures/invalid` (with the expected error pointer in `*.expect.json`).
- Canonical JSON (RFC 8785 style: sorted keys, ECMAScript number formatting, no whitespace) with golden vectors in `fixtures/canonical/vectors.json`; every hash in SDP is `sha256:` over these bytes.
