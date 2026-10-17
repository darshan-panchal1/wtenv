"""User story 3: a database that runs as a Compose service (T085; spec 002 scenarios 1 to 4, 6, 7).

`[database] type = "compose"` names a service of the worktree's compose project. `up` finds its
one published port, refuses data storage that other worktrees would share, and writes
`DATABASE_URL` for the remapped port. It never connects to the database and creates nothing.

The stack is `postgres:17` (already pulled; `pull_policy: never`, so the tests never pull). Every
project is one wtenv generated for a worktree of the test, tracked by `compose_projects`, which
takes it down by its own name when the test ends. Nothing else on the machine is looked at.
"""

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
import pytest
from helpers import (
    ComposeProjects,
    PostgresServer,
    commit_all,
    git,
    make_sqlite_template,
    parse_up,
    project_resources,
    snapshot_tree,
)

from wtenv.envfile import read_section
from wtenv.identity import current_worktree
from wtenv.output import UpResult
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

IMAGE = "postgres:17"
PASSWORD = "p@ss/w:rd%41x"  # a raw value: it is percent-encoded in the URL
DATA = "/var/lib/postgresql/data"
PORT_VARIABLE = "WTENV_PORT_DB_5432_TCP"


def stack(
    *,
    volumes: str | None = "pgdata",
    top: str = "volumes:\n  pgdata:\n",
    extra: str = "",
    anonymous: bool = False,
) -> str:
    """The compose file: service `db` publishes 5432 and keeps its data in `volumes`."""
    mount = "" if volumes is None else f"    volumes:\n      - {volumes}:{DATA}\n"
    if anonymous:
        mount = f"    volumes:\n      - {DATA}\n"
    return (
        f"services:\n  db:\n    image: {IMAGE}\n    pull_policy: never\n"
        f"    environment:\n      POSTGRES_PASSWORD: {PASSWORD}\n"
        f'    ports:\n      - "5432:5432"\n{mount}{extra}{top}'
    )


