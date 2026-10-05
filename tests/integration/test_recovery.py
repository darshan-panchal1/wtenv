"""`wtenv up` interrupted at each step, then run again (data-model.md, Write order of `up`;
FR-067, FR-069)."""

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from helpers import (
    PostgresServer,
    block_lines,
    commit_all,
    exclude_file,
    keys,
    make_sqlite_template,
    parse_down,
    parse_up,
    sqlite_rows,
)

from wtenv.database import postgres_database_name
from wtenv.envfile import read_section
from wtenv.identity import current_worktree
from wtenv.output import ResourceState
from wtenv.registry import WorktreeEntry, load, transaction

pytestmark = pytest.mark.integration

KILLED = 137

# Runs `wtenv up` in a child process with one function wrapped. With `when` "after", the wrapper
# calls the real function and then exits like a killed process. With "provisioned" (for
# `registry.save`), it exits instead of saving the entry as `provisioned`.
CHILD = """
import os, sys
import wtenv.envfile, wtenv.exclude, wtenv.registry

module = {module}
name = "{function}"
real = getattr(module, name)

def wrapper(*args, **kwargs):
    if "{when}" == "provisioned":
        saved = args[0]
        if any(entry.state == "provisioned" for entry in saved.worktrees.values()):
            os._exit({killed})
        return real(*args, **kwargs)
    real(*args, **kwargs)
    os._exit({killed})

setattr(module, name, wrapper)
from wtenv.cli import main
sys.exit(main(["up"]))
"""

POINTS = {
    "after the registry step": ("wtenv.registry", "save", "after"),
    "after the exclude step": ("wtenv.exclude", "add_patterns", "after"),
    "after the env-file step": ("wtenv.envfile", "write_section", "after"),
    "before the completion step": ("wtenv.registry", "save", "provisioned"),
}


def interrupt_up(worktree: Path, module: str, function: str, when: str) -> None:
    """Run `wtenv up` in `worktree` in a child process that dies at the given point."""
    code = CHILD.format(module=module, function=function, when=when, killed=KILLED)
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == KILLED, process.stderr


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


