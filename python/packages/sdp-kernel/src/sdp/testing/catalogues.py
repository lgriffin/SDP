"""A small synthetic catalogue and its descriptor, shared by contract and use-case tests."""

from __future__ import annotations

import pyarrow as pa

from sdp.kernel.domain.archive import ColumnDescriptor, SourceDescriptor

FAKE_GAIA = SourceDescriptor(
    source_id="fake-gaia",
    release_label="gaia:DR3",
    table="gaiadr3.gaia_source",
    columns=(
        ColumnDescriptor("source_id", "long"),
        ColumnDescriptor("ra", "double", "deg"),
        ColumnDescriptor("dec", "double", "deg"),
        ColumnDescriptor("phot_g_mean_mag", "double", "mag"),
    ),
    licence="CC BY-SA 3.0 IGO",
    citation="Gaia Collaboration 2023",
    max_rows_per_call=50,
    min_seconds_between_calls=0.0,
    dialect_id="gaia-dr3",
)


def fixture_catalogue(n: int = 120) -> pa.Table:
    """A synthetic strip of stars near the Pleiades centre; not real Gaia data."""
    return pa.table(
        {
            "source_id": list(range(n)),
            "ra": [56.0 + 0.01 * i for i in range(n)],
            "dec": [24.0 + 0.005 * i for i in range(n)],
            "phot_g_mean_mag": [10.0 + 0.05 * i for i in range(n)],
        }
    )
