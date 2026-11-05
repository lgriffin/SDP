"""T1: publication linkage on invented programs (PUB-003): the ADS query, and where each program stands."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from sdp_investigate import linkage

pytestmark = pytest.mark.tier("T1")
TODAY = date(2026, 11, 5)


def program(
    pid: str = "1324", category: str = "GO", public: datetime | None = datetime(2023, 1, 1, tzinfo=UTC)
) -> linkage.Program:
    return linkage.Program("JWST", pid, category, "Treu, T.", "GLASS", 10, public, public)


def mentions(total: int, refereed: int) -> linkage.Mentions:
    return linkage.Mentions("q", total, refereed, ("2023ApJ...1A",) if refereed else ())


@pytest.mark.req("PUB-003")
def test_the_query_names_the_mission_and_the_program_the_ways_papers_write_it() -> None:
    q = linkage.mention_query("JWST", "1324")
    assert q.startswith('full:("JWST") AND full:(')
    for phrase in ('"program 1324"', '"PID 1324"', '"GO 1324"', '"ERS 1324"', '"proposal ID 1324"', '"jw01324"'):
        assert phrase in q
    assert q.endswith("AND year:2022-")


@pytest.mark.req("PUB-003")
def test_a_leading_zero_is_dropped_and_the_file_prefix_is_padded_to_five() -> None:
    q = linkage.mention_query("JWST", "0042")
    assert '"program 42"' in q and '"jw00042"' in q and "0042" not in q.replace("jw00042", "")


@pytest.mark.req("PUB-003")
@pytest.mark.parametrize("bad", ["1324 OR *", '12"34', "", "1234567", "abc"])
def test_a_program_id_that_is_not_digits_never_reaches_a_query(bad: str) -> None:
    with pytest.raises(ValueError, match="digits"):
        linkage.mention_query("JWST", bad)


@pytest.mark.req("PUB-003")
def test_an_unknown_mission_is_refused() -> None:
    with pytest.raises(ValueError, match="no linkage settings"):
        linkage.mention_query("HST", "1")


@pytest.mark.req("PUB-003")
def test_only_science_programs_public_for_a_year_are_asked_about() -> None:
    assert linkage.status_without_ads(program(category="CAL"), TODAY) == linkage.NOT_SCIENCE
    assert linkage.status_without_ads(program(public=None), TODAY) == linkage.NOT_PUBLIC
    assert linkage.status_without_ads(program(public=datetime(2027, 1, 1, tzinfo=UTC)), TODAY) == linkage.NOT_PUBLIC
    assert linkage.status_without_ads(program(public=datetime(2026, 1, 1, tzinfo=UTC)), TODAY) == linkage.TOO_RECENT
    assert linkage.status_without_ads(program(public=datetime(2025, 11, 5, tzinfo=UTC)), TODAY) is None
    assert linkage.status_without_ads(program(), TODAY) is None


@pytest.mark.req("PUB-003")
def test_unpublished_means_no_refereed_paper_and_no_preprint_names_it() -> None:
    assert linkage.classify(program(), mentions(0, 0), TODAY) == linkage.UNPUBLISHED
    assert linkage.classify(program(), mentions(2, 0), TODAY) == linkage.ONLY_PREPRINTS
    assert linkage.classify(program(), mentions(3, 1), TODAY) == linkage.PUBLISHED
    assert linkage.classify(program(), None, TODAY) == linkage.NOT_ASKED
    assert linkage.classify(program(category="COM"), None, TODAY) == linkage.NOT_SCIENCE


@pytest.mark.req("PUB-003")
def test_the_refereed_count_comes_from_the_property_facet() -> None:
    assert linkage.refereed_count({"property": {"refereed": 13, "notrefereed": 2}}) == 13
    assert linkage.refereed_count({}) == 0


@pytest.mark.req("PUB-003")
def test_every_asked_program_keeps_its_query_and_bibcodes_and_the_summary_counts_them() -> None:
    asked = linkage.row(program(), linkage.Mentions("Q", 3, 1, ("2023ApJ...1A",)), linkage.PUBLISHED, TODAY)
    assert asked["ads_query"] == "Q" and asked["first_refereed_bibcodes"] == "2023ApJ...1A"
    assert asked["public_days"] == (TODAY - date(2023, 1, 1)).days
    quiet = linkage.row(program("7"), mentions(0, 0), linkage.UNPUBLISHED, TODAY)
    skipped = linkage.row(program("8", "CAL"), None, linkage.NOT_SCIENCE, TODAY)
    s = linkage.summary([asked, quiet, skipped], "JWST", TODAY)
    assert s["status"] == "EXPLORATORY" and s["complete"]
    assert s["counts"][linkage.PUBLISHED] == 1 and s["counts"][linkage.UNPUBLISHED] == 1
    assert s["unpublished_share"] == 0.5
    assert s["asked_by_category"] == {"GO": {"unpublished": 1, "only-preprints": 0, "published": 1}}
    assert s["settings_sha256"] == linkage.settings_sha256()


@pytest.mark.req("PUB-003")
def test_the_settings_hash_changes_when_a_setting_does() -> None:
    changed = {**linkage.SETTINGS, "min_public_days": 180}
    assert linkage.settings_sha256(changed) != linkage.settings_sha256()
