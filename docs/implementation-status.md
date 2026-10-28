# Implementation status

How the repository maps to the architecture specification v0.3.0, section by section. Updated by every pull request in the implementation train.

| Spec section | Status | Where |
|---|---|---|
| §1–§2 Preface, drivers | Recorded | `docs/architecture.md` |
| §3 Context and containers | C4 views | `docs/c4/` |
| §7 Contracts (schemas, fixtures, canonical JSON) | Schemas v1.0.0 with golden fixtures | `contracts/` |
| §12 EARS requirements | All 62 requirements as `.ears` data, status `draft` until their behaviour lands | `requirements/` |
| §14 ADRs | 16 MADR records, accepted | `docs/adr/` |
| §16 Repository layout, justfile | Skeleton | `justfile` |
| §17 Phase 0 | Draft predeclarations for UC-01 and UC-02; DGX spike procedure | `experiments/`, `docs/spike/` |

## Waiting on things the cloud CI cannot do

- **DGX Spark spike** (ADR-013): procedure in `docs/spike/phase0-dgx-spark.md`; must be run on the host.
- **Predeclaration sign-off**: both predeclarations are drafts; Leigh confirms regions, cuts and tolerances before `freeze()`.
