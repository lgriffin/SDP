"""INV-011: an ecosystem package is bound once, behind one port, inside sdp.adapters (Investigate §8).

The domain, application layer, science modules, CLI, API and fakes may use only the Astropy core
allow-list (units, constants, coordinates, time, stats, wcs, regions): value semantics, not I/O.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.tier("T0")

PACKAGES = Path(__file__).resolve().parents[2] / "packages"

#: Packages SDP binds behind a port rather than importing (Investigate §7).
BOUND = frozenset(
    {
        "astroquery",
        "pyvo",
        "daschlab",
        "astrocut",
        "lightkurve",
        "photutils",
        "tweakwcs",
        "drizzlepac",
        "specutils",
        "specreduce",
        "stdatamodels",
        "asdf",
        "gwcs",
        "reproject",
        "jdaviz",
        "glue",
        "ginga",
        "synphot",
        "stpsf",
        "webbpsf",
        "jwst",
        "romancal",
        "crds",
    }
)

#: Astropy modules the domain may treat as libraries, plus astropy.timeseries, which Investigate §7
#: names as the analysis library (retrieval goes through a gateway, analysis runs as TaskKinds).
ASTROPY_ALLOWED = frozenset({"units", "constants", "coordinates", "time", "stats", "wcs", "timeseries"})


def _imports(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, node.module


def violation(module: str) -> str | None:
    top, _, rest = module.partition(".")
    if top in BOUND:
        return f"{top} is a bound ecosystem package"
    if top == "astropy" and rest:
        sub = rest.split(".")[0]
        if sub not in ASTROPY_ALLOWED:
            return f"astropy.{sub} is outside the domain-library allow-list"
    return None


def violations(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "/sdp/adapters/" in f"/{rel}":
            continue
        for lineno, module in _imports(path):
            problem = violation(module)
            if problem:
                found.append(f"{rel}:{lineno}: {module} ({problem})")
    return found


@pytest.mark.req("INV-011")
def test_bound_packages_are_imported_only_inside_adapters() -> None:
    assert violations(PACKAGES) == []


@pytest.mark.req("INV-011")
def test_the_rule_catches_a_bound_package_outside_adapters(tmp_path: Path) -> None:
    kernel = tmp_path / "sdp-kernel" / "src" / "sdp" / "kernel"
    kernel.mkdir(parents=True)
    (kernel / "leak.py").write_text("from astroquery.mast import Observations\nimport astropy.io.fits\n")
    adapters = tmp_path / "sdp-adapters" / "src" / "sdp" / "adapters"
    adapters.mkdir(parents=True)
    (adapters / "mast.py").write_text("from astroquery.mast import Observations\n")
    found = violations(tmp_path)
    assert len(found) == 2
    assert all("kernel/leak.py" in f for f in found)


@pytest.mark.req("INV-011")
def test_the_astropy_core_allow_list_stays_importable_everywhere() -> None:
    for module in ("astropy.units", "astropy.coordinates", "astropy.wcs.utils", "astropy", "regions"):
        assert violation(module) is None
    assert violation("astropy.io.fits") is not None
    assert violation("photutils.detection") is not None
