"""Printing results, errors, and warnings (cli.md, "Rules for every command"; FR-058)."""

import json

import pytest

from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.output import (
    UpResult,
    VersionResult,
    WarningCode,
    WarningInfo,
    failed_result,
    print_error,
    print_result,
    print_warnings,
)


def test_json_mode_prints_exactly_one_document_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = VersionResult(ok=True, version="0.1.0")

    print_result(result, json_mode=True)

    captured = capsys.readouterr()
    assert captured.out == result.model_dump_json() + "\n"
    assert json.loads(captured.out) == {
        "schema_version": 1,
        "command": "version",
        "ok": True,
        "error": None,
        "warnings": [],
        "version": "0.1.0",
    }
    assert captured.err == ""


def test_json_mode_ignores_the_text_given_for_people(capsys: pytest.CaptureFixture[str]) -> None:
    print_result(VersionResult(ok=True, version="0.1.0"), json_mode=True, text="wtenv 0.1.0")

    assert "wtenv 0.1.0" not in capsys.readouterr().out


def test_text_mode_prints_the_text_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    print_result(VersionResult(ok=True, version="0.1.0"), json_mode=False, text="wtenv 0.1.0")

    captured = capsys.readouterr()
    assert captured.out == "wtenv 0.1.0\n"
    assert captured.err == ""


def test_text_mode_with_no_text_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    print_result(VersionResult(ok=True, version="0.1.0"), json_mode=False)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_an_error_prints_its_code_and_message_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_error(WtenvError(ErrorCode.NOT_IN_WORKTREE, "not inside a git worktree"))

    captured = capsys.readouterr()
    assert captured.err == "wtenv: error [not_in_worktree]: not inside a git worktree\n"
    assert captured.out == ""


def test_an_error_with_a_hint_prints_the_hint_on_the_next_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = WtenvError(ErrorCode.NO_FREE_BLOCK, "no free block", hint="Run `wtenv gc`")

    print_error(error)

    assert capsys.readouterr().err == (
        "wtenv: error [no_free_block]: no free block\nhint: Run `wtenv gc`\n"
    )


def test_warnings_print_one_line_each_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    warnings = [
        WarningInfo(code=WarningCode.ENV_DUPLICATE_VARIABLE, message="PORT is set twice"),
        WarningInfo(code=WarningCode.WORKTREE_MOVED, message="worktree moved"),
    ]

    print_warnings(warnings)

    captured = capsys.readouterr()
    assert captured.err == (
        "wtenv: warning [env_duplicate_variable]: PORT is set twice\n"
        "wtenv: warning [worktree_moved]: worktree moved\n"
    )
    assert captured.out == ""


def test_no_warnings_print_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    print_warnings([])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_failed_result_keeps_its_own_model_with_defaults_elsewhere() -> None:
    error = WtenvError(
        ErrorCode.NO_FREE_BLOCK,
        "no free block",
        hint="Run `wtenv gc`",
        details={"block_size": 10, "range": "20000-29999"},
    )

    result = failed_result(UpResult, error)

    assert isinstance(result, UpResult)
    assert result.command == "up"
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.NO_FREE_BLOCK
    assert result.error.exit_status == EXIT_STATUS[ErrorCode.NO_FREE_BLOCK] == 6
    assert result.error.message == "no free block"
    assert result.error.hint == "Run `wtenv gc`"
    assert result.error.details == {"block_size": 10, "range": "20000-29999"}
    assert result.worktree is None
    assert result.changes == []
    assert result.post_up == []
    assert result.warnings == []


@pytest.mark.parametrize("code", list(ErrorCode))
def test_a_failed_result_carries_the_status_of_its_code(code: ErrorCode) -> None:
    result = failed_result(UpResult, WtenvError(code, "message"))

    assert result.error is not None
    assert result.error.exit_status == EXIT_STATUS[code]


def test_a_failed_result_can_carry_the_warnings_found_before_the_failure() -> None:
    warning = WarningInfo(code=WarningCode.WORKTREE_MOVED, message="worktree moved")

    result = failed_result(UpResult, WtenvError(ErrorCode.CONFIG_INVALID, "bad"), [warning])

    assert result.warnings == [warning]


def test_a_failed_command_still_prints_one_document_on_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = WtenvError(ErrorCode.ENV_FILE_UNUSABLE, "env file is a directory", hint="Remove it")
    result = failed_result(UpResult, error)

    print_warnings(result.warnings)
    print_error(error)
    print_result(result, json_mode=True)

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["ok"] is False
    assert document["error"]["code"] == "env_file_unusable"
    assert document["error"]["exit_status"] == 7
    assert document["command"] == "up"
    assert captured.err == (
        "wtenv: error [env_file_unusable]: env file is a directory\nhint: Remove it\n"
    )
