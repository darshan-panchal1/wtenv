"""The `--json` documents of every command, and the functions that print results.

The models are those of `specs/001-worktree-runtime-isolation/contracts/json_models.py`,
unchanged: a contract test compares their JSON schemas, so even the docstrings match. With
`--json`, a command's standard output holds exactly one of these documents (FR-058).

`ErrorCode` and `EXIT_STATUS` come from `wtenv.errors`, where the exit statuses are defined;
this module imports no other wtenv module.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError

SCHEMA_VERSION = 1


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
    # The SQLite copy and each of its `-wal`, `-shm`, and `-journal` side files; the side
    # files are separate items whose `name` is their own absolute path (FR-039).
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


# --------------------------------------------------------------------------------------
# Printing results, errors, and warnings (cli.md, "Rules for every command")
# --------------------------------------------------------------------------------------

R = TypeVar("R", bound=Result)


def failed_result(model: type[R], error: WtenvError, warnings: Sequence[WarningInfo] = ()) -> R:
    """Return the result `model` of a failed command: `ok` false, `error` set.

    Every other field keeps its default, so `model` must be a result whose other fields all
    have one. `error.exit_status` is the exit status of the error's code.
    """
    info = ErrorInfo(
        code=error.code,
        exit_status=EXIT_STATUS[error.code],
        message=error.message,
        hint=error.hint,
        details=error.details,
    )
    return model(ok=False, error=info, warnings=list(warnings))


def render_up_text(result: UpResult) -> str:
    """Return the text `wtenv up` prints for people (cli.md, `wtenv up`; not a stable interface).

    One header line, then one line per part of the worktree: its ports with their variables, its
    databases, and the env file with what happened to it. A database line shows the kind, the
    name and server or the path, and what happened to it; never a URL, which can hold a password.
    """
    worktree = result.worktree
    if worktree is None:
        return ""
    lines = [f"wtenv: provisioned {worktree.path}"]
    if worktree.block is not None:
        variables = " ".join(
            f"{port.variable}={port.port}" for port in worktree.ports if port.variable is not None
        )
        lines.append(f"  {'ports':<11}{worktree.block.start}-{worktree.block.end}  {variables}")
    for change in result.changes:
        if change.action == "released":
            lines.append(f"  {'released':<11}{change.item.kind.value} {change.item.name}")
    for database in worktree.databases:
        lines.append(_database_line(database, result.changes))
    if worktree.env_file is not None:
        # The section of the env file that is in use, not one that was released.
        action = next(
            (
                change.action
                for change in result.changes
                if change.item.kind is ItemKind.ENV_SECTION and change.action != "released"
            ),
            "unchanged",
        )
        lines.append(f"  {'env file':<11}{worktree.env_file} ({action})")
    return "\n".join(lines)


def _database_line(database: DatabaseView, changes: Sequence[UpChange]) -> str:
    """Return the `database` line of `render_up_text`; what happened comes from `changes`."""
    kind = ItemKind.POSTGRES_DATABASE if database.kind == "postgres" else ItemKind.SQLITE_FILE
    named = database.name if database.kind == "postgres" else database.path
    action = next(
        (c.action for c in changes if c.item.kind is kind and c.item.name == named), "unchanged"
    )
    where = (
        f"{database.name} on {database.host}:{database.port}"
        if database.kind == "postgres"
        else f"{database.path}"
    )
    return f"  {'database':<11}{database.kind} {where}  ({action})"


def print_result(result: Result, *, json_mode: bool, text: str = "") -> None:
    """Print a command's result to standard output.

    With `json_mode`, exactly one document: the result serialised with `model_dump_json()`.
    Otherwise `text`, the output for people, when there is any.
    """
    if json_mode:
        print(result.model_dump_json())
    elif text:
        print(text)


def print_error(error: WtenvError) -> None:
    """Print an error to standard error, with its hint on a second line when it has one."""
    print(f"wtenv: error [{error.code.value}]: {error.message}", file=sys.stderr)
    if error.hint is not None:
        print(f"hint: {error.hint}", file=sys.stderr)


def print_warnings(warnings: Sequence[WarningInfo]) -> None:
    """Print each warning to standard error, one line each."""
    for warning in warnings:
        print(f"wtenv: warning [{warning.code.value}]: {warning.message}", file=sys.stderr)
