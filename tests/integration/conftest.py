"""Fixtures for the integration tests: a real Postgres server, started once per session, and the
Docker Compose projects of the compose tests."""

import subprocess
from collections.abc import Iterator

import pytest
from helpers import (
    COMPOSE_IMAGE,
    TEMPLATE_DATABASE,
    TEMPLATE_ROWS,
    ComposeProjects,
    PostgresServer,
    compose_version,
)
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


@pytest.fixture(scope="session")
def compose_docker(docker: None) -> None:
    """Skip the requesting test unless Docker Compose 2.24.4 or later is available.

    `docker` skips first when Docker itself is not available.
    """
    found = compose_version()
    if found is None or found < (2, 24, 4):
        shown = "not installed" if found is None else ".".join(map(str, found))
        pytest.skip(f"Docker Compose 2.24.4 or later is not available (found: {shown})")


@pytest.fixture(scope="session")
def compose_image(compose_docker: None) -> str:
    """Return the image the stacks run, or skip: the tests never pull an image."""
    result = subprocess.run(
        ["docker", "image", "inspect", COMPOSE_IMAGE], capture_output=True, check=False
    )
    if result.returncode != 0:
        pytest.skip(
            f"image {COMPOSE_IMAGE} is not present locally; run `docker pull {COMPOSE_IMAGE}`"
        )
    return COMPOSE_IMAGE


@pytest.fixture
def compose_projects(compose_docker: None) -> Iterator[ComposeProjects]:
    """Track the compose projects a test starts, and remove exactly those when it ends.

    A test registers a project with `track` before it starts anything in it. At the end, each
    tracked project is taken down with its own name, and the test fails if a container, network,
    or volume labelled with it is left. Nothing else on the machine is looked at.
    """
    projects = ComposeProjects()
    try:
        yield projects
    finally:
        projects.remove_all()
    projects.assert_nothing_left()
