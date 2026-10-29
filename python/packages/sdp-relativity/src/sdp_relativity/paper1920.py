"""The 1920 report's tables as transcribed records and an observation bundle (REL-001).

Dyson, Eddington and Davidson 1920, Phil. Trans. A 220, 291 (doi 10.1098/rsta.1920.0009) prints its
measurements, not a list of deflections: plate-against-plate differences in screw revolutions, star
positions in units of 50', and the coefficients of the predicted displacement. This module turns the
printed cells, as transcribed in ``experiments/uc02-einstein-1919/transcription/dyson-1920.csv``,
into one record per star row of the bundle. Each record names every cell it was built from, so a
reader can find them on the page, and it keeps the printed values beside the derived ones.

How each instrument's rows are built, following the paper:

- Sobral 4-inch (pp. 293-308): eclipse and comparison plates were each measured against one scale
  plate (Table II). Subtracting the mean of the comparison plates star by star removes the scale
  plate, as the paper does by subtracting the comparison plates' results (p. 306). Revolutions
  become arcsec at 6.25"/rev (p. 302). For a fixed scale, the known terms are the paper's own:
  the differential refraction and aberration of p. 307 and its adopted scale and orientation.
- Sobral astrographic (pp. 309-312): eclipse plates measured directly against comparison plates,
  in declination only; p. 311 prints D for stars 5, 4, 3, 6 and 10 on sixteen plates, at 12.3"/rev.
  For a fixed scale, the known term is the refraction and aberration change of p. 312; the
  orientation is solved from each plate.
- Principe (pp. 320-328): each eclipse plate was compared with Oxford check plates, and the paper
  removes the calculated plate constants, scale from the check plates included, before anything is
  solved (the column Delta_1). Those values, in units of 0".003 (p. 322), are the rows here. The
  orientation came from each comparison's x measures (pp. 322-325), so the rows are the y measures
  with that orientation as the known rotation. The comparison of plate U with K2 is left out, as
  the paper leaves it out (p. 327).

Positions are recentred on the Sun, which the tables do not print: for each star the printed
coefficient of the displacement points away from the Sun with a length inversely proportional to
the distance, so the centre is the star's position minus k E / |E|^2, averaged over the stars.

Probable errors: Principe's is printed (p. 328). For Sobral the paper gives none per measure, so the
4-inch value is 0.6745 times the root mean square of the comparison plates' Table VIII residuals
(the eclipse plates' rows there still hold the deflection), and the astrographic
value the same of the residuals of the per-plate solutions of the p. 311 values; both are labelled
as derived in the record.

A record is verified only when every cell it cites carries a check in the transcription's check
column ("checked by NAME DATE" or "re-read by NAME DATE"); the pass is recorded with that name and date.

    python -m sdp_relativity.paper1920 dyson-1920.csv bundle.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from sdp.kernel.domain.historical import TranscribedRecord, VerificationRecord
from sdp_relativity.transcription import bundle_from_records

DOCUMENT_ID = "dyson-1920"
SOURCE = "Dyson, Eddington and Davidson 1920, Phil. Trans. A 220, 291"
TRANSCRIBER = "claude"
TRANSCRIBED_AT = datetime(2026, 10, 29, tzinfo=UTC)

SOBRAL_STARS = ("11", "5", "4", "3", "6", "10", "2")
ECLIPSE_PLATES = ("I", "II", "III", "IV", "V", "VII", "VIII")
COMPARISON_PLATES = ("14_2a", "14_2b", "15_1", "15_2", "17_1", "17_2", "18_2")
#: Sobral astrographic plates and stars of p. 311 (no plates 13 and 14); one cell is printed as a dash.
ASTRO_PLATES = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "15", "16", "17", "18")
ASTRO_STARS = ("3", "4", "5", "6", "10")
#: Principe comparisons: (plate id, the table holding its y working, the Delta_1 y column, its stars).
PRINCIPE = (
    ("X-G1", "p323-X-G1", "d1y", ("11", "5", "4", "3", "6")),
    ("X-H1", "p324-XH1-y", "D1y", ("11", "5", "4", "3", "6")),
    ("W-D1", "p324-WD1-y", "D1y", ("5", "4", "3", "6", "10")),
    ("W-I2", "p325-WI2-y", "D1y", ("5", "4", "3", "6", "10")),
)
#: A printed dash: nothing was measured there.
DASHES = ("—", "-")
#: A check pass recorded in the transcription's check column, e.g. "checked by Leigh 2026-10-29".
CHECK = re.compile(r"(?:checked|re-read) by (?P<who>\S+) (?P<on>\d{4}-\d{2}-\d{2})")


def _check(text: str) -> re.Match[str] | None:
    """The first ";"-separated entry of a check column that is wholly a recorded check, so a note such as
    "not checked by ..." never counts."""
    for part in text.split(";"):
        if m := CHECK.fullmatch(part.strip()):
            return m
    return None


class TranscriptionError(ValueError):
    """A cell the reduction needs is missing or unreadable."""


@dataclass(frozen=True)
class Cell:
    table: str
    page: str
    instrument: str
    plate_id: str
    star_id: str
    quantity: str
    value: str
    unit: str
    check: str = ""

    @property
    def key(self) -> str:
        return f"{self.table}/{self.plate_id}/{self.star_id}/{self.quantity}".replace("//", "/")

    @property
    def number(self) -> float:
        # A printed "±0.21" is transcribed "+-0.21"; the number is its magnitude.
        text = self.value.replace("*", "").replace("−", "-").replace("±", "+-").strip().removeprefix("+-")
        try:
            return float(text)
        except ValueError:
            raise TranscriptionError(
                f"p. {self.page} {self.table} {self.quantity}: {self.value!r} is not a number"
            ) from None

    @property
    def locator(self) -> str:
        where = ", ".join(
            x for x in (self.plate_id and f"plate {self.plate_id}", self.star_id and f"star {self.star_id}") if x
        )
        return f"p. {self.page}, {self.table}, {self.quantity}" + (f", {where}" if where else "")


class Transcription:
    def __init__(self, cells: Iterable[Cell]) -> None:
        self.cells = tuple(cells)

    @classmethod
    def read(cls, path: Path) -> Transcription:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return cls(
            Cell(
                r["table"],
                r["page"],
                r["instrument"],
                r["plate_id"],
                r["star_id"],
                r["quantity"],
                r["value"],
                r["unit"],
                r.get("check") or "",
            )
            for r in rows
        )

    def cell(self, *, table: str | None = None, **match: str) -> Cell:
        found = [
            c
            for c in self.cells
            if (table is None or c.table == table) and all(getattr(c, k) == v for k, v in match.items())
        ]
        if len(found) != 1:
            where = f"table {table!r} " + " ".join(f"{k}={v!r}" for k, v in match.items())
            raise TranscriptionError(f"expected one cell for {where}, found {len(found)}")
        return found[0]


def sun_centre(
    positions: Mapping[str, tuple[float, float]], coefficients: Mapping[str, tuple[float, float]], k: float
) -> tuple[float, float]:
    """The Sun's position in the table's units: each star's position minus k E / |E|^2, averaged."""
    xs, ys = [], []
    for star, (ex, ey) in coefficients.items():
        e2 = ex * ex + ey * ey
        x, y = positions[star]
        xs.append(x - k * ex / e2)
        ys.append(y - k * ey / e2)
    return float(np.mean(xs)), float(np.mean(ys))


