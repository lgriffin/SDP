"""T4: `sdp publication` on invented programs and ADS answers (PUB-004). Not MAST or ADS data."""

from __future__ import annotations

import csv
import json
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sdp.adapters.archives import mast_programs
from sdp.adapters.archives.transport import HttpRequest, HttpResponse
from sdp.cli import app as cli
from sdp.cli import publication as cmd

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
TOKEN = "invented-token-for-tests-42"
PUBLIC = datetime(2023, 1, 1, tzinfo=UTC)


def programs(transport: object, collection: str = "JWST") -> list[dict[str, Any]]:
    def p(pid: str, category: str, public: datetime | None) -> dict[str, Any]:
        return {
            "program_id": pid,
            "pi": "Someone, A.",
            "title": f"Program {pid}",
            "category": category,
            "level3_products": 5,
            "public_from": public,
            "public_to": public,
        }

    return [
        p("1001", "GO", PUBLIC),  # named in a refereed paper
        p("1002", "GO", PUBLIC),  # named only in a preprint
        p("1003", "GTO", PUBLIC),  # named nowhere
        p("1004", "CAL", PUBLIC),  # calibration: never asked
        p("1005", "GO", datetime(2026, 9, 1, tzinfo=UTC)),  # public two months
        p("1006", "GO", None),  # nothing public
    ]


class FakeAds:
    """ADS as the tests need it: ``remaining`` searches left, falling by one per search."""

    def __init__(self, remaining: int = 5000) -> None:
        self.remaining = remaining
        self.asked: list[str] = []
        self.headers: list[dict[str, str]] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)["q"][0]
        self.asked.append(q)
        self.headers.append(dict(request.headers))
        self.remaining -= 1
        if '"program 1001"' in q:
            docs, facets, total = [{"bibcode": "2024ApJ...1....1A", "property": ["REFEREED"]}], ["refereed", 1], 3
        elif '"program 1002"' in q:
            docs, facets, total = (
                [{"bibcode": "2025arXiv250100001B", "property": ["NOT REFEREED"]}],
                ["notrefereed", 1],
                1,
            )
        else:
            docs, facets, total = [], [], 0
        body = {"response": {"numFound": total, "docs": docs}, "facet_counts": {"facet_fields": {"property": facets}}}
        return HttpResponse(200, json.dumps(body).encode(), {"X-RateLimit-Remaining": str(self.remaining)})


@pytest.fixture
def ads(monkeypatch: pytest.MonkeyPatch) -> FakeAds:
    fake = FakeAds()
    monkeypatch.setenv("ADS_API_TOKEN", TOKEN)
    monkeypatch.setattr(mast_programs, "fetch_programs", programs)
    monkeypatch.setattr(cmd, "mast_transport", lambda: None)
    monkeypatch.setattr(cmd, "ads_transport", lambda: fake)
    monkeypatch.setattr(cmd, "today", lambda: date(2026, 11, 5))
    return fake


def read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def everything_written(folder: Path) -> str:
    return "".join(p.read_text("utf-8") for p in folder.iterdir())


@pytest.mark.req("PUB-004")
def test_the_register_splits_programs_by_what_ads_holds_and_never_writes_the_token(
    ads: FakeAds, tmp_path: Path
) -> None:
    result = runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    rows = {r["program_id"]: r for r in read(tmp_path / "programs.csv")}
    assert {k: r["status"] for k, r in rows.items()} == {
        "1001": "published",
        "1002": "only-preprints",
        "1003": "unpublished",
        "1004": "not-science",
        "1005": "too-recent",
        "1006": "not-public",
    }
    assert rows["1001"]["first_refereed_bibcodes"] == "2024ApJ...1....1A"
    assert '"program 1003"' in rows["1003"]["ads_query"]
    assert [r["program_id"] for r in read(tmp_path / "unpublished.csv")] == ["1003"]
    summary = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    assert summary["status"] == "EXPLORATORY" and summary["complete"] and summary["settings_sha256"]
    assert len(ads.asked) == 3
    assert all(h == {"Authorization": f"Bearer {TOKEN}"} for h in ads.headers)
    assert TOKEN not in everything_written(tmp_path) and TOKEN not in result.output


@pytest.mark.req("PUB-004")
def test_a_rerun_asks_ads_only_about_programs_not_yet_asked(ads: FakeAds, tmp_path: Path) -> None:
    first = runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path), "--limit", "2"])
    assert first.exit_code == 0, first.output
    assert len(ads.asked) == 2
    statuses = [r["status"] for r in read(tmp_path / "programs.csv")]
    assert statuses.count("not-asked-yet") == 1
    assert not json.loads((tmp_path / "summary.json").read_text("utf-8"))["complete"]
    second = runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path)])
    assert second.exit_code == 0, second.output
    assert len(ads.asked) == 3 and len(set(ads.asked)) == 3
    assert json.loads((tmp_path / "summary.json").read_text("utf-8"))["complete"]


