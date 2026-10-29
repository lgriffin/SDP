# Level 2: Containers

Python owns the science and the evidence it produces; TypeScript checks that evidence independently and shows it to people. The two sides share one thing, `contracts/`, and both generate their types from it. Archives and the historical record enter only through `sdp-adapters`.

```mermaid
flowchart LR
    researcher(["Researcher"])
    reviewer(["Reviewer"])

    subgraph py ["Python · uv workspace"]
        direction TB
        cli["sdp-cli<br/>[Typer]<br/>composition root; signed steps"]
        api["sdp-api<br/>[ASGI: GraphQL + REST]<br/>composition root for the UIs"]
        kernel[["sdp-kernel<br/>[Python]<br/>domain, use cases, ports, science SPI, fakes"]]
        science["sdp-relativity · sdp-stellar · sdp-investigate<br/>[Python]<br/>ScientificProtocol plugins and test kinds"]
        adapters["sdp-adapters<br/>[Python]<br/>archives, products, stores, compute, ledger"]
        contractspy["sdp-contracts<br/>[Pydantic, generated]"]
    end

    subgraph ts ["TypeScript · pnpm workspace"]
        direction TB
        ui["evidence-ui<br/>[SvelteKit, adapter-node]"]
        desktop["sdp-desktop<br/>[Electron]<br/>bundles UI, API, Python and Postgres"]
        harness["spec-harness<br/>[Node]<br/>ears, trace, guard, conform, rederive, admit, ratchet, report"]
        relfit["relativity-fit<br/>[Node]<br/>independent UC-02 fitter"]
        contractsts["@sdp/contracts<br/>[Zod, generated]"]
    end

    viewer["sdp-imviz<br/>[Jdaviz plugin]<br/>renders InvestigateField"]

    pg[("PostgreSQL 16<br/>metadata, events, plans")]
    nas[("NAS artifact store<br/>raw and curated bytes")]
    duck[("DuckDB<br/>analytics over Parquet")]
    ledger[("Git signed ledger")]
    schemas{{"contracts/<br/>JSON Schemas"}}
    ext[/"Archives, registry,<br/>historical record, Zenodo"/]

    researcher --> cli
    researcher --> ui
    researcher --> desktop
    researcher --> viewer
    reviewer --> ui
    reviewer -- "signs" --> cli
    desktop --> ui
    desktop --> api
    ui -- "GraphQL, REST" --> api
    cli --> kernel
    api --> kernel
    viewer --> kernel
    science -. "implements plugin SPI" .-> kernel
    adapters -. "implements driven ports" .-> kernel
    cli --> adapters
    api --> adapters
    adapters --> pg
    adapters --> nas
    adapters --> duck
    adapters --> ledger
    adapters -- "Transport" --> ext
    harness -- "manifests, ledger, .ears, JUnit" --> nas
    relfit -. "second implementation of the fit" .-> harness
    schemas -. "generates" .-> contractspy
    schemas -. "generates" .-> contractsts
    kernel --> contractspy
    ui --> contractsts
    harness --> contractsts

    style researcher fill:#08427b,color:#fff,stroke:#073b6f
    style reviewer fill:#08427b,color:#fff,stroke:#073b6f
    style kernel fill:#1168bd,color:#fff,stroke:#0e5aa7
    style cli fill:#438dd5,color:#fff,stroke:#3c7fc0
    style api fill:#438dd5,color:#fff,stroke:#3c7fc0
    style science fill:#438dd5,color:#fff,stroke:#3c7fc0
    style adapters fill:#438dd5,color:#fff,stroke:#3c7fc0
    style contractspy fill:#438dd5,color:#fff,stroke:#3c7fc0
    style ui fill:#438dd5,color:#fff,stroke:#3c7fc0
    style desktop fill:#438dd5,color:#fff,stroke:#3c7fc0
    style harness fill:#438dd5,color:#fff,stroke:#3c7fc0
    style relfit fill:#438dd5,color:#fff,stroke:#3c7fc0
    style contractsts fill:#438dd5,color:#fff,stroke:#3c7fc0
    style viewer fill:#438dd5,color:#fff,stroke:#3c7fc0
    style ext fill:#999,color:#fff,stroke:#888
    style py fill:none,stroke:#1168bd,stroke-width:2px
    style ts fill:none,stroke:#1168bd,stroke-width:2px
```

| Container | Owns | Must not | Enforced by |
|---|---|---|---|
| `sdp-kernel` | Domain, use cases, ports, the science SPI, the fakes in `sdp.testing` | Import an adapter, a framework or I/O library | import-linter (T0), NFR-006, TST-005 |
| science plugins | Protocols, test kinds, oracles | Import anything but `sdp.kernel.domain` and `sdp.kernel.science` | import-linter |
| `sdp-adapters` | Every driven port's real implementation | Import another adapter; parse a download before `untrusted.validate_votable` | import-linter, NFR-009 |
| `sdp-cli`, `sdp-api` | Wiring adapters to use cases | Hold business rules | review; use-case tests (T4) run without them |
| `spec-harness` | An independent reading of manifests, ledger and requirements | Import the Python side; trust the kernel's verdict | dependency-cruiser, TST-007 |
| `evidence-ui`, `sdp-desktop` | Presentation; unsigned commands | Sign anything (INV-015) | e2e tests |
| `contracts/` | Every boundary type, once | Be hand-written in either language | CTR-001, CTR-002 |

The [kernel components](components.md), [adapter components](components-adapters.md) and [harness components](components-harness.md) open the three containers that carry the most rules.
