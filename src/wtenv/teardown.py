"""`wtenv down`: release everything the registry records for a worktree (FR-038 to FR-044).

Teardown depends only on the registry: it works when the worktree directory, its `wtenv.toml`, and
its env file are gone (FR-044). It removes only what the entry records, never anything found by a
matching name (FR-039). The order of work is that of contracts/cli.md, `wtenv down`: the compose
project (containers, networks, volumes, then the override file), the databases, wtenv's section of
the env file, the port block and the registry entry, and, with the last registered worktree of the
repository, wtenv's entries in `.git/info/exclude` (FR-085).

`release_entry` and `plan_release` are one code path: the plan is the release with `dry_run` set, so
`--dry-run` lists exactly what a real run removes (FR-040). A real run records each resource as
`removing` before it removes it, and drops the record once it is gone, so an interruption at any
point leaves a state that `down` or `up` finishes (FR-067, FR-069). The registry lock is held only
for those short updates (FR-068).

Steps of other modules are called through their module (`database.remove_sqlite_copy`,
`envfile.remove_section`, ...), so the recovery tests can stop `down` right before or after any one
of them. The compose module is imported only when a compose project is recorded (NFR-001).
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from wtenv import database, envfile, exclude, registry
from wtenv.config import load_config
from wtenv.database import PostgresTarget, Removal
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.identity import WorktreeIdentity, current_worktree
from wtenv.locks import WORKTREE_LOCK_TIMEOUT, registry_lock, worktree_lock
from wtenv.output import (
    DownResult,
    ErrorInfo,
    FailedItem,
    Item,
    ItemKind,
    ResourceState,
    WarningCode,
    WarningInfo,
)
from wtenv.registry import DatabaseRecord, WorktreeEntry

ItemT = TypeVar("ItemT", bound=Item)


@dataclass
class Release:
    """What releasing one entry did, or, for a plan, would do: the four lists of `DownResult`.

    `released` is whether the entry is gone from the registry, which happens only when no item
    failed. Every item names the worktree it belongs to (`Item.worktree`).
    """

    removed: list[Item] = field(default_factory=list)
    already_absent: list[Item] = field(default_factory=list)
    failed: list[FailedItem] = field(default_factory=list)
    released: bool = False

    def add(self, removal: Removal) -> None:
        """Take over the items of a database or compose removal."""
        self.removed += removal.removed
        self.already_absent += removal.already_absent
        self.failed += removal.failed


def plan_release(entry: WorktreeEntry, *, password: str | None = None) -> Release:
    """Return the items releasing `entry` would remove, changing nothing and taking no lock.

    It is `release_entry` with `dry_run` set: the same steps, in the same order, ask the same
    questions of the disk, the Postgres server, and Docker, and write nothing (FR-040).
    """
    return _release(entry, password=password, dry_run=True)


def release_entry(entry: WorktreeEntry, *, password: str | None = None) -> Release:
    """Release everything `entry` records, and drop the entry when nothing is left (FR-038).

    The caller holds the entry's worktree lock. `password` is for the Postgres drop; with none,
    libpq looks in `PGPASSWORD`, `PGPASSFILE`, and `~/.pgpass`. An item that cannot be removed is
    in `failed`, stays recorded, and keeps the entry, as `incomplete`; running this again finishes
    the job (FR-042). Removes only what the entry records (FR-039).
    """
    return _release(entry, password=password, dry_run=False)


def _release(entry: WorktreeEntry, *, password: str | None, dry_run: bool) -> Release:
    """Run the steps of cli.md, `wtenv down`, for `entry`; with `dry_run`, write nothing."""
    root = Path(entry.path)
    release = Release()
    if not dry_run:
        _update(entry.git_dir, _mark_incomplete)
    _compose_step(entry, root, release, dry_run)
    _database_step(entry, root, release, password, dry_run)
    _env_step(entry, root, release, dry_run)
    if not release.failed:
        _finish(entry, release, dry_run)
    release.removed = [_for_worktree(item, entry) for item in release.removed]
    release.already_absent = [_for_worktree(item, entry) for item in release.already_absent]
    release.failed = [_for_worktree(item, entry) for item in release.failed]
    return release


def _mark_incomplete(entry: WorktreeEntry) -> None:
    entry.state = "incomplete"


def _for_worktree(item: ItemT, entry: WorktreeEntry) -> ItemT:
    """Return `item` with the recorded path of the worktree it belongs to."""
    return item.model_copy(update={"worktree": entry.path})


def _update(git_dir: str, change: Callable[[WorktreeEntry], None]) -> None:
    """Change the entry under the registry lock; the lock is held only for this."""
    with registry.transaction() as reg:
        change(reg.worktrees[git_dir])


# --- the compose project and its override (cli.md: first) ---------------------------------------


def _compose_step(entry: WorktreeEntry, root: Path, release: Release, dry_run: bool) -> None:
    """Remove the recorded project's resources, then its override file, then its record."""
    record = entry.compose
    if record is None:
        return
    from wtenv import compose  # only now: the module is not loaded without a compose project

    failures_before = len(release.failed)
    release.add(compose.remove_project(record.project, dry_run=dry_run))
    project_failed = len(release.failed) > failures_before
    override = root / record.override
    item = Item(kind=ItemKind.COMPOSE_OVERRIDE, name=str(override))
    if not os.path.lexists(override):
        release.already_absent.append(item)
    elif dry_run:
        release.removed.append(item)
    else:
        if record.override_state is not ResourceState.REMOVING:
            _set_override_state(entry.git_dir, ResourceState.REMOVING)
        try:
            override.unlink()
        except OSError as error:
            reason = error.strerror or type(error).__name__
            release.failed.append(FailedItem(kind=item.kind, name=item.name, reason=reason))
            return
        release.removed.append(item)
    if not dry_run and not project_failed:
        _update(entry.git_dir, _drop_compose_record)


