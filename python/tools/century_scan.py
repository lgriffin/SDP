"""Scan one field for century-long brightness trends: DASCH plate light curves judged by the
century-trend test (INT-027), with Gaia DR3 synthetic Johnson B as the modern anchor.

    uv run python tools/century_scan.py                                   # M67, 1.1 degrees, B < 15
    uv run python tools/century_scan.py --ra 130.1 --dec 19.67 --label praesepe
    uv run python tools/century_scan.py --out D:\\SDP\\reports\\century    # where the cache and results go

This is the scan end of the funnel, outside an investigation: it talks to DASCH and the Gaia archive
directly, so it can cover a field of a thousand stars in about half an hour and be rerun from its cache
in a minute. Everything it fetched is kept under ``<out>/<label>/`` (the DASCH catalogue cones, one light
curve per star, the Gaia cone), and it writes ``results.csv`` (one row per scored star) and
``summary.json`` (the field's reference stars, sigma_sys and the null's measured false-positive rate).

A candidate is worth following up only when it is significant at ``--alpha``, unblended, and its trend
reaches 2016 at the magnitude Gaia measured (``|anchor_z| < 3``): ``results.csv`` marks those ``survivor``.

DASCH refuses Python's default User-Agent (HTTP 403), so every request names this tool.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

from sdp.kernel.domain.investigation import significant
from sdp_investigate.century import CenturyTrend

DASCH = "https://api.starglass.cfa.harvard.edu/public/dasch/dr7/"
GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
USER_AGENT = "sdp-century-scan/1.0 (+https://github.com/lgriffin/SDP)"
#: DASCH's querycat times out on cones much over 30 arcmin, so a field is tiled with cones this size.
CONE_ARCSEC = 1800.0
#: AFLAGS bits daschlab's standard cleaning rejects (as sdp.adapters.archives.dasch).
BAD_AFLAGS = (1 << 6) | (1 << 11) | (1 << 12) | (1 << 13) | (1 << 15)
MJD_OFFSET = 2400000.5


def request(
    url: str, body: bytes, content_type: str = "application/json", tries: int = 4, timeout: float = 180.0
) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Content-Type": content_type}
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=timeout) as answer:
                return bytes(answer.read())
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def dasch(endpoint: str, payload: dict[str, Any]) -> list[str]:
    """DASCH's answer as it sent it: a JSON list of CSV lines, header first (cached as is)."""
    data = json.loads(request(DASCH + endpoint, json.dumps(payload).encode()))
    if not isinstance(data, list):
        raise RuntimeError(f"DASCH {endpoint}: {str(data)[:200]}")
    return data


def cached(path: Path, fetch: Any) -> Any:
    if path.exists():
        return json.loads(path.read_text())
    value = fetch()
    path.write_text(json.dumps(value))
    return value


def cones(ra: float, dec: float, radius_deg: float) -> list[tuple[float, float]]:
    """The centre and, for a field wider than one cone, a ring of six cones around it whose outer
    edges reach the field's radius (a field is at most three cones across)."""
    out = [(ra, dec)]
    if radius_deg * 3600.0 > CONE_ARCSEC:
        ring = radius_deg - CONE_ARCSEC / 3600.0
        out += [
            (
                ra + ring * math.cos(math.radians(a)) / math.cos(math.radians(dec)),
                dec + ring * math.sin(math.radians(a)),
            )
            for a in range(0, 360, 60)
        ]
    return out


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def number(text: str | None) -> float:
    try:
        return float(text) if text not in (None, "") else math.nan
    except ValueError:
        return math.nan


def detected(row: dict[str, str]) -> bool:
    """A light-curve row is a detection when DASCH matched it to the star or to a source on the plate."""
    return any(
        math.isfinite(v) and v != 0 for v in (number(row.get("gsc_bin_index")), number(row.get("sextractor_number")))
    )


