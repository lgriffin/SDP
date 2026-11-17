"""IsochroneFit: MIST isochrones fitted to a cluster's Gaia colour-magnitude diagram, and stellar masses
read off them (STL-033 to STL-036). EXPLORATORY.

MIST (MESA Isochrones and Stellar Tracks; Dotter 2016, Choi et al. 2016) is a grid of stellar models
computed with MESA. Each evolutionary track is resampled onto equivalent evolutionary points (EEPs:
the zero-age main sequence, the end of core hydrogen burning, the tip of the red giant branch and so
on), so stars of different masses can be compared point for point, and an isochrone is the set of EEPs
reached by every mass at one age. MIST publishes the isochrones with synthetic photometry in ``.iso.cmd``
files: a header block of global values (MIST version, MESA revision, photometric system, the initial
composition, Av and Rv), then one table per age. SDP reads those files as published, keeps the columns
it needs and records where they came from.

A star cluster is one age, one composition and one distance, so its members lie along one isochrone.
The fit finds the age, [Fe/H], extinction A_V and distance modulus that put the members closest to one,
by a grid search over the MIST ages and metallicities and a fine grid in A_V and modulus. Each member's
distance to the isochrone is measured in the colour-magnitude plane in units of its error (a smooth
model by G plus a floor), capped so that binaries and interlopers cannot pull the fit, and the cluster's
mean parallax adds a prior on the modulus. The result is the best grid point and, per parameter, the
range whose best cost lies within ``profile_delta`` of the minimum: a grid profile, not a calibrated
confidence interval.

Masses: a member's mass is the mass of the nearest point of the cluster's best isochrone. The heaviest
star still alive at the cluster's age (the largest current mass on the isochrone) bounds any partner
that is itself a normal cluster star; a partner heavier than that is a remnant, a merger product, or
not coeval with the cluster. A field star has no cluster age, so its mass and age come from weights
over every grid point of one metallicity, with a Kroupa initial mass function and a prior uniform in
age.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray
from scipy.spatial import cKDTree

SETTINGS_FILE = "isochrones.json"
#: A_V written as "Av = <value>" anywhere on a header line, as MIST v1.2's "# CCM89 extinction: Av =  0.000"
_AV = re.compile(r"\bAv\s*=\s*(\S+)")
SCHEMA = "sdp-stellar/isochrone-fit@1"
#: the grid's columns, in the settings' names; every one is stored
FIELDS = ("eep", "log_age", "initial_mass", "star_mass", "log_teff", "log_g", "log_l", "phase", "g", "bp", "rp")
#: global values each stored grid carries in its metadata
META_KEYS = ("mist_version", "mesa_revision", "photometric_system", "vvcrit", "afe", "source_url", "source_sha256")

Array = NDArray[np.float64]


class MistFormatError(ValueError):
    """A file that is not a MIST ``.iso.cmd`` table as SDP expects it."""


def settings_text() -> str:
    return resources.files("sdp_stellar").joinpath(SETTINGS_FILE).read_text("utf-8")


def load_settings() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(settings_text())
    return document


def settings_sha256() -> str:
    return hashlib.sha256(settings_text().encode("utf-8")).hexdigest()


# --- reading MIST files ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MistFile:
    """One ``.iso.cmd`` file: its header values and one column table per isochrone."""

    meta: dict[str, Any]
    blocks: list[dict[str, Array]]


def _value_line(lines: Sequence[str], i: int) -> list[str]:
    if i >= len(lines) or not lines[i].startswith("#"):
        raise MistFormatError(f"line {i + 1}: expected a header line of values")
    return lines[i].lstrip("#").split()


def _number(token: str) -> float:
    try:
        return float(token.replace("D", "E"))
    except ValueError:
        raise MistFormatError(f"{token!r} is not a number") from None


def parse_cmd(text: str) -> MistFile:
    """Read a MIST ``.iso.cmd`` file. The header's labelled values are kept by name (``mist_version``,
    ``mesa_revision``, ``photometric_system``, ``yinit``, ``zinit``, ``feh``, ``afe``, ``vvcrit``,
    ``av``, ``rv``); each ``number of EEPs, cols = N M`` line opens a table whose column names are the
    next header line that is not only column numbers, followed by N rows of M values."""
    lines = text.splitlines()
    meta: dict[str, Any] = {}
    blocks: list[dict[str, Array]] = []
    labels = {"Yinit": "yinit", "Zinit": "zinit", "[Fe/H]": "feh", "[a/Fe]": "afe", "v/vcrit": "vvcrit"}
    i = 0
    while i < len(lines):
        line = lines[i]
        body = line.lstrip("#").strip()
        if not line.startswith("#"):
            if body:
                raise MistFormatError(f"line {i + 1}: a data row outside any table")
            i += 1
            continue
        lowered = body.lower()
        if lowered.startswith("mist version number"):
            meta["mist_version"] = body.split("=", 1)[1].strip()
        elif lowered.startswith("mesa revision number"):
            meta["mesa_revision"] = body.split("=", 1)[1].strip()
        elif lowered.startswith("photometric system"):
            if "=" in body:  # MIST's layout: the system follows "=" on the same line
                meta["photometric_system"] = body.split("=", 1)[1].strip()
            else:
                j = i + 1
                while j < len(lines) and set(lines[j].lstrip("#").strip()) <= {"-"}:
                    j += 1
                meta["photometric_system"] = lines[j].lstrip("#").strip() if j < len(lines) else ""
        elif "Yinit" in body.split() and "[Fe/H]" in body.split():
            names = body.split()
            values = _value_line(lines, i + 1)
            if len(values) != len(names):
                raise MistFormatError(f"line {i + 2}: {len(values)} values for {len(names)} labels")
            meta.update({labels.get(n, n): _number(v) for n, v in zip(names, values, strict=True)})
            i += 1
        elif body.split()[:2] == ["Av", "Rv"]:  # labels on one line, values on the next
            av, rv = (_number(v) for v in _value_line(lines, i + 1)[:2])
            meta.update({"av": av, "rv": rv})
            i += 1
        elif (found := _AV.search(body)) is not None:  # "# CCM89 extinction: Av =  0.000"
            meta["av"] = _number(found.group(1))
        elif lowered.startswith("number of eeps, cols"):
            n_rows, n_cols = (int(t) for t in body.split("=", 1)[1].split())
            j = i + 1
            while j < len(lines) and lines[j].startswith("#") and all(t.isdigit() for t in lines[j][1:].split()):
                j += 1
            names = _value_line(lines, j)
            if len(names) != n_cols:
                raise MistFormatError(f"line {j + 1}: {len(names)} column names, the header says {n_cols}")
            rows = lines[j + 1 : j + 1 + n_rows]
            if len(rows) != n_rows or any(not r.strip() or r.startswith("#") for r in rows):
                raise MistFormatError(f"line {j + 2}: fewer than the {n_rows} rows the header announces")
            try:
                data = np.asarray([[float(t) for t in r.split()] for r in rows], dtype=np.float64)
            except ValueError as exc:
                raise MistFormatError(f"line {j + 2}: {exc}") from None
            if data.shape != (n_rows, n_cols):
                raise MistFormatError(f"line {j + 2}: rows do not all have {n_cols} values")
            blocks.append({name: data[:, k] for k, name in enumerate(names)})
            i = j + 1 + n_rows
            continue
        i += 1
    if not blocks:
        raise MistFormatError("no isochrone tables (no 'number of EEPs, cols' line)")
    for key in ("feh", "av"):
        if key not in meta:
            raise MistFormatError(f"the header has no {key!r} value")
    return MistFile(meta, blocks)


# --- the grid ---------------------------------------------------------------------------------------


@dataclass
class Grid:
    """Every isochrone kept, as flat columns sorted by [Fe/H], age and EEP, plus where they came from."""

    feh: Array
    columns: dict[str, Array]
    meta: dict[str, Any] = field(default_factory=dict)
    _index: dict[tuple[float, float], slice] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        order = np.lexsort((self.columns["eep"], self.columns["log_age"], self.feh))
        self.feh = np.asarray(self.feh, dtype=np.float64)[order]
        self.columns = {k: np.asarray(v, dtype=np.float64)[order] for k, v in self.columns.items()}
        keys = np.round(self.feh, 4), np.round(self.columns["log_age"], 4)
        change = np.nonzero((np.diff(keys[0]) != 0) | (np.diff(keys[1]) != 0))[0] + 1
        starts = np.concatenate([[0], change, [self.feh.size]]).astype(int) if self.feh.size else np.zeros(1, int)
        self._index = {
            (float(keys[0][a]), float(keys[1][a])): slice(int(a), int(b)) for a, b in itertools.pairwise(starts)
        }

    @property
    def size(self) -> int:
        return int(self.feh.size)

    def fehs(self) -> list[float]:
        return sorted({k[0] for k in self._index})

    def ages(self, feh: float) -> list[float]:
        f = round(float(feh), 4)
        return sorted(k[1] for k in self._index if k[0] == f)

    def isochrone(self, feh: float, log_age: float) -> dict[str, Array]:
        """One isochrone's rows, in EEP order; KeyError when the grid has no such point."""
        s = self._index[(round(float(feh), 4), round(float(log_age), 4))]
        return {k: v[s] for k, v in self.columns.items()}

    def to_table(self) -> pa.Table:
        data = {"feh": pa.array(self.feh, pa.float32())}
        data.update({k: pa.array(self.columns[k], pa.float32()) for k in FIELDS})
        meta = {f"sdp.{k}": str(v) for k, v in self.meta.items()}
        return pa.table(data).replace_schema_metadata(meta)

    @classmethod
    def from_table(cls, table: pa.Table) -> Grid:
        missing = [c for c in ("feh", *FIELDS) if c not in table.column_names]
        if missing:
            raise MistFormatError(f"the stored grid lacks columns {missing}")
        meta = {k.decode()[4:]: v.decode() for k, v in (table.schema.metadata or {}).items() if k.startswith(b"sdp.")}
        cols = {k: np.asarray(table.column(k).to_numpy(), dtype=np.float64) for k in FIELDS}
        # float32 storage: round the keys back to the grid's own steps
        feh = np.round(np.asarray(table.column("feh").to_numpy(), dtype=np.float64), 3)
        cols["log_age"] = np.round(cols["log_age"], 3)
        return cls(feh, cols, meta)


