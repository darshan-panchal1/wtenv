"""`up` reuses a recorded value only when it passes the checks `down` applies (T214, T218;
FR-088; cli.md, `wtenv up`, steps 5 and 7, and `wtenv down`, "Recorded values").

The registry is a file a person can edit. A recorded compose project, Postgres database name or
host, or SQLite path that points at something wtenv never made would otherwise be written into the
override and the env file, so the next `docker compose up` or test run would act on someone else's
resources. Each case hands the plan step an entry with one value edited, and expects
`ownership_conflict` before anything is looked at: Docker, a Postgres server, and the file system
are all replaced by functions that fail the test when they are asked. No Docker is needed.
"""

from pathlib import Path

import pytest

from wtenv import compose, database, provision
from wtenv.config import load_config
from wtenv.errors import ErrorCode, WtenvError
from wtenv.identity import WorktreeIdentity, short_id, sqlite_copy_problem
from wtenv.output import ResourceState
from wtenv.registry import ComposeRecord, DatabaseRecord, PortBlock, WorktreeEntry

GIT_DIR = "/repos/app/.git/worktrees/feature-x"
OTHER_GIT_DIR = "/repos/app/.git/worktrees/other"
OTHER_ID = short_id(OTHER_GIT_DIR, 8)
OWN_ID = short_id(GIT_DIR, 8)

SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'
POSTGRES_TOML = (
    '[database]\ntype = "postgres"\ntemplate = "tmpl"\n'
    'url = "postgresql://wtenv:{env:WTENV_TEST_PASSWORD}@localhost:5432/{name}"\n'
)
COMPOSE_TOML = '[compose]\nfile = "compose.yaml"\n'


def identity(root: Path) -> WorktreeIdentity:
    return WorktreeIdentity(git_dir=GIT_DIR, path=str(root), repository="/repos/app/.git")


def entry(
    root: Path,
    *,
    compose_record: ComposeRecord | None = None,
    databases: list[DatabaseRecord] | None = None,
) -> WorktreeEntry:
    return WorktreeEntry(
        git_dir=GIT_DIR,
        path=str(root),
        repository="/repos/app/.git",
        state="provisioned",
        block=PortBlock(start=20000, size=10),
        databases=databases or [],
        compose=compose_record,
    )


def conflict_of(call: object) -> WtenvError:
    """Run `call`; return the `WtenvError` it raises, failing the test if it raises none."""
    assert callable(call)
    with pytest.raises(WtenvError) as raised:
        call()
    return raised.value


