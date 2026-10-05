"""User story 4: lifecycle and cleanup, on real git worktrees (spec.md, User Story 4; T092 to T095,
T097, T104 to T107, T112).

Every test works in a temporary repository with a temporary state directory (the `state_home`
fixture), so no test reads or writes the developer's registry. Tests that need Docker request a
fixture that skips them with a message when it is not available, and they remove exactly the
containers, networks, volumes, and databases they created.
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from helpers import (
    BEGIN,
    COMPOSE_IMAGE,
    END,
    PROJECT_LABEL,
    ComposeProjects,
    PostgresServer,
    block_lines,
    clean_environment,
    commit_all,
    exclude_file,
    git,
    keys,
    make_sqlite_template,
    parse_down,
    parse_gc,
    parse_ls,
    parse_up,
    project_resources,
    sqlite_rows,
)

from wtenv import orphans
from wtenv.database import postgres_database_name
from wtenv.errors import ErrorCode
from wtenv.gitutil import WorktreeRecord
from wtenv.identity import current_worktree
from wtenv.orphans import Classification
from wtenv.output import (
    BlockView,
    DatabaseView,
    DownResult,
    GcResult,
    ItemKind,
    KeptEntry,
    LsResult,
    PortView,
    ResourceState,
    Status,
    UnverifiableReason,
)
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")

# Takes the worktree lock, says so, and holds it until it is killed.
LOCK_HOLDER = """
import sys, time
from wtenv.locks import worktree_lock

with worktree_lock(sys.argv[1], timeout=5):
    print("locked", flush=True)
    time.sleep(60)
