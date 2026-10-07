"""`git_path` and `is_tracked` against real repositories and worktrees."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from wtenv.gitutil import git_path, is_tracked

pytestmark = pytest.mark.integration

MakeRepo = Callable[[str], Path]
AddWorktree = Callable[[Path, str, str], Path]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_a_shared_git_path_is_the_same_from_the_main_and_a_linked_worktree(
    make_repo: MakeRepo, add_worktree: AddWorktree
) -> None:
    repo = make_repo("app")
    linked = add_worktree(repo, "feature", "feature")

    from_main = git_path(repo, "info/exclude")
    from_linked = git_path(linked, "info/exclude")

    assert from_main == repo / ".git" / "info" / "exclude"
    assert from_linked == from_main


def test_a_private_git_path_belongs_to_the_worktree(
    make_repo: MakeRepo, add_worktree: AddWorktree
) -> None:
    repo = make_repo("app")
    linked = add_worktree(repo, "feature", "feature")

    assert git_path(repo, "HEAD") == repo / ".git" / "HEAD"
    assert git_path(linked, "HEAD") == repo / ".git" / "worktrees" / "feature" / "HEAD"


def test_git_path_is_absolute_from_a_subdirectory(make_repo: MakeRepo) -> None:
    repo = make_repo("app")
    (repo / "sub").mkdir()

    assert os.path.isabs(git_path(repo / "sub", "info/exclude"))


def test_a_committed_file_is_tracked(make_repo: MakeRepo) -> None:
    repo = make_repo("app")
    (repo / ".env.local").write_text("PORT=1\n")
    _git(repo, "add", ".env.local")
    _git(repo, "commit", "-m", "track the env file")

    assert is_tracked(repo, ".env.local") is True


def test_a_staged_file_is_tracked(make_repo: MakeRepo) -> None:
    repo = make_repo("app")
    (repo / ".env.local").write_text("PORT=1\n")
    _git(repo, "add", ".env.local")

    assert is_tracked(repo, ".env.local") is True


def test_an_untracked_or_missing_file_is_not_tracked(make_repo: MakeRepo) -> None:
    repo = make_repo("app")
    (repo / ".env.local").write_text("PORT=1\n")

    assert is_tracked(repo, ".env.local") is False
    assert is_tracked(repo, "never-created") is False


def test_an_ignored_file_is_not_tracked(make_repo: MakeRepo) -> None:
    repo = make_repo("app")
    (repo / ".gitignore").write_text(".env.local\n")
    (repo / ".env.local").write_text("PORT=1\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore the env file")

    assert is_tracked(repo, ".env.local") is False


def test_tracking_is_asked_in_the_worktree_it_is_called_for(
    make_repo: MakeRepo, add_worktree: AddWorktree
) -> None:
    repo = make_repo("app")
    (repo / "config.env").write_text("A=1\n")
    _git(repo, "add", "config.env")
    _git(repo, "commit", "-m", "add config")
    linked = add_worktree(repo, "feature", "feature")
    (linked / "scratch.env").write_text("B=2\n")

    assert is_tracked(linked, "config.env") is True
    assert is_tracked(linked, "scratch.env") is False


def test_a_path_is_matched_literally_not_as_a_pattern(make_repo: MakeRepo) -> None:
    repo = make_repo("app")
    (repo / "a.env").write_text("A=1\n")
    _git(repo, "add", "a.env")
    _git(repo, "commit", "-m", "add a.env")

    assert is_tracked(repo, "*.env") is False