def _drop_compose_record(entry: WorktreeEntry) -> None:
    entry.compose = None


def _set_override_state(git_dir: str, state: ResourceState) -> None:
    def change(entry: WorktreeEntry) -> None:
        assert entry.compose is not None
        entry.compose = entry.compose.model_copy(update={"override_state": state})

    _update(git_dir, change)


# --- the databases (cli.md: second) --------------------------------------------------------------


def _database_step(
    entry: WorktreeEntry, root: Path, release: Release, password: str | None, dry_run: bool
) -> None:
    """Remove each recorded database: a SQLite copy and its side files, or a Postgres database."""
    for record in entry.databases:
        if not dry_run and record.state is not ResourceState.REMOVING:
            _set_database_state(entry.git_dir, record.kind, ResourceState.REMOVING)
        removal = _remove_database(record, root, password, dry_run)
        release.add(removal)
        if not dry_run and not removal.failed:
            _drop_database_record(entry.git_dir, record.kind)


def _remove_database(
    record: DatabaseRecord, root: Path, password: str | None, dry_run: bool
) -> Removal:
    """Remove the one database `record` describes; only what it records is touched."""
    if record.kind == "sqlite":
        assert record.path is not None
        return database.remove_sqlite_copy(root / record.path, dry_run=dry_run)
    assert record.name is not None
    target = PostgresTarget(
        host=record.host or "localhost",
        port=record.port or database.POSTGRES_DEFAULT_PORT,
        user=record.user,
        password=password,
    )
    return database.remove_postgres_database(target, record.name, dry_run=dry_run)


def _set_database_state(git_dir: str, kind: str, state: ResourceState) -> None:
    def change(entry: WorktreeEntry) -> None:
        entry.databases = [
            d.model_copy(update={"state": state}) if d.kind == kind else d for d in entry.databases
        ]

    _update(git_dir, change)


def _drop_database_record(git_dir: str, kind: str) -> None:
    def change(entry: WorktreeEntry) -> None:
        entry.databases = [d for d in entry.databases if d.kind != kind]

    _update(git_dir, change)


# --- wtenv's section of the env file (cli.md: third) ---------------------------------------------