def grid_from_files(files: Iterable[MistFile], settings: Mapping[str, Any]) -> Grid:
    """The kept columns of every isochrone in the settings' age range from files with A_V = 0. All
    files must share one MIST version and photometric system. [Fe/H] is the file's initial value."""
    names = settings["columns"]
    lo, hi = float(settings["grid"]["log_age_min"]), float(settings["grid"]["log_age_max"])
    fehs: list[Array] = []
    parts: dict[str, list[Array]] = {k: [] for k in FIELDS}
    shared: dict[str, Any] = {}
    seen_feh: set[float] = set()
    for f in files:
        if f.meta.get("av", 0.0) != 0.0:
            raise MistFormatError(f"a file has A_V = {f.meta['av']}; SDP applies extinction itself and needs A_V = 0")
        feh = round(float(f.meta["feh"]), 4)
        if feh in seen_feh:
            raise MistFormatError(f"two files with [Fe/H] = {feh}")
        seen_feh.add(feh)
        for key in ("mist_version", "mesa_revision", "photometric_system", "vvcrit", "afe"):
            if key in f.meta:
                if key in shared and shared[key] != f.meta[key]:
                    raise MistFormatError(f"files disagree on {key}: {shared[key]!r} and {f.meta[key]!r}")
                shared[key] = f.meta[key]
        for block in f.blocks:
            absent = [names[k] for k in FIELDS if names[k] not in block]
            if absent:
                have = ", ".join(sorted(block)[:60])
                raise MistFormatError(f"the table has no column {absent}; it has: {have}")
            age = block[names["log_age"]]
            keep = (age >= lo - 1e-9) & (age <= hi + 1e-9)
            if not keep.any():
                continue
            for k in FIELDS:
                parts[k].append(block[names[k]][keep])
            fehs.append(np.full(int(keep.sum()), feh))
    if not fehs:
        raise MistFormatError(f"no isochrone in the age range log10(age/yr) {lo} to {hi}")
    cols = {k: np.concatenate(v) for k, v in parts.items()}
    cols["log_age"] = np.round(cols["log_age"], 4)
    shared["columns"] = columns_key(settings)
    return Grid(np.concatenate(fehs), cols, shared)


