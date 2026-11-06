"""T3: Gaia DR3 orbits with Gaia's masses from the Gaia TAP service (DSI-023). The service is a primed
fake answering invented orbits, plus one recording of real orbits made live on 2026-11-04."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from sdp.adapters.archives import gaia_nss as nss
from sdp.adapters.archives.transport import CassetteMissing, CassetteTransport, TransportError
from sdp.adapters.archives.vo import sync_request
from sdp_stellar import companions as c

from .vo_fakes import AsyncTapService, PrimedService

pytestmark = pytest.mark.tier("T3")
CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "gaia-nss"
#: the stars of the recording: Gaia BH1, two AMRF class III orbits, and four ordinary or unflagged ones
RECORDED = [
    4373465352415301632,
    6281177228434199296,
    3509370326763016704,
    5706622788776714752,
    498657664552495744,
    423764017715132544,
    3224141071895832448,
]


def rows(ids: list[int], kinds: list[str], methods: list[str], m1: list[float | None]) -> dict[str, object]:
    n = len(ids)
    out: dict[str, object] = {}
    for col in (*nss.ORBIT_COLUMNS, *nss.SOURCE_COLUMNS, *nss.MASS_COLUMNS):
        out[col] = [1.0] * n
    out["source_id"] = pa.array(ids, type=pa.int64())
    out["nss_solution_type"] = kinds
    out["combination_method"] = methods
    out["flag"] = [""] * n
    out["m1"] = pa.array(m1, type=pa.float64())
    return out


@pytest.mark.req("DSI-023")
def test_the_source_id_ranges_cover_every_id_once() -> None:
    spans = nss.ranges(5)
    assert spans[0][0] == 0 and spans[-1][1] == nss.SOURCE_ID_MAX
    assert all(spans[i][1] + 1 == spans[i + 1][0] for i in range(len(spans) - 1))


@pytest.mark.req("DSI-023")
def test_every_range_is_asked_for_and_one_mass_row_is_kept_per_orbit() -> None:
    service = PrimedService()
    spans = nss.ranges(2)
    service.table(
        sync_request(nss.GAIA_TAP, nss.orbit_query(*spans[0]), nss.MAXREC),
        rows([5, 5, 6], ["Orbital", "Orbital", "AstroSpectroSB1"], ["SB2+M1", "Orbital+M1", ""], [0.8, 0.9, None]),
    )
    service.table(
        sync_request(nss.GAIA_TAP, nss.orbit_query(*spans[1]), nss.MAXREC),
        rows([2**62 + 1], ["Orbital"], ["Orbital+M1"], [1.1]),
    )
    table = nss.fetch_orbits(service, parts=2, mode="sync")
    assert service.calls == 2
    assert table.column("source_id").to_pylist() == [5, 6, 2**62 + 1]
    assert table.column("combination_method").to_pylist() == ["Orbital+M1", "", "Orbital+M1"]
    meta = table.schema.metadata or {}
    assert meta[b"sdp.release"] == b"gaia:DR3" and b"binary_masses" in meta[b"sdp.query"]


@pytest.mark.req("DSI-023")
def test_an_answer_without_a_declared_column_is_refused() -> None:
    service = PrimedService()
    bad = rows([5], ["Orbital"], ["Orbital+M1"], [1.0])
    del bad["fluxratio_upper"]
    service.table(sync_request(nss.GAIA_TAP, nss.targets_query([5]), nss.MAXREC), bad)
    with pytest.raises(ValueError, match="fluxratio_upper"):
        nss.fetch_targets(service, [5])


@pytest.mark.req("DSI-023")
def test_no_targets_is_a_named_failure() -> None:
    with pytest.raises(ValueError, match="no source_ids"):
        nss.targets_query([])


@pytest.mark.req("DSI-023")
def test_the_recorded_orbits_flag_gaia_bh1_as_a_neutron_star_or_black_hole() -> None:
    table = nss.fetch_targets(CassetteTransport(CASSETTES), RECORDED)
    assert sorted(table.column("source_id").to_pylist()) == sorted(RECORDED)
    o = {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}
    masses, verdict = c.judge(o, c.load_settings())
    by_id = dict(
        zip(o["source_id"].tolist(), zip(verdict.tolist(), masses["m2_msun"].tolist(), strict=True), strict=True)
    )
    v, m2 = by_id[4373465352415301632]
    assert v == c.COMPACT
    # SDP's own Kepler arithmetic gives back Gaia's dark-partner mass for BH1
    assert m2 == pytest.approx(float(o["m2"][o["source_id"].tolist().index(4373465352415301632)]), rel=1e-4)
    assert by_id[5706622788776714752][0] == c.ORDINARY


@pytest.mark.req("DSI-023")
def test_a_download_that_stops_part_way_resumes_from_the_saved_parts(tmp_path: Path) -> None:
    spans = nss.ranges(2)
    first = PrimedService()
    first.table(
        sync_request(nss.GAIA_TAP, nss.orbit_query(*spans[0]), nss.MAXREC),
        rows([5], ["Orbital"], ["Orbital+M1"], [0.8]),
    )
    with pytest.raises(CassetteMissing):  # the second range is not primed, as if the server stopped answering
        nss.fetch_orbits(first, parts=2, parts_dir=tmp_path, mode="sync")
    assert first.calls == 2 and len(list(tmp_path.glob("part-*.parquet"))) == 1

    second = PrimedService()
    second.table(
        sync_request(nss.GAIA_TAP, nss.orbit_query(*spans[1]), nss.MAXREC),
        rows([2**62 + 1], ["Orbital"], ["Orbital+M1"], [1.1]),
    )
    table = nss.fetch_orbits(second, parts=2, parts_dir=tmp_path, mode="sync")
    assert second.calls == 1  # only the range that was missing
    assert table.column("source_id").to_pylist() == [5, 2**62 + 1]


# --- async jobs (#199): the synchronous service timed out on the three-table join ---------------------


@pytest.mark.req("DSI-023")
def test_by_default_each_range_is_an_async_job_polled_until_it_completes() -> None:
    service = AsyncTapService(nss.GAIA_TAP)
    spans = nss.ranges(2)
    service.prime(
        nss.orbit_query(*spans[0]),
        nss.MAXREC,
        rows([5], ["Orbital"], ["Orbital+M1"], [0.8]),
        phases=("QUEUED", "EXECUTING", "COMPLETED"),
        redirect=True,
    )
    service.prime(nss.orbit_query(*spans[1]), nss.MAXREC, rows([2**62 + 1], ["Orbital"], ["Orbital+M1"], [1.1]))
    table = nss.fetch_orbits(service, parts=2, sleep=service.sleep)
    assert table.column("source_id").to_pylist() == [5, 2**62 + 1]
    # the first job (a 303 to its Location) waited twice, the second (the job document's jobId) once
    assert service.sleeps == [5.0, 7.5, 5.0]
    assert f"GET {nss.GAIA_TAP}/async/job1/results/result" in service.sent
    assert f"GET {nss.GAIA_TAP}/async/job2/results/result" in service.sent
    assert not any("/sync" in s for s in service.sent)


@pytest.mark.req("DSI-023")
def test_a_job_the_service_fails_is_a_refusal_with_its_message() -> None:
    service = AsyncTapService(nss.GAIA_TAP)
    service.prime(
        nss.orbit_query(*nss.ranges(1)[0]),
        nss.MAXREC,
        rows([5], ["Orbital"], ["Orbital+M1"], [0.8]),
        phases=("ERROR",),
        error="Cannot parse query",
    )
    with pytest.raises(TransportError, match="ended ERROR: Cannot parse query") as caught:
        nss.fetch_orbits(service, parts=1, sleep=service.sleep)
    assert caught.value.status == 400


@pytest.mark.req("DSI-023")
def test_a_job_that_never_finishes_is_aborted_and_reported_as_a_timeout() -> None:
    from sdp.adapters.archives.vo import run_async

    service = AsyncTapService(nss.GAIA_TAP)
    query = nss.orbit_query(*nss.ranges(1)[0])
    service.prime(query, nss.MAXREC, rows([5], ["Orbital"], ["Orbital+M1"], [0.8]), phases=("EXECUTING",))
    clock = iter(range(0, 10_000, 100))
    with pytest.raises(TransportError, match="no result after 300 s") as caught:
        run_async(
            service,
            nss.GAIA_TAP,
            query,
            nss.MAXREC,
            "orbits",
            max_wait_s=300.0,
            sleep=service.sleep,
            monotonic=lambda: float(next(clock)),
        )
    assert caught.value.status == 408
    assert f"POST {nss.GAIA_TAP}/async/job1/phase" in service.sent