def _env_step(entry: WorktreeEntry, root: Path, release: Release, dry_run: bool) -> None:
    """Remove wtenv's section, and the file when wtenv made it and nothing else is in it."""
    record = entry.env_file
    if record is None:
        return
    path = root / record.path
    section = Item(kind=ItemKind.ENV_SECTION, name=str(path))
    if not dry_run and record.state is not ResourceState.REMOVING:
        _update(entry.git_dir, _mark_env_removing)
    try:
        removal = envfile.remove_section(
            path,
            created_file=record.created_file,
            added_newline=record.added_newline,
            dry_run=dry_run,
        )
    except WtenvError as error:
        # Damaged markers (FR-081), or a file that cannot be read: left as it is, and recorded.
        release.failed.append(
            FailedItem(kind=section.kind, name=section.name, reason=error.message)
        )
        return
    if removal.removed:
        release.removed.append(section)
    else:
        release.already_absent.append(section)
    if removal.deleted_file:
        release.removed.append(Item(kind=ItemKind.ENV_FILE, name=str(path)))
    if not dry_run:
        _update(entry.git_dir, _drop_env_record)


def _drop_env_record(entry: WorktreeEntry) -> None:
    entry.env_file = None


def _mark_env_removing(entry: WorktreeEntry) -> None:
    assert entry.env_file is not None
    entry.env_file = entry.env_file.model_copy(update={"state": ResourceState.REMOVING})


# --- the port block, the registry entry, the exclude entries (cli.md: last) ----------------------


def _finish(entry: WorktreeEntry, release: Release, dry_run: bool) -> None:
    """Release the block and the entry, and the exclude entries with the repository's last entry.

    The entry and its block are deleted in one transaction. The exclude block goes first, inside
    the same transaction and only when no other entry of the repository remains (FR-085): if the
    file cannot be changed, the entry stays and the item is `failed`.
    """
    exclude_path = Path(entry.repository) / "info" / "exclude"
    block = entry.block
    items = [
        Item(kind=ItemKind.PORT_BLOCK, name=f"{block.start}-{block.start + block.size - 1}"),
        Item(kind=ItemKind.REGISTRY_ENTRY, name=entry.git_dir),
    ]
    exclude_item = Item(kind=ItemKind.EXCLUDE_ENTRIES, name=str(exclude_path))
    try:
        if dry_run:
            with registry_lock():
                last = _is_last(registry.load().worktrees, entry)
                block_there = exclude.remove_block(exclude_path, dry_run=True) if last else None
        else:
            with registry.transaction() as reg:
                last = _is_last(reg.worktrees, entry)
                block_there = exclude.remove_block(exclude_path) if last else None
                reg.worktrees.pop(entry.git_dir, None)
    except WtenvError as error:
        if error.code is not ErrorCode.UNSUPPORTED:
            raise  # a busy or unreadable registry is not an item
        release.failed.append(
            FailedItem(kind=exclude_item.kind, name=exclude_item.name, reason=error.message)
        )
        return
    release.removed += items
    if block_there is True:
        release.removed.append(exclude_item)
    elif block_there is False:
        release.already_absent.append(exclude_item)
    release.released = not dry_run


def _is_last(worktrees: dict[str, WorktreeEntry], entry: WorktreeEntry) -> bool:
    """Return whether no other registered worktree belongs to the entry's repository."""
    return not any(
        other.repository == entry.repository
        for git_dir, other in worktrees.items()
        if git_dir != entry.git_dir
    )


# --- `wtenv down` ----------------------------------------------------------------------------------


def down(
    cwd: str | Path | None = None,
    *,
    dry_run: bool = False,
    lock_timeout: float = WORKTREE_LOCK_TIMEOUT,
) -> DownResult:
    """Release the worktree that contains `cwd` (default: the current directory).

    A real run holds the worktree lock for its whole run, waiting up to `lock_timeout` seconds
    for it (`worktree_busy`). A dry run takes no worktree lock and changes nothing (FR-040,
    FR-076). A worktree with no registry entry is success with nothing removed (FR-043). A
    worktree that moved since the last `up` is recorded at its new location first, with the
    warning `worktree_moved` (FR-084). Items that cannot be removed make the result `ok` false
    with `partial_failure`; a dry run reports them under `failed` but is still `ok`.
    """
    identity = current_worktree(cwd)
    if dry_run:
        return _down(identity, dry_run=True)
    with worktree_lock(identity.git_dir, lock_timeout):
        return _down(identity, dry_run=False)


