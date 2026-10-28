"""T0 for INV-012: the plugin is a driving adapter. It imports no analysis or archive package and no
kernel code, and only the client opens a connection, to sdp-api."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.tier("T0")

SOURCE = Path(__file__).resolve().parents[1] / "src" / "sdp_imviz"

#: Everything the plugin may import: the standard library pieces it uses, the viewer, and value types.
ALLOWED = {
    "__future__",
    "typing",
    "collections",
    "dataclasses",
    "json",
    "pathlib",
    "urllib",
    "yaml",
    "traitlets",
    "jdaviz",
    "astropy.coordinates",
    "astropy.table",
    "sdp_imviz",
}
NETWORK = {"urllib", "http", "socket", "requests", "httpx", "aiohttp"}


def imports() -> list[tuple[str, str]]:
    found = []
    for path in sorted(SOURCE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                found += [(path.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.append((path.name, node.module))
    return found


def allowed(module: str) -> bool:
    return any(module == a or module.startswith(a + ".") for a in ALLOWED)


@pytest.mark.req("INV-012")
def test_the_plugin_imports_no_analysis_archive_or_kernel_code() -> None:
    offending = [(f, m) for f, m in imports() if not allowed(m)]
    assert offending == [], f"the plugin may only render and call InvestigateField: {offending}"


@pytest.mark.req("INV-012")
def test_only_the_client_reaches_the_network() -> None:
    callers = {f for f, m in imports() if m.split(".")[0] in NETWORK}
    assert callers == {"client.py"}


def test_the_scan_sees_every_module() -> None:
    files = {f for f, _ in imports()}
    assert {"client.py", "field.py", "plugin.py", "profiles.py", "__init__.py"} <= files
