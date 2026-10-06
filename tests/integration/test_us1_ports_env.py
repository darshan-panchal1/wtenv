"""User story 1: port and env isolation, on real git worktrees (spec.md, User Story 1; T039, T042,
T043)."""

import os
import socket
import stat
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from helpers import (
    BEGIN,
    COMPOSE_IMAGE,
    END,
    ComposeProjects,
    block_lines,
    commit_all,
    git,
    make_sqlite_template,
    parse_up,
    snapshot_tree,
)

from wtenv.envfile import read_section
from wtenv.identity import current_worktree
from wtenv.output import ItemKind, Status, UpResult, WarningCode
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def up(run_wtenv: Run, cwd: Path) -> UpResult:
    """Run `wtenv up --json` in `cwd` and return its document."""
    return parse_up(run_wtenv(["up", "--json"], cwd))


def ports_of(result: UpResult) -> set[int]:
    assert result.worktree is not None and result.worktree.block is not None
    block = result.worktree.block
    return set(range(block.start, block.end + 1))


def entry_of(worktree: Path) -> WorktreeEntry:
    """Return the registry entry of `worktree`."""
    return load().worktrees[current_worktree(worktree).git_dir]


def write_config(worktree: Path, text: str) -> None:
    (worktree / "wtenv.toml").write_text(text, encoding="utf-8")


@contextmanager
def listening_on(port: int) -> Iterator[None]:
    """Listen on `127.0.0.1:port`; skip the test when the port is not available."""
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", port))
    except OSError:
        listener.close()
        pytest.skip(f"port {port} is in use on this machine")
    listener.listen()
    try:
        yield
    finally:
        listener.close()


# --- scenario 1 (FR-005, FR-007, FR-015) -----------------------------------------------


