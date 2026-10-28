"""T3: dialects map native rows to canonical astrometry exactly as their golden rows say (§8, DSI-005)."""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa
import pytest

from sdp.adapters.archives.dialect import (
    CANONICAL_COLUMNS,
    Dialect,
    DialectError,
    YamlDialectCurator,
    load_dialects,
    load_golden,
)
from sdp.kernel.domain.dataset import DialectRef
from sdp.testing.contracts.misc import DialectCuratorContract

pytestmark = pytest.mark.tier("T3")

DIALECTS = load_dialects()


def native_table(dialect_id: str) -> pa.Table:
    return pa.Table.from_pylist(load_golden(dialect_id)["native"])


@pytest.mark.parametrize("dialect_id", sorted(DIALECTS))
def test_golden_rows_reproduce_exactly(dialect_id: str) -> None:
    golden = load_golden(dialect_id)
    curated = DIALECTS[dialect_id].curate(native_table(dialect_id)).to_pylist()
    for row in curated:
        row["quality_flags"] = json.loads(row["quality_flags"])
    assert _close(curated, golden["canonical"])


def _close(got: list[dict[str, Any]], want: list[dict[str, Any]]) -> bool:
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        assert list(g) == list(CANONICAL_COLUMNS)
        for key in CANONICAL_COLUMNS:
            if isinstance(w[key], float):
                assert g[key] == pytest.approx(w[key], rel=1e-12, abs=1e-12), key
            else:
                assert g[key] == w[key], key
    return True


class TestYamlDialectCurator(DialectCuratorContract):
    @pytest.fixture(params=sorted(DIALECTS))
    def curator_case(self, request: pytest.FixtureRequest) -> tuple[Any, ...]:
        d = DIALECTS[request.param]
        return (
            YamlDialectCurator.packaged(),
            d.source_id,
            d.source_release,
            native_table(request.param),
            list(CANONICAL_COLUMNS),
        )


@pytest.mark.req("DSI-001")
def test_a_release_without_a_dialect_cannot_be_curated() -> None:
    with pytest.raises(DialectError, match="no dialect declared for gaia gaia:DR4"):
        YamlDialectCurator.packaged().dialect_for("gaia", "gaia:DR4")


@pytest.mark.req("DSI-005")
def test_curation_refuses_a_dialect_version_it_did_not_load() -> None:
    with pytest.raises(DialectError, match="is not loaded"):
        YamlDialectCurator.packaged().curate(native_table("gaia-dr3"), DialectRef("gaia-dr3", "9.9.9"))


def test_a_native_table_missing_mapped_columns_is_refused() -> None:
    with pytest.raises(DialectError, match="lacks"):
        DIALECTS["gaia-dr3"].curate(native_table("gaia-dr3").drop_columns(["pmra"]))


def base_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "dialect_id": "x",
        "dialect_version": "1.0.0",
        "source_id": "x",
        "source_release": "x:1",
        "source_key": "x:{id}",
        "epoch": {"constant": 2000.0},
        "columns": {"ra_deg": {"column": "ra"}, "dec_deg": {"column": "dec"}},
    }
    doc.update(overrides)
    return doc


def test_units_convert_through_astropy_and_incompatible_units_are_refused() -> None:
    d = Dialect.from_document(
        base_doc(
            columns={
                "ra_deg": {"column": "ra", "unit": "hourangle"},
                "dec_deg": {"column": "dec"},
                "parallax_mas": {"column": "plx", "unit": "arcsec"},
            }
        )
    )
    row = d.row({"id": 1, "ra": 3.0, "dec": 10.0, "plx": 0.002})
    assert row["ra_deg"] == pytest.approx(45.0) and row["parallax_mas"] == pytest.approx(2.0)
    with pytest.raises(DialectError, match="cannot convert"):
        Dialect.from_document(base_doc(columns={"ra_deg": {"column": "ra", "unit": "mag"}}))


def test_a_proper_motion_without_cos_dec_is_converted_to_the_canonical_convention() -> None:
    d = Dialect.from_document(
        base_doc(
            columns={
                "ra_deg": {"column": "ra"},
                "dec_deg": {"column": "dec"},
                "pm_ra_cosdec_masyr": {"column": "pmra"},
            },
            pm_ra_needs_cos_dec=True,
        )
    )
    assert d.row({"id": 1, "ra": 0.0, "dec": 60.0, "pmra": 10.0})["pm_ra_cosdec_masyr"] == pytest.approx(5.0)


def test_the_parallax_zero_point_is_a_recorded_flag_never_applied() -> None:
    row = DIALECTS["gaia-dr3"].curate(native_table("gaia-dr3")).to_pylist()[0]
    assert row["zero_point_corrected"] is False
    assert row["parallax_mas"] == load_golden("gaia-dr3")["native"][0]["parallax"]
