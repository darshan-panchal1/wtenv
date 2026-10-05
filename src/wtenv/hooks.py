"""The git `post-checkout` hook: the block's text, putting it into a hook and taking it out
(contracts/files.md, "Git hook block"; research.md section 1; FR-051 to FR-053), and the commands
`wtenv hook install` and `wtenv hook uninstall` (cli.md).

The hook file is handled as bytes, so every byte outside the block is kept as it was (FR-053).
`insert_block`, `remove_block`, and `only_shebang_left` take and return file content and touch no
file; `install` and `uninstall` read and write `<git-common-dir>/hooks/post-checkout`.
"""

import os
import re
import stat
from pathlib import Path
from typing import Literal

from wtenv import registry
from wtenv.envfile import write_atomic
from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import git_path
from wtenv.identity import WorktreeIdentity, current_worktree
from wtenv.locks import registry_lock
from wtenv.output import HookInstallResult, HookUninstallResult, Item, ItemKind
from wtenv.registry import HookRecord

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

HOOK_NAME = "post-checkout"
_NEW_FILE_MODE = 0o755

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


def install(cwd: str | Path | None = None) -> HookInstallResult:
    """Install the block into the repository's `post-checkout` hook (FR-051 to FR-053).

    Acts on the repository of the worktree that contains `cwd` (default: the current directory).
    The hook file is `<git-common-dir>/hooks/post-checkout`: a new file gets mode 0755 and a
    registry record with `created_file` true, so `uninstall` may delete it; an existing hook keeps
    its mode and is recorded with `created_file` false. Running it again is `unchanged` or
    `updated`. Raises `not_in_worktree`, `registry_busy`, `registry_unreadable`, and
    `unsupported` with reason `hooks_path_redirected` (`core.hooksPath` names another directory),
    `hook_not_shell`, or `markers_damaged`. The registry is read first, and nothing is written
    when anything fails.
    """
    identity = current_worktree(cwd)
    hooks_dir = Path(identity.repository) / "hooks"
    hook_file = hooks_dir / HOOK_NAME
    _require_default_hooks_directory(identity, hooks_dir, hook_file)
    with registry_lock():
        reg = registry.load()
        try:
            content: bytes | None = hook_file.read_bytes()
        except FileNotFoundError:
            content = None
        updated, action = insert_block(content, hook_file)
        if action != "unchanged":
            hooks_dir.mkdir(parents=True, exist_ok=True)
            mode = _NEW_FILE_MODE if content is None else stat.S_IMODE(os.stat(hook_file).st_mode)
            write_atomic(hook_file, updated, mode)
        old = reg.hooks.get(identity.repository)
        created = content is None or (
            old is not None and old.hook_file == str(hook_file) and old.created_file
        )
        record = HookRecord(hook_file=str(hook_file), created_file=created)
        if old != record:
            reg.hooks[identity.repository] = record
            registry.save(reg)
    return HookInstallResult(ok=True, hook_file=str(hook_file), action=action)


def uninstall(cwd: str | Path | None = None, *, dry_run: bool = False) -> HookUninstallResult:
    """Remove the block that `install` added, and the file too when `install` created it.

    Acts on the repository of the worktree that contains `cwd`. It looks in
    `<git-common-dir>/hooks/post-checkout` whatever `core.hooksPath` says now, so that a block can
    still be removed after the setting moved. The file is deleted only when the registry records
    that wtenv created it and nothing but the shebang line is left (cli.md). No block is success
    with `action` `absent`. With `dry_run` nothing is written, and `would_remove` holds what a
    real run would remove (FR-040); `action` is then left unset unless there is no block.
    Raises `not_in_worktree`, `registry_busy`, `registry_unreadable`, and `unsupported` (reason
    `markers_damaged`).
    """
    identity = current_worktree(cwd)
    hook_file = Path(identity.repository) / "hooks" / HOOK_NAME
    with registry_lock():
        reg = registry.load()
        record = reg.hooks.get(identity.repository)
        try:
            content = hook_file.read_bytes()
        except FileNotFoundError:
            content = None
        remaining = None if content is None else remove_block(content, hook_file)
        if remaining is None:
            # No block: success, and a record of a hook that is gone is stale.
            if record is not None and not dry_run:
                del reg.hooks[identity.repository]
                registry.save(reg)
            return HookUninstallResult(
                ok=True, dry_run=dry_run, hook_file=str(hook_file), action="absent"
            )
        items = [Item(kind=ItemKind.HOOK_BLOCK, name=str(hook_file))]
        delete = (
            record is not None
            and record.created_file
            and record.hook_file == str(hook_file)
            and only_shebang_left(remaining)
        )
        if delete:
            items.append(Item(kind=ItemKind.HOOK_FILE, name=str(hook_file)))
        if dry_run:
            return HookUninstallResult(
                ok=True, dry_run=True, hook_file=str(hook_file), would_remove=items
            )
        if delete:
            os.unlink(hook_file)
        else:
            write_atomic(hook_file, remaining, stat.S_IMODE(os.stat(hook_file).st_mode))
        reg.hooks.pop(identity.repository, None)
        registry.save(reg)
    return HookUninstallResult(ok=True, hook_file=str(hook_file), action="removed", removed=items)


def _require_default_hooks_directory(
    identity: WorktreeIdentity, hooks_dir: Path, hook_file: Path
) -> None:
    """Raise `unsupported` (`hooks_path_redirected`) unless git runs hooks from `hooks_dir`.

    Hook directories that `core.hooksPath` redirects are often tracked directories (husky,
    lefthook) or shared by many repositories, so wtenv never writes there (research.md section 1).
    """
    active = git_path(identity.path, "hooks")
    if os.path.realpath(active) == os.path.realpath(hooks_dir):
        return
    raise WtenvError(
        ErrorCode.UNSUPPORTED,
        f"core.hooksPath makes git run hooks from {active}, not from {hooks_dir}",
        hint=(
            "wtenv installs only into the default hooks directory. To provision new worktrees "
            f"automatically, add this block to your hook manager's post-checkout hook:\n{BLOCK}"
        ),
        details={"reason": "hooks_path_redirected", "file": str(hook_file)},
    )


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
