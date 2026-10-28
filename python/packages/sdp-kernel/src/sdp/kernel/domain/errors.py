"""Domain errors. Each one names the invariant it protects."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every rule the domain refuses to break."""


class InvariantViolation(DomainError):
    """A value object or aggregate was asked to hold an impossible state."""


class InvalidTransition(DomainError):
    """An aggregate was asked to move between lifecycle states that are not connected."""


class ProbableErrorNotConverted(DomainError):
    """A probable error reached a least-squares weight without to_standard_error() (REL-002)."""


class PredeclarationNotFrozen(DomainError):
    """An experiment referenced a predeclaration hash absent from the ledger (KRN-003)."""


class PlanInvalid(DomainError):
    """A ComputePlan is cyclic, dangling, or names a TaskKind without a CPU reference (KRN-012)."""


class IntegrityError(DomainError):
    """Bytes did not hash to the identity they were stored under (NFR-003, DSI-003)."""


class ImmutableArtifact(DomainError):
    """An application workflow tried to modify a raw artifact (KRN-014)."""


class PoolingNotJustified(DomainError):
    """Observation sets were pooled without a PoolingJustification (REL-004)."""


class CurationBlocked(DomainError):
    """A source without a licence cannot be curated (DSI-006)."""


class StorageExhausted(DomainError):
    """Free space is below the configured threshold; new acquisitions are refused (DSI-010)."""


class VerdictInconsistent(DomainError):
    """A science module's validation report disagrees with the gate outcomes the kernel derives."""


class PromotionRefused(DomainError):
    """An investigation was promoted without a frozen confirmatory predeclaration naming
    evidence it did not use (INV-009)."""
