"""`up` over a hand-edited registry never reuses a recorded value that fails the `down` checks
(T214, T218; FR-088; review 3, the MEDIUM finding).

Each test provisions a real worktree with `up`, edits ONE recorded field in `registry.json` so it
points at a decoy that the test made outside wtenv (the main checkout's compose project, a
database on a remote host or of another worktree, a SQLite path outside `.wtenv/`), and runs `up`
again. Every run exits 11 with `ownership_conflict`, writes nothing (the worktree, the main
checkout, and the registry are byte-identical afterwards), and leaves the decoy as it was.

Postgres tests use the session's own test server, and compose tests only the stack of the test; no
other container, network, volume, or database is looked at.
"""

import json
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    COMPOSE_IMAGE,
    PostgresServer,
    commit_all,
    exclude_file,
    make_sqlite_template,
    parse_up,
    project_resources,
    snapshot_tree,
)

from wtenv.database import postgres_database_name
from wtenv.identity import current_worktree
from wtenv.output import ResourceState
from wtenv.registry import load, registry_path, transaction

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
MakeRepo = Callable[[str], Path]
AddWorktree = Callable[[Path, str, str], Path]

SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'
PASSWORD_VARIABLE = "MYAPP_DB_PASSWORD"
STACK = f"""\
services:
  cache:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    ports:
      - "${{CACHE_PORT:-6379}}:6379"
"""
COMPOSE_TOML = (
    'ports = ["PORT", "CACHE_PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'
)


def edit_entry(worktree: Path, change: Callable[[Any], None]) -> None:
    """Edit the registry entry of `worktree` the way a person with a text editor would."""
    with transaction() as registry:
        change(registry.worktrees[current_worktree(worktree).git_dir])


