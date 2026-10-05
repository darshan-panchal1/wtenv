"""wtenv's section of the env file (contracts/files.md, "Env file section").

wtenv keeps its variables between two marker lines and treats every other byte of the file as
the developer's (FR-016). The file is handled as bytes, so what lies outside the section is
preserved exactly, whatever its encoding or line endings. Writes go through a temporary file and
an atomic rename of the real path, so a symbolic link stays a link.

`locate_section` and `write_atomic` are also used by `exclude.py`, whose block has the same
markers.
"""

import contextlib
import os
import re
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wtenv.errors import ErrorCode, WtenvError

BEGIN_MARKER = "# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>"
END_MARKER = "# <<< wtenv managed <<<"

_BEGIN = BEGIN_MARKER.encode()
_END = END_MARKER.encode()

_PRIVATE_FILE = 0o600
_BARE_VALUE = re.compile(r"[A-Za-z0-9_./:@%+=,~-]+")
# `NAME=...`, as dotenv files and shells write it, with an optional `export`.
_DEFINITION = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class MarkersDamaged(Exception):
    """The markers are not one begin line followed by one end line (FR-081)."""


@dataclass(frozen=True)
class SectionWrite:
    """What `write_section` did, for the registry record and the `up` result."""

    # `created`: there was no section before; `updated`: its lines changed; `unchanged`: the
    # file was not written.
    action: Literal["created", "updated", "unchanged"]
    created_file: bool  # this call created the file
    added_newline: bool  # this call added a line break before the section (FR-079)


@dataclass(frozen=True)
class SectionRemoval:
    """What `remove_section` did."""

    removed: bool  # there was a section, and it is gone
    deleted_file: bool  # the file was wtenv's, and nothing else was in it


