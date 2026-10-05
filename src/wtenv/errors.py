"""Error codes, exit statuses, and the exception wtenv raises (FR-059).

Standard library only, so that importing it costs nothing at start-up. The codes and statuses
are part of the stable interface (constitution, Principle IV): `contracts/cli.md` is the
authority and `contracts/json_models.py` the matching models.
"""

from enum import StrEnum

# What `--json` documents may carry in `error.details`. Recursive through string references,
# because the `type` statement needs Python 3.12 and wtenv supports 3.11.
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class ErrorCode(StrEnum):
    """Stable error codes. Each has exactly one exit status (see `EXIT_STATUS`)."""

    INTERNAL_ERROR = "internal_error"
    USAGE_ERROR = "usage_error"
    CONFIG_INVALID = "config_invalid"
    NOT_IN_WORKTREE = "not_in_worktree"
    NOT_PROVISIONED = "not_provisioned"
    NO_FREE_BLOCK = "no_free_block"
    ENV_FILE_UNUSABLE = "env_file_unusable"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    TEMPLATE_MISSING = "template_missing"
    TEMPLATE_IN_USE = "template_in_use"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    POST_UP_FAILED = "post_up_failed"
    PARTIAL_FAILURE = "partial_failure"
    REGISTRY_BUSY = "registry_busy"
    WORKTREE_BUSY = "worktree_busy"
    REGISTRY_UNREADABLE = "registry_unreadable"
    PROBLEMS_FOUND = "problems_found"
    WORKTREE_EXISTS = "worktree_exists"
    UNSUPPORTED = "unsupported"


EXIT_SUCCESS = 0

EXIT_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INTERNAL_ERROR: 1,
    ErrorCode.USAGE_ERROR: 2,
    ErrorCode.CONFIG_INVALID: 3,
    ErrorCode.NOT_IN_WORKTREE: 4,
    ErrorCode.NOT_PROVISIONED: 5,
    ErrorCode.NO_FREE_BLOCK: 6,
    ErrorCode.ENV_FILE_UNUSABLE: 7,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 8,
    ErrorCode.TEMPLATE_MISSING: 9,
    ErrorCode.TEMPLATE_IN_USE: 10,
    ErrorCode.OWNERSHIP_CONFLICT: 11,
    ErrorCode.POST_UP_FAILED: 12,
    ErrorCode.PARTIAL_FAILURE: 13,
    ErrorCode.REGISTRY_BUSY: 14,
    ErrorCode.WORKTREE_BUSY: 15,
    ErrorCode.REGISTRY_UNREADABLE: 16,
    ErrorCode.PROBLEMS_FOUND: 17,
    ErrorCode.WORKTREE_EXISTS: 18,
    ErrorCode.UNSUPPORTED: 19,
}

# `wtenv exec` only. Any failure of wtenv itself exits 125, whatever its code; the code is
# still reported in `error.code`. A command that ran decides the exit status itself.
EXEC_WTENV_FAILED = 125
EXEC_COMMAND_NOT_EXECUTABLE = 126
EXEC_COMMAND_NOT_FOUND = 127


class WtenvError(Exception):
    """A failure with a stable code, raised anywhere and turned into an exit status by the CLI."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        """Create the error.

        `details` holds the keys listed for `code` in cli.md, "Error details".
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details: dict[str, JsonValue] = {} if details is None else details
