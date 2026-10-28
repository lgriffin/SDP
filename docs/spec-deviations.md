# Deviations from the architecture specification

Each entry names the spec section, what the code does instead, and why. None changes a gate or a verdict rule.

| # | Spec | Implementation | Why |
|---|---|---|---|
| D1 | §5 `ScientificProtocol` has five methods | Adds `task_kinds() -> TaskKindRegistry` | The kernel validates a `ComputePlan` against the registry (KRN-012) before execution; a module must declare its task kinds and their CPU reference implementations somewhere the kernel can see without importing the module. |
| D2 | §5 `validate(findings, predeclaration)` | `validate(findings, predeclaration, *, experiment_id)` | The `ValidationReport` schema requires `experiment_id`; passing it keeps reports self-describing without the module reaching into kernel state. |
| D3 | §5 driven ports list | Adds `HypothesisRepository`, `PredeclarationRepository`, `ExperimentRepository`, `DossierRepository`, grouped in a `UnitOfWork` | KRN-010 needs state changes and their events in one transaction; a unit of work over typed repositories is the smallest port that expresses that. |
| D4 | §5 domain events (13) | Adds `ExperimentRefused` | The lifecycle diagram has a Refused state and KRN-003 requires the refusal to be recorded; an event is how every other state change is recorded. |
| D5 | §7 `SourceRef` requires a DOI for documents | Accepts a DOI or, for works that have none, an ADS bibcode | Several 1919–1920 eclipse papers have no DOI. The schema's `if/then` rule mirrors this. |
| D6 | §9 kernel trusts `validate()` | The kernel re-evaluates the frozen gates and refuses a report whose checks differ (`VerdictInconsistent`) | Defence in depth for KRN-009; it is the in-process form of the harness re-derivation (TST-007). |
| D7 | §10 checkpoint format unspecified | Canonical-JSON codec with tagged `.npy` arrays (no pickle), Arrow IPC tables, and tagged integral or non-finite floats | Identical results give identical artifact ids; a resumed task receives exactly the types it wrote. |
| D8 | §10 curated datasets | Curated Parquet files carry `sdp.dialect` and `sdp.raw_parent` schema metadata | DSI-005 is then visible in the file itself, and a curation that renames nothing still gets its own content address. |
