# ADR-007: CPU reference first, GPU as adapter with agreement tests

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

Carried from 0.2.0 ADR-006.

## Decision outcome

Every `TaskKind` has a CPU implementation; CUDA is an adapter admitted to gates only after passing its agreement test in the same release.

## Considered alternatives

- **GPU-first implementations.** Rejected: ARM64 CUDA toolchain risk and nondeterministic reductions would gate science on hardware.

## Consequences

- Nondeterministic GPU reductions are documented, not hidden (NFR-008).
