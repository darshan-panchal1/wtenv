"""User story 4: lifecycle and cleanup, on real git worktrees (spec.md, User Story 4; T092 to T095,
T097, T112).

Every test works in a temporary repository with a temporary state directory (the `state_home`
fixture), so no test reads or writes the developer's registry. Tests that need Docker request a
fixture that skips them with a message when it is not available, and they remove exactly the
containers, networks, volumes, and databases they created.
"""

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
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
    parse_up,
    project_resources,
    sqlite_rows,
)

from wtenv.database import postgres_database_name
from wtenv.errors import ErrorCode
from wtenv.identity import current_worktree
from wtenv.output import DownResult, ItemKind
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
