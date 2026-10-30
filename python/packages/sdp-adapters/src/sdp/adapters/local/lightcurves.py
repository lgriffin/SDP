"""Light curves of the Gaia stars in a target's field, from a local source's subs (#71).

For each solved, dated light frame that is not graded poor:

1. Each Gaia star (G 9 to 15 by default) is moved along its proper motion to the frame's date and placed
   on the frame with the frame's own solution, so no star finding is needed.
2. Aperture photometry on the green pixels of the Bayer frame (the colour closest to Gaia's G band, two
   of every four pixels), with the sky taken from an annulus around each star. A star too near the edge
   or with a pixel near saturation is left out of that frame.
3. Each frame's zero point and colour term (against Gaia BP-RP) are fitted on comparison stars with
   outliers clipped, so cloud, haze and airmass cancel. The comparison stars are chosen twice: first all
   clean stars of G 10 to 14, then only the steadier half of those, as measured on the first pass.

The curves go to ``<data>/lightcurves/<source>/<target>.parquet`` (one row per star per frame) beside a
per-night average and a summary of each star's scatter against stars of its brightness. The frames are
only read.
"""

from __future__ import annotations

import io
import json
import math
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sdp.adapters.local.catalogue import SOLVED, _separation
from sdp.adapters.local.quality import POOR
from sdp.adapters.local.solver import read_frame, sky_to_pixel
from sdp.adapters.local.solving import LocalSolver
from sdp.adapters.local.sources import LocalSource

#: Gaia's reference epoch for DR3 positions, Julian year
GAIA_EPOCH = 2016.0
#: pixels at or above this share of the camera's largest value count as saturated
SATURATION = 0.9
#: comparison stars: Gaia G range, the fewest per frame, the clip in sigma
COMPARISON_G = (10.0, 14.0)
MIN_COMPARISONS = 5
CLIP = 3.0
#: a star is listed as a variable candidate with at least this many points and this excess scatter
MIN_POINTS, EXCESS = 20, 3.0

CURVE_SCHEMA = pa.schema(
    [
        ("source_id", pa.int64()),
        ("path", pa.string()),
        ("mjd", pa.float64()),
        ("night", pa.string()),
        ("mag", pa.float64()),
        ("mag_err", pa.float64()),
        ("instrumental", pa.float64()),
        ("zero_point", pa.float64()),
        ("colour_term", pa.float64()),
        ("comparison", pa.bool_()),
    ]
)


@dataclass(frozen=True)
class Field:
    """The Gaia stars of a target's field: arrays of equal length."""

    source_id: np.ndarray
    ra: np.ndarray
    dec: np.ndarray
    pmra: np.ndarray  # mas/yr, along RA times cos(dec); 0 where Gaia has none
    pmdec: np.ndarray
    g: np.ndarray
    bp_rp: np.ndarray  # NaN where Gaia has none

    @classmethod
    def from_table(cls, table: pa.Table) -> Field:
        def column(name: str, fill: float) -> np.ndarray:
            values = np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=float)
            return np.where(np.isfinite(values), values, fill)

        return cls(
            np.asarray(table.column("source_id").to_numpy(zero_copy_only=False), dtype=np.int64),
            column("ra", math.nan),
            column("dec", math.nan),
            column("pmra", 0.0),
            column("pmdec", 0.0),
            column("phot_g_mean_mag", math.nan),
            column("bp_rp", math.nan),
        )

    def at(self, jyear: float) -> tuple[np.ndarray, np.ndarray]:
        years = jyear - GAIA_EPOCH
        dec = self.dec + self.pmdec * years / 3.6e6
        ra = self.ra + self.pmra * years / 3.6e6 / np.cos(np.radians(self.dec))
        return ra % 360.0, dec


