"""A solved frame's WCS as FITS cards, and the header-only ``.wcs`` file that carries them (#71).

The ``.wcs`` file follows the convention astrometry.net uses: a primary header with no pixels, holding the
WCS keywords and IMAGEW and IMAGEH for the frame's size. astropy reads it with ``WCS(fits.getheader(path))``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

WCS_SUFFIX = ".wcs"
#: keywords that describe a frame's sky position; a copy keeps none of the old ones
_WCS_PREFIXES = ("CTYPE", "CRVAL", "CRPIX", "CDELT", "CROTA", "CUNIT", "CD1_", "CD2_", "PC1_", "PC2_")
_WCS_KEYS = ("RADESYS", "EQUINOX", "LONPOLE", "LATPOLE", "WCSAXES", "A_ORDER", "B_ORDER", "AP_ORDER", "BP_ORDER")


def is_wcs(key: str) -> bool:
    if key in _WCS_KEYS or key.startswith(_WCS_PREFIXES):
        return True
    head, _, rest = key.partition("_")
    return head in ("A", "B", "AP", "BP") and rest.replace("_", "").isdigit()


def wcs_cards(solution: Mapping[str, Any]) -> list[tuple[str, Any, str]]:
    """The solution's WCS and SDP's provenance as FITS cards (keyword, value, comment): SDPSOLV the solver,
    SDPNMAT and SDPRMS its fit, SDPSRC and SDPFRAME the frame, SDPSHA its SHA-256, SDPDATE when it was
    solved. Text cards carry no comment, as a long value leaves no room for one."""
    wcs = solution["wcs"]
    cards: list[tuple[str, Any, str]] = [(k, v, "") for k, v in wcs.items() if k not in ("NAXIS1", "NAXIS2")]
    file = solution.get("file") or {}
    cards += [
        ("SDPSOLV", str(solution.get("solver", "")), ""),
        ("SDPNMAT", int(solution.get("matched", 0)), "Gaia stars matched"),
        ("SDPRMS", float(solution.get("rms_arcsec", 0.0)), "[arcsec] fit RMS"),
        ("SDPSRC", str(solution.get("source", "")), ""),
        ("SDPFRAME", str(solution.get("frame", "")), ""),
    ]
    if file.get("sha256"):
        cards.append(("SDPSHA", str(file["sha256"]), ""))
    if solution.get("written_at"):
        cards.append(("SDPDATE", str(solution["written_at"]), ""))
    # a FITS text card holds printable ASCII only: a folder named Andromède is written Androm\xe8de
    return [(k, _ascii(v) if isinstance(v, str) else v, c) for k, v, c in cards]


def _ascii(text: str) -> str:
    safe = text.encode("ascii", "backslashreplace").decode("ascii")
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in safe)


def write_wcs_file(path: Path, solution: Mapping[str, Any]) -> Path:
    """The ``.wcs`` header file of a solution; IMAGEW and IMAGEH give the frame's size."""
    from astropy.io import fits

    header = fits.Header()
    header["IMAGEW"] = (int(solution["wcs"]["NAXIS1"]), "frame width, pixels")
    header["IMAGEH"] = (int(solution["wcs"]["NAXIS2"]), "frame height, pixels")
    header["WCSAXES"] = (2, "celestial axes")
    for key, value, comment in wcs_cards(solution):
        header[key] = (value, comment) if comment else value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fits.PrimaryHDU(header=header).writeto(tmp, overwrite=True)
    tmp.replace(path)
    return path
