"""T3: column-at-a-time curation returns exactly what row-by-row curation returns (DSI-016)."""

from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pytest

from sdp.adapters.archives.dialect import Dialect, DialectError, curate_columnar, load_dialects, load_golden

from .conftest import gaia_native

pytestmark = pytest.mark.tier("T3")

DIALECTS = sorted(load_dialects())


def native_golden(dialect_id: str) -> pa.Table:
    return pa.Table.from_pylist(load_golden(dialect_id)["native"])


@pytest.mark.req("DSI-016")
@pytest.mark.parametrize("dialect_id", DIALECTS)
def test_every_packaged_dialect_curates_its_golden_rows_identically(dialect_id: str) -> None:
    dialect = load_dialects()[dialect_id]
    native = native_golden(dialect_id)
    assert curate_columnar(dialect, native).equals(dialect.curate(native), check_metadata=True)


@pytest.mark.req("DSI-016")
def test_gaia_rows_with_nulls_curate_identically_and_faster() -> None:
    dialect = load_dialects()["gaia-dr3"]
    native = gaia_native(400)
    rng = np.random.default_rng(5)
    for name in ("parallax", "pmra", "bp_rp", "ra_dec_corr", "ruwe"):
        values = native.column(name).to_pylist()
        for i in rng.choice(len(values), 40, replace=False):
            values[int(i)] = None
        native = native.set_column(
            native.schema.get_field_index(name), name, pa.array(values, native.schema.field(name).type)
        )
    # one row with every correlation null, which curates to a null list
    corr = load_dialects()["gaia-dr3"].correlations or ()
    for name in corr:
        values = native.column(name).to_pylist()
        values[0] = None
        native = native.set_column(
            native.schema.get_field_index(name), name, pa.array(values, native.schema.field(name).type)
        )
    started = time.perf_counter()
    rows = dialect.curate(native)
    by_rows = time.perf_counter() - started
    started = time.perf_counter()
    columns = curate_columnar(dialect, native)
    by_columns = time.perf_counter() - started
    assert columns.equals(rows, check_metadata=True)
    assert columns.column("astrometric_corr")[0].as_py() is None
    assert by_columns < by_rows


def _variant(base: Dialect, **changes: object) -> Dialect:
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


def test_unit_conversion_cos_dec_epoch_kinds_and_mapped_quality_match_the_row_path() -> None:
    gaia = load_dialects()["gaia-dr3"]
    native = gaia_native(30)
    columns = {**gaia.columns, "parallax_mas": {"column": "parallax", "unit": "arcsec"}}
    for epoch in ({"column": "ref_epoch", "kind": "jd"}, {"column": "ref_epoch", "kind": "mjd"}, {"constant": 2015.5}):
        for quality in ({"column": "astrometric_params_solved", "map": {"31": "good"}, "default": "odd"}, {}):
            dialect = _variant(
                gaia, columns=columns, pm_ra_needs_cos_dec=True, epoch=epoch, quality_class=quality, correlations=None
            )
            assert curate_columnar(dialect, native).equals(dialect.curate(native), check_metadata=True)


def test_rows_without_an_epoch_are_excluded_and_counted_as_the_row_path_does() -> None:
    gaia = load_dialects()["gaia-dr3"]
    native = gaia_native(20)
    epochs = native.column("ref_epoch").to_pylist()
    epochs[3], epochs[7] = None, -999.0
    native = native.set_column(native.schema.get_field_index("ref_epoch"), "ref_epoch", pa.array(epochs, pa.float64()))
    dialect = _variant(
        gaia, epoch={"column": "ref_epoch", "kind": "jyear", "missing": [-999.0], "exclude_rows_without_epoch": True}
    )
    columns = curate_columnar(dialect, native)
    assert columns.num_rows == 18
    assert columns.equals(dialect.curate(native), check_metadata=True)
    strict = _variant(gaia, epoch={"column": "ref_epoch", "kind": "jyear", "missing": [-999.0]})
    with pytest.raises(DialectError, match=r"^gaia-dr3: row without an epoch$"):
        curate_columnar(strict, native)
    unknown = _variant(gaia, epoch={"column": "ref_epoch", "kind": "besselian"})
    with pytest.raises(DialectError, match=r"^gaia-dr3: unknown epoch kind 'besselian'$"):
        curate_columnar(unknown, gaia_native(3))


def test_a_native_table_lacking_a_column_is_refused_by_name() -> None:
    gaia = load_dialects()["gaia-dr3"]
    with pytest.raises(DialectError, match=r"^gaia-dr3: native table lacks \['ruwe'\]$"):
        curate_columnar(gaia, gaia_native(3).drop_columns(["ruwe"]))


def test_an_empty_table_curates_to_an_empty_canonical_table() -> None:
    gaia = load_dialects()["gaia-dr3"]
    empty = gaia_native(3).slice(0, 0)
    assert curate_columnar(gaia, empty).equals(gaia.curate(empty))
