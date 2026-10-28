"""T5: archive downloads are validated before parsing and parsed out of process under limits (NFR-009, §15)."""

from __future__ import annotations

import pyarrow as pa
import pytest

from sdp.adapters.archives.tap import TapArchiveAdapter
from sdp.adapters.archives.transport import HttpResponse
from sdp.adapters.archives.untrusted import IsolatedVotableParser, UntrustedInput, validate_votable
from sdp.adapters.archives.votable import TapQueryError, parse, to_votable

from .conftest import GAIA, FixtureTap, gaia_native, gaia_query

pytestmark = [pytest.mark.tier("T5"), pytest.mark.req("NFR-009")]

SMALL = pa.table({"source_id": [1, 2, 3], "ra": [10.0, 10.1, None]})

LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<VOTABLE version="1.4"><RESOURCE><TABLE><FIELD name="x" datatype="char" arraysize="*"/>
<DATA><TABLEDATA><TR><TD>&lol2;</TD></TR></TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>"""

TAP_ERROR = b"""<?xml version="1.0"?>
<VOTABLE version="1.4"><RESOURCE type="results"><INFO name="QUERY_STATUS" value="ERROR">Table gaiadr3.nope does not exist</INFO></RESOURCE></VOTABLE>"""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"PAR1\x00\x00", "not an XML VOTable"),
        (b"<html><body>Service unavailable</body></html>", "not an XML VOTable"),
        (LAUGHS, "no DTD"),
        (b"<?xml version='1.0'?>" + b" " * 2048, "exceeds"),
    ],
)
def test_implausible_documents_are_refused_before_any_parser_sees_them(body: bytes, message: str) -> None:
    with pytest.raises(UntrustedInput, match=message):
        validate_votable(body, max_bytes=1024)


def test_the_isolated_parser_returns_what_the_in_process_parser_does() -> None:
    body = to_votable(SMALL)
    assert IsolatedVotableParser()(body).equals(parse(body))


def test_a_tap_error_document_keeps_its_meaning_across_the_process_boundary() -> None:
    with pytest.raises(TapQueryError, match="does not exist"):
        IsolatedVotableParser()(TAP_ERROR)


def test_a_document_that_breaks_the_parser_fails_in_the_child() -> None:
    with pytest.raises(UntrustedInput, match="failed in isolation"):
        IsolatedVotableParser()(b"<?xml version='1.0'?><VOTABLE><RESOURCE><TABLE><FIELD")


def test_the_child_runs_under_the_configured_address_space_limit() -> None:
    body = to_votable(SMALL)
    # 64 MiB is below what the interpreter needs to load the parser, so the limit visibly applies
    with pytest.raises(UntrustedInput, match="failed in isolation"):
        IsolatedVotableParser(memory_bytes=64 * 1024**2)(body)
    assert IsolatedVotableParser(memory_bytes=2 * 1024**3)(body).num_rows == 3


def test_the_gateway_refuses_a_hostile_response_and_parses_others_in_isolation() -> None:
    tap = FixtureTap()
    adapter = TapArchiveAdapter({"gaia": GAIA}, tap, parser=IsolatedVotableParser())
    handle = adapter.query(gaia_query())
    tap.prime(adapter, handle, gaia_native())
    assert sum(b.num_rows for b in adapter.fetch(handle)) > 0
    key = adapter.chunk_request(handle, 0).key()
    tap.responses[key] = HttpResponse(200, LAUGHS, {})
    with pytest.raises(UntrustedInput, match="no DTD"):
        list(adapter.fetch(handle))