def clean_points(lines: list[str]) -> np.ndarray:
    kept = []
    for r in csv.DictReader(lines):
        if not detected(r):
            continue
        flags = number(r.get("aflags"))
        if math.isfinite(flags) and int(flags) & BAD_AFLAGS:
            continue
        mag, err, jd = number(r.get("magcal_magdep")), number(r.get("magcal_magdep_rms")), number(r.get("date_jd"))
        if math.isfinite(mag) and math.isfinite(err) and math.isfinite(jd) and 0 < err < 99:
            kept.append((jd - MJD_OFFSET, mag, err))
    return np.array(sorted(kept), dtype=float).reshape(-1, 3)


def gaia_cone(ra: float, dec: float, radius_deg: float, g_max: float) -> list[dict[str, str]]:
    query = (
        "SELECT g.source_id, g.ra, g.dec, g.pmra, g.pmdec, g.phot_g_mean_mag, g.bp_rp, g.ruwe, "
        "g.phot_variable_flag, g.non_single_star, s.b_jkc_mag FROM gaiadr3.gaia_source g "
        "LEFT JOIN gaiadr3.synthetic_photometry_gspc s ON s.source_id = g.source_id "
        f"WHERE 1 = CONTAINS(POINT('ICRS', g.ra, g.dec), CIRCLE('ICRS', {ra}, {dec}, {radius_deg + 0.05})) "
        f"AND g.phot_g_mean_mag < {g_max}"
    )
    form = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query})
    return list(
        csv.DictReader(
            io.StringIO(request(GAIA_TAP, form.encode(), "application/x-www-form-urlencoded", timeout=600).decode())
        )
    )


