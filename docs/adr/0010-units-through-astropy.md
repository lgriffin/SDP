# ADR-010: Units through astropy.units at every port

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Unit errors are the classic silent scientific failure.

## Decision outcome

Physical values cross ports only as `Quantity`; constants come from `astropy.constants` with a recorded CODATA version.

## Considered alternatives

- **pint.** Rejected: a second units system beside astropy.

## Consequences

- Some boilerplate.
- Unit tests become dimensional tests.
