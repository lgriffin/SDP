"""T3: known-cluster catalogues from VizieR become benchmark tables the UC-01 cross-match accepts
(DSI-015). VizieR is a primed fake answering invented clusters; recordings come from a live run."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import pyarrow as pa
import pytest

from sdp.adapters.archives import benchmarks as bm
from sdp.adapters.archives.benchmarks import (
    CATALOGUES,
    MAXREC,
    TAPVIZIER,
    CatalogueSchemaError,
    VizierKnownClusters,
    choose_columns,
)
from sdp.adapters.archives.transport import TransportError
from sdp.adapters.archives.votable import TapQueryError
from sdp_stellar.benchmark import benchmark_from

from .vo_fakes import PrimedService

pytestmark = pytest.mark.tier("T3")

#: TAP_SCHEMA.columns rows shaped like VizieR's for a cluster table; names invented to show the UCD wins
SCHEMA = [
    {"column_name": "Seq", "ucd": "meta.record", "unit": ""},
    {"column_name": "Name", "ucd": "meta.id;meta.main", "unit": ""},
    {"column_name": "RA_ICRS", "ucd": "pos.eq.ra;meta.main", "unit": "deg"},
    {"column_name": "DE_ICRS", "ucd": "pos.eq.dec;meta.main", "unit": "deg"},
    {"column_name": "e_pmRA", "ucd": "stat.error;pos.pm;pos.eq.ra", "unit": "mas/yr"},
    {"column_name": "pmRA*", "ucd": "pos.pm;pos.eq.ra", "unit": "mas/yr"},
    {"column_name": "pmDE", "ucd": "pos.pm;pos.eq.dec", "unit": "mas/yr"},
    {"column_name": "Plx", "ucd": "pos.parallax.trig", "unit": "mas"},
]
CHOSEN = {
    "name": "Name",
    "ra_deg": "RA_ICRS",
    "dec_deg": "DE_ICRS",
    "pm_ra_cosdec_masyr": "pmRA*",
    "pm_dec_masyr": "pmDE",
    "parallax_mas": "Plx",
}
ROWS: dict[str, object] = {
    "name": ["  Fixture_B ", "Fixture_A", "Fixture_C"],
    "ra_deg": [56.75, 130.1, None],
    "dec_deg": [24.12, 19.67, 10.0],
    "pm_ra_cosdec_masyr": [19.9, -36.0, 1.0],
    "pm_dec_masyr": [-45.4, -12.9, 1.0],
    "parallax_mas": [7.36, 5.37, 1.0],
}


def schema_rows(rows: list[dict[str, str]]) -> dict[str, object]:
    return {k: [r[k] for r in rows] for k in ("column_name", "ucd", "unit")}


def primed(rows: dict[str, object] | None = None, schema: list[dict[str, str]] = SCHEMA) -> PrimedService:
    fake = PrimedService()
    source = VizierKnownClusters(fake)
    catalogue = CATALOGUES["hr23"]
    fake.table(source.schema_request(catalogue), schema_rows(schema))
    fake.table(source.rows_request(catalogue, choose_columns(schema)), rows or ROWS)
    return fake


@pytest.mark.req("DSI-015")
def test_the_two_predeclared_catalogues_are_registered_with_their_release_labels() -> None:
    assert {k: (c.vizier_table, c.release_label) for k, c in CATALOGUES.items()} == {
        "hr23": ("J/A+A/673/A114/clusters", "hr23:v1.0"),
        "cga20": ("J/A+A/640/A1/table1", "cga20:v1"),
    }
    assert TAPVIZIER == "https://tapvizier.cds.unistra.fr/TAPVizieR/tap"
    assert MAXREC == 100_000


@pytest.mark.req("DSI-015")
def test_columns_are_chosen_by_ucd_and_never_by_an_error_column() -> None:
    assert choose_columns(SCHEMA) == CHOSEN


def test_a_main_ucd_is_preferred_over_a_plain_one_wherever_it_sits() -> None:
    schema = [
        {"column_name": "Other", "ucd": "meta.id", "unit": ""},
        {"column_name": "RAdeg", "ucd": "pos.eq.ra", "unit": "deg"},
        *SCHEMA,
    ]
    chosen = choose_columns(schema)
    assert (chosen["name"], chosen["ra_deg"]) == ("Name", "RA_ICRS")


def test_ucds_and_units_are_compared_without_case_or_padding() -> None:
    schema = [{**r, "ucd": f" {str(r['ucd']).upper()} ", "unit": f"{str(r['unit']).upper()} "} for r in SCHEMA]
    assert choose_columns(schema) == CHOSEN


def test_a_missing_parallax_is_left_out_but_a_missing_proper_motion_is_refused() -> None:
    no_plx = [r for r in SCHEMA if r["column_name"] != "Plx"]
    assert "parallax_mas" not in choose_columns(no_plx)
    no_pm = [r for r in SCHEMA if r["column_name"] != "pmDE"]
    with pytest.raises(CatalogueSchemaError) as caught:
        choose_columns(no_pm)
    assert str(caught.value) == (
        "pm_dec_masyr: no column with UCD pos.pm;pos.eq.dec;meta.main or pos.pm;pos.eq.dec; name one with --column"
    )


@pytest.mark.req("DSI-015")
def test_a_column_in_another_unit_is_refused_by_name() -> None:
    schema = [{**r, "unit": "arcsec/yr"} if r["column_name"] == "pmDE" else r for r in SCHEMA]
    with pytest.raises(CatalogueSchemaError) as caught:
        choose_columns(schema)
    assert str(caught.value) == "pm_dec_masyr: column pmDE is in 'arcsec/yr', not mas/yr or mas/a"


def test_a_column_without_a_unit_says_so() -> None:
    schema = [{**r, "unit": None} if r["column_name"] == "RA_ICRS" else r for r in SCHEMA]
    with pytest.raises(CatalogueSchemaError, match=r"^ra_deg: column RA_ICRS is in 'no unit', not deg$"):
        choose_columns(schema)


def test_mas_per_annum_is_accepted_as_mas_per_year() -> None:
    schema = [{**r, "unit": "mas/a"} if r["column_name"] in ("pmRA*", "pmDE") else r for r in SCHEMA]
    assert choose_columns(schema) == CHOSEN


def test_an_override_names_the_column_and_its_unit_is_still_checked() -> None:
    schema = [*SCHEMA, {"column_name": "Cluster", "ucd": "meta.id", "unit": ""}]
    assert choose_columns(schema, {"name": "Cluster"})["name"] == "Cluster"
    with pytest.raises(CatalogueSchemaError, match=r"^ra_deg: column Plx is in 'mas', not deg$"):
        choose_columns(schema, {"ra_deg": "Plx"})
    with pytest.raises(CatalogueSchemaError, match=r"^name: the table has no column 'Nope'$"):
        choose_columns(schema, {"name": "Nope"})
    with pytest.raises(CatalogueSchemaError, match=r"^no benchmark column \['colour'\]; the columns are \['name',"):
        choose_columns(schema, {"colour": "BP-RP"})


@pytest.mark.req("DSI-015")
def test_requests_ask_tap_schema_then_the_chosen_columns_quoted() -> None:
    source = VizierKnownClusters(PrimedService())
    schema = source.schema_request(CATALOGUES["hr23"])
    assert schema.url == TAPVIZIER + "/sync"
    assert parse_qs(schema.body.decode())["QUERY"] == [
        "SELECT column_name, ucd, unit FROM TAP_SCHEMA.columns WHERE table_name = 'J/A+A/673/A114/clusters'"
    ]
    rows = source.rows_request(CATALOGUES["hr23"], {"name": "Name", "pm_ra_cosdec_masyr": 'pm"RA*'})
    query = parse_qs(rows.body.decode())
    assert query["QUERY"] == ['SELECT "Name" AS name, "pm""RA*" AS pm_ra_cosdec_masyr FROM "J/A+A/673/A114/clusters"']
    assert query["MAXREC"] == ["100000"]


@pytest.mark.req("DSI-015")
def test_a_fetched_catalogue_is_a_benchmark_the_cross_match_accepts() -> None:
    table = VizierKnownClusters(primed()).fetch("hr23")
    assert table.column_names == list(CHOSEN)
    assert table.column("name").to_pylist() == ["Fixture_A", "Fixture_B"]  # trimmed, sorted, unplaced row dropped
    assert table.column("pm_ra_cosdec_masyr").to_pylist() == [-36.0, 19.9]
    assert all(table.schema.field(c).type == pa.float64() for c in list(CHOSEN)[1:])
    meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
    assert meta == {
        "sdp.benchmark.release": "hr23:v1.0",
        "sdp.benchmark.licence": bm.LICENCE,
        "sdp.benchmark.citation": "Hunt & Reffert 2023, A&A 673, A114 (doi:10.1051/0004-6361/202346285)",
        "sdp.benchmark.source": "VizieR J/A+A/673/A114/clusters",
        "sdp.benchmark.columns": json.dumps(CHOSEN, sort_keys=True),
    }
    benchmark = benchmark_from("hr23", table)
    assert benchmark.problems("hr23:v1.0") == []


def test_a_missing_value_becomes_null_not_nan() -> None:
    rows = {**ROWS, "parallax_mas": [None, 5.37, 1.0]}
    table = VizierKnownClusters(primed(rows)).fetch("hr23")
    assert table.column("parallax_mas").to_pylist() == [5.37, None]


def test_an_unknown_catalogue_is_refused_before_any_request() -> None:
    fake = PrimedService()
    with pytest.raises(KeyError, match=r"unknown catalogue 'mwsc'; known: \['cga20', 'hr23'\]"):
        VizierKnownClusters(fake).fetch("mwsc")
    assert fake.calls == 0


def test_a_table_vizier_does_not_describe_is_refused() -> None:
    fake = PrimedService()
    source = VizierKnownClusters(fake)
    fake.table(source.schema_request(CATALOGUES["cga20"]), schema_rows([]))
    with pytest.raises(CatalogueSchemaError, match=r"^VizieR does not describe J/A\+A/640/A1/table1$"):
        source.fetch("cga20")


def test_a_catalogue_that_fills_the_row_limit_is_refused_as_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bm, "MAXREC", 2)
    with pytest.raises(
        CatalogueSchemaError, match=r"^J/A\+A/673/A114/clusters filled the 2-row limit; it was cut off$"
    ):
        VizierKnownClusters(primed()).fetch("hr23")


def test_one_row_under_the_limit_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bm, "MAXREC", 4)
    assert VizierKnownClusters(primed()).fetch("hr23").num_rows == 2


def test_an_http_error_names_the_table() -> None:
    fake = PrimedService()
    source = VizierKnownClusters(fake)
    fake.raw(source.schema_request(CATALOGUES["hr23"]), b"busy", status=503)
    with pytest.raises(TransportError, match=r"^VizieR J/A\+A/673/A114/clusters: HTTP 503$"):
        source.fetch("hr23")


# --- when TAPVizieR does not answer: VizieR's mirrors, then a saved file -----------------------------

MIRRORS = ("https://vizier.one.example", "https://vizier.two.example")


def asu_votable(
    schema: list[dict[str, str]] = SCHEMA,
    rows: list[list[str]] | None = None,
    table: str = 'ID="J_A_A_673_A114_clusters" name="J/A+A/673/A114/clusters"',
) -> bytes:
    """A whole-table VOTable as VizieR's ASU interface writes one: every FIELD carries its UCD and unit."""
    fields = "".join(
        f'<FIELD name="{f["column_name"]}" ucd="{f["ucd"]}" unit="{f["unit"]}" '
        + ('datatype="char" arraysize="*"/>' if f["column_name"] in ("Name",) else 'datatype="double"/>')
        for f in schema
    )
    rows = rows or [
        ["1", "Fixture_B", "56.75", "24.12", "0.1", "19.9", "-45.4", "7.36"],
        ["2", "Fixture_A", "130.1", "19.67", "0.1", "-36.0", "-12.9", "5.37"],
    ]
    data = "".join("<TR>" + "".join(f"<TD>{v}</TD>" for v in r) + "</TR>" for r in rows)
    return (
        '<?xml version="1.0"?><VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">'
        f"<RESOURCE><TABLE {table}>{fields}<DATA><TABLEDATA>{data}</TABLEDATA></DATA>"
        "</TABLE></RESOURCE></VOTABLE>"
    ).encode()