def failed_up(
    run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    """Run `wtenv up --json`; return the exit status and the `error` object."""
    process = run_wtenv(["up", "--json"], worktree, env)
    assert len(process.stdout.splitlines()) == 1, process.stdout
    error = json.loads(process.stdout)["error"]
    assert isinstance(error, dict)
    return process.returncode, error


class Untouched:
    """What a refused `up` must leave as it found it: the trees, the exclude file, the registry."""

    def __init__(self, repo: Path, worktree: Path, *decoys: Path) -> None:
        self.paths = [repo, worktree, *decoys]
        self.exclude = exclude_file(repo)
        self.before = self._state()

    def _state(self) -> tuple[object, ...]:
        return (
            [snapshot_tree(path) for path in self.paths],
            self.exclude.read_bytes(),
            registry_path().read_bytes(),
        )

    def assert_unchanged(self) -> None:
        assert self._state() == self.before


@pytest.fixture
def repo(make_repo: MakeRepo) -> Path:
    return make_repo("app")


# --- the SQLite path (needs no Docker) -----------------------------------------------------------

DECOY_PATHS = {
    "outside by ..": "../outside/main.sqlite3",
    "absolute": "ABSOLUTE",
    "not under .wtenv": "data/main.sqlite3",
    "nested under .wtenv": ".wtenv/sub/main.sqlite3",
}


@pytest.mark.parametrize("name", DECOY_PATHS)
def test_up_refuses_a_recorded_sqlite_path_outside_wtenv(
    name: str,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    tmp_path: Path,
) -> None:
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    (repo / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    assert run_wtenv(["up", "--json"], worktree).returncode == 0
    outside = tmp_path / "outside"
    outside.mkdir()
    value = DECOY_PATHS[name].replace("ABSOLUTE", str(outside / "main.sqlite3"))
    decoy = (worktree / value).resolve()
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(b"the main checkout's database\n")
    edit_entry(
        worktree,
        lambda entry: entry.databases.__setitem__(
            0, entry.databases[0].model_copy(update={"path": value})
        ),
    )
    untouched = Untouched(repo, worktree, outside)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "sqlite_file", "name": str(worktree / value)}
    assert "SQLite path" in error["message"]
    assert decoy.read_bytes() == b"the main checkout's database\n"
    untouched.assert_unchanged()


# --- a SQLite copy left in `removing` ------------------------------------------------------------


def test_up_does_not_hand_back_a_sqlite_copy_that_an_interrupted_down_left_in_removing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    (repo / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    assert run_wtenv(["up", "--json"], worktree).returncode == 0
    copy = worktree / ".wtenv" / "dev.sqlite3"
    edit_entry(
        worktree,
        lambda entry: entry.databases.__setitem__(
            0, entry.databases[0].model_copy(update={"state": ResourceState.REMOVING})
        ),
    )
    untouched = Untouched(repo, worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"] == {"reason": "interrupted_removal", "name": str(copy)}
    assert "wtenv down" in error["hint"]
    untouched.assert_unchanged()
    # `down` finishes the removal, and then `up` provisions a fresh copy.
    assert run_wtenv(["down", "--json"], worktree).returncode == 0
    assert not copy.exists()
    assert run_wtenv(["up", "--json"], worktree).returncode == 0
    assert copy.is_file()


# --- Postgres -------------------------------------------------------------------------------------


@pytest.fixture
def postgres_worktree(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
) -> Iterator[tuple[Path, dict[str, str], list[str]]]:
    """A provisioned worktree with a Postgres database; the databases a test makes are dropped."""
    url = (
        f"postgresql://{postgres_server.user}:{{env:{PASSWORD_VARIABLE}}}"
        f"@{postgres_server.host}:{postgres_server.port}/{{name}}"
    )
    (repo / "wtenv.toml").write_text(
        f'[database]\ntype = "postgres"\ntemplate = "{postgres_server.template}"\nurl = "{url}"\n',
        encoding="utf-8",
    )
    commit_all(repo)
    env = {PASSWORD_VARIABLE: postgres_server.password}
    worktree = add_worktree(repo, "feature-x", "feature-x")
    names = [postgres_database_name("feature-x", current_worktree(worktree).git_dir)]
    try:
        process = run_wtenv(["up", "--json"], worktree, env)
        assert process.returncode == 0, process.stdout + process.stderr
        parse_up(process)
        yield worktree, env, names
    finally:
        with postgres_server.connect() as connection:
            for name in names:
                connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def widgets_of(server: PostgresServer, name: str) -> list[tuple[object, ...]]:
    with server.connect(name) as connection:
        return connection.execute("SELECT id, label FROM widgets ORDER BY id").fetchall()


@pytest.mark.parametrize("which", ["the main checkout's database", "another worktree's database"])
def test_up_refuses_a_recorded_postgres_name_that_is_not_its_own(
    which: str,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    postgres_worktree: tuple[Path, dict[str, str], list[str]],
) -> None:
    worktree, env, names = postgres_worktree
    if which == "the main checkout's database":
        decoy = "myapp_dev"
        names.append(decoy)
        with postgres_server.connect() as connection:
            connection.execute(f'CREATE DATABASE "{decoy}" TEMPLATE "{postgres_server.template}"')
    else:
        other = add_worktree(repo, "other", "other")
        assert run_wtenv(["up", "--json"], other, env).returncode == 0
        decoy = postgres_database_name("other", current_worktree(other).git_dir)
        names.append(decoy)
    rows_before = widgets_of(postgres_server, decoy)
    edit_entry(
        worktree,
        lambda entry: entry.databases.__setitem__(
            0, entry.databases[0].model_copy(update={"name": decoy})
        ),
    )
    untouched = Untouched(repo, worktree)

    status, error = failed_up(run_wtenv, worktree, env)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "postgres_database", "name": decoy}
    assert "database name" in error["message"]
    assert widgets_of(postgres_server, decoy) == rows_before
    untouched.assert_unchanged()


def test_up_refuses_a_recorded_postgres_host_that_is_not_local(
    run_wtenv: Run,
    repo: Path,
    postgres_server: PostgresServer,
    postgres_worktree: tuple[Path, dict[str, str], list[str]],
) -> None:
    worktree, env, names = postgres_worktree
    edit_entry(
        worktree,
        lambda entry: entry.databases.__setitem__(
            0, entry.databases[0].model_copy(update={"host": "db.example.com"})
        ),
    )
    rows_before = widgets_of(postgres_server, names[0])
    untouched = Untouched(repo, worktree)

    status, error = failed_up(run_wtenv, worktree, env)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "postgres_database", "name": names[0]}
    assert "database host" in error["message"]
    assert widgets_of(postgres_server, names[0]) == rows_before
    untouched.assert_unchanged()


# --- the compose project --------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["the main checkout's project", "another worktree's project"])
def test_up_refuses_a_recorded_compose_project_that_is_not_its_own(
    which: str,
    compose_docker: None,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
) -> None:
    (repo / "compose.yaml").write_text(STACK, encoding="utf-8")
    (repo / "wtenv.toml").write_text(COMPOSE_TOML, encoding="utf-8")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    assert run_wtenv(["up", "--json"], worktree).returncode == 0
    if which == "the main checkout's project":
        decoy = "app"  # what `docker compose` names the project of the checkout in `app/`
    else:
        other = add_worktree(repo, "other", "other")
        assert run_wtenv(["up", "--json"], other).returncode == 0
        entry = load().worktrees[current_worktree(other).git_dir]
        assert entry.compose is not None
        decoy = entry.compose.project
    resources_before = project_resources(decoy)
    edit_entry(
        worktree,
        lambda entry: setattr(
            entry, "compose", entry.compose.model_copy(update={"project": decoy})
        ),
    )
    untouched = Untouched(repo, worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "compose_project", "name": decoy}
    assert "compose project" in error["message"]
    assert project_resources(decoy) == resources_before
    untouched.assert_unchanged()
