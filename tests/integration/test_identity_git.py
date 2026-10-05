"""Worktree identity on real git worktrees (FR-002, FR-006; research.md section 6)."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import GIT_LOCAL_ENV_VARS
from wtenv.identity import current_worktree

pytestmark = pytest.mark.integration

MakeRepo = Callable[[str], Path]
AddWorktree = Callable[[Path, str, str], Path]


def test_a_linked_worktree_has_the_same_identity_from_every_place_in_it(
    make_repo: MakeRepo, add_worktree: AddWorktree, tmp_path: Path
) -> None:
    repo = make_repo("app")
    linked = add_worktree(repo, "feature", "feature")
    subdirectory = linked / "src" / "deep"
    subdirectory.mkdir(parents=True)
    link = tmp_path / "link-to-feature"
    link.symlink_to(linked)

    from_root = current_worktree(linked)

    assert from_root.git_dir == str(repo / ".git" / "worktrees" / "feature")
    assert from_root.path == str(linked)
    assert from_root.repository == str(repo / ".git")
    assert current_worktree(subdirectory) == from_root
    assert current_worktree(link) == from_root


def test_the_main_worktree_has_its_own_identity(make_repo: MakeRepo) -> None:
    repo = make_repo("app")
    (repo / "sub").mkdir()

    identity = current_worktree(repo / "sub")

    assert identity.git_dir == str(repo / ".git")
    assert identity.path == str(repo)
    assert identity.repository == str(repo / ".git")


def test_main_and_linked_worktrees_differ_and_share_the_repository(
    make_repo: MakeRepo, add_worktree: AddWorktree
) -> None:
    repo = make_repo("app")
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")

    identities = [current_worktree(path) for path in (repo, first, second)]

    assert len({identity.git_dir for identity in identities}) == 3
    assert len({identity.path for identity in identities}) == 3
    assert len({identity.repository for identity in identities}) == 1


def test_git_worktree_move_keeps_the_git_dir_and_changes_the_path(
    make_repo: MakeRepo, add_worktree: AddWorktree
) -> None:
    repo = make_repo("app")
    linked = add_worktree(repo, "feature", "feature")
    before = current_worktree(linked)
    moved = repo.parent / "feature-moved"

    subprocess.run(
        ["git", "worktree", "move", str(linked), str(moved)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    after = current_worktree(moved)

    assert after.git_dir == before.git_dir
    assert after.repository == before.repository
    assert after.path == str(moved.resolve())
    assert after.path != before.path


def test_a_plain_mv_keeps_the_git_dir_and_changes_the_path(
    make_repo: MakeRepo, add_worktree: AddWorktree
) -> None:
    repo = make_repo("app")
    linked = add_worktree(repo, "feature", "feature")
    before = current_worktree(linked)
    moved = repo.parent / "feature-mv"

    shutil.move(linked, moved)
    after = current_worktree(moved)

    assert after.git_dir == before.git_dir
    assert after.path == str(moved.resolve())


def test_outside_a_repository_is_not_in_worktree_with_the_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(WtenvError) as raised:
        current_worktree(outside)

    assert raised.value.code is ErrorCode.NOT_IN_WORKTREE
    assert raised.value.details == {"cwd": str(outside)}


def test_inside_dot_git_is_not_in_worktree_with_the_directory(make_repo: MakeRepo) -> None:
    repo = make_repo("app")

    with pytest.raises(WtenvError) as raised:
        current_worktree(repo / ".git")

    assert raised.value.code is ErrorCode.NOT_IN_WORKTREE
    assert raised.value.details == {"cwd": str(repo / ".git")}


def test_the_current_directory_is_used_when_none_is_given(
    make_repo: MakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo("app")
    monkeypatch.chdir(repo)

    assert current_worktree().path == str(repo)


def test_an_inherited_git_dir_does_not_change_the_result(
    make_repo: MakeRepo, add_worktree: AddWorktree, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo("app")
    linked = add_worktree(repo, "feature", "feature")
    other = make_repo("other")
    expected = current_worktree(linked)

    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    assert current_worktree(linked) == expected


def test_every_variable_git_calls_repository_local_is_scrubbed() -> None:
    printed = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], check=True, capture_output=True, text=True
    ).stdout.split()

    assert [name for name in printed if name not in GIT_LOCAL_ENV_VARS] == []
