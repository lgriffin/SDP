"""Writing the Markdown reports people open on Windows (#201).

Reports are UTF-8. Without a byte-order mark, Windows PowerShell 5.1 (``Get-Content``, ``type``),
older Notepad and Excel read them in the ANSI code page, and ``±`` shows as ``Â±``. The mark makes
those tools read UTF-8; Markdown viewers, GitHub and Python's ``utf-8-sig`` ignore it.
"""

from __future__ import annotations

from pathlib import Path

ENCODING = "utf-8-sig"


def write_markdown(path: Path, text: str) -> None:
    """Write a Markdown report as UTF-8 with a byte-order mark."""
    path.write_text(text, encoding=ENCODING)
