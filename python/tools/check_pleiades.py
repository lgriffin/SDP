"""Check the sweep found the Pleiades once, as a known cluster, before the whole-sky sweep is left running.

    sdp discover sweep --bands 57
    sdp discover report
    uv run python tools\\check_pleiades.py                       # reads <SDP data>\\sweep\\gaia-dr3\\candidates.csv
    uv run python tools\\check_pleiades.py --without-benchmarks  # the sweep ran with --without-benchmarks
    uv run python tools\\check_pleiades.py --csv PATH            # another sweep's candidates.csv

Band 57 (Dec +24 to +26 degrees) holds the Pleiades, the nearest rich open cluster and one every known-cluster
catalogue lists. A sweep whose settings work must report it exactly once: not missing, not cut into pieces by
tiles, and not listed again by a second distance shell. Each check prints PASS or FAIL with the value it saw;
the exit status is 1 if any check fails, so the full sweep is only worth starting after an exit status of 0.

A Pleiades row is any candidate within 3 degrees of the cluster's centre whose mean parallax and proper motion
match the cluster's: parallax 7.36 mas and proper motion (+20.0, -45.5) mas/yr (Gaia DR3 values, to within
the windows below, which are wide enough for a sweep's mean over its own members).

It first checks that every tile of band 57 is in the sweep's ``tiles`` folder, in every distance shell, so a
band stopped half way (or with failed tiles) cannot pass on the tiles it did finish. The expected tiles come
from ``sdp_stellar.sweep``, so run it with ``uv run`` from the ``python`` folder of a checkout.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

PLEIADES_RA_DEG = 56.75
PLEIADES_DEC_DEG = 24.12
PLEIADES_PARALLAX_MAS = 7.36
PLEIADES_PM_MASYR = (20.0, -45.5)

RADIUS_DEG = 3.0  # the cluster's core plus its corona, as a sweep candidate's centre can sit off the core
PARALLAX_WINDOW_MAS = 1.0
PM_WINDOW_MASYR = 6.0
MIN_SIGMA = 3.0  # the sweep's own rejection threshold (sweep.json significance)
MIN_MEMBERS = 50  # far below the hundreds of Pleiades stars with G < 18; a fragment of a few dozen fails
BAND_DEC_DEG = (24.0, 26.0)  # band 57 of the prepared 2-degree bands


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def default_csv(env: Mapping[str, str]) -> Path:
    """The default sweep's candidates.csv under SDP's data folder, found as ``sdp discover`` finds it."""
    for key in ("SDP_DATA", "SDP_DESKTOP_DATA"):
        if env.get(key):
            return Path(env[key]) / "sweep" / "gaia-dr3" / "candidates.csv"
    if env.get("SDP_ARTIFACT_ROOT"):
        return Path(env["SDP_ARTIFACT_ROOT"]).parent / "sweep" / "gaia-dr3" / "candidates.csv"
    raise SystemExit("SDP_DATA is not set: open the SDP Command Prompt, or give --csv PATH")


def _float(row: Mapping[str, str], key: str) -> float | None:
    text = (row.get(key) or "").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation (haversine), in degrees."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def is_pleiades(row: Mapping[str, str]) -> bool:
    ra, dec = _float(row, "ra_deg"), _float(row, "dec_deg")
    plx = _float(row, "parallax_mas")
    pmra, pmdec = _float(row, "pm_ra_cosdec_masyr"), _float(row, "pm_dec_masyr")
    if None in (ra, dec, plx, pmra, pmdec):
        return False
    assert ra is not None and dec is not None and plx is not None and pmra is not None and pmdec is not None
    if separation_deg(ra, dec, PLEIADES_RA_DEG, PLEIADES_DEC_DEG) > RADIUS_DEG:
        return False
    if abs(plx - PLEIADES_PARALLAX_MAS) > PARALLAX_WINDOW_MAS:
        return False
    return math.hypot(pmra - PLEIADES_PM_MASYR[0], pmdec - PLEIADES_PM_MASYR[1]) <= PM_WINDOW_MASYR


def check(rows: Sequence[Mapping[str, str]], fieldnames: Sequence[str], *, with_benchmarks: bool) -> list[Check]:
    found = [r for r in rows if is_pleiades(r)]
    ids = ", ".join(r.get("candidate_id") or "?" for r in found)
    if not found:
        detail = "no candidate matches the Pleiades' position, parallax and motion"
    elif len(found) == 1:
        detail = f"candidate {ids}"
    else:
        detail = f"{len(found)} candidates match, so the cluster was cut up or listed twice ({ids})"
    checks = [Check("found once", len(found) == 1, detail)]
    if len(found) != 1:
        return checks
    row = found[0]
    status = (row.get("status") or "").strip()
    if with_benchmarks:
        matches = (row.get("matches") or "").strip()
        checks.append(Check("labelled known", status == "known", f"status {status or '(blank)'}"))
        checks.append(Check("matched in a catalogue", bool(matches), f"matches: {matches or '(none)'}"))
    else:
        checks.append(Check("not rejected", status not in ("", "rejected"), f"status {status or '(blank)'}"))
    if "significance_sigma" not in fieldnames:
        checks.append(Check("significance", False, "no significance_sigma column: this SDP predates #149"))
    else:
        sigma = _float(row, "significance_sigma")
        checks.append(
            Check(
                f"significance above {MIN_SIGMA:g} sigma",
                sigma is not None and sigma > MIN_SIGMA,
                "not tested" if sigma is None else f"{sigma:.1f} sigma",
            )
        )
    members = _float(row, "members")
    checks.append(
        Check(
            f"at least {MIN_MEMBERS} members",
            members is not None and members >= MIN_MEMBERS,
            "unknown" if members is None else f"{members:.0f} members",
        )
    )
    return checks


def expected_tiles(dec_lo: float = BAND_DEC_DEG[0], dec_hi: float = BAND_DEC_DEG[1]) -> list[str]:
    """Every tile id `sdp discover sweep` searches for declinations [dec_lo, dec_hi), in every shell."""
    from sdp_stellar.sweep import bands, load_settings, scales, tile, tiles_in_band

    out = []
    for scale in scales(load_settings()):
        for band in range(bands(scale.tile_deg)):
            lo = -90.0 + band * scale.tile_deg
            if lo < dec_hi and lo + scale.tile_deg > dec_lo:
                n = tiles_in_band(band, scale.tile_deg)
                out += [tile(band, i, scale.tile_deg, scale.name).tile_id for i in range(n)]
    return out


def tiles_check(folder: Path, expected: Sequence[str]) -> Check:
    missing = [t for t in expected if not (folder / f"{t}.json").is_file()]
    if not missing:
        return Check("band swept", True, f"all {len(expected)} tiles in {folder}")
    shown = ", ".join(missing[:5]) + (", ..." if len(missing) > 5 else "")
    return Check(
        "band swept",
        False,
        f"{len(missing)} of {len(expected)} tiles missing ({shown}): run `sdp discover sweep --bands 57` again",
    )


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, help="candidates.csv to read (default: the gaia-dr3 sweep's)")
    parser.add_argument(
        "--without-benchmarks",
        action="store_true",
        help="The sweep ran with --without-benchmarks, so nothing can be labelled known",
    )
    args = parser.parse_args(argv)
    path = args.csv or default_csv(os.environ if env is None else env)
    if not path.is_file():
        print(f"FAIL  {path} does not exist: run `sdp discover sweep --bands 57`, then `sdp discover report`")
        return 1
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    try:
        expected = expected_tiles()
    except ImportError:
        print("FAIL  SDP's Python packages are not importable: run this with `uv run` from the python folder")
        return 1
    checks = [tiles_check(path.parent / "tiles", expected)]
    checks += check(rows, fieldnames, with_benchmarks=not args.without_benchmarks)
    print(f"Pleiades check on {path} ({len(rows)} candidates)")
    for c in checks:
        print(f"{'PASS' if c.passed else 'FAIL'}  {c.name}: {c.detail}")
    ok = all(c.passed for c in checks)
    print("All checks passed: the full sweep is worth starting." if ok else "Fix the failures before the full sweep.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
