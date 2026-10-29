# SDP — Scientific Discovery Platform

A polyglot hexagonal platform for reproducible, preregistered science: a Python scientific kernel with pluggable science modules, a TypeScript specification-and-conformance harness, and a contract between them that is itself under test. The architecture is specified in [docs/architecture.md](docs/architecture.md) (v0.3.0, decisions accepted 28 October 2026).

Two programmes run on the kernel:

- **UC-02, Einstein 1919** (`sdp_relativity`): a re-examination of the eclipse light-deflection measurements, and the validation vertical slice.
- **UC-01, stellar clusters** (`sdp_stellar`): open-cluster recovery and candidate search in Gaia DR3, built on a release-aware data-source integration layer.

## Repository layout

| Path | What lives there |
|---|---|
| `contracts/` | JSON Schemas, GraphQL schema, golden fixtures (§7) |
| `requirements/` | `.ears` requirement files (§12) |
| `ledger/` | Signed predeclaration and approval entries (§15) |
| `docs/` | Architecture, ADRs, C4 views, protocols, spike report |
| `python/` | uv workspace: kernel, contracts, adapters, science modules, CLI, API |
| `typescript/` | pnpm workspace: generated contracts, spec harness, evidence UI, e2e, and the Windows desktop app (`typescript/apps/sdp-desktop`) |
| `experiments/` | Predeclarations and run manifests by protocol |
| `infrastructure/` | Quadlet units, developer compose stack, Grafana |

## Developer loop

```bash
just test       # stages 1-2 of §11.7 with fakes
just gate       # merge stage against real PostgreSQL
just reproduce  # clean-room reproduction of a bundle
```

See [docs/implementation-status.md](docs/implementation-status.md) for what is built, what is waiting on hardware or on human transcription, and how the code maps to each section of the specification.