def mirrored(fake: PrimedService) -> VizierKnownClusters:
    return VizierKnownClusters(fake, mirrors=MIRRORS)


@pytest.mark.req("DSI-017")
def test_when_tapvizier_gives_no_answer_the_first_mirror_that_answers_serves_the_same_benchmark() -> None:
    fake = PrimedService()
    source = mirrored(fake)
    hr23 = CATALOGUES["hr23"]
    fake.raw(source.mirror_request(hr23, MIRRORS[1]), asu_votable(), content_type="application/x-votable+xml")
    table = source.fetch("hr23")  # TAPVizieR and the first mirror were never primed: no answer from either
    assert table.column_names == list(CHOSEN)
    assert table.column("name").to_pylist() == ["Fixture_A", "Fixture_B"]
    assert table.column("pm_ra_cosdec_masyr").to_pylist() == [-36.0, 19.9]
    meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
    assert meta["sdp.benchmark.source"] == f"VizieR J/A+A/673/A114/clusters via {MIRRORS[1]}"
    assert meta["sdp.benchmark.columns"] == json.dumps(CHOSEN, sort_keys=True)
    assert benchmark_from("hr23", table).problems("hr23:v1.0") == []


@pytest.mark.req("DSI-017")
def test_the_mirror_query_asks_for_the_whole_table_in_decimal_degrees() -> None:
    request = VizierKnownClusters(PrimedService()).mirror_request(CATALOGUES["cga20"], "https://vizier.cfa.harvard.edu")
    assert request.method == "GET"
    base, _, query = request.url.partition("?")
    assert base == "https://vizier.cfa.harvard.edu/viz-bin/votable"
    assert parse_qs(query) == {
        "-source": ["J/A+A/640/A1/table1"],
        "-out.max": ["100000"],
        "-oc.form": ["dec"],
        "-out.all": ["1"],
    }
    assert bm.VIZIER_MIRRORS[:2] == ("https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu")


