"""Contract: the JSON documents wtenv prints with `--json` (schema version 1).

This file is the contract, not the implementation. `src/wtenv/output.py` must define the
same models, and the contract tests validate real command output against them.

Rules that apply to every command (FR-058, FR-059):

- With `--json`, standard output holds exactly one JSON document: one of the `*Result`
  models below, serialised with `model_dump_json()`. Everything else goes to standard error.
- `ok` is true when the exit status is 0. When it is false, `error` is set.
- A failed command still prints its own result model. Fields the command did not get to fill
  keep their defaults.
- Codes and exit statuses are stable. New codes may be added; existing ones never change
  meaning.
- Credentials never appear in any field (FR-019). A database is shown by kind, name, host,
  and port, never by URL.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

SCHEMA_VERSION = 1


# --------------------------------------------------------------------------------------
# Error codes and exit statuses
# --------------------------------------------------------------------------------------


class ErrorCode(StrEnum):
    """Stable error codes. Each has exactly one exit status (see EXIT_STATUS)."""

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


class WarningCode(StrEnum):
    """Stable codes for things worth telling the caller that are not failures."""

    ENV_DUPLICATE_VARIABLE = "env_duplicate_variable"
    COMPOSE_FIXED_CONTAINER_NAME = "compose_fixed_container_name"
    WORKTREE_MOVED = "worktree_moved"
    CONFIG_IGNORED = "config_ignored"


class FindingCode(StrEnum):
    """Stable codes for `wtenv doctor` findings (FR-061)."""

    BLOCK_OVERLAP = "block_overlap"
    PORT_CONFLICT = "port_conflict"
    PORT_IN_USE = "port_in_use"
    ORPHANED_WORKTREE = "orphaned_worktree"
    UNVERIFIABLE_WORKTREE = "unverifiable_worktree"
    MISSING_RESOURCE = "missing_resource"
    INCOMPLETE_WORKTREE = "incomplete_worktree"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    RESOURCE_CHECK_SKIPPED = "resource_check_skipped"


# --------------------------------------------------------------------------------------
# Shared parts
# --------------------------------------------------------------------------------------


class Model(BaseModel):
    """Base for every contract model: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


class ErrorInfo(Model):
    code: ErrorCode
    exit_status: int
    message: str
    hint: str | None = None
    # Keys per code are listed in contracts/cli.md, "Error details".
    details: dict[str, JsonValue] = Field(default_factory=dict)


class WarningInfo(Model):
    code: WarningCode
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class Result(Model):
    """Fields every `--json` document has."""

    schema_version: Literal[1] = 1
    # The command that ran; null only when the command line could not be parsed far enough
    # to know it.
    command: str | None = None
    ok: bool
    error: ErrorInfo | None = None
    warnings: list[WarningInfo] = Field(default_factory=list)


class Status(StrEnum):
    """Worktree status (FR-049)."""

    PROVISIONED = "provisioned"
    UNPROVISIONED = "unprovisioned"
    INCOMPLETE = "incomplete"
    ORPHANED = "orphaned"
    UNVERIFIABLE = "unverifiable"


class UnverifiableReason(StrEnum):
    """Which orphan check failed (FR-072)."""

    REPOSITORY_NOT_FOUND = "repository_not_found"
    GIT_STILL_LISTS = "git_still_lists"
    MOVED = "moved"
    PATH_EXISTS = "path_exists"


class ResourceState(StrEnum):
    """Where a recorded resource is in its life (data-model.md, "Resource states")."""

    CREATING = "creating"
    CREATED = "created"
    REMOVING = "removing"


class BlockView(Model):
    start: int
    end: int  # inclusive
    size: int


class PortView(Model):
    """One assigned port of a block."""

    port: int
    # The env variable that carries this port, if any.
    variable: str | None = None
    # The compose service that publishes on this port, if any. A port tied to a variable
    # has both `variable` and `service` set.
    service: str | None = None
    target: int | None = None  # container port
    protocol: Literal["tcp", "udp"] | None = None
    host_ip: str | None = None


class DatabaseView(Model):
    kind: Literal["postgres", "sqlite"]
    state: ResourceState
    # postgres
    name: str | None = None
    host: str | None = None
    port: int | None = None
    # sqlite: absolute path of the worktree's copy
    path: str | None = None


class WorktreeView(Model):
    """One row of `ls`; also describes the worktree in `up` output."""

    repository: str  # absolute path of the repository's common git directory
    git_dir: str  # the identity (registry key)
    path: str  # recorded location; for unprovisioned rows, the current location
    status: Status
    reason: UnverifiableReason | None = None  # set when status is "unverifiable"
    current_path: str | None = None  # where git has the worktree now; set when reason is "moved"
    block: BlockView | None = None
    ports: list[PortView] = Field(default_factory=list)
    databases: list[DatabaseView] = Field(default_factory=list)
    compose_project: str | None = None
    env_file: str | None = None  # relative to the worktree root


