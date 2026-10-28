# Capabilities and admission

What the release says each source can do, and which adapters have earned it (Integration v0.4.0 §2, §6).

| Directory | Holds | Written by |
|---|---|---|
| `descriptors/` | One `SourceCapabilityDescriptor` per source (`capability-descriptor.schema.json`): read from the VO where the source publishes it, hand-written otherwise | Discovery (Stage C) or a person, reviewed in a PR |
| `compatibility/` | One `AdapterCompatibilityRecord` per adapter and product type (`adapter-compatibility.schema.json`): contract, operational, semantic and scientific conformance with the CI run that produced each | The conformance run for the release |

Two harness checks read these:

- `pnpm harness claims` removes every science claim (image, spectrum, cube, timeseries, measurements, catalogue) that no passing record backs for the descriptor's adapter and version, and fails (INT-002). Previews, thumbnails and auxiliary files are never evidence, so they are never checked. The release report runs the same check.
- `pnpm harness admit <manifests>` fails when a manifest carries a verdict although one of its inputs came through an adapter without a passing record for the product type it was used as (INT-016). The kernel refuses such a verdict before it is issued; the harness checks again from the manifest alone.

"Passing" is re-derived from the four results, not read from the record's own `passed` flag.

Both directories are empty until the first adapters are admitted in Stage A (MAST for JWST images), so today both checks pass trivially.