def _record(record_id: str, cells: Sequence[Cell], fields: Mapping[str, Any], notes: str = "") -> TranscribedRecord:
    locator = "; ".join(dict.fromkeys(c.locator for c in cells))
    printed = {c.key: c.value for c in cells}
    return TranscribedRecord(
        record_id=record_id,
        document_id=DOCUMENT_ID,
        locator=locator,
        fields={**fields, "printed": printed},
        transcriber=TRANSCRIBER,
        transcribed_at=TRANSCRIBED_AT,
        notes=notes,
    )


def _probable_error(residuals: Sequence[float], dof: int | None = None) -> float:
    r = np.asarray(residuals, dtype=float)
    n = len(r) if dof is None else dof
    return float(0.6745 * math.sqrt(float(r @ r) / n))


def _table_one(t: Transcription, stars: Iterable[str]) -> dict[str, tuple[Cell, Cell]]:
    return {s: (t.cell(table="I", star_id=s, quantity="x"), t.cell(table="I", star_id=s, quantity="y")) for s in stars}


def _sobral_coefficients(t: Transcription) -> dict[str, tuple[Cell, Cell]]:
    return {
        s: (
            t.cell(table="p304-conditions", star_id=s, quantity="Ex"),
            t.cell(table="p304-conditions", star_id=s, quantity="Ey"),
        )
        for s in SOBRAL_STARS
    }


def sobral_sun(t: Transcription) -> tuple[float, float]:
    """The Sun's centre on the Sobral plates, in Table I's units of 50' (the p. 304 coefficients are
    per unit deflection at 50', so k = 1)."""
    return sun_centre(
        {s: (x.number, y.number) for s, (x, y) in _table_one(t, SOBRAL_STARS).items()},
        {s: (ex.number, ey.number) for s, (ex, ey) in _sobral_coefficients(t).items()},
        1.0,
    )


