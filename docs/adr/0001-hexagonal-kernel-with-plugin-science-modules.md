# ADR-001: Hexagonal kernel with plugin science modules

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

SDD 0.2.0 drew the core depending on the two science domains, which is a hidden inward dependency: every new programme would be a kernel change.

## Decision outcome

One kernel hexagon. Science modules implement `ScientificProtocol` and are loaded through the `sdp.protocols` entry-point group; the kernel never imports them.

## Considered alternatives

- **Science inside the kernel.** Rejected: every new programme is a kernel change.
- **One hexagon per science domain with duplicated evidence logic.** Rejected: evidence rules diverge.

## Consequences

- A third programme is a package, not a kernel change.
- The plugin contract becomes a versioned public API that needs its own contract tests (`sdp.testing.contracts.scientific_protocol`).
