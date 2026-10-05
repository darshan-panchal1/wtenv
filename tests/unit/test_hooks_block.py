"""The git hook block: its text, insertion, and removal (files.md, Git hook block; T116).

The tests that run the block in a shell use only directories that are not inside a repository,
because the unit tests make no git worktrees. The block inside real worktrees is tested in
`tests/integration/test_us5_hook_exec.py`.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from wtenv.errors import ErrorCode, WtenvError
from wtenv.hooks import BLOCK, insert_block, only_shebang_left, remove_block

HOOK = Path("/repo/.git/hooks/post-checkout")
FILES_MD = (
    Path(__file__).parents[2]
    / "specs"
    / "001-worktree-runtime-isolation"
    / "contracts"
    / "files.md"
)
BEGIN = "# >>> wtenv managed (written by `wtenv hook install`; do not edit) >>>"
END = "# <<< wtenv managed <<<"
ZEROS = "0" * 40


def inserted(content: bytes | None) -> tuple[bytes, str]:
    return insert_block(content, HOOK)


def unsupported(content: bytes, *, remove: bool = False) -> WtenvError:
    """Return the error that inserting into (or removing from) `content` raises."""
    with pytest.raises(WtenvError) as caught:
        remove_block(content, HOOK) if remove else insert_block(content, HOOK)
    assert caught.value.code is ErrorCode.UNSUPPORTED
    return caught.value


# --- the block's text -----------------------------------------------------------------


def test_the_block_is_the_text_in_files_md_byte_for_byte() -> None:
    text = FILES_MD.read_text(encoding="utf-8")
    section = text.split("## Git hook block", 1)[1]
    match = re.search(r"```sh\n(.*?)```", section, re.DOTALL)
    assert match is not None

    assert BLOCK == match.group(1)


def test_the_block_runs_wtenv_up_and_never_changes_the_exit_status() -> None:
    assert BLOCK.startswith(BEGIN + "\n")
    assert BLOCK.endswith(END + "\n")
    assert "wtenv up 1>&2 || true\n" in BLOCK


# --- inserting ------------------------------------------------------------------------


def test_a_new_file_gets_a_sh_shebang_and_the_block() -> None:
    content, action = inserted(None)

    assert content == b"#!/bin/sh\n" + BLOCK.encode()
    assert action == "installed"


def test_the_block_goes_directly_after_the_shebang_of_an_existing_hook() -> None:
    existing = b"#!/bin/sh\necho mine\nexit 3\n"

    content, action = inserted(existing)

    assert content == b"#!/bin/sh\n" + BLOCK.encode() + b"echo mine\nexit 3\n"
    assert action == "installed"


@pytest.mark.parametrize(
    "shebang",
    [
        "#!/bin/sh",
        "#!/bin/bash",
        "#!/usr/bin/dash",
        "#!/bin/ksh",
        "#!/bin/sh -e",
        "#! /bin/bash",
        "#!/usr/bin/env bash",
        "#!/usr/bin/env sh",
        "#!/bin/sh\r",
    ],
)
def test_a_shebang_naming_a_posix_shell_counts(shebang: str) -> None:
    existing = f"{shebang}\necho mine\n".encode()

    content, _ = inserted(existing)

    assert content == f"{shebang}\n".encode() + BLOCK.encode() + b"echo mine\n"


@pytest.mark.parametrize(
    "existing",
    [
        b"#!/usr/bin/env python3\nprint(1)\n",
        b"#!/usr/bin/perl\n",
        b"#!/usr/bin/env node\n",
        b"#!/bin/zsh\n",
        b"echo no shebang\n",
        b"",
    ],
)
def test_a_hook_that_is_not_a_shell_script_is_unsupported(existing: bytes) -> None:
    error = unsupported(existing)

    assert error.details["reason"] == "hook_not_shell"
    assert error.details["file"] == str(HOOK)
    assert error.hint is not None and BLOCK in error.hint


def test_inserting_again_changes_no_byte() -> None:
    first, _ = inserted(b"#!/bin/sh\necho mine\n")

    second, action = inserted(first)

    assert second == first
    assert action == "unchanged"


def test_an_outdated_block_is_rewritten_where_it_is() -> None:
    outdated = f"{BEGIN}\necho old block\n{END}\n"
    existing = f"#!/bin/sh\necho before\n{outdated}echo after\n".encode()

    content, action = inserted(existing)

    assert content == f"#!/bin/sh\necho before\n{BLOCK}echo after\n".encode()
    assert action == "updated"


def test_a_shebang_without_a_line_break_gets_one_before_the_block() -> None:
    content, _ = inserted(b"#!/bin/sh")

    assert content == b"#!/bin/sh\n" + BLOCK.encode()


def test_the_existing_content_is_kept_byte_for_byte() -> None:
    rest = b"\r\necho caf\xc3\xa9\r\n\xff\xfe binary-ish\n\n\n"

    content, _ = inserted(b"#!/bin/sh\n" + rest)

    assert content.endswith(rest)


@pytest.mark.parametrize(
    "damage",
    [
        f"{BEGIN}\necho no end\n",
        f"echo no begin\n{END}\n",
        f"{END}\n{BEGIN}\n",
        f"{BEGIN}\n{END}\n{BEGIN}\n{END}\n",
    ],
)
def test_damaged_markers_are_unsupported(damage: str) -> None:
    error = unsupported(f"#!/bin/sh\n{damage}".encode())

    assert error.details["reason"] == "markers_damaged"
    assert error.hint is not None and BLOCK in error.hint


# --- removing -------------------------------------------------------------------------


def test_removing_takes_out_exactly_the_block() -> None:
    original = b"#!/bin/bash\necho mine\nexit 3\n"
    installed, _ = inserted(original)

    assert remove_block(installed, HOOK) == original


def test_removing_keeps_every_other_byte() -> None:
    original = b"#!/bin/sh\r\n# a comment\r\n\xff\xfe\n\n"
    installed, _ = inserted(original)

    assert remove_block(installed, HOOK) == original


def test_removing_from_a_hook_with_the_block_in_the_middle_keeps_both_sides() -> None:
    content = f"#!/bin/sh\necho before\n{BLOCK}echo after\n".encode()

    assert remove_block(content, HOOK) == b"#!/bin/sh\necho before\necho after\n"


def test_removing_without_a_block_returns_none() -> None:
    assert remove_block(b"#!/bin/sh\necho mine\n", HOOK) is None


def test_removing_with_damaged_markers_is_unsupported_and_names_the_reason() -> None:
    error = unsupported(f"#!/bin/sh\n{BEGIN}\n".encode(), remove=True)

    assert error.details["reason"] == "markers_damaged"


def test_removing_does_not_need_a_shell_shebang() -> None:
    content = f"#!/usr/bin/env python3\n{BLOCK}".encode()

    assert remove_block(content, HOOK) == b"#!/usr/bin/env python3\n"


# --- a file wtenv created, with only the shebang left ----------------------------------


def test_only_the_shebang_is_left_after_removing_from_a_file_wtenv_created() -> None:
    installed, _ = inserted(None)
    remaining = remove_block(installed, HOOK)

    assert remaining == b"#!/bin/sh\n"
    assert only_shebang_left(remaining)


@pytest.mark.parametrize("remaining", [b"#!/bin/sh\necho mine\n", b"#!/bin/sh\n# note\n", b""])
def test_other_content_means_more_than_the_shebang_is_left(remaining: bytes) -> None:
    assert not only_shebang_left(remaining)


def test_blank_lines_do_not_count_as_content() -> None:
    assert only_shebang_left(b"#!/bin/sh\n\n  \n")


# --- the block in a shell: it never fails git, and does nothing outside a worktree ------


def run_block(
    tmp_path: Path, args: list[str], *, wtenv_script: str | None, inside: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the hook with `args` (`$1 $2 $3`), in a directory that is not inside a repository.

    `wtenv_script` is the body of a fake `wtenv` on PATH, or None for no `wtenv` at all. The fake
    appends a line to `calls` when it runs.
    """
    home = tmp_path / "hookrun"
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    work = inside or home / "work"
    work.mkdir(parents=True, exist_ok=True)
    hook = home / "post-checkout"
    hook.write_text("#!/bin/sh\n" + BLOCK + "echo hook-continued\n", encoding="utf-8")
    hook.chmod(0o755)
    if wtenv_script is not None:
        fake = bin_dir / "wtenv"
        fake.write_text(f"#!/bin/sh\necho called >> {home / 'calls'}\n{wtenv_script}\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    # `sh` and the tools the block uses (git, tr, printf) live in the system directories.
    path = os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"])
    return subprocess.run(
        [str(hook), *args],
        cwd=work,
        env={
            "PATH": path,
            "HOME": str(home),
            "GIT_CEILING_DIRECTORIES": str(tmp_path),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "hookrun" / "calls"
    return path.read_text().splitlines() if path.exists() else []


def test_outside_a_worktree_the_hook_does_nothing_and_exits_0(tmp_path: Path) -> None:
    result = run_block(tmp_path, [ZEROS, "abc123", "1"], wtenv_script="exit 0")

    assert result.returncode == 0
    assert result.stderr == ""
    assert calls(tmp_path) == []
    assert "hook-continued" in result.stdout


def test_outside_a_worktree_with_no_wtenv_on_path_the_hook_is_still_silent(
    tmp_path: Path,
) -> None:
    result = run_block(tmp_path, [ZEROS, "abc123", "1"], wtenv_script=None)

    assert result.returncode == 0
    assert result.stderr == ""
    assert "hook-continued" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        [ZEROS, "abc123", "0"],  # a file checkout
        ["a" * 40, "abc123", "1"],  # not from the all-zero object name: `git switch`
        ["", "", ""],
        [],
    ],
)
def test_the_hook_does_nothing_for_other_checkouts(tmp_path: Path, args: list[str]) -> None:
    result = run_block(tmp_path, args, wtenv_script="exit 0")

    assert result.returncode == 0
    assert result.stderr == ""
    assert calls(tmp_path) == []
    assert "hook-continued" in result.stdout


def test_a_sha256_all_zero_name_counts_as_the_null_ref(tmp_path: Path) -> None:
    """With 64 zeros the checks on `$1` and `$3` pass; outside a worktree the third stops it."""
    result = run_block(tmp_path, ["0" * 64, "abc123", "1"], wtenv_script="exit 0")

    assert result.returncode == 0
    assert calls(tmp_path) == []