"""


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def write_config(worktree: Path, text: str) -> None:
    (worktree / "wtenv.toml").write_text(text, encoding="utf-8")


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


def project_of(worktree: Path) -> str:
    compose = entry_of(worktree).compose
    assert compose is not None
    return compose.project


def is_recorded(worktree: Path) -> bool:
    return current_worktree(worktree).git_dir in load().worktrees


def up(run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None) -> None:
    """Run `wtenv up` in `worktree`; it must succeed."""
    process = run_wtenv(["up", "--json"], worktree, env)
    assert process.returncode == 0, process.stdout + process.stderr
    parse_up(process)


def down(
    run_wtenv: Run, worktree: Path, *flags: str, env: dict[str, str] | None = None
) -> tuple[int, DownResult]:
    """Run `wtenv down --json` with `flags`; return the exit status and the one document."""
    process = run_wtenv(["down", *flags, "--json"], worktree, env)
    return process.returncode, parse_down(process)


def exclude_text(repo: Path) -> str:
    path = exclude_file(repo)
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- scenario 1: down releases everything and lists each item (FR-038, FR-041) -----------------


def test_down_releases_everything_and_lists_each_item_under_removed(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    git_dir = current_worktree(worktree).git_dir
    env_file = worktree / ".env.local"
    assert env_file.exists() and is_recorded(worktree)

    status, result = down(run_wtenv, worktree)

    assert status == 0
    assert result.ok and result.error is None and not result.dry_run
    assert result.worktree_path == str(worktree)
    assert keys(result.removed) == [
        ("env_section", str(env_file)),
        ("env_file", str(env_file)),  # wtenv created the file and nothing else is in it
        ("port_block", "20000-20009"),
        ("registry_entry", git_dir),
        ("exclude_entries", str(exclude_file(repo))),  # the last registered worktree (FR-085)
    ]
    assert all(item.worktree == str(worktree) for item in result.removed)
    assert result.would_remove == [] and result.already_absent == [] and result.failed == []
    assert not env_file.exists()
    assert load().worktrees == {}
    assert BEGIN not in exclude_text(repo)


def test_the_block_that_down_released_is_given_to_the_next_worktree(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    up(run_wtenv, first)
    down(run_wtenv, first)

    up(run_wtenv, second)

    assert entry_of(second).block.start == 20000


@pytest.mark.parametrize("original", [b"SECRET=abc", b"SECRET=abc\n", b"# mine\n\nA=1\nB=2\n\n"])
def test_the_developers_env_lines_are_byte_identical_after_up_and_down(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, original: bytes
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    env_file = worktree / ".env.local"
    env_file.write_bytes(original)
    up(run_wtenv, worktree)

    status, result = down(run_wtenv, worktree)

    assert status == 0
    assert env_file.read_bytes() == original  # FR-079: the line break wtenv added is gone too
    assert ("env_section", str(env_file)) in keys(result.removed)
    assert ("env_file", str(env_file)) not in keys(result.removed)  # the file was not wtenv's


def test_down_from_a_subdirectory_acts_on_the_worktree(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    deep = worktree / "src" / "deep"
    deep.mkdir(parents=True)

    status, result = down(run_wtenv, deep)

    assert status == 0 and result.worktree_path == str(worktree)
    assert not is_recorded(worktree)


def test_the_text_output_names_what_was_removed(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)

    dry = run_wtenv(["down", "--dry-run"], worktree)
    real = run_wtenv(["down"], worktree)

    assert dry.returncode == 0 and real.returncode == 0
    assert "would remove" in dry.stdout and "port_block 20000-20009" in dry.stdout
    assert "removed" in real.stdout and "port_block 20000-20009" in real.stdout
    assert not real.stdout.lstrip().startswith("{")


# --- scenario 2: --dry-run changes nothing and lists the same items (FR-040) -------------------


def snapshot(repo: Path, worktree: Path) -> dict[str, bytes | None]:
    """Return the bytes of every file down could touch; None for a file that is not there."""
    paths = [registry_path(), worktree / ".env.local", exclude_file(repo)]
    return {str(path): path.read_bytes() if path.exists() else None for path in paths}


def test_dry_run_changes_nothing_and_lists_exactly_what_down_then_removes(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    before = snapshot(repo, worktree)

    status, planned = down(run_wtenv, worktree, "--dry-run")

    assert status == 0 and planned.ok and planned.dry_run
    assert snapshot(repo, worktree) == before  # byte-identical
    assert planned.removed == [] and planned.failed == []
    assert [item.kind for item in planned.would_remove] == [
        ItemKind.ENV_SECTION,
        ItemKind.ENV_FILE,
        ItemKind.PORT_BLOCK,
        ItemKind.REGISTRY_ENTRY,
        ItemKind.EXCLUDE_ENTRIES,
    ]

    _, actual = down(run_wtenv, worktree)

    assert actual.removed == planned.would_remove  # the very same items, in the same order
    assert not actual.dry_run


def test_dry_run_takes_no_worktree_lock(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    git_dir = current_worktree(worktree).git_dir
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            LOCK_HOLDER,
            git_dir,
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ},
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"

        status, result = down(run_wtenv, worktree, "--dry-run")

        assert status == 0 and result.dry_run and result.would_remove
    finally:
        holder.kill()
        holder.wait()


# --- scenario 7: nothing to release (FR-043, FR-002) -------------------------------------------


def test_down_in_a_worktree_that_was_never_provisioned_succeeds_and_changes_nothing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / ".env.local").write_bytes(b"SECRET=abc\n")
    before = snapshot(repo, worktree)

    status, result = down(run_wtenv, worktree)

    assert status == 0 and result.ok
    assert result.removed == [] and result.already_absent == [] and result.failed == []
    assert snapshot(repo, worktree) == before
    assert not registry_path().exists()


def test_down_twice_is_harmless(run_wtenv: Run, repo: Path, add_worktree: AddWorktree) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    down(run_wtenv, worktree)
    after_first = snapshot(repo, worktree)

    status, second = down(run_wtenv, worktree)

    assert status == 0 and second.ok and second.removed == []
    assert snapshot(repo, worktree) == after_first


def test_down_outside_a_worktree_is_exit_4(run_wtenv: Run, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    process = run_wtenv(["down", "--json"], plain)

    assert process.returncode == 4
    document = json.loads(process.stdout)
    assert document["ok"] is False and document["command"] == "down"
    assert document["error"]["code"] == ErrorCode.NOT_IN_WORKTREE.value


def test_down_leaves_a_neighbouring_entry_alone(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    up(run_wtenv, first)
    up(run_wtenv, second)
    neighbour = entry_of(second).model_dump_json()
    neighbour_env = (second / ".env.local").read_bytes()

    _, result = down(run_wtenv, first)

    assert ("exclude_entries", str(exclude_file(repo))) not in keys(result.removed)  # FR-085
    assert entry_of(second).model_dump_json() == neighbour
    assert (second / ".env.local").read_bytes() == neighbour_env
    assert block_lines(exclude_file(repo)) == ["/.env.local"]


# --- errors and partial failure (T093; FR-042, FR-044, FR-081) ---------------------------------


def test_down_works_without_a_wtenv_toml(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, 'ports = ["PORT", "API_PORT"]\n')
    up(run_wtenv, worktree)
    (worktree / "wtenv.toml").unlink()

    status, result = down(run_wtenv, worktree)

    assert status == 0 and result.warnings == []
    assert not is_recorded(worktree) and not (worktree / ".env.local").exists()


def test_an_invalid_wtenv_toml_is_ignored_with_the_warning_config_ignored(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    write_config(worktree, "this is [not valid toml\n")

    process = run_wtenv(["down", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    result = parse_down(process)
    assert [w.code.value for w in result.warnings] == ["config_ignored"]
    assert "warning [config_ignored]" in process.stderr
    assert not is_recorded(worktree)


def test_an_env_file_deleted_by_hand_is_already_absent_not_an_error(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    (worktree / ".env.local").unlink()

    status, result = down(run_wtenv, worktree)

    assert status == 0 and result.failed == []
    assert keys(result.already_absent) == [("env_section", str(worktree / ".env.local"))]
    assert not is_recorded(worktree)


def test_damaged_markers_fail_the_section_keep_the_file_and_the_entry_until_repaired(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    env_file = worktree / ".env.local"
    good = env_file.read_bytes()
    damaged = good.replace(f"{END}\n".encode(), b"")
    assert damaged != good
    env_file.write_bytes(damaged)

    status, result = down(run_wtenv, worktree)

    assert status == 13
    assert not result.ok and result.error is not None
    assert result.error.code is ErrorCode.PARTIAL_FAILURE and result.error.exit_status == 13
    assert keys(result.failed) == [("env_section", str(env_file))]
    assert "markers" in result.failed[0].reason
    assert env_file.read_bytes() == damaged  # FR-081: left unchanged
    entry = entry_of(worktree)
    assert entry.state == "incomplete" and entry.env_file is not None  # still recorded
    assert ("port_block", "20000-20009") not in keys(result.removed)
    assert BEGIN in exclude_text(repo)  # the entry stays, so the exclude lines stay

    env_file.write_bytes(good)  # repaired by hand
    status, finished = down(run_wtenv, worktree)

    assert status == 0 and finished.failed == []
    assert not is_recorded(worktree) and not env_file.exists()
    assert BEGIN not in exclude_text(repo)


# --- the exclude block and a moved worktree (T094; FR-084, FR-085) -----------------------------


def test_the_exclude_block_goes_with_the_last_registered_worktree_and_the_developers_lines_stay(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    path = exclude_file(repo)
    developer = path.read_text(encoding="utf-8") + "# developer\n*.swp\n"
    path.write_text(developer, encoding="utf-8")
    up(run_wtenv, first)
    up(run_wtenv, second)
    with path.open("a", encoding="utf-8") as file:
        file.write("later-line\n")  # added by the developer after the block

    _, first_result = down(run_wtenv, first)

    assert block_lines(path) == ["/.env.local"]  # kept: another worktree is registered
    assert ItemKind.EXCLUDE_ENTRIES not in {item.kind for item in first_result.removed}

    _, second_result = down(run_wtenv, second)

    text = path.read_text(encoding="utf-8")
    assert BEGIN not in text and END not in text and "/.env.local" not in text
    assert text == developer + "later-line\n"
    assert ("exclude_entries", str(path)) in keys(second_result.removed)


def test_down_after_git_worktree_move_releases_and_warns_worktree_moved(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    moved = worktree.parent / "feature-moved"
    git(repo, "worktree", "move", str(worktree), str(moved))
    moved = moved.resolve()

    status, result = down(run_wtenv, moved)

    assert status == 0 and result.ok
    assert result.worktree_path == str(moved)
    assert [w.code.value for w in result.warnings] == ["worktree_moved"]
    assert result.warnings[0].details == {"from": str(worktree), "to": str(moved)}
    assert all(item.worktree == str(moved) for item in result.removed)
    assert not is_recorded(moved) and not (moved / ".env.local").exists()


def test_a_moved_worktree_is_recorded_at_its_new_location_before_anything_is_released(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    moved = worktree.parent / "feature-moved"
    git(repo, "worktree", "move", str(worktree), str(moved))
    moved = moved.resolve()
    env_file = moved / ".env.local"
    env_file.write_bytes(env_file.read_bytes().replace(f"{END}\n".encode(), b""))  # damaged

    status, result = down(run_wtenv, moved)

    assert status == 13
    assert [w.code.value for w in result.warnings] == ["worktree_moved"]
    assert entry_of(moved).path == str(moved)  # FR-084: the new location is recorded


def test_dry_run_in_a_moved_worktree_warns_but_records_nothing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    moved = worktree.parent / "feature-moved"
    git(repo, "worktree", "move", str(worktree), str(moved))
    moved = moved.resolve()
    before = registry_path().read_bytes()

    status, result = down(run_wtenv, moved, "--dry-run")

    assert status == 0
    assert [w.code.value for w in result.warnings] == ["worktree_moved"]
    assert registry_path().read_bytes() == before
    assert entry_of(moved).path == str(worktree)


# --- SQLite side files (T095; FR-039) -----------------------------------------------------------


@pytest.fixture
def sqlite_repo(repo: Path) -> Path:
    """A repository whose commit holds `wtenv.toml` and a real SQLite template with one row."""
    make_sqlite_template(repo / "db" / "dev.sqlite3")
    write_config(repo, SQLITE_TOML)
    commit_all(repo)
    return repo


def add_side_files(copy: Path, suffixes: tuple[str, ...] = SIDE_SUFFIXES) -> list[Path]:
    paths = [Path(f"{copy}{suffix}") for suffix in suffixes]
    for path in paths:
        path.write_bytes(b"side file")
    return paths


def test_each_side_file_beside_the_recorded_copy_is_its_own_item_and_is_removed_with_it(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    copy = worktree / ".wtenv" / "dev.sqlite3"
    sides = add_side_files(copy)
    expected = [("sqlite_file", str(path)) for path in [copy, *sides]]

    _, planned = down(run_wtenv, worktree, "--dry-run")

    assert planned.removed == []
    assert [pair for pair in keys(planned.would_remove) if pair[0] == "sqlite_file"] == expected
    assert all(Path(name).is_absolute() for _, name in expected)
    assert copy.exists() and all(path.exists() for path in sides)  # nothing was deleted

    _, actual = down(run_wtenv, worktree)

    assert [pair for pair in keys(actual.removed) if pair[0] == "sqlite_file"] == expected
    assert actual.removed == planned.would_remove  # the same items, in the same order
    assert not copy.exists() and not any(path.exists() for path in sides)


def test_a_side_file_that_does_not_exist_is_not_listed(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    copy = worktree / ".wtenv" / "dev.sqlite3"
    (wal,) = add_side_files(copy, ("-wal",))

    _, result = down(run_wtenv, worktree)

    files = [name for kind, name in keys(result.removed) if kind == "sqlite_file"]
    assert files == [str(copy), str(wal)]


def test_the_template_and_another_worktrees_copy_and_side_files_are_unchanged(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(sqlite_repo, "one", "one")
    second = add_worktree(sqlite_repo, "two", "two")
    for worktree in (first, second):
        up(run_wtenv, worktree)
        add_side_files(worktree / ".wtenv" / "dev.sqlite3")
    template = first / "db" / "dev.sqlite3"
    template_before = template.read_bytes()
    other_before = {path.name: path.read_bytes() for path in sorted((second / ".wtenv").iterdir())}

    down(run_wtenv, first)

    assert template.read_bytes() == template_before
    assert {path.name: path.read_bytes() for path in sorted((second / ".wtenv").iterdir())} == (
        other_before
    )
    assert sqlite_rows(second / ".wtenv" / "dev.sqlite3") == [(0,)]
    assert is_recorded(second) and not is_recorded(first)


def test_a_recorded_copy_that_is_gone_is_already_absent_and_its_side_files_are_left(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    copy = worktree / ".wtenv" / "dev.sqlite3"
    sides = add_side_files(copy)
    copy.unlink()  # deleted by hand

    status, result = down(run_wtenv, worktree)

    assert status == 0
    assert keys(result.already_absent) == [("sqlite_file", str(copy))]
    assert not any(kind == "sqlite_file" for kind, _ in keys(result.removed))
    assert all(path.exists() for path in sides)  # FR-039: never on their own
    assert not is_recorded(worktree)


def test_dry_run_and_down_list_the_same_items_for_a_worktree_with_a_database(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    add_side_files(worktree / ".wtenv" / "dev.sqlite3", ("-wal", "-journal"))
    before = snapshot(sqlite_repo, worktree)

    _, planned = down(run_wtenv, worktree, "--dry-run")
    assert snapshot(sqlite_repo, worktree) == before
    _, actual = down(run_wtenv, worktree)

    assert [item.kind.value for item in actual.removed] == [
        "sqlite_file",
        "sqlite_file",
        "sqlite_file",
        "env_section",
        "env_file",
        "port_block",
        "registry_entry",
        "exclude_entries",
    ]
    assert planned.would_remove == actual.removed


# --- Docker: Postgres and compose (T097; FR-019, FR-039, FR-042; US2 scenario 5) ---------------

PASSWORD_VARIABLE = "MYAPP_DB_PASSWORD"


def postgres_toml(server: PostgresServer) -> str:
    url = f"postgresql://{server.user}:{{env:{PASSWORD_VARIABLE}}}@{server.host}:{server.port}/{{name}}"
    return f'[database]\ntype = "postgres"\ntemplate = "{server.template}"\nurl = "{url}"\n'


@pytest.fixture
def pg_env(postgres_server: PostgresServer) -> dict[str, str]:
    return {PASSWORD_VARIABLE: postgres_server.password}


@pytest.fixture
def no_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hide every libpq password source from the wtenv processes the test starts."""
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("PGPASSFILE", str(tmp_path / "no-such-pgpass"))
    monkeypatch.delenv(PASSWORD_VARIABLE, raising=False)


