"""Things wtenv did not create must survive teardown (SC-007; FR-039; constitution Principle II;
T096 for `down`, T108 for `gc`).

A decoy looks like wtenv's own: a name with the `wtenv_` or `wtenv-` prefix, a file beside the
recorded SQLite copy, a section with wtenv's markers. `down` may remove only what the registry
records, and so may `gc`, so every decoy must be byte for byte, or resource for resource, as it was.

Nothing here touches anything outside its own temporary repository and its own temporary state
directory. The Docker decoys are made by the test, under unique names, and removed by name at the
end of the test; no other container, network, volume, or database is looked at.
"""

import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import (
    BEGIN,
    END,
    PROJECT_LABEL,
    ComposeProjects,
    PostgresServer,
    clean_environment,
    commit_all,
    make_sqlite_template,
    parse_down,
    parse_gc,
    parse_up,
    project_resources,
)

from wtenv.database import postgres_database_name
from wtenv.identity import current_worktree
from wtenv.registry import load

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'


def up(run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None) -> None:
    process = run_wtenv(["up", "--json"], worktree, env)
    assert process.returncode == 0, process.stdout + process.stderr
    parse_up(process)


def tree(root: Path) -> dict[str, bytes]:
    """Return every file under `root` with its bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


# --- files ----------------------------------------------------------------------------------------


def sqlite_repo(make_repo: Callable[[str], Path]) -> Path:
    """Create a repository whose commit holds `wtenv.toml` and a real SQLite template."""
    repo = make_repo("app")
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    (repo / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    commit_all(repo)
    return repo


def plant_file_decoys(worktree: Path) -> dict[Path, bytes]:
    """Write files that look like wtenv's own into a provisioned worktree; return their bytes."""
    wtenv_dir = worktree / ".wtenv"
    decoys = {
        # An unrecorded database with its own side files.
        wtenv_dir / "decoy.sqlite3": b"a database wtenv did not make",
        wtenv_dir / "decoy.sqlite3-wal": b"its wal",
        wtenv_dir / "decoy.sqlite3-journal": b"its journal",
        # Side files whose database file is not recorded, or does not exist at all.
        wtenv_dir / "orphan.sqlite3-wal": b"no database file beside it",
        wtenv_dir / "orphan.sqlite3-shm": b"no database file beside it",
        # Names that only look like the recorded copy's side files.
        wtenv_dir / "dev.sqlite3-wal.bak": b"a backup of a wal",
        wtenv_dir / "dev.sqlite3.backup": b"a backup",
        # A section with wtenv's markers in a file that is not the recorded env file.
        worktree / ".env.other": f"A=1\n{BEGIN}\nPORT=1\n{END}\n".encode(),
        worktree / "notes.txt": b"mine",
    }
    for path, data in decoys.items():
        path.write_bytes(data)
    return decoys


def test_files_that_are_not_recorded_survive_down(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo(make_repo), "feature-x", "feature-x")
    up(run_wtenv, worktree)
    wtenv_dir = worktree / ".wtenv"
    decoys = plant_file_decoys(worktree)

    process = run_wtenv(["down", "--json"], worktree)

    assert process.returncode == 0, process.stdout + process.stderr
    removed = {item.name for item in parse_down(process).removed}
    assert removed.isdisjoint(str(path) for path in decoys)  # not even listed
    for path, data in decoys.items():
        assert path.read_bytes() == data, f"{path} was changed or removed"
    assert not (wtenv_dir / "dev.sqlite3").exists()  # what was recorded is gone
    assert current_worktree(worktree).git_dir not in load().worktrees


def test_a_recorded_copy_that_is_gone_leaves_even_its_own_side_files(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree
) -> None:
    repo = make_repo("app")
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    (repo / "wtenv.toml").write_text(SQLITE_TOML, encoding="utf-8")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    copy = worktree / ".wtenv" / "dev.sqlite3"
    sides = [Path(f"{copy}-wal"), Path(f"{copy}-journal")]
    for side in sides:
        side.write_bytes(b"left behind")
    copy.unlink()

    process = run_wtenv(["down", "--json"], worktree)

    assert process.returncode == 0
    assert all(side.read_bytes() == b"left behind" for side in sides)


