"""The domain: value objects, aggregates, events and invariants. Imports nothing above it (§4)."""

from sdp.kernel.domain.canonical import canonical_json, hash_document, sha256_id
from sdp.kernel.domain.dataset import (
    AcquisitionPart,
    AcquisitionRecord,
    ArtifactKind,
    DatasetArtifact,
    DatasetStatus,
    DialectRef,
)
from sdp.kernel.domain.dossier import Dossier, Figure
from sdp.kernel.domain.errors import (
    CurationBlocked,
    DomainError,
    ImmutableArtifact,
    IntegrityError,
    InvalidTransition,
    InvariantViolation,
    PlanInvalid,
    PoolingNotJustified,
    PredeclarationNotFrozen,
    ProbableErrorNotConverted,
    StorageExhausted,
    VerdictInconsistent,
)
from sdp.kernel.domain.experiment import (
    EnvironmentRecord,
    Experiment,
    ExperimentDefinition,
    ExperimentState,
    InputBinding,
    TaskRecord,
)
from sdp.kernel.domain.findings import Finding, Findings, OracleStatus
from sdp.kernel.domain.gates import (
    Check,
    CheckOutcome,
    Comparator,
    Gate,
    Severity,
    evaluate_gate,
    evaluate_gates,
)
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ArtifactId, ProtocolId
from sdp.kernel.domain.measurement import (
    PROBABLE_ERROR_TO_STANDARD,
    Measurement,
    UncertaintyConvention,
    least_squares_weight,
)
from sdp.kernel.domain.policy import BITWISE, DeterminismClass, NumericalPolicy, SeedHandling
from sdp.kernel.domain.pooling import PoolingJustification, require_pooling_justification
from sdp.kernel.domain.predeclaration import FrozenPredeclaration, OracleTolerance, Predeclaration, Target
from sdp.kernel.domain.prediction import Prediction
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.quantity import PhysicalQuantity
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.domain.verdict import ValidationReport, Verdict, VerdictStatus, decide_verdict

__all__ = [
    "BITWISE",
    "PROBABLE_ERROR_TO_STANDARD",
    "AcquisitionPart",
    "AcquisitionRecord",
    "ArtifactId",
    "ArtifactKind",
    "Check",
    "CheckOutcome",
    "Comparator",
    "CurationBlocked",
    "DatasetArtifact",
    "DatasetStatus",
    "DeterminismClass",
    "DialectRef",
    "DomainError",
    "Dossier",
    "EnvironmentRecord",
    "Experiment",
    "ExperimentDefinition",
    "ExperimentState",
    "Figure",
    "Finding",
    "Findings",
    "FrozenPredeclaration",
    "Gate",
    "Hypothesis",
    "ImmutableArtifact",
    "InputBinding",
    "IntegrityError",
    "InvalidTransition",
    "InvariantViolation",
    "Measurement",
    "NumericalPolicy",
    "OracleStatus",
    "OracleTolerance",
    "PhysicalQuantity",
    "PlanInvalid",
    "PoolingJustification",
    "PoolingNotJustified",
    "Predeclaration",
    "PredeclarationNotFrozen",
    "Prediction",
    "ProbableErrorNotConverted",
    "ProtocolId",
    "ProvGraph",
    "SeedHandling",
    "Severity",
    "SourceKind",
    "SourceRef",
    "StorageExhausted",
    "Target",
    "TaskRecord",
    "UncertaintyConvention",
    "ValidationReport",
    "Verdict",
    "VerdictInconsistent",
    "VerdictStatus",
    "canonical_json",
    "decide_verdict",
    "evaluate_gate",
    "evaluate_gates",
    "hash_document",
    "least_squares_weight",
    "require_pooling_justification",
    "sha256_id",
]
