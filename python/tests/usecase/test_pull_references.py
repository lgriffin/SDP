"""T4: PullReferences asks reference catalogues about a candidate and keeps every answer (INV-023)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from sdp.kernel.application.references import PullReferencesCommand
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import Investigation
from sdp.kernel.ports.investigate import InvestigateFieldCommand, TriageCommand
from sdp.testing.investigate import (
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.references import FakeReferenceCatalogue, offset
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INV-023")]


@dataclass
class World:
    k: InMemoryKernel
    candidate: Investigation
    position: tuple[float, float]
    gaia_row: dict[str, float]


def make_world(**catalogues: FakeReferenceCatalogue) -> World:
    catalogue = synthetic_catalogue()
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
        references=catalogues,
    )
    assert k.kernel.investigate_field is not None
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(synthetic_field(), synthetic_profile(), seed=11))
    candidate = max(run.investigations, key=lambda i: i.rank.score)
    assert k.kernel.query_investigations is not None
    dossier = k.kernel.query_investigations.dossier(candidate.investigation_id)
    assert dossier is not None
    obj = next(o for o in dossier["objects"] if o["object"]["object_key"] == candidate.candidate.object_key)
    gaia_row = next(r for rows in obj["catalogue_rows"].values() for r in rows if "pm_ra_cosdec_masyr" in r)
    return World(k, candidate, tuple(obj["object"]["sky_icrs"]), gaia_row)


def pull(world: World, *sources: str, radius: float = 5.0, actor: str = "leigh") -> Investigation:
    use_case = world.k.kernel.pull_references
    assert use_case is not None
    return use_case.execute(PullReferencesCommand(world.candidate.investigation_id, sources, actor, radius))


def test_each_catalogue_answers_and_every_answer_is_kept_with_the_candidate() -> None:
    near = FakeReferenceCatalogue("simbad", "SIMBAD", [])
    empty = FakeReferenceCatalogue("vsx", "AAVSO VSX", [])
    down = FakeReferenceCatalogue("ps1", "Pan-STARRS DR2", failure=TimeoutError("MAST did not answer"))
    world = make_world(simbad=near, vsx=empty, ps1=down)
    ra, dec = world.position
    near.rows = [
        {"source_key": "simbad:near", "ra_deg": offset(ra, dec, 1.0)[0], "dec_deg": dec, "object_type": "*"},
        {"source_key": "simbad:far", "ra_deg": ra, "dec_deg": offset(ra, dec, 0, 4.0)[1], "object_type": "V*"},
    ]
    before = world.candidate.to_document()

    after = pull(world, "simbad", "vsx", "ps1")

    by_source = {p.source_id: p for p in after.references}
    assert {s: p.status for s, p in by_source.items()} == {"simbad": "found", "vsx": "none", "ps1": "failed"}
    found = by_source["simbad"]
    assert [m["source_key"] for m in found.matches] == ["simbad:near", "simbad:far"]
    assert found.matches[0]["separation_arcsec"] == pytest.approx(1.0, abs=0.01)
    assert found.actor == "leigh" and found.radius_arcsec == 5.0 and found.title == "SIMBAD"
    assert by_source["ps1"].error == "TimeoutError: MAST did not answer"
    assert by_source["ps1"].raw_artifact_id is None
    # the archive's answer is stored as it came
    raw = world.k.store.get(ArtifactId(found.raw_artifact_id or ""))
    assert {r["source_key"] for r in json.loads(raw)} == {"simbad:near", "simbad:far"}
    # nothing the search decided changes, and the pulls survive a reload
    doc = after.to_document()
    for key in ("test_results", "rank", "state", "transitions", "evidence_used", "notes", "label"):
        assert doc[key] == before[key]
    assert world.k.kernel.query_investigations is not None
    reloaded = world.k.kernel.query_investigations.investigation(world.candidate.investigation_id)
    assert reloaded is not None and reloaded.to_document()["references"] == doc["references"]


def test_a_catalogue_from_another_epoch_is_compared_where_the_candidate_was_then() -> None:
    old = FakeReferenceCatalogue("2mass", "2MASS")
    world = make_world(**{"2mass": old})
    ra, dec = world.position
    years = 2000.0 - float(world.gaia_row["epoch_jyear"])
    east = float(world.gaia_row["pm_ra_cosdec_masyr"]) * years / 1000.0
    north = float(world.gaia_row["pm_dec_masyr"]) * years / 1000.0
    then = offset(ra, dec, east, north)
    old.rows = [{"source_key": "2mass:then", "ra_deg": then[0], "dec_deg": then[1], "epoch_jyear": 2000.0}]

    pulled = pull(world, "2mass", radius=0.5).references[-1]

    assert pulled.status == "found"
    assert pulled.matches[0]["separation_arcsec"] < 0.05
    assert pulled.target.pm_ra_cosdec_masyr == pytest.approx(world.gaia_row["pm_ra_cosdec_masyr"])
    # the cone was asked wider than the radius, so the moved position is inside it
    assert old.asked[-1][2] > 0.5


def test_pulls_are_appended_and_a_closed_candidate_still_takes_them() -> None:
    simbad = FakeReferenceCatalogue("simbad", "SIMBAD")
    world = make_world(simbad=simbad)
    pull(world, "simbad")
    assert world.k.kernel.triage_investigation is not None
    world.k.kernel.triage_investigation.execute(
        TriageCommand(world.candidate.investigation_id, "close", actor="leigh", reason="known star")
    )
    after = pull(world, "simbad", radius=30.0)
    assert [p.radius_arcsec for p in after.references] == [5.0, 30.0]
    assert after.state.value == "closed"


@pytest.mark.parametrize(
    ("sources", "radius", "actor", "message"),
    [
        ((), 5.0, "leigh", "at least one catalogue"),
        (("nowhere",), 5.0, "leigh", "unknown reference catalogue 'nowhere'"),
        (("simbad",), 0.0, "leigh", "search radius"),
        (("simbad",), 601.0, "leigh", "search radius"),
        (("simbad",), 5.0, " ", "who asked"),
    ],
)
def test_a_malformed_pull_is_refused_before_any_archive_is_asked(
    sources: tuple[str, ...], radius: float, actor: str, message: str
) -> None:
    simbad = FakeReferenceCatalogue("simbad", "SIMBAD")
    world = make_world(simbad=simbad)
    with pytest.raises(InvariantViolation, match=message):
        pull(world, *sources, radius=radius, actor=actor)
    assert simbad.asked == []


def test_an_unknown_investigation_is_refused() -> None:
    world = make_world(simbad=FakeReferenceCatalogue("simbad", "SIMBAD"))
    assert world.k.kernel.pull_references is not None
    with pytest.raises(InvariantViolation, match="no investigation inv-missing"):
        world.k.kernel.pull_references.execute(PullReferencesCommand("inv-missing", ("simbad",), "leigh"))