def sobral_4in(t: Transcription) -> list[TranscribedRecord]:
    instrument = "sobral-4in"
    screw_cell = t.cell(table="text", quantity="screw_value")
    screw = screw_cell.number
    radius = t.cell(table="text", instrument=instrument, quantity="solar_radius")
    table_one = _table_one(t, SOBRAL_STARS)
    coeff = _sobral_coefficients(t)
    sun = sobral_sun(t)
    # The comparison plates' residuals hold no deflection (the eclipse plates' Table VIII rows show it).
    # A row is one eclipse plate less the mean of seven comparison plates, hence sqrt(1 + 1/7).
    residual_cells = [
        c
        for c in t.cells
        if c.table == "VIII-comparison"
        and c.plate_id in COMPARISON_PLATES
        and c.star_id in SOBRAL_STARS
        and c.quantity.startswith("residual_")
    ]
    pe = _probable_error([c.number for c in residual_cells]) * math.sqrt(1 + 1 / len(COMPARISON_PLATES))
    records = [
        _record(
            f"{DOCUMENT_ID}/{instrument}",
            [radius, screw_cell, *residual_cells],
            {"kind": "instrument", "instrument": instrument, "solar_radius_arcmin": radius.number},
            notes=f"probable error of one row {pe:.3f} arcsec, derived: 0.6745 x RMS of the "
            f"{len(residual_cells)} Table VIII comparison-plate residuals (p. 308) x sqrt(1 + 1/7)",
        )
    ]

    def d(kind: str, plate: str, star: str, axis: str) -> Cell:
        return t.cell(table=f"II-{kind}", plate_id=plate, star_id=star, quantity=axis)

    refraction = {
        p: {k: t.cell(table="p307-refraction", plate_id=p, quantity=f"refraction_aberration_{k}") for k in "aebd"}
        for p in ("eclipse", *COMPARISON_PLATES)
    }
    adopted = {
        p: (
            t.cell(table="p307-scale-orientation", plate_id=p, quantity="adopted_scale"),
            t.cell(table="p307-scale-orientation", plate_id=p, quantity="adopted_orientation"),
        )
        for p in (*ECLIPSE_PLATES, *COMPARISON_PLATES)
    }
    mean_ref = {k: float(np.mean([refraction[p][k].number for p in COMPARISON_PLATES])) for k in "aebd"}
    mean_scale = float(np.mean([adopted[p][0].number for p in COMPARISON_PLATES]))
    mean_orient = float(np.mean([adopted[p][1].number for p in COMPARISON_PLATES]))
    per_arcmin = screw / 50.0  # rev per 50' of position -> arcsec per arcmin
    for plate in ECLIPSE_PLATES:
        scale = adopted[plate][0].number - mean_scale
        ecl = {k: refraction["eclipse"][k].number - mean_ref[k] for k in "aebd"}
        records.append(
            _record(
                f"{DOCUMENT_ID}/{instrument}/{plate}",
                [
                    *adopted[plate],
                    *refraction["eclipse"].values(),
                    *(c for p in COMPARISON_PLATES for c in (*adopted[p], *refraction[p].values())),
                ],
                {
                    "kind": "plate",
                    "instrument": instrument,
                    "plate_id": plate,
                    "check_terms": {
                        "xx": (scale - ecl["a"]) * per_arcmin,
                        "xy": -ecl["b"] * per_arcmin,
                        "yx": -ecl["d"] * per_arcmin,
                        "yy": (scale - ecl["e"]) * per_arcmin,
                    },
                    "check_rotation": (adopted[plate][1].number - mean_orient) * per_arcmin,
                    "check_scale_sigma": 0.0,
                },
                notes="known terms: p. 307 refraction and aberration and adopted scale and orientation, "
                "eclipse plate minus the mean of the comparison plates",
            )
        )
        for star in SOBRAL_STARS:
            x, y = table_one[star]
            cells = [d("eclipse", plate, star, "Dx"), d("eclipse", plate, star, "Dy"), x, y, *coeff[star]]
            comparison = [d("comparison", c, star, a) for c in COMPARISON_PLATES for a in ("Dx", "Dy")]
            mean_c = {a: float(np.mean([c.number for c in comparison if c.quantity == a])) for a in ("Dx", "Dy")}
            records.append(
                _record(
                    f"{DOCUMENT_ID}/{instrument}/{plate}/{star}",
                    [*cells, *comparison],
                    {
                        "kind": "star",
                        "instrument": instrument,
                        "plate_id": plate,
                        "star_id": star,
                        "x_arcmin": (x.number - sun[0]) * 50.0,
                        "y_arcmin": (y.number - sun[1]) * 50.0,
                        "dx_arcsec": (cells[0].number - mean_c["Dx"]) * screw,
                        "dy_arcsec": (cells[1].number - mean_c["Dy"]) * screw,
                        "error_arcsec": pe,
                        "convention": "probable_error",
                    },
                    notes="Table II eclipse value minus the mean of the seven comparison plates, at the screw value "
                    "of p. 302; position from Table I, centred on the Sun found from the p. 304 coefficients",
                )
            )
    return records


