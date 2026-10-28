# Implementation status

How the repository maps to the architecture specification v0.3.0, section by section. Updated by every pull request in the implementation train.

| Spec section | Status | Where |
|---|---|---|
| §1–§2 Preface, drivers | Recorded | `docs/architecture.md` |
| §3 Context and containers | C4 views | `docs/c4/` |
| §4–§6 Kernel: domain, ports, application, science port | Implemented, with in-memory fakes and one contract suite per driven port | `python/packages/sdp-kernel/` |
| §8 Determinism and NumericalPolicy | Implemented: canonical JSON (RFC 8785), task seeds, policies on every task | `sdp.kernel.domain.canonical`, `.policy`, `sdp.kernel.science.plan` |
| §9 Experiment lifecycle and checkpointing runner | Implemented: refusal, checkpoints, resume, failure preservation, validate, approve, publish | `sdp.kernel.application.experiments`, `.publication` |
| §10 Evidence (manifest, PROV, RO-Crate) | Implemented; manifests validate against `evidence-manifest.schema.json` | `sdp.kernel.application.manifest`, `.rocrate` |
| §11 Test tiers T0–T4 | Architecture, unit, property, contract and use-case suites in CI | `python/tests/` |
| §7 Contracts (schemas, fixtures, canonical JSON) | Schemas v1.0.0 with golden fixtures; Pydantic and Zod generated with a drift check; fixture suites in both languages; canonical JSON identical in both | `contracts/`, `python/packages/sdp-contracts/`, `typescript/packages/contracts/` |
| §11.7, §13 Specification harness | `spec-harness` ears, trace, conform, rederive, guard, ratchet, report; CI jobs `trace`, `guard`, `cross-language` | `typescript/packages/spec-harness/` |
| §12 EARS requirements | All 62 requirements as `.ears` data; 34 approved with traces (kernel, acquisition, CTR-001 to 004 and 006, NFR-003/005/006, TST-001/002/004/005/006/007), the rest `draft` until their behaviour lands | `requirements/` |
| §14 ADRs | 16 MADR records, accepted | `docs/adr/` |
| §16 Repository layout, justfile | Skeleton | `justfile` |
| §17 Phase 0 | Draft predeclarations for UC-01 and UC-02; DGX spike procedure | `experiments/`, `docs/spike/` |

## Deviations from the specification

Recorded here so the spec can be amended or the code brought back in line. See `docs/spec-deviations.md`.

## Waiting on things the cloud CI cannot do

- **DGX Spark spike** (ADR-013): procedure in `docs/spike/phase0-dgx-spark.md`; must be run on the host.
- **Predeclaration sign-off**: both predeclarations are drafts; Leigh confirms regions, cuts and tolerances before `freeze()`.
