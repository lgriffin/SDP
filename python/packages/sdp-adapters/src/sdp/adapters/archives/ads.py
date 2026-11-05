"""NASA ADS, the astrophysics literature index, through the archive ``Transport`` (Discovery Roadmap C3).

The search API (https://ui.adsabs.harvard.edu/help/api/) answers a Solr-style query with matching
records and, on request, facet counts. A personal API token is required: it is read from the
environment (``ADS_API_TOKEN``, or ``ADS_DEV_KEY`` as astroquery names it), never from Git, sent only
in the ``Authorization`` header, and never written to a cassette (``write_cassette`` drops it), a log
or an error message. The cassette key is the URL, so a recording replays whatever token recorded it.

ADS allows 5,000 searches a day per token; every answer reports how many are left, and a caller that
runs many queries stops before the allowance is spent rather than being refused part way.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sdp.adapters.archives.transport import HttpRequest, HttpResponse, Transport, TransportError
from sdp.adapters.archives.untrusted import UntrustedInput

ADS_API = "https://api.adsabs.harvard.edu/v1"
TOKEN_VARIABLES = ("ADS_API_TOKEN", "ADS_DEV_KEY")
MAX_RESPONSE_BYTES = 16 * 1024**2
MAX_ROWS = 2000


class AdsTokenMissing(RuntimeError):
    """No ADS token in the environment."""


class AdsRefused(TransportError):
    """ADS refused the token (401/403) or the daily allowance is spent (429)."""


@dataclass(frozen=True)
class AdsAnswer:
    query: str
    num_found: int
    docs: tuple[Mapping[str, Any], ...]
    #: facet field -> value -> count, as ADS gives them (values in lower case)
    facets: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    #: searches left today, when ADS said (a replayed recording does not)
    remaining: int | None = None


def token_from_env(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    for name in TOKEN_VARIABLES:
        value = (env.get(name) or "").strip()
        if value:
            return value
    raise AdsTokenMissing(
        "no NASA ADS token: put ADS_API_TOKEN=<your token> in the .env file at the top of your SDP checkout "
        "(it is gitignored), or set it in the environment. Get a token at https://ui.adsabs.harvard.edu/user/settings/token"
    )


def search_request(
    q: str,
    *,
    fields: Sequence[str] = ("bibcode", "title", "year", "property"),
    rows: int = 20,
    sort: str = "date desc",
    facet_fields: Sequence[str] = (),
    token: str | None = None,
) -> HttpRequest:
    if not q.strip():
        raise ValueError("an ADS query may not be empty")
    if not 0 <= rows <= MAX_ROWS:
        raise ValueError(f"rows must be between 0 and {MAX_ROWS}, not {rows}")
    params: list[tuple[str, str]] = [("q", q), ("fl", ",".join(fields)), ("rows", str(rows)), ("sort", sort)]
    if facet_fields:
        params += [("facet", "true"), ("facet.mincount", "1"), ("facet.limit", "100")]
        params += [("facet.field", f) for f in facet_fields]
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return HttpRequest("GET", f"{ADS_API}/search/query?{urllib.parse.urlencode(params)}", b"", headers)


def _remaining(response: HttpResponse) -> int | None:
    for key, value in response.headers.items():
        if key.lower() == "x-ratelimit-remaining":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _facets(doc: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    raw = (doc.get("facet_counts") or {}).get("facet_fields") or {}
    if not isinstance(raw, dict):
        raise UntrustedInput("ADS facet_fields is not an object")
    out: dict[str, dict[str, int]] = {}
    for name, flat in raw.items():
        if not isinstance(flat, list) or len(flat) % 2:
            raise UntrustedInput(f"ADS facet {name!r} is not a list of value, count pairs")
        out[str(name)] = {str(flat[i]).lower(): int(flat[i + 1]) for i in range(0, len(flat), 2)}
    return out


def parse_answer(q: str, response: HttpResponse) -> AdsAnswer:
    if response.status in (401, 403):
        raise AdsRefused(
            f"ADS refused the token (HTTP {response.status}); check ADS_API_TOKEN in your .env", response.status
        )
    if response.status == 429:
        raise AdsRefused("ADS says today's 5,000 searches are spent (HTTP 429); run again tomorrow", 429)
    if response.status != 200:
        raise TransportError(f"ADS search: HTTP {response.status}", response.status)
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise UntrustedInput(f"ADS answer of {len(response.body)} bytes exceeds {MAX_RESPONSE_BYTES}")
    doc = json.loads(response.body)
    if not isinstance(doc, dict) or not isinstance(doc.get("response"), dict):
        raise UntrustedInput("ADS answered without a response object")
    body = doc["response"]
    docs = body.get("docs", [])
    if not isinstance(docs, list) or not all(isinstance(d, dict) for d in docs):
        raise UntrustedInput("ADS docs is not a list of records")
    return AdsAnswer(
        query=q,
        num_found=int(body.get("numFound", 0)),
        docs=tuple(docs),
        facets=_facets(doc),
        remaining=_remaining(response),
    )


def search(
    transport: Transport,
    q: str,
    *,
    token: str,
    fields: Sequence[str] = ("bibcode", "title", "year", "property"),
    rows: int = 20,
    sort: str = "date desc",
    facet_fields: Sequence[str] = (),
) -> AdsAnswer:
    """One ADS search. The token is the caller's (``token_from_env``); it goes only into the header."""
    request = search_request(q, fields=fields, rows=rows, sort=sort, facet_fields=facet_fields, token=token)
    return parse_answer(q, transport.send(request))
