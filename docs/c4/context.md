# Level 1: System context

SDP is one host (the DGX Spark, or a Windows desktop for a single scientist) running a Python kernel, a TypeScript harness and two user interfaces. It reaches outward only to public archives, the VO registry, the historical record, Zenodo and the metrics stack, and every one of those calls goes through an adapter behind a kernel port.

```mermaid
flowchart TB
    researcher(["Researcher<br/>[Person]<br/>designs and runs experiments and investigations"])
    reviewer(["Reviewer<br/>[Person]<br/>approves, freezes and signs steps"])
    ci(["CI<br/>[GitHub Actions]<br/>runs every gate unattended"])

    subgraph boundary ["SDP · Scientific Discovery Platform"]
        sdp["SDP<br/>[Software system]<br/>preregistered experiments, investigations,<br/>evidence manifests and verdicts"]
    end

    gaia[/"ESA Gaia Archive<br/>[TAP/ADQL]"/]
    vizier[/"CDS VizieR, X-Match, SIMBAD<br/>[TAP, HTTP]"/]
    mast[/"MAST and IRSA<br/>[TAP, ObsCore, DataLink, file download]"/]
    dasch[/"DASCH plate archive<br/>[HTTP]"/]
    registry[/"IVOA registry<br/>[RegTAP, VOSI]"/]
    hist[/"Historical record<br/>[PDF by DOI]"/]
    zenodo[/"Zenodo<br/>[REST]"/]
    grafana[/"Prometheus and Grafana<br/>[metrics]"/]

    researcher -- "CLI, notebooks, evidence UI, desktop app" --> sdp
    reviewer -- "evidence UI; signs with sdp CLI" --> sdp
    ci -- "tiers T0–T11, harness gates" --> sdp
    sdp -- "astrometry, photometry" --> gaia
    sdp -- "catalogues, cross-match, identifiers" --> vizier
    sdp -- "observations, JWST products, 2MASS, PS1" --> mast
    sdp -- "plate light curves" --> dasch
    sdp -- "source capabilities" --> registry
    sdp -- "transcriptions of 1919 tables" --> hist
    sdp -- "bundles, RO-Crate" --> zenodo
    sdp -- "run metrics, GPU telemetry" --> grafana

    style researcher fill:#08427b,color:#fff,stroke:#073b6f
    style reviewer fill:#08427b,color:#fff,stroke:#073b6f
    style ci fill:#08427b,color:#fff,stroke:#073b6f
    style sdp fill:#1168bd,color:#fff,stroke:#0e5aa7
    style gaia fill:#999,color:#fff,stroke:#888
    style vizier fill:#999,color:#fff,stroke:#888
    style mast fill:#999,color:#fff,stroke:#888
    style dasch fill:#999,color:#fff,stroke:#888
    style registry fill:#999,color:#fff,stroke:#888
    style hist fill:#999,color:#fff,stroke:#888
    style zenodo fill:#999,color:#fff,stroke:#888
    style grafana fill:#999,color:#fff,stroke:#888
    style boundary fill:none,stroke:#1168bd,stroke-width:2px,stroke-dasharray:5
```

| External system | Protocol | Adapter (in `sdp.adapters`) | How tests reach it |
|---|---|---|---|
| ESA Gaia Archive | TAP/ADQL | `archives.tap` with the `gaia-dr3` dialect | Cassettes through `CassetteTransport`; weekly live smoke (T11) |
| CDS VizieR, X-Match, SIMBAD | TAP, HTTP | `archives.tap`, `archives.xmatch`, `archives.simbad` | Cassettes; weekly live smoke |
| MAST and IRSA | TAP, ObsCore, DataLink, HTTP | `archives.mast`, `archives.mast_inventory`, `products.jwst` | Cassettes and reference recordings |
| DASCH | HTTP | `archives.dasch` | Cassettes |
| IVOA registry | RegTAP, VOSI | `archives.registry`, `archives.vo`, `archives.capabilities` | Cassettes |
| Historical record | PDF by DOI | `historical.filesystem` | Fixture files; transcriptions are human-verified |
| Zenodo | REST | `publication.zenodo` | Recorded exchanges; sandbox publish by hand |
| Prometheus and Grafana | metrics | `telemetry.prometheus` | Integration tier |

Every remote call goes through the archive `Transport` (ADR-015), so a test swaps the network for a cassette at one seam and nothing else.