def columns_key(settings: Mapping[str, Any]) -> str:
    """The settings' MIST column choice as stored in a grid's metadata, so a grid built with another
    choice (say the DR2 instead of the EDR3 Gaia bands) is never read as this one."""
    return json.dumps({k: settings["columns"][k] for k in FIELDS}, sort_keys=True, separators=(",", ":"))


# --- photometry, extinction and distance -----------------------------------------------------------


def extinction(av: float | Array, settings: Mapping[str, Any]) -> tuple[Array, Array]:
    """(A_G, E(BP-RP)) for an A_V, from the settings' fixed ratios."""
    e = settings["extinction"]
    a = np.asarray(av, dtype=np.float64)
    return a * float(e["a_g_over_a_v"]), a * (float(e["a_bp_over_a_v"]) - float(e["a_rp_over_a_v"]))


def photometric_errors(g: Array, settings: Mapping[str, Any], abs_g: Array | None = None) -> tuple[Array, Array]:
    """(colour error, G error) of each star, each the quadrature sum of the floor and an error that grows
    by a factor 10^0.2 per magnitude fainter than G = 15 (photon noise). Given its absolute G, a star
    fainter than ``red_dwarf_abs_g`` (a K or M dwarf) also carries ``red_dwarf_colour_error`` in colour:
    the models' colours of such stars are known to be poor, and without it a handful of red dwarfs pulled
    fits to [Fe/H] +0.5 (the 2026-11-16 PC run)."""
    p = settings["photometry"]
    grow = 10 ** (0.2 * (np.asarray(g, dtype=np.float64) - 15.0))
    colour = np.hypot(float(p["colour_floor"]), float(p["colour_error_at_g15"]) * grow)
    mag = np.hypot(float(p["g_floor"]), float(p["g_error_at_g15"]) * grow)
    if abs_g is not None and "red_dwarf_abs_g" in p:
        red = np.asarray(abs_g, dtype=np.float64) > float(p["red_dwarf_abs_g"])
        colour = np.hypot(colour, np.where(red, float(p["red_dwarf_colour_error"]), 0.0))
    return colour, mag


def modulus_prior(parallax_mas: float, parallax_err_mas: float, settings: Mapping[str, Any]) -> tuple[float, float]:
    """The distance modulus a cluster's mean parallax gives, after the zero point, and its error with the
    systematic floor; ValueError when the corrected parallax is not positive."""
    p = settings["parallax"]
    plx = float(parallax_mas) - float(p["zero_point_mas"])
    if not plx > 0:
        raise ValueError(f"a corrected parallax of {plx} mas gives no distance")
    err = math.hypot(float(parallax_err_mas), float(p["systematic_mas"]))
    return 5.0 * math.log10(100.0 / plx), 5.0 / math.log(10.0) * err / plx


# --- the isochrone as a line in the colour-magnitude plane ------------------------------------------


@dataclass(frozen=True)
class Line:
    """An isochrone's points in absolute G against intrinsic BP-RP, with points added along each segment
    so no two neighbours are more than ``densify_step`` apart; a segment that changes phase is not
    filled, so the jump from the red giant tip to the helium-burning clump draws no line."""

    colour: Array
    mag: Array
    initial_mass: Array
    star_mass: Array
    phase: Array
    log_age: Array

    @property
    def size(self) -> int:
        return int(self.colour.size)


def line(iso: Mapping[str, Array], phases: Sequence[int], step: float) -> Line:
    keep = np.isin(np.round(iso["phase"]).astype(int), list(phases))
    colour = (iso["bp"] - iso["rp"])[keep]
    mag = iso["g"][keep]
    mi, ms, ph, age = iso["initial_mass"][keep], iso["star_mass"][keep], iso["phase"][keep], iso["log_age"][keep]
    if colour.size < 2:
        return Line(colour, mag, mi, ms, ph, age)
    seg = np.hypot(np.diff(colour), np.diff(mag))
    same = ph[1:] == ph[:-1]
    pieces = np.where(same, np.maximum(np.ceil(seg / step), 1), 1).astype(int)
    starts = np.repeat(np.arange(colour.size - 1), pieces)
    frac = np.concatenate([np.arange(n) / n for n in pieces])

    def fill(x: Array) -> Array:
        return np.concatenate([x[starts] + frac * (x[starts + 1] - x[starts]), x[-1:]])

    def hold(x: Array) -> Array:
        return np.concatenate([x[starts], x[-1:]])

    return Line(fill(colour), fill(mag), fill(mi), fill(ms), hold(ph), hold(age))


# threads per tree query: -1 uses every core; a process that fits clusters side by side sets it to 1
TREE_WORKERS = -1


