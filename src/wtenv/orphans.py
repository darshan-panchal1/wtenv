"""Telling an entry's worktree apart from a missing one: `classify` (data-model.md, "Status and the
orphan checks"; FR-045, FR-072). And `wtenv gc`, which releases the entries whose worktrees git
confirms are gone (FR-045 to FR-047, FR-072 to FR-075, FR-077).

`ls`, `exec`, `doctor`, and `gc` use the one function `classify`, so they can never disagree about
a worktree. `classify` only reads: it changes nothing on disk and nothing in the entry.

`gc` releases an entry through `teardown`, exactly as `down` would. `teardown` is imported inside
the functions that use it, because `ls` imports this module and has to start fast (NFR-001).
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wtenv import registry
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.gitutil import WorktreeRecord, git_listing
from wtenv.identity import points_to
from wtenv.locks import registry_lock, try_worktree_lock
from wtenv.output import ErrorInfo, GcResult, KeptEntry, Status, UnverifiableReason
from wtenv.registry import WorktreeEntry

if TYPE_CHECKING:
    from wtenv.teardown import Release


@dataclass(frozen=True)
class Classification:
    """The status of one entry, and why it is unverifiable when it is."""

    status: Status
    reason: UnverifiableReason | None = None
    # Where git has the worktree now; set only with the reason `moved`, and only when a listed
    # path points to the entry's git directory.
    current_path: str | None = None


def classify(entry: WorktreeEntry, listing: Sequence[WorktreeRecord] | None) -> Classification:
    """Return the status of `entry`, given `listing`, the worktrees git reports for its repository.

    `listing` is the output of `git --git-dir=<repository> worktree list --porcelain`, parsed, or
    None when that failed (the repository is gone, or is not a repository). The rows of the table
    in data-model.md are checked in order.
    """
    if listing is None:
        return Classification(Status.UNVERIFIABLE, UnverifiableReason.REPOSITORY_NOT_FOUND)
    if points_to(entry.path) == entry.git_dir:
        status = Status.PROVISIONED if entry.state == "provisioned" else Status.INCOMPLETE
        return Classification(status)
    listed = [os.path.realpath(record.path) for record in listing]
    if os.path.isdir(entry.git_dir):
        # Git still has this worktree.
        if not os.path.lexists(entry.path) and entry.path in listed:
            return Classification(Status.UNVERIFIABLE, UnverifiableReason.GIT_STILL_LISTS)
        current = next((path for path in listed if points_to(path) == entry.git_dir), None)
        return Classification(Status.UNVERIFIABLE, UnverifiableReason.MOVED, current)
    # Git no longer has this worktree.
    if os.path.lexists(entry.path):
        return Classification(Status.UNVERIFIABLE, UnverifiableReason.PATH_EXISTS)
    if entry.path in listed:
        return Classification(Status.UNVERIFIABLE, UnverifiableReason.GIT_STILL_LISTS)
    return Classification(Status.ORPHANED)


# --- `wtenv gc` ------------------------------------------------------------------------------------


def gc(*, dry_run: bool = False) -> GcResult:
    """Release every entry whose worktree git confirms is gone, in every repository (FR-045).

    The registry is read once, and each repository is asked once for its worktrees. An `orphaned`
    entry is released as `down` would release it: its worktree lock is taken without waiting
    (held: `skipped_busy`, FR-077), and it is classified again with a fresh listing, holding the
    lock, and left alone unless it is still orphaned (FR-074). An `unverifiable` entry is `kept`
    with its reason (FR-072). Entries of existing worktrees are not touched and not listed
    (FR-046). A dry run takes no worktree lock, changes nothing, and lists what a real run would
    remove (FR-075).

    `gc` reads no `wtenv.toml`: a Postgres drop takes its password from the libpq sources (cli.md,
    `wtenv gc`, Credentials). No git command that changes a repository is run (FR-075).
    """
    from wtenv import teardown  # only now; see the module docstring

    result = GcResult(ok=True, dry_run=dry_run)
    listings: dict[str, list[WorktreeRecord] | None] = {}
    for entry in _entries():
        if entry.repository not in listings:
            listings[entry.repository] = git_listing(entry.repository)
        found = classify(entry, listings[entry.repository])
        if found.status is Status.UNVERIFIABLE:
            result.kept.append(_kept(entry, found))
        elif found.status is Status.ORPHANED:
            if dry_run:
                _add(result, entry, teardown.plan_release(entry))
            else:
                _release(result, entry, only_if_orphaned=True)
    return _finished(result)


def gc_release(paths: Sequence[str], *, dry_run: bool = False) -> GcResult:
    """Release the entries recorded at `paths`, even though they are unverifiable (FR-073).

    A path matches the entry recorded at it made absolute, else the one at its resolved path.
    Every path is checked before anything changes: an entry whose worktree still exists, at the
    path or, moved, at another, stops the command with `worktree_exists`. A path with no entry is
    reported under `no_entry`, so the command can be repeated. Each named entry is then released
    as `down` would release it, with the lock rule of plain `gc` (`skipped_busy`), after the check
    is repeated under the lock; a worktree that has appeared since stops the command at that
    entry with `worktree_exists` in `error`. Nothing else is swept. A dry run takes no worktree
    lock and changes nothing.
    """
    from wtenv import teardown  # only now; see the module docstring

    recorded: dict[str, list[WorktreeEntry]] = {}
    for entry in _entries():
        recorded.setdefault(entry.path, []).append(entry)
    result = GcResult(ok=True, dry_run=dry_run)
    named: dict[str, WorktreeEntry] = {}  # by git directory: each entry once
    for path in paths:
        # The path as given first: an entry whose recorded path is now a symbolic link is still
        # found, and its files are left alone by the symbolic-link rule of `down` (FR-086).
        entries = recorded.get(os.path.abspath(path)) or recorded.get(os.path.realpath(path), [])
        if not entries and path not in result.no_entry:
            result.no_entry.append(path)
        for entry in entries:
            _refuse_if_it_exists(entry)
            named[entry.git_dir] = entry
    for entry in named.values():
        if dry_run:
            _add(result, entry, teardown.plan_release(entry))
            continue
        try:
            _release(result, entry, only_if_orphaned=False)
        except WtenvError as error:
            if error.code is not ErrorCode.WORKTREE_EXISTS:
                raise
            # A worktree appeared after the step-1 check (reading R7): this entry is stopped with
            # nothing changed, and so is every entry after it. What was released before it stays
            # released and is reported.
            result.ok = False
            result.error = ErrorInfo(
                code=error.code,
                exit_status=EXIT_STATUS[error.code],
                message=error.message,
                hint=error.hint,
                details=error.details,
            )
            break
    return _finished(result)


def _entries() -> list[WorktreeEntry]:
    """Read the registry once, under its lock; return its entries in the order of their blocks."""
    with registry_lock():
        entries = list(registry.load().worktrees.values())
    return sorted(entries, key=lambda entry: entry.block.start)


def _entry(git_dir: str) -> WorktreeEntry | None:
    """Read one entry under the registry lock; None when the registry no longer holds it."""
    with registry_lock():
        return registry.load().worktrees.get(git_dir)


def _release(result: GcResult, entry: WorktreeEntry, *, only_if_orphaned: bool) -> None:
    """Release `entry` while holding its worktree lock, taken without waiting (FR-077).

    The entry is read again with the lock held, because another process may have changed it.
    With `only_if_orphaned` (plain `gc`) it is also classified again with a fresh listing, and
    released only if it is still orphaned (FR-074); if it is now unverifiable, it is `kept`.
    Otherwise (`--release`) the step-1 check is repeated on the re-read entry immediately before
    the delete (reading R7): a worktree that has appeared raises `worktree_exists`, and nothing of
    the entry is changed.
    """
    from wtenv import teardown  # only now; see the module docstring

    with try_worktree_lock(entry.git_dir) as locked:
        if not locked:
            result.skipped_busy.append(entry.path)
            return
        current = _entry(entry.git_dir)
        if current is None:
            return  # another process released it in the meantime
        if only_if_orphaned:
            again = classify(current, git_listing(current.repository))
            if again.status is Status.UNVERIFIABLE:
                result.kept.append(_kept(current, again))
            if again.status is not Status.ORPHANED:
                return
        else:
            _refuse_if_it_exists(current)
        _add(result, current, teardown.release_entry(current))


def _refuse_if_it_exists(entry: WorktreeEntry) -> None:
    """Raise `worktree_exists` when a worktree still exists at the entry's path, or it moved.

    A directory whose `.git` names a git directory that exists is a worktree, even when that git
    directory is not the entry's: the repository was moved and repaired, or another worktree took
    the path (reading R5). One whose `.git` names a git directory that no longer exists is not a
    worktree (reading R3), so its entry can be released. A `moved` entry is refused too, at the
    location git lists or else the one `<git_dir>/gitdir` names, if a `.git` file is there.
    """
    live = points_to(entry.path)
    if live is not None and os.path.isdir(live):
        raise WtenvError(
            ErrorCode.WORKTREE_EXISTS,
            f"the worktree at {entry.path} still exists",
            hint="Run `wtenv down` in that worktree instead.",
            details={"path": entry.path},
        )
    found = classify(entry, git_listing(entry.repository))
    if found.reason is UnverifiableReason.MOVED:
        # Where git lists it; failing that, where the git directory itself says the worktree is.
        current_path = found.current_path or _location_named_by_git_dir(entry)
        if current_path is not None:
            raise WtenvError(
                ErrorCode.WORKTREE_EXISTS,
                f"the worktree recorded at {entry.path} still exists at {current_path}",
                hint="Run `wtenv down` in that worktree instead.",
                details={"path": entry.path, "current_path": current_path},
            )


def _location_named_by_git_dir(entry: WorktreeEntry) -> str | None:
    """Return the directory of the `.git` file that `<git_dir>/gitdir` names, if that file exists.

    Git writes that file when it adds or moves a worktree. A `moved` entry that the listing gives
    no location for can still have a live worktree there (LOW-5, FR-073). A `gitdir` file that is
    missing, unreadable, or names no existing `.git` file gives None: nothing is known to be there.
    """
    try:
        with open(os.path.join(entry.git_dir, "gitdir"), encoding="utf-8") as file:
            named = file.readline().rstrip("\r\n")
    except (OSError, UnicodeDecodeError):
        return None
    dot_git = os.path.realpath(os.path.join(entry.git_dir, named))
    if not named or not os.path.isfile(dot_git):
        return None
    return os.path.dirname(dot_git)


def _kept(entry: WorktreeEntry, found: Classification) -> KeptEntry:
    """Describe an unverifiable entry that `gc` leaves alone (FR-072)."""
    assert found.reason is not None
    return KeptEntry(
        path=entry.path,
        git_dir=entry.git_dir,
        reason=found.reason,
        current_path=found.current_path,
    )


def _add(result: GcResult, entry: WorktreeEntry, release: "Release") -> None:
    """Add what releasing `entry` did, or for a dry run would do, to `result`."""
    if result.dry_run:
        result.would_remove += release.removed
        if not release.failed:
            result.would_release.append(entry.path)
    else:
        result.removed += release.removed
        if release.released:
            result.released.append(entry.path)
    result.already_absent += release.already_absent
    result.failed += release.failed
    result.kept_volumes += release.kept_volumes


def _finished(result: GcResult) -> GcResult:
    """Make a real run that could not remove an item fail with `partial_failure` (exit 13).

    The failed items stay recorded; running `gc` again finishes the job (FR-042). A dry run
    reports them under `failed`, but is still `ok`, as for `down`.
    """
    if result.failed and not result.dry_run and result.error is None:
        result.ok = False
        result.error = ErrorInfo(
            code=ErrorCode.PARTIAL_FAILURE,
            exit_status=EXIT_STATUS[ErrorCode.PARTIAL_FAILURE],
            message=f"{len(result.failed)} item(s) could not be removed and stay recorded",
            hint="Fix the cause in `failed[].reason`, then run the same `wtenv gc` again.",
        )
    return result