def toml(*, ports: str = '"PORT"', service: str = "db", database: str | None = None) -> str:
    url = "postgresql://postgres:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/postgres"
    return f'ports = [{ports}]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n\n' + (
        database
        if database is not None
        else f'[database]\ntype = "compose"\nservice = "{service}"\nurl = "{url}"\n'
    )


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def make_worktree(
    repo: Path,
    add_worktree: AddWorktree,
    name: str,
    *,
    compose: str | None = None,
    config: str | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    worktree = add_worktree(repo, name, f"branch-{name}")
    (worktree / "compose.yaml").write_text(compose or stack(), encoding="utf-8")
    (worktree / "wtenv.toml").write_text(config or toml(), encoding="utf-8")
    for relative, text in (files or {}).items():
        (worktree / relative).write_text(text, encoding="utf-8")
    commit_all(worktree)
    return worktree


def up(run_wtenv: Run, worktree: Path) -> UpResult:
    process = run_wtenv(["up", "--json"], worktree)
    assert process.returncode == 0, process.stdout + process.stderr
    return parse_up(process)


def failed_up(run_wtenv: Run, worktree: Path) -> tuple[int, dict[str, Any]]:
    process = run_wtenv(["up", "--json"], worktree)
    assert len(process.stdout.splitlines()) == 1, process.stdout
    error = json.loads(process.stdout)["error"]
    assert isinstance(error, dict)
    return process.returncode, error


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


def project_of(worktree: Path) -> str:
    compose = entry_of(worktree).compose
    assert compose is not None
    return compose.project


def database_url(worktree: Path, env_file: str = ".env.local") -> str:
    return dict(read_section(worktree / env_file))["DATABASE_URL"]


def exec_in(run_wtenv: Run, worktree: Path, *command: str) -> "subprocess.CompletedProcess[str]":
    return run_wtenv(["exec", "--", *command], worktree)


def wait_for(url: str, timeout: float = 120.0) -> None:
    """Return when the server behind `url` accepts a connection (the image initialises first)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            psycopg.connect(url, connect_timeout=3).close()
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def state_before(worktree: Path) -> tuple[Any, bytes | None]:
    registry = registry_path().read_bytes() if registry_path().exists() else None
    return snapshot_tree(worktree), registry


# --- scenario 1: the URL, the section, `exec` ------------------------------------------------------


def test_up_writes_the_database_url_for_the_services_port_and_exec_carries_it(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "one")

    result = up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    entry = entry_of(worktree)
    (published,) = [p for p in entry.published if p.service == "db"]
    assert result.compose_database is not None
    view = result.compose_database
    assert (view.service, view.target, view.protocol, view.port) == (
        "db",
        5432,
        "tcp",
        published.port,
    )
    assert (view.port_variable, view.url_variable) == (PORT_VARIABLE, "DATABASE_URL")
    expected = (
        f"postgresql://postgres:{quote(PASSWORD, safe='')}@localhost:{published.port}/postgres"
    )
    section = read_section(worktree / ".env.local")
    assert section[-1] == ("DATABASE_URL", expected)  # last in the section (FR-013)
    assert dict(section)[PORT_VARIABLE] == str(published.port)
    process = exec_in(run_wtenv, worktree, "printenv", "DATABASE_URL")
    assert process.stdout.strip() == expected
    assert entry.databases == []  # nothing is recorded for it, and no template was copied
    assert not (worktree / ".wtenv").exists()


def test_up_does_not_import_psycopg_for_a_compose_database(
    repo: Path, add_worktree: AddWorktree, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "lazy")
    code = (
        "import sys; from wtenv.provision import up; up(sys.argv[1]); "
        "print('psycopg' in sys.modules)"
    )

    process = subprocess.run(
        [sys.executable, "-c", code, str(worktree)], capture_output=True, text=True, check=False
    )

    assert process.returncode == 0, process.stderr
    compose_projects.track(project_of(worktree))
    assert process.stdout.strip() == "False"


# --- scenarios 2 and 4: three databases of one image, and a repeat `up` ------------------------------


def test_three_worktrees_each_get_their_own_database_and_a_repeat_up_keeps_it(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktrees = [make_worktree(repo, add_worktree, f"three-{n}") for n in range(3)]
    for worktree in worktrees:
        up(run_wtenv, worktree)
        compose_projects.track(project_of(worktree))
    for worktree in worktrees:  # each `db` started through `wtenv exec`
        started = exec_in(run_wtenv, worktree, "docker", "compose", "up", "-d")
        assert started.returncode == 0, started.stdout + started.stderr
    urls = [database_url(worktree) for worktree in worktrees]
    assert len(set(urls)) == 3
    for url in urls:
        wait_for(url)

    for number, url in enumerate(urls):
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("CREATE TABLE t (owner text)")
            connection.execute("INSERT INTO t VALUES (%s)", [f"worktree-{number}"])

    for number, url in enumerate(urls):  # a row written in one is found only there (SC-003)
        with psycopg.connect(url) as connection:
            rows = connection.execute("SELECT owner FROM t").fetchall()
        assert rows == [(f"worktree-{number}",)]
    before = [(entry_of(w).published, database_url(w)) for w in worktrees]
    for worktree in worktrees:  # FR-024: a repeat `up` keeps the port, the URL and the row
        up(run_wtenv, worktree)
    assert [(entry_of(w).published, database_url(w)) for w in worktrees] == before
    with psycopg.connect(urls[0]) as connection:
        assert connection.execute("SELECT owner FROM t").fetchall() == [("worktree-0",)]


# --- scenario 3: a tracked `.env` names the main checkout's database ------------------------------------


def test_a_tracked_env_file_with_the_main_checkouts_database_is_left_as_it_is(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    dot_env = "DATABASE_URL=postgresql://postgres:x@localhost:5432/postgres\n"
    worktree = make_worktree(repo, add_worktree, "tracked-env", files={".env": dot_env})

    up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    (published,) = [p for p in entry_of(worktree).published if p.service == "db"]
    process = exec_in(run_wtenv, worktree, "printenv", "DATABASE_URL")
    assert f"@localhost:{published.port}/" in process.stdout
    assert "@localhost:5432/" not in process.stdout
    assert (worktree / ".env").read_text(encoding="utf-8") == dot_env
    assert git(worktree, "status", "--short") == ""


# --- scenario 6: the service and its port; nothing changes when they are wrong ----------------------------

TWO_PORTS = stack().replace('      - "5432:5432"\n', '      - "5432:5432"\n      - "5433:5433"\n')
NO_PORT = stack().replace('    ports:\n      - "5432:5432"\n', "")


@pytest.mark.parametrize(
    ("compose", "service", "found"),
    [
        (stack(), "postgres", "no service"),
        (NO_PORT, "db", "no published port"),
        (TWO_PORTS, "db", "2 published ports"),
    ],
    ids=["missing service", "no published port", "two ports"],
)
def test_a_database_service_with_two_ports_changes_nothing(
    compose: str,
    service: str,
    found: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
) -> None:
    worktree = make_worktree(
        repo, add_worktree, "bad-service", compose=compose, config=toml(service=service)
    )
    before = state_before(worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (3, "config_invalid")
    assert error["details"]["setting"] == "database.service"
    assert service in error["message"] and found in error["message"]
    assert state_before(worktree) == before


# --- scenario 7: shared storage is refused before anything is created -----------------------------------------

REFUSED = {
    "fixed name": (
        stack(top="volumes:\n  pgdata:\n    name: wtenv-test-shared-pgdata\n"),
        "fixed_name_volume",
        "wtenv-test-shared-pgdata",
    ),
    "external": (
        stack(top="volumes:\n  pgdata:\n    external: true\n    name: wtenv-test-external\n"),
        "external_volume",
        "wtenv-test-external",
    ),
}


@pytest.mark.parametrize("case", [*REFUSED, "outside bind", "relative device"])
def test_shared_storage_is_refused_with_exit_23_and_nothing_changes(
    case: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
    tmp_path: Path,
) -> None:
    if case in REFUSED:
        compose, storage, name = REFUSED[case]
    elif case == "outside bind":
        outside = tmp_path / "outside-data"
        outside.mkdir()
        compose, storage, name = (
            stack(volumes=str(outside), top=""),
            "host_directory",
            str(outside.resolve()),
        )
    else:
        top = (
            "volumes:\n  pgdata:\n    driver_opts:\n      type: none\n      o: bind\n"
            "      device: ./data\n"
        )
        compose, storage, name = stack(top=top), "device_not_absolute", "./data"
    worktree = make_worktree(repo, add_worktree, "shared", compose=compose)
    before = state_before(worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (23, "database_storage_shared")
    assert error["details"]["service"] == "db"
    assert (error["details"]["storage"], error["details"]["name"]) == (storage, name)
    assert state_before(worktree) == before


@pytest.mark.parametrize(
    ("compose", "storage"),
    [
        (stack(anonymous=True, top=""), "anonymous_volume"),
        (stack(volumes=None, top=""), "no_volume"),
        (stack(volumes="./pgdata", top=""), "worktree_directory"),
    ],
    ids=["anonymous volume", "no volume", "worktree directory"],
)
def test_data_kept_outside_a_project_volume_is_accepted_with_a_warning(
    compose: str,
    storage: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "kept", compose=compose)

    result = up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    (warning,) = [w for w in result.warnings if w.code.value == "compose_database_data_kept"]
    assert warning.details["service"] == "db" and warning.details["storage"] == storage


# --- FR-022: `up` starts nothing --------------------------------------------------------------------------------


def test_up_with_a_compose_database_starts_no_container_and_creates_no_network_or_volume(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "starts-nothing")

    up(run_wtenv, worktree)
    up(run_wtenv, worktree)

    project = compose_projects.track(project_of(worktree))
    assert project_resources(project) == {"containers": [], "networks": [], "volumes": []}


# --- FR-020: the password is in the env file and in `exec`, and nowhere else ---------------------------------------


def test_no_up_output_contains_the_compose_database_password(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "secret", compose=stack(volumes=None, top=""))
    broken = make_worktree(repo, add_worktree, "secret-broken", config=toml(service="missing"))
    texts: list[str] = []

    for command in (["up"], ["up", "--json"], ["up"], ["up", "--json"]):
        process = run_wtenv(command, worktree)  # the second pair repeats: warnings again
        assert process.returncode == 0, process.stdout + process.stderr
        texts += [process.stdout, process.stderr]
    compose_projects.track(project_of(worktree))
    for command in (["up"], ["up", "--json"]):
        process = run_wtenv(command, broken)  # errors
        assert process.returncode == 3
        texts += [process.stdout, process.stderr]

    secrets = (PASSWORD, quote(PASSWORD, safe=""))
    for text in texts:
        assert not any(secret in text for secret in secrets)
    assert not any(s.encode() in registry_path().read_bytes() for s in secrets)
    assert quote(PASSWORD, safe="") in database_url(worktree)  # where it is allowed to be


# --- FR-012: a name that the rule gives to the database port ----------------------------------------------------------


def test_a_ports_entry_named_like_the_database_ports_variable_is_a_collision(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_docker: None
) -> None:
    worktree = make_worktree(
        repo, add_worktree, "collision", config=toml(ports=f'"PORT", "{PORT_VARIABLE}"')
    )
    before = state_before(worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (22, "port_variable_conflict")
    assert error["details"]["variable"] == PORT_VARIABLE
    assert state_before(worktree) == before


# --- changes of configuration after provisioning --------------------------------------------------------------------------


def test_changing_from_postgres_to_compose_rebuilds_the_url_and_keeps_the_old_database_recorded(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    postgres_server: PostgresServer,
) -> None:
    password = "WTENV_TEST_PG_PASSWORD"
    postgres = (
        'ports = ["PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n\n'
        f'[database]\ntype = "postgres"\ntemplate = "{postgres_server.template}"\n'
        f'url = "postgresql://{postgres_server.user}:{{env:{password}}}@{postgres_server.host}'
        f':{postgres_server.port}/{{name}}"\n'
    )
    worktree = make_worktree(repo, add_worktree, "to-compose", config=postgres)
    env = {password: postgres_server.password}
    process = run_wtenv(["up", "--json"], worktree, env)
    assert process.returncode == 0, process.stdout + process.stderr
    compose_projects.track(project_of(worktree))
    (recorded,) = entry_of(worktree).databases
    assert recorded.kind == "postgres" and recorded.name is not None
    try:
        (worktree / "wtenv.toml").write_text(toml(), encoding="utf-8")
        commit_all(worktree)

        up(run_wtenv, worktree)

        (published,) = [p for p in entry_of(worktree).published if p.service == "db"]
        assert f"@localhost:{published.port}/postgres" in database_url(worktree)
        assert entry_of(worktree).databases == [recorded]  # stays recorded until `down`
        assert postgres_server.database_exists(recorded.name)
    finally:
        if recorded.name is not None:
            with postgres_server.connect() as connection:
                connection.execute(f'DROP DATABASE IF EXISTS "{recorded.name}" WITH (FORCE)')


def test_changing_from_compose_to_sqlite_creates_the_copy_as_in_v0_1_x(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "to-sqlite")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    make_sqlite_template(worktree / "db" / "dev.sqlite3")
    sqlite = (
        toml(database='[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\n')
        + 'url = "sqlite:///{path}"\n'
    )
    (worktree / "wtenv.toml").write_text(sqlite, encoding="utf-8")
    commit_all(worktree)

    up(run_wtenv, worktree)

    (record,) = entry_of(worktree).databases
    assert record.kind == "sqlite" and (worktree / ".wtenv" / "dev.sqlite3").exists()
    assert database_url(worktree).startswith("sqlite:///")


def test_changing_the_service_makes_the_url_and_the_port_follow(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    second = (
        f"  other:\n    image: {IMAGE}\n    pull_policy: never\n"
        '    environment:\n      POSTGRES_PASSWORD: other-pw\n    ports:\n      - "5433:5432"\n'
    )
    worktree = make_worktree(
        repo, add_worktree, "service", compose=stack(top="", volumes=None, extra=second)
    )
    first = up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    assert first.compose_database is not None and first.compose_database.service == "db"
    before = database_url(worktree)
    (worktree / "wtenv.toml").write_text(toml(service="other"), encoding="utf-8")
    commit_all(worktree)

    again = up(run_wtenv, worktree)

    assert again.compose_database is not None and again.compose_database.service == "other"
    (published,) = [p for p in entry_of(worktree).published if p.service == "other"]
    assert again.compose_database.port == published.port
    assert database_url(worktree) != before and f":{published.port}/" in database_url(worktree)
    assert "other-pw" in database_url(worktree)
