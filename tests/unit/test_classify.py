"""`classify`: every row of data-model.md, "Status and the orphan checks" (FR-045, FR-072).

The worktrees are hand-made directories (`.git` files and directories) and the listings are
canned, so no git and no Docker run here.
"""

from pathlib import Path

import pytest

from wtenv.gitutil import WorktreeRecord
from wtenv.orphans import Classification, classify
from wtenv.output import Status, UnverifiableReason
from wtenv.registry import PortBlock, WorktreeEntry


def make_entry(
    path: Path, git_dir: Path, *, state: str = "provisioned", repository: Path | None = None
) -> WorktreeEntry:
    """Return an entry recorded at `path` with git directory `git_dir`."""
    return WorktreeEntry.model_validate(
        {
            "git_dir": str(git_dir),
            "path": str(path),
            "repository": str(repository or git_dir.parent),
            "state": state,
            "block": PortBlock(start=20000, size=10).model_dump(),
        }
    )


def make_worktree(root: Path, name: str) -> tuple[Path, Path]:
    """Create a worktree directory whose `.git` file points to a git directory that exists."""
    git_dir = root / "repo.git" / "worktrees" / name
    git_dir.mkdir(parents=True)
    path = root / name
    path.mkdir()
    (path / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    return path.resolve(), git_dir.resolve()


def listing(*paths: Path) -> list[WorktreeRecord]:
    return [WorktreeRecord(path=str(path)) for path in paths]


def test_no_listing_is_unverifiable_because_the_repository_was_not_found(tmp_path: Path) -> None:
    path, git_dir = make_worktree(tmp_path, "feature")
    entry = make_entry(path, git_dir)

    result = classify(entry, None)

    assert result == Classification(
        Status.UNVERIFIABLE, UnverifiableReason.REPOSITORY_NOT_FOUND, None
    )


def test_a_worktree_that_points_to_its_git_dir_is_provisioned(tmp_path: Path) -> None:
    path, git_dir = make_worktree(tmp_path, "feature")
    entry = make_entry(path, git_dir, state="provisioned")

    result = classify(entry, listing(path))

    assert result == Classification(Status.PROVISIONED, None, None)


def test_a_worktree_that_points_to_its_git_dir_is_incomplete_when_the_entry_is(
    tmp_path: Path,
) -> None:
    path, git_dir = make_worktree(tmp_path, "feature")
    entry = make_entry(path, git_dir, state="incomplete")

    assert classify(entry, listing(path)) == Classification(Status.INCOMPLETE, None, None)


def test_a_main_worktree_whose_dot_git_is_a_directory_is_found_too(tmp_path: Path) -> None:
    path = tmp_path / "main"
    (path / ".git").mkdir(parents=True)
    entry = make_entry(path.resolve(), (path / ".git").resolve())

    assert classify(entry, listing(path)).status is Status.PROVISIONED


def test_a_path_that_is_missing_while_git_still_has_the_git_dir_and_lists_it(
    tmp_path: Path,
) -> None:
    path, git_dir = make_worktree(tmp_path, "feature")
    (path / ".git").unlink()
    path.rmdir()  # deleted by hand: git's own directory is still there
    entry = make_entry(path, git_dir)

    result = classify(entry, listing(path))

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.GIT_STILL_LISTS, None)


def test_a_git_dir_that_is_still_there_and_a_path_that_points_elsewhere_is_moved(
    tmp_path: Path,
) -> None:
    path, git_dir = make_worktree(tmp_path, "feature")
    new_path = tmp_path / "moved"
    path.rename(new_path)  # `git worktree move` rewrites `.git`; here it still names `git_dir`
    entry = make_entry(path, git_dir)

    result = classify(entry, listing(new_path.resolve()))

    assert result == Classification(
        Status.UNVERIFIABLE, UnverifiableReason.MOVED, str(new_path.resolve())
    )


def test_moved_without_a_listed_path_that_points_to_the_git_dir_has_no_current_path(
    tmp_path: Path,
) -> None:
    path, git_dir = make_worktree(tmp_path, "feature")
    (path / ".git").unlink()  # the directory exists, but no longer names the git dir
    other = tmp_path / "unrelated"
    other.mkdir()
    entry = make_entry(path, git_dir)

    result = classify(entry, listing(other))

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.MOVED, None)


