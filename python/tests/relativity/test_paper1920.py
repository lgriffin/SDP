"""T3: the 1920 report's transcribed tables become an observation bundle that reproduces the paper.

The transcription is experiments/uc02-einstein-1919/transcription/dyson-1920.csv, read from the page
images of Dyson, Eddington and Davidson 1920 with a page for every value (REL-001).
"""

from __future__ import annotations

import csv
import dataclasses
from pathlib import Path

import numpy as np
import pytest

from sdp_relativity import paper1920
from sdp_relativity.fitter import fit_set
from sdp_relativity.observations import parse_bundle
from sdp_relativity.oracles import load_registry
from sdp_relativity.reduction import historical

from .conftest import RunFactory

pytestmark = pytest.mark.tier("T3")

FIELDS = ("table", "page", "instrument", "plate_id", "star_id", "quantity", "value", "unit", "check")
CSV = Path(__file__).resolve().parents[3] / "experiments" / "uc02-einstein-1919" / "transcription" / "dyson-1920.csv"


@pytest.fixture(scope="module")
def transcription() -> paper1920.Transcription:
    return paper1920.Transcription.read(CSV)


@pytest.mark.req("REL-009")
def test_every_instrument_is_built_and_every_row_names_its_cells(transcription: paper1920.Transcription) -> None:
    records = paper1920.records(transcription)
    stars = [r for r in records if r.fields["kind"] == "star"]
    by_instrument = {
        i: [r for r in stars if r.fields["instrument"] == i] for i in ("sobral-4in", "sobral-astro", "principe-astro")
    }
    # Seven eclipse plates of seven stars; sixteen astrographic plates of five stars, one blank on
    # plate 15 (p. 311); four Principe comparisons of five stars (U against K2 left out, p. 327).
    assert {i: len(r) for i, r in by_instrument.items()} == {"sobral-4in": 49, "sobral-astro": 79, "principe-astro": 20}
    assert len({r.record_id for r in records}) == len(records)
    for r in stars:
        assert r.locator.startswith("p. ") and r.fields["printed"], r.record_id
    four_inch = next(
        r for r in by_instrument["sobral-4in"] if r.fields["plate_id"] == "I" and r.fields["star_id"] == "11"
    )
    assert "p. 303, II-eclipse, Dx, plate I, star 11" in four_inch.locator
    assert "p. 303, II-comparison, Dx, plate 14_2a, star 11" in four_inch.locator
    assert four_inch.fields["printed"]["II-eclipse/I/11/Dx"] == "-1.411"


@pytest.mark.req("REL-009")
def test_the_sun_found_from_the_coefficients_gives_them_back(transcription: paper1920.Transcription) -> None:
    sun = paper1920.sobral_sun(transcription)
    for star in paper1920.SOBRAL_STARS:
        x = transcription.cell(table="I", star_id=star, quantity="x").number - sun[0]
        y = transcription.cell(table="I", star_id=star, quantity="y").number - sun[1]
        ex = transcription.cell(table="p304-conditions", star_id=star, quantity="Ex").number
        ey = transcription.cell(table="p304-conditions", star_id=star, quantity="Ey").number
        assert (x / (x * x + y * y), y / (x * x + y * y)) == pytest.approx((ex, ey), abs=0.01), star


@pytest.mark.req("REL-009")
def test_the_1920_reduction_reproduces_the_published_deflections(transcription: paper1920.Transcription) -> None:
    bundle = parse_bundle(paper1920.bundle(CSV))
    registry = load_registry()
    for oracle_id, tolerance in (("sobral-4in-1920", 0.05), ("sobral-astro-1920", 0.1), ("principe-1920", 0.1)):
        oracle = registry[oracle_id]
        assert oracle.instrument is not None and oracle.value_arcsec is not None
        result = fit_set(bundle.instrument(oracle.instrument), historical(oracle.instrument))
        assert abs(result.alpha - oracle.value_arcsec) < tolerance, (oracle_id, result.alpha)


@pytest.mark.req("REL-009")
def test_table_ii_agrees_with_the_1920_normal_equations_except_three_printed_cells(
    transcription: paper1920.Transcription,
) -> None:
    """The sums of Tables III-VI rebuilt from Table II. Three cells disagree with the 1920 working:
    plate I star 2 Dx (-0.789 printed, -0.798 used), plate 14_2a star 2 Dx (-0.396 printed, -0.345
    implied) and plate VIII's Dy constant (-1.322 printed, -1.332 implied)."""
    t = transcription
    stars = paper1920.SOBRAL_STARS
    x = {s: t.cell(table="I", star_id=s, quantity="x").number for s in stars}
    y = {s: t.cell(table="I", star_id=s, quantity="y").number for s in stars}
    disagree = set()
    for table, kind, axis, plates in (
        ("III", "eclipse", "Dx", paper1920.ECLIPSE_PLATES),
        ("IV", "comparison", "Dx", paper1920.COMPARISON_PLATES),
        ("V", "eclipse", "Dy", paper1920.ECLIPSE_PLATES),
        ("VI", "comparison", "Dy", paper1920.COMPARISON_PLATES),
    ):
        names = ("rhs_c", "rhs_b", "rhs_a") if axis == "Dx" else ("rhs_f", "rhs_d", "rhs_e")
        # The c (f) equation sums D, the next two D times the other and the own coordinate.
        other, own = (y, x) if axis == "Dx" else (x, y)
        weights: tuple[dict[str, float], ...] = (dict.fromkeys(stars, 1.0), other, own)
        for plate in plates:
            constant = t.cell(table=f"II-{kind}", plate_id=plate, star_id="below-line", quantity=axis).number
            d = {
                s: t.cell(table=f"II-{kind}", plate_id=plate, star_id=s, quantity=axis).number - constant for s in stars
            }
            for name, w in zip(names, weights, strict=True):
                printed = t.cell(table=table, plate_id=plate, quantity=name).number
                if abs(sum(d[s] * w[s] for s in stars) - printed) > 0.0035:
                    disagree.add((kind, axis, plate))
    assert disagree == {("eclipse", "Dx", "I"), ("comparison", "Dx", "14_2a"), ("eclipse", "Dy", "VIII")}


