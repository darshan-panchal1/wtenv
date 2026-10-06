"""An error in the middle of a release is a failed item, or a stopped `gc`, never a crash
(T191, T193; review 2, finding M3; FR-041, FR-042, FR-073).

`OSError` from the env file or the exclude file, and a `WtenvError` such as `registry_busy` during
one entry of `gc`, must not end the run with `internal_error` (exit 1) and a result that says
nothing: the item is `failed` with a reason that names the path and the error, and the result lists
what was released before it. No git and no Docker run here: `git_listing` is replaced by an empty
listing, and the entries are bare.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from wtenv import envfile, exclude, orphans, registry, teardown
from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import WorktreeRecord
from wtenv.identity import WorktreeIdentity
from wtenv.output import ItemKind, ResourceState
from wtenv.registry import EnvFileRecord, PortBlock, WorktreeEntry

BEGIN = envfile.BEGIN_MARKER
END = envfile.END_MARKER
SECTION = f"{BEGIN}\nPORT=1\n{END}\n".encode()


def entry_at(
    root: Path, *, repository: Path, block: int = 20000, env: str | None = ".env.local"
) -> WorktreeEntry:
    git_dir = repository / "worktrees" / root.name
    return WorktreeEntry(
        git_dir=str(git_dir),
        path=str(root),
        repository=str(repository),
        state="provisioned",
        block=PortBlock(start=block, size=10),
        env_file=None
        if env is None
        else EnvFileRecord(
            path=env, created_file=True, added_newline=False, state=ResourceState.CREATED
        ),
    )


def record(*entries: WorktreeEntry) -> None:
    with registry.transaction() as reg:
        for entry in entries:
            reg.worktrees[entry.git_dir] = entry


def refuse(path: Path, error: OSError) -> object:
    """Return a stand-in for `os.unlink` that fails for `path` only."""
    real = os.unlink

    def unlink(target: str | Path, *args: object, **kwargs: object) -> None:
        if Path(target) == path:
            raise error
        real(target, *args, **kwargs)  # type: ignore[arg-type]

    return unlink


# --- an OSError in the env step (T191) -----------------------------------------------------------


def test_an_env_file_that_cannot_be_deleted_is_a_failed_item_naming_the_path_and_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    env = root / ".env.local"
    env.write_bytes(SECTION)  # wtenv made it and nothing else is in it: it is deleted
    entry = entry_at(root, repository=tmp_path / "repo.git")
    record(entry)
    monkeypatch.setattr(os, "unlink", refuse(env, PermissionError(13, "Permission denied")))

    release = teardown.release_entry(entry)

    assert [(f.kind, f.name) for f in release.failed] == [(ItemKind.ENV_SECTION, str(env))]
    assert str(env) in release.failed[0].reason and "Permission denied" in release.failed[0].reason
    assert release.released is False
    recorded = registry.load().worktrees[entry.git_dir]
    assert recorded.state == "incomplete" and recorded.env_file is not None
    assert env.read_bytes() == SECTION


def test_an_env_file_that_cannot_be_rewritten_is_a_failed_item_naming_the_path_and_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    env = root / ".env.local"
    original = b"MINE=1\n" + SECTION
    env.write_bytes(original)
    entry = entry_at(root, repository=tmp_path / "repo.git")
    record(entry)

    def no_write(path: Path, data: bytes, mode: int) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(envfile, "write_atomic", no_write)

    release = teardown.release_entry(entry)

    assert [(f.kind, f.name) for f in release.failed] == [(ItemKind.ENV_SECTION, str(env))]
    assert str(env) in release.failed[0].reason and "Permission denied" in release.failed[0].reason
    assert release.released is False
    assert registry.load().worktrees[entry.git_dir].state == "incomplete"
    assert env.read_bytes() == original


def test_a_dry_run_reports_the_same_item_when_the_env_file_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    env = root / ".env.local"
    env.write_bytes(SECTION)
    entry = entry_at(root, repository=tmp_path / "repo.git")
    record(entry)
    real = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self == env:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    planned = teardown.plan_release(entry)

    assert [(f.kind, f.name) for f in planned.failed] == [(ItemKind.ENV_SECTION, str(env))]
    assert str(env) in planned.failed[0].reason


# --- an OSError in the exclude step (T191) -------------------------------------------------------


def test_an_exclude_file_that_cannot_be_rewritten_is_a_failed_item_and_the_entry_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    repository = tmp_path / "repo.git"
    (repository / "info").mkdir(parents=True)
    block = f"{BEGIN}\n/.env.local\n{END}\n".encode()
    (repository / "info" / "exclude").write_bytes(block)
    entry = entry_at(root, repository=repository, env=None)  # the last entry of the repository
    record(entry)

    def no_write(path: Path, data: bytes, mode: int) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(exclude, "write_atomic", no_write)

    release = teardown.release_entry(entry)

    exclude_path = repository / "info" / "exclude"
    assert [(f.kind, f.name) for f in release.failed] == [
        (ItemKind.EXCLUDE_ENTRIES, str(exclude_path))
    ]
    assert str(exclude_path) in release.failed[0].reason
    assert "Permission denied" in release.failed[0].reason
    assert release.released is False
    assert entry.git_dir in registry.load().worktrees  # the entry and its block stay
    assert exclude_path.read_bytes() == block


@pytest.mark.parametrize("which", ["env", "exclude"])
def test_down_gives_partial_failure_and_exit_status_13_for_such_an_error(
    which: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    repository = tmp_path / "repo.git"
    (repository / "info").mkdir(parents=True)
    (repository / "info" / "exclude").write_bytes(f"{BEGIN}\n/x\n{END}\n".encode())
    (root / ".env.local").write_bytes(b"MINE=1\n" + SECTION)
    entry = entry_at(root, repository=repository)
    record(entry)

    def no_write(path: Path, data: bytes, mode: int) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(envfile if which == "env" else exclude, "write_atomic", no_write)
    identity = WorktreeIdentity(git_dir=entry.git_dir, path=str(root), repository=str(repository))

    result = teardown._down(identity, dry_run=False)

    assert not result.ok and result.error is not None
    assert (result.error.code, result.error.exit_status) == (ErrorCode.PARTIAL_FAILURE, 13)
    assert len(result.failed) == 1


# --- an error during one entry of `gc` (T193) -----------------------------------------------------


@pytest.fixture
def three_orphans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[WorktreeEntry]:
    """Three entries whose worktrees are gone, in three repositories that answer with no worktree."""
    entries = []
    for number, name in enumerate(["one", "two", "three"]):
        root = tmp_path / name  # does not exist; its parent does
        entries.append(
            entry_at(root, repository=tmp_path / f"{name}.git", block=20000 + 10 * number, env=None)
        )
    record(*entries)
    monkeypatch.setattr(orphans, "git_listing", lambda repository: list[WorktreeRecord]())
    return entries


def busy_on_the_second(monkeypatch: pytest.MonkeyPatch, entries: list[WorktreeEntry]) -> list[str]:
    """Make the release of the second entry raise `registry_busy`; return the entries released."""
    real = teardown.release_entry
    attempted: list[str] = []

    def release_entry(entry: WorktreeEntry, *, password: str | None = None) -> teardown.Release:
        attempted.append(entry.path)
        if entry.git_dir == entries[1].git_dir:
            raise WtenvError(ErrorCode.REGISTRY_BUSY, "the registry is busy", hint="Try again.")
        return real(entry, password=password)

    monkeypatch.setattr(teardown, "release_entry", release_entry)
    return attempted


def test_plain_gc_stops_at_the_entry_that_raised_and_returns_what_it_had_released(
    three_orphans: list[WorktreeEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = three_orphans
    attempted = busy_on_the_second(monkeypatch, three_orphans)

    result = orphans.gc()

    assert result.released == [first.path]
    assert {item.kind for item in result.removed} >= {ItemKind.PORT_BLOCK, ItemKind.REGISTRY_ENTRY}
    assert not result.ok and result.error is not None
    assert (result.error.code, result.error.exit_status) == (ErrorCode.REGISTRY_BUSY, 14)
    assert attempted == [first.path, second.path]  # the third is not touched
    left = registry.load().worktrees
    assert first.git_dir not in left and second.git_dir in left and third.git_dir in left


def test_gc_release_stops_at_the_entry_that_raised_and_returns_what_it_had_released(
    three_orphans: list[WorktreeEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = three_orphans
    attempted = busy_on_the_second(monkeypatch, three_orphans)

    result = orphans.gc_release([first.path, second.path, third.path])

    assert result.released == [first.path]
    assert not result.ok and result.error is not None
    assert (result.error.code, result.error.exit_status) == (ErrorCode.REGISTRY_BUSY, 14)
    assert attempted == [first.path, second.path]
    left = registry.load().worktrees
    assert first.git_dir not in left and second.git_dir in left and third.git_dir in left


def test_a_dry_run_that_meets_an_error_stops_and_returns_what_it_had_listed(
    three_orphans: list[WorktreeEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, _ = three_orphans
    real = teardown.plan_release
    planned: list[str] = []

    def plan_release(entry: WorktreeEntry, **kwargs: Any) -> teardown.Release:
        planned.append(entry.path)
        if entry.git_dir == second.git_dir:
            raise WtenvError(ErrorCode.REGISTRY_BUSY, "the registry is busy")
        return real(entry, **kwargs)

    monkeypatch.setattr(teardown, "plan_release", plan_release)

    result = orphans.gc(dry_run=True)

    assert result.would_release == [first.path]
    assert result.error is not None and result.error.exit_status == 14
    assert planned == [first.path, second.path]
