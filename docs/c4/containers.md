# Containers

Python owns the science, TypeScript checks the evidence. Archives and the historical record enter only through `sdp-adapters`; the harness reads manifests from the NAS and the UI reads the kernel through GraphQL, with `contracts/` the one thing both sides generate from.

```mermaid
flowchart LR
  subgraph ext [External]
    gaia[(Gaia)]
    vizier[(VizieR / CDS)]
    mast[(MAST / IRSA)]
    hist[(Historical record)]
    zenodo[(Zenodo)]
  end
  subgraph py [Python - uv workspace]
    cli[sdp-cli / sdp-api<br/>composition roots]
    kernel[[sdp-kernel<br/>domain, use cases, ports]]
    science[sdp-relativity / sdp-stellar<br/>ScientificProtocol plugins]
    adapters[sdp-adapters<br/>archives, stores, compute]
    cli --> kernel
    science -. implements plugin port .-> kernel
    adapters -. implements driven ports .-> kernel
    cli --> adapters
  end
  subgraph ts [TypeScript - pnpm workspace]
    contracts[@sdp/contracts<br/>generated types and Zod]
    ui[evidence-ui<br/>SvelteKit]
    harness[spec-harness<br/>ears, trace, conform, rederive, report]
    ui --> contracts
    harness --> contracts
  end
  ext --> adapters
  adapters --> pg[(PostgreSQL 16)]
  adapters --> nas[(NAS)]
  adapters --> duck[(DuckDB)]
  ui -->|GraphQL| cli
  harness -->|manifests, ledger, .ears| nas
  schemas{{contracts/ JSON Schemas}} -.generates.-> contracts
  schemas -.generates.-> kernel
```