def stray_of_a_deleted_repository(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree
) -> Path:
    """Provision a worktree of a new repository and delete the repository; return the worktree.

    Its entry is unverifiable (`repository_not_found`), so only `gc --release` releases it.
    """
    doomed = make_repo("doomed")
    stray = add_worktree(doomed, "stray", "stray")
    up(run_wtenv, stray)
    shutil.rmtree(doomed)
    return stray


def test_files_that_are_not_recorded_survive_gc_and_gc_release_of_a_neighbouring_entry(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree, tmp_path: Path
) -> None:
    repo = sqlite_repo(make_repo)
    neighbour = add_worktree(repo, "neighbour", "neighbour")
    orphan = add_worktree(repo, "orphan", "orphan")
    up(run_wtenv, neighbour)
    up(run_wtenv, orphan)
    decoys = plant_file_decoys(neighbour)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(orphan)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    stray = stray_of_a_deleted_repository(run_wtenv, make_repo, add_worktree)
    outside = tmp_path / "outside"
    outside.mkdir()

    plain = run_wtenv(["gc", "--json"], outside)
    named = run_wtenv(["gc", "--release", str(stray), "--json"], outside)

    for process in (plain, named):
        assert process.returncode == 0, process.stdout + process.stderr
        removed = {item.name for item in parse_gc(process).removed}
        assert removed, "gc released nothing, so the test proves nothing"
        assert removed.isdisjoint(str(path) for path in decoys)  # not even listed
    for path, data in decoys.items():
        assert path.read_bytes() == data, f"{path} was changed or removed"
    assert current_worktree(neighbour).git_dir in load().worktrees


# --- Docker: a database, a volume, and a container that are not recorded ---------------------------


@pytest.fixture
def decoy_project(compose_image: str, compose_projects: ComposeProjects) -> Iterator[str]:
    """Create a container and a volume labelled with a project name nobody recorded.

    Both are made by the test under unique names and removed by exactly those names at the end.
    The project is tracked too, so the test fails if anything labelled with it is left.
    """
    suffix = uuid.uuid4().hex[:8]
    project = compose_projects.track(f"wtenv-decoy-{suffix}")
    label = f"{PROJECT_LABEL}={project}"
    volume, container = f"wtenv-decoy-vol-{suffix}", f"wtenv-decoy-ctr-{suffix}"
    subprocess.run(
        ["docker", "volume", "create", "--label", label, volume], check=True, capture_output=True
    )
    subprocess.run(
        ["docker", "create", "--label", label, "--name", container, compose_image],
        check=True,
        capture_output=True,
    )
    try:
        yield project
    finally:
        subprocess.run(["docker", "rm", "--force", container], capture_output=True, check=False)
        subprocess.run(
            ["docker", "volume", "rm", "--force", volume], capture_output=True, check=False
        )


@dataclass(frozen=True)
class Stack:
    """A provisioned worktree with a running compose stack and a Postgres database, and the decoy
    database made beside it."""

    worktree: Path
    env: dict[str, str]  # what `up` needs to resolve the database URL
    project: str
    database: str
    decoy_database: str