@pytest.mark.req("REL-009")
def test_a_record_is_verified_only_when_every_cell_it_cites_was_checked(tmp_path: Path) -> None:
    rows = list(csv.DictReader(CSV.open(newline="", encoding="utf-8")))
    target = f"{paper1920.DOCUMENT_ID}/principe-astro/X-G1/3"
    cites = {r.record_id: set(r.fields["printed"]) for r in paper1920.records(paper1920.Transcription.read(CSV))}
    dropped = sorted(cites[target])[0]
    for r in rows:
        key = paper1920.Cell(*(r[f] for f in FIELDS)).key
        r["check"] = "" if key == dropped else "re-read by claude 2026-10-30"
    path = tmp_path / "checked.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    passes = {v["record_id"]: v for v in paper1920.bundle(path)["verifications"]}
    assert set(passes) == {k for k, keys in cites.items() if dropped not in keys}
    assert target not in passes
    some = passes[f"{paper1920.DOCUMENT_ID}/principe-astro/X-G1/4"]
    assert some["matches"] and some["verifier"] == "claude" and some["verified_at"].startswith("2026-10-30")


@pytest.mark.req("REL-009")
def test_every_cell_of_the_committed_transcription_has_a_recorded_check() -> None:
    rows = list(csv.DictReader(CSV.open(newline="", encoding="utf-8")))
    assert all(paper1920.CHECK.search(r["check"]) for r in rows)
    bundle = paper1920.bundle(CSV)
    star_ids = {s["record_id"] for i in bundle["instrument_sets"] for p in i["plates"] for s in p["stars"]}
    assert star_ids <= {v["record_id"] for v in bundle["verifications"]}


@pytest.mark.req("REL-009")
def test_every_expected_measurement_must_be_transcribed_and_only_a_dash_means_none(
    transcription: paper1920.Transcription,
) -> None:
    dash = transcription.cell(table="p311-Dy", plate_id="15", star_id="10", quantity="Dy")
    assert dash.value == "—"
    for gone in (dash, transcription.cell(table="p324-WD1-y", star_id="10", quantity="D1y")):
        without = paper1920.Transcription(c for c in transcription.cells if c != gone)
        with pytest.raises(paper1920.TranscriptionError, match=f"quantity={gone.quantity!r}"):
            paper1920.records(without)


@pytest.mark.req("REL-009")
def test_a_missing_or_unreadable_cell_is_refused_by_name(transcription: paper1920.Transcription) -> None:
    without = paper1920.Transcription(c for c in transcription.cells if c.quantity != "screw_value")
    with pytest.raises(paper1920.TranscriptionError, match="quantity='screw_value'"):
        paper1920.sobral_4in(without)
    cell = transcription.cell(table="p328-result", quantity="pe_y")
    assert cell.value == "+-0.21" and cell.number == pytest.approx(0.21)
    blot = dataclasses.replace(cell, value="0.2?")
    with pytest.raises(paper1920.TranscriptionError, match=r"p\. 328 p328-result pe_y"):
        _ = blot.number


def test_the_sun_centre_is_the_mean_of_each_star_less_its_coefficient() -> None:
    sun = (0.4, -0.3)
    positions = {"a": (1.0, 0.2), "b": (-0.5, 0.9)}
    coefficients = {}
    for s, (x, y) in positions.items():
        dx, dy = x - sun[0], y - sun[1]
        coefficients[s] = (2.0 * dx / (dx * dx + dy * dy), 2.0 * dy / (dx * dx + dy * dy))
    assert np.allclose(paper1920.sun_centre(positions, coefficients, 2.0), sun)


@pytest.mark.req("REL-009")
@pytest.mark.gate("REL-G-04")
@pytest.mark.tier("T8")
def test_the_1919_data_reproduces_the_published_values_and_waits_only_on_the_2020_reanalysis(
    run_uc02: RunFactory,
) -> None:
    run = run_uc02(paper1920.bundle(CSV))
    outcomes = {g: run.outcome(g) for g in (f"REL-G-0{i}" for i in range(1, 10))}
    assert {g for g, o in outcomes.items() if o != "pass"} == {"REL-G-07"}
    assert outcomes["REL-G-07"] == "oracle_unverified"