@pytest.mark.req("PUB-004")
def test_the_run_stops_before_the_days_searches_run_out(ads: FakeAds, tmp_path: Path) -> None:
    ads.remaining = 201
    result = runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path), "--reserve", "200"])
    assert result.exit_code == 0, result.output
    assert len(ads.asked) == 1
    summary = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    assert "searches left" in summary["stopped"] and summary["counts"]["not-asked-yet"] == 2


@pytest.mark.req("PUB-004")
def test_without_a_token_the_command_says_where_to_put_one(
    ads: FakeAds, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADS_API_TOKEN")
    monkeypatch.delenv("ADS_DEV_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    result = runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path / "out")])
    assert result.exit_code != 0 and ".env" in str(result.exception)
    assert ads.asked == []


@pytest.mark.req("PUB-004")
def test_search_is_the_prior_art_check(ads: FakeAds) -> None:
    result = runner.invoke(cli.app, ["publication", "search", '"program 1001"', "--refereed"])
    assert result.exit_code == 0, result.output
    assert ads.asked == ['("program 1001") AND property:refereed']
    assert '"num_found": 3' in result.output and TOKEN not in result.output


@pytest.mark.req("PUB-004")
def test_an_answer_older_than_max_age_is_asked_again_and_the_summary_dates_its_answers(
    ads: FakeAds, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cmd, "today", lambda: date(2026, 9, 1))
    assert runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path)]).exit_code == 0
    monkeypatch.setattr(cmd, "today", lambda: date(2026, 9, 20))
    assert runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path)]).exit_code == 0
    assert len(ads.asked) == 3
    monkeypatch.setattr(cmd, "today", lambda: date(2026, 11, 5))
    assert runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path)]).exit_code == 0
    assert len(ads.asked) == 6
    summary = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    assert summary["ads_asked_from"] == summary["ads_asked_to"] == "2026-11-05"


@pytest.mark.req("PUB-004")
def test_a_title_a_spreadsheet_would_run_as_a_formula_is_written_as_text(
    ads: FakeAds, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def with_formula(transport: object, collection: str = "JWST") -> list[dict[str, Any]]:
        found = programs(transport, collection)
        found[0] = {**found[0], "title": '=HYPERLINK("http://x")', "pi": " +1 Someone"}
        return found

    monkeypatch.setattr(mast_programs, "fetch_programs", with_formula)
    assert runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path)]).exit_code == 0
    row = read(tmp_path / "programs.csv")[0]
    assert row["title"] == '\'=HYPERLINK("http://x")' and row["pi"] == "' +1 Someone"


@pytest.mark.req("PUB-004")
def test_refereed_papers_past_the_first_page_are_fetched_with_one_more_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class PreprintsFirst(FakeAds):
        def send(self, request: HttpRequest) -> HttpResponse:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)["q"][0]
            self.asked.append(q)
            if q.endswith("AND property:refereed"):
                docs = [{"bibcode": "2025ApJ...9....9Z", "property": ["REFEREED"]}]
                return HttpResponse(200, json.dumps({"response": {"numFound": 1, "docs": docs}}).encode())
            docs = [{"bibcode": f"2024arXiv{i}", "property": ["NOT REFEREED"]} for i in range(20)]
            body = {
                "response": {"numFound": 21, "docs": docs},
                "facet_counts": {"facet_fields": {"property": ["refereed", 1]}},
            }
            return HttpResponse(200, json.dumps(body).encode())

    fake = PreprintsFirst()
    monkeypatch.setenv("ADS_API_TOKEN", TOKEN)
    monkeypatch.setattr(mast_programs, "fetch_programs", lambda t, c="JWST": programs(t, c)[:1])
    monkeypatch.setattr(cmd, "mast_transport", lambda: None)
    monkeypatch.setattr(cmd, "ads_transport", lambda: fake)
    monkeypatch.setattr(cmd, "today", lambda: date(2026, 11, 5))
    assert runner.invoke(cli.app, ["publication", "programs", "--out", str(tmp_path)]).exit_code == 0
    assert len(fake.asked) == 2
    assert read(tmp_path / "programs.csv")[0]["first_refereed_bibcodes"] == "2025ApJ...9....9Z"


@pytest.mark.req("PUB-004")
@pytest.mark.parametrize(("left", "searches"), [(200, 1), (201, 2)])
def test_the_second_search_is_not_spent_from_the_reserve(left: int, searches: int) -> None:
    class Answer:
        def __init__(self, docs: list[dict[str, Any]], remaining: int) -> None:
            self.num_found, self.docs, self.remaining = 21, docs, remaining
            self.facets = {"property": {"refereed": 1}}

    calls: list[str] = []

    def search(transport: object, q: str, **_: object) -> Answer:
        calls.append(q)
        if len(calls) == 1:
            return Answer([{"bibcode": "2024arXiv1", "property": ["NOT REFEREED"]}], left)
        return Answer([{"bibcode": "2025ApJ...9....9Z", "property": ["REFEREED"]}], left - 1)

    record, remaining = cmd._ask(FakeAds(), TOKEN, '"program 1"', search, date(2026, 11, 5), reserve=200)
    assert len(calls) == searches
    assert record["refereed"] == 1
    assert remaining == left - (searches - 1)
