"""Every text file the packages read or write names its encoding (#201).

On Windows the default text encoding is the ANSI code page (cp1252), so a report written or read
without ``encoding`` turns ``±`` into ``Â±``. Ruff's PLW1514 catches ``open`` and ``Path(...)`` calls
it can type; this scan also catches ``read_text``/``write_text``/``open`` on any receiver.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted(p for p in (ROOT / "packages").glob("*/src/**/*.py") if "/contracts/v1/" not in p.as_posix())


def _text_mode(call: ast.Call, position: int) -> bool:
    mode: ast.expr | None = call.args[position] if len(call.args) > position else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return True
    return not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value)


def _path_open_in_text_mode(call: ast.Call) -> bool:
    """``x.open()`` or ``x.open("w")``: Path.open's shape. ``fits.open(data)`` and the like are left alone."""
    if not call.args and not call.keywords:
        return True
    mode = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and _text_mode(call, 0) and len(call.args) < 3


def unspecified(source: str) -> list[int]:
    """Line numbers of text reads and writes that leave the encoding to the platform."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or any(k.arg == "encoding" for k in node.keywords):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if (
                (func.attr == "read_text" and not node.args)
                or (func.attr == "write_text" and len(node.args) < 2)
                or (func.attr == "open" and _path_open_in_text_mode(node))
            ):
                lines.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id == "open" and len(node.args) < 4 and _text_mode(node, 1):
            lines.append(node.lineno)
    return lines


def test_the_scan_finds_a_platform_default_read() -> None:
    assert unspecified("from pathlib import Path\nPath('r.md').write_text('±')\n") == [2]
    assert unspecified("p.read_text()\np.read_text('utf-8')\np.open('rb')\nopen(x, 'w', encoding='utf-8')\n") == [1]


@pytest.mark.req("NFR-012")
@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_text_io_names_its_encoding(path: Path) -> None:
    found = unspecified(path.read_text("utf-8"))
    assert not found, f"{path.relative_to(ROOT)}: lines {found} read or write text without an encoding"


@pytest.mark.req("NFR-012")
def test_a_markdown_report_round_trips_plus_minus(tmp_path: Path) -> None:
    from sdp.cli.reports import write_markdown

    path = tmp_path / "report.md"
    write_markdown(path, "A = 0.0306 ± 0.0036 km/s/pc\n")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig") == "A = 0.0306 ± 0.0036 km/s/pc\n"
    # What Windows PowerShell 5.1 shows for the same bytes without the mark: the bug in #201.
    assert raw[3:].decode("cp1252") == "A = 0.0306 Â± 0.0036 km/s/pc\n"