def _greens(data: np.ndarray, bayer: str) -> tuple[np.ndarray, np.ndarray]:
    pattern = bayer.strip().upper()
    if len(pattern) != 4 or pattern.count("G") != 2:
        raise ValueError(f"Bayer pattern {bayer!r} is not a 2 x 2 pattern with two greens")
    h, w = (data.shape[0] // 2) * 2, (data.shape[1] // 2) * 2
    cells = [data[r:h:2, c:w:2] for r, c in ((0, 0), (0, 1), (1, 0), (1, 1))]
    picked = [cell for cell, colour in zip(cells, pattern, strict=True) if colour == "G"]
    return picked[0], picked[1]


def green(data: np.ndarray, bayer: str) -> np.ndarray:
    """The mean of each 2 x 2 colour cell's green pixels: one value per cell."""
    first, second = _greens(data, bayer)
    return np.asarray((first + second) / 2.0, dtype=np.float32)


def bayer_pattern(header: dict[str, Any]) -> str | None:
    """BAYERPAT as it lies from the first pixel: an odd XBAYROFF swaps its columns, an odd YBAYROFF its rows."""
    pattern = str(header.get("BAYERPAT") or "").strip().upper()
    if len(pattern) != 4:
        return pattern or None
    if int(header.get("XBAYROFF") or 0) % 2:
        pattern = pattern[1] + pattern[0] + pattern[3] + pattern[2]
    if int(header.get("YBAYROFF") or 0) % 2:
        pattern = pattern[2:] + pattern[:2]
    return pattern


def _saturation(header: dict[str, Any]) -> float:
    """SATURATE, or the top of the integer type: unsigned when BZERO is 2^(BITPIX-1), as it is for the
    usual 16-bit camera file, and signed otherwise."""
    if isinstance(header.get("SATURATE"), int | float):
        return float(header["SATURATE"])
    bits = int(header.get("BITPIX", 16))
    if bits <= 0:
        return math.inf
    unsigned = bits == 8 or header.get("BZERO") == 2 ** (bits - 1)
    return float(2**bits - 1 if unsigned else 2 ** (bits - 1) - 1)


def measure_frame(
    data: np.ndarray, header: dict[str, Any], bayer: str, x: np.ndarray, y: np.ndarray, fwhm_px: float
) -> tuple[np.ndarray, np.ndarray]:
    """Sky-subtracted green flux and its error at raw FITS pixels (x, y); NaN where a star is off the
    frame, crowded by the edge, or touches saturation."""
    from astropy.stats import SigmaClip
    from photutils.aperture import ApertureStats, CircularAnnulus, CircularAperture, aperture_photometry

    first, second = _greens(data, bayer)
    cells = np.asarray((first + second) / 2.0, dtype=np.float32)
    brightest = np.maximum(first, second)  # a saturated green is not hidden by its dimmer partner
    # raw 0-based pixel p lies in cell p // 2, whose centre is raw 2c + 0.5: cell coordinate (p - 0.5) / 2
    cx, cy = (x - 1 - 0.5) / 2, (y - 1 - 0.5) / 2
    radius = max(1.5, 1.5 * fwhm_px / 2)
    inner, outer = radius + 2.0, radius + 6.0
    height, width = cells.shape
    on = (cx > outer) & (cy > outer) & (cx < width - 1 - outer) & (cy < height - 1 - outer)
    flux = np.full(x.size, math.nan)
    err = np.full(x.size, math.nan)
    if not on.any():
        return flux, err
    positions = np.column_stack([cx[on], cy[on]])
    apertures = CircularAperture(positions, r=radius)
    annuli = CircularAnnulus(positions, r_in=inner, r_out=outer)
    sky = ApertureStats(cells, annuli, sigma_clip=SigmaClip(sigma=3.0))
    peak = ApertureStats(brightest, apertures).max
    total = np.asarray(aperture_photometry(cells, apertures)["aperture_sum"], dtype=float)
    area = math.pi * radius**2
    background = np.asarray(sky.median, dtype=float)
    spread = np.asarray(sky.std, dtype=float)
    count = np.asarray(getattr(sky.sum_aper_area, "value", sky.sum_aper_area), dtype=float)
    net = total - background * area
    # the sky's own scatter, its uncertainty, and the star's photon noise taking one count as one electron
    variance = area * spread**2 * (1 + area / np.maximum(count, 1.0)) + np.maximum(net, 0.0)
    clean = np.asarray(peak, dtype=float) < SATURATION * _saturation(header)
    flux[on] = np.where(clean, net, math.nan)
    err[on] = np.where(clean, np.sqrt(variance), math.nan)
    return flux, err


def _fit_zero_point(
    g: np.ndarray, colour: np.ndarray, instrumental: np.ndarray, err: np.ndarray, use: np.ndarray
) -> tuple[float, float, np.ndarray] | None:
    """G - instrumental = zp + c (BP - RP), weighted, clipped at CLIP sigma until it settles."""
    keep = use & np.isfinite(instrumental) & np.isfinite(colour) & np.isfinite(g)
    for _ in range(5):
        if keep.sum() < MIN_COMPARISONS:
            return None
        design = np.column_stack([np.ones(keep.sum()), colour[keep]])
        w = 1 / np.maximum(err[keep], 1e-3)
        coeffs, *_ = np.linalg.lstsq(design * w[:, None], (g - instrumental)[keep] * w, rcond=None)
        residual = g - instrumental - (coeffs[0] + coeffs[1] * colour)
        sigma = 1.4826 * float(np.median(np.abs(residual[keep])))
        new = keep & (np.abs(residual) <= max(CLIP * sigma, 0.01))
        if (new == keep).all():
            break
        keep = new
    return float(coeffs[0]), float(coeffs[1]), keep


def _night(mjd: float) -> str:
    from datetime import UTC, datetime, timedelta

    when = datetime.fromtimestamp((mjd - 40587.0) * 86400.0, tz=UTC)
    return (when - timedelta(hours=12)).date().isoformat()


def file_stem(target: str) -> str:
    """A target's name as a file name: anything but letters, digits, space, ``.``, ``_``, ``+`` and ``-``
    becomes ``_``, and it never starts with a dot (so ``NGC 7000/IC 5070`` stays in the folder)."""
    stem = "".join(c if c.isascii() and (c.isalnum() or c in " ._+-") else "_" for c in target)
    return stem.strip(" ").lstrip(".") or "_"


@dataclass
class LightCurves:
    source: LocalSource
    data: Path
    target: str

    @property
    def root(self) -> Path:
        return self.source.output_dir(self.data, "lightcurves")

    @property
    def path(self) -> Path:
        return self.root / f"{file_stem(self.target)}.parquet"

    @property
    def nights_path(self) -> Path:
        return self.root / f"{file_stem(self.target)}-nights.parquet"

    @property
    def stars_path(self) -> Path:
        return self.root / f"{file_stem(self.target)}-stars.json"

    @staticmethod
    def frames_of(rows: Iterable[dict[str, Any]], target: str) -> list[dict[str, Any]]:
        """The catalogue rows that go into the curves: the target's solved, dated lights not graded poor."""
        return [
            r for r in rows
            if r["target"] == target and r["kind"] == "light" and r["status"] == SOLVED
            and r["mjd"] is not None and r["grade"] != POOR
        ]  # fmt: skip

    def build(
        self,
        rows: Iterable[dict[str, Any]],
        field: Field,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Measure each of the target's frames (catalogue rows), calibrate, and write the curves."""
        solver = LocalSolver(self.source, self.data, None, lambda _c: None)  # type: ignore[arg-type,return-value]
        measured: list[dict[str, Any]] = []
        for frame in self.frames_of(rows, self.target):
            solution = json.loads(solver.solution_path(frame["path"]).read_text("utf-8"))
            try:
                data, header = read_frame(io.BytesIO((self.source.path / frame["path"]).read_bytes()))
            except OSError:  # moved or deleted since it was catalogued: leave it out
                continue
            bayer = bayer_pattern(header)
            if data.ndim != 2 or not bayer:
                continue
            jyear = 2000.0 + (frame["mjd"] - 51544.5) / 365.25
            ra, dec = field.at(jyear)
            x, y = sky_to_pixel(solution["wcs"], ra, dec)
            fwhm_px = float((solution.get("quality") or {}).get("fwhm_arcsec") or 6.0) / float(solution["scale_arcsec"])
            flux, err = measure_frame(data, header, bayer, x, y, fwhm_px)
            seconds = float(frame["exposure_s"] or 1.0)
            ok = flux > 0
            instrumental = np.where(ok, -2.5 * np.log10(np.where(ok, flux, 1.0) / seconds), math.nan)
            mag_err = np.where(ok, 1.0857 * err / np.where(ok, flux, 1.0), math.nan)
            measured.append({"path": frame["path"], "mjd": float(frame["mjd"]), "inst": instrumental, "err": mag_err})
            if progress is not None:
                progress(frame["path"])
        return self._calibrate(measured, field)

    def _calibrate(self, measured: list[dict[str, Any]], field: Field) -> dict[str, Any]:
        candidates = (field.g >= COMPARISON_G[0]) & (field.g <= COMPARISON_G[1]) & np.isfinite(field.bp_rp)
        use = candidates.copy()
        fits: list[tuple[float, float, np.ndarray] | None] = []
        for round_ in range(2):
            fits = [_fit_zero_point(field.g, field.bp_rp, m["inst"], m["err"], use) for m in measured]
            if round_ == 1:
                break
            # the steadier half of the comparison stars, by their scatter on the first pass
            mags = np.array(
                [m["inst"] + f[0] + f[1] * field.bp_rp if f else np.full(field.g.size, math.nan)
                 for m, f in zip(measured, fits, strict=True)]
            )  # fmt: skip
            with warnings.catch_warnings(), np.errstate(all="ignore"):
                warnings.simplefilter("ignore", RuntimeWarning)  # a star on no frame has no scatter
                scatter = np.nanstd(mags, axis=0) if len(mags) else np.full(field.g.size, math.nan)
            steady = candidates & np.isfinite(scatter)
            if steady.sum() >= 2 * MIN_COMPARISONS:
                use = steady & (scatter <= np.nanmedian(scatter[steady]))
        rows = []
        for m, f in zip(measured, fits, strict=True):
            if f is None:
                continue
            zp, c, kept = f
            mag = m["inst"] + zp + c * np.where(np.isfinite(field.bp_rp), field.bp_rp, 0.0)
            night = _night(m["mjd"])
            for i in np.flatnonzero(np.isfinite(mag) & np.isfinite(m["err"])):
                rows.append(
                    {"source_id": int(field.source_id[i]), "path": m["path"], "mjd": m["mjd"], "night": night,
                     "mag": float(mag[i]), "mag_err": float(m["err"][i]), "instrumental": float(m["inst"][i]),
                     "zero_point": zp, "colour_term": c, "comparison": bool(kept[i])}
                )  # fmt: skip
        rows.sort(key=lambda r: (r["source_id"], r["mjd"]))
        table = pa.Table.from_pylist(rows, schema=CURVE_SCHEMA)
        self.root.mkdir(parents=True, exist_ok=True)
        _write(table, self.path)
        _write(per_night(table), self.nights_path)
        stars = {"target": self.target, **summarise(table, field)}
        tmp = self.stars_path.with_name(self.stars_path.name + ".tmp")
        tmp.write_text(json.dumps(stars, indent=2) + "\n", "utf-8")
        tmp.replace(self.stars_path)
        return {
            "frames_measured": len(measured),
            "frames_calibrated": sum(1 for f in fits if f is not None),
            "points": table.num_rows,
            **stars,
        }


def _write(table: pa.Table, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)


def per_night(table: pa.Table) -> pa.Table:
    """Each star's weighted mean magnitude per night."""
    groups: dict[tuple[int, str], list[tuple[float, float, float]]] = {}
    for r in table.to_pylist():
        groups.setdefault((r["source_id"], r["night"]), []).append((r["mjd"], r["mag"], r["mag_err"]))
    rows = []
    for (source_id, night), points in sorted(groups.items()):
        t, m, e = (np.array(v) for v in zip(*points, strict=True))
        w = 1 / e**2
        rows.append(
            {"source_id": source_id, "night": night, "mjd": float(t.mean()), "points": len(m),
             "mag": float(np.sum(w * m) / np.sum(w)), "mag_err": float(1 / math.sqrt(np.sum(w))),
             "scatter": float(np.std(m))}
        )  # fmt: skip
    return pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [("source_id", pa.int64()), ("night", pa.string()), ("mjd", pa.float64()), ("points", pa.int64()),
             ("mag", pa.float64()), ("mag_err", pa.float64()), ("scatter", pa.float64())]
        ),
    )  # fmt: skip


