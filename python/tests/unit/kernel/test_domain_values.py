from __future__ import annotations

from datetime import UTC, datetime

import astropy.units as u
import numpy as np
import pytest

from sdp.kernel.domain.archive import Box, CatalogueQuery, Cone, Filter, idempotency_key
from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.dataset import AcquisitionRecord, DatasetArtifact, DatasetStatus, DialectRef
from sdp.kernel.domain.errors import CurationBlocked, IntegrityError, InvariantViolation, PoolingNotJustified
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.ids import ArtifactId, ProtocolId
from sdp.kernel.domain.policy import BITWISE, NumericalPolicy
from sdp.kernel.domain.pooling import PoolingJustification, require_pooling_justification
from sdp.kernel.domain.predeclaration import Predeclaration, Target
from sdp.kernel.domain.quantity import PhysicalQuantity
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.testing.synthetic import SyntheticLinearProtocol

pytestmark = pytest.mark.tier("T1")
AT = datetime(2026, 10, 28, tzinfo=UTC)


def test_artifact_ids_are_sha256() -> None:
    assert ArtifactId.of_bytes(b"x").value == sha256_id(b"x")
    with pytest.raises(InvariantViolation):
        ArtifactId("md5:abc")


def test_protocol_ids_are_slug_and_semver() -> None:
    assert str(ProtocolId("uc02-einstein-1919", "1.0.0")) == "uc02-einstein-1919@1.0.0"
    with pytest.raises(InvariantViolation):
        ProtocolId("UC02", "1.0")


def test_archive_sources_need_a_release_and_documents_a_doi() -> None:
    with pytest.raises(InvariantViolation):
        SourceRef(SourceKind.ARCHIVE, "gaia", "CC", "cite")
    with pytest.raises(InvariantViolation):
        SourceRef(SourceKind.DOCUMENT, "paper", "PD", "cite")
    assert SourceRef(SourceKind.DOCUMENT, "Harvey 1979", "fair use", "Obs 99 195", bibcode="1979Obs....99..195H")


def test_physical_quantities_are_dimensionless_only_when_declared() -> None:
    with pytest.raises(InvariantViolation):
        PhysicalQuantity(3.0 * u.dimensionless_unscaled)
    assert PhysicalQuantity(3.0 * u.dimensionless_unscaled, dimensionless=True).to_document()["value"] == 3.0
    with pytest.raises(InvariantViolation):
        PhysicalQuantity(3.0)


def test_numerical_policies_compare_as_declared() -> None:
    assert NumericalPolicy(rtol=1e-6).agrees(1.0, 1.0 + 1e-7)
    assert not NumericalPolicy(rtol=1e-9).agrees(1.0, 1.0 + 1e-7)
    assert BITWISE.agrees([1.0, 2.0], [1.0, 2.0]) and not BITWISE.agrees([1.0], [float(np.nextafter(1.0, 2.0))])
    with pytest.raises(InvariantViolation):
        NumericalPolicy(rtol=1e-3, determinism=BITWISE.determinism)


@pytest.mark.req("KRN-001")
def test_editing_a_hypothesis_creates_a_new_version() -> None:
    h1 = Hypothesis("H", 1, "first")
    h2 = h1.revise(statement="second")
    assert (h1.version, h1.statement) == (1, "first")
    assert (h2.version, h2.statement, h2.supersedes) == (2, "second", 1)


@pytest.mark.req("STL-001")
def test_targets_must_record_a_reason() -> None:
    with pytest.raises(InvariantViolation):
        Target("pleiades", "region", "   ", {})


def test_gate_oracles_need_a_predeclared_tolerance() -> None:
    doc = SyntheticLinearProtocol().predeclare().to_document()
    doc["oracle_tolerances"] = []
    with pytest.raises(InvariantViolation):
        Predeclaration.from_document(doc)


def test_predeclaration_round_trips_and_hash_is_stable() -> None:
    p = SyntheticLinearProtocol().predeclare()
    assert Predeclaration.from_document(p.to_document()) == p
    assert p.freeze().hash == p.compute_hash()


