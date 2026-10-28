"""ArchiveGateway contract (§8). Each case is given a gateway plus a bounded query it can answer."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pyarrow as pa
import pytest

from sdp.kernel.domain.archive import CatalogueQuery, idempotency_key
from sdp.kernel.ports.archives import ArchiveGateway


@dataclass(frozen=True)
class GatewayCase:
    gateway: ArchiveGateway
    query: CatalogueQuery
    expected_rows: int | None = None


class ArchiveGatewayContract:
    @pytest.mark.req("DSI-001")
    def test_describe_names_release_licence_citation_and_limits(self, archive_case: GatewayCase) -> None:
        d = archive_case.gateway.describe(archive_case.query.source_id)
        assert d.release_label == archive_case.query.release_label
        assert d.licence.strip() and d.citation.strip()
        assert d.max_rows_per_call > 0 and d.min_seconds_between_calls >= 0
        assert d.dialect_id

    @pytest.mark.req("DSI-002")
    def test_query_renders_text_and_a_stable_idempotency_key(self, archive_case: GatewayCase) -> None:
        h1 = archive_case.gateway.query(archive_case.query)
        h2 = archive_case.gateway.query(archive_case.query)
        assert h1.rendered_text and h1.rendered_text == h2.rendered_text
        assert h1.idempotency_key == idempotency_key(
            h1.rendered_text, archive_case.query.release_label, archive_case.query.columns
        )
        assert h1.idempotency_key == h2.idempotency_key

    def test_different_queries_have_different_keys(self, archive_case: GatewayCase) -> None:
        q2 = replace(archive_case.query, row_cap=archive_case.query.row_cap + 1)
        assert (
            archive_case.gateway.query(q2).idempotency_key
            != archive_case.gateway.query(archive_case.query).idempotency_key
        )

    @pytest.mark.req("DSI-001")
    def test_fetch_yields_native_columns_in_bounded_chunks(self, archive_case: GatewayCase) -> None:
        q = archive_case.query
        batches = list(archive_case.gateway.fetch(archive_case.gateway.query(q)))
        assert batches, "a contract query must return at least one chunk"
        for batch in batches:
            assert isinstance(batch, pa.RecordBatch)
            assert set(q.columns) <= set(batch.schema.names)
            assert batch.num_rows <= q.chunk_rows
        total = sum(b.num_rows for b in batches)
        assert total <= q.row_cap
        if archive_case.expected_rows is not None:
            assert total == archive_case.expected_rows

    def test_fetch_is_idempotent_and_resumable(self, archive_case: GatewayCase) -> None:
        handle = archive_case.gateway.query(archive_case.query)
        full = [b.replace_schema_metadata(None) for b in archive_case.gateway.fetch(handle)]
        again = [b.replace_schema_metadata(None) for b in archive_case.gateway.fetch(handle)]
        assert [b.to_pylist() for b in full] == [b.to_pylist() for b in again]
        if len(full) > 1:
            resumed = [b.replace_schema_metadata(None) for b in archive_case.gateway.fetch(handle.resumed_at(1))]
            assert [b.to_pylist() for b in resumed] == [b.to_pylist() for b in full[1:]]