@dataclass
class _Matcher:
    """Nearest points of one line for stars with per-star errors."""

    ln: Line
    scale: tuple[float, float]
    k: int
    tree: Any = None

    def __post_init__(self) -> None:
        pts = np.column_stack([self.ln.colour / self.scale[0], self.ln.mag / self.scale[1]])
        self.tree = cKDTree(pts)

    def nearest(
        self, colour: Array, mag: Array, sc: Array, sm: Array, limit: float = math.inf
    ) -> tuple[Array, NDArray[np.int64]]:
        """(squared distance in units of each star's errors, index of the nearest line point), exact for
        every star whose distance is below ``limit`` (beyond it only a value at or above the limit: the
        fit caps such distances anyway). The tree measures in units of the floors; a star's own errors are never
        below them, so a point nearer in the star's units lies within sqrt(d2) / rho floor units, rho the
        smaller of the floor-to-error ratios. Where the k-th neighbour is nearer than that, every point
        inside that radius is checked too."""
        k = min(self.k, self.ln.size)
        q = np.column_stack([colour / self.scale[0], mag / self.scale[1]])
        dist, idx = self.tree.query(q, k=k, workers=TREE_WORKERS)
        dist, idx = np.asarray(dist).reshape(colour.size, k), np.asarray(idx).reshape(colour.size, k)
        d2 = ((colour[:, None] - self.ln.colour[idx]) / sc[:, None]) ** 2 + (
            (mag[:, None] - self.ln.mag[idx]) / sm[:, None]
        ) ** 2
        best = np.argmin(d2, axis=1)
        rows = np.arange(colour.size)
        out_d2, out_i = d2[rows, best], idx[rows, best]
        rho = np.minimum(self.scale[0] / sc, self.scale[1] / sm)
        # a nearer point, or one below the limit, lies within this many floor units
        radius = np.minimum(np.sqrt(out_d2), limit) / rho * (1.0 + 1e-9)
        check = np.nonzero((dist[:, -1] < radius) & (k < self.ln.size))[0]
        if check.size:
            balls = self.tree.query_ball_point(q[check], radius[check], workers=TREE_WORKERS)
            lens = np.asarray([len(b) for b in balls], dtype=np.int64)
            star = np.repeat(check, lens)
            pts = np.fromiter(itertools.chain.from_iterable(balls), dtype=np.int64, count=int(lens.sum()))
            dd = ((colour[star] - self.ln.colour[pts]) / sc[star]) ** 2 + (
                (mag[star] - self.ln.mag[pts]) / sm[star]
            ) ** 2
            order = np.lexsort((dd, star))
            first = order[np.concatenate([[True], np.diff(star[order]) != 0])] if order.size else order
            better = dd[first] < out_d2[star[first]]
            out_d2[star[first][better]] = dd[first][better]
            out_i[star[first][better]] = pts[first][better]
        return out_d2, out_i


# --- fitting a cluster -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Stars:
    """A cluster's members: observed BP-RP and G and their errors. ``red`` marks the stars counted as red
    dwarfs, whose ``colour_err`` carries the models' extra colour error (see :func:`photometric_errors`).
    The marks are fixed for every trial a fit compares: were they judged per trial, a trial that made more
    stars red dwarfs (less dust, a larger distance) would get their larger errors and a smaller cost for
    nothing, and the 2026-11-16 22:40 PC run showed exactly that pull to low A_V and high [Fe/H]."""

    colour: Array
    g: Array
    colour_err: Array
    g_err: Array
    red: NDArray[np.bool_]

    @classmethod
    def from_photometry(
        cls, colour: Array, g: Array, settings: Mapping[str, Any], offset: float | None = None
    ) -> Stars:
        """The stars with a colour and G; with ``offset`` (A_G plus the distance modulus) those whose
        absolute G is fainter than ``red_dwarf_abs_g`` are marked red dwarfs."""
        c, m = np.asarray(colour, dtype=np.float64), np.asarray(g, dtype=np.float64)
        ok = np.isfinite(c) & np.isfinite(m)
        return cls(c[ok], m[ok], *_errors(m[ok], settings, offset))

    def judged(self, settings: Mapping[str, Any], offset: float) -> Stars:
        """The same stars with their red dwarfs judged again at this ``offset`` (A_G plus modulus)."""
        return Stars(self.colour, self.g, *_errors(self.g, settings, offset))

    @property
    def size(self) -> int:
        return int(self.colour.size)

    def take(self, rows: NDArray[np.int64]) -> Stars:
        return Stars(self.colour[rows], self.g[rows], self.colour_err[rows], self.g_err[rows], self.red[rows])


def _errors(g: Array, settings: Mapping[str, Any], offset: float | None) -> tuple[Array, Array, NDArray[np.bool_]]:
    p = settings["photometry"]
    if offset is None or "red_dwarf_abs_g" not in p:
        red = np.zeros(np.shape(g), dtype=bool)
    else:
        red = (g - offset) > float(p["red_dwarf_abs_g"])
    ce, ge = photometric_errors(g, settings, None if offset is None else g - offset)
    return ce, ge, red


