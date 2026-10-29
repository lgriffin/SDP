# Level 3: Kernel components

The kernel is a hexagon. Driving adapters call use cases through the driving ports on the left; driven ports on the right are implemented by `sdp-adapters` in production and by the fakes in `sdp.testing` under test; science plugins arrive from above through the science SPI. The same contract suite (`sdp.testing.contracts`) runs against a port's fake and its real adapter, so a fake cannot pass what the adapter fails (TST-003).

```mermaid
flowchart LR
    subgraph driving ["Driving adapters"]
        direction TB
        cli["Typer CLI<br/>sdp.cli"]
        api["GraphQL + REST<br/>sdp.api"]
        viewer["Imviz plugin"]
        runner["Checkpointing runner"]
    end

    subgraph hex ["sdp.kernel"]
        direction TB
        dports["driving ports<br/>ports.driving · ports.investigate · ports.integration<br/>RegisterHypothesis, FreezePredeclaration, AcquireDataset,<br/>Define/Run/ValidateExperiment, AssembleDossier, PublishBundle,<br/>QueryEvidence, InvestigateField, TriageInvestigation,<br/>Plan/Approve/ExecutePlan"]
        app["application<br/>hypotheses · predeclarations · acquisition · experiments ·<br/>execution · dossiers · publication · investigate · queries ·<br/>manifest · records · rocrate · codec"]
        domain[("domain<br/>experiment lifecycle · predeclaration · gates · verdict ·<br/>dataset · provenance · investigation · association ·<br/>integration (product envelope, capabilities, plans)")]
        science["science SPI<br/>ScientificProtocol · ComputePlan · NumericalPolicy ·<br/>investigation test kinds · association · products"]
        drports["driven ports<br/>repositories · events · evidence · ledger · storage ·<br/>archives · compute · determinism · publication ·<br/>integration · investigate · plugins"]
        dports --> app
        app --> domain
        app --> science
        app --> drports
        science --> domain
    end

    subgraph plugins ["Science plugins"]
        direction TB
        rel["sdp_relativity<br/>UC-02 Einstein 1919"]
        stl["sdp_stellar<br/>UC-01 cluster search, stellar ranker"]
        inv["sdp_investigate<br/>astrometry, photometry test kinds"]
    end

    subgraph driven ["Driven adapters · sdp.adapters"]
        direction TB
        pg["postgres: repositories, event log,<br/>capabilities, plans"]
        store["storage.nas · analytics.duckdb"]
        arch["archives: TAP, MAST, SIMBAD, DASCH,<br/>registry, X-Match over Transport"]
        prod["products: FITS, JWST, validator"]
        comp["compute: CPU executor, clustering<br/>imaging: photutils, tweakwcs"]
        led["ledger.git (signed)"]
        pub["publication.zenodo · telemetry"]
    end

    subgraph fakes ["sdp.testing"]
        direction TB
        f["fakes: in-memory port implementations"]
        k["contracts: one suite per driven port"]
        syn["synthetic fields, catalogues, emitters"]
    end

    driving --> dports
    plugins -- "implements" --> science
    driven -- "implements" --> drports
    f -- "implements" --> drports
    k -. "runs against" .-> f
    k -. "runs against" .-> driven

    style hex fill:none,stroke:#1168bd,stroke-width:2px
    style domain fill:#1168bd,color:#fff,stroke:#0e5aa7
    style app fill:#1168bd,color:#fff,stroke:#0e5aa7
    style dports fill:#438dd5,color:#fff,stroke:#3c7fc0
    style drports fill:#438dd5,color:#fff,stroke:#3c7fc0
    style science fill:#438dd5,color:#fff,stroke:#3c7fc0
    style fakes fill:none,stroke:#999,stroke-width:2px,stroke-dasharray:5
```

## Dependency rule

Enforced by import-linter (T0, §11.3):

- `domain` imports nothing above it.
- `application` imports `domain`, `ports` and `science`.
- Science plugins import only `sdp.kernel.domain` and `sdp.kernel.science`.
- Adapters never import each other; only the composition roots (`sdp.cli`, `sdp.api`, `sdp.adapters.bootstrap`) import adapters.
- Nothing under `domain`, `application` or `science` uses `unittest.mock` (TST-005); the doubles are the fakes.

## What each component is tested by

| Component | Tier | Where | Mutation target |
|---|---|---|---|
| domain | T1 unit, T2 property | `python/tests/unit/kernel`, `python/tests/property`, `python/tests/investigate`, `python/tests/association` | `sdp-kernel-domain` |
| science SPI | T1, T4 | `python/tests/unit/kernel`, `python/tests/usecase`, `python/tests/investigate`, `python/tests/archives` | `sdp-kernel-science` |
| application | T4 use case on fakes, BDD | `python/tests/usecase`, `python/tests/bdd` | `sdp-kernel-application` |
| driven ports | T3 contract | `python/tests/contract` (fakes), `python/tests/integration` (real) | — |
| plugins | T1, T8 scientific | `python/tests/relativity`, `python/tests/stellar` | `sdp-relativity-*`, `sdp-stellar-*` |

The [testing strategy](../testing.md) explains each tier and the signal that proves it can fail.
