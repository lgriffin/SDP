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
| §11 Test tiers T0–T5, T8 | Architecture, unit, property, contract and use-case suites; T5 runs the same contract suites against the real adapters on PostgreSQL in CI; T8 recovers planted deflections from synthetic bundles | `python/tests/`, `python/tests/integration/` |
| §6 Driven adapters | PostgreSQL metadata, event log (append-only, NOTIFY) and evidence graph with versioned migrations; NAS content-addressed store; Git ledger signed with Ed25519 SSH keys and `allowed_signers`; CPU executor; system clock, ULIDs, environment probe; Prometheus textfile telemetry; filesystem historical sources (append-only transcriptions and verifications); JSON logs; production composition root | `python/packages/sdp-adapters/` |
| §4 Driving adapters | `sdp` CLI (Typer) for predeclare, define, run, validate, dossier, approve, publish, ledger verify, db migrate; ASGI app with Strawberry GraphQL queries, run/validate endpoints and `/health`; committed `schema.graphql` with generated TypeScript types | `python/packages/sdp-cli/`, `python/packages/sdp-api/`, `contracts/graphql/` |
| §7 Contracts (schemas, fixtures, canonical JSON) | Schemas v1.0.0 with golden fixtures; Pydantic and Zod generated with a drift check; fixture suites in both languages; canonical JSON identical in both | `contracts/`, `python/packages/sdp-contracts/`, `typescript/packages/contracts/` |
| §11.7, §13 Specification harness | `spec-harness` ears, trace, conform, rederive, guard, ratchet, report; CI jobs `trace`, `guard`, `cross-language` | `typescript/packages/spec-harness/` |
| §9 UC-02 relativity module | `sdp_relativity`: CODATA/IAU constants recorded per run, explicit probable-to-standard conversion, four hypotheses, per-instrument GLS and Student-t fits, check-plate correlated errors, bootstrap, BIC and Bayes factors at each prior width, the 144-scenario sweep, pooling only under a `PoolingJustification`, transcription audit, oracle registry, dossier recoverability lint; registered as the `uc02-einstein-1919` entry point | `python/packages/sdp-relativity/`, `docs/protocols/uc02-einstein-1919.md` |
| REL-007 independent fitter | `@sdp/relativity-fit` in TypeScript, sharing no code with the workspace (dependency-cruiser); agrees with the reference fitter to 1e-6 on every instrument and scenario, and in CI its output is the `independent_fit` input of a full UC-02 run through the kernel and the harness | `typescript/packages/relativity-fit/` |
| §12 EARS requirements | All 62 requirements as `.ears` data; 47 approved with traces (kernel, acquisition, CTR-001 to 006, NFR-003/005/006/007/010, REL-001 to 008, TST-001 to 007), the rest `draft` until their behaviour lands | `requirements/` |
| Evidence UI (ADR-002, ADR-016) | SvelteKit: experiment list, dossier page with every predeclared gate and the gates that decided the verdict, failure tracebacks, tasks, provenance and lineage, manifest download, ledger chain and events; reads GraphQL through one port with a fixture fake; Playwright on desktop and phone in CI | `typescript/packages/evidence-ui/`, `typescript/packages/e2e/` |
| §14 ADRs | 16 MADR records, accepted | `docs/adr/` |
| §16 Repository layout, justfile | Skeleton | `justfile` |
| §17 Phase 0 | Draft predeclarations for UC-01 and UC-02; DGX spike procedure | `experiments/`, `docs/spike/` |

## Deviations from the specification

Recorded here so the spec can be amended or the code brought back in line. See `docs/spec-deviations.md`.

## Waiting on things the cloud CI cannot do

- **Archive dialects and the DOI publisher**: the production composition root refuses curation and publication (`NotConfigured`) until the archive adapters and the Zenodo publisher land later in the train.

- **The 1919 data**: no measurement or published estimate has been transcribed. Every UC-02 run so far uses synthetic bundles with a planted deflection, and the verdict is INCONCLUSIVE by design because REL-G-04 and REL-G-07 name oracles that are still `to_transcribe`. Transcription (page locator plus a second reader for every row) is human work; `sdp_relativity.transcription.bundle_from_records` turns the stored records into the run's input.
- **DGX Spark spike** (ADR-013): procedure in `docs/spike/phase0-dgx-spark.md`; must be run on the host.
- **Predeclaration sign-off**: both predeclarations are drafts; Leigh confirms regions, cuts and tolerances before `freeze()`.
