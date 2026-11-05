"""T1: what the delta engine asks VizieR and Gaia (STL-032), on primed fakes. Not archive data."""

from __future__ import annotations

import pytest
from tests.archives.vo_fakes import PrimedService

from sdp.adapters.archives import gaia_delta as g
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.adapters.archives.vo import sync_request

pytestmark = pytest.mark.tier("T1")
MATCH = {
    "table": "gaiadr3.dr2_neighbourhood",
    "from_column": "dr2_source_id",
    "to_column": "dr3_source_id",
    "order_by": "angular_distance",
}


@pytest.mark.req("STL-032")
def test_the_cone_names_a_gaia_table_and_cuts_on_the_server() -> None:
    q = g.cone_query("gaiadr3.gaia_source", ("source_id", "ra"), 132.85, 11.82, 0.6, 1.0, 1.3, 18.0)
    assert "FROM gaiadr3.gaia_source" in q and "CIRCLE('ICRS', 132.85000000, 11.82000000, 0.60000000)" in q
    assert "parallax BETWEEN 1.000000 AND 1.300000" in q and "phot_g_mean_mag <= 18.000" in q
    for bad in ("gaiadr3.gaia_source; DROP", "gaia_source", "a.b.c"):
        with pytest.raises(ValueError):
            g.cone_query(bad, ("source_id",), 0, 0, 1, 0, 1, 18)


@pytest.mark.req("STL-032")
def test_a_cone_that_reaches_the_row_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "MAXREC", 2)
    service = PrimedService()
    service.table(sync_request(g.GAIA_TAP, "Q", 2), {"source_id": [1, 2]})
    with pytest.raises(UntrustedInput, match="cut off"):
        g.cone(service, "Q", "dr3 test")


@pytest.mark.req("STL-032")
def test_the_nearest_neighbour_wins_and_ids_go_in_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "CHUNK", 2)
    service = PrimedService()
    q1 = "SELECT dr2_source_id, dr3_source_id, angular_distance FROM gaiadr3.dr2_neighbourhood WHERE dr2_source_id IN (1, 2)"
    q2 = "SELECT dr2_source_id, dr3_source_id, angular_distance FROM gaiadr3.dr2_neighbourhood WHERE dr2_source_id IN (3)"
    service.table(
        sync_request(g.GAIA_TAP, q1, 40),
        {"dr2_source_id": [1, 1, 2], "dr3_source_id": [11, 12, 21], "angular_distance": [5.0, 1.0, 0.1]},
    )
    service.table(
        sync_request(g.GAIA_TAP, q2, 40), {"dr2_source_id": [3], "dr3_source_id": [31], "angular_distance": [0.2]}
    )
    assert g.neighbourhood(service, MATCH, [3, 2, 1, 1]) == {1: 12, 2: 21, 3: 31}
    with pytest.raises(ValueError):
        g.neighbourhood(service, {**MATCH, "table": "x; DROP"}, [1])


@pytest.mark.req("STL-032")
def test_a_cluster_name_is_matched_exactly_and_must_be_a_catalogue_name() -> None:
    with pytest.raises(ValueError):
        g.hr23_cluster(PrimedService(), "NGC 2682&-out=*")