def summarise(table: pa.Table, field: Field) -> dict[str, Any]:
    """Each star's points, mean and scatter; the scatter stars of its brightness typically show; and the
    candidates whose scatter is ``EXCESS`` times that, with a Lomb-Scargle period."""
    from astropy.timeseries import LombScargle

    by_star: dict[int, list[tuple[float, float, float]]] = {}
    for r in table.to_pylist():
        by_star.setdefault(r["source_id"], []).append((r["mjd"], r["mag"], r["mag_err"]))
    g_of = dict(zip(field.source_id.tolist(), field.g.tolist(), strict=True))
    where = {int(i): (float(r), float(d)) for i, r, d in zip(field.source_id, field.ra, field.dec, strict=True)}
    stars: list[dict[str, Any]] = []
    for source_id, points in sorted(by_star.items()):
        t, m, e = (np.array(v) for v in zip(*points, strict=True))
        if len(m) < 2:
            continue
        w = 1 / e**2
        mean = float(np.sum(w * m) / np.sum(w))
        stars.append(
            {"source_id": source_id, "ra_deg": where[source_id][0], "dec_deg": where[source_id][1],
             "g": g_of.get(source_id), "points": len(m), "mean_mag": round(mean, 4),
             "scatter": float(1.4826 * np.median(np.abs(m - np.median(m)))),
             "median_err": float(np.median(e)),
             "chi2_reduced": float(np.sum(((m - mean) / e) ** 2) / (len(m) - 1)),
             "baseline_days": float(t.max() - t.min()), "first_mjd": float(t.min()), "last_mjd": float(t.max()),
             "_t": t, "_m": m, "_e": e}
        )  # fmt: skip
    mags = np.array([s["mean_mag"] for s in stars])
    scatters = np.array([s["scatter"] for s in stars])
    candidates = []
    for s in stars:
        near = np.abs(mags - s["mean_mag"]) <= 0.5
        typical = float(np.median(scatters[near])) if near.sum() >= 3 else math.nan
        s["typical_scatter"] = typical
        s["excess"] = s["scatter"] / typical if typical and math.isfinite(typical) and typical > 0 else None
        t, m, e = s.pop("_t"), s.pop("_m"), s.pop("_e")
        if s["points"] >= MIN_POINTS and s["excess"] is not None and s["excess"] >= EXCESS:
            if t.max() > t.min():
                ls = LombScargle(t, m, e)
                frequency, power = ls.autopower(samples_per_peak=5, nyquist_factor=2)
                best = int(np.argmax(power))
                s["ls_period_days"] = float(1 / frequency[best])
                s["ls_false_alarm"] = float(ls.false_alarm_probability(power[best]))
            candidates.append(s)
    precision = {}
    for lo in range(9, 16):
        inside = [s["scatter"] for s in stars if s["g"] is not None and lo <= s["g"] < lo + 1]
        if inside:
            precision[f"G {lo}-{lo + 1}"] = round(float(np.median(inside)), 4)
    candidates.sort(key=lambda s: -s["excess"])
    return {"stars": len(stars), "precision_by_g": precision, "candidates": candidates, "all_stars": stars}


