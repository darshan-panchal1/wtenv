"""Fixtures for the integration tests: a real Postgres server, started once per session."""

from collections.abc import Iterator

import pytest
from helpers import TEMPLATE_DATABASE, TEMPLATE_ROWS, PostgresServer
from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "postgres:17"


@pytest.fixture(scope="session")
def postgres_server(docker: None) -> Iterator[PostgresServer]:
    """Start `postgres:17`, published on 127.0.0.1, with a template database holding a table.

    The `docker` fixture skips the requesting test, with a message, when Docker is not
    available. The container and its anonymous volume are removed when the session ends.
    """
    user, password = "wtenv", "wtenv-test-password"
    container = PostgresContainer(
        POSTGRES_IMAGE, username=user, password=password, dbname="postgres", driver=None
    )
    # Publish on the loopback address only; Docker picks the host port.
    container.ports = {"5432/tcp": ("127.0.0.1",)}
    with container:
        server = PostgresServer(
            host="127.0.0.1",
            port=int(container.get_exposed_port(5432)),
            user=user,
            password=password,
            template=TEMPLATE_DATABASE,
        )
        _create_template(server)
        yield server


def _create_template(server: PostgresServer) -> None:
    """Create the template database with one small table, and leave no session on it."""
    with server.connect() as connection:
        connection.execute(f'CREATE DATABASE "{server.template}"')
    with server.connect(server.template) as connection:
        connection.execute("CREATE TABLE widgets (id integer PRIMARY KEY, label text NOT NULL)")
        for widget_id, label in TEMPLATE_ROWS:
            connection.execute("INSERT INTO widgets VALUES (%s, %s)", [widget_id, label])