def sobral_astro(t: Transcription, sun: tuple[float, float]) -> list[TranscribedRecord]:
    instrument = "sobral-astro"
    revolution = t.cell(table="IX", plate_id="mean", quantity="revolution")
    radius = t.cell(table="text", instrument="sobral-4in", quantity="solar_radius")
    refraction = t.cell(table="p312-text", quantity="e_refraction_aberration")
    # Every plate and star of p. 311 must be transcribed; only a printed dash means "not measured".
    printed = [t.cell(table="p311-Dy", plate_id=p, star_id=s, quantity="Dy") for p in ASTRO_PLATES for s in ASTRO_STARS]
    measured = [c for c in printed if c.value.strip() not in DASHES]
    plates = ASTRO_PLATES
    positions = _table_one(t, ASTRO_STARS)
    coeff_cells = {s: t.cell(table="p304-conditions", star_id=s, quantity="Ey") for s in positions}
    coeff_y = {s: c.number for s, c in coeff_cells.items()}
    # Per-plate solutions of Dy = f + d x + e y + a Ey, as the paper reduced each plate (p. 310).
    residuals: list[float] = []
    dof = 0
    for plate in plates:
        rows = [c for c in measured if c.plate_id == plate]
        a = np.array(
            [[1.0, positions[c.star_id][0].number, positions[c.star_id][1].number, coeff_y[c.star_id]] for c in rows]
        )
        o = np.array([c.number for c in rows]) * revolution.number
        beta = np.linalg.lstsq(a, o, rcond=None)[0]
        residuals.extend(o - a @ beta)
        dof += len(rows) - a.shape[1]
    pe = _probable_error(residuals, dof)
    records = [
        _record(
            f"{DOCUMENT_ID}/{instrument}",
            [radius, revolution, *measured, *(c for xy in positions.values() for c in xy), *coeff_cells.values()],
            {"kind": "instrument", "instrument": instrument, "solar_radius_arcmin": radius.number},
            notes=f"probable error of one measure {pe:.3f} arcsec, derived: 0.6745 x RMS of the residuals of the "
            f"per-plate solutions of the p. 311 values ({dof} degrees of freedom)",
        )
    ]
    for plate in plates:
        records.append(
            _record(
                f"{DOCUMENT_ID}/{instrument}/{plate}",
                [refraction, revolution],
                {
                    "kind": "plate",
                    "instrument": instrument,
                    "plate_id": plate,
                    "check_terms": {
                        "xx": 0.0,
                        "xy": 0.0,
                        "yx": 0.0,
                        "yy": refraction.number * revolution.number / 50.0,
                    },
                    "check_rotation": None,
                    "check_scale_sigma": 0.0,
                },
                notes="known term: the scale change from refraction and aberration, p. 312; orientation solved",
            )
        )
        for cell in (c for c in measured if c.plate_id == plate):
            x, y = positions[cell.star_id]
            records.append(
                _record(
                    f"{DOCUMENT_ID}/{instrument}/{plate}/{cell.star_id}",
                    [cell, x, y, revolution],
                    {
                        "kind": "star",
                        "instrument": instrument,
                        "plate_id": plate,
                        "star_id": cell.star_id,
                        "x_arcmin": (x.number - sun[0]) * 50.0,
                        "y_arcmin": (y.number - sun[1]) * 50.0,
                        "dx_arcsec": None,
                        "dy_arcsec": cell.number * revolution.number,
                        "error_arcsec": pe,
                        "convention": "probable_error",
                    },
                    notes="measured in declination only (p. 310)",
                )
            )
    return records


