"""T5 fixtures: a real PostgreSQL per test, cloned from a migrated template database.

Set ``SDP_TEST_DATABASE_URL`` to a server where the user may create databases. Without it the
Postgres tests skip, unless ``SDP_REQUIRE_DATABASE=1`` (as in CI), where a missing server fails.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from sdp.adapters.postgres import migrate

TEMPLATE = "sdp_test_template"


def _server_url() -> str:
    url = os.environ.get("SDP_TEST_DATABASE_URL")
    if not url:
        if os.environ.get("SDP_REQUIRE_DATABASE") == "1":
            pytest.fail("SDP_REQUIRE_DATABASE=1 but SDP_TEST_DATABASE_URL is not set")
        pytest.skip("SDP_TEST_DATABASE_URL is not set")
    return url


def _with_database(url: str, name: str) -> str:
    return psycopg.conninfo.make_conninfo(url, dbname=name)


def _create(admin_url: str, name: str, template: str | None = None) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        if template:
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template))
            )
        else:
            conn.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(name)))


def _drop(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def postgres_server() -> str:
    return _server_url()


@pytest.fixture(scope="session")
def migrated_template(postgres_server: str) -> Iterator[str]:
    _create(postgres_server, TEMPLATE)
    migrate.upgrade(_with_database(postgres_server, TEMPLATE))
    yield TEMPLATE
    _drop(postgres_server, TEMPLATE)


@pytest.fixture
def empty_database(postgres_server: str) -> Iterator[str]:
    name = f"sdp_test_{uuid.uuid4().hex[:12]}"
    _create(postgres_server, name)
    yield _with_database(postgres_server, name)
    _drop(postgres_server, name)


@pytest.fixture
def database_url(postgres_server: str, migrated_template: str) -> Iterator[str]:
    name = f"sdp_test_{uuid.uuid4().hex[:12]}"
    _create(postgres_server, name, migrated_template)
    yield _with_database(postgres_server, name)
    _drop(postgres_server, name)