@pytest.mark.req("REL-004")
def test_pooling_requires_a_predeclared_justification() -> None:
    with pytest.raises(PoolingNotJustified):
        require_pooling_justification(["a", "b"], None)
    j = PoolingJustification(("a", "b"), "same instrument, same night", predeclared=False)
    with pytest.raises(PoolingNotJustified):
        require_pooling_justification(["a", "b"], j)
    ok = PoolingJustification(("a", "b", "c"), "same instrument", predeclared=True)
    assert require_pooling_justification(["a", "b"], ok) is ok


SOURCE = SourceRef(SourceKind.ARCHIVE, "gaia", "CC BY-SA 3.0 IGO", "Gaia DR3", release_label="gaia:DR3")


def _record(checksum: ArtifactId) -> AcquisitionRecord:
    return AcquisitionRecord("SELECT", "sha256:" + "1" * 64, "gaia:DR3", AT, checksum, 3)


def test_artifact_with_checksum_mismatch_is_rejected_never_curated() -> None:
    actual = ArtifactId.of_bytes(b"abc")
    rejected = DatasetArtifact.acquired_raw(
        actual=actual, media_type="x", byte_count=3, source_ref=SOURCE, acquisition=_record(ArtifactId.of_bytes(b"abd"))
    )
    assert rejected.status is DatasetStatus.REJECTED
    with pytest.raises(IntegrityError):
        rejected.curate(
            curated_id=ArtifactId.of_bytes(b"c"), byte_count=1, dialect=DialectRef("d", "1.0.0"), media_type="x"
        )


@pytest.mark.req("DSI-006")
def test_source_without_licence_cannot_be_curated() -> None:
    unlicensed = SourceRef(SourceKind.ARCHIVE, "gaia", " ", "Gaia DR3", release_label="gaia:DR3")
    actual = ArtifactId.of_bytes(b"abc")
    raw = DatasetArtifact.acquired_raw(
        actual=actual, media_type="x", byte_count=3, source_ref=unlicensed, acquisition=_record(actual)
    )
    with pytest.raises(CurationBlocked):
        raw.curate(curated_id=ArtifactId.of_bytes(b"c"), byte_count=1, dialect=DialectRef("d", "1.0.0"), media_type="x")


@pytest.mark.req("DSI-005")
def test_curated_artifacts_cite_dialect_and_parent() -> None:
    actual = ArtifactId.of_bytes(b"abc")
    raw = DatasetArtifact.acquired_raw(
        actual=actual, media_type="x", byte_count=3, source_ref=SOURCE, acquisition=_record(actual)
    )
    curated = raw.curate(
        curated_id=ArtifactId.of_bytes(b"c"), byte_count=1, dialect=DialectRef("gaia-dr3", "1.0.0"), media_type="x"
    )
    assert curated.parents == (raw.artifact_id,) and curated.dialect == DialectRef("gaia-dr3", "1.0.0")
    assert DatasetArtifact.from_document(curated.to_document()) == curated


def test_queries_are_bounded() -> None:
    with pytest.raises(InvariantViolation):
        Cone(10, 10, 45)
    with pytest.raises(InvariantViolation):
        Box(0, 50, 0, 50)
    with pytest.raises(InvariantViolation):
        CatalogueQuery("gaia", "gaia:DR3", Cone(10, 10, 1), ())
    with pytest.raises(InvariantViolation):
        Filter("ra; DROP TABLE", "<", 1)


def test_idempotency_key_depends_on_text_release_and_columns() -> None:
    k = idempotency_key("SELECT a", "gaia:DR3", ("a",))
    assert k != idempotency_key("SELECT a", "gaia:DR4", ("a",))
    assert k != idempotency_key("SELECT a", "gaia:DR3", ("a", "b"))
    assert k == idempotency_key("SELECT a", "gaia:DR3", ("a",))
