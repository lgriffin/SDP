"""T1: the NASA ADS client and MAST's program list on invented answers (PUB-001, PUB-002). Not ADS or MAST data."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.archives.vo_fakes import PrimedService

from sdp.adapters.archives import ads
from sdp.adapters.archives.mast import MAST_OBSCORE
from sdp.adapters.archives.mast_programs import fetch_programs, programs_query, releases_query
from sdp.adapters.archives.transport import CassetteTransport, HttpRequest, HttpResponse
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.adapters.archives.vo import sync_request
from sdp.adapters.settings import find_dotenv, load_dotenv

pytestmark = pytest.mark.tier("T1")
TOKEN = "not-a-real-token-0123456789"


def answer(num_found: int, docs: list[dict[str, object]], facets: dict[str, list[object]] | None = None) -> bytes:
    doc: dict[str, object] = {"response": {"numFound": num_found, "docs": docs}}
    if facets is not None:
        doc["facet_counts"] = {"facet_fields": facets}
    return json.dumps(doc).encode()


class OneAnswer:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.sent: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.sent.append(request)
        return self.response


@pytest.mark.req("PUB-001")
def test_the_token_comes_from_the_environment_and_goes_only_in_the_header() -> None:
    assert ads.token_from_env({"ADS_API_TOKEN": f" {TOKEN} "}) == TOKEN
    assert ads.token_from_env({"ADS_DEV_KEY": TOKEN}) == TOKEN
    service = OneAnswer(
        HttpResponse(200, answer(1, [{"bibcode": "2023A&A...673A.114H"}]), {"X-RateLimit-Remaining": "4321"})
    )
    found = ads.search(service, 'full:"Ruprecht 147"', token=TOKEN, facet_fields=("property",))
    sent = service.sent[0]
    assert sent.headers == {"Authorization": f"Bearer {TOKEN}"}
    assert TOKEN not in sent.url and TOKEN not in sent.key()
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(sent.url).query)
    assert params["q"] == ['full:"Ruprecht 147"'] and params["facet.field"] == ["property"]
    assert found.num_found == 1 and found.remaining == 4321


@pytest.mark.req("PUB-001")
def test_no_token_says_where_to_put_one_without_guessing() -> None:
    with pytest.raises(ads.AdsTokenMissing, match=r"\.env"):
        ads.token_from_env({"ADS_API_TOKEN": "  "})


@pytest.mark.req("PUB-001")
def test_a_recording_never_holds_the_token(tmp_path: Path) -> None:
    service = OneAnswer(HttpResponse(200, answer(0, []), {"Content-Type": "application/json"}))
    recorder = CassetteTransport(tmp_path, "record", inner=service, clock=lambda: datetime(2026, 11, 5, tzinfo=UTC))
    ads.search(recorder, "year:2026", token=TOKEN)
    (cassette,) = tmp_path.glob("*.json")
    assert TOKEN not in cassette.read_text("utf-8")
    assert "Authorization" not in json.loads(cassette.read_text("utf-8"))["request"]["headers"]
    replayed = ads.search(CassetteTransport(tmp_path, "replay"), "year:2026", token="another")
    assert replayed.num_found == 0


@pytest.mark.req("PUB-001")
@pytest.mark.parametrize(("status", "words"), [(401, "refused the token"), (403, "refused the token"), (429, "spent")])
def test_a_refusal_is_named_and_never_echoes_the_token(status: int, words: str) -> None:
    with pytest.raises(ads.AdsRefused, match=words) as caught:
        ads.search(OneAnswer(HttpResponse(status, b"{}")), "year:2026", token=TOKEN)
    assert TOKEN not in str(caught.value)


@pytest.mark.req("PUB-001")
def test_facets_are_read_as_value_count_pairs_and_a_malformed_answer_is_refused() -> None:
    body = answer(15, [], {"property": ["refereed", 13, "notrefereed", 2]})
    found = ads.parse_answer("q", HttpResponse(200, body))
    assert found.facets == {"property": {"refereed": 13, "notrefereed": 2}}
    with pytest.raises(UntrustedInput):
        ads.parse_answer("q", HttpResponse(200, answer(1, [], {"property": ["refereed"]})))
    with pytest.raises(UntrustedInput):
        ads.parse_answer("q", HttpResponse(200, b'{"error": "x"}'))
    with pytest.raises(ValueError):
        ads.search_request(" ")


@pytest.mark.req("PUB-001")
def test_the_dotenv_file_fills_only_what_the_shell_has_not_set(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text(
        f"# a comment\nADS_API_TOKEN='{TOKEN}'\nexport OTHER=\"two\"\nSHELL_WINS=file\nnot a pair\n", "utf-8"
    )
    inner = tmp_path / "python" / "packages"
    inner.mkdir(parents=True)
    assert find_dotenv(inner) == tmp_path / ".env"
    env = {"SHELL_WINS": "shell"}
    added = load_dotenv(find_dotenv(inner), env)
    assert sorted(added) == ["ADS_API_TOKEN", "OTHER"]
    assert env == {"SHELL_WINS": "shell", "ADS_API_TOKEN": TOKEN, "OTHER": "two"}


@pytest.mark.req("PUB-001")
def test_the_search_for_a_dotenv_stops_at_the_top_of_the_checkout(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("X=1\n", "utf-8")
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    assert find_dotenv(checkout) is None


def primed_mast(described: dict[str, object], released: dict[str, object]) -> PrimedService:
    service = PrimedService()
    service.table(sync_request(MAST_OBSCORE, programs_query("JWST"), 50_000), described)
    service.table(sync_request(MAST_OBSCORE, releases_query("JWST"), 50_000), released)
    return service


@pytest.mark.req("PUB-002")
def test_each_program_has_its_description_and_the_dates_its_products_are_public() -> None:
    service = primed_mast(
        {
            "prpid": ["1324", "1324", "1011", "2736"],
            "prppi": ["Treu, Tommaso L.", "Treu, T.", "Ressler, M.", "Pontoppidan, K."],
            "prptitle": ["GLASS", "GLASS", "MIRI check-out", "Early Release Observations"],
            "prpproject": ["ERS", "ERS", "com", "DD"],
        },
        {
            "prpid": ["1324", "2736"],
            "level3_products": [1147, 52],
            "public_from": ["2022-07-14 13:00:00", "2022-07-12 14:30:00.5"],
            "public_to": ["2023-07-08 15:14:11", "2022-07-12 14:30:00"],
        },
    )
    programs = fetch_programs(service)
    assert [p["program_id"] for p in programs] == ["1011", "1324", "2736"]
    commissioning, glass, ero = programs
    assert commissioning["category"] == "COM" and commissioning["public_from"] is None
    assert commissioning["level3_products"] == 0
    assert glass["pi"] == "Treu, T." and glass["level3_products"] == 1147
    assert glass["public_from"] == datetime(2022, 7, 14, 13, tzinfo=UTC)
    assert ero["public_from"] == datetime(2022, 7, 12, 14, 30, 0, 500000, tzinfo=UTC)


@pytest.mark.req("PUB-002")
def test_a_program_number_that_is_not_digits_is_refused() -> None:
    service = primed_mast(
        {"prpid": ['1" OR "x'], "prppi": ["x"], "prptitle": ["x"], "prpproject": ["GO"]},
        {"prpid": ["1"], "level3_products": [1], "public_from": ["2023-01-01"], "public_to": ["2023-01-01"]},
    )
    with pytest.raises(UntrustedInput, match="not a number"):
        fetch_programs(service)


@pytest.mark.req("PUB-002")
def test_a_release_date_that_cannot_be_read_is_refused() -> None:
    service = primed_mast(
        {"prpid": ["1"], "prppi": ["x"], "prptitle": ["x"], "prpproject": ["GO"]},
        {"prpid": ["1"], "level3_products": [1], "public_from": ["soon"], "public_to": ["2023-01-01"]},
    )
    with pytest.raises(UntrustedInput, match="release date"):
        fetch_programs(service)


@pytest.mark.req("PUB-002")
def test_an_answer_that_reaches_the_row_cap_is_refused_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    from sdp.adapters.archives import mast_programs as mp

    monkeypatch.setattr(mp, "MAX_PROGRAMS", 2)
    service = PrimedService()
    service.table(
        sync_request(MAST_OBSCORE, programs_query("JWST"), 2),
        {"prpid": ["1", "2"], "prppi": ["a", "b"], "prptitle": ["x", "y"], "prpproject": ["GO", "GO"]},
    )
    service.table(
        sync_request(MAST_OBSCORE, releases_query("JWST"), 2),
        {"prpid": ["1"], "level3_products": [1], "public_from": ["2023-01-01"], "public_to": ["2023-01-01"]},
    )
    with pytest.raises(UntrustedInput, match="cap"):
        mp.fetch_programs(service)