@pytest.mark.req("DSI-017")
def test_a_server_error_moves_on_but_a_refusal_does_not() -> None:
    fake = PrimedService()
    source = mirrored(fake)
    hr23 = CATALOGUES["hr23"]
    fake.raw(source.schema_request(hr23), b"down", status=503)
    fake.raw(source.mirror_request(hr23, MIRRORS[0]), b"<html>maintenance</html>", content_type="text/html")
    fake.raw(source.mirror_request(hr23, MIRRORS[1]), asu_votable(), content_type="application/x-votable+xml")
    assert source.fetch("hr23").num_rows == 2
    refused = PrimedService()
    fake_source = mirrored(refused)
    refused.raw(fake_source.schema_request(hr23), b"bad query", status=400)
    with pytest.raises(TransportError, match=r"HTTP 400$"):
        fake_source.fetch("hr23")
    assert refused.calls == 1


@pytest.mark.req("DSI-017")
def test_when_nothing_answers_every_service_tried_is_named() -> None:
    with pytest.raises(TransportError) as caught:
        mirrored(PrimedService()).fetch("hr23")
    message = str(caught.value)
    assert message.startswith("no VizieR service answered for J/A+A/673/A114/clusters: TAPVizieR: ")
    assert all(f"{m}: " in message for m in MIRRORS)


