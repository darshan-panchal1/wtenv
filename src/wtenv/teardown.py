"""`wtenv down`: release everything the registry records for a worktree (FR-038 to FR-044).

Teardown depends only on the registry: it works when the worktree directory, its `wtenv.toml`, and
its env file are gone (FR-044). It removes only what the entry records, never anything found by a
matching name (FR-039). The order of work is that of contracts/cli.md, `wtenv down`: the compose
project (containers, networks, the volumes that carry its label, then the override file), the
databases, wtenv's section of the env file, the port block and the registry entry, and, with the
last registered worktree of the repository, wtenv's entries in `.git/info/exclude` (FR-085).

Nothing is written or deleted through a symbolic link (FR-086). Before the override file, the SQLite
copy, or the env section is touched, and before it is marked `removing`, its path is checked with
`identity.symlinked_part`. A link, or a link anywhere from the worktree root down to the path, makes
the item `failed` with the reason `symlink`: it stays recorded, the entry stays `incomplete`, and a
SQLite copy's side files are not even looked for. `down`, `gc`, `gc --release`, and every dry run go
through this one code path, so they list the same items.

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
import posixpath
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from wtenv import database, envfile, exclude, registry
from wtenv.config import load_config
from wtenv.database import PostgresTarget, Removal
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.identity import (
    WorktreeIdentity,
    current_worktree,
    points_to,
    recorded_path_problem,
    symlinked_part,
)
from wtenv.locks import WORKTREE_LOCK_TIMEOUT, registry_lock, worktree_lock
from wtenv.output import (
    DownResult,
    ErrorInfo,
    FailedItem,
    Item,
    ItemKind,
    KeptVolume,
    ResourceState,
    WarningCode,
    WarningInfo,
)
from wtenv.registry import DatabaseRecord, WorktreeEntry

ItemT = TypeVar("ItemT", bound=Item)

# The `reason` of an item left alone because it, or a directory above it, is a symbolic link
# (FR-086).
SYMLINK_REASON = "symlink"
# The `reason` of an item left alone because a worktree has appeared at the recorded path (L4).
WORKTREE_EXISTS_REASON = "worktree_exists"

# Where `up` puts a SQLite copy: exactly `.wtenv/<file name>` (files.md, Names).
SQLITE_DIRECTORY = ".wtenv"


@dataclass
class Release:
    """What releasing one entry did, or, for a plan, would do: the lists of `DownResult`.

    `released` is whether the entry is gone from the registry, which happens only when no item
    failed. Every item names the worktree it belongs to (`Item.worktree`). `kept_volumes` are the
    volumes of the compose project that were found and not removed, with or without a dry run.
    """

    removed: list[Item] = field(default_factory=list)
    already_absent: list[Item] = field(default_factory=list)
    failed: list[FailedItem] = field(default_factory=list)
    kept_volumes: list[KeptVolume] = field(default_factory=list)
    released: bool = False

    def add(self, removal: Removal) -> None:
        """Take over the items of a database or compose removal."""
        self.removed += removal.removed
        self.already_absent += removal.already_absent
        self.failed += removal.failed
        self.kept_volumes += removal.kept_volumes


def plan_release(
    entry: WorktreeEntry, *, password: str | None = None, gone: Collection[str] = ()
) -> Release:
    """Return the items releasing `entry` would remove, changing nothing and taking no lock.

    It is `release_entry` with `dry_run` set: the same steps, in the same order, ask the same
    questions of the disk, the Postgres server, and Docker, and write nothing (FR-040). `gone` is
    the git directories of the entries a dry run of `gc` already plans to release completely: it
    counts them as gone, because a real run would have released them, when it decides whether
    `entry` is the last of its repository and so removes the exclude block (L1).
    """
    return _release(entry, password=password, dry_run=True, gone=gone)


def release_entry(
    entry: WorktreeEntry, *, password: str | None = None, recheck_path: bool = False
) -> Release:
    """Release everything `entry` records, and drop the entry when nothing is left (FR-038).

    The caller holds the entry's worktree lock. `password` is for the Postgres drop; with none,
    libpq looks in `PGPASSWORD`, `PGPASSFILE`, and `~/.pgpass`. An item that cannot be removed is
    in `failed`, stays recorded, and keeps the entry, as `incomplete`; running this again finishes
    the job (FR-042). Removes only what the entry records (FR-039).

    `gc` passes `recheck_path`: after the compose step, which can take a while, `points_to` is read
    again on the recorded path, and a worktree that has appeared there puts every file item under
    `failed` with the reason `worktree_exists` (L4). `down` does not: its root is the worktree it
    runs in.
    """
    return _release(entry, password=password, dry_run=False, recheck_path=recheck_path)


def _release(
    entry: WorktreeEntry,
    *,
    password: str | None,
    dry_run: bool,
    gone: Collection[str] = (),
    recheck_path: bool = False,
) -> Release:
    """Run the steps of cli.md, `wtenv down`, for `entry`; with `dry_run`, write nothing."""
    root = Path(entry.path)
    release = Release()
    if not dry_run:
        _update(entry.git_dir, _mark_incomplete)
    project_failed = _compose_step(entry, root, release, dry_run)
    blocked = _files_blocked_by(root, recheck_path=recheck_path)
    if project_failed is not None:
        _override_step(entry, root, release, dry_run, project_failed, blocked)
    _database_step(entry, root, release, password, dry_run, blocked)
    _env_step(entry, root, release, dry_run, blocked)
    if not release.failed:
        _finish(entry, release, dry_run, gone)
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


def _compose_step(entry: WorktreeEntry, root: Path, release: Release, dry_run: bool) -> bool | None:
    """Remove the resources of the recorded project.

    Returns None when there is no override to deal with next: no compose record, or a project
    that is not the entry's own, of which nothing is listed or removed and whose override and
    record are kept (H1). Otherwise returns whether removing the project failed.
    """
    record = entry.compose
    if record is None:
        return None
    from wtenv import compose  # only now: the module is not loaded without a compose project

    problem = compose.project_problem(record.project, entry.git_dir)
    if problem is not None:
        release.failed.append(
            FailedItem(kind=ItemKind.COMPOSE_PROJECT, name=record.project, reason=problem)
        )
        return None
    failures_before = len(release.failed)
    release.add(compose.remove_project(record.project, entry.git_dir, dry_run=dry_run))
    return len(release.failed) > failures_before


def _files_blocked_by(root: Path, *, recheck_path: bool) -> str | None:
    """Return the reason every file item must be left alone, or None when they may be touched.

    A root that does not resolve to itself has a symbolic link in it or above it: `symlink` (L5).
    The identity of a worktree is resolved first (FR-006), so only a recorded path that `gc` or
    `gc --release` uses can have changed since. With `recheck_path` (`gc`, after the compose step),
    a git directory that `points_to` now names on the recorded path means a worktree has appeared
    there: `worktree_exists` (L4).
    """
    if os.path.realpath(root) != str(root):
        return SYMLINK_REASON
    if recheck_path:
        live = points_to(root)
        if live is not None and os.path.isdir(live):
            return WORKTREE_EXISTS_REASON
    return None


def _override_step(
    entry: WorktreeEntry,
    root: Path,
    release: Release,
    dry_run: bool,
    project_failed: bool,
    blocked: str | None,
) -> None:
    """Remove the override file, then the compose record when nothing of it failed."""
    record = entry.compose
    assert record is not None
    from wtenv import compose

    override = root / record.override
    item = Item(kind=ItemKind.COMPOSE_OVERRIDE, name=str(override))
    form = _form_reason(record.override)
    if form is not None:
        release.failed.append(FailedItem(kind=item.kind, name=item.name, reason=form))
        return
    if blocked is not None:
        release.failed.append(FailedItem(kind=item.kind, name=item.name, reason=blocked))
        return
    if symlinked_part(root, record.override) is not None:
        release.failed.append(FailedItem(kind=item.kind, name=item.name, reason=SYMLINK_REASON))
        return
    not_wtenvs = compose.override_problem(root, record.override)
    if not_wtenvs is not None:
        release.failed.append(FailedItem(kind=item.kind, name=item.name, reason=not_wtenvs))
        return
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


def _form_reason(path: str) -> str | None:
    """Return the reason a recorded path is left alone because of its form, or None (H2).

    The registry is a file a person can edit, so a path is used only when it has the form `up`
    records (`identity.recorded_path_problem`). The check runs before the file is looked at.
    """
    problem = recorded_path_problem(path)
    if problem is None:
        return None
    return f"the recorded path {path!r} is {problem}, not a path wtenv records; it was left alone"


def _sqlite_form_reason(path: str) -> str | None:
    """Like `_form_reason`, and the copy must be exactly `.wtenv/<file name>`."""
    problem = _form_reason(path)
    if problem is not None:
        return problem
    directory, name = posixpath.split(path)
    if directory == SQLITE_DIRECTORY and name:
        return None
    return (
        f"the recorded path {path!r} is not `{SQLITE_DIRECTORY}/<file name>`, "
        "where wtenv puts a SQLite copy; it was left alone"
    )


def _drop_compose_record(entry: WorktreeEntry) -> None:
    entry.compose = None


def _set_override_state(git_dir: str, state: ResourceState) -> None:
    def change(entry: WorktreeEntry) -> None:
        assert entry.compose is not None
        entry.compose = entry.compose.model_copy(update={"override_state": state})

    _update(git_dir, change)


# --- the databases (cli.md: second) --------------------------------------------------------------


def _database_step(
    entry: WorktreeEntry,
    root: Path,
    release: Release,
    password: str | None,
    dry_run: bool,
    blocked: str | None,
) -> None:
    """Remove each recorded database: a SQLite copy and its side files, or a Postgres database."""
    for record in entry.databases:
        if record.kind == "sqlite":
            assert record.path is not None
            form = _sqlite_form_reason(record.path)
            if form is not None:
                # No side file is looked for, and the record is not marked `removing`.
                release.failed.append(
                    FailedItem(kind=ItemKind.SQLITE_FILE, name=str(root / record.path), reason=form)
                )
                continue
            if blocked is not None:
                release.failed.append(
                    FailedItem(
                        kind=ItemKind.SQLITE_FILE, name=str(root / record.path), reason=blocked
                    )
                )
                continue
            if symlinked_part(root, record.path) is not None:
                release.failed.append(
                    FailedItem(
                        kind=ItemKind.SQLITE_FILE,
                        name=str(root / record.path),
                        reason=SYMLINK_REASON,
                    )
                )
                continue  # not marked `removing`, and no side file is looked for
        if not dry_run and record.state is not ResourceState.REMOVING:
            _set_database_state(entry.git_dir, record.kind, ResourceState.REMOVING)
        removal = _remove_database(record, entry.git_dir, root, password, dry_run)
        release.add(removal)
        if not dry_run and not removal.failed:
            _drop_database_record(entry.git_dir, record.kind)


def _remove_database(
    record: DatabaseRecord, git_dir: str, root: Path, password: str | None, dry_run: bool
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
    return database.remove_postgres_database(target, record.name, git_dir, dry_run=dry_run)


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


def _env_step(
    entry: WorktreeEntry, root: Path, release: Release, dry_run: bool, blocked: str | None
) -> None:
    """Remove wtenv's section, and the file when wtenv made it and nothing else is in it."""
    record = entry.env_file
    if record is None:
        return
    path = root / record.path
    section = Item(kind=ItemKind.ENV_SECTION, name=str(path))
    form = _form_reason(record.path)
    if form is not None:
        release.failed.append(FailedItem(kind=section.kind, name=section.name, reason=form))
        return
    if blocked is not None:
        release.failed.append(FailedItem(kind=section.kind, name=section.name, reason=blocked))
        return
    if symlinked_part(root, record.path) is not None:
        release.failed.append(
            FailedItem(kind=section.kind, name=section.name, reason=SYMLINK_REASON)
        )
        return
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
    except OSError as error:
        # The file cannot be deleted or rewritten (M3): the same, with the path and the error.
        release.failed.append(
            FailedItem(kind=section.kind, name=section.name, reason=_os_reason(path, error))
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


def _os_reason(path: Path, error: OSError) -> str:
    """Say which file a step could not change and why, for the reason of a failed item."""
    return f"{path}: {error.strerror or type(error).__name__}"


class _ExcludeFailed(Exception):
    """The exclude file could not be changed; the message becomes the reason of a failed item."""


def _remove_exclude_block(path: Path, *, dry_run: bool) -> bool:
    """Remove wtenv's block from the exclude file; an `OSError` becomes `_ExcludeFailed`."""
    try:
        return exclude.remove_block(path, dry_run=dry_run)
    except OSError as error:
        raise _ExcludeFailed(_os_reason(path, error)) from error


def _drop_env_record(entry: WorktreeEntry) -> None:
    entry.env_file = None


def _mark_env_removing(entry: WorktreeEntry) -> None:
    assert entry.env_file is not None
    entry.env_file = entry.env_file.model_copy(update={"state": ResourceState.REMOVING})


# --- the port block, the registry entry, the exclude entries (cli.md: last) ----------------------


def _finish(
    entry: WorktreeEntry, release: Release, dry_run: bool, gone: Collection[str] = ()
) -> None:
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
                last = _is_last(registry.load().worktrees, entry, gone)
                block_there = _remove_exclude_block(exclude_path, dry_run=True) if last else None
        else:
            with registry.transaction() as reg:
                last = _is_last(reg.worktrees, entry)
                block_there = _remove_exclude_block(exclude_path, dry_run=False) if last else None
                reg.worktrees.pop(entry.git_dir, None)
    except _ExcludeFailed as error:
        # The transaction ended with an exception, so nothing was saved: the entry stays.
        release.failed.append(
            FailedItem(kind=exclude_item.kind, name=exclude_item.name, reason=str(error))
        )
        return
    except WtenvError as error:
        if error.code is ErrorCode.ENV_FILE_UNUSABLE and error.details.get("reason") == "symlink":
            # The exclude file, or `info/`, is a link (L6): not written through; the entry stays.
            reason = SYMLINK_REASON
        elif error.code is ErrorCode.UNSUPPORTED:
            reason = error.message
        else:
            raise  # a busy or unreadable registry is not an item
        release.failed.append(
            FailedItem(kind=exclude_item.kind, name=exclude_item.name, reason=reason)
        )
        return
    release.removed += items
    if block_there is True:
        release.removed.append(exclude_item)
    elif block_there is False:
        release.already_absent.append(exclude_item)
    release.released = not dry_run


def _is_last(
    worktrees: dict[str, WorktreeEntry], entry: WorktreeEntry, gone: Collection[str] = ()
) -> bool:
    """Return whether no other registered worktree belongs to the entry's repository.

    The entries in `gone` are not counted: a dry run has not released them, but would have.
    """
    return not any(
        other.repository == entry.repository
        for git_dir, other in worktrees.items()
        if git_dir != entry.git_dir and git_dir not in gone
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
        kept_volumes=release.kept_volumes,
    )