@pytest.fixture
def database_cleanup(postgres_server: PostgresServer) -> Iterator[list[str]]:
    """Collect database names a test creates; drop them when the test ends."""
    names: list[str] = []
    yield names
    with postgres_server.connect() as connection:
        for name in names:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def postgres_repo(repo: Path, postgres_server: PostgresServer) -> Path:
    write_config(repo, postgres_toml(postgres_server))
    commit_all(repo)
    return repo


def database_name(worktree: Path) -> str:
    return postgres_database_name(worktree.name, current_worktree(worktree).git_dir)


def test_down_drops_the_worktrees_database_while_a_session_is_connected_and_keeps_the_rest(
    run_wtenv: Run,
    postgres_repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    first = add_worktree(postgres_repo, "one", "one")
    second = add_worktree(postgres_repo, "two", "two")
    names = [database_name(first), database_name(second)]
    database_cleanup.extend(names)
    for worktree in (first, second):
        up(run_wtenv, worktree, pg_env)
    assert all(postgres_server.database_exists(name) for name in names)

    with postgres_server.connect(names[0]) as session:  # a developer's open session
        session.execute("SELECT 1")
        status, result = down(run_wtenv, first, env=pg_env)

    assert status == 0, result
    assert ("postgres_database", names[0]) in keys(result.removed)
    assert not postgres_server.database_exists(names[0])
    assert postgres_server.database_exists(names[1])  # another worktree's database
    assert postgres_server.database_exists(postgres_server.template)
    assert not is_recorded(first) and is_recorded(second)


def test_without_a_password_the_database_is_failed_and_stays_recorded_until_down_has_one(
    run_wtenv: Run,
    postgres_repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
    no_password: None,
) -> None:
    worktree = add_worktree(postgres_repo, "feature-x", "feature-x")
    name = database_name(worktree)
    database_cleanup.append(name)
    up(run_wtenv, worktree, pg_env)

    status, result = down(run_wtenv, worktree)  # no password anywhere

    assert status == 13
    assert result.error is not None and result.error.code is ErrorCode.PARTIAL_FAILURE
    assert keys(result.failed) == [("postgres_database", name)]
    assert result.failed[0].reason
    assert pg_env[PASSWORD_VARIABLE] not in json.dumps(result.model_dump(mode="json"))
    assert [w.code.value for w in result.warnings] == ["config_ignored"]  # the variable is unset
    assert postgres_server.database_exists(name)
    entry = entry_of(worktree)  # still recorded, as is everything the entry holds
    assert entry.state == "incomplete"
    assert [(d.kind, d.name) for d in entry.databases] == [("postgres", name)]

    status, finished = down(run_wtenv, worktree, env=pg_env)  # now with the password

    assert status == 0 and finished.failed == []
    assert ("postgres_database", name) in keys(finished.removed)
    assert not postgres_server.database_exists(name) and not is_recorded(worktree)


def test_a_password_from_the_libpq_sources_is_enough_when_wtenv_toml_has_none(
    run_wtenv: Run,
    postgres_repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
    no_password: None,
) -> None:
    worktree = add_worktree(postgres_repo, "feature-x", "feature-x")
    name = database_name(worktree)
    database_cleanup.append(name)
    up(run_wtenv, worktree, pg_env)
    write_config(worktree, "")  # no [database]: nothing in wtenv.toml gives a password

    status, result = down(run_wtenv, worktree, env={"PGPASSWORD": postgres_server.password})

    assert status == 0 and result.failed == []
    assert ("postgres_database", name) in keys(result.removed)
    assert not postgres_server.database_exists(name)


def container_names(project: str) -> list[str]:
    """Return the names of the containers labelled with `project`, sorted."""
    process = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={PROJECT_LABEL}={project}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(process.stdout.split())


def stack_with_external(volume: str) -> str:
    return f"""\
services:
  cache:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    ports:
      - "${{CACHE_PORT:-6379}}:6379"
    volumes:
      - cache-data:/data
      - kept-data:/kept
volumes:
  cache-data:
  kept-data:
    external: true
    name: {volume}
"""


@pytest.fixture
def external_volume(compose_projects: ComposeProjects) -> Iterator[str]:
    """Create a volume of the test's own, with no compose label; remove exactly it at the end.

    A stack that still runs would keep the volume in use, so the tracked projects are taken down
    first (`remove_all` is safe to repeat; the `compose_projects` fixture runs it again).
    """
    name = f"wtenv-test-external-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "volume", "create", name], capture_output=True, check=True)
    try:
        yield name
    finally:
        compose_projects.remove_all()
        subprocess.run(
            ["docker", "volume", "rm", "--force", name], capture_output=True, check=False
        )


def volume_exists(name: str) -> bool:
    inspect = subprocess.run(
        ["docker", "volume", "inspect", name], capture_output=True, check=False
    )
    return inspect.returncode == 0


def start_stack(worktree: Path) -> None:
    subprocess.run(
        ["docker", "compose", "up", "-d", "--pull", "never"],
        cwd=worktree,
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=True,
    )


COMPOSE_TOML = (
    'ports = ["PORT", "CACHE_PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'
)


def compose_worktree(repo: Path, add_worktree: AddWorktree, volume: str, toml: str) -> Path:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / "compose.yaml").write_text(stack_with_external(volume), encoding="utf-8")
    write_config(worktree, toml)
    commit_all(worktree)
    return worktree