def test_up_allocates_a_block_and_writes_port_to_the_env_file(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    result = parse_up(process)
    assert result.ok and result.error is None and result.warnings == []
    assert result.worktree is not None
    assert result.worktree.status is Status.PROVISIONED
    assert result.worktree.path == str(worktree)
    assert result.worktree.env_file == ".env.local"
    block = result.worktree.block
    assert block is not None and block.size == 10 and block.end == block.start + 9
    env_file = worktree / ".env.local"
    assert read_section(env_file) == [("PORT", str(block.start))]
    assert [(port.variable, port.port) for port in result.worktree.ports] == [("PORT", block.start)]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    changes = {(change.item.kind, change.action) for change in result.changes}
    assert (ItemKind.PORT_BLOCK, "created") in changes
    assert (ItemKind.ENV_SECTION, "created") in changes


def test_up_records_the_worktree_as_provisioned(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")

    up(run_wtenv, worktree)

    entry = entry_of(worktree)
    assert entry.state == "provisioned"
    assert entry.path == str(worktree)
    assert entry.env_file is not None
    assert (entry.env_file.path, entry.env_file.state.value) == (".env.local", "created")
    assert entry.env_file.created_file is True
    assert entry.exclude_patterns == ["/.env.local"]


def test_up_prints_text_for_people_without_json(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")

    process = run_wtenv(["up"], worktree)

    assert process.returncode == 0, process.stderr
    lines = process.stdout.splitlines()
    assert lines[0] == f"wtenv: provisioned {worktree}"
    assert any(line.startswith("  ports") and "PORT=" in line for line in lines)
    assert any(line.startswith("  env file") and ".env.local (created)" in line for line in lines)


def test_up_never_waits_for_input(run_wtenv: Run, repo: Path, add_worktree: AddWorktree) -> None:
    # `run_wtenv` closes standard input, so a prompt would fail here (FR-004, SC-009).
    worktree = add_worktree(repo, "feature-x", "feature-x")

    assert run_wtenv(["up"], worktree).returncode == 0


# --- scenario 2 (FR-008) ---------------------------------------------------------------


def test_two_worktrees_get_blocks_that_share_no_port(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    first = up(run_wtenv, add_worktree(repo, "one", "branch-one"))
    second = up(run_wtenv, add_worktree(repo, "two", "branch-two"))

    assert ports_of(first).isdisjoint(ports_of(second))


def test_the_main_worktree_works_like_any_other(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    main = up(run_wtenv, repo)
    linked = up(run_wtenv, add_worktree(repo, "linked", "linked"))

    assert main.ok and (repo / ".env.local").exists()
    assert ports_of(main).isdisjoint(ports_of(linked))


# --- scenario 3 (FR-011, FR-017, SC-006) -----------------------------------------------


def test_a_repeat_up_from_a_subdirectory_changes_nothing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    first = up(run_wtenv, worktree)
    env_bytes = (worktree / ".env.local").read_bytes()
    registry_bytes = registry_path().read_bytes()
    subdirectory = worktree / "src" / "deep"
    subdirectory.mkdir(parents=True)

    second = up(run_wtenv, subdirectory)

    assert second.ok
    assert second.worktree == first.worktree
    assert (worktree / ".env.local").read_bytes() == env_bytes
    assert registry_path().read_bytes() == registry_bytes
    assert {change.action for change in second.changes} == {"unchanged"}
    assert {change.item.kind for change in second.changes} >= {
        ItemKind.PORT_BLOCK,
        ItemKind.ENV_SECTION,
    }


def test_a_repeat_up_stays_provisioned_and_says_unchanged_in_text(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    run_wtenv(["up"], worktree)

    process = run_wtenv(["up"], worktree)

    assert process.returncode == 0
    assert ".env.local (unchanged)" in process.stdout
    assert entry_of(worktree).state == "provisioned"


def test_a_section_edited_by_hand_is_restored_by_the_next_up(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    first = up(run_wtenv, worktree)
    env_file = worktree / ".env.local"
    original = env_file.read_bytes()
    env_file.write_bytes(original.replace(b"PORT=", b"PORT=9"))

    result = up(run_wtenv, worktree)

    assert env_file.read_bytes() == original
    assert result.worktree == first.worktree
    assert (ItemKind.ENV_SECTION, "updated") in {(c.item.kind, c.action) for c in result.changes}
    assert entry_of(worktree).state == "provisioned"


def test_an_exclude_block_that_was_removed_is_put_back_by_the_next_up(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    exclude_path = repo / ".git" / "info" / "exclude"
    exclude_path.write_text("*.log\n", encoding="utf-8")
    registry_bytes = registry_path().read_bytes()

    result = up(run_wtenv, worktree)

    assert block_lines(exclude_path) == ["/.env.local"]
    assert git(worktree, "status", "--porcelain") == ""
    assert (ItemKind.EXCLUDE_ENTRIES, "updated") in {
        (c.item.kind, c.action) for c in result.changes
    }
    assert registry_path().read_bytes() == registry_bytes


# --- scenario 4 (FR-009) ---------------------------------------------------------------


def test_a_block_avoids_a_port_in_use(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")

    with listening_on(20003):
        result = up(run_wtenv, worktree)

    assert 20003 not in ports_of(result)
    assert result.worktree is not None and result.worktree.block is not None
    block = result.worktree.block
    assert not block.start <= 20003 <= block.end  # the block does not hold the busy port
    assert block.start > 20000  # and it is not the first candidate, which does


# --- scenario 5 (FR-014) ----------------------------------------------------------------


def test_several_variables_get_different_ports_in_order(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, 'ports = ["PORT", "API_PORT", "VITE_PORT"]\n')

    first = up(run_wtenv, worktree)
    second = up(run_wtenv, worktree)

    assert first.worktree is not None and first.worktree.block is not None
    start = first.worktree.block.start
    expected = [("PORT", str(start)), ("API_PORT", str(start + 1)), ("VITE_PORT", str(start + 2))]
    assert read_section(worktree / ".env.local") == expected
    assert second.worktree == first.worktree


# --- scenario 6 (FR-016, FR-018) ---------------------------------------------------------


def test_the_developers_lines_are_unchanged_and_the_section_follows_them(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    mine = b"# my settings\nSECRET_KEY=abc\n\nDEBUG = 'yes'\n"
    (worktree / ".env.local").write_bytes(mine)

    result = up(run_wtenv, worktree)

    content = (worktree / ".env.local").read_bytes()
    assert content.startswith(mine)
    assert content[len(mine) :].decode().startswith(BEGIN + "\nPORT=")
    assert content.decode().endswith(END + "\n")
    assert (ItemKind.ENV_SECTION, "created") in {(c.item.kind, c.action) for c in result.changes}


def test_up_leaves_git_status_empty_in_every_worktree(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(repo, "one", "branch-one")
    second = add_worktree(repo, "two", "branch-two")
    for worktree in (repo, first, second):
        up(run_wtenv, worktree)

    for worktree in (repo, first, second):
        assert (worktree / ".env.local").exists()
        assert git(worktree, "status", "--porcelain") == ""


# --- scenario 7 (FR-015) -----------------------------------------------------------------


def test_env_file_set_to_another_path_writes_there(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / "config").mkdir()
    write_config(worktree, 'env_file = "config/.env.dev"\n')
    commit_all(worktree, "configure wtenv")

    result = up(run_wtenv, worktree)

    assert not (worktree / ".env.local").exists()
    assert read_section(worktree / "config" / ".env.dev")[0][0] == "PORT"
    assert result.worktree is not None and result.worktree.env_file == "config/.env.dev"
    assert git(worktree, "status", "--porcelain") == ""


# --- outside a worktree (FR-002) ---------------------------------------------------------


def test_outside_a_worktree_up_fails_with_exit_4_and_changes_nothing(
    run_wtenv: Run, tmp_path: Path
) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()

    process = run_wtenv(["up", "--json"], outside)

    assert process.returncode == 4
    result = parse_up(process)
    assert result.ok is False and result.error is not None
    assert result.error.code.value == "not_in_worktree"
    assert result.error.details["cwd"] == str(outside)
    assert not registry_path().exists()
    assert list(outside.iterdir()) == []


# --- errors that change nothing (T042; FR-018, FR-081, FR-064, FR-012, FR-070) ------------


def make_unusable_env_file(worktree: Path, reason: str) -> Path:
    """Arrange the worktree so that its env file is unusable for `reason`; return its path."""
    env_file = worktree / ".env.local"
    if reason == "is_directory":
        env_file.mkdir()
    elif reason == "parent_missing":
        write_config(worktree, 'env_file = "missing/.env"\n')
        env_file = worktree / "missing" / ".env"
    elif reason == "not_writable":
        env_file.write_text("A=1\n", encoding="utf-8")
        env_file.chmod(0o444)
    elif reason == "tracked_by_git":
        env_file.write_text("A=1\n", encoding="utf-8")
        commit_all(worktree, "track the env file")
    elif reason == "markers_damaged":
        env_file.write_text(f"A=1\n{BEGIN}\nPORT=1\n", encoding="utf-8")
    return env_file


@pytest.mark.parametrize(
    "reason",
    ["is_directory", "parent_missing", "not_writable", "tracked_by_git", "markers_damaged"],
)
def test_an_unusable_env_file_fails_with_exit_7_and_changes_nothing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, reason: str
) -> None:
    if reason == "not_writable" and os.geteuid() == 0:
        pytest.skip("root can write to a read-only file")
    worktree = add_worktree(repo, "feature-x", "feature-x")
    env_file = make_unusable_env_file(worktree, reason)
    before = None if env_file.is_dir() or not env_file.exists() else env_file.read_bytes()

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 7, process.stderr
    result = parse_up(process)
    assert result.error is not None
    assert result.error.code.value == "env_file_unusable"
    assert result.error.details["reason"] == reason
    assert result.error.details["path"] == str(env_file)
    assert not registry_path().exists()
    if before is None:
        assert env_file.is_dir() or not env_file.exists()
    else:
        assert env_file.read_bytes() == before
    assert "wtenv: error [env_file_unusable]" in process.stderr


def test_an_unknown_setting_fails_with_exit_3_on_a_first_up(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, "verbose = true\n")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 3
    result = parse_up(process)
    assert result.error is not None and result.error.details["setting"] == "verbose"
    assert not registry_path().exists()
    assert not (worktree / ".env.local").exists()


def test_an_unknown_setting_fails_with_exit_3_and_changes_nothing_once_provisioned(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    registry_bytes = registry_path().read_bytes()
    env_bytes = (worktree / ".env.local").read_bytes()
    write_config(worktree, "verbose = true\n")

    process = run_wtenv(["up"], worktree)

    assert process.returncode == 3
    assert registry_path().read_bytes() == registry_bytes
    assert (worktree / ".env.local").read_bytes() == env_bytes


def test_more_variables_than_the_block_holds_fails_on_a_first_up(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, 'ports = ["A", "B", "C"]\nblock_size = 2\n')

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 3
    result = parse_up(process)
    assert result.error is not None
    assert result.error.details["min_block_size"] == 3
    assert result.error.details["setting"] == "block_size"
    assert not registry_path().exists()
    assert not (worktree / ".env.local").exists()


def test_more_variables_than_the_block_holds_changes_nothing_once_provisioned(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    registry_bytes = registry_path().read_bytes()
    env_bytes = (worktree / ".env.local").read_bytes()
    names = ", ".join(f'"V{i}"' for i in range(11))
    write_config(worktree, f"ports = [{names}]\n")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 3
    error = parse_up(process).error
    assert error is not None and error.details["min_block_size"] == 11
    assert registry_path().read_bytes() == registry_bytes
    assert (worktree / ".env.local").read_bytes() == env_bytes


def test_a_managed_variable_defined_outside_the_section_is_a_warning(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / ".env.local").write_bytes(b"PORT=3000\n")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    result = parse_up(process)
    assert [warning.code for warning in result.warnings] == [WarningCode.ENV_DUPLICATE_VARIABLE]
    assert "PORT" in result.warnings[0].message
    assert "wtenv: warning [env_duplicate_variable]" in process.stderr
    assert (worktree / ".env.local").read_bytes().startswith(b"PORT=3000\n")


def test_a_registry_that_is_not_json_fails_with_exit_16_and_is_not_rewritten(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    registry_path().parent.mkdir(parents=True, exist_ok=True)
    registry_path().write_text("this is not json", encoding="utf-8")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 16
    result = parse_up(process)
    assert result.error is not None and result.error.details["reason"] == "invalid_json"
    assert registry_path().read_text(encoding="utf-8") == "this is not json"
    assert not (worktree / ".env.local").exists()


def test_no_free_block_fails_with_exit_6_and_allocates_nothing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, "block_size = 1000\n")
    held = []
    try:
        # Ten candidates of 1000 ports; one busy port in each makes every candidate unusable.
        for start in range(20000, 30000, 1000):
            listener = socket.socket()
            try:
                listener.bind(("127.0.0.1", start + 7))
            except OSError:
                listener.close()  # already taken by someone else, which counts as taken
                continue
            listener.listen()
            held.append(listener)

        process = run_wtenv(["up", "--json"], worktree)
    finally:
        for listener in held:
            listener.close()

    assert process.returncode == 6, process.stderr
    result = parse_up(process)
    assert result.error is not None
    assert result.error.details == {"block_size": 1000, "range": "20000-29999"}
    assert not registry_path().exists() or load().worktrees == {}
    assert not (worktree / ".env.local").exists()


# --- configuration changes and moves (T043; FR-062, FR-065, FR-084, FR-011) -----------------


def test_a_changed_ports_list_is_reassigned_within_the_same_block(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, 'ports = ["PORT", "API_PORT"]\n')
    first = up(run_wtenv, worktree)
    assert first.worktree is not None and first.worktree.block is not None
    start = first.worktree.block.start

    write_config(worktree, 'ports = ["API_PORT", "PORT", "VITE_PORT"]\n')
    second = up(run_wtenv, worktree)

    assert second.worktree is not None and second.worktree.block == first.worktree.block
    assert read_section(worktree / ".env.local") == [
        ("API_PORT", str(start)),
        ("PORT", str(start + 1)),
        ("VITE_PORT", str(start + 2)),
    ]
    assert entry_of(worktree).state == "provisioned"
    assert (ItemKind.ENV_SECTION, "updated") in {(c.item.kind, c.action) for c in second.changes}


def test_a_changed_block_size_allocates_a_new_block_and_releases_the_old_one(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    first = up(run_wtenv, worktree)
    assert first.worktree is not None and first.worktree.block is not None
    old = first.worktree.block

    write_config(worktree, "block_size = 20\n")
    second = up(run_wtenv, worktree)

    assert second.worktree is not None and second.worktree.block is not None
    new = second.worktree.block
    assert new.size == 20 and new != old
    old_name, new_name = f"{old.start}-{old.end}", f"{new.start}-{new.end}"
    reported = {(c.item.kind, c.item.name, c.action) for c in second.changes}
    assert (ItemKind.PORT_BLOCK, old_name, "released") in reported
    assert (ItemKind.PORT_BLOCK, new_name, "created") in reported
    assert read_section(worktree / ".env.local") == [("PORT", str(new.start))]
    entry = entry_of(worktree)
    assert entry.block.size == 20 and entry.state == "provisioned"


def test_a_changed_env_file_moves_the_section_to_the_new_file(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / ".env.local").write_bytes(b"MINE=1\n")
    up(run_wtenv, worktree)
    write_config(worktree, 'env_file = ".env.dev"\n')

    result = up(run_wtenv, worktree)

    assert (worktree / ".env.local").read_bytes() == b"MINE=1\n"
    assert [name for name, _ in read_section(worktree / ".env.dev")] == ["PORT"]
    reported = {(c.item.kind, c.item.name, c.action) for c in result.changes}
    assert (ItemKind.ENV_SECTION, str(worktree / ".env.local"), "released") in reported
    assert (ItemKind.ENV_SECTION, str(worktree / ".env.dev"), "created") in reported
    entry = entry_of(worktree)
    assert entry.env_file is not None and entry.env_file.path == ".env.dev"
    assert entry.state == "provisioned"


def test_the_old_env_file_is_deleted_when_wtenv_created_it_and_nothing_else_is_in_it(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    write_config(worktree, 'env_file = ".env.dev"\n')

    up(run_wtenv, worktree)

    assert not (worktree / ".env.local").exists()
    assert (worktree / ".env.dev").exists()


def test_each_worktree_is_provisioned_from_its_own_wtenv_toml(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    one = add_worktree(repo, "one", "branch-one")
    two = add_worktree(repo, "two", "branch-two")
    write_config(one, 'ports = ["PORT"]\n')
    commit_all(one, "configure one")
    write_config(two, 'ports = ["PORT", "DEBUG_PORT"]\nblock_size = 5\n')
    commit_all(two, "configure two")

    first = up(run_wtenv, one)
    second = up(run_wtenv, two)

    assert [name for name, _ in read_section(one / ".env.local")] == ["PORT"]
    assert [name for name, _ in read_section(two / ".env.local")] == ["PORT", "DEBUG_PORT"]
    assert first.worktree is not None and first.worktree.block is not None
    assert second.worktree is not None and second.worktree.block is not None
    assert (first.worktree.block.size, second.worktree.block.size) == (10, 5)


def test_a_moved_worktree_keeps_its_block_and_records_the_new_location(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    first = up(run_wtenv, worktree)
    new_path = repo.parent / "moved-feature-x"
    git(repo, "worktree", "move", str(worktree), str(new_path))
    new_path = new_path.resolve()

    process = run_wtenv(["up", "--json"], new_path)

    assert process.returncode == 0, process.stderr
    result = parse_up(process)
    assert result.worktree is not None and first.worktree is not None
    assert result.worktree.block == first.worktree.block
    assert result.worktree.git_dir == first.worktree.git_dir
    assert result.worktree.path == str(new_path)
    assert [warning.code for warning in result.warnings] == [WarningCode.WORKTREE_MOVED]
    assert "wtenv: warning [worktree_moved]" in process.stderr
    entry = entry_of(new_path)
    assert entry.path == str(new_path) and entry.state == "provisioned"
    assert len(load().worktrees) == 1


def test_a_port_of_the_block_taken_later_does_not_move_the_block(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    first = up(run_wtenv, worktree)
    env_bytes = (worktree / ".env.local").read_bytes()
    assert first.worktree is not None and first.worktree.block is not None
    taken = first.worktree.block.start + 5

    with listening_on(taken):
        second = up(run_wtenv, worktree)

    assert second.ok
    assert second.worktree == first.worktree
    assert (worktree / ".env.local").read_bytes() == env_bytes


# --- symbolic links (T152; FR-086) -----------------------------------------------------
#
# Every case links to a decoy outside the worktree, or to another worktree, and compares it
# before and after. A link is refused with `env_file_unusable`, reason `symlink`, `details.path`
# the link, and nothing changes: not the target, not the registry.

SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'
COMPOSE_TOML = (
    'ports = ["PORT", "CACHE_PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'
)
COMPOSE_STACK = f"""\
services:
  cache:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    ports:
      - "${{CACHE_PORT:-6379}}:6379"
"""


def assert_refused_as_symlink(process: subprocess.CompletedProcess[str], link: Path) -> None:
    """The run failed with exit 7 and `env_file_unusable`, reason `symlink`, naming `link`."""
    assert process.returncode == 7, process.stdout + process.stderr
    result = parse_up(process)
    assert result.error is not None
    assert result.error.code.value == "env_file_unusable"
    assert result.error.details["reason"] == "symlink"
    assert result.error.details["path"] == str(link)
    assert "wtenv: error [env_file_unusable]" in process.stderr


def sqlite_worktree(repo: Path, add_worktree: AddWorktree, name: str) -> Path:
    """Add a worktree whose commit holds a SQLite template and its `wtenv.toml`."""
    worktree = add_worktree(repo, name, f"branch-{name}")
    make_sqlite_template(worktree / "db" / "dev.sqlite3")
    write_config(worktree, SQLITE_TOML)
    commit_all(worktree)
    return worktree


def compose_worktree(repo: Path, add_worktree: AddWorktree) -> Path:
    """Add a worktree whose commit holds a compose file and a `wtenv.toml` with `[compose]`."""
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / "compose.yaml").write_text(COMPOSE_STACK, encoding="utf-8")
    write_config(worktree, COMPOSE_TOML)
    commit_all(worktree)
    return worktree


def provisioned_compose_worktree(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, compose_projects: ComposeProjects
) -> Path:
    """`compose_worktree`, brought up; its compose project is tracked for cleanup."""
    worktree = compose_worktree(repo, add_worktree)
    up(run_wtenv, worktree)
    compose = entry_of(worktree).compose
    assert compose is not None
    compose_projects.track(compose.project)
    return worktree


@pytest.mark.parametrize("dangling", [False, True], ids=["to-a-file", "dangling"])
def test_an_env_file_that_is_a_link_is_refused_and_its_target_is_left_alone(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, decoy: Path, dangling: bool
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    link = worktree / ".env.local"
    link.symlink_to(decoy / ("new.env" if dangling else "shared.env"))
    before = snapshot_tree(decoy)

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, link)
    assert snapshot_tree(decoy) == before
    assert link.is_symlink()
    assert not registry_path().exists()  # no entry was created


def test_an_env_file_in_a_directory_that_is_a_link_is_refused(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, decoy: Path
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, 'env_file = "config/.env.local"\n')
    (worktree / "config").symlink_to(decoy)
    before = snapshot_tree(decoy)

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, worktree / "config")  # the link, not the file below it
    assert snapshot_tree(decoy) == before  # no `.env.local` was written into the decoy
    assert not registry_path().exists()


def test_an_env_file_two_directories_down_a_link_is_refused_at_the_link(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, decoy: Path
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    write_config(worktree, 'env_file = "config/sub/.env.local"\n')
    (worktree / "config").symlink_to(decoy)  # `sub` exists in the decoy
    before = snapshot_tree(decoy)

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, worktree / "config")
    assert snapshot_tree(decoy) == before
    assert not registry_path().exists()


def test_a_sqlite_directory_that_is_a_link_to_another_worktrees_copy_is_refused(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    other = sqlite_worktree(repo, add_worktree, "other")
    up(run_wtenv, other)
    worktree = sqlite_worktree(repo, add_worktree, "feature-x")
    (worktree / ".wtenv").symlink_to(other / ".wtenv")
    other_before = snapshot_tree(other / ".wtenv")
    registry_before = registry_path().read_bytes()

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, worktree / ".wtenv")
    assert snapshot_tree(other / ".wtenv") == other_before
    assert registry_path().read_bytes() == registry_before
    assert not (worktree / ".env.local").exists()


@pytest.mark.parametrize("dangling", [False, True], ids=["to-a-file", "dangling"])
def test_a_sqlite_copy_that_is_a_link_is_refused_and_its_target_is_left_alone(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, decoy: Path, dangling: bool
) -> None:
    worktree = sqlite_worktree(repo, add_worktree, "feature-x")
    (worktree / ".wtenv").mkdir()
    copy = worktree / ".wtenv" / "dev.sqlite3"
    copy.symlink_to(decoy / ("new.sqlite3" if dangling else "notes.txt"))
    before = snapshot_tree(decoy)

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, copy)
    assert snapshot_tree(decoy) == before
    assert not registry_path().exists()
    assert not (worktree / ".env.local").exists()


def test_a_repeat_up_refuses_an_env_file_that_has_become_a_link(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, decoy: Path
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    env_file = worktree / ".env.local"
    env_file.unlink()
    env_file.symlink_to(decoy / "shared.env")
    decoy_before = snapshot_tree(decoy)
    registry_before = registry_path().read_bytes()

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, env_file)
    assert snapshot_tree(decoy) == decoy_before
    assert registry_path().read_bytes() == registry_before
    assert env_file.is_symlink()


def test_a_changed_env_file_does_not_remove_a_section_through_a_link(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, decoy: Path
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")
    up(run_wtenv, worktree)
    old = worktree / ".env.local"
    old.unlink()
    old.symlink_to(decoy / "shared.env")  # holds a wtenv section that a removal would take out
    write_config(worktree, 'env_file = ".env.dev"\n')
    decoy_before = snapshot_tree(decoy)
    registry_before = registry_path().read_bytes()

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, old)
    assert snapshot_tree(decoy) == decoy_before
    assert registry_path().read_bytes() == registry_before
    assert not (worktree / ".env.dev").exists()  # nothing was written for the new file either


def test_a_compose_override_that_is_a_link_is_refused_before_anything_changes(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    compose_projects: ComposeProjects,
    decoy: Path,
) -> None:
    worktree = provisioned_compose_worktree(run_wtenv, repo, add_worktree, compose_projects)
    override = worktree / "compose.override.yaml"
    override.unlink()
    override.symlink_to(decoy / "notes.txt")
    decoy_before = snapshot_tree(decoy)
    registry_before = registry_path().read_bytes()
    env_before = (worktree / ".env.local").read_bytes()

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, override)
    assert snapshot_tree(decoy) == decoy_before
    assert registry_path().read_bytes() == registry_before
    assert (worktree / ".env.local").read_bytes() == env_before


def test_a_compose_override_that_is_a_link_is_refused_on_a_first_up_too(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, compose_docker: None, decoy: Path
) -> None:
    worktree = compose_worktree(repo, add_worktree)
    override = worktree / "compose.override.yaml"
    override.symlink_to(decoy / "new.override.yaml")  # dangling: a write would create it
    decoy_before = snapshot_tree(decoy)

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, override)
    assert snapshot_tree(decoy) == decoy_before
    assert not registry_path().exists()


def test_removing_compose_does_not_remove_an_override_that_is_a_link(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    compose_projects: ComposeProjects,
    decoy: Path,
) -> None:
    worktree = provisioned_compose_worktree(run_wtenv, repo, add_worktree, compose_projects)
    override = worktree / "compose.override.yaml"
    override.unlink()
    override.symlink_to(decoy / "notes.txt")
    write_config(worktree, 'ports = ["PORT", "CACHE_PORT"]\n')  # no [compose]: the override goes
    decoy_before = snapshot_tree(decoy)
    registry_before = registry_path().read_bytes()

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, override)
    assert snapshot_tree(decoy) == decoy_before
    assert registry_path().read_bytes() == registry_before
    assert override.is_symlink()


def test_a_new_compose_file_does_not_remove_the_old_override_through_a_link(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    compose_projects: ComposeProjects,
    decoy: Path,
) -> None:
    worktree = provisioned_compose_worktree(run_wtenv, repo, add_worktree, compose_projects)
    old = worktree / "compose.override.yaml"
    old.unlink()
    old.symlink_to(decoy / "notes.txt")
    (worktree / "docker-compose.yml").write_text(COMPOSE_STACK, encoding="utf-8")
    write_config(worktree, COMPOSE_TOML.replace("compose.yaml", "docker-compose.yml"))
    decoy_before = snapshot_tree(decoy)
    registry_before = registry_path().read_bytes()

    process = run_wtenv(["up", "--json"], worktree)

    assert_refused_as_symlink(process, old)
    assert snapshot_tree(decoy) == decoy_before
    assert registry_path().read_bytes() == registry_before
    assert not (worktree / "docker-compose.override.yml").exists()
