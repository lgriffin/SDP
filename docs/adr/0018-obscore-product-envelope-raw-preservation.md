# ADR-018: ObsCore-shaped product envelope with raw preservation

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: Integration Architecture v0.4.0 §11 (`docs/integration.md`)

## Context and problem statement

Rows and products need different models.

## Decision outcome

`ScientificDataProduct` carries an ObsCore 1.1-shaped envelope filled by per-mission dialects; raw bytes are the artifact, and curation never rewrites them.

## Considered alternatives

- **Flatten products into catalogue rows.** Rejected: loses structure and uncertainty.
- **A bespoke envelope.** Rejected: no interoperability with MAST's own ObsTAP.

## Consequences

- Dialects per mission and release.
- A semantic conformance tier (T5s) to prove the mapping.