@pytest.fixture
def worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A worktree root; nothing outside the checks may be asked for."""
    monkeypatch.setenv("WTENV_TEST_PASSWORD", "unused")

    def asked(*args: object, **kwargs: object) -> None:
        raise AssertionError("a recorded value that fails its check must be refused before this")

    monkeypatch.setattr(compose, "check_docker", asked)
    monkeypatch.setattr(compose, "check_project_is_unused", asked)
    monkeypatch.setattr(database, "inspect_postgres", asked)
    root = tmp_path / "feature-x"
    root.mkdir()
    return root


# --- the compose project (FR-088, step 5) ------------------------------------------------------

BAD_PROJECTS = {
    "the main checkout's project": "app",
    "the form without an id": "wtenv-feature-x",
    "another worktree's project": f"wtenv-other-{OTHER_ID}",
    "this id with a different form": f"WTENV-feature-x-{OWN_ID}",
    "an empty name": "",
}


@pytest.mark.parametrize("name", BAD_PROJECTS)
def test_up_refuses_a_recorded_compose_project_that_down_would_refuse(
    name: str, worktree: Path
) -> None:
    project = BAD_PROJECTS[name]
    (worktree / "wtenv.toml").write_text(COMPOSE_TOML, encoding="utf-8")
    config = load_config(worktree)
    record = ComposeRecord(
        project=project,
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )
    known = entry(worktree, compose_record=record)

    error = conflict_of(
        lambda: provision._compose_plan(worktree, identity(worktree), config, known)
    )

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "compose_project", "name": project}
    assert "compose project" in error.message


def test_up_goes_on_with_a_recorded_compose_project_of_its_own_form(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (worktree / "wtenv.toml").write_text(COMPOSE_TOML, encoding="utf-8")
    config = load_config(worktree)
    record = ComposeRecord(
        project=f"wtenv-feature-x-{OWN_ID}",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )

    class Reached(Exception):
        pass

    def reached(*args: object, **kwargs: object) -> None:
        raise Reached

    monkeypatch.setattr(compose, "check_docker", reached)

    with pytest.raises(Reached):
        provision._compose_plan(
            worktree, identity(worktree), config, entry(worktree, compose_record=record)
        )


# --- the SQLite copy (FR-088, step 7) ----------------------------------------------------------

BAD_SQLITE_PATHS = {
    "outside by ..": "../main/db.sqlite3",
    "a part that is `..`": ".wtenv/../../main/db.sqlite3",
    "absolute": "/srv/main/db.sqlite3",
    "not under .wtenv": "data/db.sqlite3",
    "nested under .wtenv": ".wtenv/sub/db.sqlite3",
    "the directory itself": ".wtenv",
    "not in its plain form": ".wtenv//db.sqlite3",
    "empty": "",
}


@pytest.mark.parametrize("name", BAD_SQLITE_PATHS)
def test_up_refuses_a_recorded_sqlite_path_that_down_would_refuse(
    name: str, worktree: Path
) -> None:
    path = BAD_SQLITE_PATHS[name]
    (worktree / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    config = load_config(worktree)
    record = DatabaseRecord(kind="sqlite", path=path, state=ResourceState.CREATED)

    error = conflict_of(
        lambda: provision._database_plan(
            worktree, identity(worktree), config, entry(worktree, databases=[record])
        )
    )

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "sqlite_file", "name": str(worktree / path)}
    assert "SQLite path" in error.message


def test_up_uses_a_recorded_sqlite_path_inside_wtenv(worktree: Path) -> None:
    (worktree / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    config = load_config(worktree)
    record = DatabaseRecord(kind="sqlite", path=".wtenv/other.sqlite3", state=ResourceState.CREATED)

    plan = provision._database_plan(
        worktree, identity(worktree), config, entry(worktree, databases=[record])
    )

    assert plan is not None and plan.path == ".wtenv/other.sqlite3"


def test_sqlite_copy_problem_is_the_rule_down_applies() -> None:
    assert sqlite_copy_problem(".wtenv/dev.sqlite3") is None
    assert sqlite_copy_problem("data/dev.sqlite3") is not None
    assert sqlite_copy_problem("/abs/dev.sqlite3") is not None


# --- the Postgres database (FR-088, step 7) ----------------------------------------------------

BAD_NAMES = {
    "the main checkout's database": "myapp_dev",
    "another worktree's database": f"wtenv_other_{OTHER_ID}",
    "the form without an id": "wtenv_feature_x",
    "an empty name": "",
}
BAD_HOSTS = {"a remote host": "db.example.com", "an address": "10.0.0.5", "no host": None}


def postgres_record(name: str, host: str | None) -> DatabaseRecord:
    return DatabaseRecord(
        kind="postgres",
        name=name,
        host=host,
        port=5432,
        user="wtenv",
        state=ResourceState.CREATED,
    )


@pytest.mark.parametrize("name", BAD_NAMES)
def test_up_refuses_a_recorded_postgres_name_that_down_would_refuse(
    name: str, worktree: Path
) -> None:
    value = BAD_NAMES[name]
    (worktree / "wtenv.toml").write_text(POSTGRES_TOML, encoding="utf-8")
    config = load_config(worktree)
    record = postgres_record(value, "localhost")

    error = conflict_of(
        lambda: provision._database_plan(
            worktree, identity(worktree), config, entry(worktree, databases=[record])
        )
    )

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "postgres_database", "name": value}
    assert "database name" in error.message


@pytest.mark.parametrize("name", BAD_HOSTS)
def test_up_refuses_a_recorded_postgres_host_that_down_would_refuse(
    name: str, worktree: Path
) -> None:
    (worktree / "wtenv.toml").write_text(POSTGRES_TOML, encoding="utf-8")
    config = load_config(worktree)
    own_name = f"wtenv_feature_x_{OWN_ID}"
    record = postgres_record(own_name, BAD_HOSTS[name])

    error = conflict_of(
        lambda: provision._database_plan(
            worktree, identity(worktree), config, entry(worktree, databases=[record])
        )
    )

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "postgres_database", "name": own_name}
    assert "database host" in error.message


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_up_uses_a_recorded_postgres_database_of_its_own_name_on_a_local_host(
    host: str, worktree: Path
) -> None:
    (worktree / "wtenv.toml").write_text(POSTGRES_TOML, encoding="utf-8")
    config = load_config(worktree)
    own_name = f"wtenv_feature_x_{OWN_ID}"
    record = postgres_record(own_name, host)

    plan = provision._database_plan(
        worktree, identity(worktree), config, entry(worktree, databases=[record])
    )

    assert plan is not None and plan.name == own_name


# --- a SQLite copy an interrupted `down` left in `removing` (FR-088) ---------------------------


def test_up_refuses_a_sqlite_copy_left_in_removing_when_the_file_exists(worktree: Path) -> None:
    (worktree / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    config = load_config(worktree)
    (worktree / ".wtenv").mkdir()
    (worktree / ".wtenv" / "dev.sqlite3").write_bytes(b"missing committed -wal pages")
    record = DatabaseRecord(kind="sqlite", path=".wtenv/dev.sqlite3", state=ResourceState.REMOVING)

    error = conflict_of(
        lambda: provision._database_plan(
            worktree, identity(worktree), config, entry(worktree, databases=[record])
        )
    )

    assert error.code is ErrorCode.UNSUPPORTED
    assert error.details["reason"] == "interrupted_removal"
    assert error.details["name"] == str(worktree / ".wtenv" / "dev.sqlite3")
    assert "wtenv down" in (error.hint or "")


def test_up_copies_again_a_sqlite_copy_in_removing_that_is_gone(worktree: Path) -> None:
    (worktree / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    config = load_config(worktree)
    record = DatabaseRecord(kind="sqlite", path=".wtenv/dev.sqlite3", state=ResourceState.REMOVING)

    plan = provision._database_plan(
        worktree, identity(worktree), config, entry(worktree, databases=[record])
    )

    assert plan is not None and plan.path == ".wtenv/dev.sqlite3"


# --- the override header, checked again where the override is deleted (T216; FR-087) -----------

OWN_HEADER = (
    "# Generated by wtenv for this worktree. Do not edit or commit; `wtenv up` rewrites it.\n"
)


def compose_record() -> ComposeRecord:
    return ComposeRecord(
        project=f"wtenv-feature-x-{OWN_ID}",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )


@pytest.mark.parametrize(
    "text",
    ["services: {}\n", "", OWN_HEADER.replace("Generated", "Written")],
    ids=["developer content", "empty", "altered header"],
)
def test_up_does_not_delete_a_recorded_override_that_lost_its_header_since_the_plan(
    text: str, worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`up` removes the override because `[compose]` is gone; the file was edited after the plan."""
    registered: list[object] = []
    monkeypatch.setattr(provision, "_save_compose_record", lambda *args: registered.append(args))
    override = worktree / "compose.override.yaml"
    override.write_bytes(text.encode())

    error = conflict_of(lambda: provision._remove_override(worktree, GIT_DIR, compose_record()))

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "compose_override", "name": str(override)}
    assert override.read_bytes() == text.encode()


def test_up_deletes_a_recorded_override_that_still_has_its_header(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provision, "_save_compose_record", lambda *args: None)
    override = worktree / "compose.override.yaml"
    override.write_text(OWN_HEADER + 'name: "stale"\n', encoding="utf-8")

    changes = provision._remove_override(worktree, GIT_DIR, compose_record())

    assert not override.exists()
    assert [change.action for change in changes] == ["released"]