def _down(identity: WorktreeIdentity, *, dry_run: bool) -> DownResult:
    """Run `down` for `identity`, whose worktree lock is held unless this is a dry run."""
    with registry_lock():
        entry = registry.load().worktrees.get(identity.git_dir)
    if entry is None:
        return DownResult(ok=True, dry_run=dry_run, worktree_path=identity.path)
    warnings: list[WarningInfo] = []
    if entry.path != identity.path:
        warnings.append(_moved(entry.path, identity.path))
        entry = _at_new_location(entry, identity.path, dry_run)
    password, config_warnings = _password(entry)
    warnings += config_warnings
    release = (
        plan_release(entry, password=password)
        if dry_run
        else release_entry(entry, password=password)
    )
    return _result(identity, release, warnings, dry_run)


def _moved(old: str, new: str) -> WarningInfo:
    return WarningInfo(
        code=WarningCode.WORKTREE_MOVED,
        message=f"the worktree moved from {old} to {new}; its resources are released from there",
        details={"from": old, "to": new},
    )


def _at_new_location(entry: WorktreeEntry, path: str, dry_run: bool) -> WorktreeEntry:
    """Return `entry` at `path`; a real run records the new location first (FR-084)."""
    if dry_run:
        return entry.model_copy(update={"path": path})
    with registry.transaction() as reg:
        recorded = reg.worktrees[entry.git_dir]
        recorded.path = path
        return recorded.model_copy(deep=True)


def _password(entry: WorktreeEntry) -> tuple[str | None, list[WarningInfo]]:
    """Return the Postgres password `wtenv.toml` gives for the drop, and any `config_ignored`.

    Teardown does not need `wtenv.toml` (FR-044): one that is missing gives nothing, and one that
    is invalid, or whose URL cannot be resolved, is ignored with a warning. Without a password
    here, libpq looks in `PGPASSWORD`, `PGPASSFILE`, and `~/.pgpass`. Only the password is taken
    from the file: the server is the one the registry records.
    """
    try:
        config = load_config(entry.path)
    except WtenvError as error:
        return None, [_config_ignored(error.message)]
    record = next((d for d in entry.databases if d.kind == "postgres"), None)
    settings = config.database
    if record is None or settings is None or settings.type != "postgres":
        return None, []
    try:
        url = database.resolve_url(settings.url, name=record.name)
        return database.postgres_target(url).password, []
    except WtenvError as error:
        return None, [_config_ignored(error.message)]


def _config_ignored(why: str) -> WarningInfo:
    return WarningInfo(
        code=WarningCode.CONFIG_IGNORED,
        message=f"wtenv.toml was ignored, because teardown uses only the registry: {why}",
    )


def _result(
    identity: WorktreeIdentity, release: Release, warnings: list[WarningInfo], dry_run: bool
) -> DownResult:
    """Build the `DownResult`: `would_remove` for a dry run, `removed` for a real one."""
    error = None
    if release.failed and not dry_run:
        error = ErrorInfo(
            code=ErrorCode.PARTIAL_FAILURE,
            exit_status=EXIT_STATUS[ErrorCode.PARTIAL_FAILURE],
            message=f"{len(release.failed)} item(s) could not be removed and stay recorded",
            hint="Fix the cause in `failed[].reason`, then run `wtenv down` again.",
        )
    return DownResult(
        ok=error is None,
        error=error,
        warnings=warnings,
        dry_run=dry_run,
        worktree_path=identity.path,
        removed=[] if dry_run else release.removed,
        would_remove=release.removed if dry_run else [],
        already_absent=release.already_absent,
        failed=release.failed,
    )