def crossmatch(stars: list[dict[str, str]], gaia: list[dict[str, str]]) -> dict[str, int]:
    """Each DASCH (APASS) star's Gaia row: the brightest Gaia source within 3 arcsec of its position,
    Gaia's position taken back to 2000."""
    from scipy.spatial import cKDTree

    def unit(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
        r, d = np.radians(ra), np.radians(dec)
        return np.column_stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])

    gra = np.array([number(g["ra"]) for g in gaia])
    gdec = np.array([number(g["dec"]) for g in gaia])
    pmra = np.nan_to_num(np.array([number(g["pmra"]) for g in gaia]))
    pmdec = np.nan_to_num(np.array([number(g["pmdec"]) for g in gaia]))
    gmag = np.array([number(g["phot_g_mean_mag"]) for g in gaia])
    tree = cKDTree(unit(gra - pmra * 16 / 3.6e6 / np.cos(np.radians(gdec)), gdec - pmdec * 16 / 3.6e6))
    chord = 2 * math.sin(math.radians(3 / 3600) / 2)
    out = {}
    for s in stars:
        near = tree.query_ball_point(unit(np.array([number(s["ra_deg"])]), np.array([number(s["dec_deg"])]))[0], chord)
        if near:
            out[s["ref_number"]] = int(min(near, key=lambda j: gmag[j]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ra", type=float, default=132.846)
    parser.add_argument("--dec", type=float, default=11.814)
    parser.add_argument("--radius-deg", type=float, default=1.1)
    parser.add_argument("--b-max", type=float, default=15.0, help="APASS B limit of the stars fetched")
    parser.add_argument("--min-matches", type=int, default=100, help="DASCH plates a star must appear on")
    parser.add_argument("--g-max", type=float, default=17.0, help="Gaia G limit of the stars that can blend")
    parser.add_argument("--label", default="m67")
    parser.add_argument("--out", type=Path, default=Path("century-scan"))
    parser.add_argument("--alpha", type=float, default=0.001)
    parser.add_argument("--null-trials", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    folder = args.out / args.label
    (folder / "lc").mkdir(parents=True, exist_ok=True)
    stars: dict[str, dict[str, str]] = {}
    for i, (ra, dec) in enumerate(cones(args.ra, args.dec, args.radius_deg)):
        payload = {"refcat": "apass", "ra_deg": ra, "dec_deg": dec, "radius_arcsec": CONE_ARCSEC}
        for row in csv.DictReader(cached(folder / f"querycat-{i}.json", lambda p=payload: dasch("querycat", p))):
            mag = number(row.get("stdmag"))
            inside = (
                separation_deg(args.ra, args.dec, number(row.get("ra_deg")), number(row.get("dec_deg")))
                <= args.radius_deg
            )
            if (
                inside
                and math.isfinite(mag)
                and mag < args.b_max
                and number(row.get("num_matches")) >= args.min_matches
            ):
                stars[row["ref_number"]] = row
    print(f"{len(stars)} DASCH stars with B < {args.b_max}", flush=True)

    def light_curve(row: dict[str, str]) -> list[str]:
        payload = {"refcat": "apass", "gsc_bin_index": int(row["gsc_bin_index"]), "ref_number": int(row["ref_number"])}
        return list(cached(folder / "lc" / f"{row['ref_number']}.json", lambda: dasch("lightcurve", payload)))

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        curves_raw = dict(zip(stars, pool.map(light_curve, stars.values()), strict=True))
    gaia = cached(folder / "gaia.json", lambda: gaia_cone(args.ra, args.dec, args.radius_deg, args.g_max))
    match = crossmatch(list(stars.values()), gaia)
    print(f"{len(match)} matched to Gaia; {len(gaia)} Gaia stars can blend", flush=True)

    listed = [(number(g["ra"]), number(g["dec"]), number(g["phot_g_mean_mag"])) for g in gaia]
    rows, curves = [], []
    for ref, raw in curves_raw.items():
        j = match.get(ref)
        if j is None or not math.isfinite(number(gaia[j]["bp_rp"])):
            continue
        points = clean_points(raw)
        if len(points) == 0:
            continue
        g = gaia[j]
        rows.append(
            (
                ref,
                number(g["ra"]),
                number(g["dec"]),
                number(g["phot_g_mean_mag"]),
                number(g["bp_rp"]),
                number(g["b_jkc_mag"]),
                j,
            )
        )
        curves.append((points[:, 0], points[:, 1], points[:, 2]))

    test = CenturyTrend()
    features = test.features_from_curves(rows, curves, listed, {})
    scored = test.evaluate(features, {}, np.random.default_rng(0))
    rng = np.random.default_rng(1)
    examined = positives = 0
    for _ in range(args.null_trials):
        null = test.evaluate(test.null_features(features, {}, rng), {}, rng)
        examined += len(null)
        positives += sum(significant(s.p_value, args.alpha) for s in null)
    null_rate = positives / examined if examined else math.nan

    by_ref = {r[0]: r for r in rows}
    with open(folder / "results.csv", "w", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(
            ["dasch_ref", "gaia_source_id", "g_mag", "bp_rp", "slope_mag_per_century", "peer_relative_slope", "error",
             "z", "p", "points", "blend_fraction", "anchor_offset_mag", "anchor_z", "survivor"]
        )  # fmt: skip
        survivors = 0
        for s in sorted(scored, key=lambda s: s.p_value or 1.0):
            d, g = s.details, gaia[by_ref[s.subject.key][6]]
            survivor = (
                significant(s.p_value, args.alpha)
                and d["blend_fraction"] <= 0.1
                and abs(d.get("anchor_z", math.inf)) < 3
            )
            survivors += survivor
            out.writerow(
                [s.subject.key, g["source_id"], g["phot_g_mean_mag"], g["bp_rp"], d["slope_mag_per_century"],
                 s.effect_size, s.uncertainty, s.statistic, s.p_value, d["points"], d["blend_fraction"],
                 d.get("anchor_offset_mag", ""), d.get("anchor_z", ""), survivor]
            )  # fmt: skip
    flagged = sum(significant(s.p_value, args.alpha) for s in scored)
    summary = {
        "field": {"ra": args.ra, "dec": args.dec, "radius_deg": args.radius_deg, "b_max": args.b_max},
        "dasch_stars": len(stars),
        "scored": len(scored),
        "reference_stars": scored[0].details["reference_stars"] if scored else 0,
        "sigma_sys_mag_per_century": scored[0].details["sigma_sys"] if scored else None,
        "alpha": args.alpha,
        "flagged": flagged,
        "survivors": survivors,
        "null": {"trials": args.null_trials, "examined": examined, "false_positive_rate": null_rate},
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
