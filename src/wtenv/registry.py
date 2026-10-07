"""The registry: the only state wtenv keeps (data-model.md; FR-066 to FR-070).

One JSON file in the per-user state directory records everything wtenv allocates and creates,
keyed by each worktree's git directory. It is read and written only while the registry lock is
held, and written through a temporary file and an atomic rename. A file that cannot be read is
never rewritten (FR-070).
"""

import contextlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from wtenv.errors import ErrorCode, WtenvError
from wtenv.locks import REGISTRY_LOCK_TIMEOUT, ensure_state_dir, registry_lock, state_dir
from wtenv.output import ResourceState

PORT_RANGE_START = 20000
PORT_RANGE_END = 29999  # inclusive
MAX_BLOCK_SIZE = 1000

REGISTRY_VERSION = 1


class RegistryModel(BaseModel):
    """Base for every registry model: unknown fields are an error.

    No model has a field for a password or any other credential (FR-019).
    """

    model_config = ConfigDict(extra="forbid")


class PortBlock(RegistryModel):
    """The ports reserved for one worktree: `size` consecutive ports from `start`."""

    start: int
    size: int = Field(ge=1, le=MAX_BLOCK_SIZE)

    @model_validator(mode="after")
    def _start_is_a_block_boundary_inside_the_range(self) -> Self:
        """`start` is `20000 + k × size` for a whole number `k`, and the block ends by 29999."""
        if self.start < PORT_RANGE_START or (self.start - PORT_RANGE_START) % self.size != 0:
            raise ValueError(f"start must be {PORT_RANGE_START} + k × size, got {self.start}")
        if self.start + self.size - 1 > PORT_RANGE_END:
            raise ValueError(f"the block must end by {PORT_RANGE_END}")
        return self


class VariablePort(RegistryModel):
    """The port assigned to one variable of `ports` in `wtenv.toml`."""

    variable: str
    port: int


class PublishedPort(RegistryModel):
    """The host port assigned to one port a compose service publishes."""

    service: str
    target: int  # the container port
    protocol: Literal["tcp", "udp"]
    host_ip: str | None = None
    port: int  # the assigned host port
    variable: str | None = None  # the variable it is tied to, if any


class EnvFileRecord(RegistryModel):
    """The env file wtenv writes its section to."""

    path: str  # relative to the worktree root
    created_file: bool  # wtenv created the file; `down` deletes it if nothing else is in it
    added_newline: bool  # wtenv added a line break before its section; `down` removes it
    state: ResourceState


class DatabaseRecord(RegistryModel):
    """A database wtenv created for the worktree. The password is never stored."""

    kind: Literal["postgres", "sqlite"]
    name: str | None = None  # Postgres
    host: str | None = None  # Postgres, from the URL pattern at creation
    port: int | None = None  # Postgres, from the URL pattern at creation
    user: str | None = None  # Postgres, from the URL pattern at creation
    path: str | None = None  # SQLite: the copy's path, relative to the worktree root
    state: ResourceState


class ComposeRecord(RegistryModel):
    """The compose project of the worktree, and the override file wtenv generated for it."""

    project: str  # recorded before anything else is done for compose, and never changed
    file: str  # relative to the worktree root
    override: str  # relative to the worktree root
    override_state: ResourceState


class HookRecord(RegistryModel):
    """The git hook wtenv installed in a repository."""

    hook_file: str  # absolute path
    created_file: bool  # wtenv created the file, so `hook uninstall` may delete it


class WorktreeEntry(RegistryModel):
    """Everything wtenv allocated and created for one worktree."""

    git_dir: str  # equals the key in `Registry.worktrees`
    path: str  # the location at the last `up` or `down` in the worktree
    repository: str  # the common git directory
    state: Literal["incomplete", "provisioned"]
    block: PortBlock
    ports: list[VariablePort] = Field(default_factory=list)  # same order as `wtenv.toml`
    published: list[PublishedPort] = Field(default_factory=list)  # empty without compose
    env_file: EnvFileRecord | None = None
    # At most one per kind. A kind that is no longer configured stays until `down` (FR-065).
    databases: list[DatabaseRecord] = Field(default_factory=list)
    compose: ComposeRecord | None = None
    # This worktree's generated paths, as written to `.git/info/exclude`.
    exclude_patterns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_most_one_database_per_kind(self) -> Self:
        kinds = [database.kind for database in self.databases]
        if len(kinds) != len(set(kinds)):
            raise ValueError("databases may hold at most one record per kind")
        return self


class Registry(RegistryModel):
    """The whole registry file."""

    version: Literal[1]
    worktrees: dict[str, WorktreeEntry] = Field(default_factory=dict)  # keyed by `git_dir`
    hooks: dict[str, HookRecord] = Field(default_factory=dict)  # keyed by repository

    @model_validator(mode="after")
    def _every_entry_is_keyed_by_its_git_dir(self) -> Self:
        for key, entry in self.worktrees.items():
            if entry.git_dir != key:
                raise ValueError(f"entry {key!r} has git_dir {entry.git_dir!r}")
        return self


def registry_path() -> Path:
    """Return the path of the registry file, in the per-user state directory."""
    return state_dir() / "registry.json"


def _unreadable(path: Path, reason: str, why: str) -> WtenvError:
    """Build the `registry_unreadable` error: nothing was changed, and nothing will be."""
    return WtenvError(
        ErrorCode.REGISTRY_UNREADABLE,
        f"cannot use the registry {path}: {why}",
        hint="Repair or remove the file by hand. wtenv never rewrites a registry it cannot read.",
        details={"path": str(path), "reason": reason},
    )


def load() -> Registry:
    """Read the registry; with no file, return an empty one. Call it with the lock held.

    A file that is not valid JSON, does not match the schema, has an unknown `version`, or
    cannot be read stops with `registry_unreadable`. It is never rewritten (FR-070).
    """
    path = registry_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Registry(version=REGISTRY_VERSION)
    except UnicodeDecodeError:
        raise _unreadable(path, "invalid_json", "it is not valid UTF-8 text") from None
    except OSError as error:
        raise _unreadable(path, "not_readable", error.strerror or "it cannot be read") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise _unreadable(path, "invalid_json", f"it is not valid JSON ({error})") from error
    if isinstance(data, dict) and "version" in data and data["version"] != REGISTRY_VERSION:
        raise _unreadable(
            path, "unknown_version", f"its version is {data['version']!r}, not {REGISTRY_VERSION}"
        )
    try:
        return Registry.model_validate(data)
    except ValidationError as error:
        raise _unreadable(path, "invalid_schema", "it does not match the schema") from error


def save(registry: Registry) -> None:
    """Write the registry atomically: a temporary file, `fsync`, then `os.replace`.

    The file has mode 0600. Call it with the lock held. A failure leaves the old file as it was.
    """
    descriptor, temporary = tempfile.mkstemp(
        dir=ensure_state_dir(), prefix="registry.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(registry.model_dump_json(indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, registry_path())
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


@contextmanager
def transaction(timeout: float = REGISTRY_LOCK_TIMEOUT) -> Iterator[Registry]:
    """Read, check, and write the registry under the registry lock (FR-068).

    Yields the registry to change in place. It is saved when the block ends normally; an
    exception inside the block saves nothing. `timeout` is how long to wait for the lock.
    """
    with registry_lock(timeout):
        registry = load()
        yield registry
        save(registry)