def test_a_missing_git_dir_with_something_at_the_path_is_path_exists(tmp_path: Path) -> None:
    path = tmp_path / "feature"
    path.mkdir()
    (path / "notes.txt").write_text("not a worktree\n", encoding="utf-8")
    entry = make_entry(path.resolve(), tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing())

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.PATH_EXISTS, None)


def test_a_missing_git_dir_with_the_path_still_listed_is_git_still_lists(tmp_path: Path) -> None:
    path = (tmp_path / "feature").resolve()  # nothing at the path
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing(path))

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.GIT_STILL_LISTS, None)


def test_a_missing_git_dir_nothing_at_the_path_and_not_listed_is_orphaned(tmp_path: Path) -> None:
    path = (tmp_path / "feature").resolve()
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing(tmp_path / "other"))

    assert result == Classification(Status.ORPHANED, None, None)


# --- a missing parent directory: `parent_missing` (T189; reading R9; data-model.md, step 4.3) -------


def test_a_missing_git_dir_nothing_at_the_path_and_a_missing_parent_is_parent_missing(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "drive" / "feature").resolve()  # `drive` is not mounted
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing(tmp_path / "other"))

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.PARENT_MISSING, None)


def test_the_same_entry_with_its_parent_present_is_orphaned(tmp_path: Path) -> None:
    (tmp_path / "drive").mkdir()
    path = (tmp_path / "drive" / "feature").resolve()
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing(tmp_path / "other"))

    assert result == Classification(Status.ORPHANED, None, None)


def test_a_parent_that_is_a_file_is_a_missing_parent_too(tmp_path: Path) -> None:
    (tmp_path / "drive").write_text("not a directory\n", encoding="utf-8")
    path = tmp_path / "drive" / "feature"
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing())

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.PARENT_MISSING, None)


def test_a_parent_that_is_a_dangling_link_is_a_missing_parent_too(tmp_path: Path) -> None:
    (tmp_path / "drive").symlink_to(tmp_path / "nowhere")
    path = tmp_path / "drive" / "feature"
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing())

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.PARENT_MISSING, None)


def test_a_missing_parent_does_not_change_repository_not_found(tmp_path: Path) -> None:
    path = (tmp_path / "drive" / "feature").resolve()
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    assert classify(entry, None) == Classification(
        Status.UNVERIFIABLE, UnverifiableReason.REPOSITORY_NOT_FOUND, None
    )


def test_a_missing_parent_does_not_change_git_still_lists_for_a_listed_path(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "drive" / "feature").resolve()
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature")

    result = classify(entry, listing(path))

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.GIT_STILL_LISTS, None)


def test_a_missing_parent_does_not_change_git_still_lists_while_the_git_dir_is_there(
    tmp_path: Path,
) -> None:
    git_dir = tmp_path / "repo.git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    path = (tmp_path / "drive" / "feature").resolve()
    entry = make_entry(path, git_dir.resolve())

    result = classify(entry, listing(path))

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.GIT_STILL_LISTS, None)


def test_a_missing_parent_does_not_change_moved(tmp_path: Path) -> None:
    git_dir = tmp_path / "repo.git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    path = (tmp_path / "drive" / "feature").resolve()
    entry = make_entry(path, git_dir.resolve())

    result = classify(entry, listing())

    assert result == Classification(Status.UNVERIFIABLE, UnverifiableReason.MOVED, None)


@pytest.mark.parametrize("state", ["provisioned", "incomplete"])
def test_an_orphan_is_orphaned_whatever_the_entry_state(tmp_path: Path, state: str) -> None:
    path = (tmp_path / "feature").resolve()
    entry = make_entry(path, tmp_path / "gone.git" / "worktrees" / "feature", state=state)

    assert classify(entry, listing()).status is Status.ORPHANED


def test_classify_changes_nothing(tmp_path: Path) -> None:
    path, git_dir = make_worktree(tmp_path, "feature")
    entry = make_entry(path, git_dir)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    snapshot = entry.model_copy(deep=True)

    classify(entry, listing(path))
    classify(entry, None)

    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before
    assert entry == snapshot
