"""HistoricalSourceGateway contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdp.kernel.domain.historical import (
    HistoricalDocument,
    TranscribedRecord,
    VerificationRecord,
    is_verified,
)
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.ports.archives import HistoricalSourceGateway

DOC = HistoricalDocument(
    "dyson-1920",
    SourceRef(
        SourceKind.DOCUMENT,
        "Dyson, Eddington and Davidson 1920",
        "Public domain",
        "Phil. Trans. A 220, 291",
        doi="10.1098/rsta.1920.0009",
    ),
    "A determination of the deflection of light by the Sun's gravitational field",
)
AT = datetime(2026, 10, 28, tzinfo=UTC)


class HistoricalSourcesContract:
    def test_registered_documents_are_returned(self, historical_sources: HistoricalSourceGateway) -> None:
        historical_sources.register(DOC)
        assert historical_sources.document("dyson-1920") == DOC
        assert historical_sources.document("unknown") is None

    @pytest.mark.req("REL-001")
    def test_transcriptions_keep_their_locator(self, historical_sources: HistoricalSourceGateway) -> None:
        historical_sources.register(DOC)
        rec = TranscribedRecord("r1", "dyson-1920", "p. 312, table VI, row 3", {"dx": "-0.22"}, "leigh", AT)
        historical_sources.add_transcription(rec)
        assert historical_sources.transcriptions("dyson-1920") == [rec]

    def test_unknown_document_cannot_be_transcribed(self, historical_sources: HistoricalSourceGateway) -> None:
        with pytest.raises(KeyError):
            historical_sources.add_transcription(TranscribedRecord("r9", "nope", "p. 1", {}, "leigh", AT))

    def test_second_pass_verification_by_someone_else(self, historical_sources: HistoricalSourceGateway) -> None:
        historical_sources.register(DOC)
        rec = TranscribedRecord("r2", "dyson-1920", "p. 312, table VI, row 4", {"dx": "0.10"}, "leigh", AT)
        historical_sources.add_transcription(rec)
        historical_sources.add_verification(VerificationRecord("r2", "leigh", AT, True))
        assert not is_verified(rec, tuple(historical_sources.verifications("r2")))
        historical_sources.add_verification(VerificationRecord("r2", "second-reader", AT, True))
        assert is_verified(rec, tuple(historical_sources.verifications("r2")))
