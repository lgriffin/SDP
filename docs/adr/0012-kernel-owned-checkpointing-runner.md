# ADR-012: Kernel-owned checkpointing runner; Prefect as optional driving adapter

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Single host, bounded jobs, solo builder.

## Decision outcome

The application layer owns a runner with checkpoints and resume; Prefect is added as a driving adapter only when fan-out across regions needs it (decision 3, §19: through Phase 4).

## Considered alternatives

- **Prefect from day one.** Rejected: another service to keep alive for the Einstein slice.

## Consequences

- Resume semantics are tested in the kernel, not delegated.
