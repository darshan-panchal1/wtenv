"""Smoke test for the integration gate: the fixtures create real git worktrees."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_make_repo_and_add_worktree_give_a_main_and_a_linked_worktree(
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
) -> None:
    repo = make_repo("app")
    linked = add_worktree(repo, "feature", "feature")

    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    listed = [
        line.removeprefix("worktree ")
        for line in listing.splitlines()
        if line.startswith("worktree ")
    ]

    assert listed == [str(repo), str(linked)]
    assert (linked / ".git").is_file()