class ItemKind(StrEnum):
    """Kinds of things wtenv creates, changes, or removes."""

    PORT_BLOCK = "port_block"
    ENV_SECTION = "env_section"
    ENV_FILE = "env_file"
    POSTGRES_DATABASE = "postgres_database"
    SQLITE_FILE = "sqlite_file"
    COMPOSE_OVERRIDE = "compose_override"
    COMPOSE_PROJECT = "compose_project"
    COMPOSE_CONTAINER = "compose_container"
    COMPOSE_NETWORK = "compose_network"
    COMPOSE_VOLUME = "compose_volume"
    EXCLUDE_ENTRIES = "exclude_entries"
    REGISTRY_ENTRY = "registry_entry"
    HOOK_BLOCK = "hook_block"
    HOOK_FILE = "hook_file"


class Item(Model):
    kind: ItemKind
    # What identifies it: a port range ("20010-20019"), a path, a database name, a container
    # name, a project name.
    name: str
    # Recorded path of the worktree it belongs to. Set by `down` and `gc`.
    worktree: str | None = None


class FailedItem(Item):
    reason: str


# --------------------------------------------------------------------------------------
# One result model per command
# --------------------------------------------------------------------------------------


class VersionResult(Result):
    """`wtenv --version --json`."""

    command: Literal["version"] = "version"
    version: str


class UpChange(Model):
    item: Item
    action: Literal["created", "updated", "unchanged", "released"]


class PostUpRun(Model):
    command: str
    exit_status: int


class UpResult(Result):
    """`wtenv up --json`."""

    command: Literal["up"] = "up"
    worktree: WorktreeView | None = None
    changes: list[UpChange] = Field(default_factory=list)
    post_up: list[PostUpRun] = Field(default_factory=list)


class DownResult(Result):
    """`wtenv down [--dry-run] --json`.

    A real run fills `removed`; a dry run fills `would_remove` and changes nothing.
    """

    command: Literal["down"] = "down"
    dry_run: bool = False
    worktree_path: str | None = None
    removed: list[Item] = Field(default_factory=list)
    would_remove: list[Item] = Field(default_factory=list)
    already_absent: list[Item] = Field(default_factory=list)
    failed: list[FailedItem] = Field(default_factory=list)


class KeptEntry(Model):
    """An unverifiable entry that `gc` left alone (FR-072)."""

    path: str
    git_dir: str
    reason: UnverifiableReason
    current_path: str | None = None


class GcResult(Result):
    """`wtenv gc [--dry-run] [--release PATH]... --json`."""

    command: Literal["gc"] = "gc"
    dry_run: bool = False
    # Recorded worktree paths whose entries were completely released / would be.
    released: list[str] = Field(default_factory=list)
    would_release: list[str] = Field(default_factory=list)
    removed: list[Item] = Field(default_factory=list)
    would_remove: list[Item] = Field(default_factory=list)
    already_absent: list[Item] = Field(default_factory=list)
    failed: list[FailedItem] = Field(default_factory=list)
    kept: list[KeptEntry] = Field(default_factory=list)
    # Entries whose worktree lock was held; not waited for (FR-077).
    skipped_busy: list[str] = Field(default_factory=list)
    # `--release` paths for which the registry holds nothing.
    no_entry: list[str] = Field(default_factory=list)


class LsResult(Result):
    """`wtenv ls --json`."""

    command: Literal["ls"] = "ls"
    worktrees: list[WorktreeView] = Field(default_factory=list)


class ExecResult(Result):
    """`wtenv exec --json -- <command>`: printed only when wtenv itself fails.

    When the command runs, wtenv prints nothing; the command owns standard output.
    """

    command: Literal["exec"] = "exec"


class Finding(Model):
    code: FindingCode
    # Only "problem" findings make `doctor` exit with `problems_found`.
    severity: Literal["problem", "info"]
    message: str
    worktree: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)


class DependencyStatus(Model):
    name: Literal["git", "docker", "postgres"]
    status: Literal["ok", "unavailable", "not_required"]
    detail: str | None = None


class DoctorResult(Result):
    """`wtenv doctor --json`."""

    command: Literal["doctor"] = "doctor"
    findings: list[Finding] = Field(default_factory=list)
    dependencies: list[DependencyStatus] = Field(default_factory=list)


class HookInstallResult(Result):
    """`wtenv hook install --json`."""

    command: Literal["hook install"] = "hook install"
    hook_file: str | None = None
    action: Literal["installed", "updated", "unchanged"] | None = None


class HookUninstallResult(Result):
    """`wtenv hook uninstall [--dry-run] --json`."""

    command: Literal["hook uninstall"] = "hook uninstall"
    dry_run: bool = False
    hook_file: str | None = None
    action: Literal["removed", "absent"] | None = None
    removed: list[Item] = Field(default_factory=list)
    would_remove: list[Item] = Field(default_factory=list)