def stars_files(source: LocalSource, data: Path) -> list[Path]:
    """Every target's star summary under ``<data>/lightcurves/<source>``, by name."""
    root = source.output_dir(data, "lightcurves")
    return sorted(root.glob("*-stars.json")) if root.is_dir() else []


def curve_csv(source: LocalSource, data: Path, target: str, source_id: int) -> bytes:
    """One star's light curve from a target's table, as the canonical CSV (mjd, mag, mag_err)."""
    from sdp.kernel.science.products import lightcurve_csv

    table = pq.read_table(LightCurves(source, data, target).path, filters=[("source_id", "=", source_id)])
    if table.num_rows == 0:
        raise LookupError(f"{target} has no light curve for Gaia DR3 {source_id}")

    def column(name: str) -> np.ndarray:
        return np.asarray(table.column(name).to_numpy(), dtype=float)

    return lightcurve_csv(column("mjd"), column("mag"), column("mag_err"))


def busiest_target(rows: Iterable[dict[str, Any]]) -> str | None:
    """The target whose solved lights span the most nights (the default for ``sdp local lightcurve``)."""
    nights: dict[str, set[str]] = {}
    for r in rows:
        if r["kind"] == "light" and r["status"] == SOLVED and r["night"] and r["target"]:
            nights.setdefault(r["target"], set()).add(r["night"])
    return max(sorted(nights), key=lambda t: len(nights[t]), default=None)


def field_cone(rows: Iterable[dict[str, Any]], target: str) -> tuple[float, float, float] | None:
    """The cone (RA, Dec, radius in degrees) holding every solved light of the target."""
    frames = LightCurves.frames_of(rows, target)
    if not frames:
        return None
    ra, dec = np.radians([f["ra_deg"] for f in frames]), np.radians([f["dec_deg"] for f in frames])
    v = np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)]).sum(axis=1)
    ra_c = math.degrees(math.atan2(v[1], v[0])) % 360.0
    dec_c = math.degrees(math.asin(v[2] / np.linalg.norm(v)))
    reach = max(_separation(ra_c, dec_c, f["ra_deg"], f["dec_deg"]) + f["radius_deg"] for f in frames)
    return ra_c, dec_c, reach
