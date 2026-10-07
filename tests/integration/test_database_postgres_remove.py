"""`DROP DATABASE … WITH (FORCE)` against a real Postgres server (T089; research.md section 3;
FR-019, FR-027, FR-039, FR-042).

The server is the throwaway container of the `postgres_server` fixture. Every database a test
makes has a unique name and is dropped again when the test ends; nothing else is touched.
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace

import pytest
from helpers import TEMPLATE_ROWS, PostgresServer

from wtenv.database import (
    PostgresTarget,
    Removal,
    create_postgres_database,
    remove_postgres_database,
)
from wtenv.identity import short_id
from wtenv.output import Item, ItemKind

pytestmark = pytest.mark.integration

# Every database wtenv drops is named for the git directory of its own entry (T208): each test
# invents a git directory, and the name it makes carries that directory's id.
GIT_DIRS: dict[str, str] = {}


def a_worktree() -> tuple[str, str]:
    """Invent a worktree's git directory; return it and the database name wtenv would give it."""
    git_dir = f"/repos/{uuid.uuid4().hex}/.git/worktrees/one"
    name = f"wtenv_remove_{short_id(git_dir, 8)}"
    GIT_DIRS[name] = git_dir
    return git_dir, name


def drop(target: PostgresTarget, name: str, *, dry_run: bool = False) -> Removal:
    """Drop the database `name` as the entry that has its git directory would."""
    return remove_postgres_database(target, name, GIT_DIRS[name], dry_run=dry_run)


@pytest.fixture
def target(postgres_server: PostgresServer) -> PostgresTarget:
    return PostgresTarget(
        host=postgres_server.host,
        port=postgres_server.port,
        user=postgres_server.user,
        password=postgres_server.password,
    )


@pytest.fixture
def made(postgres_server: PostgresServer, target: PostgresTarget) -> Iterator[Callable[[], str]]:
    """Return a function that creates a database from the template; drop what is left at the end."""
    names: list[str] = []

    def make() -> str:
        _, name = a_worktree()
        create_postgres_database(target, template=postgres_server.template, name=name)
        names.append(name)
        return name

    yield make
    with postgres_server.connect() as connection:
        for name in names:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def database_item(name: str) -> Item:
    return Item(kind=ItemKind.POSTGRES_DATABASE, name=name)


def test_the_recorded_database_is_dropped_and_listed_as_removed(
    postgres_server: PostgresServer, target: PostgresTarget, made: Callable[[], str]
) -> None:
    name = made()

    result = drop(target, name)

    assert result.removed == [database_item(name)]
    assert result.already_absent == [] and result.failed == []
    assert not postgres_server.database_exists(name)


def test_only_the_named_database_goes_not_the_template_or_a_database_that_is_not_recorded(
    postgres_server: PostgresServer, target: PostgresTarget, made: Callable[[], str]
) -> None:
    recorded, decoy = made(), made()

    drop(target, recorded)

    assert not postgres_server.database_exists(recorded)
    assert postgres_server.database_exists(decoy)
    assert postgres_server.database_exists(postgres_server.template)
    with postgres_server.connect(postgres_server.template) as connection:
        rows = connection.execute("SELECT id, label FROM widgets ORDER BY id").fetchall()
    assert rows == TEMPLATE_ROWS


def test_a_database_that_is_already_gone_is_already_absent(
    target: PostgresTarget, made: Callable[[], str]
) -> None:
    name = made()
    drop(target, name)

    result = drop(target, name)

    assert result.removed == [] and result.failed == []
    assert result.already_absent == [database_item(name)]


def test_listing_only_drops_nothing(
    postgres_server: PostgresServer, target: PostgresTarget, made: Callable[[], str]
) -> None:
    name = made()

    result = drop(target, name, dry_run=True)

    assert result.removed == [database_item(name)]
    assert postgres_server.database_exists(name)


def test_listing_only_for_a_database_that_is_gone_lists_nothing_to_remove(
    target: PostgresTarget,
) -> None:
    _, name = a_worktree()

    result = drop(target, name, dry_run=True)

    assert result.removed == []
    assert result.already_absent == [database_item(name)]


def test_a_database_with_a_connected_session_is_dropped_too(
    postgres_server: PostgresServer, target: PostgresTarget, made: Callable[[], str]
) -> None:
    name = made()
    with postgres_server.connect(name) as session:
        session.execute("SELECT 1")

        result = drop(target, name)

        assert result.removed == [database_item(name)]
    assert not postgres_server.database_exists(name)


def test_a_wrong_password_is_a_failure_that_keeps_the_database_and_hides_the_password(
    postgres_server: PostgresServer, target: PostgresTarget, made: Callable[[], str]
) -> None:
    name = made()
    wrong = replace(target, password="not-the-password-12345")

    result = drop(wrong, name)

    assert result.removed == []
    assert [failed.name for failed in result.failed] == [name]
    assert "not-the-password-12345" not in result.failed[0].reason
    assert "not-the-password-12345" not in repr(result)
    assert postgres_server.database_exists(name)
