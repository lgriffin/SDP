# ADR-022: Mission-aware MAST adapter with JWST as the first proven mission

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: Integration Architecture v0.4.0 §11 (`docs/integration.md`)

## Context and problem statement

JWST is the first acceptance case but not the only MAST mission SDP needs.

## Decision outcome

One adapter parameterised by mission, discovery through MAST's ObsTAP, product inventory through MAST's product listing, dialects per mission; capability claims per mission and product type.

## Considered alternatives

- **A JWST-only adapter.** Rejected: HST, TESS and Kepler would each need their own.

## Consequences

- JWST conformance says nothing about other missions until theirs passes.
- The fixture suite grows per mission.
