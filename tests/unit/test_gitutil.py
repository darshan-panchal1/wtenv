"""Running git with a scrubbed environment, and parsing `git worktree list --porcelain`."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import (
    GIT_LOCAL_ENV_VARS,
    WorktreeRecord,
    git_environment,
    parse_worktree_list,
    run_git,
)

PORCELAIN = """\
worktree /code/app
HEAD ca26ed87533e11b45e5f6e2bfce25b9b8681777c
branch refs/heads/main

worktree /code/usb drive
HEAD ca26ed87533e11b45e5f6e2bfce25b9b8681777c
detached
locked on usb

worktree /code/gone
HEAD ca26ed87533e11b45e5f6e2bfce25b9b8681777c
branch refs/heads/gone
prunable gitdir file points to non-existent location

worktree /code/bare.git
bare

worktree /code/pinned
HEAD ca26ed87533e11b45e5f6e2bfce25b9b8681777c
branch refs/heads/pinned
locked
"""


def _record(path: str, **attributes: bool) -> WorktreeRecord:
    return WorktreeRecord(path=path, **attributes)


def test_parsing_gives_one_record_per_worktree_in_order() -> None:
    records = parse_worktree_list(PORCELAIN)

    assert [record.path for record in records] == [
        "/code/app",
        "/code/usb drive",
        "/code/gone",
        "/code/bare.git",
        "/code/pinned",
    ]


def test_parsing_reads_the_bare_detached_locked_and_prunable_attributes() -> None:
    records = parse_worktree_list(PORCELAIN)

    assert records == [
        _record("/code/app"),
        _record("/code/usb drive", detached=True, locked=True),
        _record("/code/gone", prunable=True),
        _record("/code/bare.git", bare=True),
        _record("/code/pinned", locked=True),
    ]


def test_parsing_empty_output_gives_no_records() -> None:
    assert parse_worktree_list("") == []


def test_parsing_does_not_need_a_trailing_blank_line() -> None:
    records = parse_worktree_list("worktree /code/app\nHEAD abc\nbranch refs/heads/main")

    assert records == [_record("/code/app")]


def _install_fake_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """Make `git` resolve to a shell script, and nothing else, on PATH."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\n" + script)
    git.chmod(git.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))


def test_a_missing_git_is_dependency_unavailable_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(WtenvError) as raised:
        run_git(["rev-parse"], cwd=tmp_path)

    assert raised.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert raised.value.details == {"dependency": "git", "reason": "not_installed"}


def test_a_missing_directory_is_not_reported_as_a_missing_git(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_git(["rev-parse"], cwd=tmp_path / "does-not-exist")


def test_a_failing_git_older_than_2_31_is_dependency_unavailable_too_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_git(
        tmp_path,
        monkeypatch,
        'if [ "$1" = "--version" ]; then echo "git version 2.30.1"; exit 0; fi\n'
        "echo 'unknown option: --path-format=absolute' >&2\n"
        "exit 129\n",
    )

    with pytest.raises(WtenvError) as raised:
        run_git(["rev-parse", "--path-format=absolute"], cwd=tmp_path)

    assert raised.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert raised.value.details == {
        "dependency": "git",
        "reason": "too_old",
        "required": "2.31",
        "found": "2.30.1",
    }


def test_a_failing_git_that_is_new_enough_returns_its_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_git(
        tmp_path,
        monkeypatch,
        'if [ "$1" = "--version" ]; then echo "git version 2.39.5 (Apple Git-154)"; exit 0; fi\n'
        "echo 'fatal: not a git repository' >&2\n"
        "exit 128\n",
    )

    result = run_git(["rev-parse"], cwd=tmp_path)

    assert result.returncode == 128
    assert "not a git repository" in result.stderr


def test_a_failing_git_with_an_unreadable_version_returns_its_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_git(
        tmp_path,
        monkeypatch,
        'if [ "$1" = "--version" ]; then echo "something else"; exit 0; fi\nexit 128\n',
    )

    assert run_git(["rev-parse"], cwd=tmp_path).returncode == 128


def test_check_raises_when_git_fails_for_another_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_git(
        tmp_path,
        monkeypatch,
        'if [ "$1" = "--version" ]; then echo "git version 2.54.0"; exit 0; fi\nexit 1\n',
    )

    with pytest.raises(subprocess.CalledProcessError):
        run_git(["ls-files"], cwd=tmp_path, check=True)


def test_check_still_reports_a_git_that_is_too_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_git(
        tmp_path,
        monkeypatch,
        'if [ "$1" = "--version" ]; then echo "git version 2.20.0"; exit 0; fi\nexit 129\n',
    )

    with pytest.raises(WtenvError) as raised:
        run_git(["ls-files"], cwd=tmp_path, check=True)

    assert raised.value.details["reason"] == "too_old"


def test_the_normal_path_makes_exactly_one_git_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_GIT_LOG", str(log))
    _install_fake_git(tmp_path, monkeypatch, 'echo "$*" >> "$FAKE_GIT_LOG"\nexit 0\n')

    run_git(["rev-parse", "--show-toplevel"], cwd=tmp_path)

    assert log.read_text().splitlines() == ["rev-parse --show-toplevel"]


def test_the_version_is_read_only_after_a_call_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_GIT_LOG", str(log))
    _install_fake_git(
        tmp_path,
        monkeypatch,
        'echo "$*" >> "$FAKE_GIT_LOG"\n'
        'if [ "$1" = "--version" ]; then echo "git version 2.54.0"; exit 0; fi\n'
        "exit 128\n",
    )

    run_git(["rev-parse"], cwd=tmp_path)

    assert log.read_text().splitlines() == ["rev-parse", "--version"]


def test_the_scrub_list_names_the_variables_the_design_documents_name() -> None:
    assert {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"} <= set(
        GIT_LOCAL_ENV_VARS
    )


def test_the_scrub_list_names_a_variable_that_git_2_31_prints_and_later_gits_do_not() -> None:
    """`git rev-parse --local-env-vars` on 2.31.0 includes this one (T223)."""
    assert "GIT_INTERNAL_SUPER_PREFIX" in GIT_LOCAL_ENV_VARS


def test_the_environment_given_to_git_lacks_every_repository_local_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in GIT_LOCAL_ENV_VARS:
        monkeypatch.setenv(name, "/somewhere/else")

    environment = git_environment()

    assert [name for name in GIT_LOCAL_ENV_VARS if name in environment] == []


def test_the_environment_given_to_git_keeps_everything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", "/somewhere/else")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "someone")
    monkeypatch.setenv("WTENV_TEST_OTHER", "kept")

    environment = git_environment()

    assert environment["GIT_AUTHOR_NAME"] == "someone"
    assert environment["WTENV_TEST_OTHER"] == "kept"
    assert environment["PATH"] == os.environ["PATH"]


def test_the_git_process_does_not_see_the_repository_local_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in GIT_LOCAL_ENV_VARS:
        monkeypatch.setenv(name, "/somewhere/else")
    _install_fake_git(tmp_path, monkeypatch, "/usr/bin/env\n")

    seen = run_git([], cwd=tmp_path).stdout.splitlines()

    # Guards against passing because the script printed nothing.
    assert any(line.startswith("PATH=") for line in seen)
    assert [line for line in seen if line.split("=", 1)[0] in GIT_LOCAL_ENV_VARS] == []