def test_down_removes_the_compose_project_and_its_override_and_keeps_an_external_volume(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    compose_image: str,
    compose_projects: ComposeProjects,
    external_volume: str,
) -> None:
    worktree = compose_worktree(repo, add_worktree, external_volume, COMPOSE_TOML)
    up(run_wtenv, worktree)
    project = compose_projects.track(project_of(worktree))
    start_stack(worktree)
    override = worktree / "compose.override.yaml"
    names = container_names(project)
    resources = project_resources(project)
    assert override.exists() and names
    assert resources["networks"] and resources["volumes"]

    _, planned = down(run_wtenv, worktree, "--dry-run")
    assert project_resources(project) == resources and container_names(project) == names

    status, result = down(run_wtenv, worktree)

    assert status == 0 and result.failed == []
    removed = keys(result.removed)
    assert [("compose_container", name) for name in names] == [
        pair for pair in removed if pair[0] == "compose_container"
    ]
    assert [kind for kind, _ in removed if kind == "compose_network"] == ["compose_network"]
    assert f"{project}_cache-data" in [n for k, n in removed if k == "compose_volume"]
    assert ("compose_override", str(override)) in removed
    assert result.removed == planned.would_remove
    assert all(not ids for ids in project_resources(project).values())
    assert not override.exists() and (worktree / "compose.yaml").exists()
    assert volume_exists(external_volume)  # declared external: never removed (FR-039)
    assert not is_recorded(worktree)


def test_the_items_of_a_full_down_come_out_in_the_order_of_cli_md(
    run_wtenv: Run,
    postgres_server: PostgresServer,
    repo: Path,
    add_worktree: AddWorktree,
    compose_image: str,
    compose_projects: ComposeProjects,
    external_volume: str,
    pg_env: dict[str, str],
    database_cleanup: list[str],
) -> None:
    toml = COMPOSE_TOML + "\n" + postgres_toml(postgres_server)
    worktree = compose_worktree(repo, add_worktree, external_volume, toml)
    database_cleanup.append(database_name(worktree))
    up(run_wtenv, worktree, pg_env)
    project = compose_projects.track(project_of(worktree))
    start_stack(worktree)

    _, planned = down(run_wtenv, worktree, "--dry-run", env=pg_env)
    status, result = down(run_wtenv, worktree, env=pg_env)

    assert status == 0 and result.failed == []
    kinds = [item.kind.value for item in result.removed]
    ranks = {
        kind: rank
        for rank, kind in enumerate(
            [
                "compose_container",
                "compose_network",
                "compose_volume",
                "compose_override",
                "postgres_database",
                "env_section",
                "env_file",
                "port_block",
                "registry_entry",
                "exclude_entries",
            ]
        )
    }
    assert kinds == sorted(kinds, key=lambda kind: ranks[kind])
    assert set(kinds) == set(ranks)  # every kind of item is there
    assert planned.would_remove == result.removed
    assert not postgres_server.database_exists(database_name(worktree))
    assert all(not ids for ids in project_resources(project).values())


# --- `wtenv ls` (T112; FR-048 to FR-050, FR-054, FR-076; US4 scenario 6) -------------------------


def ls(run_wtenv: Run, cwd: Path) -> LsResult:
    """Run `wtenv ls --json` in `cwd`; it must succeed."""
    process = run_wtenv(["ls", "--json"], cwd)
    assert process.returncode == 0, process.stdout + process.stderr
    return parse_ls(process)


def test_ls_lists_the_entries_of_two_repositories_from_anywhere_with_everything_recorded(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree, tmp_path: Path
) -> None:
    first_repo = make_repo("app")
    make_sqlite_template(first_repo / "db" / "dev.sqlite3")
    write_config(first_repo, 'ports = ["PORT", "API_PORT"]\n\n' + SQLITE_TOML.lstrip())
    commit_all(first_repo)
    first = add_worktree(first_repo, "feature-a", "feature-a")
    second = add_worktree(make_repo("other"), "feature-b", "feature-b")
    up(run_wtenv, first)
    up(run_wtenv, second)
    plain = tmp_path / "plain"
    plain.mkdir()

    result = ls(run_wtenv, plain)  # outside any repository (FR-003)

    assert result.ok and result.error is None
    views = {view.path: view for view in result.worktrees}
    assert set(views) == {str(first), str(second)}  # nothing is unprovisioned outside a repository
    a, b = views[str(first)], views[str(second)]
    assert (a.repository, a.git_dir) == (entry_of(first).repository, entry_of(first).git_dir)
    assert b.repository != a.repository  # two repositories, listed together
    assert a.status is Status.PROVISIONED and a.reason is None and a.current_path is None
    assert a.block == BlockView(start=20000, end=20009, size=10)
    assert a.ports == [
        PortView(port=20000, variable="PORT"),
        PortView(port=20001, variable="API_PORT"),
    ]
    assert a.databases == [
        DatabaseView(
            kind="sqlite", state=ResourceState.CREATED, path=str(first / ".wtenv" / "dev.sqlite3")
        )
    ]
    assert a.compose_project is None and a.env_file == ".env.local"
    assert b.block == BlockView(start=20010, end=20019, size=10)
    assert b.ports == [PortView(port=20010, variable="PORT")] and b.databases == []