def test_a_mirror_table_lacking_a_column_is_refused_not_skipped() -> None:
    fake = PrimedService()
    source = mirrored(fake)
    schema = [f for f in SCHEMA if f["column_name"] != "pmDE"]
    rows = [["1", "Fixture_A", "130.1", "19.67", "0.1", "-36.0", "5.37"]]
    fake.raw(source.mirror_request(CATALOGUES["hr23"], MIRRORS[0]), asu_votable(schema, rows))
    with pytest.raises(CatalogueSchemaError, match=r"^pm_dec_masyr: no column with UCD"):
        source.fetch("hr23")


@pytest.mark.req("DSI-017")
def test_a_saved_votable_reads_its_units_as_written_and_becomes_the_same_benchmark() -> None:
    body = asu_votable()
    assert bm.votable_columns(body) == SCHEMA
    table = bm.from_votable("hr23", body, source="VizieR J/A+A/673/A114/clusters from file hr23.vot")
    assert table.column("name").to_pylist() == ["Fixture_A", "Fixture_B"]
    assert table.schema.metadata[b"sdp.benchmark.source"] == b"VizieR J/A+A/673/A114/clusters from file hr23.vot"
    wrong = [{**f, "unit": "arcsec/yr"} if f["column_name"] == "pmDE" else f for f in SCHEMA]
    with pytest.raises(CatalogueSchemaError, match=r"^pm_dec_masyr: column pmDE is in 'arcsec/yr'"):
        bm.from_votable("hr23", asu_votable(wrong))


