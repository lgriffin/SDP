"""T4: acquisition through the TAP gateway and curation through a YAML dialect, end to end on the kernel."""

from __future__ import annotations

import io

import pyarrow.parquet as pq
import pytest

from sdp.adapters.archives.dialect import CANONICAL_COLUMNS, YamlDialectCurator
from sdp.adapters.archives.tap import TapArchiveAdapter
from sdp.kernel.ports.driving import AcquireDatasetCommand
from sdp.testing.fakes.archives import apply_query
from sdp.testing.wiring import build_in_memory_kernel

from .conftest import GAIA, FixtureTap, gaia_native, gaia_query

pytestmark = pytest.mark.tier("T4")


@pytest.mark.req("DSI-002")
@pytest.mark.req("DSI-005")
def test_a_gaia_cone_is_acquired_and_curated_to_canonical_astrometry() -> None:
    tap = FixtureTap()
    adapter = TapArchiveAdapter({"gaia": GAIA}, tap)
    tap.prime(adapter, adapter.query(gaia_query()), gaia_native())
    k = build_in_memory_kernel(gateways={"gaia": adapter})
    k.kernel.acquire_dataset.curator = YamlDialectCurator.packaged()
    result = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(gaia_query()))
    assert result.curated is not None
    assert result.raw.source_ref.query_text == adapter.query(gaia_query()).rendered_text
    assert result.raw.source_ref.licence == "CC BY-SA 3.0 IGO"
    assert result.curated.dialect is not None and result.curated.dialect.dialect_id == "gaia-dr3"
    table = pq.read_table(io.BytesIO(k.store.get(result.curated.artifact_id)))
    assert tuple(table.column_names) == CANONICAL_COLUMNS
    assert table.num_rows == apply_query(gaia_native(), gaia_query()).num_rows
    assert set(table.column("source_release").to_pylist()) == {"gaia:DR3"}
    calls = tap.calls
    again = k.kernel.acquire_dataset.execute(AcquireDatasetCommand(gaia_query()))
    assert again.reused and tap.calls == calls