def test_inside_a_repository_ls_adds_its_worktrees_that_have_no_entry_as_unprovisioned(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree, tmp_path: Path
) -> None:
    repo = make_repo("app")
    provisioned = add_worktree(repo, "one", "one")
    plain_worktree = add_worktree(repo, "two", "two")
    elsewhere = add_worktree(make_repo("other"), "three", "three")  # another repository
    up(run_wtenv, provisioned)

    inside = ls(run_wtenv, provisioned)

    statuses = {view.path: view.status for view in inside.worktrees}
    assert statuses == {
        str(provisioned): Status.PROVISIONED,
        str(plain_worktree): Status.UNPROVISIONED,
        str(repo): Status.UNPROVISIONED,  # the main worktree has no entry either
    }
    assert str(elsewhere) not in statuses
    row = next(view for view in inside.worktrees if view.path == str(plain_worktree))
    assert row.git_dir == current_worktree(plain_worktree).git_dir
    assert row.repository == entry_of(provisioned).repository
    assert row.block is None and row.ports == [] and row.databases == []
    assert row.compose_project is None and row.env_file is None

    plain = tmp_path / "plain"
    plain.mkdir()
    outside = ls(run_wtenv, plain)

    assert [view.path for view in outside.worktrees] == [str(provisioned)]