def _costs(
    matcher: _Matcher,
    stars: Stars,
    avs: Array,
    moduli: Array,
    prior: tuple[float, float],
    settings: Mapping[str, Any],
    chunk: int = 200_000,
) -> Array:
    """The fit's cost at each A_V (rows) and distance modulus (columns): the members' capped squared
    distances to the line, summed, plus the parallax prior's term. Every A_V and modulus is matched in a
    few large queries rather than one per A_V: a tree query has a fixed start-up cost (its worker threads),
    which thousands of small queries per cluster paid over and over."""
    cap = float(settings["fit"]["outlier_cap"]) ** 2
    n, k = stars.size, moduli.size
    a_g, e_c = extinction(np.asarray(avs, dtype=np.float64), settings)
    out = np.empty((a_g.size, k))
    per = max(chunk // max(n * k, 1), 1)
    for lo in range(0, a_g.size, per):
        hi = min(lo + per, a_g.size)
        m = hi - lo
        colour = np.broadcast_to((stars.colour[None, :] - e_c[lo:hi, None])[:, None, :], (m, k, n)).ravel()
        mag = (stars.g[None, None, :] - a_g[lo:hi, None, None] - moduli[None, :, None]).ravel()
        reps = m * k
        d2, _ = matcher.nearest(
            colour, mag, np.tile(stars.colour_err, reps), np.tile(stars.g_err, reps), math.sqrt(cap)
        )
        out[lo:hi] = np.minimum(d2, cap).reshape(m, k, n).sum(axis=2)
    result: Array = out + ((moduli[None, :] - prior[0]) / prior[1]) ** 2
    return result


def _steps(lo: float, hi: float, step: float) -> Array:
    n = math.floor((hi - lo) / step + 1e-9)
    return np.round(lo + step * np.arange(n + 1), 6)


@dataclass
class _Search:
    grid: Grid
    stars: Stars
    prior: tuple[float, float]
    settings: Mapping[str, Any]
    feh_prior: tuple[float, float] | None = None
    blocks: list[Array] = field(default_factory=list)
    _lines: dict[tuple[float, float], _Matcher | None] = field(default_factory=dict)

    def matcher(self, feh: float, age: float) -> _Matcher | None:
        key = (feh, age)
        if key not in self._lines:
            fit = self.settings["fit"]
            ln = line(self.grid.isochrone(feh, age), self.settings["phases"]["fit"], float(fit["densify_step"]))
            floors = self.settings["photometry"]
            scale = (float(floors["colour_floor"]), float(floors["g_floor"]))
            self._lines[key] = _Matcher(ln, scale, int(fit["neighbours"])) if ln.size >= 2 else None
        return self._lines[key]

    def run(self, fehs: Sequence[float], ages_of: Mapping[float, Sequence[float]], avs: Array, mods: Array) -> None:
        av_col, mod_col = np.repeat(avs, mods.size), np.tile(mods, avs.size)
        for feh in fehs:
            for age in ages_of[feh]:
                m = self.matcher(feh, age)
                if m is None:
                    continue
                costs = _costs(m, self.stars, avs, mods, self.prior, self.settings).ravel()
                if self.feh_prior is not None:
                    costs = costs + ((feh - self.feh_prior[0]) / self.feh_prior[1]) ** 2
                block = np.column_stack([np.full(costs.size, feh), np.full(costs.size, age), av_col, mod_col, costs])
                self.blocks.append(block)

    @property
    def trials(self) -> Array:
        return np.concatenate(self.blocks) if self.blocks else np.empty((0, 5))

    def best(self) -> tuple[float, float, float, float, float]:
        t = self.trials
        i = int(np.lexsort((t[:, 3], t[:, 2], t[:, 0], t[:, 1], t[:, 4]))[0])
        feh, age, av, mod, cost = (float(x) for x in t[i])
        return feh, age, av, mod, cost


def fit_cluster(
    colour: Array,
    g: Array,
    parallax_mas: float,
    parallax_err_mas: float,
    grid: Grid,
    settings: Mapping[str, Any] | None = None,
    feh_prior: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The best isochrone for a cluster's members (their BP-RP and G) at the distance its mean parallax
    suggests, or None when fewer than ``min_stars`` members have both. A ``feh_prior`` (``mean``,
    ``sigma``, from :func:`metallicity_prior`) adds a Gaussian term in [Fe/H] to every cost. Coarse grid over every [Fe/H] in
    range, every ``age_coarse_stride``-th age, A_V and modulus; then the neighbouring metallicities, all
    ages within two coarse steps, and a fine A_V and modulus grid around the coarse best."""
    s = settings or load_settings()
    f = s["fit"]
    if int(np.sum(np.isfinite(np.asarray(colour, dtype=float)) & np.isfinite(np.asarray(g, dtype=float)))) < int(
        f["min_stars"]
    ):
        return None
    prior = modulus_prior(parallax_mas, parallax_err_mas, s)
    # red dwarfs are judged first at the parallax distance without dust, then again at the coarse best A_V
    stars = Stars.from_photometry(colour, g, s, prior[0])
    if stars.size > int(f["max_fit_stars"]):
        rng = np.random.default_rng(int(f["seed"]))
        stars = stars.take(np.sort(rng.choice(stars.size, int(f["max_fit_stars"]), replace=False)))
    half = max(float(f["modulus_halfwidth_sigma"]) * prior[1], float(f["modulus_halfwidth_min"]))
    fehs = [x for x in grid.fehs() if float(f["feh_min"]) - 1e-9 <= x <= float(f["feh_max"]) + 1e-9]
    if not fehs:
        raise ValueError(f"the grid has no [Fe/H] between {f['feh_min']} and {f['feh_max']}")
    gauss = None if feh_prior is None else (float(feh_prior["mean"]), float(feh_prior["sigma"]))
    search = _Search(grid, stars, prior, s, gauss)
    stride = max(int(f["age_coarse_stride"]), 1)
    coarse_ages = {x: grid.ages(x)[::stride] for x in fehs}
    av_c = _steps(0.0, float(f["av_max"]), float(f["av_coarse_step"]))
    mod_c = _steps(prior[0] - half, prior[0] + half, float(f["modulus_coarse_step"]))
    search.run(fehs, coarse_ages, av_c, mod_c)
    feh0, age0, av0, mod0, _ = search.best()
    again = stars.judged(s, float(extinction(av0, s)[0]) + mod0)
    if not np.array_equal(again.red, stars.red):  # dust moved stars across the cutoff: search again with them
        stars = again
        search = _Search(grid, stars, prior, s, gauss, _lines=search._lines)
        search.run(fehs, coarse_ages, av_c, mod_c)
        feh0, age0, av0, mod0, _ = search.best()

    i = fehs.index(feh0)
    near_fehs = fehs[max(i - 1, 0) : i + 2]
    every = grid.ages(feh0)
    j = every.index(age0)
    fine_ages = {
        x: [a for a in grid.ages(x) if every[max(j - 2 * stride, 0)] <= a <= every[min(j + 2 * stride, len(every) - 1)]]
        for x in near_fehs
    }
    av_f = _steps(
        max(av0 - float(f["av_coarse_step"]), 0.0), av0 + float(f["av_coarse_step"]), float(f["av_fine_step"])
    )
    av_f = av_f[av_f <= float(f["av_max"]) + 1e-9]  # the fine steps stay inside the search
    mod_f = _steps(
        mod0 - float(f["modulus_coarse_step"]), mod0 + float(f["modulus_coarse_step"]), float(f["modulus_fine_step"])
    )
    mod_f = mod_f[(mod_f >= prior[0] - half - 1e-9) & (mod_f <= prior[0] + half + 1e-9)]
    search.run(near_fehs, fine_ages, av_f, mod_f)
    feh, age, av, mod, cost = search.best()

    best = search.matcher(feh, age)
    assert best is not None
    a_g, e_c = extinction(av, s)
    d2, _ = best.nearest(stars.colour - e_c, stars.g - a_g - mod, stars.colour_err, stars.g_err)
    iso = grid.isochrone(feh, age)
    trials = search.trials
    ages_all = grid.ages(feh)
    edges = {
        "feh": feh in (fehs[0], fehs[-1]) and len(fehs) > 1,
        "log_age": age in (ages_all[0], ages_all[-1]),
        "av": av >= float(f["av_max"]) - 1e-9,
        "distance_modulus": abs(abs(mod - prior[0]) - half) < 0.5 * float(f["modulus_fine_step"]),
    }
    return {
        "schema": SCHEMA,
        "feh": feh,
        "log_age": age,
        "age_myr": round(10 ** (age - 6.0), 3),
        "av": round(av, 4),
        "a_g": round(float(a_g), 4),
        "e_bp_rp": round(float(e_c), 4),
        "distance_modulus": round(mod, 4),
        "distance_pc": round(10 ** (mod / 5.0 + 1.0), 2),
        "parallax_modulus": round(prior[0], 4),
        "parallax_modulus_err": round(prior[1], 4),
        "cost": round(cost, 4),
        "stars_fitted": stars.size,
        "red_dwarfs": int(stars.red.sum()),
        "on_isochrone_share": round(float(np.mean(np.sqrt(d2) <= float(f["inlier_distance"]))), 4),
        "median_distance": round(float(np.median(np.sqrt(d2))), 4),
        "ranges": profile_ranges(trials, float(f["profile_delta"])),
        "turnoff_initial_msun": turnoff_mass(iso),
        "heaviest_living_msun": heaviest_living(iso),
        "trials": int(trials.shape[0]),
        "at_grid_edge": [k for k, v in edges.items() if v],
        "feh_prior": None if feh_prior is None else dict(feh_prior),
        "surface": surface(trials),
    }


def profile_ranges(trials: Array, delta: float) -> dict[str, list[float]]:
    """Per parameter (columns feh, log_age, av, distance_modulus of ``trials``, cost last), the smallest
    and largest value whose best cost over the others is within ``delta`` of the minimum."""
    low = float(trials[:, 4].min())
    out: dict[str, list[float]] = {}
    for k, name in enumerate(("feh", "log_age", "av", "distance_modulus")):
        values = np.unique(trials[:, k])
        best = np.asarray([trials[trials[:, k] == v, 4].min() for v in values])
        ok = values[best <= low + delta]
        out[name] = [round(float(ok.min()), 4), round(float(ok.max()), 4)]
    return out


def surface(trials: Array) -> list[dict[str, float]]:
    """The best cost at each (feh, log_age) tried: the age-metallicity map of the fit."""
    keys = np.unique(trials[:, :2], axis=0)
    out = []
    for feh, age in keys:
        sel = (trials[:, 0] == feh) & (trials[:, 1] == age)
        out.append({"feh": float(feh), "log_age": float(age), "cost": round(float(trials[sel, 4].min()), 4)})
    return out


def turnoff_mass(iso: Mapping[str, Array]) -> float | None:
    """The largest initial mass still on the main sequence (phase 0)."""
    ms = np.round(iso["phase"]) == 0
    return round(float(iso["initial_mass"][ms].max()), 4) if ms.any() else None


def heaviest_living(iso: Mapping[str, Array]) -> float:
    """The largest current mass of any star on the isochrone, whatever its phase: no normal star of the
    cluster can be heavier."""
    return round(float(iso["star_mass"].max()), 4)


# --- masses -------------------------------------------------------------------------------------


def member_masses(
    colour: Array, g: Array, fit: Mapping[str, Any], grid: Grid, settings: Mapping[str, Any] | None = None
) -> dict[str, Array]:
    """Each star's distance (in error units) to the fitted isochrone and the phase of its nearest point,
    whether it is within ``inlier_distance`` of it, and the initial and current mass of that point, but
    only for a star within ``mass_distance_max``: a star further off (a white dwarf, a photometric
    binary, an interloper) is not one the isochrone describes, so its masses are NaN. ``g_offset`` is the
    star's absolute G minus the lower main sequence's at its colour, negative above it (brighter: an unresolved
    companion that shines adds light, up to 0.75 mag for an equal pair); NaN off the main sequence's colours. Stars with no colour or G get NaN
    throughout."""
    s = settings or load_settings()
    c, m = np.asarray(colour, dtype=np.float64), np.asarray(g, dtype=np.float64)
    n = c.size
    out = {k: np.full(n, np.nan) for k in ("initial_mass", "star_mass", "phase", "distance", "g_offset")}
    ok = np.isfinite(c) & np.isfinite(m)
    if ok.any():
        ln = line(
            grid.isochrone(float(fit["feh"]), float(fit["log_age"])),
            s["phases"]["fit"],
            float(s["fit"]["densify_step"]),
        )
        floors = s["photometry"]
        matcher = _Matcher(ln, (float(floors["colour_floor"]), float(floors["g_floor"])), int(s["fit"]["neighbours"]))
        a_g, e_c = extinction(float(fit["av"]), s)
        abs_g = m[ok] - a_g - float(fit["distance_modulus"])
        ce, ge = photometric_errors(m[ok], s, abs_g)
        d2, idx = matcher.nearest(c[ok] - e_c, abs_g, ce, ge)
        out["g_offset"][ok] = abs_g - main_sequence_g(
            grid.isochrone(float(fit["feh"]), float(fit["log_age"])), c[ok] - e_c
        )
        near = np.sqrt(d2) <= float(s["fit"]["mass_distance_max"])
        out["initial_mass"][ok] = np.where(near, ln.initial_mass[idx], np.nan)
        out["star_mass"][ok] = np.where(near, ln.star_mass[idx], np.nan)
        out["phase"][ok] = ln.phase[idx]
        out["distance"][ok] = np.sqrt(d2)
    out["on_isochrone"] = np.asarray(out["distance"] <= float(s["fit"]["inlier_distance"]), dtype=np.float64)
    return out


def main_sequence_g(iso: Mapping[str, Array], colour0: Array, chunk: int = 2048) -> Array:
    """The absolute G of the isochrone's lower main sequence (and pre-main sequence) at each intrinsic
    colour: where several of its segments cross that colour (the turnoff's hook), the faintest, so a star
    lifted by a companion's light is measured from the sequence below it; NaN where none crosses."""
    keep = np.round(iso["phase"]) <= 0
    col, mag = (iso["bp"] - iso["rp"])[keep], iso["g"][keep]
    out = np.full(colour0.size, np.nan)
    if col.size < 2:
        return out
    a, b, ga, gb = col[:-1][None, :], col[1:][None, :], mag[:-1][None, :], mag[1:][None, :]
    for lo in range(0, colour0.size, chunk):  # stars by chunks: a cluster can have thousands of members
        x = colour0[lo : lo + chunk, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (x - a) / (b - a)
            g_at = np.where((t >= 0) & (t <= 1), ga + t * (gb - ga), -np.inf)
        faint = g_at.max(axis=1)
        out[lo : lo + chunk] = np.where(np.isfinite(faint), faint, np.nan)
    return out


def imf_weight(mass: Array, settings: Mapping[str, Any]) -> Array:
    """Kroupa's broken power law, continuous at the break."""
    fs = settings["field_stars"]
    brk = float(fs["imf_break_msun"])
    a1, a2 = (float(x) for x in fs["imf_slopes"])
    m = np.asarray(mass, dtype=np.float64)
    return np.where(m < brk, (m / brk) ** -a1, (m / brk) ** -a2)


def _weighted_quantiles(values: Array, weights: Array, qs: Sequence[float]) -> list[float]:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    c = np.cumsum(w)
    if c[-1] <= 0:
        return [math.nan] * len(qs)
    return [float(v[min(int(np.searchsorted(c, q * c[-1])), v.size - 1)]) for q in qs]


def field_star_estimates(
    abs_g: Array,
    colour0: Array,
    grid: Grid,
    settings: Mapping[str, Any] | None = None,
    feh: float | None = None,
    apparent_g: Array | None = None,
    modulus_err: Array | None = None,
) -> list[dict[str, Any]]:
    """Mass and age of stars with no cluster, from their absolute G and intrinsic BP-RP: weights over
    every grid point of the [Fe/H] nearest ``feh`` (default the settings'), each point weighted by the
    initial mass function times the span of initial mass it stands for, by the age prior, and by a
    Gaussian likelihood in the star's errors. Each star gets the 16th, 50th and 84th percentiles of
    initial mass, current mass and log age, the most likely phase, and ``off_grid`` when no point lies
    within ``off_grid_distance`` errors (a white dwarf, a binary, bad photometry).

    The photometric errors grow with the star's apparent G (``apparent_g``; absolute G when it is not
    given), and the error of its distance modulus from the parallax (``modulus_err``, magnitudes) adds
    in quadrature to its G error."""
    s = settings or load_settings()
    fs = s["field_stars"]
    target = float(fs["feh"] if feh is None else feh)
    chosen = min(grid.fehs(), key=lambda x: (abs(x - target), x))
    phases = s["phases"]["fit"]
    pts: dict[str, list[Array]] = {k: [] for k in ("colour", "mag", "mi", "ms", "age", "phase", "w")}
    for age in grid.ages(chosen):
        iso = grid.isochrone(chosen, age)
        keep = np.isin(np.round(iso["phase"]).astype(int), list(phases))
        if keep.sum() < 2:
            continue
        mi = iso["initial_mass"][keep]
        span = np.abs(np.gradient(mi))
        prior_age = 10**age if fs["age_prior"] == "uniform-in-age" else 1.0
        pts["colour"].append((iso["bp"] - iso["rp"])[keep])
        pts["mag"].append(iso["g"][keep])
        pts["mi"].append(mi)
        pts["ms"].append(iso["star_mass"][keep])
        pts["age"].append(np.full(mi.size, age))
        pts["phase"].append(iso["phase"][keep])
        pts["w"].append(imf_weight(mi, s) * span * prior_age)
    p = {k: np.concatenate(v) for k, v in pts.items()}
    p["w"] = p["w"] / p["w"].sum()
    c0, mg = np.asarray(colour0, dtype=np.float64), np.asarray(abs_g, dtype=np.float64)
    out: list[dict[str, Any]] = []
    cap = float(fs["off_grid_distance"]) ** 2
    chunk = int(fs["chunk"])
    observed = mg if apparent_g is None else np.asarray(apparent_g, dtype=np.float64)
    ce_all, ge_all = photometric_errors(observed, s, mg)
    if modulus_err is not None:
        ge_all = np.hypot(ge_all, np.nan_to_num(np.asarray(modulus_err, dtype=np.float64), nan=0.0))
    for a in range(0, c0.size, chunk):
        c, m, ce, ge = c0[a : a + chunk], mg[a : a + chunk], ce_all[a : a + chunk], ge_all[a : a + chunk]
        d2 = ((c[:, None] - p["colour"][None, :]) / ce[:, None]) ** 2 + (
            (m[:, None] - p["mag"][None, :]) / ge[:, None]
        ) ** 2
        for r in range(c.size):
            if not (math.isfinite(c[r]) and math.isfinite(m[r])):
                out.append({"feh": chosen, "off_grid": True})
                continue
            nearest = float(d2[r].min())
            w = p["w"] * np.exp(-0.5 * (d2[r] - nearest))
            q_mi = _weighted_quantiles(p["mi"], w, (0.16, 0.5, 0.84))
            q_ms = _weighted_quantiles(p["ms"], w, (0.16, 0.5, 0.84))
            q_age = _weighted_quantiles(p["age"], w, (0.16, 0.5, 0.84))
            phase_w = {int(ph): float(w[np.round(p["phase"]) == ph].sum()) for ph in np.unique(np.round(p["phase"]))}
            out.append(
                {
                    "feh": chosen,
                    "initial_mass_msun": [round(x, 4) for x in q_mi],
                    "star_mass_msun": [round(x, 4) for x in q_ms],
                    "log_age": [round(x, 3) for x in q_age],
                    "phase": max(phase_w, key=lambda k: (phase_w[k], -k)),
                    "nearest_distance": round(math.sqrt(nearest), 3),
                    "off_grid": nearest > cap,
                }
            )
    return out


# --- known answers ------------------------------------------------------------------------------


def _names(cluster_names: Iterable[str]) -> set[str]:
    """Every name a cluster goes by, lower case, with and without a catalogue prefix ("hr23:")."""
    return {
        n.strip().lower()
        for name in cluster_names
        for part in str(name).split(",")
        for n in [part, part.split(":", 1)[-1]]
        if n.strip()
    }


def metallicity_prior(
    cluster_names: Iterable[str],
    ra_deg: Any,
    dec_deg: Any,
    distance_pc: float | None,
    settings: Mapping[str, Any],
) -> dict[str, Any] | None:
    """A Gaussian prior on the fitted [Fe/H] (``mean``, ``sigma``, ``source``). A Gaia colour-magnitude
    diagram of a few dozen stars hardly fixes metallicity: it trades against dust, and the models' own
    errors (the colours of red dwarfs, the horizontal branch) pull the fit to the grid's ends. So:

    * a cluster the settings know by name takes its spectroscopic [Fe/H], turned into the solar-scaled
      metallicity MIST uses with Salaris et al. (1993): [M/H] = [Fe/H] + log10(0.638 f + 0.362), f = 10^[a/Fe];
    * otherwise a cluster within ``z_max_kpc`` of the Galactic plane takes the disc's radial gradient at
      its Galactocentric radius;
    * and one further out the halo's broad distribution.

    ``ra_deg`` and ``dec_deg`` are the members' positions (or one position); their mean is taken on the
    sphere, so a cluster across RA 0 is placed right. None without a position or the settings block."""
    mp = settings.get("metallicity_prior")
    if not mp:
        return None
    tokens = _names(cluster_names)
    for entry in mp.get("known", []):
        if tokens & {n.lower() for n in entry["names"]}:
            f = 10 ** float(entry.get("alpha_fe", 0.0))
            mean = float(entry["feh"]) + math.log10(0.638 * f + 0.362)
            return {"mean": round(mean, 3), "sigma": float(entry["sigma"]), "source": f"known: {entry['names'][0]}"}
    if ra_deg is None or dec_deg is None or distance_pc is None or not math.isfinite(float(distance_pc)):
        return None
    ra = np.radians(np.atleast_1d(np.asarray(ra_deg, dtype=float)))
    dec = np.radians(np.atleast_1d(np.asarray(dec_deg, dtype=float)))
    ok = np.isfinite(ra) & np.isfinite(dec)
    if not ok.any():
        return None
    vx, vy, vz = (float(np.mean(v[ok])) for v in (np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)))
    ra_deg, dec_deg = math.degrees(math.atan2(vy, vx)) % 360.0, math.degrees(math.atan2(vz, math.hypot(vx, vy)))
    import astropy.units as u
    from astropy.coordinates import Galactocentric, SkyCoord

    disc = mp["disc"]
    here = SkyCoord(ra=float(ra_deg) * u.deg, dec=float(dec_deg) * u.deg, distance=float(distance_pc) * u.pc)
    gc = here.transform_to(Galactocentric(galcen_distance=float(disc["r_sun_kpc"]) * u.kpc))
    r_kpc = float(np.hypot(gc.x.to_value(u.kpc), gc.y.to_value(u.kpc)))
    z_kpc = float(gc.z.to_value(u.kpc))
    if abs(z_kpc) <= float(disc["z_max_kpc"]):
        mean = float(disc["feh_at_r_sun"]) + float(disc["gradient_dex_per_kpc"]) * (r_kpc - float(disc["r_sun_kpc"]))
        return {
            "mean": round(mean, 3),
            "sigma": float(disc["sigma"]),
            "source": f"disc gradient at R = {r_kpc:.2f} kpc, z = {z_kpc:.2f} kpc",
        }
    halo = mp["halo"]
    return {
        "mean": float(halo["mean"]),
        "sigma": float(halo["sigma"]),
        "source": f"halo at R = {r_kpc:.2f} kpc, z = {z_kpc:.2f} kpc",
    }


def known_answer(
    cluster_names: Iterable[str], fit: Mapping[str, Any] | None, settings: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The settings' sanity window for a famous cluster this one matches by name, and whether the fitted
    age (and [Fe/H], where the window gives one) falls inside it; None when it matches none."""
    tokens = _names(cluster_names)
    for entry in settings["known_answers"]["clusters"]:
        if tokens & {n.lower() for n in entry["names"]}:
            lo, hi = (float(x) for x in entry["log_age"])
            age = None if fit is None else float(fit["log_age"])
            out: dict[str, Any] = {
                "cluster": entry["names"][0],
                "window_log_age": [lo, hi],
                "fitted_log_age": age,
                "inside": age is not None and lo <= age <= hi,
                "verified_window": bool(settings["known_answers"]["verified"]),
            }
            if "feh" in entry:
                f_lo, f_hi = (float(x) for x in entry["feh"])
                feh = None if fit is None else float(fit["feh"])
                out.update(window_feh=[f_lo, f_hi], fitted_feh=feh)
                out["inside"] = bool(out["inside"] and feh is not None and f_lo <= feh <= f_hi)
            return out
    return None
