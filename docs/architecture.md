<!-- Exported from the Claude Doc "SDP — Architecture Specification v0.3.0" (rev 38, 28 Oct 2026). The doc is the editable source; regenerate this file rather than editing it by hand. -->

# SDP — Architecture Specification v0.3.0

2026-10-28 · @Leigh

**Status:** Decisions accepted 28 Oct 2026 · baseline for Phase 0 · **Supersedes:** Software Design Document v0.2.0 · **Architecture:** polyglot hexagonal (Python scientific kernel, TypeScript specification harness) · **Target:** NVIDIA DGX Spark + 4 TB NAS

## 1. Preface and refinement delta

This specification turns SDD 0.2.0 into a buildable architecture: one Python scientific kernel with pluggable science modules, one TypeScript specification-and-conformance harness, and a contract between them that is itself under test. The Einstein 1919 examination (UC-02) remains the validation vertical slice; the stellar programme (UC-01) is specified here chiefly through its data-source integration layer, which is the part that must be right before any clustering science runs.

**How to read it.** Sections 2–7 are the architecture proper. Sections 8–10 specify the data layer and the two science modules. Sections 11–13 are verification: the test taxonomy, the EARS requirements and the traceability that binds them. Section 14 records the decisions, 15–18 cover build, run and risk, and 19 lists what only Leigh can decide. Anything carried forward unchanged from 0.2.0 is cited by its old section number rather than repeated.

**The vision in one interaction.** Open a field, select Investigate: the platform identifies the objects in it, retrieves what the archives already know about them, runs the tests you chose, and opens one investigation per candidate, on the Python ecosystem that STScI and Astropy already maintain. That use case, and the bindings it needs, are specified in the companion document [Investigate: Field to Candidate on the STScI Ecosystem](investigate.md); the deltas it made to this specification are marked *(Investigate)* below.

**What changed from 0.2.0 and why**