def principe(t: Transcription) -> list[TranscribedRecord]:
    instrument = "principe-astro"
    unit = t.cell(table="p322-text", quantity="unit")
    radius = t.cell(table="p322-text", quantity="solar_radius")
    kappa = t.cell(table="p322-text", quantity="kappa_full_einstein")
    pe_y = t.cell(table="p328-result", quantity="pe_y")
    pe = abs(pe_y.number)
    stars = ("11", "5", "4", "3", "6", "10")
    table_one = _table_one(t, stars)
    # The displacement coefficients, from the X-G1 tables (3/4 of Ex is printed) and W-D1 for star 10.
    coeff: dict[str, tuple[float, float]] = {}
    for s in ("11", "5", "4", "3", "6"):
        coeff[s] = (
            t.cell(table="p323-X-G1", star_id=s, quantity="3/4Ex").number * 4 / 3,
            t.cell(table="p323-X-G1", star_id=s, quantity="Ey").number,
        )
    coeff["10"] = (
        t.cell(table="p324-WD1-x", star_id="10", quantity="three_quarter_Ex").number * 4 / 3,
        t.cell(table="p324-WD1-y", star_id="10", quantity="Ey").number,
    )
    sun = sun_centre({s: (x.number, y.number) for s, (x, y) in table_one.items()}, coeff, kappa.number)
    records = [
        _record(
            f"{DOCUMENT_ID}/{instrument}",
            [radius, unit, kappa, pe_y],
            {"kind": "instrument", "instrument": instrument, "solar_radius_arcmin": radius.number},
            notes="comparison of plate U with K2 left out, as on p. 327",
        )
    ]
    for plate_id, y_table, y_quantity, plate_stars in PRINCIPE:
        theta = t.cell(plate_id=plate_id, quantity="theta")
        records.append(
            _record(
                f"{DOCUMENT_ID}/{instrument}/{plate_id}",
                [theta, unit],
                {
                    "kind": "plate",
                    "instrument": instrument,
                    "plate_id": plate_id,
                    "check_terms": {"xx": 0.0, "xy": 0.0, "yx": 0.0, "yy": 0.0},
                    "check_rotation": theta.number * unit.number / 5.0,
                    "check_scale_sigma": 0.0,
                },
                notes="calculated plate constants, check-plate scale included, already removed in Delta_1; "
                "orientation from this comparison's x measures",
            )
        )
        for cell in (t.cell(table=y_table, star_id=s, quantity=y_quantity) for s in plate_stars):
            x, y = table_one[cell.star_id]
            records.append(
                _record(
                    f"{DOCUMENT_ID}/{instrument}/{plate_id}/{cell.star_id}",
                    [cell, x, y, unit],
                    {
                        "kind": "star",
                        "instrument": instrument,
                        "plate_id": plate_id,
                        "star_id": cell.star_id,
                        "x_arcmin": (x.number - sun[0]) * 50.0,
                        "y_arcmin": (y.number - sun[1]) * 50.0,
                        "dx_arcsec": None,
                        "dy_arcsec": cell.number * unit.number,
                        "error_arcsec": pe,
                        "convention": "probable_error",
                    },
                    notes='Delta_1 y in units of 0".003; the x measures fixed the orientation (pp. 322-325)',
                )
            )
    return records


def records(t: Transcription) -> list[TranscribedRecord]:
    return [*sobral_4in(t), *sobral_astro(t, sobral_sun(t)), *principe(t)]


def verifications(t: Transcription, records: Sequence[TranscribedRecord]) -> list[VerificationRecord]:
    """A matching second pass for each record whose every cited cell has a recorded check in the
    transcription's check column; a record with any unchecked cell stays unverified."""
    checks = {c.key: _check(c.check) for c in t.cells}
    cites = {r.record_id: list(r.fields["printed"]) for r in records}
    out = []
    for r in records:
        # A star is fitted with its plate's known terms and its instrument's error, so it is verified
        # only when the cells behind those are checked too.
        parts = r.record_id.split("/")
        keys = [k for n in range(len(parts), 1, -1) for k in cites.get("/".join(parts[:n]), [])]
        found = [checks.get(k) for k in keys]
        if not found or not all(found):
            continue
        marks = [m for m in found if m is not None]
        who = "+".join(sorted({m["who"] for m in marks}))
        on = max(m["on"] for m in marks)
        at = datetime.fromisoformat(on).replace(tzinfo=UTC)
        out.append(VerificationRecord(r.record_id, who, at, True, notes="every cited cell checked against the page"))
    return out


def bundle(path: Path) -> dict[str, Any]:
    t = Transcription.read(path)
    recs = records(t)
    passes = verifications(t, recs)
    return bundle_from_records(
        recs,
        passes,
        {"document": DOCUMENT_ID, "source": SOURCE, "doi": "10.1098/rsta.1920.0009", "transcription": path.name},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcription", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    doc = bundle(args.transcription)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for s in doc["instrument_sets"]:
        n = sum(len(p["stars"]) for p in s["plates"])
        print(f"{s['instrument']}: {len(s['plates'])} plates, {n} star rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
