"""Run git with a scrubbed environment, and read what git reports about worktrees.

Every git call in wtenv goes through `run_git`. Git needs 2.31 or later (`--path-format`, and
`prunable` in `git worktree list --porcelain`; research.md section 6).
"""

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from wtenv.errors import ErrorCode, WtenvError

MIN_GIT_VERSION = (2, 31)

# The names `git rev-parse --local-env-vars` prints. They tell git which repository to use, so
# an inherited one (a git hook sets several) must not reach the git processes wtenv starts.
# A constant, so that no extra git call is made to find them (NFR-001).
GIT_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)


@dataclass(frozen=True)
class WorktreeRecord:
    """One worktree in the output of `git worktree list --porcelain`."""

    path: str
    bare: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False


def git_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return `environ` (default: the current environment) without the repository-local variables."""
    source = os.environ if environ is None else environ
    return {name: value for name, value in source.items() if name not in GIT_LOCAL_ENV_VARS}


def run_git(
    args: Sequence[str], cwd: str | Path | None = None, *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run `git` with `args` in `cwd` and return the finished process.

    Standard input is closed and the environment is scrubbed (`git_environment`). A failing git
    is returned as it is, for the caller to read, unless it is older than 2.31. Then the
    failure is reported as `dependency_unavailable` with reason `too_old`; the version is read
    only after a call fails, so the normal path makes one git call. With `check`, any other
    failure raises `subprocess.CalledProcessError`.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=git_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
    except FileNotFoundError:
        if cwd is not None and not Path(cwd).is_dir():
            raise
        raise WtenvError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "git was not found on PATH",
            hint=f"Install git {_version_text(MIN_GIT_VERSION)} or later.",
            details={"dependency": "git", "reason": "not_installed"},
        ) from None
    if result.returncode != 0:
        _raise_if_too_old()
        if check:
            result.check_returncode()
    return result


def _version_text(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


def _raise_if_too_old() -> None:
    """Raise `dependency_unavailable` (`too_old`) when `git --version` is below the minimum."""
    try:
        output = subprocess.run(
            ["git", "--version"],
            env=git_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return
    match = re.search(r"git version ((\d+)\.(\d+)\S*)", output)
    if match is None:
        return
    found, major, minor = match.group(1), int(match.group(2)), int(match.group(3))
    if (major, minor) >= MIN_GIT_VERSION:
        return
    required = _version_text(MIN_GIT_VERSION)
    raise WtenvError(
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"git {found} is too old; wtenv needs git {required} or later",
        hint=f"Upgrade git to {required} or later.",
        details={
            "dependency": "git",
            "reason": "too_old",
            "required": required,
            "found": found,
        },
    )


def parse_worktree_list(text: str) -> list[WorktreeRecord]:
    """Parse the output of `git worktree list --porcelain` into one record per worktree."""
    records = []
    for block in text.split("\n\n"):
        lines = block.strip("\n").splitlines()
        if not lines or not lines[0].startswith("worktree "):
            continue
        attributes = {line.split(" ", 1)[0] for line in lines[1:]}
        records.append(
            WorktreeRecord(
                path=lines[0].removeprefix("worktree "),
                bare="bare" in attributes,
                detached="detached" in attributes,
                locked="locked" in attributes,
                prunable="prunable" in attributes,
            )
        )
    return records


def git_listing(repository: str) -> list[WorktreeRecord] | None:
    """Return the worktrees git lists for `repository` (its common git directory), or None.

    None when git cannot answer: the repository is gone, or is not a repository. One call per
    repository, and a command that changes nothing (FR-075).
    """
    result = run_git([f"--git-dir={repository}", "worktree", "list", "--porcelain"])
    if result.returncode != 0:
        return None
    return parse_worktree_list(result.stdout)


def git_path(worktree: str | Path, name: str) -> Path:
    """Return the absolute path git uses for `name` (such as `info/exclude`) in `worktree`."""
    result = run_git(
        ["rev-parse", "--path-format=absolute", "--git-path", name], cwd=worktree, check=True
    )
    return Path(result.stdout.rstrip("\n"))


def is_tracked(worktree: str | Path, path: str) -> bool:
    """Return whether git tracks `path`, which is relative to the root of `worktree`.

    The path is matched literally, so a name such as `*.env` is not read as a pattern.
    """
    result = run_git(
        ["--literal-pathspecs", "ls-files", "--cached", "--", path], cwd=worktree, check=True
    )
    return bool(result.stdout.strip())
