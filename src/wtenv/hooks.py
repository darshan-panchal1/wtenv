"""The git `post-checkout` hook block: its text, and putting it into a hook and taking it out
(contracts/files.md, "Git hook block"; research.md section 1; FR-051 to FR-053).

The hook file is handled as bytes, so every byte outside the block is kept as it was (FR-053).
The functions here take and return file content; they touch no file.
"""

import re
from pathlib import Path
from typing import Literal

from wtenv.errors import ErrorCode, WtenvError

BEGIN_MARKER = "# >>> wtenv managed (written by `wtenv hook install`; do not edit) >>>"
END_MARKER = "# <<< wtenv managed <<<"

# The text of files.md, byte for byte. The `wtenv up` line ends in `|| true`, so the block never
# changes the hook's exit status, and git never reports a failed `git worktree add` because
# `up` failed (FR-052). The output of `wtenv up` goes to standard error.
BLOCK = f"""{BEGIN_MARKER}
if [ "$3" = "1" ] && [ -n "$1" ] && [ -z "$(printf '%s' "$1" | tr -d 0)" ]; then
  if [ "$(git rev-parse --git-dir 2>/dev/null)" != "$(git rev-parse --git-common-dir 2>/dev/null)" ]; then
    if command -v wtenv >/dev/null 2>&1; then
      wtenv up 1>&2 || true
    else
      echo "wtenv: not found on PATH; this worktree was not provisioned" 1>&2
    fi
  fi
fi
{END_MARKER}
"""

_BLOCK = BLOCK.encode()
_BEGIN = BEGIN_MARKER.encode()
_END = END_MARKER.encode()

# Reading R4: a shebang that names one of these counts as a POSIX shell script.
_SHELLS = frozenset({"sh", "bash", "dash", "ksh"})
_SHEBANG = re.compile(rb"#!\s*(\S+)(?:\s+(.*))?")


def insert_block(
    content: bytes | None, hook_file: Path
) -> tuple[bytes, Literal["installed", "updated", "unchanged"]]:
    """Return the hook content with the block in it, and what that did.

    `content` is the existing hook file, or None when there is none; a new file is `#!/bin/sh`
    followed by the block. In an existing hook the block goes directly after the shebang line,
    so that a hook that ends with `exit` or `exec` cannot skip it. A block that is already there
    is rewritten where it is: `unchanged` when it is the current one, `updated` when not. Every
    other byte is kept. `hook_file` is named in errors only.

    Raises `unsupported`: reason `hook_not_shell` when the shebang does not name a POSIX shell
    (or there is none), reason `markers_damaged` when the marker lines are damaged. The hint of
    both holds the block, for adding by hand.
    """
    if content is None:
        return b"#!/bin/sh\n" + _BLOCK, "installed"
    lines = content.splitlines(keepends=True)
    if not lines or not _names_a_shell(lines[0]):
        raise _unsupported(
            hook_file,
            "hook_not_shell",
            f"cannot install into {hook_file}: it is not a POSIX shell script",
        )
    located = _locate(hook_file, lines)
    if located is None:
        shebang = lines[0] if lines[0].endswith(b"\n") else lines[0] + b"\n"
        return shebang + _BLOCK + b"".join(lines[1:]), "installed"
    begin, end = located
    updated = b"".join(lines[:begin]) + _BLOCK + b"".join(lines[end + 1 :])
    return updated, "unchanged" if updated == content else "updated"


def remove_block(content: bytes, hook_file: Path) -> bytes | None:
    """Return the hook content without the block, or None when there is no block.

    Exactly the block's lines, the marker lines included, are taken out; every other byte stays
    (FR-053). A hook that is no longer a shell script can still have its block removed. Raises
    `unsupported` (reason `markers_damaged`) when the marker lines are damaged.
    """
    lines = content.splitlines(keepends=True)
    located = _locate(hook_file, lines)
    if located is None:
        return None
    begin, end = located
    return b"".join(lines[:begin]) + b"".join(lines[end + 1 :])


def only_shebang_left(content: bytes) -> bool:
    """Return whether `content` is a shebang line and nothing else but blank lines.

    That is what is left of a hook file that `hook install` created, once the block is out; the
    file is then deleted (cli.md, `wtenv hook uninstall`).
    """
    lines = [line for line in content.splitlines() if line.strip()]
    return len(lines) == 1 and lines[0].startswith(b"#!")


def _names_a_shell(line: bytes) -> bool:
    """Return whether the shebang `line` names `sh`, `bash`, `dash`, or `ksh`.

    `#!/usr/bin/env bash` names bash through `env`; options of the interpreter or of `env`
    (words starting with `-`) are skipped.
    """
    match = _SHEBANG.match(line.rstrip())
    if match is None:
        return False
    words = [match.group(1).decode(errors="replace")]
    if match.group(2):
        words += match.group(2).decode(errors="replace").split()
    name = Path(words[0]).name
    if name == "env":
        name = next((Path(word).name for word in words[1:] if not word.startswith("-")), "")
    return name in _SHELLS


def _locate(hook_file: Path, lines: list[bytes]) -> tuple[int, int] | None:
    """Return the indexes of the begin and end marker lines, None without markers.

    Markers are recognised with trailing whitespace or a carriage return. A begin with no end,
    an end with no begin, an end before its begin, or two blocks raise `unsupported` (reason
    `markers_damaged`): wtenv does not guess where its block ends.
    """
    begins = [index for index, line in enumerate(lines) if line.rstrip() == _BEGIN]
    ends = [index for index, line in enumerate(lines) if line.rstrip() == _END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
        raise _unsupported(
            hook_file,
            "markers_damaged",
            f"cannot update {hook_file}: its wtenv marker lines are damaged",
        )
    return begins[0], ends[0]


def _unsupported(hook_file: Path, reason: str, message: str) -> WtenvError:
    """Build the `unsupported` error; its hint holds the block."""
    if reason == "markers_damaged":
        advice = (
            "Repair the two wtenv marker lines by hand, or delete the lines between them, then "
            "run the command again. wtenv does not guess where its block ends. The block is:"
        )
    else:
        advice = (
            "To provision new worktrees automatically, add this block to your post-checkout "
            "hook, or to your hook manager's:"
        )
    return WtenvError(
        ErrorCode.UNSUPPORTED,
        message,
        hint=f"{advice}\n{BLOCK}",
        details={"reason": reason, "file": str(hook_file)},
    )