@pytest.mark.req("DSI-017")
def test_a_votable_of_another_or_an_unnamed_table_is_refused() -> None:
    with pytest.raises(CatalogueSchemaError, match=r"called 'J/A\+A/673/A114/clusters'.*, not J/A\+A/640/A1/table1"):
        bm.from_votable("cga20", asu_votable())
    with pytest.raises(CatalogueSchemaError, match=r"called nothing, not J/A\+A/673/A114/clusters"):
        bm.from_votable("hr23", asu_votable(table=""))
    by_id = bm.from_votable("hr23", asu_votable(table='ID="J_A_A_673_A114_clusters"'))
    assert by_id.num_rows == 2


@pytest.mark.req("DSI-017")
def test_a_query_a_mirror_refuses_in_its_votable_is_not_asked_elsewhere() -> None:
    fake = PrimedService()
    source = mirrored(fake)
    hr23 = CATALOGUES["hr23"]
    refusal = (
        b'<?xml version="1.0"?><VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3"><RESOURCE>'
        b'<INFO name="QUERY_STATUS" value="ERROR">unknown catalogue</INFO>'
        b'<TABLE name="J/A+A/673/A114/clusters"><FIELD name="Name" datatype="char" arraysize="*"/></TABLE>'
        b"</RESOURCE></VOTABLE>"
    )
    fake.raw(source.mirror_request(hr23, MIRRORS[0]), refusal)
    fake.raw(source.mirror_request(hr23, MIRRORS[1]), asu_votable())
    with pytest.raises(TapQueryError, match=r"^unknown catalogue$"):
        source.fetch("hr23")


def test_only_the_first_tables_fields_are_read_even_when_it_has_no_rows() -> None:
    body = asu_votable().replace(
        b"<RESOURCE>",
        b'<RESOURCE><TABLE name="J/A+A/673/A114/clusters"><FIELD name="Other" ucd="meta.id" unit=""'
        b' datatype="char" arraysize="*"/></TABLE>',
        1,
    )
    first = bm.first_table(body)
    assert first.names == ("J/A+A/673/A114/clusters",)
    assert [c["column_name"] for c in first.columns] == ["Other"]


@pytest.mark.req("DSI-017")
def test_a_mirror_reply_cut_off_in_its_rows_moves_on_to_the_next_mirror() -> None:
    fake = PrimedService()
    source = mirrored(fake)
    hr23 = CATALOGUES["hr23"]
    whole = asu_votable()
    fake.raw(source.mirror_request(hr23, MIRRORS[0]), whole[: whole.index(b"<TR>") + 30])
    fake.raw(source.mirror_request(hr23, MIRRORS[1]), whole)
    table = source.fetch("hr23")
    assert table.schema.metadata[b"sdp.benchmark.source"].endswith(MIRRORS[1].encode())
