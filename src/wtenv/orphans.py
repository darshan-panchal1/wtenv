"""Telling an entry's worktree apart from a missing one: `classify` (data-model.md, "Status and
the orphan checks"; FR-045, FR-072).

`ls`, `exec`, and `doctor` use this one function, so they can never disagree about a worktree.
It only reads: it changes nothing on disk and nothing in the entry.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass

from wtenv.gitutil import WorktreeRecord
from wtenv.identity import points_to
from wtenv.output import Status, UnverifiableReason
from wtenv.registry import WorktreeEntry


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