@pytest.mark.parametrize("point", list(POINTS))
def test_up_interrupted_at_any_point_leaves_only_recorded_resources_and_recovers(
    point: str,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    repo = make_repo("app")
    worktree = add_worktree(repo, "feature-x", "feature-x")
    env_file = worktree / ".env.local"

    interrupt_up(worktree, *POINTS[point])

    # What the interrupted run left: an entry with a block, and nothing the entry does not record.
    entry = entry_of(worktree)
    assert entry.state == "incomplete"
    assert entry.block.size == 10
    assert set(block_lines(exclude_file(repo))) <= set(entry.exclude_patterns)
    if env_file.exists():
        assert entry.env_file is not None
        assert entry.env_file.path == ".env.local"
    if point == "after the registry step":
        assert block_lines(exclude_file(repo)) == []
        assert not env_file.exists()
        assert entry.env_file is not None and entry.env_file.state.value == "creating"
    if point == "after the exclude step":
        assert block_lines(exclude_file(repo)) == ["/.env.local"]
        assert not env_file.exists()
    if point == "after the env-file step":
        assert [name for name, _ in read_section(env_file)] == ["PORT"]
        assert entry.env_file is not None and entry.env_file.state.value == "creating"
    if point == "before the completion step":
        assert entry.env_file is not None and entry.env_file.state.value == "created"
    interrupted_block = entry.block

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    result = parse_up(process)
    assert result.ok
    entry = entry_of(worktree)
    assert entry.state == "provisioned"
    assert entry.block == interrupted_block
    assert block_lines(exclude_file(repo)) == ["/.env.local"]
    assert read_section(env_file) == [("PORT", str(interrupted_block.start))]
    assert entry.env_file is not None
    assert entry.env_file.state.value == "created"
    # The file was created by the interrupted run, which recorded that before writing.
    assert entry.env_file.created_file is True
    assert os.stat(env_file).st_mode & 0o777 == 0o600


# --- the database step (T063; FR-067, FR-069; data-model.md, Resource states) -------------------

DATABASE_CHILD = """
import os, sys
import wtenv.database, wtenv.registry

module = {module}
name = "{function}"
real = getattr(module, name)

def wrapper(*args, **kwargs):
    result = real(*args, **kwargs)
    if "{when}" == "recorded":
        saved = args[0]
        if any(d.state.value == "creating" for e in saved.worktrees.values() for d in e.databases):
            os._exit({killed})
    else:
        os._exit({killed})
    return result

setattr(module, name, wrapper)
from wtenv.cli import main
sys.exit(main(["up"]))
"""

SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'

# (module, function, when): the child exits right after the function returns; for "recorded", only
# once a save holds a database in state `creating`.
DATABASE_POINTS = {
    "recorded as creating, before it exists": ("wtenv.registry", "save", "recorded"),
    "exists, before it is marked created": ("wtenv.database", "{create}", "created"),
}


def interrupt_up_in_database_step(worktree: Path, module: str, function: str, when: str) -> None:
    """Run `wtenv up` in a child process that dies at the given point of the database step."""
    code = DATABASE_CHILD.format(module=module, function=function, when=when, killed=KILLED)
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == KILLED, process.stderr


@pytest.mark.parametrize("point", list(DATABASE_POINTS))
def test_up_interrupted_in_the_sqlite_step_leaves_only_recorded_files_and_recovers(
    point: str,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    repo = make_repo("app")
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    (repo / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    copy = worktree / ".wtenv" / "dev.sqlite3"
    module, function, when = DATABASE_POINTS[point]

    interrupt_up_in_database_step(
        worktree, module, function.format(create="create_sqlite_copy"), when
    )

    entry = entry_of(worktree)
    assert entry.state == "incomplete"
    assert [(d.kind, d.path, d.state.value) for d in entry.databases] == [
        ("sqlite", ".wtenv/dev.sqlite3", "creating")
    ]
    # Nothing exists that the registry does not record, and the copy is whole when it exists.
    assert copy.exists() == (when == "created")
    if copy.exists():
        assert sqlite_rows(copy) == [(0,)]
    assert not (worktree / ".env.local").exists()

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    assert parse_up(process).ok
    entry = entry_of(worktree)
    assert entry.state == "provisioned"
    assert [(d.kind, d.state.value) for d in entry.databases] == [("sqlite", "created")]
    assert sqlite_rows(copy) == [(0,)]
    assert sorted(p.name for p in copy.parent.iterdir()) == ["dev.sqlite3"]
    section = dict(read_section(worktree / ".env.local"))
    assert section["DATABASE_URL"] == f"sqlite:///{copy}"


@pytest.mark.parametrize("point", list(DATABASE_POINTS))
def test_up_interrupted_in_the_postgres_step_leaves_only_recorded_databases_and_recovers(
    point: str,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYAPP_DB_PASSWORD", postgres_server.password)
    repo = make_repo("app")
    worktree = add_worktree(repo, "feature-x", "feature-x")
    url = (
        f"postgresql://{postgres_server.user}:{{env:MYAPP_DB_PASSWORD}}"
        f"@{postgres_server.host}:{postgres_server.port}/{{name}}"
    )
    (worktree / "wtenv.toml").write_text(
        f'[database]\ntype = "postgres"\ntemplate = "{postgres_server.template}"\nurl = "{url}"\n',
        encoding="utf-8",
    )
    name = postgres_database_name(worktree.name, current_worktree(worktree).git_dir)
    module, function, when = DATABASE_POINTS[point]
    try:
        interrupt_up_in_database_step(
            worktree, module, function.format(create="create_postgres_database"), when
        )

        entry = entry_of(worktree)
        assert entry.state == "incomplete"
        assert [(d.kind, d.name, d.state.value) for d in entry.databases] == [
            ("postgres", name, "creating")
        ]
        assert postgres_server.database_exists(name) == (when == "created")
        assert wtenv_databases(postgres_server) == ([name] if when == "created" else [])

        process = run_wtenv(["up", "--json"], worktree)

        assert process.returncode == 0, process.stderr
        entry = entry_of(worktree)
        assert entry.state == "provisioned"
        assert [(d.kind, d.name, d.state.value) for d in entry.databases] == [
            ("postgres", name, "created")
        ]
        assert wtenv_databases(postgres_server) == [name]
        with postgres_server.connect(name) as connection:
            assert connection.execute("SELECT count(*) FROM widgets").fetchone() == (2,)
        assert "DATABASE_URL" in dict(read_section(worktree / ".env.local"))
    finally:
        with postgres_server.connect() as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def wtenv_databases(server: PostgresServer) -> list[str]:
    """Return the names of the databases on the server that start with `wtenv_feature_x_`."""
    with server.connect() as connection:
        rows = connection.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'wtenv\\_feature\\_x\\_%' ORDER BY 1"
        ).fetchall()
    return [str(row[0]) for row in rows]


# --- `down` interrupted (T098; FR-042, FR-067, FR-069; data-model.md, Resource states) -----------

# Runs `wtenv down` in a child process with one function wrapped. With `when` "before", the child
# exits like a killed process instead of calling the real function, so the resource is marked
# `removing` and still exists. With "after", it calls the real function and then exits, so the
# resource is gone and its record is still there.
DOWN_CHILD = """
import os, sys
import wtenv.database, wtenv.envfile

module = {module}
name = "{function}"
real = getattr(module, name)

def wrapper(*args, **kwargs):
    if "{when}" == "before":
        os._exit({killed})
    real(*args, **kwargs)
    os._exit({killed})

setattr(module, name, wrapper)
from wtenv.cli import main
sys.exit(main(["down"]))
"""


def interrupt_down(worktree: Path, module: str, function: str, when: str) -> None:
    """Run `wtenv down` in `worktree` in a child process that dies at the given point."""
    code = DOWN_CHILD.format(module=module, function=function, when=when, killed=KILLED)
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == KILLED, process.stderr


# What to interrupt: the module and function of the removal, and how to read the resource's state
# and existence afterwards.
DOWN_RESOURCES = {
    "sqlite copy": ("wtenv.database", "remove_sqlite_copy"),
    "env section": ("wtenv.envfile", "remove_section"),
}


def recorded_state(entry: WorktreeEntry, resource: str) -> str | None:
    """Return the state of the recorded `resource`, or None when it is not recorded."""
    if resource == "sqlite copy":
        return entry.databases[0].state.value if entry.databases else None
    return None if entry.env_file is None else entry.env_file.state.value


def exists(worktree: Path, resource: str) -> bool:
    if resource == "sqlite copy":
        return (worktree / ".wtenv" / "dev.sqlite3").exists()
    return (worktree / ".env.local").exists()


def sqlite_worktree(
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> Path:
    """Return a provisioned worktree with a SQLite copy, in its own temporary repository."""
    repo = make_repo("app")
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    (repo / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    process = run_wtenv(["up", "--json"], worktree)
    assert process.returncode == 0, process.stderr
    return worktree


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("resource", list(DOWN_RESOURCES))
def test_down_interrupted_around_a_removal_is_finished_by_down_again(
    resource: str,
    when: str,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    worktree = sqlite_worktree(make_repo, add_worktree, run_wtenv)
    module, function = DOWN_RESOURCES[resource]

    interrupt_down(worktree, module, function, when)

    entry = entry_of(worktree)
    assert entry.state == "incomplete"
    assert recorded_state(entry, resource) == "removing"  # recorded before it was removed
    assert exists(worktree, resource) == (when == "before")

    process = run_wtenv(["down", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    result = parse_down(process)
    assert result.ok and result.failed == []
    kinds = {"sqlite copy": "sqlite_file", "env section": "env_section"}
    handled = [pair for pair in keys(result.removed) if pair[0] == kinds[resource]]
    absent = [pair for pair in keys(result.already_absent) if pair[0] == kinds[resource]]
    if when == "before":
        assert handled and not absent  # it was still there, and is removed now
    else:
        assert absent and not handled  # it was gone: FR-069 reports it as already absent
    assert current_worktree(worktree).git_dir not in load().worktrees
    assert not exists(worktree, resource)


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("resource", list(DOWN_RESOURCES))
def test_up_after_an_interrupted_down_keeps_what_is_still_there_and_creates_what_is_gone(
    resource: str,
    when: str,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    worktree = sqlite_worktree(make_repo, add_worktree, run_wtenv)
    copy = worktree / ".wtenv" / "dev.sqlite3"
    connection = sqlite3.connect(copy)
    with connection:
        connection.execute("INSERT INTO t VALUES (99)")
    connection.close()
    module, function = DOWN_RESOURCES[resource]
    interrupt_down(worktree, module, function, when)

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    entry = entry_of(worktree)
    assert entry.state == "provisioned"
    assert recorded_state(entry, resource) == "created"
    assert exists(worktree, resource)
    assert read_section(worktree / ".env.local")  # the section is there either way
    if resource == "sqlite copy":
        # Still there: kept with its data. Gone: copied again from the template.
        assert sqlite_rows(copy) == ([(0,), (99,)] if when == "before" else [(0,)])


# With Postgres: the same two points, and a database the server marks invalid.

PASSWORD_VARIABLE = "MYAPP_DB_PASSWORD"


@pytest.fixture
def postgres_worktree(
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, str]]:
    """A provisioned worktree with a Postgres database; the database is dropped at the end."""
    monkeypatch.setenv(PASSWORD_VARIABLE, postgres_server.password)
    repo = make_repo("app")
    worktree = add_worktree(repo, "feature-x", "feature-x")
    url = (
        f"postgresql://{postgres_server.user}:{{env:{PASSWORD_VARIABLE}}}"
        f"@{postgres_server.host}:{postgres_server.port}/{{name}}"
    )
    (worktree / "wtenv.toml").write_text(
        f'[database]\ntype = "postgres"\ntemplate = "{postgres_server.template}"\nurl = "{url}"\n',
        encoding="utf-8",
    )
    name = postgres_database_name(worktree.name, current_worktree(worktree).git_dir)
    try:
        process = run_wtenv(["up", "--json"], worktree)
        assert process.returncode == 0, process.stderr
        yield worktree, name
    finally:
        with postgres_server.connect() as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.parametrize("when", ["before", "after"])
def test_down_interrupted_around_dropping_a_postgres_database_is_finished_by_down_again(
    when: str,
    postgres_worktree: tuple[Path, str],
    postgres_server: PostgresServer,
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    worktree, name = postgres_worktree

    interrupt_down(worktree, "wtenv.database", "remove_postgres_database", when)

    entry = entry_of(worktree)
    assert [(d.name, d.state.value) for d in entry.databases] == [(name, "removing")]
    assert postgres_server.database_exists(name) == (when == "before")

    process = run_wtenv(["down", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    result = parse_down(process)
    pair = ("postgres_database", name)
    assert (pair in keys(result.removed)) == (when == "before")
    assert (pair in keys(result.already_absent)) == (when == "after")
    assert not postgres_server.database_exists(name)
    assert current_worktree(worktree).git_dir not in load().worktrees


@pytest.mark.parametrize("when", ["before", "after"])
def test_up_after_an_interrupted_postgres_drop_keeps_the_database_or_creates_it_again(
    when: str,
    postgres_worktree: tuple[Path, str],
    postgres_server: PostgresServer,
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    worktree, name = postgres_worktree
    with postgres_server.connect(name) as connection:
        connection.execute("CREATE TABLE marker (id integer)")
    interrupt_down(worktree, "wtenv.database", "remove_postgres_database", when)

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    assert [(d.name, d.state.value) for d in entry_of(worktree).databases] == [(name, "created")]
    with postgres_server.connect(name) as connection:
        tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'marker'"
        ).fetchone()
    assert tables == ((1,) if when == "before" else (0,))  # kept with its data, or copied again


def test_a_database_the_server_marks_invalid_is_interrupted_removal_and_down_finishes_it(
    postgres_worktree: tuple[Path, str],
    postgres_server: PostgresServer,
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    worktree, name = postgres_worktree
    interrupt_down(worktree, "wtenv.database", "remove_postgres_database", "before")
    with postgres_server.connect() as connection:
        # PostgreSQL's own mark for a `DROP DATABASE` that was interrupted.
        connection.execute("UPDATE pg_database SET datconnlimit = -2 WHERE datname = %s", [name])

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 19
    error = json.loads(process.stdout)["error"]
    assert error["code"] == "unsupported"
    assert error["details"]["reason"] == "interrupted_removal"
    assert "wtenv down" in error["hint"]
    assert [d.state.value for d in entry_of(worktree).databases] == ["removing"]

    process = run_wtenv(["down", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    assert not postgres_server.database_exists(name)
    assert current_worktree(worktree).git_dir not in load().worktrees


@pytest.mark.parametrize("override_survived", [True, False])
def test_up_with_a_compose_override_recorded_as_removing_keeps_or_writes_it_again(
    override_survived: bool,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
    compose_image: str,
) -> None:
    """data-model.md, Resource states: an override that is still there is kept and marked
    `created`; one that is gone is written again. `up` starts no container, so no stack is made."""
    repo = make_repo("app")
    worktree = add_worktree(repo, "feature-x", "feature-x")
    stack = (
        f"services:\n  web:\n    image: {compose_image}\n    pull_policy: never\n"
        '    ports:\n      - "8080:6379"\n'
    )
    (worktree / "compose.yaml").write_text(stack, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(
        'ports = ["PORT"]\n\n[compose]\nfile = "compose.yaml"\n', encoding="utf-8"
    )
    commit_all(worktree)
    process = run_wtenv(["up", "--json"], worktree)
    assert process.returncode == 0, process.stderr
    override = worktree / "compose.override.yaml"
    written = override.read_text(encoding="utf-8")
    git_dir = current_worktree(worktree).git_dir
    with transaction() as reg:  # what an interrupted `down` leaves just before it removes the file
        record = reg.worktrees[git_dir].compose
        assert record is not None
        reg.worktrees[git_dir].compose = record.model_copy(
            update={"override_state": ResourceState.REMOVING}
        )
    if not override_survived:
        override.unlink()

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    compose = entry_of(worktree).compose
    assert compose is not None and compose.override_state.value == "created"
    assert override.read_text(encoding="utf-8") == written
