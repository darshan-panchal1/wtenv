"""Worktree identity, and the names derived from it (data-model.md; contracts/files.md, Names).

The identity of a worktree is its git directory (FR-006): the same from any subdirectory and
through any symbolic link, and unchanged when a linked worktree is moved. `slug` and `short_id`
are here, and not with the databases or compose, because both use them to name things.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import run_git

# One call gives all three lines, in this order (research.md section 6).
_IDENTITY_ARGS = [
    "rev-parse",
    "--path-format=absolute",
    "--absolute-git-dir",
    "--show-toplevel",
    "--git-common-dir",
]

# `git rev-parse` exits with 128 when the directory is not inside a worktree.
_GIT_FATAL = 128

SLUG_LENGTH = 40
_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class WorktreeIdentity:
    """Where a worktree is and what it belongs to; every path went through `os.path.realpath`."""

    git_dir: str  # the identity, and the registry key
    path: str  # the worktree's location now
    repository: str  # the common git directory, the same for all worktrees of a repository


def parse_identity(text: str) -> WorktreeIdentity:
    """Build the identity from the three lines `git rev-parse` prints for `_IDENTITY_ARGS`."""
    lines = text.removesuffix("\n").split("\n")
    if len(lines) != 3:
        raise ValueError(f"expected three lines from git rev-parse, got {len(lines)}")
    git_dir, path, repository = (os.path.realpath(line) for line in lines)
    return WorktreeIdentity(git_dir=git_dir, path=path, repository=repository)


def current_worktree(cwd: str | Path | None = None) -> WorktreeIdentity:
    """Return the identity of the worktree that contains `cwd` (default: the current directory).

    Raises `not_in_worktree` when `cwd` is not inside a worktree, which includes the inside of
    a `.git` directory (FR-002).
    """
    directory = os.getcwd() if cwd is None else str(cwd)
    result = run_git(_IDENTITY_ARGS, cwd=directory)
    if result.returncode == _GIT_FATAL:
        raise WtenvError(
            ErrorCode.NOT_IN_WORKTREE,
            f"not inside a git worktree: {directory}",
            hint="Run wtenv from a directory inside a git worktree.",
            details={"cwd": directory},
        )
    result.check_returncode()
    return parse_identity(result.stdout)


def points_to(path: str | Path) -> str | None:
    """Return the git directory that `path/.git` names, or None when it names nothing.

    A `.git` directory is its own git directory. A `.git` file holds `gitdir: <path>`, which is
    resolved against `path`. A missing or unreadable `.git`, or a file that is not a `gitdir`
    line, names nothing. The result went through `os.path.realpath`, like `git_dir` of an
    identity, so the two can be compared.
    """
    dot_git = os.path.join(path, ".git")
    if os.path.isdir(dot_git):
        return os.path.realpath(dot_git)
    try:
        with open(dot_git, encoding="utf-8") as file:
            first_line = file.readline()
    except (OSError, UnicodeDecodeError):
        return None
    prefix = "gitdir: "
    if not first_line.startswith(prefix):
        return None
    return os.path.realpath(os.path.join(path, first_line[len(prefix) :].rstrip("\r\n")))


def short_id(git_dir: str, length: int) -> str:
    """Return the first `length` hexadecimal digits of the SHA-256 of the git directory path.

    `length` is 8 for database and compose names (`<id8>`) and 16 for worktree lock files
    (`<id16>`).
    """
    return hashlib.sha256(os.fsencode(git_dir)).hexdigest()[:length]


def slug(name: str, separator: Literal["_", "-"]) -> str:
    """Return a worktree directory's name in the form used inside database and compose names.

    Lowercased, with every run of characters outside `a-z` and `0-9` replaced by one
    `separator` (`_` for databases, `-` for compose), trimmed of separators at both ends, cut to
    40 characters, and trimmed again, because the cut can land just after a separator. An empty
    result becomes `wt`.
    """
    text = _NOT_ALPHANUMERIC.sub(separator, name.lower()).strip(separator)
    return text[:SLUG_LENGTH].rstrip(separator) or "wt"
