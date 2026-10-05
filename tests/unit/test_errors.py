"""The error-code table of cli.md, "Error codes and exit statuses" (FR-059)."""

import subprocess
import sys

import pytest

from wtenv.errors import (
    EXEC_COMMAND_NOT_EXECUTABLE,
    EXEC_COMMAND_NOT_FOUND,
    EXEC_WTENV_FAILED,
    EXIT_STATUS,
    EXIT_SUCCESS,
    ErrorCode,
    WtenvError,
)

# Written out in full on purpose: this test is the second copy of the table, so a change to
# a published code or status has to be made in two places.
EXPECTED_STATUSES = {
    "internal_error": 1,
    "usage_error": 2,
    "config_invalid": 3,
    "not_in_worktree": 4,
    "not_provisioned": 5,
    "no_free_block": 6,
    "env_file_unusable": 7,
    "dependency_unavailable": 8,
    "template_missing": 9,
    "template_in_use": 10,
    "ownership_conflict": 11,
    "post_up_failed": 12,
    "partial_failure": 13,
    "registry_busy": 14,
    "worktree_busy": 15,
    "registry_unreadable": 16,
    "problems_found": 17,
    "worktree_exists": 18,
    "unsupported": 19,
}


def test_there_are_nineteen_codes_with_the_documented_statuses() -> None:
    table = {code.value: status for code, status in EXIT_STATUS.items()}

    assert table == EXPECTED_STATUSES


def test_every_code_has_exactly_one_status() -> None:
    assert set(EXIT_STATUS) == set(ErrorCode)
    assert len(ErrorCode) == 19


def test_no_status_is_used_twice() -> None:
    statuses = list(EXIT_STATUS.values())

    assert len(statuses) == len(set(statuses))


@pytest.mark.parametrize("code", list(ErrorCode))
def test_a_failure_status_is_never_the_success_status(code: ErrorCode) -> None:
    assert EXIT_STATUS[code] != EXIT_SUCCESS


def test_success_is_zero() -> None:
    assert EXIT_SUCCESS == 0


def test_exec_statuses_follow_the_env_convention() -> None:
    assert EXEC_WTENV_FAILED == 125
    assert EXEC_COMMAND_NOT_EXECUTABLE == 126
    assert EXEC_COMMAND_NOT_FOUND == 127


def test_exec_statuses_do_not_collide_with_a_table_status() -> None:
    exec_statuses = {EXEC_WTENV_FAILED, EXEC_COMMAND_NOT_EXECUTABLE, EXEC_COMMAND_NOT_FOUND}

    assert exec_statuses.isdisjoint(EXIT_STATUS.values())


def test_wtenv_error_carries_code_message_hint_and_details() -> None:
    error = WtenvError(
        ErrorCode.CONFIG_INVALID,
        "block_size must be 1 to 1000",
        hint="Edit wtenv.toml",
        details={"setting": "block_size", "min_block_size": 3},
    )

    assert error.code is ErrorCode.CONFIG_INVALID
    assert error.message == "block_size must be 1 to 1000"
    assert error.hint == "Edit wtenv.toml"
    assert error.details == {"setting": "block_size", "min_block_size": 3}


def test_wtenv_error_defaults_to_no_hint_and_empty_details() -> None:
    error = WtenvError(ErrorCode.NOT_IN_WORKTREE, "not in a worktree")

    assert error.hint is None
    assert error.details == {}


def test_wtenv_error_details_are_not_shared_between_instances() -> None:
    first = WtenvError(ErrorCode.NOT_IN_WORKTREE, "one")
    second = WtenvError(ErrorCode.NOT_IN_WORKTREE, "two")
    first.details["cwd"] = "/somewhere"

    assert second.details == {}


def test_wtenv_error_is_an_exception_whose_text_is_the_message() -> None:
    error = WtenvError(ErrorCode.REGISTRY_BUSY, "registry is busy")

    assert isinstance(error, Exception)
    assert str(error) == "registry is busy"


def test_importing_errors_imports_only_the_standard_library() -> None:
    script = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import wtenv.errors\n"
        "new = set(sys.modules) - before\n"
        "tops = {name.split('.')[0] for name in new}\n"
        "print('\\n'.join(sorted(tops - set(sys.stdlib_module_names) - {'wtenv'})))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert result.stdout.split() == []
