# ADR-009: PROV-aligned evidence graph and RO-Crate bundles

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Ad-hoc manifests are unreadable by anyone else.

## Decision outcome

The evidence graph uses W3C PROV concepts (entity, activity, agent) with PROV-JSON export; publication bundles are RO-Crate with `ro-crate-metadata.json`. Both are produced from Phase 1 (decision 5, §19).

## Considered alternatives

- **Custom JSON.** Rejected: no tooling.
- **Full OpenLineage.** Rejected: pipeline-centred, heavy.

## Consequences

- Zenodo and FAIR tooling read the bundles.
- A small vocabulary mapping is maintained.
