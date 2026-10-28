# ADR-006: Fakes over mocks, contract suites per port

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Mock-heavy tests pass while adapters fail.

## Decision outcome

Each driven port ships an in-memory fake in `sdp.testing`; one contract suite runs against the fake and every real adapter; mocks are lint-forbidden inside the hexagon.

## Considered alternatives

- **Mocks.** Rejected.
- **Only real adapters.** Rejected: slow, infrastructure-bound.

## Consequences

- Fakes are maintained as product code.
- T3 is the largest suite.
