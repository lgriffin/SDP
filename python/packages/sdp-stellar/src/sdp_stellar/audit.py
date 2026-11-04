"""ClaimAudit: do clusters that only one catalogue lists survive SDP's own tests? (STL-020)

Published cluster catalogues disagree: many objects appear in one catalogue and in no other. This
audit takes every catalogue SDP has stored (``hr23``, ``cga20``, ``cwnu23``), finds which of them
list each object, and searches the Gaia stars around every object that only one catalogue claims
with exactly the sweep's pipeline: the sweep's quality cuts, feature policy, HDBSCAN grid, pass marks
and per-candidate significance test (STL-011), with its distance shell's parallax window and tile
size (STL-012), but on a field centred on the claim. The same is done for a seeded sample of objects
two or more catalogues agree on: that control says how often SDP recovers a cluster everyone accepts,
so the single-claim survival rate can be read against it rather than against 100%.

A claim survives when a candidate the search finds matches it, passes every check, can be tested,
and is more significant than every false candidate the field's own null fields produced (the same
one-by-one rule the sweep applies to potentially new candidates). A null survivor anywhere in the
claim's field counts, not only near the claim, so the rule is as strict as the sweep's over a field
of about a tile's size. Every result is EXPLORATORY: a claim that does not survive is a reason to
look again, not a retraction.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any

import astropy.units as u
import numpy as np
import pyarrow as pa
from astropy.coordinates import SkyCoord

from sdp.kernel.ports.compute import ClusterSearchAlgorithm
from sdp_stellar import analysis
from sdp_stellar import benchmark as bm
from sdp_stellar.features import PolicyError
from sdp_stellar.null import null_field
from sdp_stellar.quality import QualityCuts, column
from sdp_stellar.sweep import LABEL, Scale, labellings_at, scales, tile_seed

SCHEMA = "sdp-stellar/audit-claim@1"

#: what an audited claim comes out as, best first
VERDICTS = ("survives", "below_null", "untestable", "fails_checks", "not_recovered", "unassessable")


def load_settings() -> dict[str, Any]:
    """The sweep's settings (cuts, policy, grid, pass marks, significance, scales) plus the audit's own
    (``audit.json``): how catalogues are matched to each other, the control sample and null fields."""
    from sdp_stellar.sweep import load_settings as sweep_settings

    own: dict[str, Any] = json.loads(resources.files("sdp_stellar").joinpath("audit.json").read_text("utf-8"))
    return {**sweep_settings(), "audit": own}


@dataclass(frozen=True)
class Claim:
    """One object as one catalogue lists it, and every catalogue that lists it too."""

    catalogue: str
    name: str
    ra_deg: float
    dec_deg: float
    pm_ra_cosdec_masyr: float
    pm_dec_masyr: float
    parallax_mas: float | None
    claimed_by: tuple[str, ...]
    #: the other catalogues' rows it matches, as ``catalogue:name``
    partners: tuple[str, ...] = ()

    @property
    def claim_id(self) -> str:
        return f"{self.catalogue}:{self.name}"

    @property
    def agreement(self) -> int:
        return len(self.claimed_by)

    def centre(self) -> dict[str, float]:
        return {
            "ra_deg": self.ra_deg,
            "dec_deg": self.dec_deg,
            "pm_ra_cosdec_masyr": self.pm_ra_cosdec_masyr,
            "pm_dec_masyr": self.pm_dec_masyr,
        }


def match_radius_deg(parallax_mas: float | None, doc: Mapping[str, Any]) -> float:
    """The match radius at an object's distance: the fixed ``match_radius_deg``, widened to what
    ``match_radius_pc`` subtends when the object is near enough for that to be larger, since the
    centres two catalogues give a nearby cluster several degrees across can differ by more than 0.1 deg."""
    radius = float(doc["match_radius_deg"])
    if parallax_mas is None or not math.isfinite(parallax_mas) or parallax_mas <= 0:
        return radius
    distance_pc = 1000.0 / parallax_mas
    return max(radius, math.degrees(float(doc["match_radius_pc"]) / distance_pc))


def _floats(table: pa.Table, name: str) -> np.ndarray:
    return column(table, name) if name in table.column_names else np.full(table.num_rows, np.nan)


def claims(benchmarks: Sequence[bm.Benchmark], doc: Mapping[str, Any]) -> list[Claim]:
    """Every object of every catalogue, with the catalogues that list it: another catalogue lists it
    when one of its clusters lies within the pair's match radius on the sky (the wider of the two
    objects' radii, so the match is the same seen from either side) and ``pm_window_masyr`` in proper
    motion. A row without a position or a proper motion cannot be matched: it is kept, claimed by its
    own catalogue alone, and the audit calls it unassessable."""
    window = float(doc["pm_window_masyr"])
    loaded = []
    for b in benchmarks:
        t = b.table
        ra, dec = column(t, "ra_deg"), column(t, "dec_deg")
        pmra, pmdec = column(t, "pm_ra_cosdec_masyr"), column(t, "pm_dec_masyr")
        plx = _floats(t, "parallax_mas")
        ok = np.isfinite(ra) & np.isfinite(dec) & np.isfinite(pmra) & np.isfinite(pmdec)
        radii = np.array([match_radius_deg(float(p) if np.isfinite(p) else None, doc) for p in plx])
        names = [str(n) for n in t.column("name").to_pylist()]
        loaded.append((b.benchmark_id, names, ra, dec, pmra, pmdec, plx, ok, radii))
    out: list[Claim] = []
    for own, names, ra, dec, pmra, pmdec, plx, ok, radii in loaded:
        rows = np.nonzero(ok)[0]
        listed: dict[int, set[str]] = {int(r): {own} for r in range(len(names))}
        partners: dict[int, set[str]] = {int(r): set() for r in range(len(names))}
        for other, onames, ora, odec, opmra, opmdec, _, ook, oradii in loaded:
            orows = np.nonzero(ook)[0]
            if other == own or not orows.size or not rows.size:
                continue
            here = SkyCoord(ra[rows] * u.deg, dec[rows] * u.deg)
            there = SkyCoord(ora[orows] * u.deg, odec[orows] * u.deg)
            reach = max(float(radii[rows].max()), float(oradii[orows].max()))
            i, j, sep, _ = there.search_around_sky(here, reach * u.deg)
            for a, b, sep_deg in zip(np.asarray(i), np.asarray(j), np.asarray(sep.deg), strict=True):
                ra_row, ob_row = int(rows[a]), int(orows[b])
                if sep_deg > max(radii[ra_row], oradii[ob_row]):
                    continue
                if math.hypot(opmra[ob_row] - pmra[ra_row], opmdec[ob_row] - pmdec[ra_row]) <= window:
                    listed[ra_row].add(other)
                    partners[ra_row].add(f"{other}:{onames[ob_row]}")
        for r in range(len(names)):
            p = float(plx[r])
            out.append(
                Claim(
                    own,
                    names[r],
                    float(ra[r]),
                    float(dec[r]),
                    float(pmra[r]),
                    float(pmdec[r]),
                    p if math.isfinite(p) else None,
                    tuple(sorted(listed[r])),
                    tuple(sorted(partners[r])),
                )
            )
    return out


def chosen(all_claims: Sequence[Claim], doc: Mapping[str, Any], seed: int) -> list[tuple[Claim, str]]:
    """The claims to audit and the group each is in: every claim only its own catalogue makes
    (``single``), and a seeded sample of ``control_sample`` claims two or more catalogues make
    (``control``). An object several catalogues list is sampled once, as the first catalogue in
    ``control_from`` that lists it gives it; and a row that shares a matching row of another catalogue
    with one already sampled is passed over, so the control holds no object twice."""
    singles = [(c, "single") for c in all_claims if c.agreement == 1]
    order = list(doc["control_from"])
    agreed = sorted(
        (
            c
            for c in all_claims
            if c.agreement >= 2 and c.catalogue in order and c.catalogue == next(x for x in order if x in c.claimed_by)
        ),
        key=lambda c: c.claim_id,
    )
    want = int(doc["control_sample"])
    shuffled = np.random.default_rng(seed).permutation(len(agreed)).tolist() if agreed else []
    used: set[str] = set()
    picked: list[Claim] = []
    for i in shuffled:
        if len(picked) >= want:
            break
        c = agreed[i]
        if used & set(c.partners):
            continue
        used.update(c.partners)
        picked.append(c)
    picked.sort(key=lambda c: c.claim_id)
    return singles + [(c, "control") for c in picked]


def placed(claim: Claim) -> bool:
    """Whether the claim has a finite position and proper motion, so it can be matched and searched."""
    return all(math.isfinite(v) for v in claim.centre().values())


def scale_for(claim: Claim, settings: Mapping[str, Any]) -> Scale | None:
    """The distance shell that owns the claim's parallax, or None when the catalogue gives none (or
    gives no position or proper motion)."""
    if claim.parallax_mas is None or not placed(claim):
        return None
    return next(s for s in scales(settings) if s.owns(np.array([claim.parallax_mas]))[0])


def field_radius_deg(scale: Scale) -> float:
    """A field centred on the claim as wide as the shell's tile with its rim."""
    return scale.tile_deg / 2.0 + scale.margin_deg