def locate_section(lines: Sequence[bytes]) -> tuple[int, int] | None:
    """Return the indexes of the begin and end marker lines in `lines`, or None without markers.

    Markers are recognised with trailing whitespace or a carriage return. Raises `MarkersDamaged`
    for a begin without an end, an end without a begin, an end before its begin, or more than
    one section; wtenv does not guess where a section ends.
    """
    begins = [index for index, line in enumerate(lines) if line.rstrip() == _BEGIN]
    ends = [index for index, line in enumerate(lines) if line.rstrip() == _END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
        raise MarkersDamaged
    return begins[0], ends[0]


def write_section(path: Path, variables: Sequence[tuple[str, str]]) -> SectionWrite:
    """Write `variables` as wtenv's section of the env file `path`, and say what changed.

    A missing file is created with mode 0600 and holds only the section (FR-019). Without a
    section, it is appended at the end, after a line break when the file lacks one. An existing
    section is rewritten where it is. An existing file keeps its mode. The file is not touched
    when its bytes would not change (FR-017). Raises `env_file_unusable` for damaged markers.
    """
    section = _render(variables)
    content = _read(path)
    if content is None:
        write_atomic(path, section, _PRIVATE_FILE)
        return SectionWrite("created", created_file=True, added_newline=False)

    lines = content.splitlines(keepends=True)
    located = _locate(path, lines)
    if located is None:
        added_newline = bool(content) and not content.endswith(b"\n")
        updated = content + (b"\n" if added_newline else b"") + section
        action: Literal["created", "updated"] = "created"
    else:
        begin, end = located
        added_newline = False
        updated = b"".join(lines[:begin]) + section + b"".join(lines[end + 1 :])
        action = "updated"
    if updated == content:
        return SectionWrite("unchanged", created_file=False, added_newline=False)
    write_atomic(path, updated, stat.S_IMODE(os.stat(path).st_mode))
    return SectionWrite(action, created_file=False, added_newline=added_newline)


def read_section(path: Path) -> list[tuple[str, str]]:
    """Return the `(name, value)` pairs of wtenv's section, in file order, with quotes removed.

    What `write_section` wrote is returned unchanged. A value edited by hand is returned as it
    stands. Raises `env_file_unusable` with reason `missing` (no file), `no_section` (no marker
    line), or `markers_damaged` (reading R1).
    """
    content = _read(path)
    if content is None:
        raise _unusable(path, "missing")
    lines = content.splitlines(keepends=True)
    located = _locate(path, lines)
    if located is None:
        raise _unusable(path, "no_section")
    begin, end = located
    pairs = []
    for line in lines[begin + 1 : end]:
        text = _decode(line).rstrip("\r\n")
        name, separator, value = text.partition("=")
        if not separator or not name.strip() or name.lstrip().startswith("#"):
            continue
        pairs.append((name.strip(), _unquote(value)))
    return pairs


def remove_section(
    path: Path, *, created_file: bool = False, added_newline: bool = False
) -> SectionRemoval:
    """Remove wtenv's section, both markers included, and nothing else (FR-079).

    `added_newline` is the recorded flag of `write_section`: the line break it added is removed
    again when the section is the last thing in the file. A file that wtenv created
    (`created_file`) and that holds nothing else afterwards is deleted (FR-038). A missing file
    or a file without a section is already absent, not an error (FR-042). Damaged markers raise
    `env_file_unusable` and leave the file as it is (FR-081).
    """
    content = _read(path)
    if content is None:
        return SectionRemoval(removed=False, deleted_file=False)
    lines = content.splitlines(keepends=True)
    located = _locate(path, lines)
    if located is None:
        return SectionRemoval(removed=False, deleted_file=False)
    begin, end = located
    before = b"".join(lines[:begin])
    after = b"".join(lines[end + 1 :])
    if added_newline and not after and before.endswith(b"\n"):
        before = before.removesuffix(b"\n")
    remaining = before + after
    if created_file and not remaining.strip():
        os.unlink(path)
        return SectionRemoval(removed=True, deleted_file=True)
    write_atomic(path, remaining, stat.S_IMODE(os.stat(path).st_mode))
    return SectionRemoval(removed=True, deleted_file=False)


def find_duplicates(path: Path, names: Sequence[str]) -> list[str]:
    """Return those of `names` that the file also defines outside wtenv's section (FR-080).

    The lines are not changed. A missing file has none. Names come back in the order of `names`,
    once each. Damaged markers raise `env_file_unusable`.
    """
    content = _read(path)
    if content is None:
        return []
    lines = content.splitlines(keepends=True)
    located = _locate(path, lines)
    if located is not None:
        lines = lines[: located[0]] + lines[located[1] + 1 :]
    defined = set()
    for line in lines:
        match = _DEFINITION.match(_decode(line))
        if match is not None:
            defined.add(match.group(1))
    return [name for name in names if name in defined]


def _render(variables: Sequence[tuple[str, str]]) -> bytes:
    """Return the section as it is written: markers and one `NAME=value` line per variable."""
    out = [BEGIN_MARKER]
    for name, value in variables:
        if "'" in value or "\n" in value or "\r" in value:
            raise ValueError(f"the value of {name} holds a single quote or a line break")
        quoted = value if _BARE_VALUE.fullmatch(value) else f"'{value}'"
        out.append(f"{name}={quoted}")
    out.append(END_MARKER)
    return ("\n".join(out) + "\n").encode()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _decode(line: bytes) -> str:
    return line.decode("utf-8", errors="surrogateescape")


def _locate(path: Path, lines: Sequence[bytes]) -> tuple[int, int] | None:
    """Like `locate_section`, but damaged markers become `env_file_unusable`."""
    try:
        return locate_section(lines)
    except MarkersDamaged:
        raise _unusable(path, "markers_damaged") from None


def _read(path: Path) -> bytes | None:
    """Return the file's bytes, or None when it does not exist."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise _unusable(path, "is_directory") from None
    except OSError:
        raise _unusable(path, "not_writable") from None


def write_atomic(path: Path, data: bytes, mode: int) -> None:
    """Replace the real file behind `path` with `data` through a temporary file beside it.

    The file gets `mode`. The temporary file is removed again when anything fails.
    """
    real = Path(os.path.realpath(path))
    descriptor, temporary = tempfile.mkstemp(
        dir=real.parent, prefix=f".{real.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, real)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


# Per reason: what is wrong, and what to do about it.
_UNUSABLE = {
    "markers_damaged": (
        "its wtenv marker lines are damaged",
        (
            "Repair the two wtenv marker lines by hand, or delete the lines between them, then "
            "run the command again. wtenv does not guess where its section ends."
        ),
    ),
    "is_directory": ("it is a directory", "Point env_file at a file."),
    "not_writable": (
        "it cannot be read or written",
        "Make the file readable and writable, then run the command again.",
    ),
    "missing": ("it does not exist", "Run `wtenv up` to write the env file."),
    "no_section": ("it has no wtenv section", "Run `wtenv up` to write wtenv's section."),
}


def _unusable(path: Path, reason: str) -> WtenvError:
    """Build the `env_file_unusable` error for `path`."""
    problem, hint = _UNUSABLE[reason]
    return WtenvError(
        ErrorCode.ENV_FILE_UNUSABLE,
        f"cannot use the env file {path}: {problem}",
        hint=hint,
        details={"path": str(path), "reason": reason},
    )
