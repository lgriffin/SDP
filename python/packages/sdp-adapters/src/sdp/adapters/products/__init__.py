"""Scientific product model adapters (Integration v0.4.0 §3, §6, ADR-018, ADR-021).

``JwstDialect`` fills the ObsCore-shaped envelope for JWST pipeline products and is the Investigate
``ProductDialect`` for them, ``GaiaDataLinkDialect`` does the same for Gaia DR3's DataLink
VOTables and ``WiseLightCurveDialect`` for IRSA's WISE and NEOWISE light curves; ``EnvelopeValidator`` checks a filled envelope against the raw file and
the envelope rules and writes the ``SemanticValidationReport`` that curation requires.
"""

from sdp.adapters.products.fits import HduInfo, read_hdus
from sdp.adapters.products.gaia import GaiaDataLinkDialect
from sdp.adapters.products.jwst import JwstDialect
from sdp.adapters.products.validator import EnvelopeValidator
from sdp.adapters.products.wise import WiseLightCurveDialect

__all__ = ["EnvelopeValidator", "GaiaDataLinkDialect", "HduInfo", "JwstDialect", "WiseLightCurveDialect", "read_hdus"]
