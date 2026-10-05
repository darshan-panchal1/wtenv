"""User story 2: database isolation, on real git worktrees (spec.md, User Story 2; T059, T060,
T062, T067).

Postgres tests request `postgres_server` and are skipped, with a message, when Docker is not
available. SQLite and post-up tests need no Docker.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from helpers import (
    TEMPLATE_ROWS,
    PostgresServer,
    commit_all,
    git,
    make_sqlite_template,
    parse_up,
    sqlite_rows,
)

from wtenv.database import postgres_database_name
from wtenv.envfile import read_section
from wtenv.identity import current_worktree
from wtenv.output import DatabaseView, ItemKind, PostUpRun, ResourceState
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

PASSWORD_VARIABLE = "MYAPP_DB_PASSWORD"
SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def write_config(worktree: Path, text: str) -> None:
    (worktree / "wtenv.toml").write_text(text, encoding="utf-8")


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


def run_up(
    run_wtenv: Run, cwd: Path, env: dict[str, str] | None = None
) -> "subprocess.CompletedProcess[str]":
    return run_wtenv(["up", "--json"], cwd, env)


def error_of(process: "subprocess.CompletedProcess[str]") -> dict[str, object]:
    """Return the `error` object of the one JSON document a failed `up --json` printed."""
    assert len(process.stdout.splitlines()) == 1, process.stdout
    error = json.loads(process.stdout)["error"]
    assert isinstance(error, dict)
    return error


# ---------------------------------------------------------------------------------------------
# Postgres (T059, T060)
# ---------------------------------------------------------------------------------------------


def postgres_toml(server: PostgresServer, host: str | None = None, port: int | None = None) -> str:
    url = (
        f"postgresql://{server.user}:{{env:{PASSWORD_VARIABLE}}}"
        f"@{host or server.host}:{port or server.port}/{{name}}"
    )
    return f'[database]\ntype = "postgres"\ntemplate = "{server.template}"\nurl = "{url}"\n'


@pytest.fixture
def pg_env(postgres_server: PostgresServer) -> dict[str, str]:
    return {PASSWORD_VARIABLE: postgres_server.password}


@pytest.fixture
def database_cleanup(postgres_server: PostgresServer) -> Iterator[list[str]]:
    """Collect database names a test creates; drop them when the test ends."""
    names: list[str] = []
    yield names
    with postgres_server.connect() as connection:
        for name in names:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def database_name(worktree: Path) -> str:
    return postgres_database_name(worktree.name, current_worktree(worktree).git_dir)


def test_up_creates_a_postgres_database_from_the_template(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, postgres_toml(postgres_server))
    name = database_name(worktree)
    database_cleanup.append(name)

    process = run_up(run_wtenv, worktree, pg_env)

    assert process.returncode == 0, process.stderr
    result = parse_up(process)
    assert re.fullmatch(r"wtenv_feature_x_[0-9a-f]{8}", name)
    assert postgres_server.database_exists(name)
    with postgres_server.connect(name) as connection:
        rows = connection.execute("SELECT id, label FROM widgets ORDER BY id").fetchall()
    assert rows == TEMPLATE_ROWS
    section = read_section(worktree / ".env.local")
    assert [variable for variable, _ in section] == ["PORT", "DATABASE_URL"]
    assert dict(section)["DATABASE_URL"] == (
        f"postgresql://{postgres_server.user}:{postgres_server.password}"
        f"@{postgres_server.host}:{postgres_server.port}/{name}"
    )
    assert result.worktree is not None
    assert result.worktree.databases == [
        DatabaseView(
            kind="postgres",
            state=ResourceState.CREATED,
            name=name,
            host=postgres_server.host,
            port=postgres_server.port,
        )
    ]
    created = [c for c in result.changes if c.item.kind is ItemKind.POSTGRES_DATABASE]
    assert [(c.item.name, c.action) for c in created] == [(name, "created")]
    record = entry_of(worktree).databases[0]
    assert (record.kind, record.name, record.state) == ("postgres", name, ResourceState.CREATED)
    assert (record.host, record.port, record.user) == (
        postgres_server.host,
        postgres_server.port,
        postgres_server.user,
    )


def test_a_schema_change_in_one_worktree_leaves_the_other_and_the_template_alone(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    first = add_worktree(repo, "feature-a", "feature-a")
    second = add_worktree(repo, "feature-b", "feature-b")
    for worktree in (first, second):
        write_config(worktree, postgres_toml(postgres_server))
        database_cleanup.append(database_name(worktree))
        assert run_up(run_wtenv, worktree, pg_env).returncode == 0

    with postgres_server.connect(database_name(first)) as connection:
        connection.execute("ALTER TABLE widgets ADD COLUMN extra text")
        connection.execute("DELETE FROM widgets")

    assert database_name(first) != database_name(second)
    for name in (database_name(second), postgres_server.template):
        with postgres_server.connect(name) as connection:
            columns = connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'widgets'"
                " ORDER BY ordinal_position"
            ).fetchall()
            rows = connection.execute("SELECT id, label FROM widgets ORDER BY id").fetchall()
        assert columns == [("id",), ("label",)]
        assert rows == TEMPLATE_ROWS


def test_a_repeat_up_keeps_the_database_and_its_rows(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, postgres_toml(postgres_server))
    name = database_name(worktree)
    database_cleanup.append(name)
    assert run_up(run_wtenv, worktree, pg_env).returncode == 0
    with postgres_server.connect(name) as connection:
        connection.execute("INSERT INTO widgets VALUES (3, 'mine')")
    registry_before = registry_path().read_text(encoding="utf-8")
    env_before = (worktree / ".env.local").read_bytes()

    process = run_up(run_wtenv, worktree, pg_env)

    assert process.returncode == 0, process.stderr
    result = parse_up(process)
    with postgres_server.connect(name) as connection:
        assert connection.execute("SELECT count(*) FROM widgets").fetchone() == (3,)
    assert all(c.action == "unchanged" for c in result.changes)  # SC-006
    assert registry_path().read_text(encoding="utf-8") == registry_before
    assert (worktree / ".env.local").read_bytes() == env_before


def test_the_password_comes_from_the_environment_and_is_never_recorded_or_printed(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, postgres_toml(postgres_server))
    database_cleanup.append(database_name(worktree))
    secret = postgres_server.password

    first = run_up(run_wtenv, worktree, pg_env)
    second = run_up(run_wtenv, worktree, pg_env)

    for process in (first, second):
        assert secret not in process.stdout and secret not in process.stderr
    assert secret not in registry_path().read_text(encoding="utf-8")
    assert secret not in (worktree / "wtenv.toml").read_text(encoding="utf-8")
    assert secret in (worktree / ".env.local").read_text(
        encoding="utf-8"
    )  # where it belongs (FR-026)


def test_a_variable_that_is_not_set_is_config_invalid_before_anything_changes(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, postgres_server: PostgresServer
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, postgres_toml(postgres_server))
    environment = {k: v for k, v in os.environ.items() if k != PASSWORD_VARIABLE}

    process = subprocess.run(
        [sys.executable, "-m", "wtenv", "up", "--json"],
        cwd=worktree,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 3
    error = error_of(process)
    assert error["code"] == "config_invalid"
    assert error["details"]["variable"] == PASSWORD_VARIABLE  # type: ignore[index]
    assert not (worktree / ".env.local").exists()
    assert current_worktree(worktree).git_dir not in load().worktrees


# --- failures (T060) ----------------------------------------------------------------------


def test_an_unrecorded_database_with_the_target_name_is_an_ownership_conflict(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, postgres_toml(postgres_server))
    name = database_name(worktree)
    database_cleanup.append(name)
    with postgres_server.connect() as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    with postgres_server.connect(name) as connection:
        connection.execute("CREATE TABLE keep_me (id integer)")
        connection.execute("INSERT INTO keep_me VALUES (42)")

    process = run_up(run_wtenv, worktree, pg_env)

    assert process.returncode == 11
    error = error_of(process)
    assert error["code"] == "ownership_conflict"
    assert error["details"] == {"kind": "postgres_database", "name": name}
    with postgres_server.connect(name) as connection:  # not modified
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
        assert tables == [("keep_me",)]
        assert connection.execute("SELECT id FROM keep_me").fetchall() == [(42,)]
    entry = entry_of(worktree)
    assert entry.state == "incomplete" and entry.databases == []
    assert not (worktree / ".env.local").exists()


def test_a_closed_port_is_dependency_unavailable_and_a_later_up_completes(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    import socket

    worktree = add_worktree(repo, "feature-x", "feature-x")
    database_cleanup.append(database_name(worktree))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    write_config(worktree, postgres_toml(postgres_server, port=closed_port))

    failed = run_up(run_wtenv, worktree, pg_env)

    assert failed.returncode == 8
    error = error_of(failed)
    assert error["code"] == "dependency_unavailable"
    assert error["details"] == {"dependency": "postgres", "reason": "cannot_connect"}
    entry = entry_of(worktree)
    assert entry.state == "incomplete" and entry.databases == []

    write_config(worktree, postgres_toml(postgres_server))  # the server answers now
    again = run_up(run_wtenv, worktree, pg_env)

    assert again.returncode == 0, again.stderr
    assert postgres_server.database_exists(database_name(worktree))
    assert entry_of(worktree).state == "provisioned"


def test_another_session_on_the_template_is_template_in_use_and_nothing_is_changed(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, postgres_toml(postgres_server))
    name = database_name(worktree)
    database_cleanup.append(name)

    with postgres_server.connect(postgres_server.template) as session:
        session.execute("SELECT 1")
        started = time.monotonic()
        process = run_up(run_wtenv, worktree, pg_env)
        elapsed = time.monotonic() - started

        assert process.returncode == 10
        assert elapsed < 2 + 3  # FR-082: no retry or wait; the 3 s is the start of the process
        error = error_of(process)
        assert error["code"] == "template_in_use"
        assert error["details"] == {
            "kind": "postgres",
            "template": postgres_server.template,
            "connections": 1,
        }
        assert session.execute("SELECT 1").fetchone() == (1,)  # still connected (FR-027)
        assert not postgres_server.database_exists(name)
        entry = entry_of(worktree)
        assert entry.state == "incomplete"
        assert entry.databases == []  # nothing recorded
        assert entry.block.size == 10  # the block is kept
        assert not (worktree / ".env.local").exists()  # DATABASE_URL not written

    again = run_up(run_wtenv, worktree, pg_env)

    assert again.returncode == 0, again.stderr
    assert postgres_server.database_exists(name)
    assert entry_of(worktree).state == "provisioned"
    assert "DATABASE_URL" in dict(read_section(worktree / ".env.local"))


def test_a_host_that_is_not_local_is_config_invalid_before_any_connection(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, postgres_server: PostgresServer
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, postgres_toml(postgres_server, host="db.example.com"))

    process = run_up(run_wtenv, worktree, {PASSWORD_VARIABLE: "x"})

    assert process.returncode == 3
    error = error_of(process)
    assert error["code"] == "config_invalid"
    assert error["details"]["setting"] == "database.url"  # type: ignore[index]
    assert current_worktree(worktree).git_dir not in load().worktrees
    assert not (worktree / ".env.local").exists()


def test_switching_from_sqlite_to_postgres_creates_the_new_kind_and_keeps_the_old_one(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, SQLITE_TOML)
    assert run_up(run_wtenv, worktree).returncode == 0
    database_cleanup.append(database_name(worktree))

    write_config(worktree, postgres_toml(postgres_server))
    process = run_up(run_wtenv, worktree, pg_env)

    assert process.returncode == 0, process.stderr
    kinds = {d.kind: d for d in entry_of(worktree).databases}
    assert set(kinds) == {"sqlite", "postgres"}
    assert kinds["sqlite"].state is ResourceState.CREATED
    assert (worktree / ".wtenv" / "dev.sqlite3").exists()
    assert dict(read_section(worktree / ".env.local"))["DATABASE_URL"].startswith("postgresql://")


# ---------------------------------------------------------------------------------------------
# SQLite (T062)
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def sqlite_repo(repo: Path) -> Path:
    """A repository whose commit holds `wtenv.toml` and a real SQLite template with one row."""
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    write_config(repo, SQLITE_TOML)
    commit_all(repo)
    return repo


def test_up_gives_the_worktree_its_own_sqlite_copy_and_points_database_url_at_it(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(sqlite_repo, "feature-a", "feature-a")
    second = add_worktree(sqlite_repo, "feature-b", "feature-b")
    template_before = (first / "db" / "dev.sqlite3").read_bytes()

    results = [parse_up(run_up(run_wtenv, worktree)) for worktree in (first, second)]

    for worktree, result in zip((first, second), results, strict=True):
        copy = worktree / ".wtenv" / "dev.sqlite3"
        assert copy.is_file()
        section = read_section(worktree / ".env.local")
        assert [name for name, _ in section] == ["PORT", "DATABASE_URL"]  # after the port variables
        assert dict(section)["DATABASE_URL"] == f"sqlite:///{copy}"
        assert result.worktree is not None
        assert result.worktree.databases == [
            DatabaseView(kind="sqlite", state=ResourceState.CREATED, path=str(copy))
        ]
        files = [c for c in result.changes if c.item.kind is ItemKind.SQLITE_FILE]
        assert [(c.item.name, c.action) for c in files] == [(str(copy), "created")]
        record = entry_of(worktree).databases[0]
        assert (record.kind, record.path, record.state) == (
            "sqlite",
            ".wtenv/dev.sqlite3",
            ResourceState.CREATED,
        )

    connection = sqlite3.connect(first / ".wtenv" / "dev.sqlite3")
    with connection:
        connection.execute("INSERT INTO t VALUES (99)")
    connection.close()
    assert sqlite_rows(first / ".wtenv" / "dev.sqlite3") == [(0,), (99,)]
    assert sqlite_rows(second / ".wtenv" / "dev.sqlite3") == [(0,)]
    assert (first / "db" / "dev.sqlite3").read_bytes() == template_before
    assert sqlite_rows(second / "db" / "dev.sqlite3") == [(0,)]


def test_a_repeat_up_keeps_the_copy_and_its_data(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    assert run_up(run_wtenv, worktree).returncode == 0
    copy = worktree / ".wtenv" / "dev.sqlite3"
    connection = sqlite3.connect(copy)
    with connection:
        connection.execute("INSERT INTO t VALUES (7)")
    connection.close()
    registry_before = registry_path().read_text(encoding="utf-8")

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stderr
    assert sqlite_rows(copy) == [(0,), (7,)]  # FR-023
    assert all(c.action == "unchanged" for c in parse_up(process).changes)
    assert registry_path().read_text(encoding="utf-8") == registry_before


def test_an_unrecorded_file_at_the_target_is_an_ownership_conflict_and_is_not_modified(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    target = worktree / ".wtenv" / "dev.sqlite3"
    target.parent.mkdir()
    target.write_bytes(b"not wtenv's")

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 11
    error = error_of(process)
    assert error["code"] == "ownership_conflict"
    assert error["details"] == {"kind": "sqlite_file", "name": str(target)}
    assert target.read_bytes() == b"not wtenv's"
    assert entry_of(worktree).databases == []


def test_a_missing_template_is_exit_9_and_a_later_up_completes(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    template = worktree / "db" / "dev.sqlite3"
    saved = template.read_bytes()
    template.unlink()

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 9
    error = error_of(process)
    assert error["code"] == "template_missing"
    assert error["details"] == {"kind": "sqlite", "template": str(template)}
    assert_nothing_recorded_for_the_database(worktree)

    template.write_bytes(saved)
    again = run_up(run_wtenv, worktree)

    assert again.returncode == 0, again.stderr
    assert (worktree / ".wtenv" / "dev.sqlite3").is_file()
    assert entry_of(worktree).state == "provisioned"


def test_a_template_with_a_non_empty_wal_file_is_exit_10_and_a_later_up_completes(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    wal = worktree / "db" / "dev.sqlite3-wal"
    wal.write_bytes(b"unfinished work")

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 10
    error = error_of(process)
    assert error["code"] == "template_in_use"
    assert error["details"] == {"kind": "sqlite", "template": str(worktree / "db" / "dev.sqlite3")}
    assert_nothing_recorded_for_the_database(worktree)

    wal.unlink()
    again = run_up(run_wtenv, worktree)

    assert again.returncode == 0, again.stderr
    assert (worktree / ".wtenv" / "dev.sqlite3").is_file()


def assert_nothing_recorded_for_the_database(worktree: Path) -> None:
    """The state a failed database check leaves (FR-083): block kept, entry `incomplete`."""
    entry = entry_of(worktree)
    assert entry.state == "incomplete"
    assert entry.databases == []
    assert entry.block.size == 10
    assert not (worktree / ".wtenv").exists()
    assert not (worktree / ".env.local").exists()  # no DATABASE_URL


def test_git_status_stays_clean_because_the_copy_directory_is_excluded(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stderr
    assert git(worktree, "status", "--porcelain") == ""  # FR-018
    assert "/.wtenv/" in entry_of(worktree).exclude_patterns


# --- configuration changes (config.md; FR-065) --------------------------------------------


def test_a_new_url_rewrites_database_url_and_keeps_the_database(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    assert run_up(run_wtenv, worktree).returncode == 0
    copy = worktree / ".wtenv" / "dev.sqlite3"
    connection = sqlite3.connect(copy)
    with connection:
        connection.execute("INSERT INTO t VALUES (5)")
    connection.close()

    write_config(worktree, SQLITE_TOML.replace("sqlite:///{path}", "sqlite:///{path}?timeout=5"))
    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stderr
    assert (
        dict(read_section(worktree / ".env.local"))["DATABASE_URL"] == f"sqlite:///{copy}?timeout=5"
    )
    assert sqlite_rows(copy) == [(0,), (5,)]


def test_a_new_template_keeps_the_existing_copy_and_does_not_create_another(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    assert run_up(run_wtenv, worktree).returncode == 0
    copy = worktree / ".wtenv" / "dev.sqlite3"
    make_sqlite_template(worktree / "db" / "other.sqlite3", rows=3)

    write_config(worktree, SQLITE_TOML.replace("dev.sqlite3", "other.sqlite3"))
    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stderr
    assert sqlite_rows(copy) == [(0,)]  # never re-created from the new template
    assert sorted(p.name for p in (worktree / ".wtenv").iterdir()) == ["dev.sqlite3"]
    assert dict(read_section(worktree / ".env.local"))["DATABASE_URL"] == f"sqlite:///{copy}"


def test_removing_the_database_table_removes_database_url_and_keeps_the_database_recorded(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    assert run_up(run_wtenv, worktree).returncode == 0
    copy = worktree / ".wtenv" / "dev.sqlite3"

    write_config(worktree, "")
    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stderr
    assert [name for name, _ in read_section(worktree / ".env.local")] == ["PORT"]
    assert copy.is_file()
    record = entry_of(worktree).databases[0]  # FR-065
    assert (record.kind, record.state) == ("sqlite", ResourceState.CREATED)
    assert entry_of(worktree).state == "provisioned"


def test_a_copy_that_was_deleted_is_created_again(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    assert run_up(run_wtenv, worktree).returncode == 0
    copy = worktree / ".wtenv" / "dev.sqlite3"
    copy.unlink()

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stderr
    assert sqlite_rows(copy) == [(0,)]
    files = [c for c in parse_up(process).changes if c.item.kind is ItemKind.SQLITE_FILE]
    assert [c.action for c in files] == ["created"]


# ---------------------------------------------------------------------------------------------
# Post-up commands (T067)
# ---------------------------------------------------------------------------------------------


def toml_with_post_up(commands: list[str], extra: str = "") -> str:
    return f"post_up = {json.dumps(commands)}\n{extra}"


def test_post_up_commands_run_in_order_from_the_worktree_root_with_its_variables(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    commands = [
        "echo first >> order.txt",
        "pwd > cwd.txt",
        'printf "%s" "$PORT" > port.txt; printf "%s" "$DATABASE_URL" > url.txt',
        "echo last >> order.txt",
    ]
    write_config(worktree, toml_with_post_up(commands, SQLITE_TOML))

    process = run_wtenv(["up", "--json"], worktree / "db")  # from a subdirectory

    assert process.returncode == 0, process.stderr
    assert (worktree / "order.txt").read_text() == "first\nlast\n"
    assert Path((worktree / "cwd.txt").read_text().strip()).resolve() == worktree
    block = entry_of(worktree).block
    assert (worktree / "port.txt").read_text() == str(block.start)
    assert (worktree / "url.txt").read_text() == f"sqlite:///{worktree / '.wtenv' / 'dev.sqlite3'}"
    assert parse_up(process).post_up == [PostUpRun(command=c, exit_status=0) for c in commands]
    assert entry_of(worktree).state == "provisioned"


def test_post_up_commands_run_on_every_successful_up_including_a_repeat_one(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, toml_with_post_up(["echo ran >> runs.txt"]))

    for _ in range(3):
        process = run_up(run_wtenv, worktree)
        assert process.returncode == 0, process.stderr
        assert len(parse_up(process).post_up) == 1

    assert (worktree / "runs.txt").read_text() == "ran\n" * 3
    assert entry_of(worktree).state == "provisioned"


def test_post_up_standard_output_goes_to_standard_error_and_json_stays_one_document(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, toml_with_post_up(["echo marker-$((20 + 22))"]))

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stderr
    assert "marker-42" in process.stderr
    assert "marker-42" not in process.stdout  # the command text is in the JSON, its output is not
    assert len(process.stdout.splitlines()) == 1
    assert parse_up(process).ok


def test_post_up_commands_get_a_closed_standard_input(
    repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, toml_with_post_up(["cat > stdin-seen.txt"]))
    # wtenv's own standard input stays open and silent: a command that inherited it would wait.
    process = subprocess.Popen(
        [sys.executable, "-m", "wtenv", "up"],
        cwd=worktree,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    assert process.returncode == 0
    assert (worktree / "stdin-seen.txt").read_text() == ""


def test_the_first_failing_command_stops_up_with_exit_12(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    commands = ["echo one >> log.txt", "exit 3", "echo never >> log.txt"]
    write_config(worktree, toml_with_post_up(commands))

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 12
    error = error_of(process)
    assert error["code"] == "post_up_failed"
    assert error["details"] == {"command": "exit 3", "exit_status": 3}
    assert (worktree / "log.txt").read_text() == "one\n"  # later commands do not run
    entry = entry_of(worktree)
    assert entry.state == "incomplete"
    assert [name for name, _ in read_section(worktree / ".env.local")] == ["PORT"]  # resources stay

    write_config(worktree, toml_with_post_up(["echo fixed >> log.txt"]))
    again = run_up(run_wtenv, worktree)

    assert again.returncode == 0, again.stderr
    assert entry_of(worktree).state == "provisioned"


def test_a_failing_command_on_a_repeat_up_makes_the_entry_incomplete(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    assert run_up(run_wtenv, worktree).returncode == 0
    assert entry_of(worktree).state == "provisioned"

    write_config(worktree, toml_with_post_up(["exit 7"]))
    process = run_up(run_wtenv, worktree)

    assert process.returncode == 12
    assert error_of(process)["details"] == {"command": "exit 7", "exit_status": 7}
    assert entry_of(worktree).state == "incomplete"
