# Kernel components

Driving adapters call use cases from the left, driven adapters implement ports on the right, science modules plug in from above through `ScientificProtocol`, and the fakes at the bottom implement the same driven ports under the same contract suite.

```mermaid
flowchart LR
  subgraph driving [Driving adapters]
    cli[Typer CLI]
    api[ASGI app: GraphQL + REST]
    runner[Checkpointing runner / optional Prefect]
  end
  subgraph hex [sdp.kernel]
    direction TB
    app[application<br/>9 use cases + runner]
    domain[(domain<br/>value objects, aggregates, events)]
    ports[ports<br/>driving + driven Protocols]
    science[science<br/>ScientificProtocol, ComputePlan, NumericalPolicy]
    app --> domain
    app --> ports
    science --> domain
  end
  subgraph plugins [Science plugins]
    rel[sdp_relativity]
    stl[sdp_stellar]
  end
  subgraph driven [Driven adapters]
    pg[Postgres repositories,<br/>event log, evidence graph]
    nas[NAS artifact store]
    arch[Archive gateways]
    comp[CPU / CUDA executors]
    ledger[Git signed ledger]
  end
  subgraph fakes [sdp.testing fakes]
    f[In-memory fakes<br/>same contract suites]
  end
  driving --> app
  plugins -->|implements| science
  driven -->|implements| ports
  fakes -->|implements| ports
```

Dependency rule (enforced by import-linter, §11.3): domain imports nothing above it; application imports domain and ports; science modules import only `sdp.kernel.domain` and `sdp.kernel.science`; adapters never import each other; only composition roots import adapters.
