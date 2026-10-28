"""Scientific product model adapters (Integration v0.4.0 §3, §6, ADR-018, ADR-021).

``JwstDialect`` fills the ObsCore-shaped envelope for JWST pipeline products and is the Investigate
``ProductDialect`` for them; ``EnvelopeValidator`` checks a filled envelope against the raw file and
the envelope rules and writes the ``SemanticValidationReport`` that curation requires.
"""

from sdp.adapters.products.fits import HduInfo, read_hdus
from sdp.adapters.products.jwst import JwstDialect
from sdp.adapters.products.validator import EnvelopeValidator

__all__ = ["EnvelopeValidator", "HduInfo", "JwstDialect", "read_hdus"]
