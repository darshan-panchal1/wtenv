"""The wtenv block in the repository's `.git/info/exclude` (contracts/files.md).

The block keeps the files wtenv generates out of `git status` without touching `.gitignore` or
any tracked file (FR-018). It has the same two marker lines as the env file section and holds the
sorted union of the generated paths of every registered worktree, each anchored with a leading
`/`. `up` only adds lines; the block is removed with the last registered worktree (FR-085).

Callers hold the registry lock while they read and write the file, so that two `up` runs in
different worktrees cannot lose each other's lines.
"""

import os
import stat
from collections.abc import Iterable
from pathlib import Path

from wtenv.envfile import (
    BEGIN_MARKER,
    END_MARKER,
    MarkersDamaged,
    locate_section,
    unusable,
    write_atomic,
)
from wtenv.errors import ErrorCode, WtenvError
from wtenv.identity import symlinked_part

_NEW_FILE_MODE = 0o644  # what git gives the files it creates, before the umask


def refuse_link(path: Path) -> None:
    """Raise `env_file_unusable` (reason `symlink`) when the exclude file `path`, or the `info`
    directory above it, is a symbolic link (FR-086, L6). `details.path` is the link.

    `path` is `<git directory>/info/exclude`. The git directory itself is not looked at: a
    developer may keep `.git` elsewhere and link to it, and that is the layout git reports.
    """
    repository = os.path.realpath(path.parent.parent)
    link = symlinked_part(repository, f"{path.parent.name}/{path.name}")
    if link is not None:
        raise unusable(link, "symlink")


def add_patterns(path: Path, patterns: Iterable[str]) -> bool:
    """Add `patterns` to the wtenv block of the exclude file `path`; return whether it changed.

    The block holds the sorted union of what it already holds and `patterns`, so no line is ever
    removed. Without a block, one is appended at the end, after a line break when the file lacks
    one. A missing file, or a missing `info` directory, is created. Every other line is kept, and
    nothing is written when the file would not change. Raises `unsupported` (reason
    `markers_damaged`) when the markers are damaged, and `env_file_unusable` (reason `symlink`)
    when the file or `info/` is a link (`refuse_link`).
    """
    wanted = set(patterns)
    for pattern in wanted:
        if not pattern.startswith("/"):
            raise ValueError(f"an exclude pattern must start with '/', got {pattern!r}")
    refuse_link(path)
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = None
    lines = [] if content is None else content.splitlines(keepends=True)
    located = _locate(path, lines)
    if located is None:
        if not wanted:
            return False
        before = content or b""
        if before and not before.endswith(b"\n"):
            before += b"\n"
        after = b""
    else:
        begin, end = located
        wanted |= {
            text for line in lines[begin + 1 : end] if (text := _text(line).rstrip("\r\n").strip())
        }
        before, after = b"".join(lines[:begin]), b"".join(lines[end + 1 :])
    updated = before + _render(wanted) + after
    if updated == content:
        return False
    if content is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = _NEW_FILE_MODE
    else:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    write_atomic(path, updated, mode)
    return True


def remove_block(path: Path, *, dry_run: bool = False) -> bool:
    """Remove the wtenv block, markers included, and nothing else; return whether one was there.

    A missing file or a file without a block is not an error (FR-085). Raises `unsupported`
    (reason `markers_damaged`) when the markers are damaged; the file is left as it is. With
    `dry_run` nothing is written, and the answer is the one a real removal would give (FR-040).
    Raises `env_file_unusable` (reason `symlink`) when the file or `info/` is a link (`refuse_link`).
    """
    refuse_link(path)
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return False
    lines = content.splitlines(keepends=True)
    located = _locate(path, lines)
    if located is None:
        return False
    begin, end = located
    if not dry_run:
        write_atomic(
            path, b"".join(lines[:begin] + lines[end + 1 :]), stat.S_IMODE(os.stat(path).st_mode)
        )
    return True


def _render(patterns: Iterable[str]) -> bytes:
    """Return the block holding `patterns`, sorted."""
    return ("\n".join([BEGIN_MARKER, *sorted(patterns), END_MARKER]) + "\n").encode()


def _text(line: bytes) -> str:
    return line.decode("utf-8", errors="surrogateescape")


def _locate(path: Path, lines: list[bytes]) -> tuple[int, int] | None:
    """Return the marker line indexes, or raise `unsupported` when the markers are damaged."""
    try:
        return locate_section(lines)
    except MarkersDamaged:
        raise WtenvError(
            ErrorCode.UNSUPPORTED,
            f"cannot update {path}: its wtenv marker lines are damaged",
            hint=(
                "Repair the two wtenv marker lines by hand, or delete the lines between them, "
                "then run the command again. wtenv does not guess where its block ends."
            ),
            details={"reason": "markers_damaged", "file": str(path)},
        ) from None
