# ADR-017: Capability descriptors read from the Virtual Observatory

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: Integration Architecture v0.4.0 §11 (`docs/integration.md`)

## Context and problem statement

Sources differ in what they offer and the proposal asked SDP to describe it.

## Decision outcome

Descriptors are read from RegTAP, VOSI and ObsCore wherever published; native services get a hand-written descriptor in the same schema, checked by the weekly smoke.

## Considered alternatives

- **SDP-defined descriptors for every source.** Rejected: duplicates what the registry holds and drifts.
- **No descriptors; adapters decide at call time.** Rejected: no planning, no explanation.

## Consequences

- A VO discovery dependency in the adapter layer. In SDP it is spoken over the archive `Transport` so CI replays cassettes (spec-deviations D31).
- Descriptor freshness becomes a monitored quantity.
