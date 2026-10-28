"""Contracts for the determinism seams, the tracker, the publisher and the dialect curator."""

from __future__ import annotations

import pyarrow as pa

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.ports.archives import DialectCurator
from sdp.kernel.ports.determinism import Clock, Hasher, IdGenerator
from sdp.kernel.ports.evidence import ExperimentTracker
from sdp.kernel.ports.publication import BundlePublisher


class DeterminismContract:
    def test_clock_is_timezone_aware_and_monotonic(self, clock: Clock) -> None:
        a, b = clock.now(), clock.now()
        assert a.tzinfo is not None and b >= a

    def test_ids_are_unique_and_prefixed(self, ids: IdGenerator) -> None:
        values = [ids.new("exp") for _ in range(50)]
        assert len(set(values)) == 50 and all(v.startswith("exp-") for v in values)

    def test_hasher_is_sha256(self, hasher: Hasher) -> None:
        assert hasher.sha256(b"abc") == sha256_id(b"abc")


class TrackerContract:
    def test_tracker_accepts_params_metrics_and_tags(self, tracker: ExperimentTracker) -> None:
        tracker.log_params("exp-1", {"seed": 1, "alpha": 0.5})
        tracker.log_metrics("exp-1", {"rms": 0.1})
        tracker.set_tags("exp-1", {"verdict": "VERIFIED"})


class PublisherContract:
    def test_publish_returns_a_doi(self, publisher: BundlePublisher) -> None:
        record = publisher.publish(b"PK\x05\x06" + b"\x00" * 18, "bundle.zip", {"title": "t", "license": "CC-BY-4.0"})
        assert record.doi.startswith("10.") and "/" in record.doi
        assert record.url.startswith("https://")


class DialectCuratorContract:
    """Run with ``curator_case``: (curator, source_id, release, native table, expected canonical columns)."""

    def test_curation_yields_the_declared_columns(
        self, curator_case: tuple[DialectCurator, str, str, pa.Table, list[str]]
    ) -> None:
        curator, source, release, native, expected = curator_case
        dialect = curator.dialect_for(source, release)
        canonical = curator.curate(native, dialect)
        assert canonical.column_names == expected
        assert canonical.num_rows == native.num_rows
