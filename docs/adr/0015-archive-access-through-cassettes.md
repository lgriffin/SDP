# ADR-015: Archive access through recorded cassettes in CI

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Live archives are rate-limited and change.

## Decision outcome

Contract tests replay committed cassettes keyed by query hash; one weekly live job re-records and detects schema drift.

## Considered alternatives

- **Live calls in CI.** Rejected: flaky and rate-limited.
- **Hand mocks of archive clients.** Rejected: ADR-006.

## Consequences

- Cassettes are reviewed like code.
- Drift is caught within a week.
