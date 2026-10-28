"""The SDP Investigate plugin for Jdaviz Imviz (Investigate §7, INV-012).

The core (``field``, ``client``, ``profiles``) imports neither Jdaviz nor the kernel; ``plugin``
registers the tray plugin and is imported by :func:`attach`.
"""

from __future__ import annotations

from typing import Any

from sdp_imviz.client import Candidate, HttpInvestigateClient, InvestigateClient, InvestigateError, RunResult
from sdp_imviz.field import circle_region, footprint_region
from sdp_imviz.profiles import load_profiles

__all__ = [
    "Candidate",
    "HttpInvestigateClient",
    "InvestigateClient",
    "InvestigateError",
    "RunResult",
    "attach",
    "circle_region",
    "footprint_region",
    "load_profiles",
]


def attach(viz: Any, api: str | None = None, profiles_dir: str | None = None, client: Any = None) -> Any:
    """Add the Investigate plugin to a Jdaviz app (configured or not) and return its user API."""
    from sdp_imviz.plugin import attach as _attach

    return _attach(viz, api=api, profiles_dir=profiles_dir, client=client)
