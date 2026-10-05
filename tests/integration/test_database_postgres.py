"""`CREATE DATABASE … TEMPLATE` against a real Postgres server (T061; research.md section 3;
FR-020, FR-024, FR-027, FR-082)."""

import socket
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace

import pytest
from helpers import TEMPLATE_ROWS, PostgresServer

from wtenv.database import (
    PostgresTarget,
    create_postgres_database,
    inspect_postgres,
)
from wtenv.errors import ErrorCode, WtenvError

pytestmark = pytest.mark.integration


@pytest.fixture
def target(postgres_server: PostgresServer) -> PostgresTarget:
    return PostgresTarget(
        host=postgres_server.host,
        port=postgres_server.port,
        user=postgres_server.user,
        password=postgres_server.password,
    )


@pytest.fixture
def new_name(postgres_server: PostgresServer) -> Iterator[Callable[[], str]]:
    """Return a function that makes a unique database name, and drop what the test made."""
    names: list[str] = []

    def make() -> str:
        names.append(f"wtenv_test_{uuid.uuid4().hex[:12]}")
        return names[-1]

    yield make
    with postgres_server.connect() as connection:
        for name in names:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def failure(call: Callable[[], object]) -> WtenvError:
    with pytest.raises(WtenvError) as caught:
        call()
    return caught.value


def test_inspect_reports_the_template_and_that_the_name_is_free(
    postgres_server: PostgresServer, target: PostgresTarget, new_name: Callable[[], str]
) -> None:
    state = inspect_postgres(target, template=postgres_server.template, name=new_name())

    assert state.template_exists is True
    assert state.template_connections == 0
    assert state.database_exists is False


def test_create_copies_the_template_with_its_rows(
    postgres_server: PostgresServer, target: PostgresTarget, new_name: Callable[[], str]
) -> None:
    name = new_name()

    create_postgres_database(target, template=postgres_server.template, name=name)

    assert postgres_server.database_exists(name)
    with postgres_server.connect(name) as connection:
        rows = connection.execute("SELECT id, label FROM widgets ORDER BY id").fetchall()
    assert rows == TEMPLATE_ROWS
    state = inspect_postgres(target, template=postgres_server.template, name=name)
    assert state.database_exists is True


def test_a_change_in_the_copy_leaves_the_template_alone(
    postgres_server: PostgresServer, target: PostgresTarget, new_name: Callable[[], str]
) -> None:
    name = new_name()
    create_postgres_database(target, template=postgres_server.template, name=name)

    with postgres_server.connect(name) as connection:
        connection.execute("ALTER TABLE widgets ADD COLUMN extra text")
        connection.execute("DELETE FROM widgets")

    with postgres_server.connect(postgres_server.template) as connection:
        assert connection.execute("SELECT id, label FROM widgets ORDER BY id").fetchall() == (
            TEMPLATE_ROWS
        )


def test_a_name_that_exists_is_reported_and_is_an_ownership_conflict_on_create(
    postgres_server: PostgresServer, target: PostgresTarget, new_name: Callable[[], str]
) -> None:
    name = new_name()
    with postgres_server.connect() as connection:
        connection.execute(f'CREATE DATABASE "{name}"')  # somebody else's database

    assert inspect_postgres(target, template=postgres_server.template, name=name).database_exists
    error = failure(
        lambda: create_postgres_database(target, template=postgres_server.template, name=name)
    )

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "postgres_database", "name": name}


def test_a_missing_template_is_template_missing(
    postgres_server: PostgresServer, target: PostgresTarget, new_name: Callable[[], str]
) -> None:
    name = new_name()

    state = inspect_postgres(target, template="no_such_template", name=name)
    error = failure(
        lambda: create_postgres_database(target, template="no_such_template", name=name)
    )

    assert state.template_exists is False
    assert error.code is ErrorCode.TEMPLATE_MISSING
    assert error.details == {"kind": "postgres", "template": "no_such_template"}
    assert not postgres_server.database_exists(name)


def test_another_session_on_the_template_fails_at_once_and_is_left_alone(
    postgres_server: PostgresServer, target: PostgresTarget, new_name: Callable[[], str]
) -> None:
    name = new_name()
    with postgres_server.connect(postgres_server.template) as session:
        session.execute("SELECT 1")
        assert (
            inspect_postgres(target, template=postgres_server.template, name=name)
        ).template_connections == 1

        started = time.monotonic()
        error = failure(
            lambda: create_postgres_database(target, template=postgres_server.template, name=name)
        )
        elapsed = time.monotonic() - started

        assert elapsed < 2  # PostgreSQL itself would wait about five seconds (FR-082)
        assert error.code is ErrorCode.TEMPLATE_IN_USE
        assert error.details == {
            "kind": "postgres",
            "template": postgres_server.template,
            "connections": 1,
        }
        assert not postgres_server.database_exists(name)
        assert session.execute("SELECT 1").fetchone() == (1,)  # still connected (FR-027)

    create_postgres_database(target, template=postgres_server.template, name=name)
    assert postgres_server.database_exists(name)


def test_a_wrong_password_is_authentication_failed_without_the_password(
    postgres_server: PostgresServer, target: PostgresTarget, new_name: Callable[[], str]
) -> None:
    wrong = replace(target, password="not-the-password")

    error = failure(lambda: inspect_postgres(wrong, template=postgres_server.template, name="x"))

    assert error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert error.details == {"dependency": "postgres", "reason": "authentication_failed"}
    assert "not-the-password" not in error.message


def test_a_closed_port_is_cannot_connect(
    postgres_server: PostgresServer, target: PostgresTarget
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]

    error = failure(
        lambda: inspect_postgres(
            replace(target, port=closed_port), template=postgres_server.template, name="x"
        )
    )

    assert error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert error.details == {"dependency": "postgres", "reason": "cannot_connect"}


def test_a_role_that_neither_owns_nor_may_clone_the_template_is_permission_denied(
    postgres_server: PostgresServer, new_name: Callable[[], str]
) -> None:
    role = f"wtenv_role_{uuid.uuid4().hex[:8]}"
    name = new_name()
    with postgres_server.connect() as admin:
        admin.execute(f"CREATE ROLE {role} LOGIN CREATEDB PASSWORD 'pw'")
    limited = PostgresTarget(
        host=postgres_server.host, port=postgres_server.port, user=role, password="pw"
    )
    try:
        error = failure(
            lambda: create_postgres_database(limited, template=postgres_server.template, name=name)
        )

        assert error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert error.details == {"dependency": "postgres", "reason": "permission_denied"}
        assert "IS_TEMPLATE" in error.message
        assert not postgres_server.database_exists(name)
    finally:
        with postgres_server.connect() as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            admin.execute(f"DROP ROLE {role}")
