"""NFR-007: migrations are versioned and tested one step forward and one step back."""

from __future__ import annotations

import psycopg
import pytest

from sdp.adapters.postgres import migrate

pytestmark = [pytest.mark.tier("T5"), pytest.mark.integration]


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def test_migrations_are_numbered_from_one_without_gaps() -> None:
    versions = [m.version for m in migrate.available()]
    assert versions == list(range(1, len(versions) + 1))


@pytest.mark.req("NFR-007")
def test_every_migration_steps_forward_and_back(empty_database: str) -> None:
    before = _tables(empty_database) | {"schema_migrations"}
    for m in migrate.available():
        assert migrate.upgrade(empty_database, target=m.version) == [m.version]
        assert _tables(empty_database) > before, f"{m.version:04d} up created nothing"
        assert migrate.downgrade(empty_database, steps=1) == [m.version]
        assert _tables(empty_database) == before, f"{m.version:04d} down did not undo up"
        assert migrate.upgrade(empty_database, target=m.version) == [m.version], "up after down must reapply"
        before = _tables(empty_database)


@pytest.mark.req("NFR-007")
def test_upgrade_is_idempotent_and_records_versions(empty_database: str) -> None:
    latest = migrate.available()[-1].version
    assert migrate.upgrade(empty_database)[-1] == latest
    assert migrate.upgrade(empty_database) == []
    with psycopg.connect(empty_database) as conn:
        assert migrate.current(conn) == latest


@pytest.mark.req("NFR-007")
def test_downgrade_to_zero_leaves_only_the_version_table(empty_database: str) -> None:
    migrate.upgrade(empty_database)
    migrate.downgrade(empty_database, steps=len(migrate.available()))
    assert _tables(empty_database) == {"schema_migrations"}
