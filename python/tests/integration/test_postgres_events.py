"""The Postgres event log: append-only, NOTIFY on insert, committed with state (KRN-010)."""

from __future__ import annotations

import psycopg
import pytest

from sdp.adapters.postgres import PostgresDatabase
from sdp.testing.contracts.metadata import sample_experiment

pytestmark = [pytest.mark.tier("T5"), pytest.mark.integration]


def _save_with_event(db: PostgresDatabase, experiment_id: str) -> None:
    with db.unit_of_work() as uow:
        experiment = sample_experiment(experiment_id)
        uow.experiments.save(experiment)
        uow.events.append(experiment.pull_events())
        uow.commit()


@pytest.mark.req("KRN-010")
def test_a_failed_transaction_leaves_neither_state_nor_event(database_url: str) -> None:
    def crash_before_commit() -> None:
        with PostgresDatabase(database_url).unit_of_work() as uow:
            experiment = sample_experiment("exp-lost")
            uow.experiments.save(experiment)
            uow.events.append(experiment.pull_events())
            raise RuntimeError("crash before commit")

    with pytest.raises(RuntimeError):
        crash_before_commit()
    with psycopg.connect(database_url) as conn:
        assert conn.execute("SELECT count(*) FROM experiments").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM events").fetchone() == (0,)


def test_events_cannot_be_updated_or_deleted(database_url: str) -> None:
    _save_with_event(PostgresDatabase(database_url), "exp-1")
    with psycopg.connect(database_url) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE events SET name = 'Edited'")
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM events")


def test_committed_events_are_announced_on_the_sdp_events_channel(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as listener:
        listener.execute("LISTEN sdp_events")
        _save_with_event(PostgresDatabase(database_url), "exp-2")
        notices = list(listener.notifies(timeout=5, stop_after=1))
    assert notices and notices[0].channel == "sdp_events"
