# ADR-013: Rootless Podman with Quadlet and CDI on the DGX Spark

- Status: Accepted
- Date: 2026-10-28
- Deciders: Leigh
- Source: architecture specification v0.3.0 §14

## Context and problem statement

0.2.0 asked for OCI containers without root.

## Decision outcome

Rootless Podman, systemd Quadlet units for PostgreSQL, the API and the harness, GPU exposure through CDI.

## Considered alternatives

- **Docker.** Rejected: root daemon by default.
- **Bare metal.** Rejected: no reproducibility.

## Consequences

- A Phase 0 spike must confirm CDI and the CUDA toolchain on DGX OS (`docs/spike/phase0-dgx-spark.md`).
