"""Shared fixtures: an isolated state directory, a fixed git setup, and real worktrees."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `XDG_STATE_HOME` at a temporary directory.

    The variable is inherited by every process a test starts, so no test touches the
    developer's own registry (research.md section 7).
    """
    path = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(path))
    return path


@pytest.fixture(autouse=True)
def git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give git a fixed identity and hide the developer's own git configuration.

    Without this, a setting such as `core.hooksPath` could change test results.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "wtenv tests")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tests@wtenv.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "wtenv tests")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tests@wtenv.invalid")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


def _git(cwd: Path, *args: str) -> None:
    """Run git in `cwd`; raise with git's own message when it fails."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[str], Path]:
    """Return a function that creates a repository `name` with one empty commit."""

    def make(name: str) -> Path:
        """Create `<tmp>/repos/<name>` on branch `main`; return its resolved path."""
        path = tmp_path / "repos" / name
        path.mkdir(parents=True)
        _git(path, "init", "-b", "main")
        _git(path, "commit", "--allow-empty", "-m", "initial commit")
        return path.resolve()

    return make


@pytest.fixture
def add_worktree() -> Callable[[Path, str, str], Path]:
    """Return a function that runs the real `git worktree add` for a new branch."""

    def add(repo: Path, name: str, branch: str) -> Path:
        """Add worktree `name` beside `repo`, on the new branch `branch`; return its path."""
        path = repo.parent / name
        _git(repo, "worktree", "add", "-b", branch, str(path))
        return path.resolve()

    return add


@pytest.fixture
def run_wtenv() -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return a function that runs `python -m wtenv` and returns the finished process.

    Standard input is closed, so a command that prompts fails the test (FR-004). The result has
    `returncode`, `stdout`, and `stderr`.
    """

    def run(
        args: list[str], cwd: Path, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run wtenv with `args` in `cwd`; `env` adds to or overrides the inherited environment."""
        return subprocess.run(
            [sys.executable, "-m", "wtenv", *args],
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )

    return run


@pytest.fixture(scope="session")
def docker() -> None:
    """Skip the requesting test unless `docker info` succeeds (checked once per session)."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker is not available")
    if result.returncode != 0:
        pytest.skip("Docker is not available")