def start_stack(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    compose_image: str,
    compose_projects: ComposeProjects,
) -> Stack:
    """Provision a worktree with compose and Postgres, start its stack, and make a decoy database
    whose name looks like the worktree's own: the same name with another id."""
    password = "MYAPP_DB_PASSWORD"
    url = f"postgresql://{postgres_server.user}:{{env:{password}}}@{postgres_server.host}:{postgres_server.port}/{{name}}"
    toml = (
        'ports = ["PORT", "CACHE_PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n\n'
        f'[database]\ntype = "postgres"\ntemplate = "{postgres_server.template}"\nurl = "{url}"\n'
    )
    stack = (
        "services:\n  cache:\n"
        f"    image: {compose_image}\n    pull_policy: never\n"
        '    ports:\n      - "${CACHE_PORT:-6379}:6379"\n    volumes:\n      - cache-data:/data\n'
        "volumes:\n  cache-data:\n"
    )
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / "compose.yaml").write_text(stack, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(toml, encoding="utf-8")
    commit_all(worktree)
    env = {password: postgres_server.password}
    up(run_wtenv, worktree, env)
    entry = load().worktrees[current_worktree(worktree).git_dir]
    assert entry.compose is not None
    project = compose_projects.track(entry.compose.project)
    database = postgres_database_name(worktree.name, entry.git_dir)
    decoy_database = f"{database[:-8]}00000000"
    with postgres_server.connect() as connection:
        connection.execute(f'CREATE DATABASE "{decoy_database}"')
    subprocess.run(
        ["docker", "compose", "up", "-d", "--pull", "never"],
        cwd=worktree,
        env=clean_environment(),
        capture_output=True,
        check=True,
    )
    return Stack(worktree, env, project, database, decoy_database)


def drop_databases(postgres_server: PostgresServer, stack: Stack) -> None:
    with postgres_server.connect() as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{stack.decoy_database}" WITH (FORCE)')
        connection.execute(f'DROP DATABASE IF EXISTS "{stack.database}" WITH (FORCE)')


def test_a_database_a_volume_and_a_container_that_are_not_recorded_survive_down(
    run_wtenv: Run,
    make_repo: Callable[[str], Path],
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    compose_image: str,
    compose_projects: ComposeProjects,
    decoy_project: str,
) -> None:
    stack = start_stack(
        run_wtenv,
        make_repo("app"),
        add_worktree,
        postgres_server,
        compose_image,
        compose_projects,
    )
    decoys_before = project_resources(decoy_project)
    assert any(decoys_before.values())

    try:
        process = run_wtenv(["down", "--json"], stack.worktree, stack.env)

        assert process.returncode == 0, process.stdout + process.stderr
        # What was recorded is gone ...
        assert not postgres_server.database_exists(stack.database)
        assert all(not ids for ids in project_resources(stack.project).values())
        # ... and every decoy is as it was.
        assert postgres_server.database_exists(stack.decoy_database)
        assert project_resources(decoy_project) == decoys_before
        assert postgres_server.database_exists(postgres_server.template)
    finally:
        drop_databases(postgres_server, stack)


def test_a_database_a_volume_and_a_container_that_are_not_recorded_survive_gc(
    run_wtenv: Run,
    make_repo: Callable[[str], Path],
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    compose_image: str,
    compose_projects: ComposeProjects,
    decoy_project: str,
    tmp_path: Path,
) -> None:
    repo = make_repo("app")
    stack = start_stack(
        run_wtenv, repo, add_worktree, postgres_server, compose_image, compose_projects
    )
    decoys_before = project_resources(decoy_project)
    assert any(decoys_before.values())
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(stack.worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    stray = stray_of_a_deleted_repository(run_wtenv, make_repo, add_worktree)
    outside = tmp_path / "outside"
    outside.mkdir()
    libpq = {"PGPASSWORD": postgres_server.password}  # gc reads no wtenv.toml

    try:
        plain = run_wtenv(["gc", "--json"], outside, libpq)
        named = run_wtenv(["gc", "--release", str(stray), "--json"], outside, libpq)

        assert plain.returncode == 0, plain.stdout + plain.stderr
        assert parse_gc(plain).released == [str(stack.worktree)]
        assert named.returncode == 0, named.stdout + named.stderr
        assert parse_gc(named).released == [str(stray)]
        # What was recorded is gone ...
        assert not postgres_server.database_exists(stack.database)
        assert all(not ids for ids in project_resources(stack.project).values())
        # ... and every decoy is as it was.
        assert postgres_server.database_exists(stack.decoy_database)
        assert project_resources(decoy_project) == decoys_before
        assert postgres_server.database_exists(postgres_server.template)
    finally:
        drop_databases(postgres_server, stack)