def test_ls_with_an_empty_registry_outside_any_repository_is_an_empty_list(
    run_wtenv: Run, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    result = ls(run_wtenv, plain)

    assert result.ok and result.worktrees == []
    assert not registry_path().exists()  # reading creates no registry (FR-050)


def test_every_status_comes_from_classify(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree, tmp_path: Path
) -> None:
    repo = make_repo("app")
    ok = add_worktree(repo, "ok", "ok")
    failing = add_worktree(repo, "failing", "failing")
    removed = add_worktree(repo, "removed", "removed")
    deleted = add_worktree(repo, "deleted", "deleted")
    moving = add_worktree(repo, "moving", "moving")
    doomed_repo = make_repo("doomed")
    doomed = add_worktree(doomed_repo, "stray", "stray")
    write_config(failing, 'post_up = ["exit 3"]\n')
    for worktree in (ok, removed, deleted, moving, doomed):
        up(run_wtenv, worktree)
    assert run_wtenv(["up"], failing).returncode == 12  # a failed post-up command
    git(repo, "worktree", "remove", "--force", str(removed))
    shutil.rmtree(deleted)  # deleted by hand: git still lists it
    new_place = moving.parent / "moved-away"
    git(repo, "worktree", "move", str(moving), str(new_place))
    shutil.rmtree(doomed_repo)
    shutil.rmtree(doomed)  # the whole repository, and its worktree, are gone
    plain = tmp_path / "plain"
    plain.mkdir()

    views = {view.path: view for view in ls(run_wtenv, plain).worktrees}

    def row(path: Path) -> tuple[Status, UnverifiableReason | None, str | None]:
        view = views[str(path)]
        return view.status, view.reason, view.current_path

    assert row(ok) == (Status.PROVISIONED, None, None)
    assert row(failing) == (Status.INCOMPLETE, None, None)
    assert row(removed) == (Status.ORPHANED, None, None)  # FR-054
    assert row(deleted) == (Status.UNVERIFIABLE, UnverifiableReason.GIT_STILL_LISTS, None)
    assert row(moving) == (Status.UNVERIFIABLE, UnverifiableReason.MOVED, str(new_place.resolve()))
    assert row(doomed) == (Status.UNVERIFIABLE, UnverifiableReason.REPOSITORY_NOT_FOUND, None)


def test_ls_changes_nothing_and_works_while_another_process_holds_a_worktree_lock(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    before = registry_path().read_bytes()
    holder = subprocess.Popen(
        [sys.executable, "-c", LOCK_HOLDER, current_worktree(worktree).git_dir],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"

        result = ls(run_wtenv, worktree)
        text = run_wtenv(["ls"], worktree)

        assert [view.path for view in result.worktrees] == [str(worktree), str(repo)]
        assert text.returncode == 0
    finally:
        holder.kill()
        holder.wait()
    assert registry_path().read_bytes() == before  # FR-050: byte-identical


def test_the_text_table_has_the_columns_of_cli_md_and_names_the_reason(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, tmp_path: Path
) -> None:
    ok = add_worktree(repo, "ok", "ok")
    gone = add_worktree(repo, "gone", "gone")
    write_config(ok, 'ports = ["PORT", "API_PORT"]\n')
    up(run_wtenv, ok)
    up(run_wtenv, gone)
    shutil.rmtree(gone)

    process = run_wtenv(["ls"], ok)

    assert process.returncode == 0
    header, *rows = process.stdout.splitlines()
    assert header.split() == ["STATUS", "PORTS", "VARIABLES", "DATABASE", "COMPOSE", "PATH"]
    by_path = {row.split()[-1] if "(" not in row else row.split()[-2]: row for row in rows}
    assert "provisioned" in by_path[str(ok)] and "20000-20009" in by_path[str(ok)]
    assert "PORT=20000 API_PORT=20001" in by_path[str(ok)]
    assert "unverifiable" in by_path[str(gone)] and "(git_still_lists)" in by_path[str(gone)]
    assert any("unprovisioned" in row and str(repo) in row for row in rows)
    assert not process.stdout.lstrip().startswith("{")


def test_ls_shows_a_postgres_database_and_a_compose_project_and_never_a_credential(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
    compose_image: str,
    compose_projects: ComposeProjects,
    external_volume: str,
) -> None:
    toml = COMPOSE_TOML + "\n" + postgres_toml(postgres_server)
    worktree = compose_worktree(repo, add_worktree, external_volume, toml)
    database_cleanup.append(database_name(worktree))
    up(run_wtenv, worktree, pg_env)
    project = compose_projects.track(project_of(worktree))

    as_json = run_wtenv(["ls", "--json"], worktree, pg_env)
    as_text = run_wtenv(["ls"], worktree, pg_env)

    result = parse_ls(as_json)
    row = next(view for view in result.worktrees if view.path == str(worktree))
    assert row.compose_project == project
    assert row.databases == [
        DatabaseView(
            kind="postgres",
            state=ResourceState.CREATED,
            name=database_name(worktree),
            host=postgres_server.host,
            port=postgres_server.port,
        )
    ]
    cache = next(port for port in row.ports if port.variable == "CACHE_PORT")
    assert (cache.service, cache.target, cache.protocol) == ("cache", 6379, "tcp")
    assert row.block is not None
    assert cache.port in range(row.block.start, row.block.end + 1)
    for process in (as_json, as_text):
        assert postgres_server.password not in process.stdout + process.stderr  # FR-019
    assert f"postgres {database_name(worktree)}" in as_text.stdout and project in as_text.stdout


# --- `wtenv gc` (T104 to T107; FR-045 to FR-047, FR-072 to FR-075, FR-077, FR-085) --------------


def gc(
    run_wtenv: Run, cwd: Path, *flags: str, env: dict[str, str] | None = None
) -> tuple[int, GcResult]:
    """Run `wtenv gc --json` with `flags` in `cwd`; return the exit status and the one document."""
    process = run_wtenv(["gc", *flags, "--json"], cwd, env)
    return process.returncode, parse_gc(process)


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory outside any repository: `gc` runs anywhere (FR-003)."""
    path = tmp_path / "outside"
    path.mkdir()
    return path


def git_dir_of(worktree: Path) -> str:
    return current_worktree(worktree).git_dir


def remove_with_git(repo: Path, worktree: Path) -> str:
    """Remove `worktree` with `git worktree remove --force`; return the git directory it had."""
    git_dir = git_dir_of(worktree)
    git(repo, "worktree", "remove", "--force", str(worktree))
    return git_dir


def stray_of_a_deleted_repository(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree
) -> tuple[Path, str]:
    """Provision a worktree of a new repository, then delete the repository.

    The worktree's directory is left; its `.git` file now names a git directory that is gone.
    Return the worktree's path and the git directory it had.
    """
    doomed = make_repo("doomed")
    stray = add_worktree(doomed, "stray", "stray")
    up(run_wtenv, stray)
    git_dir = git_dir_of(stray)
    shutil.rmtree(doomed)
    return stray, git_dir


def state_files(repo: Path) -> dict[str, bytes | None]:
    """Return the bytes of the registry and of the repository's exclude file."""
    paths = [registry_path(), exclude_file(repo)]
    return {str(path): path.read_bytes() if path.exists() else None for path in paths}


# --- plain gc releases orphans (T104; scenarios 3 to 5) ------------------------------------------


def test_gc_releases_worktrees_removed_with_git_worktree_remove_and_lists_each_item(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    kept = add_worktree(repo, "three", "three")
    for worktree in (first, second, kept):
        up(run_wtenv, worktree)
    first_dir = remove_with_git(repo, first)
    second_dir = remove_with_git(repo, second)

    status, result = gc(run_wtenv, outside)

    assert status == 0
    assert result.ok and result.error is None and not result.dry_run
    assert result.released == [str(first), str(second)]
    assert keys(result.removed) == [
        ("port_block", "20000-20009"),
        ("registry_entry", first_dir),
        ("port_block", "20010-20019"),
        ("registry_entry", second_dir),
    ]
    assert [item.worktree for item in result.removed] == [str(first)] * 2 + [str(second)] * 2
    # The worktree directories went with `git worktree remove`, and their env files with them.
    assert keys(result.already_absent) == [
        ("env_section", str(first / ".env.local")),
        ("env_section", str(second / ".env.local")),
    ]
    assert result.would_release == [] and result.would_remove == [] and result.failed == []
    assert result.kept == [] and result.skipped_busy == [] and result.no_entry == []
    assert set(load().worktrees) == {git_dir_of(kept)}


def test_gc_dry_run_changes_nothing_and_lists_exactly_what_gc_then_removes(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    orphan = add_worktree(repo, "one", "one")
    neighbour = add_worktree(repo, "two", "two")
    up(run_wtenv, orphan)
    up(run_wtenv, neighbour)
    remove_with_git(repo, orphan)
    before = state_files(repo)

    status, planned = gc(run_wtenv, outside, "--dry-run")

    assert status == 0 and planned.ok and planned.dry_run
    assert state_files(repo) == before  # byte-identical
    assert planned.would_release == [str(orphan)] and planned.released == []
    assert planned.removed == [] and planned.failed == []
    assert [item.kind for item in planned.would_remove] == [
        ItemKind.PORT_BLOCK,
        ItemKind.REGISTRY_ENTRY,
    ]

    _, actual = gc(run_wtenv, outside)

    assert actual.removed == planned.would_remove  # the very same items, in the same order
    assert actual.released == planned.would_release
    assert actual.already_absent == planned.already_absent


def test_only_orphaned_entries_are_released_and_existing_worktrees_are_not_listed(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    existing = add_worktree(repo, "existing", "existing")
    orphan = add_worktree(repo, "orphan", "orphan")
    deleted = add_worktree(repo, "deleted", "deleted")
    for worktree in (existing, orphan, deleted):
        up(run_wtenv, worktree)
    existing_entry = entry_of(existing).model_dump_json()
    existing_env = (existing / ".env.local").read_bytes()
    deleted_dir = git_dir_of(deleted)
    remove_with_git(repo, orphan)
    shutil.rmtree(deleted)  # deleted by hand: git still lists it

    status, result = gc(run_wtenv, outside)

    assert status == 0 and result.ok
    assert result.released == [str(orphan)]
    assert result.kept == [
        KeptEntry(path=str(deleted), git_dir=deleted_dir, reason=UnverifiableReason.GIT_STILL_LISTS)
    ]
    assert str(existing) not in result.model_dump_json()  # existing worktrees are not listed
    assert entry_of(existing).model_dump_json() == existing_entry
    assert (existing / ".env.local").read_bytes() == existing_env
    assert deleted_dir in load().worktrees  # FR-047: kept, with all its resources


def test_gc_removes_the_exclude_block_only_with_the_repositorys_last_entry(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    path = exclude_file(repo)
    developer = path.read_text(encoding="utf-8") + "# developer\n*.swp\n"
    path.write_text(developer, encoding="utf-8")
    up(run_wtenv, first)
    up(run_wtenv, second)

    remove_with_git(repo, first)
    _, first_result = gc(run_wtenv, outside)

    assert block_lines(path) == ["/.env.local"]  # kept: another entry of the repository remains
    assert ItemKind.EXCLUDE_ENTRIES not in {item.kind for item in first_result.removed}

    remove_with_git(repo, second)
    _, second_result = gc(run_wtenv, outside)

    assert path.read_text(encoding="utf-8") == developer  # markers gone, developer's lines kept
    assert ("exclude_entries", str(path)) in keys(second_result.removed)


def test_gc_runs_outside_any_repository_and_a_second_run_behaves_the_same(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    status, empty = gc(run_wtenv, outside)

    assert status == 0 and empty == GcResult(ok=True)
    assert not registry_path().exists()  # nothing to do creates no registry

    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    remove_with_git(repo, worktree)
    status, first = gc(run_wtenv, outside)
    assert status == 0 and first.released == [str(worktree)]
    after_first = registry_path().read_bytes()

    status, second = gc(run_wtenv, outside)

    assert status == 0 and second == GcResult(ok=True)  # FR-075: the same as a first run
    assert registry_path().read_bytes() == after_first


def test_the_gc_text_output_names_what_was_released_and_what_was_kept(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    orphan = add_worktree(repo, "orphan", "orphan")
    deleted = add_worktree(repo, "deleted", "deleted")
    up(run_wtenv, orphan)
    up(run_wtenv, deleted)
    remove_with_git(repo, orphan)
    shutil.rmtree(deleted)

    dry = run_wtenv(["gc", "--dry-run"], outside)
    real = run_wtenv(["gc"], outside)

    assert dry.returncode == 0 and real.returncode == 0
    assert "would remove" in dry.stdout and "port_block 20000-20009" in dry.stdout
    assert "removed" in real.stdout and "port_block 20000-20009" in real.stdout
    assert f"kept {deleted} (git_still_lists)" in " ".join(real.stdout.split())
    assert not real.stdout.lstrip().startswith("{")


# --- plain gc keeps and skips entries (T105; scenario 8; FR-072, FR-074, FR-075, FR-077) --------


def test_gc_keeps_every_unverifiable_entry_with_its_reason_and_removes_nothing(
    run_wtenv: Run,
    repo: Path,
    make_repo: Callable[[str], Path],
    add_worktree: AddWorktree,
    outside: Path,
) -> None:
    deleted = add_worktree(repo, "deleted", "deleted")
    moving = add_worktree(repo, "moving", "moving")
    occupied = add_worktree(repo, "occupied", "occupied")
    for worktree in (deleted, moving, occupied):
        up(run_wtenv, worktree)
    dirs = {worktree: git_dir_of(worktree) for worktree in (deleted, moving, occupied)}
    stray, stray_dir = stray_of_a_deleted_repository(run_wtenv, make_repo, add_worktree)
    shutil.rmtree(deleted)  # by hand: git still lists it
    new_place = moving.parent / "moved-away"
    git(repo, "worktree", "move", str(moving), str(new_place))
    remove_with_git(repo, occupied)
    occupied.mkdir()  # something is at the removed worktree's path again
    before = registry_path().read_bytes()

    status, result = gc(run_wtenv, outside)

    assert status == 0 and result.ok
    assert result.released == [] and result.removed == [] and result.already_absent == []
    assert result.kept == [
        KeptEntry(
            path=str(deleted), git_dir=dirs[deleted], reason=UnverifiableReason.GIT_STILL_LISTS
        ),
        KeptEntry(
            path=str(moving),
            git_dir=dirs[moving],
            reason=UnverifiableReason.MOVED,
            current_path=str(new_place.resolve()),
        ),
        KeptEntry(
            path=str(occupied), git_dir=dirs[occupied], reason=UnverifiableReason.PATH_EXISTS
        ),
        KeptEntry(
            path=str(stray), git_dir=stray_dir, reason=UnverifiableReason.REPOSITORY_NOT_FOUND
        ),
    ]
    assert registry_path().read_bytes() == before


def test_a_hand_deleted_worktree_is_released_after_git_worktree_prune_and_gc_never_prunes(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    shutil.rmtree(worktree)

    status, first = gc(run_wtenv, outside)

    assert status == 0
    assert [entry.reason for entry in first.kept] == [UnverifiableReason.GIT_STILL_LISTS]
    listing = git(repo, "worktree", "list", "--porcelain")
    assert f"worktree {worktree}\n" in listing and "prunable" in listing  # FR-075: not pruned

    git(repo, "worktree", "prune")  # the developer's own statement that it is gone
    status, second = gc(run_wtenv, outside)

    assert status == 0 and second.released == [str(worktree)]


def test_an_orphan_whose_worktree_lock_is_held_is_skipped_busy_without_failing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    git_dir = remove_with_git(repo, worktree)
    holder = subprocess.Popen(
        [sys.executable, "-c", LOCK_HOLDER, git_dir], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"

        status, result = gc(run_wtenv, outside)

        assert status == 0 and result.ok  # FR-077: not waited for, not a failure
        assert result.skipped_busy == [str(worktree)]
        assert result.released == [] and result.removed == []
        assert git_dir in load().worktrees
    finally:
        holder.kill()
        holder.wait()

    status, later = gc(run_wtenv, outside)

    assert status == 0 and later.released == [str(worktree)]


def test_an_orphan_that_is_no_longer_orphaned_once_locked_is_skipped(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    git_dir = remove_with_git(repo, worktree)
    real_classify = orphans.classify
    seen: list[str] = []

    def reappears(entry: WorktreeEntry, listing: Sequence[WorktreeRecord] | None) -> Classification:
        """The real answer the first time; then the worktree is back."""
        seen.append(entry.git_dir)
        if len(seen) == 1:
            return real_classify(entry, listing)
        return Classification(Status.PROVISIONED)

    monkeypatch.setattr(orphans, "classify", reappears)

    result = orphans.gc()

    assert seen == [git_dir, git_dir]  # FR-074: classified again, with the lock held
    assert result.ok and result.released == [] and result.removed == [] and result.kept == []
    assert git_dir in load().worktrees


# --- gc --release (T106; scenario 9; FR-073) -----------------------------------------------------


def test_gc_release_releases_an_entry_whose_repository_was_deleted_and_can_be_repeated(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree, outside: Path
) -> None:
    stray, stray_dir = stray_of_a_deleted_repository(run_wtenv, make_repo, add_worktree)
    env_file = stray / ".env.local"

    status, result = gc(run_wtenv, outside, "--release", str(stray))

    assert status == 0 and result.ok and not result.dry_run
    assert result.released == [str(stray)]
    assert keys(result.removed) == [
        ("env_section", str(env_file)),
        ("env_file", str(env_file)),
        ("port_block", "20000-20009"),
        ("registry_entry", stray_dir),
    ]
    assert all(item.worktree == str(stray) for item in result.removed)
    assert stray_dir not in load().worktrees and not env_file.exists()

    status, again = gc(run_wtenv, outside, "--release", str(stray))

    assert status == 0 and again.ok
    assert again.no_entry == [str(stray)]
    assert again.released == [] and again.removed == [] and again.failed == []


def test_gc_release_acts_only_on_the_named_entries(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    up(run_wtenv, first)
    up(run_wtenv, second)
    first_dir = remove_with_git(repo, first)
    second_dir = remove_with_git(repo, second)

    status, result = gc(run_wtenv, outside, "--release", str(first))

    assert status == 0 and result.released == [str(first)]
    assert result.kept == []  # with --release, gc does not sweep
    assert first_dir not in load().worktrees
    assert second_dir in load().worktrees  # an unnamed orphan stays


def test_gc_release_refuses_a_path_whose_worktree_still_exists_and_changes_nothing(
    run_wtenv: Run,
    repo: Path,
    make_repo: Callable[[str], Path],
    add_worktree: AddWorktree,
    outside: Path,
) -> None:
    existing = add_worktree(repo, "existing", "existing")
    up(run_wtenv, existing)
    stray, _ = stray_of_a_deleted_repository(run_wtenv, make_repo, add_worktree)
    before = registry_path().read_bytes()

    process = run_wtenv(
        ["gc", "--release", str(stray), "--release", str(existing), "--json"], outside
    )

    assert process.returncode == 18
    result = parse_gc(process)
    assert not result.ok and result.error is not None
    assert result.error.code is ErrorCode.WORKTREE_EXISTS and result.error.exit_status == 18
    assert result.error.details["path"] == str(existing)
    assert "error [worktree_exists]" in process.stderr
    assert registry_path().read_bytes() == before  # not even the stray entry was released


def test_gc_release_refuses_a_moved_worktree_that_exists_elsewhere(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    new_place = worktree.parent / "moved-away"
    git(repo, "worktree", "move", str(worktree), str(new_place))
    before = registry_path().read_bytes()

    status, result = gc(run_wtenv, outside, "--release", str(worktree))

    assert status == 18
    assert result.error is not None and result.error.code is ErrorCode.WORKTREE_EXISTS
    assert result.error.details["path"] == str(worktree)
    assert result.error.details["current_path"] == str(new_place.resolve())
    assert registry_path().read_bytes() == before


def test_gc_release_dry_run_lists_and_changes_nothing(
    run_wtenv: Run, make_repo: Callable[[str], Path], add_worktree: AddWorktree, outside: Path
) -> None:
    stray, stray_dir = stray_of_a_deleted_repository(run_wtenv, make_repo, add_worktree)
    env_file = stray / ".env.local"
    before = (registry_path().read_bytes(), env_file.read_bytes())

    status, planned = gc(run_wtenv, outside, "--release", str(stray), "--dry-run")

    assert status == 0 and planned.ok and planned.dry_run
    assert planned.would_release == [str(stray)] and planned.released == []
    assert ("registry_entry", stray_dir) in keys(planned.would_remove)
    assert planned.removed == []
    assert (registry_path().read_bytes(), env_file.read_bytes()) == before


def test_gc_release_lists_and_removes_each_sqlite_side_file_of_a_worktree_left_behind(
    run_wtenv: Run, sqlite_repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    worktree = add_worktree(sqlite_repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    copy = worktree / ".wtenv" / "dev.sqlite3"
    sides = add_side_files(copy)
    expected = [("sqlite_file", str(path)) for path in [copy, *sides]]
    shutil.rmtree(sqlite_repo)  # the repository; the worktree's directory is left

    _, planned = gc(run_wtenv, outside, "--release", str(worktree), "--dry-run")

    assert [pair for pair in keys(planned.would_remove) if pair[0] == "sqlite_file"] == expected
    assert copy.exists() and all(path.exists() for path in sides)  # nothing was deleted

    status, actual = gc(run_wtenv, outside, "--release", str(worktree))

    assert status == 0
    assert [pair for pair in keys(actual.removed) if pair[0] == "sqlite_file"] == expected
    assert actual.removed == planned.would_remove
    assert not copy.exists() and not any(path.exists() for path in sides)


# --- gc --release and a live worktree at the path (T149; reading R5) ----------------------------


def test_gc_release_refuses_a_worktree_repaired_after_its_repository_moved(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, outside: Path
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    moved_repo = repo.parent / "app-moved"
    repo.rename(moved_repo)
    git(moved_repo, "worktree", "repair")  # the worktree works again, with a new git directory
    assert current_worktree(worktree).repository == str(moved_repo / ".git")
    env_file = worktree / ".env.local"
    before = (registry_path().read_bytes(), env_file.read_bytes())

    status, result = gc(run_wtenv, outside, "--release", str(worktree))

    assert status == 18
    assert result.error is not None and result.error.code is ErrorCode.WORKTREE_EXISTS
    assert result.error.details["path"] == str(worktree)
    assert (registry_path().read_bytes(), env_file.read_bytes()) == before


def test_gc_release_refuses_a_path_taken_by_a_worktree_of_another_repository(
    run_wtenv: Run,
    repo: Path,
    make_repo: Callable[[str], Path],
    add_worktree: AddWorktree,
    outside: Path,
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    remove_with_git(repo, worktree)
    other = make_repo("other")
    git(other, "worktree", "add", "-b", "taken", str(worktree))  # the same path, not provisioned
    before = registry_path().read_bytes()

    status, result = gc(run_wtenv, outside, "--release", str(worktree))

    assert status == 18
    assert result.error is not None and result.error.code is ErrorCode.WORKTREE_EXISTS
    assert result.error.details["path"] == str(worktree)
    assert registry_path().read_bytes() == before


# --- Docker: Postgres and compose (T107; SC-003; cli.md, `wtenv gc`, Credentials) ---------------


def test_gc_without_a_password_fails_the_orphans_database_until_pgpassword_is_given(
    run_wtenv: Run,
    postgres_repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    pg_env: dict[str, str],
    database_cleanup: list[str],
    no_password: None,
    outside: Path,
) -> None:
    worktree = add_worktree(postgres_repo, "feature-x", "feature-x")
    name = database_name(worktree)
    database_cleanup.append(name)
    up(run_wtenv, worktree, pg_env)
    git_dir = remove_with_git(postgres_repo, worktree)

    status, result = gc(run_wtenv, outside)  # gc reads no wtenv.toml, and libpq has nothing

    assert status == 13
    assert result.error is not None and result.error.code is ErrorCode.PARTIAL_FAILURE
    assert keys(result.failed) == [("postgres_database", name)]
    assert result.released == []
    assert postgres_server.password not in result.model_dump_json()
    assert postgres_server.database_exists(name)
    assert git_dir in load().worktrees  # still recorded

    status, finished = gc(run_wtenv, outside, env={"PGPASSWORD": postgres_server.password})

    assert status == 0 and finished.failed == []
    assert ("postgres_database", name) in keys(finished.removed)
    assert finished.released == [str(worktree)]
    assert not postgres_server.database_exists(name)
    assert git_dir not in load().worktrees


def test_one_gc_after_git_worktree_remove_leaves_no_database_container_volume_or_entry(
    run_wtenv: Run,
    postgres_server: PostgresServer,
    repo: Path,
    add_worktree: AddWorktree,
    compose_image: str,
    compose_projects: ComposeProjects,
    external_volume: str,
    pg_env: dict[str, str],
    database_cleanup: list[str],
    outside: Path,
) -> None:
    toml = COMPOSE_TOML + "\n" + postgres_toml(postgres_server)
    worktree = compose_worktree(repo, add_worktree, external_volume, toml)
    name = database_name(worktree)
    database_cleanup.append(name)
    up(run_wtenv, worktree, pg_env)
    project = compose_projects.track(project_of(worktree))
    start_stack(worktree)
    assert any(project_resources(project).values())
    git_dir = remove_with_git(repo, worktree)

    status, result = gc(run_wtenv, outside, env={"PGPASSWORD": postgres_server.password})

    assert status == 0 and result.failed == [], result
    assert result.released == [str(worktree)]
    assert not postgres_server.database_exists(name)
    assert all(not ids for ids in project_resources(project).values())
    assert volume_exists(external_volume)  # declared external: never removed (FR-039)
    assert git_dir not in load().worktrees
