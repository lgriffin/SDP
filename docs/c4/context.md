# System context

SDP is one host running a Python kernel, a TypeScript harness and UI, PostgreSQL and a NAS; it reaches outward only to public archives, the historical record and Zenodo.

```mermaid
flowchart LR
  researcher([Researcher]) -->|CLI, notebooks, evidence UI| sdp
  reviewer([Reviewer]) -->|evidence UI, signs approvals| sdp
  ci([CI]) -->|runs every gate unattended| sdp
  subgraph sdp [SDP on the DGX Spark]
    direction TB
    kernel[Python kernel + science modules]
    harness[TypeScript harness + evidence UI]
  end
  sdp -->|TAP/ADQL| gaia[(ESA Gaia Archive)]
  sdp -->|TAP, X-Match| vizier[(CDS VizieR / X-Match)]
  sdp -->|TAP / HTTP| mast[(MAST and IRSA)]
  sdp -->|PDF by DOI| hist[(Historical record)]
  sdp -->|REST| zenodo[(Zenodo)]
```

| External system | Protocol | Adapter |
|---|---|---|
| ESA Gaia Archive | TAP/ADQL | `GaiaTapArchiveAdapter` |
| CDS VizieR / X-Match | TAP, X-Match HTTP | `VizierArchiveAdapter`, `CdsXMatchAdapter` |
| MAST and IRSA | TAP / HTTP | `MastArchiveAdapter`, `IrsaArchiveAdapter` |
| Historical record | PDF by DOI | `FilesystemHistoricalSources` |
| Zenodo | REST | `ZenodoPublisherAdapter` |
