"""The weekly live smoke for the Investigate adapters (T11, DSI-008): one small question per service
at the Pleiades core, recorded as cassettes under ``<record>/<name>/`` like the TAP sources.

Each probe checks the shape of the answer, not its values: SIMBAD resolves a bright member by
name and by position, the registry lists TAP services there, MAST lists observations there, and
DASCH serves a light curve with every column ``DaschDialect`` reads and curates it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sdp.adapters.archives.dasch import LIGHTCURVE_COLUMNS, DaschDialect, DaschGateway, csv_lines
from sdp.adapters.archives.mast import MastObservationGateway
from sdp.adapters.archives.registry import RegTapRegistry
from sdp.adapters.archives.simbad import SimbadResolver
from sdp.adapters.archives.smoke import SMOKE_CONE, SmokeResult
from sdp.adapters.archives.transport import CassetteTransport, Transport
from sdp.kernel.domain.investigation import Field, ObservationCriteria
from sdp.kernel.ports.investigate import ObservationTarget
from sdp.kernel.science import products

#: Alcyone (η Tau), the brightest Pleiad, inside the smoke cone.
STAR = ObservationTarget("smoke-alcyone", 56.871152, 24.105136)
STAR_NAME = "Alcyone"


def _simbad(transport: Transport) -> list[str]:
    resolver = SimbadResolver(transport)
    problems = []
    if resolver.by_name(STAR_NAME) is None:
        problems.append(f"simbad: {STAR_NAME} did not resolve by name")
    hit = resolver.by_position(STAR.ra_deg, STAR.dec_deg, 5.0)
    if hit is None or not hit.object_type:
        problems.append("simbad: nothing with a type within 5 arcsec of Alcyone")
    return problems


def _registry(transport: Transport) -> list[str]:
    services = RegTapRegistry(transport).services(Field(SMOKE_CONE), "tap")
    return [] if services else ["regtap: no TAP service claims coverage of the Pleiades"]


def _mast(transport: Transport) -> list[str]:
    index = MastObservationGateway(transport, max_records=20).search(STAR, ObservationCriteria(radius_arcsec=30.0))
    return [] if index.records else ["mast: ObsCore lists no observation of Alcyone"]


def _dasch(transport: Transport) -> list[str]:
    gateway = DaschGateway(transport)
    index = gateway.search(STAR, ObservationCriteria(radius_arcsec=10.0))
    if not index.records:
        return ["dasch: no reference star within 10 arcsec of Alcyone"]
    fetched = gateway.fetch(index.records[0].product)
    rows = csv_lines(fetched.data, "lightcurve")
    missing = sorted(LIGHTCURVE_COLUMNS - set(rows[0] if rows else ()))
    if missing:
        return [f"dasch: light curve lacks columns {missing}"]
    dialect = DaschDialect()
    curated_lc = dialect.curate(products.LIGHTCURVE, fetched)
    curated_pos = dialect.curate(products.ASTROMETRY, fetched)
    problems = []
    if curated_lc is None or len(products.parse_lightcurve(curated_lc.data).mjd) == 0:
        problems.append("dasch: the dialect kept no light-curve epochs")
    if curated_pos is None or len(products.parse_positions(curated_pos.data).epoch_jyear) == 0:
        problems.append("dasch: the dialect kept no position epochs")
    return problems


PROBES: dict[str, Callable[[Transport], list[str]]] = {
    "simbad": _simbad,
    "regtap": _registry,
    "mast": _mast,
    "dasch": _dasch,
}


def run_investigate_smoke(live: Callable[[str], Transport], record: Path) -> list[SmokeResult]:
    results = []
    for name, probe in PROBES.items():
        recorder = CassetteTransport(record / name, "record", inner=live(name), recorder="weekly-live-smoke")
        try:
            problems = probe(recorder)
        except Exception as exc:
            problems = [f"{name}: {exc}"]
        results.append(SmokeResult(name, tuple(problems), None))
    return results