| Area | SDD 0.2.0 | This specification | Why |
|---|---|---|---|
| Languages | Python only | Python kernel + TypeScript harness and UI, contract-first | Two independent implementations of the verification layer; TypeScript carries the .ears, mutation and release-harness conventions already proven in ESI.ts and Tutors |
| Science domains | "Isolated", yet the core diagram points at them | Science modules are plugins behind one plugin port (`ScientificProtocol`); the kernel never imports them | Removes the hidden inward dependency; new programmes add a package, not a kernel change |
| Predeclaration | Mentioned, no mechanism | Frozen, hashed, signed predeclaration ledger; an experiment cannot start against an unfrozen predeclaration | Preregistration is what makes a VERIFIED verdict mean something |
| Ports | Four `Protocol` sketches | Full driving/driven catalogue; every driven port ships an in-memory fake and a contract suite that real adapters must also pass | Fakes are production code; tests never reach for mocks inside the hexagon |
| Requirements | Markdown tables | `.ears` files as data, compiled to the tables in §12 and the matrix in §13; CI fails a PR that adds behaviour without an `.ears` file or leaves an approved requirement untested | Requirements become a checkable artifact, not prose |
| ADRs | 8 short records, no alternatives | 16 MADR records with alternatives and consequences | Decisions are reviewable and reversible on evidence |
| Testing | A pyramid list | 11 tiers with tools per language, metamorphic relations for pipelines without oracles, mutation-score gates, an oracle registry, clean-room reproduction | "Exceptional" has to be measurable |
| Data sources | One ADQL example and links | An integration layer: `ArchiveGateway` port, canonical catalogue schema, per-source dialects, release pinning, cassette-recorded contract tests | Gaia DR4 lands on 2 Dec 2026 ([ESA](https://www.cosmos.esa.int/web/gaia/news)); the benchmark pins DR3 and the adapter must be release-aware from day one |
| Provenance and publication | Ad-hoc manifests | W3C PROV-aligned evidence graph; publication bundles as RO-Crate | Interoperable with Zenodo and FAIR tooling without custom readers |
| Orchestration and tracking | Prefect and MLflow assumed | Both are optional adapters; the kernel owns a checkpointing runner | Single host, bounded jobs; fewer moving parts until fan-out is needed |
| Containers | "OCI-compatible" | Rootless Podman + Quadlet, GPU through CDI | Matches the no-root operational requirement and the DGX OS toolchain |

## 2. Architectural drivers

Scientific correctness, reproducibility and testability are the three attributes everything else bends to; a faster or more elegant design that weakens any of them is rejected.

| Rank | Quality attribute | What it means for SDP | How the architecture serves it |
|---|---|---|---|
| 1 | Scientific correctness | A result follows from the stated model, data, units and uncertainty treatment, nothing else | Science modules are pure functions over typed quantities; oracles and independent implementations in §11; explicit error-model conventions in §9 |
| 2 | Reproducibility | Levels 1–4 of §11.6, with the achieved level stated in every bundle | Content-addressed artifacts, pinned environments, evidence manifests, clean-room reproduction in CI |
| 3 | Testability | Every port has a fake; every approved requirement has a test; a verdict is re-derivable from recorded evidence | Hexagon with contract suites (§6, §11), requirement-coverage gate (§13), TypeScript re-derivation (§7) |
| 4 | Explainability | A reviewer can follow any number in a report back to source bytes and a model version | PROV-aligned evidence graph, dossiers, evidence UI |
| 5 | Evolvability | Gaia DR4, new archives, a GPU backend or a third science module arrive without kernel changes | Plugin protocol, adapter matrix, versioned schemas and dialects |
| 6 | Operability | Unattended multi-hour runs on one host with bounded storage | Checkpointed runner, structured logs, NAS quotas, Grafana dashboards |

**Hard constraints**

- One NVIDIA DGX Spark (ARM64, unified memory) as the only compute host; no distributed execution in the initial release.
- One 4 TB NAS with the allocation plan of SDD 0.2.0 §8.3; raw data are never modified in place.
- Bounded remote queries only; no mirroring of Gaia, VizieR or MAST.
- GPL-3.0 software (the repository `LICENSE`); datasets under their own licences, recorded per artifact.
- A solo builder initially, so every automated gate must run unattended and report plainly.

**Guiding principles.** Principles 1–10 of SDD 0.2.0 §1.2 stand. Four are added:

- We do not assume. An unstated condition is unconstrained until it is declared; predeclarations, source dialects and numerical policies exist to make declarations explicit.
- Anything that issues a verdict is implemented twice. The Python kernel computes a verdict; the TypeScript harness re-derives it from the recorded evidence; disagreement blocks publication.
- Fakes are production code. Every in-memory adapter passes the same contract suite as the real one and ships in the released package.
- Requirements are data. An `.ears` file is the unit of behaviour; code without one is unreviewable.

## 3. System context and containers

SDP is one host running a Python kernel, a TypeScript harness and UI, PostgreSQL and a NAS; it reaches outward only to public archives, the historical record and Zenodo.

**Actors**

| Actor | Role | Touches |
|---|---|---|
| Researcher | Registers hypotheses, freezes predeclarations, runs experiments, authors dossiers | CLI, notebooks, evidence UI |
| Reviewer | Independent of the run; reads dossiers, signs verdict approvals into the ledger | Evidence UI, ledger |
| CI | Runs every gate in §11 unattended; blocks merges and releases | Both language toolchains, Podman |

**External systems**

| System | Protocol | Used for | Adapter |
|---|---|---|---|
| [ESA Gaia Archive](https://gea.esac.esa.int/archive/) | TAP/ADQL (astroquery.gaia) | Gaia DR3 astrometry and photometry; DR4 from 2 Dec 2026 | `GaiaTapArchiveAdapter` |
| [CDS VizieR / X-Match](https://vizier.cds.unistra.fr/) | TAP, X-Match HTTP | Benchmark cluster catalogues, 2MASS, cross-matching | `VizierArchiveAdapter`, `CdsXMatchAdapter` |
| [MAST](https://archive.stsci.edu/) and [IRSA](https://irsa.ipac.caltech.edu/) | TAP / HTTP | Pan-STARRS and 2MASS corroboration | `MastArchiveAdapter`, `IrsaArchiveAdapter` |
| Historical record | PDF by DOI | [Dyson, Eddington and Davidson 1920](https://doi.org/10.1098/rsta.1920.0009); [Gilmore and Tausch-Pebody 2020](https://doi.org/10.1098/rsnr.2020.0040); Harvey 1979 | `HistoricalSourceAdapter` |
| [Zenodo](https://zenodo.org/) | REST | DOI minting for release bundles | `ZenodoPublisherAdapter` (release phase) |
| [SIMBAD](https://simbad.cds.unistra.fr/) *(Investigate)* | TAP (astroquery.simbad) | Object identifications, types and bibcodes, so a rediscovery is recognised at Identify | `SimbadResolver` |
| [MAST observations](https://archive.stsci.edu/) *(Investigate)* | astroquery.mast Observations, astrocut | HST, JWST, TESS, Kepler and K2, GALEX, IUE, Digitized Sky Survey plates: observation indices, products, cloud URIs and cutouts | `MastObservationGateway` |
| [IRSA](https://irsa.ipac.caltech.edu/) time domain *(Investigate)* | astroquery.ipac.irsa | ZTF light curves, WISE and NEOWISE | `IrsaArchiveAdapter` |
| [DASCH](https://dasch.cfa.harvard.edu/dr7) *(Investigate)* | daschlab over the Starglass web APIs | Harvard plates, about 1880 to 1990: light curves, plate cutouts | `DaschPlateGateway` |

> *Figure: container view · two language hexagons, three stores, five external systems* (see `docs/c4/`)

Archives and the historical record enter only through `sdp-adapters`; the harness reads manifests from the NAS and the UI reads the kernel through GraphQL, with `contracts/` the one thing both sides generate from.

**Containers**

| Container | Language | Responsibility | Depends on |
|---|---|---|---|
| `sdp-kernel` | Python 3.12 | Domain, application use cases, ports, checkpointing runner | Nothing outside the standard library, astropy.units and pyarrow types |
| `sdp-relativity`, `sdp-stellar` | Python | Science modules implementing `ScientificProtocol` | `sdp-kernel` contracts, numerical libraries |
| `sdp-adapters` | Python | Archives, NAS artifact store, PostgreSQL, DuckDB, CPU/GPU compute, event log, trackers | `sdp-kernel` ports, vendor SDKs |
| `sdp-cli`, `sdp-api` | Python | Composition roots: Typer CLI; ASGI app with GraphQL queries and command endpoints | Everything above, wired at start-up |
| `spec-harness` | TypeScript | `.ears` compiler, traceability matrix, manifest conformance, independent verdict re-derivation, release report | `@sdp/contracts` only |
| `evidence-ui` | TypeScript (SvelteKit) | Dossier and provenance browser, ledger view | `@sdp/contracts`, GraphQL client |
| `@sdp/contracts` | TypeScript (generated) | Types and Zod schemas generated from `contracts/` | Nothing |
| PostgreSQL 16 | — | Experiment, hypothesis, artifact and provenance metadata | — |
| NAS | — | Immutable raw area, curated Parquet, artifacts, bundles (layout as 0.2.0 §8.2) | — |
| DuckDB | in-process | Analytical queries over curated Parquet | NAS |

## 4. Hexagonal design

The kernel is the only hexagon that holds rules about evidence; science modules plug into it through one plugin port, a driven port in the service-provider sense, and never see infrastructure. There are three hexagons in the system, not one: the kernel (Python), each science module (Python, pure), and the specification harness (TypeScript), which treats the kernel as an external system it reads through files and an API.

> *Figure: kernel hexagon · domain core, application ring, 3 driving and 3 driven adapters, 2 plugins* (see `docs/c4/`)

Driving adapters call use cases from the left, driven adapters implement ports on the right, science modules plug in from above through `ScientificProtocol`, and the fakes at the bottom implement the same driven ports under the same contract suite.

**Layers of the kernel, inside out**

| Layer | Package | Holds | May import |
|---|---|---|---|
| Domain | `sdp.kernel.domain` | Value objects, entities, aggregates, domain events, invariants, state machines | Standard library, `astropy.units`, `numpy` types |
| Application | `sdp.kernel.application` | Use cases (one class per driving port), transactions, event publication, the checkpointing runner | Domain, ports |
| Ports | `sdp.kernel.ports` | `Protocol` classes for everything the application needs or offers | Domain |
| Science contracts | `sdp.kernel.science` | `ScientificProtocol`, `ComputePlan`, `Predeclaration`, numerical policy types | Domain |
| Adapters | `sdp.adapters.*` | One package per technology; each implements one or more driven ports | Ports, domain, its own vendor SDK |
| Composition roots | `sdp.cli`, `sdp.api`, `sdp.testing.wiring` | Wiring of ports to adapters; nothing else | Everything |

**Dependency rule, enforced by import-linter in CI (§11.3):** domain imports nothing above it; application imports domain and ports; science modules import only `sdp.kernel.domain` and `sdp.kernel.science`; adapters never import each other; only composition roots import adapters. A violation is a failed build, not a warning.

**Science modules as plugins.** A module is discovered through the `sdp.protocols` entry-point group and implements the following contract. Every method is pure: inputs in, values out, no I/O, no clock, no randomness beyond the seed it is handed.

```python
class ScientificProtocol(Protocol):
    protocol_id: ProtocolId            # e.g. "uc02-einstein-1919", semver

    def predeclare(self) -> Predeclaration:
        """Hypotheses, gates, oracle tolerances, target regions or sources.
        Hashed and frozen before any data are touched."""

    def required_inputs(self) -> tuple[InputSpec, ...]:
        """What artifacts an experiment must supply, by role and media type."""

    def plan(self, inputs: ResolvedInputs, params: Params, seed: int) -> ComputePlan:
        """A DAG of ComputeTasks; each names a TaskKind, a NumericalPolicy and its inputs."""

    def interpret(self, results: ComputeResults) -> Findings:
        """Numbers to scientific statements: fitted amplitudes, candidate lists, metrics."""

    def validate(self, findings: Findings, predeclaration: Predeclaration) -> ValidationReport:
        """Evaluates every predeclared gate; returns check values, thresholds and a verdict."""
```

The kernel executes a `ComputePlan` through the `ComputeExecutor` driven port. A `TaskKind` is registered with one CPU reference implementation and optionally a GPU implementation; the executor chooses by backend, and the agreement test of §11 binds the two.

**Where the ambiguous things live**

- Clustering libraries (scikit-learn, hdbscan, cuML) are adapters behind the `ClusterSearchAlgorithm` port; the stellar module compares algorithms without naming libraries.
- Units are a domain concern: `astropy.units.Quantity` is the only way a physical value crosses a port.
- Prefect, if adopted, is a driving adapter that calls application use cases; it never contains logic.
- MLflow, if adopted, is a driven adapter behind `ExperimentTracker`; the manifest in PostgreSQL stays the source of truth.
- The TypeScript harness is not inside the Python hexagon; it consumes manifests, `.ears` files and the GraphQL API as a separate system with its own ports and fakes.

## 5. Domain model

The domain has four aggregates (Hypothesis, Predeclaration, Experiment, Dossier), a handful of value objects that make units and provenance unforgeable, and a lifecycle that refuses to issue a verdict without frozen intent. All domain types are frozen dataclasses; mutation is a new value.

**Value objects**

| Type | Carries | Invariant |
|---|---|---|
| `ArtifactId` | `sha256:<hex>` of canonical bytes | Content-addressed; two identical files share one id |
| `SourceRef` | Archive or document identity, release label (`gaia:DR3`), query text or DOI, licence, citation | Release label is mandatory for archives; DOI for documents |
| `Quantity` | `astropy.units.Quantity` wrapped with a `dtype` and a `NumericalPolicy` | Dimensionless only when declared so |
| `Measurement` | Value, uncertainty, uncertainty convention (`standard`, `probable_error`), `SourceRef`, page or table locator | Convention is never implied; a `probable_error` is stored as given, converted only by an explicit domain function |
| `Prediction` | Model id and version, parameters, predicted `Quantity`, geometry assumptions | Model version is part of identity |
| `NumericalPolicy` | `dtype`, `rtol`, `atol`, determinism class (`bitwise`, `tolerance`, `statistical`), seed handling | Every `TaskKind` names one |
| `Gate` | Id, check expression over named findings, threshold, comparator, severity | Belongs to a Predeclaration; immutable once frozen |
| `Verdict` | `VERIFIED`, `CANDIDATE`, `REJECTED`, `INCONCLUSIVE` + protocol version + gate outcomes | Never constructed without a `ValidationReport` |

**Entities and aggregates**

| Aggregate root | Children | Identity | Notes |
|---|---|---|---|
| `Hypothesis` | `Prediction[]`, evaluation criteria | `hypothesis_id` + `version` | Editing creates a new version; old versions never disappear |
| `Predeclaration` | `Hypothesis` refs, `Gate[]`, oracle tolerances, target regions or source list, `ScientificProtocol` version | `predeclaration_id` + `hash` | `freeze()` computes the hash over canonical JSON and records a ledger entry (§15) |
| `Experiment` | `ExperimentDefinition`, `ComputePlan`, checkpoints, `ComputeResults`, `Findings`, `ValidationReport` | `experiment_id` | References a frozen `Predeclaration` by hash; refuses to start otherwise |
| `DatasetArtifact` | Source ref, checksum, media type, dialect id, acquisition record | `ArtifactId` | Raw artifacts are immutable; curated artifacts reference their raw parents |
| `Dossier` | Evidence manifest, figures, limitations, reviewer approvals | `dossier_id` | Assembled only from a validated experiment; becomes a `PublicationBundle` (RO-Crate) on release |

> *Figure: experiment lifecycle · 8 states, the ledger guard before Running, retained failures* (see `docs/c4/`)

A resumed experiment re-enters Running from its last checkpoint; Refused and Failed are terminal but retained with their manifests, and only a Validated experiment with a signed approval reaches Published.

**Domain events** (appended to the `EventLog` port in the same transaction as the state change; the harness and UI consume them): `HypothesisRegistered`, `PredeclarationFrozen`, `DatasetAcquired`, `DatasetRejected`, `ExperimentDefined`, `ExperimentStarted`, `CheckpointWritten`, `ExperimentCompleted`, `ExperimentFailed`, `VerdictIssued`, `DossierAssembled`, `ApprovalRecorded`, `BundlePublished`.

**Invariants the domain enforces, each with a unit test named after it**

- An `Experiment` cannot enter `Running` unless its `Predeclaration` hash appears in the ledger.
- A `Verdict` cannot be `VERIFIED` if any gate of severity `blocking` failed, or if any oracle used by a gate lacks a verification record.
- A `Measurement` with convention `probable_error` cannot enter a least-squares weight without passing through `to_standard_error()`.
- A `DatasetArtifact` whose checksum differs from its acquisition record is `Rejected`, never `Curated`.
- A `ComputePlan` whose `TaskKind` has no CPU reference implementation is invalid.
- `Findings` from pooled observation sets must cite a `PoolingJustification`; otherwise `interpret()` keeps them separate (UC-02 §9).

**Investigate additions** *(Investigate §2–§6)*. `Field` is a value object: a cone, box or polygon with optional images and their WCS, and an epoch. `Investigation` is an aggregate with four states (Open, Parked, Promoted, Closed) and the events `InvestigationOpened`, `InvestigationParked`, `InvestigationPromoted` and `InvestigationClosed`; every transition records its reason and actor, and an investigation never carries a `Verdict` (its results are labelled `EXPLORATORY`). Invariant: an investigation is promoted only against a frozen confirmatory predeclaration that names evidence the investigation did not use (a different archive, a later release, an independent epoch, or a held-out region); otherwise the kernel refuses and records the refusal (INV-009). The implementation lives in `sdp.kernel.domain.investigation`.

## 6. Ports catalogue and adapter matrix

Nine driving ports expose the kernel; twelve driven ports are everything it needs from the world, and each driven port ships with an in-memory fake and a contract suite in `sdp.testing`.

**Driving ports (application use cases)**

| Port | Command or query | Emits | Called by |
|---|---|---|---|
| `RegisterHypothesis` | Create or version a hypothesis | `HypothesisRegistered` | CLI, API |
| `FreezePredeclaration` | Hash and sign a predeclaration; write the ledger entry | `PredeclarationFrozen` | CLI (signed commit) |
| `AcquireDataset` | Run a bounded, checkpointed acquisition; verify; register raw and curated artifacts | `DatasetAcquired` / `DatasetRejected` | CLI, runner |
| `DefineExperiment` | Bind protocol, predeclaration hash, inputs, parameters, seed, code revision, environment digest | `ExperimentDefined` | CLI, API |
| `RunExperiment` | Execute the plan through `ComputeExecutor` with checkpoints; resumable | `ExperimentStarted`, `CheckpointWritten`, `ExperimentCompleted` / `Failed` | CLI, runner, Prefect adapter |
| `ValidateExperiment` | Call `validate()`, persist the report, issue the verdict | `VerdictIssued` | Runner, CLI |
| `AssembleDossier` | Collect manifest, figures, limitations into a dossier | `DossierAssembled` | CLI |
| `PublishBundle` | Build the RO-Crate, record approvals, mint the DOI | `BundlePublished` | CLI (release phase) |
| `QueryEvidence` | Lineage, manifests, verdicts, ledger entries (read-only) | — | GraphQL API, harness |

**Driven ports**

| Port | Responsibility | Fake | Real adapters |
|---|---|---|---|
| `DatasetRepository` | Register and fetch `DatasetArtifact` metadata | `InMemoryDatasetRepository` | `PostgresDatasetRepository` |
| `ArtifactStore` | Content-addressed bytes; write-once | `InMemoryArtifactStore` | `NasArtifactStore` (filesystem) |
| `ArchiveGateway` | Describe, query, fetch from a remote archive (§8) | `FakeArchive` (fixture-backed) | `GaiaTapArchiveAdapter`, `VizierArchiveAdapter`, `MastArchiveAdapter`, `IrsaArchiveAdapter` |
| `HistoricalSourceGateway` | Register documents, serve pages, hold transcriptions and verification passes | `InMemoryHistoricalSources` | `FilesystemHistoricalSources` |
| `CrossMatchService` | Positional cross-match with radius and epoch handling | `AstropyCrossMatch` (also the CPU reference) | `CdsXMatchAdapter` |
| `ComputeExecutor` | Run a `ComputePlan`; honour `NumericalPolicy`; write checkpoints | `SynchronousExecutor` | `CpuExecutor`, `CudaExecutor` (DGX Spark) |
| `ClusterSearchAlgorithm` | Labels, membership probabilities, stability for a feature matrix | `FakeClusterer` (planted labels) | `SklearnHdbscan`, `SklearnDbscan`, `CumlHdbscan` |
| `EvidenceRepository` | PROV-aligned graph of activities, entities, agents | `InMemoryEvidenceGraph` | `PostgresEvidenceGraph` |
| `EventLog` | Append-only domain events with sequence numbers | `InMemoryEventLog` | `PostgresEventLog` (table + `LISTEN/NOTIFY`) |
| `ExperimentTracker` | Optional mirror of metrics and parameters | `NullTracker` | `MlflowTracker` |
| `Ledger` | Signed append-only entries for predeclarations and approvals | `InMemoryLedger` | `GitSignedLedger` (`ledger/*.jsonl`, signed commits) |
| `Clock`, `IdGenerator`, `Hasher` | Determinism seams | Fixed clock, sequential ids, real SHA-256 | System clock, ULID, SHA-256 |

`ScientificProtocol` (§4) is the thirteenth driven port, in the service-provider sense: the kernel requires it and science modules implement it, so it has a contract suite like any other, run against the synthetic protocol used by the walking skeleton and against both science modules.

**Adapter matrix: which technology satisfies which port, by environment**

| Port | Unit and application tests | Integration (CI) | DGX Spark |
|---|---|---|---|
| `ArtifactStore` | In-memory | tmpfs filesystem | NAS filesystem |
| `DatasetRepository`, `EvidenceRepository`, `EventLog` | In-memory | PostgreSQL via testcontainers | PostgreSQL 16 container |
| `ArchiveGateway` | Fixture-backed fake | Recorded cassettes (no network) | Live archives, rate-limited |
| `ComputeExecutor` | Synchronous | CPU | CPU reference + CUDA |
| `ClusterSearchAlgorithm` | Planted labels | scikit-learn | scikit-learn and cuML |
| `Ledger` | In-memory | Git in a temp repo | Git with required signatures |

A contract suite is one parametrised test module per port, run against the fake and every real adapter available in the environment; the fake is never allowed to pass a case the real adapter fails.

**Investigate additions** *(Investigate §3–§8)*. Driving port `InvestigateField` (with `TriageInvestigation` and `QueryInvestigations` beside it). Driven ports `ObservationArchiveGateway` (products, not rows), `SourceDetector` (photutils; fake `PlantedDetector`), `WcsRefiner` (tweakwcs), `ObjectResolver` (SIMBAD; fake backed by a fixture table), `SourceRegistry` (pyvo), `CandidateRanker`, and `InvestigationRepository` inside the unit of work. Each has a fake and a contract suite in `sdp.testing`. The Jdaviz Imviz plugin is a driving adapter: it reads the viewer footprint as the `Field`, calls `InvestigateField` and overlays the candidates, and holds no analysis logic or archive calls (INV-012). It lives in its own environment (`viewer/sdp-imviz`) and reaches the kernel only through `POST /investigations` on sdp-api.

## 7. Polyglot boundary and contracts

Python owns scientific truth; TypeScript owns specification, conformance and presentation; the boundary between them is a versioned set of JSON Schemas from which both sides generate their types, and the evidence manifest is the durable contract that crosses it.

**Division of responsibility**

| Concern | Python | TypeScript | Rationale |
|---|---|---|---|
| Domain rules, use cases, science modules, numerics | Owns | Never reimplements | Astropy, SciPy, Arrow and CUDA live here; one source of scientific truth |
| Archive, storage, compute adapters | Owns | — | Vendor SDKs are Python |
| `.ears` compiler, traceability matrix, requirement-coverage gate | — | Owns | Shared with the ESI.ts and Tutors conventions; one toolchain across repos |
| Manifest conformance (schema, hashes, completeness) | Emits | Verifies | Independent reader catches writer bugs |
| Verdict re-derivation from recorded check values and frozen gates | Computes | Re-derives; disagreement blocks publication | Principle 12: anything that issues a verdict is implemented twice |
| Release report (gates, mutation scores, reproduction result, approvals) | Supplies data | Owns | Same shape as the Tutors release harness report |
| Evidence and provenance UI | Serves GraphQL | Owns (SvelteKit) | Graph browsing suits GraphQL; typed client generated from the schema |
| Contracts | Generates Pydantic DTOs | Generates types and Zod schemas | Neither hand-writes a boundary type |

**Contract repository (****contracts/****, the single source of truth)**

```text
contracts/
├── schemas/                     # JSON Schema 2020-12, $id carries semver
│   ├── evidence-manifest.schema.json
│   ├── predeclaration.schema.json
│   ├── validation-report.schema.json
│   ├── dataset-artifact.schema.json
│   ├── ledger-entry.schema.json
│   └── canonical-astrometry.schema.json
├── graphql/schema.graphql       # query side, exported from the ASGI app and committed
├── fixtures/
│   ├── valid/                   # golden examples both languages must accept
│   └── invalid/                 # examples both languages must reject, with the expected error path
└── CHANGELOG.md
```

**Generation and drift.** `datamodel-code-generator` produces `sdp.contracts` (Pydantic v2); `json-schema-to-zod` produces `@sdp/contracts`. Generated code is committed, and CI regenerates and diffs it, so a schema edit without regeneration fails the build. Domain types are mapped to DTOs by explicit mappers in the application layer, and a round-trip property test (domain → DTO → domain) runs per type.

**Versioning.** Schemas are semver; a manifest carries `schema_version`. The harness supports the current major and the previous one, and a fixture from each supported version is kept under `fixtures/valid`. A breaking change needs an ADR and a migration note in `CHANGELOG.md`.

**Evidence manifest (the durable contract).** One JSON document per experiment, written atomically to the NAS and mirrored in PostgreSQL:

| Field group | Content |
|---|---|
| Identity | `experiment_id`, `protocol_id` and version, `schema_version` |
| Intent | `predeclaration_hash`, ledger entry id, hypotheses and gates as frozen |
| Inputs | Artifact ids with checksums, `SourceRef`s, dialect ids, acquisition records |
| Environment | Code revision, `uv.lock` digest, container image digest, backend (`cpu` or `cuda`), driver and CUDA versions |
| Execution | Task graph with `TaskKind`, `NumericalPolicy`, seed, checkpoints, durations |
| Outputs | Artifact ids of results, figures and tables |
| Findings and checks | Named findings with values, uncertainties and units; every gate's check value, threshold and outcome |
| Verdict | Status, protocol version, limitations, reviewer approvals |
| Provenance | PROV-JSON subgraph linking all of the above |

**Transport.** Files on the NAS are the primary channel: the harness reads manifests, ledger entries and `.ears` files from disk, which makes the whole verification layer reproducible offline. The GraphQL API (Strawberry on the ASGI app) is the secondary channel for live status and the UI; commands stay on the CLI and a minimal REST surface. The TypeScript side also defines its own ports (`ManifestSource`, `LedgerSource`, `GateEvaluator`) with fakes, so harness tests never need the Python side running.

**Cross-language tests (tier T6 in §11).** Every fixture under `fixtures/` is loaded by pytest and vitest in the same CI job; GraphQL responses are checked against `schema.graphql` with a schema-driven fuzzer on the Python side and the generated client on the TypeScript side; a nightly job runs the Python kernel end to end and the harness over its output.

## 8. Data-source integration layer

Every archive enters SDP through one port, one canonical schema and one declared dialect per source; a catalogue that cannot be described as a dialect cannot be ingested. This layer is the deliverable for the stellar programme in the initial release: the clustering science of §10 is only credible once the data beneath it are versioned, mapped and cross-matchable.

**The ****ArchiveGateway**** port**

```python
class ArchiveGateway(Protocol):
    def describe(self, source: SourceId) -> SourceDescriptor: ...
        # release label, column list, licence, citation, rate limits, max rows per call
    def query(self, q: CatalogueQuery) -> AcquisitionHandle: ...
        # bounded: a cone or box, column subset, filters, row cap; returns a resumable handle
    def fetch(self, h: AcquisitionHandle) -> Iterator[RecordBatch]: ...
        # pyarrow batches in the source's native columns; idempotent per chunk
```

A `CatalogueQuery` is a value object, not an ADQL string: the adapter renders ADQL (or an HTTP query) from it, and the rendered text is stored in the `SourceRef` so the acquisition is auditable and replayable. The example cone in SDD 0.2.0 §10.4 becomes a predeclared `TargetRegion` list, with the reason for each region recorded in the predeclaration.

**Canonical astrometry schema (****canonical-astrometry.schema.json****, v1)**

| Column | Unit | Notes |
|---|---|---|
| `source_key` | — | Namespaced: `gaia:DR3:<source_id>`, `2mass:<designation>` |
| `ra_deg`, `dec_deg` | deg | ICRS at `epoch_jyear` |
| `epoch_jyear` | yr | 2016.0 for Gaia DR3; adapters must propagate when epochs differ |
| `parallax_mas` + `_err` | mas | Zero-point correction recorded as a flag, never silently applied |
| `pm_ra_cosdec_masyr`, `pm_dec_masyr` + `_err` | mas/yr | The cos(dec) convention is fixed here; dialects convert |
| `astrometric_corr[10]` | — | Upper triangle of the 5×5 correlation matrix, Gaia order; null where a source lacks it |
| `g_mag`, `bp_rp` | mag | Optional for non-Gaia sources |
| `ruwe`, `quality_flags` | — | Source-specific flags preserved as a JSON column, plus a normalised `quality_class` |
| `source_release` | — | `gaia:DR3`, `gaia:DR4`, `hr23:v1.0` ... |

**Dialects.** A dialect is a YAML document per source and release: column mapping, unit conversion, epoch, frame and flag normalisation. It is data, loaded by the adapter and validated by a dialect contract test that pushes known native rows through it and compares the canonical result to golden values. Adding Gaia DR4 is a new dialect file plus its golden rows, not a code change.

**Release pinning.** Gaia DR4 is scheduled for 2 December 2026 with roughly twice the observation baseline of DR3 ([ESA Gaia news](https://www.cosmos.esa.int/web/gaia/news)). The stellar benchmark is predeclared against `gaia:DR3`; a second experiment with the same predeclaration against `gaia:DR4` becomes a cross-release reproducibility result in its own right, which is more interesting scientifically than silently moving to DR4 mid-programme.

**Acquisition policy (carried from 0.2.0 §8.5, now with mechanism)**

- Idempotency key = SHA-256 of rendered query text + `source_release` + column list; a repeat acquisition returns the existing artifact.
- Chunks are written as separate Parquet parts under `raw/<source>/<release>/<key>/part-NNNN.parquet` with a sidecar checksum; a `CheckpointWritten` event follows each part; resume reads the last part number.
- Retries use bounded exponential backoff; the archive's own `maxrec` and rate limits from `describe()` are honoured, never guessed.
- Every artifact carries licence and citation from the descriptor; a missing licence blocks curation.
- Curated Parquet is derived from raw by the dialect and carries `dialect_id`, `dialect_version` and the raw parent id.

**Cross-match.** `CrossMatchService` matches canonical frames with an angular radius and optional epoch propagation; `AstropyCrossMatch` is the CPU reference and the fake, and `CdsXMatchAdapter` must agree with it on a fixture to the declared tolerance before it is used in any gate.

**Benchmark catalogues registered as sources**

| Source | Release label | Role |
|---|---|---|
| [Hunt and Reffert 2023](https://arxiv.org/abs/2303.13424) all-sky cluster catalogue | `hr23:<catalogue version>` | Primary known-cluster benchmark and a published HDBSCAN baseline |
| Cantat-Gaudin and Anders 2020 | `cga20:<version>` | Second benchmark to estimate catalogue disagreement |
| 2MASS point-source catalogue | `2mass:psc` | Photometric corroboration |
| Pan-STARRS DR2 | `ps1:DR2` | Photometric corroboration |

**Testing this layer.** Adapter contract tests run against recorded cassettes committed with their query hash, so CI never touches a live archive; a weekly live smoke job re-records one small query per adapter and fails on schema drift (new, renamed or retyped columns). Dialect golden rows are the first thing updated when an archive publishes a release note.

**Future sources.** Local FITS from amateur instruments enters through the same port as a `LocalObservationGateway` adapter reading a read-only curated store, which keeps the astrorepo invariant (never write to the instrument copy) intact.

**Gateway split and DASCH** *(Investigate §4)*. Catalogue rows keep coming through `ArchiveGateway`; observations, where the unit is a product, come through `ObservationArchiveGateway`, whose index is cheap and always fetched while products are fetched only when a test in the profile declares it needs them. DASCH is a registered source (DR7, all access restrictions lifted): cutouts and light curves by object, never bulk. The same rules hold for both gateways: idempotency keys, resumable fetches, licence and citation or retrieval is blocked, cassettes in CI and one weekly live smoke per adapter.

## 9. Relativity module (UC-02, Einstein 1919) — the validation slice

The relativity module is the first complete vertical slice because it exercises every kernel capability with a few hundred numbers: transcription with provenance, pure physical models, competing-hypothesis comparison, sensitivity to reduction choices, and a verdict a reviewer can re-derive by hand. Its research question stands as in 0.2.0 §11.2.

**Components (****sdp_relativity****, all pure)**

| Component | Responsibility | Tested by |
|---|---|---|
| `DeflectionModel` family | `ZeroDeflection`, `HalfDeflection`, `GRDeflection`, `FreeAmplitude(alpha)`; each maps star geometry to a predicted displacement `Quantity` | Solar-limb oracle, impact-parameter scaling, unit and dimension tests |
| `StarGeometry` | Angular separation from the Sun's centre to impact parameter and projection onto plate axes | Analytic cases, metamorphic rotation test |
| `ObservationSet` | One per instrument: Sobral 4-inch, Sobral astrographic, Príncipe astrographic; plates, stars, measured displacements, comparison-plate epochs | Transcription verification records |
| `ReductionScenario` | Which plate constants (scale, orientation, offsets) are free, fixed from check plates, or shared across plates | Historical reproduction against published estimates |
| `ErrorModel` | Independent Gaussian, per-plate correlated, Student-t robust; uncertainty convention conversion | Synthetic recovery tests |
| `Fitter` | Weighted least squares and maximum likelihood for `alpha` under a scenario and error model; bootstrap and Bayesian (secondary) | Synthetic GR, half and zero data |
| `ModelComparison` | Likelihoods, information criteria, Bayes factors with prior sensitivity, posterior predictive checks | Planted-truth fixtures |
| `SensitivitySweep` | Grid over scenarios × error models × inclusion sets; reports how `alpha` and the preferred hypothesis move | Stability tests |

**The physical prediction** at the solar limb, with `b` the impact parameter:

```latex
\alpha_{\mathrm{GR}}(b) = \frac{4 G M_{\odot}}{c^{2} b}, \qquad \alpha_{\mathrm{half}}(b) = \frac{2 G M_{\odot}}{c^{2} b}
```

Constants come from `astropy.constants` with their CODATA version recorded in the manifest; the module never hard-codes 1.75.

**Conventions that must be explicit, each a ****Measurement**** field rather than a comment**

- Uncertainty convention: the 1920 paper quotes probable errors; a probable error converts to a standard error by a factor of about 1.4826, and the conversion is a named domain function applied once, visibly.
- Pooling: Sobral 4-inch, Sobral astrographic and Príncipe results are never averaged without a `PoolingJustification`; the original authors' decision to set the astrographic result aside is modelled as one `ReductionScenario` among several, not as a default.
- Plate scale: whether scale is solved from the eclipse plates or fixed from check plates is the single largest driver of the astrographic result and is therefore a scenario axis, not a constant.

**Oracle registry (****sdp_relativity/oracles.yaml****).** Values below are as commonly reported and are placeholders until transcribed from the cited page and verified by a second pass (gates REL-G-01 and REL-G-02); a gate that references an unverified oracle cannot pass.

| Oracle | Value | Source | Status |
|---|---|---|---|
| GR limb deflection | 1.75 arcsec | Computed from constants | Analytic |
| Half deflection | 0.87 arcsec | Computed from constants | Analytic |
| Sobral 4-inch, 1920 | 1.98 ± 0.12 arcsec | [Dyson, Eddington and Davidson 1920](https://doi.org/10.1098/rsta.1920.0009), table to cite | To transcribe |
| Príncipe, 1920 | 1.61 ± 0.30 arcsec | Same | To transcribe |
| Sobral astrographic, 1920 | 0.93 arcsec | Same | To transcribe |
| Sobral 4-inch, 1979 remeasurement | 1.90 ± 0.11 arcsec | Harvey 1979, The Observatory 99, 195 | To transcribe |
| Sobral astrographic, 1979 remeasurement | 1.55 ± 0.34 arcsec | Same | To transcribe |
| Modern statistical reanalysis | Conclusions and intervals | [Gilmore and Tausch-Pebody 2020](https://doi.org/10.1098/rsnr.2020.0040) | To transcribe |

**Predeclared sensitivity scenarios**

- Error convention: probable error taken as standard error versus converted.
- Plate scale solved versus fixed from check plates, per instrument.
- Astrographic set included, excluded, or down-weighted by its own scatter.
- Independent versus per-plate correlated errors.
- Robust (Student-t) versus Gaussian likelihood.
- Prior width on `alpha` for the Bayesian comparison, over at least three widths.

**Gates (****REL-G-*****, frozen in the predeclaration)**

| Gate | Check | Severity |
|---|---|---|
| REL-G-01 | Every transcribed measurement links to a source page or table | Blocking |
| REL-G-02 | Every measurement used in a fit has a second-pass verification record | Blocking |
| REL-G-03 | `GRDeflection` and `HalfDeflection` reproduce the analytic limb values within `NumericalPolicy` tolerance | Blocking |
| REL-G-04 | Historical published estimates reproduced within tolerances set after source inspection and recorded in the predeclaration | Blocking |
| REL-G-05 | All four hypotheses evaluated on every instrument set separately | Blocking |
| REL-G-06 | Sensitivity sweep complete; the report states which conclusions survive all scenarios | Blocking |
| REL-G-07 | Comparison with the 2020 reanalysis present, with agreements and disagreements listed | Blocking |
| REL-G-08 | Independent implementation of the fitter (different author or language) agrees on `alpha` within tolerance | Blocking |
| REL-G-09 | The dossier never describes a table-based reanalysis as a plate remeasurement (lint on the limitations section) | Blocking |

The publication package layout of 0.2.0 §11.13 stands, produced as an RO-Crate.

## 10. Stellar module (UC-01) design

The stellar module is a second plugin on the same kernel: its feature-space policy, clustering adapters, benchmark cross-match and null and injection tests are specified now so the data layer of §8 is built for them, but its science runs only after the Einstein slice has proven the kernel. Hypotheses UC01-H1 to H3 of 0.2.0 §10.2 stand.

**Components (****sdp_stellar****)**

| Component | Responsibility | Pure? |
|---|---|---|
| `FeatureSpacePolicy` | Versioned transformation from canonical astrometry to a feature matrix: tangent-plane projection, proper-motion scaling, optional parallax-aware whitening using the correlation matrix; never five raw Euclidean axes | Yes |
| `ClusterSearch` | Orchestrates a `ClusterSearchAlgorithm` adapter over a feature matrix with declared hyperparameter grids | Plans only; execution through `ComputeExecutor` |
| `MembershipEstimator` | Membership probabilities and stability per candidate | Yes |
| `CmdCoherence` | Colour-magnitude sequence tightness versus field stars for each candidate | Yes |
| `BenchmarkCrossMatch` | Matches candidates to registered benchmark catalogues within predeclared radii and proper-motion windows | Uses `CrossMatchService` |
| `NullFieldGenerator` | Shuffles proper motions and parallaxes within magnitude bins to destroy real structure while keeping selection effects | Yes, seeded |
| `SyntheticClusterInjector` | Plants clusters with declared richness, distance, dispersion and magnitude distribution into real fields | Yes, seeded |
| `CandidateRanker` | Reproducible ranking from membership, coherence, stability and novelty | Yes |
| `DossierBuilder` | One dossier per candidate with every check value and failure reason | Yes |

**Clustering as adapters.** `ClusterSearchAlgorithm` has three adapters in the initial release: `SklearnHdbscan` and `SklearnDbscan` as CPU references, and `CumlHdbscan` on the DGX Spark. The GPU adapter is admitted to a gate only when its labels and membership probabilities agree with the CPU reference on a fixed fixture to the declared tolerance; an unavoidable nondeterminism is documented under NFR-010 rather than hidden. Hunt and Reffert's published HDBSCAN configuration is registered as a named baseline so the comparison in UC01-H3 is against a real published method.

**Metrics reported with bootstrap intervals:** benchmark recovery rate; contamination estimate; completeness by distance, richness and magnitude; false-positive rate under null fields; stability across hyperparameters and quality cuts; candidate-review effort.

**Gates (****STL-G-*****)**

| Gate | Check | Severity |
|---|---|---|
| STL-G-01 | Predeclared target regions and quality cuts recorded and frozen before the first Gaia query | Blocking |
| STL-G-02 | Benchmark catalogues registered with release labels, licences and citations | Blocking |
| STL-G-03 | Known-cluster recovery on predeclared regions reported with intervals against both benchmarks | Blocking |
| STL-G-04 | Null-field false-positive rate below the predeclared tolerance | Blocking |
| STL-G-05 | Injection recovery curve reported across richness and distance | Blocking |
| STL-G-06 | Every candidate labelled potentially new has a cross-match record against both benchmarks | Blocking |
| STL-G-07 | Rejected and inconclusive candidates retained with failure reasons | Blocking |
| STL-G-08 | GPU adapter agreement with the CPU reference on the fixture, or GPU results excluded from the gates | Blocking |
| STL-G-09 | Report distinguishes candidate from confirmed discovery in a fixed sentence pattern (lint) | Blocking |

**Scientific tests specific to this module** (tier T8 of §11): known-cluster recovery, randomised-field null, synthetic injection, quality-cut perturbation, hyperparameter perturbation, catalogue cross-match, photometric consistency, covariance sensitivity, and three metamorphic relations: row permutation leaves labels invariant up to relabelling; a small rigid rotation of a field leaves memberships invariant; unit rescaling of proper motions before the policy leaves the feature matrix invariant.

## 11. Testing and verification strategy

Eleven test tiers, two toolchains, and three gates that no line-coverage number can satisfy: requirement coverage, mutation score and clean-room reproduction. Line coverage is reported but never gates a merge.

**11.1 Taxonomy**

| Tier | Name | Scope | May touch | Python | TypeScript | Runs |
|---|---|---|---|---|---|---|
| T0 | Architecture | Import graph and package boundaries | Nothing | import-linter, pytest-archon | dependency-cruiser | Every push |
| T1 | Domain unit | Value objects, aggregates, science functions | Nothing | pytest | vitest | Every push |
| T2 | Property and metamorphic | Invariants and relations across generated inputs | Nothing | Hypothesis | fast-check | Every push |
| T3 | Port contract | Every fake and every real adapter against one suite per port | Fakes; testcontainers locally | pytest parametrised | vitest | Fakes every push; real on merge |
| T4 | Use case | Application services wired to fakes only | Fakes | pytest | vitest | Every push |
| T5 | Integration | Adapter against real infrastructure | PostgreSQL, filesystem, DuckDB, cassettes | testcontainers, pytest-recording | — | Merge |
| T6 | Cross-language contract | Golden fixtures, generated code drift, GraphQL schema | Generated clients | pytest, schemathesis | vitest, Zod | Every push |
| T7 | End to end | Synthetic experiment from acquisition to bundle to harness to UI | Full compose stack | pytest | Playwright | Nightly, release |
| T8 | Scientific validation | Oracles, null, injection, sensitivity, independent implementation | Compute | pytest `-m scientific` | — | Nightly, release |
| T9 | Reproducibility | Clean-room `reproduce.sh` in a fresh container | Containers | shell | harness verifies | Release |
| T10 | Mutation | Strength of T1–T4 | — | mutmut | Stryker | Weekly, release |
| T11 | Live archive smoke | One small query per adapter; schema drift | Network | pytest `-m live` | — | Weekly |

**11.2 Policies**

- Fakes over mocks: `unittest.mock` and `vi.mock` are forbidden under `domain`, `application` and the science modules by a lint rule; doubles are the fakes in `sdp.testing`.
- One contract suite per port, parametrised over adapters; the suite is the port's specification and lives beside the `Protocol`.
- Every test carries a requirement marker, `@pytest.mark.req("KRN-FR-004")` or `it.req("CTR-003", ...)`; the harness reads JUnit output to build §13.
- Scientific tests load oracles from `oracles.yaml`; an oracle without a verification record fails the test that uses it.
- Numerical comparisons use the `NumericalPolicy` of the `TaskKind` under test, never ad-hoc tolerances.
- Seeds are explicit and recorded; a test that depends on an unseeded random source is a bug.

**11.3 Architecture tests.** import-linter contracts: domain imports no application, ports or adapters; science modules import only `sdp.kernel.domain` and `sdp.kernel.science`; adapters do not import each other; only composition roots import adapters. dependency-cruiser: `spec-harness` and `evidence-ui` import only `@sdp/contracts` from the workspace.

**11.4 Metamorphic relations** (for pipelines without a closed-form oracle): rotating all 1919 star positions about the Sun leaves fitted `alpha` unchanged; doubling every displacement doubles `alpha`; adding a plate offset changes nothing when the offset is modelled and is reported when it is not; permuting Gaia rows leaves cluster labels invariant up to relabelling; injecting then removing a synthetic cluster returns the baseline.

**11.5 Mutation testing.** mutmut for Python and Stryker for TypeScript, scored per package as in ESI.ts. Initial thresholds are set from the first full run and then ratcheted: a package's score may not drop below its recorded high-water mark minus 2 points.

**11.6 Reproducibility levels** (as 0.2.0 §13.4): Level 1 computational, Level 2 analytical (independent implementation), Level 3 scientific (survives alternative assumptions), Level 4 independent evidence. The harness refuses a release report that claims a level without the corresponding tier having passed in that release's CI run.

**11.7 CI pipeline**

- Static: Ruff, mypy strict, ESLint, tsc, T0, contract regeneration diff.
- Fast: T1, T2, T3 (fakes), T4, T6 in both languages; requirement-coverage gate.
- Merge: T3 (real adapters), T5 with cassettes.
- Nightly: T7, T8 on CPU; GPU agreement tests on the DGX Spark runner.
- Weekly: T10 scores and ratchet; T11 live smoke and dialect drift.
- Release: T9 in a fresh container; harness release report with gates, scores, reproduction result and approvals; `.ears` compile; RO-Crate validation.

**11.8 Naming.** Tests are named after the requirement or invariant they protect: `test_experiment_refuses_unfrozen_predeclaration` not `test_run_experiment_2`.

## 12. EARS requirements as data

Requirements live in `.ears` files under `requirements/`, one per capability; the tables below are what the harness compiles from them, and the keywords While, When, Where, If/then and shall are kept exactly so the files remain machine-checkable. The grammar follows the `.ears` convention already used in ESI.ts and proposed for Camel, so one compiler serves every repository.

```yaml
# requirements/kernel/predeclaration.ears
capability: Predeclaration freezing
owner: kernel
requirements:
  - id: KRN-002
    pattern: event
    text: When a predeclaration is frozen, the kernel shall compute its hash over canonical JSON and append a signed ledger entry before returning.
    rationale: Preregistration integrity is what gives VERIFIED its meaning.
    verification: test          # test | analysis | inspection | demonstration
    status: approved            # draft | approved | retired
    traces:
      - tests/unit/kernel/test_predeclaration.py::test_freeze_appends_signed_ledger_entry
```

**12.1 Kernel (KRN)**

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| KRN-001 | Ubiquitous | The kernel shall register versioned hypotheses, and an edit shall create a new version while retaining every prior version. | T1 |
| KRN-002 | Event | When a predeclaration is frozen, the kernel shall compute its hash over canonical JSON and append a signed ledger entry before returning. | T4 |
| KRN-003 | Unwanted | If an experiment references a predeclaration hash absent from the ledger, then the kernel shall refuse to start it and record the refusal. | T1, T4 |
| KRN-004 | Event | When an experiment starts, the kernel shall record input artifact ids with checksums, code revision, environment digests, backend, numerical policies and seed in the manifest. | T4 |
| KRN-005 | Event | When a compute task completes, the kernel shall write a checkpoint and emit `CheckpointWritten`. | T4 |
| KRN-006 | Event | When an interrupted experiment is resumed, the kernel shall continue from the last checkpoint without re-executing completed tasks. | T5 |
| KRN-007 | Unwanted | If a compute task fails, then the kernel shall preserve the manifest, diagnostics and partial outputs and mark the experiment Failed. | T4 |
| KRN-008 | Event | When `validate()` returns, the kernel shall persist the validation report and issue a verdict that carries every gate outcome. | T4 |
| KRN-009 | Unwanted | If any blocking gate fails or any oracle used by a gate lacks a verification record, then the kernel shall not issue VERIFIED. | T1 |
| KRN-010 | Ubiquitous | The kernel shall append every domain event to the event log in the same transaction as the state change it describes. | T5 |
| KRN-011 | Ubiquitous | The kernel shall invoke science modules only through the `ScientificProtocol` port. | T0 |
| KRN-012 | Unwanted | If a `ComputePlan` names a `TaskKind` without a CPU reference implementation, then the kernel shall reject the plan. | T1 |
| KRN-013 | Optional | Where the CUDA backend is selected, the kernel shall record driver and CUDA versions and the agreement-test result id in the manifest. | T5 (DGX) |
| KRN-014 | Ubiquitous | The kernel shall prevent modification of raw artifacts through application workflows. | T3 |

**12.2 Contracts (CTR)**

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| CTR-001 | Ubiquitous | Every boundary type shall be defined once under `contracts/schemas` and generated into both languages. | T6 |
| CTR-002 | Unwanted | If committed generated code differs from a fresh generation, then CI shall fail. | T6 |
| CTR-003 | Ubiquitous | Both languages shall accept every fixture under `fixtures/valid` and reject every fixture under `fixtures/invalid` at the expected error path. | T6 |
| CTR-004 | Ubiquitous | An evidence manifest shall carry `schema_version`, and the harness shall read the current and the previous major version. | T6 |
| CTR-005 | Event | When the GraphQL schema changes, the committed `schema.graphql` and the generated client shall change in the same commit. | T6 |
| CTR-006 | Ubiquitous | Every domain-to-DTO mapper shall round-trip its type. | T2 |

**12.3 Data-source integration (DSI)**

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| DSI-001 | Ubiquitous | The system shall access every archive only through `ArchiveGateway` with a declared dialect per source release. | T0, T3 |
| DSI-002 | Event | When a dataset is acquired, the system shall record the rendered query text, source release, licence, citation, checksum, byte count and timestamp. | T3 |
| DSI-003 | Unwanted | If a downloaded chunk fails checksum verification, then the system shall discard it, emit `DatasetRejected` and retry within the bounded policy. | T5 |
| DSI-004 | Event | When an acquisition repeats an existing idempotency key, the system shall return the existing artifact without a remote call. | T3 |
| DSI-005 | Ubiquitous | Curated datasets shall carry dialect id, dialect version and the raw parent artifact id. | T3 |
| DSI-006 | Unwanted | If a source descriptor declares no licence, then the system shall block curation of that source. | T4 |
| DSI-007 | Ubiquitous | Archive adapter contract tests shall run from recorded cassettes, and CI shall make no live archive call. | T5 |
| DSI-008 | Event | When the weekly live smoke detects a column added, renamed or retyped, the system shall fail the job and name the column. | T11 |
| DSI-009 | Ubiquitous | A cross-match adapter shall agree with `AstropyCrossMatch` on the fixture within tolerance before any gate uses it. | T3 |
| DSI-010 | State | While NAS free space is below the configured threshold, the system shall refuse new acquisitions. | T5 |

**12.4 Relativity module (REL)**

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| REL-001 | Ubiquitous | Every transcribed measurement shall carry an uncertainty convention and a page or table locator. | T1, REL-G-01 |
| REL-002 | Unwanted | If a probable error enters a least-squares weight without explicit conversion, then the domain shall raise. | T1 |
| REL-003 | Ubiquitous | Deflection models shall take physical constants from a recorded CODATA version. | T1 |
| REL-004 | Ubiquitous | The module shall fit each instrument set separately, and pooling shall require a `PoolingJustification`. | T1, REL-G-05 |
| REL-005 | Ubiquitous | The module shall evaluate all four hypotheses per instrument and per scenario. | T8 |
| REL-006 | Ubiquitous | The sensitivity sweep shall cover the six predeclared scenario axes. | T8, REL-G-06 |
| REL-007 | Ubiquitous | The fitter shall exist in two independent implementations that agree within tolerance. | T8, REL-G-08 |
| REL-008 | Ubiquitous | The dossier shall list unrecoverable inputs and shall not describe a table-based reanalysis as a plate remeasurement. | REL-G-09 |

**12.5 Stellar module (STL)**

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| STL-001 | Event | When target regions are predeclared, the system shall record the reason for each region. | T1, STL-G-01 |
| STL-002 | Ubiquitous | Feature-space transformations shall be versioned policies, and raw five-axis Euclidean clustering shall not feed a gate. | T1 |
| STL-003 | Ubiquitous | Quality cuts shall be configuration recorded in the manifest. | T4 |
| STL-004 | Optional | Where a GPU clustering adapter is used, the system shall exclude its results from gates unless the agreement test passed in the same release. | T8, STL-G-08 |
| STL-005 | Ubiquitous | A candidate shall be cross-matched against two benchmarks before it is labelled potentially new. | STL-G-06 |
| STL-006 | Ubiquitous | Null-field and injection tests shall run per region with recorded seeds. | T8 |
| STL-007 | Ubiquitous | Rejected and inconclusive candidates shall be retained with failure reasons. | STL-G-07 |

**12.6 Testing and quality (TST)**

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| TST-001 | Ubiquitous | Every approved requirement with verification `test` shall have at least one passing test marker, and CI shall fail otherwise. | Harness |
| TST-002 | Unwanted | If a change adds a use case, port, adapter or gate without an `.ears` change, then CI shall fail. | Harness |
| TST-003 | Ubiquitous | Each driven port shall have one contract suite run against its fake and each available real adapter. | T3 |
| TST-004 | Ubiquitous | A package's mutation score shall not drop below its high-water mark minus 2 points. | T10 |
| TST-005 | Ubiquitous | Mocks shall not be used under domain, application or science modules. | Lint |
| TST-006 | Ubiquitous | The release report shall claim only reproducibility levels whose tiers passed in that run. | Harness |
| TST-007 | Unwanted | If the harness's re-derived verdict differs from the kernel's, then publication shall be blocked. | Harness, T7 |

**12.7 Non-functional (NFR)**

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| NFR-001 | Ubiquitous | The platform shall run on the designated DGX Spark under rootless Podman. | Deployment smoke |
| NFR-002 | Ubiquitous | Every scientific tier shall pass on CPU-only execution. | T8 on CI |
| NFR-003 | Ubiquitous | The platform shall verify artifact SHA-256 on every read from the artifact store. | T3, T5 |
| NFR-004 | Ubiquitous | Released experiments shall reproduce from pinned environments and accessible inputs in a fresh container. | T9 |
| NFR-005 | Ubiquitous | Logs shall be structured and carry experiment id, task id, duration, resource usage and failure reason. | T5 |
| NFR-006 | Ubiquitous | Domain and science code shall not depend on infrastructure implementations. | T0 |
| NFR-007 | Ubiquitous | Schema migrations shall be versioned and tested one step forward and one step back. | T5 |
| NFR-008 | Ubiquitous | Nondeterminism that prevents bitwise reproduction shall be documented per `TaskKind`. | Release review |
| NFR-009 | Ubiquitous | Credentials shall live outside Git, containers shall not run as root, and downloads shall be validated before parsing. | Security review, T5 |
| NFR-010 | Ubiquitous | Run metrics and GPU telemetry shall be exported for Grafana. | Observability test |

**Companion set** *(Investigate §10)*. The thirteen INV requirements live in `requirements/investigate/investigate.ears` and are compiled into the same tables and traceability matrix.

## 13. Traceability

The harness generates the full matrix from `.ears` files, test markers and gate definitions on every CI run and fails the build on any approved requirement with no passing test; the table below is the hand-maintained seed for the first release and the shape the generated matrix keeps.

| Requirement | Component | Test or suite | Gate | Use case |
|---|---|---|---|---|
| KRN-002 | `FreezePredeclaration`, `Ledger` | `test_predeclaration.py::test_freeze_appends_signed_ledger_entry` | — | Both |
| KRN-003 | `RunExperiment` | `test_run_experiment.py::test_refuses_unfrozen_predeclaration` | — | Both |
| KRN-004 | `RunExperiment`, manifest writer | `test_manifest.py::test_records_inputs_environment_and_seed` | — | Both |
| KRN-006 | Checkpointing runner | `test_runner_resume.py` (T5) | — | Both |
| KRN-009 | `Verdict` | `test_verdict.py::test_blocking_gate_failure_prevents_verified` | — | Both |
| KRN-011 | Plugin loader | `test_architecture.py::test_kernel_never_imports_science_modules` | — | Both |
| CTR-003 | `sdp.contracts`, `@sdp/contracts` | `test_fixtures.py`, `fixtures.test.ts` | — | Both |
| DSI-001 | `ArchiveGateway`, dialects | `contract_archive_gateway.py` over all adapters | — | UC-01 |
| DSI-004 | `AcquireDataset` | `test_acquire.py::test_idempotent_key_returns_existing_artifact` | — | UC-01 |
| DSI-007 | Archive adapters | cassette suites | — | UC-01 |
| REL-001 | `Measurement`, transcription schema | `test_measurement.py::test_requires_convention_and_locator` | REL-G-01 | UC-02 |
| REL-002 | `ErrorModel` | `test_error_model.py::test_probable_error_needs_explicit_conversion` | — | UC-02 |
| REL-003 | `DeflectionModel` | `test_deflection.py::test_limb_values_from_constants` | REL-G-03 | UC-02 |
| REL-004 | `ObservationSet`, `interpret()` | `test_pooling.py::test_requires_justification` | REL-G-05 | UC-02 |
| REL-006 | `SensitivitySweep` | `scientific/test_sensitivity_sweep.py` | REL-G-06 | UC-02 |
| REL-007 | `Fitter`, independent fitter | `scientific/test_fitter_agreement.py` | REL-G-08 | UC-02 |
| STL-002 | `FeatureSpacePolicy` | `test_feature_space.py::test_policy_is_versioned_and_whitened` | — | UC-01 |
| STL-004 | `CumlHdbscan` | `scientific/test_gpu_agreement.py` | STL-G-08 | UC-01 |
| STL-005 | `BenchmarkCrossMatch` | `test_cross_match.py::test_two_benchmarks_before_novel_label` | STL-G-06 | UC-01 |
| TST-001 | Harness coverage gate | `coverage-gate.test.ts` | — | Both |
| TST-007 | Harness `GateEvaluator` | `rederive-verdict.test.ts`, nightly T7 | — | Both |
| NFR-004 | `reproduce.sh`, harness | T9 release job | — | Both |
| NFR-006 | import-linter contracts | T0 | — | Both |

**The gate itself.** `spec-harness trace` reads every `.ears` file, every JUnit report from both toolchains and the predeclared gate lists, then emits `traceability.md` and `traceability.json` into the release report. It exits non-zero when an approved `test` requirement has no passing marker, when a marker names a requirement that does not exist, or when a gate named in a predeclaration has no evaluating test. Retired requirements keep their rows with a `retired` status so history survives.

## 14. Architecture decision records

Sixteen records in MADR form; each states context, decision, alternatives and consequences, and each lives as `docs/adr/NNNN-slug.md` with the status below. All sixteen records were accepted on 28 October 2026 (§19).

### ADR-001 Hexagonal kernel with plugin science modules — Accepted

**Context.** 0.2.0 drew the core depending on the two science domains. **Decision.** One kernel hexagon; science modules implement `ScientificProtocol` and are loaded by entry point; the kernel never imports them. **Alternatives.** Science inside the kernel (rejected: every new programme is a kernel change); one hexagon per science domain with duplicated evidence logic (rejected: evidence rules diverge). **Consequences.** A third programme is a package; the plugin contract becomes a versioned public API that needs its own contract tests.

### ADR-002 Python kernel, TypeScript specification harness and UI — Accepted

**Context.** A mix of Python and TypeScript is wanted; the astronomy ecosystem is Python; the EARS, mutation and release-harness tooling is TypeScript. **Decision.** Python owns domain, science and adapters; TypeScript owns `.ears` compilation, traceability, manifest conformance, verdict re-derivation, release report and the UI. **Alternatives.** All Python (rejected: no independent verification layer, two toolchains across repos); TypeScript API gateway in front of Python (rejected: duplicated boundary with no scientific benefit); TypeScript workflows (rejected: Prefect-class tooling is Python). **Consequences.** Two toolchains in CI; a contract repository with codegen; a genuinely independent check on every verdict.

### ADR-003 Contract-first boundary with JSON Schema and generated types — Accepted

**Context.** Two languages must agree on manifests, reports and ledger entries. **Decision.** Schemas in `contracts/` are the single source; Pydantic and Zod are generated and committed; golden fixtures are tested in both languages. **Alternatives.** Protobuf (rejected: poorer fit for JSON-LD provenance and RO-Crate); hand-written types on both sides (rejected: drift). **Consequences.** Schema changes are visible diffs; breaking changes need an ADR.

### ADR-004 Signed predeclaration ledger — Accepted

**Context.** Preregistration was named but had no mechanism. **Decision.** `freeze()` hashes the predeclaration and writes a signed, append-only ledger entry; experiments refuse to start without it; reviewer approvals of verdicts are ledger entries too. **Alternatives.** Database flag (rejected: mutable, unsigned); external preregistration service (rejected: offline reproduction). **Consequences.** Git signing is required on the DGX Spark; self-approval ends when a second maintainer joins.

### ADR-005 Requirements as `.ears` data with a CI coverage gate — Accepted

**Context.** Markdown tables are not checkable. **Decision.** `.ears` files, compiled by the harness into tables, matrix and marker lists; CI fails on untested approved requirements and on behaviour changes without an `.ears` change. **Alternatives.** Gherkin only (rejected: scenario-shaped, not requirement-shaped); issue tracker (rejected: not versioned with code). **Consequences.** Small friction on every PR; a living requirements baseline.

### ADR-006 Fakes over mocks, contract suites per port — Accepted

**Context.** Mock-heavy tests pass while adapters fail. **Decision.** Each driven port ships an in-memory fake in `sdp.testing`; one contract suite runs against the fake and every real adapter; mocks are lint-forbidden inside the hexagon. **Alternatives.** Mocks (rejected); only real adapters (rejected: slow, infra-bound). **Consequences.** Fakes are maintained as product code; T3 is the largest suite.

### ADR-007 CPU reference first, GPU as adapter with agreement tests — Accepted

**Context.** From 0.2.0 ADR-006. **Decision.** Every `TaskKind` has a CPU implementation; CUDA is an adapter admitted to gates only after passing its agreement test in the same release. **Consequences.** Nondeterministic GPU reductions are documented, not hidden.

### ADR-008 Immutable content-addressed artifacts — Accepted

**Context.** From 0.2.0 ADR-007. **Decision.** `ArtifactId` is SHA-256 of canonical bytes; the artifact store is write-once; raw is never modified. **Consequences.** Dedup for free; curation always derives a new artifact.

### ADR-009 PROV-aligned evidence graph and RO-Crate bundles — Accepted

**Context.** Ad-hoc manifests are unreadable by anyone else. **Decision.** The evidence graph uses W3C PROV concepts (entity, activity, agent) with PROV-JSON export; publication bundles are RO-Crate with `ro-crate-metadata.json`. **Alternatives.** Custom JSON (rejected: no tooling); full OpenLineage (rejected: pipeline-centred, heavy). **Consequences.** Zenodo and FAIR tooling read the bundles; a small vocabulary mapping is maintained.

### ADR-010 Units through `astropy.units` at every port — Accepted

**Context.** Unit errors are the classic silent scientific failure. **Decision.** Physical values cross ports only as `Quantity`; constants come from `astropy.constants` with a recorded CODATA version. **Alternatives.** pint (rejected: second units system beside astropy). **Consequences.** Some boilerplate; unit tests become dimensional tests.

### ADR-011 Parquet curated, PostgreSQL metadata, DuckDB analytics — Accepted

**Context.** From 0.2.0 ADR-003 to ADR-005, merged. **Decision.** As stated; FITS originals retained in raw. **Consequences.** One relational store, one columnar format, no cluster.

### ADR-012 Kernel-owned checkpointing runner; Prefect as optional driving adapter — Accepted

**Context.** Single host, bounded jobs, solo builder. **Decision.** The application layer owns a runner with checkpoints and resume; Prefect is added as a driving adapter only when fan-out across regions needs it. **Alternatives.** Prefect from day one (rejected: another service to keep alive for the Einstein slice). **Consequences.** Resume semantics are tested in the kernel, not delegated.

### ADR-013 Rootless Podman with Quadlet and CDI on the DGX Spark — Accepted

**Context.** 0.2.0 asked for OCI containers without root. **Decision.** Rootless Podman, systemd Quadlet units for PostgreSQL, the API and the harness, GPU exposure through CDI. **Alternatives.** Docker (rejected: root daemon by default); bare metal (rejected: no reproducibility). **Consequences.** A Phase 0 spike must confirm CDI and the CUDA toolchain on DGX OS.

### ADR-014 Einstein examination as the first vertical slice — Accepted

**Context.** From 0.2.0 §22. **Decision.** Build the relativity module end to end before any Gaia science; build the data-source layer next. **Consequences.** Kernel, contracts, harness and UI are proven on hundreds of numbers before millions.

### ADR-015 Archive access through recorded cassettes in CI — Accepted

**Context.** Live archives are rate-limited and change. **Decision.** Contract tests replay committed cassettes keyed by query hash; one weekly live job re-records and detects schema drift. **Consequences.** Cassettes are reviewed like code; drift is caught within a week.

### ADR-016 GraphQL for the query side, CLI and minimal REST for commands — Accepted

**Context.** The UI browses a provenance graph. **Decision.** Strawberry GraphQL on the ASGI app for queries; commands stay on the CLI and a small REST surface; the committed `schema.graphql` is a contract. **Alternatives.** REST only (rejected: lineage queries become many round trips); GraphQL mutations (rejected: commands belong to the CLI's signed workflow). **Consequences.** A generated typed client in the UI; one more schema under drift checks.

## 15. Security and integrity model

The threats that matter here are integrity threats: a quietly altered input, an unsigned change of intent, a dependency that changes a number, or a malformed FITS file that executes something. The model is the same stance used across ESI.ts, Tutors and Íomhá, grounded in six controls with a test or review each.

| Control | Mechanism | Verified by |
|---|---|---|
| Intent integrity | Signed ledger entries for predeclarations and approvals; required signatures on the ledger branch; a second maintainer ends self-approval | T4 ledger contract, repo rules |
| Input integrity | SHA-256 on every artifact, verified on read; raw area write-once; curated artifacts cite raw parents | T3, T5, NFR-003 |
| Supply chain | `uv.lock` and `pnpm-lock.yaml` pinned; image digests pinned in Quadlet units; SBOM generated per release; Dependabot-style updates land through the full pipeline | Release job |
| Untrusted data | FITS, VOTable, PDF and Parquet parsed in a subprocess with resource limits; no pickle anywhere; downloads validated before parsing (NFR-009) | T5 with malformed fixtures |
| Least privilege | Rootless Podman; containers drop capabilities; NAS mounted read-only into runners except the experiment and artifact areas; archive credentials in Podman secrets | Deployment smoke |
| Observability of change | Every domain event logged; release report lists dependency, schema and dialect changes since the last release | Harness |

Archive credentials are optional (Gaia allows anonymous access with lower quotas) and never enter a manifest. The `.ears` files, ADRs and ledger are the three artifacts that may not change without review, enforced by branch protection on their paths.

## 16. Infrastructure, deployment and repository layout

One polyglot monorepo, two workspaces, one task runner, and a DGX Spark that runs everything under rootless Podman with the NAS mounted once.

**Host.** NVIDIA DGX Spark (ARM64, unified CPU and GPU memory) on DGX OS; the exact driver, CUDA, container toolkit and NAS protocol are verified in the Phase 0 spike before any dependency is pinned, as 0.2.0 §9.1 required. NAS layout and the 4,000 GB allocation plan are carried from 0.2.0 §8.2 and §8.3.

**Runtime units (Quadlet, rootless)**

| Unit | Image | Role |
|---|---|---|
| `sdp-postgres.container` | PostgreSQL 16 | Metadata, provenance, event log; nightly `pg_dump` to NAS `backups/` with a tested restore |
| `sdp-api.container` | `sdp-kernel` + adapters | ASGI app: GraphQL queries, command endpoints, health |
| `sdp-runner.container` | `sdp-kernel` + adapters, CDI GPU device | Executes experiments; one at a time initially |
| `sdp-harness.container` | `spec-harness` | Compiles `.ears`, verifies manifests, builds release reports |
| `sdp-ui.container` | `evidence-ui` | Dossier and provenance browser |
| `sdp-grafana.container`, exporters | Grafana, DCGM exporter, postgres exporter | Run metrics and GPU telemetry (NFR-010) |

**Repository layout**

```text
sdp/
├── contracts/                  # §7: schemas, graphql, fixtures, changelog
├── requirements/               # §12: .ears files by capability
├── ledger/                     # §15: signed predeclaration and approval entries (jsonl)
├── docs/
│   ├── architecture.md         # this specification, exported
│   ├── adr/                    # §14, one file per record
│   ├── c4/                     # context, container, component views
│   └── protocols/              # scientific protocols per module
├── python/                     # uv workspace
│   ├── packages/sdp-kernel/    # domain, application, ports, science contracts, testing fakes
│   ├── packages/sdp-contracts/ # generated Pydantic DTOs
│   ├── packages/sdp-adapters/  # gaia, vizier, mast, irsa, historical, nas, postgres, duckdb, compute, ledger, mlflow
│   ├── packages/sdp-relativity/
│   ├── packages/sdp-stellar/
│   ├── packages/sdp-cli/
│   └── packages/sdp-api/
├── typescript/                 # pnpm workspace
│   ├── packages/contracts/     # @sdp/contracts, generated
│   ├── packages/spec-harness/  # ears, trace, conform, rederive, report
│   ├── packages/evidence-ui/   # SvelteKit
│   └── packages/e2e/           # Playwright
├── tests/cross-language/       # T6 and T7 drivers
├── experiments/                # predeclarations and run manifests, by protocol
├── publications/               # RO-Crate bundles
├── infrastructure/
│   ├── quadlet/                # systemd units
│   ├── compose/                # developer stack
│   └── grafana/
├── justfile                    # one entry point: just test, just gate, just reproduce
└── .github/workflows/          # CI stages of §11.7; a self-hosted ARM64 runner on the DGX Spark for GPU jobs
```

**Tooling.** Python: uv workspaces, Ruff, mypy strict, pytest, Hypothesis, import-linter, mutmut, testcontainers, pytest-recording, schemathesis. TypeScript: pnpm workspaces, tsc strict, ESLint, vitest, fast-check, dependency-cruiser, Stryker, Playwright. Shared: just, pre-commit, Podman, release-please for versioning, Zenodo for DOIs.

**Developer loop.** `just test` runs stages 1–2 of §11.7 in under ten minutes on a laptop with fakes and tmpfs; `just gate` runs the merge stage against testcontainers; `just reproduce <bundle>` is the same script a reviewer runs.

## 17. Roadmap

Seven phases over about sixteen weeks, each closed by a gate the harness can evaluate; the walking skeleton in Phase 1 crosses both languages before any science is written, and the Einstein slice is complete before the first Gaia byte is curated. Durations are planning estimates for a solo builder.

> *Figure: roadmap · 7 phases over 16 weeks, a gate on each phase edge, Gaia DR4 marked* (see `docs/c4/`)

Bands are equal, not to scale; the Gaia DR4 marker falls in the Einstein phase, which is why the DR4 dialect is scheduled for the data-layer phase after its documentation is public.

| Phase | Weeks | Deliverables | Exit gate |
|---|---|---|---|

| 0 Protocols and spike | 1 | Draft predeclarations for both programmes; `contracts/` v0.1 with fixtures; DGX Spark spike (CUDA, CDI, rootless Podman, NAS throughput); ADR-002, 003, 009, 012, 013, 016 accepted 28 Oct 2026 | Both predeclarations have hypotheses, gates and oracle tolerances; the spike report is in `docs/` |
| 1 Walking skeleton | 2–3 | Kernel domain, ports, fakes and runner; PostgreSQL and NAS adapters; CLI; synthetic `ScientificProtocol`; harness `ears`, `trace`, `conform`, `rederive`; UI dossier page; CI stages 1–3 | A synthetic experiment runs register → freeze → define → run → validate → manifest; the harness re-derives the verdict; T0, T3, T6 and the requirement-coverage gate are green |
| 2 Einstein slice | 4–6 | Historical source registry; verified transcription; deflection models; reduction scenarios; fitter and independent fitter; model comparison; sensitivity sweep; dossier | REL-G-01 to REL-G-09 pass or their failures are documented; the dossier exports as an RO-Crate draft |
| 3 Data-source layer | 7–9 | `ArchiveGateway` adapters for Gaia DR3, VizieR, MAST, IRSA with dialects and cassettes; benchmark catalogues registered; cross-match agreement; curated Parquet; DR4 dialect once its documentation is public | DSI-001 to DSI-010 green; weekly live smoke running; predeclared regions acquired and checksummed |
| 4 Stellar slice | 10–12 | Feature-space policy; CPU clustering adapters; benchmark cross-match; membership and CMD checks; null and injection; candidate dossiers; cuML spike | STL-G-01 to STL-G-09 on CPU; GPU agreement result recorded |
| 5 Validation and hardening | 13–14 | Full T8 on both modules; mutation baselines and ratchets; T9 clean-room reproduction; Grafana dashboards | Both programmes pass their gates or document failures; mutation thresholds recorded |
| 6 Release | 15–16 | RO-Crate bundles; Zenodo DOIs; harness release report; architecture report; JOSS draft | An independent clean environment reproduces both reference experiments from the bundles alone |

**Investigate on the roadmap** *(Investigate §9)*. Investigate on one predeclared field becomes the exit criterion of the data-layer phase; the Imviz plugin lands with the stellar slice.

## 18. Risks and mitigations

The risks that could sink the programme are integrity and scope risks, not performance ones; each row names the control that already exists in this specification.

| Risk | Impact | Mitigation | Owner section |
|---|---|---|---|
| Two toolchains drift apart | Harness silently stops reading what the kernel writes | Contract repository, codegen diff gate, golden fixtures in both languages, nightly T7 | §7 |
| Gaia DR4 arrives mid-programme (2 Dec 2026) | Benchmark moves under the analysis | Predeclare DR3; DR4 as a separate cross-release experiment; dialect per release | §8 |
| cuML or CUDA toolchain gaps on ARM64 | GPU adapter late | CPU reference first; Phase 0 spike; GPU results never required for a gate | ADR-007, §17 |
| Historical plate data unavailable | UC-02 cannot go beyond published tables | Recoverability stated in the dossier; REL-G-09 lint on claims | §9 |
| Transcription errors | Wrong conclusions | Two-pass verification records; oracle status gate | REL-G-01, REL-G-02 |
| Probable-error versus standard-error confusion | Overstated or understated significance | Convention is a `Measurement` field; conversion is explicit and tested | §5, REL-002 |
| Over-engineering before science | Slow delivery | Walking skeleton then Einstein slice; Prefect and MLflow deferred | ADR-012, ADR-014 |
| Archive schema or rate-limit changes | Broken ingestion | Cassettes, weekly live smoke, dialect golden rows | ADR-015 |
| Self-approval on a solo project | Weak preregistration | Ledger entries are signed and timestamped; second maintainer ends self-approval and re-reviews | ADR-004 |
| NAS exhaustion | Workflow failure | DSI-010 threshold refusal; allocation plan; backups tested | §8, §16 |
| Mutation testing too slow on science code | Gate ignored | Per-package scoring, weekly cadence, ratchet rather than absolute target | §11.5 |
| Publication novelty insufficient | No paper | Software contribution (JOSS) separated from scientific contribution; literature review in Phase 2 | §9, 0.2.0 §18 |

## 19. Decisions taken

Leigh accepted every recommendation on 28 October 2026; Phase 0 builds against the record below.

| # | Decision | Closes |
|---|---|---|
| 1 | Python kernel, science modules and adapters; TypeScript specification harness and evidence UI | ADR-002 |
| 2 | SDP's harness stays purpose-built, keeping `.ears` files and the signed ledger in CSH-compatible conventions, with no dependency on CSH; CSH may analyse the repository later as an external polyglot target | ADR-005 |
| 3 | Kernel-owned checkpointing runner through Phase 4; Prefect only if region fan-out needs it | ADR-012 |
| 4 | GraphQL for queries with a committed schema; commands on the CLI | ADR-016 |
| 5 | PROV-JSON export and RO-Crate bundles from Phase 1 | ADR-009 |
| 6 | Predeclare `gaia:DR3`; run DR4 as a separate experiment under the same predeclaration after 2 December 2026 | §8 |

Also accepted: the evidence UI is SvelteKit, matching the Tutors toolchain. `sdp` stays the working package name until a project name is chosen.

## 20. References

**Data and releases**

- [ESA Gaia Archive](https://gea.esac.esa.int/archive/) and [Gaia DR3 documentation](https://gea.esac.esa.int/archive/documentation/GDR3/); Gaia DR3 DOI [10.5270/esa-qa4lep3](https://doi.org/10.5270/esa-qa4lep3)
- [Gaia mission news: DR4 planned for 2 December 2026](https://www.cosmos.esa.int/web/gaia/news)
- [Hunt and Reffert 2023, all-sky cluster catalogue with Gaia DR3](https://arxiv.org/abs/2303.13424)
- [VizieR](https://vizier.cds.unistra.fr/), [MAST](https://archive.stsci.edu/), [IRSA](https://irsa.ipac.caltech.edu/)

**Historical relativity**

- [Dyson, Eddington and Davidson 1920](https://doi.org/10.1098/rsta.1920.0009)
- Harvey 1979, The Observatory 99, 195 (remeasurement of the Sobral plates)
- [Gilmore and Tausch-Pebody 2020](https://doi.org/10.1098/rsnr.2020.0040)
- [O'Raifeartaigh et al. on Einstein's 1917 cosmology](https://arxiv.org/abs/1701.07261)

**Standards and methods**

- [EARS: Easy Approach to Requirements Syntax](https://alistairmavin.com/ears/)
- [MADR: Markdown Architectural Decision Records](https://adr.github.io/madr/)
- [W3C PROV data model](https://www.w3.org/TR/prov-dm/) and [RO-Crate](https://www.researchobject.org/ro-crate/)
- [FAIR principles](https://doi.org/10.1038/sdata.2016.18)
- [C4 model](https://c4model.com/)

**Software**

- [Astropy](https://www.astropy.org/), [Astroquery](https://astroquery.readthedocs.io/), [DuckDB](https://duckdb.org/), [Apache Parquet](https://parquet.apache.org/)
- [Hypothesis](https://hypothesis.readthedocs.io/), [pytest](https://docs.pytest.org/), [import-linter](https://import-linter.readthedocs.io/), [mutmut](https://mutmut.readthedocs.io/)
- [fast-check](https://fast-check.dev/), [Stryker](https://stryker-mutator.io/), [dependency-cruiser](https://github.com/sverweij/dependency-cruiser)
- [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html), [NVIDIA CDI support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)
- [Journal of Open Source Software](https://joss.theoj.org/), [Zenodo](https://zenodo.org/)

**Supersedes:** Scientific Discovery Platform, Software Design Document v0.2.0, 28 October 2026.