def in_field(table: pa.Table, claim: Claim, scale: Scale) -> np.ndarray:
    """Rows within the claim's field on the sky and inside its shell's parallax window."""
    near = SkyCoord(claim.ra_deg * u.deg, claim.dec_deg * u.deg).separation(
        SkyCoord(column(table, "ra_deg") * u.deg, column(table, "dec_deg") * u.deg)
    ).deg <= field_radius_deg(scale)
    return np.asarray(near & scale.searched(column(table, "parallax_mas")), dtype=bool)


def _passed(c: Mapping[str, Any]) -> bool:
    return bool(c["status"] != "rejected")


def _tested(c: Mapping[str, Any]) -> bool:
    return c.get("significance_sigma") is not None and c.get("cmd_coherence") is not None


def _best(matches: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The matching candidate the claim is judged by: one that passed before one that did not, then
    the most significant, then the largest."""
    if not matches:
        return None
    return min(
        matches,
        key=lambda c: (
            not _passed(c),
            -(c.get("significance_sigma") or -1.0),
            -int(c["n_members"]),
            str(c["candidate_id"]),
        ),
    )


def audit_claim(
    table: pa.Table,
    claim: Claim,
    group: str,
    settings: Mapping[str, Any],
    clusterers: Mapping[str, ClusterSearchAlgorithm],
    seed: int,
) -> dict[str, Any]:
    """One claim: cuts, its shell's field around it, the sweep grid, the claim's best matching
    candidate, and ``null_fields_per_claim`` null fields of the same field."""
    doc = settings["audit"]
    analysis.cmd_policy(settings)
    if analysis.significance_settings(settings) is None:
        raise ValueError("the audit needs the significance test (STL-011) in its settings")
    s = tile_seed(claim.claim_id, seed)
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "label": LABEL,
        "claim": {**asdict(claim), "claimed_by": list(claim.claimed_by), "claim_id": claim.claim_id},
        "group": group,
        "seed": s,
        "scale": None,
        "field_radius_deg": None,
        "stars": 0,
        "match": None,
        "candidates_found": 0,
        "null_fields": 0,
        "null_survivor_sigmas": [],
        "verdict": "unassessable",
        "reasons": [],
    }
    scale = scale_for(claim, settings)
    if not placed(claim):
        result["reasons"].append("the catalogue gives no position or proper motion, so nothing can be matched to it")
        return result
    if scale is None:
        result["reasons"].append("the catalogue gives no parallax, so no distance shell can be chosen")
        return result
    if table.num_rows == 0:
        result["reasons"].append("no prepared stars around the claim")
        return result
    result["scale"], result["field_radius_deg"] = scale.name, field_radius_deg(scale)
    kept, _ = QualityCuts.from_parameters(settings["quality_cuts"]).apply(table)
    kept = kept.filter(pa.array(in_field(kept, claim, scale)))
    result["stars"] = kept.num_rows
    least = 2 * int(settings["candidate_checks"]["min_members"])
    if kept.num_rows < least:
        result["reasons"].append(f"{kept.num_rows} stars pass the cuts in the field, fewer than {least}")
        return result
    most = settings.get("max_tile_stars")
    if most is not None and kept.num_rows > int(most):
        result["reasons"].append(f"{kept.num_rows} stars pass the cuts, more than max_tile_stars {int(most)}")
        return result
    try:
        features, labellings = labellings_at(kept, claim.ra_deg, claim.dec_deg, settings, clusterers, s)
    except PolicyError as exc:
        result["reasons"].append(str(exc))
        return result
    found = analysis._assess(claim.claim_id, kept, labellings, [], settings, s, features)
    result["candidates_found"] = len(found)
    radius, window = match_radius_deg(claim.parallax_mas, doc), float(doc["pm_window_masyr"])
    target = bm.Benchmark(
        claim.catalogue,
        "",
        "",
        "",
        pa.table({"name": [claim.name], **{k: [v] for k, v in claim.centre().items()}}),
    )
    matches = [c for c in found if c["centre"] and bm.cross_match(c["centre"], target, radius, window)["matches"]]
    best = _best(matches)

    sigmas: list[float | None] = []
    nulls = int(doc["null_fields_per_claim"])
    for k in range(nulls):
        shuffled = null_field(kept, s + 1 + k)
        try:
            null_features, null_labellings = labellings_at(
                shuffled, claim.ra_deg, claim.dec_deg, settings, clusterers, s + 1 + k
            )
        except PolicyError:
            continue
        false = analysis._assess(claim.claim_id + "-null", shuffled, null_labellings, [], settings, s, null_features)
        sigmas += [c.get("significance_sigma") for c in false if _passed(c)]
    result["null_fields"], result["null_survivor_sigmas"] = nulls, sigmas

    if best is not None:
        result["match"] = {
            k: best.get(k)
            for k in (
                "candidate_id",
                "status",
                "n_members",
                "stability",
                "cmd_coherence",
                "significance_sigma",
                "centre",
                "reasons",
            )
        }
    verdict, reasons = judge(best, sigmas, nulls)
    result["verdict"], result["reasons"] = verdict, reasons
    return result


def judge(best: Mapping[str, Any] | None, null_sigmas: Sequence[float | None], nulls: int) -> tuple[str, list[str]]:
    """The claim's verdict from its best matching candidate and its field's null survivors."""
    if best is None:
        return "not_recovered", ["no candidate in the field matches the claimed centre and proper motion"]
    if not _passed(best):
        return "fails_checks", list(best["reasons"])
    if not _tested(best):
        return "untestable", ["too few members or field stars for the significance or CMD check"]
    if nulls == 0:
        return "untestable", ["no null field was run"]
    sigma = float(best["significance_sigma"])
    if any(v is None for v in null_sigmas):
        return "below_null", ["a null field produced a surviving candidate the significance test could not assess"]
    worst = max((float(v) for v in null_sigmas if v is not None), default=None)
    if worst is not None and sigma <= worst:
        return "below_null", [f"significance {sigma:.1f} sigma is no higher than a false candidate's {worst:.1f} sigma"]
    beaten = f"beats every null survivor (best {worst:.1f} sigma)" if worst is not None else "no null survivor"
    return "survives", [f"recovered at {sigma:.1f} sigma, passed every check, {beaten}"]


def wilson(hits: int, n: int, z: float = 1.959964) -> list[float] | None:
    """The 95% Wilson score interval of a share, or None with nothing to share."""
    if n == 0:
        return None
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def summarise(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Survival per group and catalogue. The share is of claims that could be assessed (a claim with no
    parallax or no stars is left out of it); ``recovered`` is the share SDP found at all."""
    groups: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for r in results:
        cat = str(r["claim"]["catalogue"])
        groups.setdefault(str(r["group"]), {}).setdefault(cat, []).append(r)
        groups[str(r["group"])].setdefault("all", []).append(r)

    def stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
        assessed = len(rows) - counts["unassessable"]
        found = assessed - counts["not_recovered"]
        return {
            "claims": len(rows),
            "verdicts": counts,
            "assessed": assessed,
            "survival": counts["survives"] / assessed if assessed else None,
            "survival_95": wilson(counts["survives"], assessed),
            "recovered": found / assessed if assessed else None,
        }

    out = {g: {c: stats(rows) for c, rows in sorted(by.items())} for g, by in sorted(groups.items())}
    control = out.get("control", {}).get("all", {}).get("survival")
    single = out.get("single", {}).get("all", {}).get("survival")
    return {
        "label": LABEL,
        "groups": out,
        "single_vs_control": single / control if single is not None and control else None,
    }


def claim_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One flat row per audited claim: single claims first, then by verdict and catalogue."""
    rows = []
    for r in results:
        c, m = r["claim"], r.get("match") or {}
        rows.append(
            {
                "claim_id": c["claim_id"],
                "group": r["group"],
                "catalogue": c["catalogue"],
                "claimed_by": " ".join(c["claimed_by"]),
                "verdict": r["verdict"],
                "ra_deg": c["ra_deg"],
                "dec_deg": c["dec_deg"],
                "parallax_mas": c["parallax_mas"],
                "scale": r["scale"],
                "stars": r["stars"],
                "members": m.get("n_members"),
                "significance_sigma": m.get("significance_sigma"),
                "worst_null_sigma": max((v for v in r["null_survivor_sigmas"] if v is not None), default=None),
                "reasons": "; ".join(r["reasons"]),
            }
        )
    order = {v: i for i, v in enumerate(VERDICTS)}
    rows.sort(key=lambda row: (row["group"] != "single", order[row["verdict"]], row["claim_id"]))
    return rows
