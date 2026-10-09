"""The views of `wtenv ls`: every registry entry with its status, and the worktrees of the current
repository that have no entry (data-model.md, "Status and the orphan checks"; FR-048 to FR-050).

It only reads. The registry lock is held just long enough to read the file, no worktree lock is
taken (FR-076), and nothing is written. `up` describes the worktree it provisioned with the same
views, so the two commands show a worktree the same way.
"""

import os
from collections.abc import Sequence
from pathlib import Path

from wtenv import registry
from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import WorktreeRecord, git_listing
from wtenv.identity import current_worktree, points_to
from wtenv.locks import registry_lock
from wtenv.orphans import Classification, classify
from wtenv.output import BlockView, DatabaseView, LsResult, PortView, Status, WorktreeView
from wtenv.registry import DatabaseRecord, PublishedPort, WorktreeEntry


def port_views(entry: WorktreeEntry) -> list[PortView]:
    """Describe the block's ports: each variable's port, then the published ports without one.

    A published port tied to a variable shows on that variable's view, with its service.
    """
    tied: dict[str, PublishedPort] = {}
    others: list[PublishedPort] = []
    for published in entry.published:
        if published.variable is not None and published.variable not in tied:
            tied[published.variable] = published
        else:
            others.append(published)
    views = []
    for assigned in entry.ports:
        match = tied.get(assigned.variable)
        views.append(
            PortView(
                port=assigned.port,
                variable=assigned.variable,
                service=None if match is None else match.service,
                target=None if match is None else match.target,
                protocol=None if match is None else match.protocol,
                host_ip=None if match is None else match.host_ip,
            )
        )
    for published in others:
        views.append(
            PortView(
                port=published.port,
                variable=published.variable,
                service=published.service,
                target=published.target,
                protocol=published.protocol,
                host_ip=published.host_ip,
            )
        )
    return views


def database_view(worktree_path: str, record: DatabaseRecord) -> DatabaseView:
    """Describe a recorded database: kind, name, host, and port, or the copy's path; no URL."""
    path = None if record.path is None else str(Path(worktree_path) / record.path)
    return DatabaseView(
        kind=record.kind,
        state=record.state,
        name=record.name,
        host=record.host,
        port=record.port,
        path=path,
    )


def worktree_view(entry: WorktreeEntry, classification: Classification) -> WorktreeView:
    """Return the row of `ls` for a registry entry, with the status `classify` gave it."""
    block = entry.block
    return WorktreeView(
        repository=entry.repository,
        git_dir=entry.git_dir,
        path=entry.path,
        status=classification.status,
        reason=classification.reason,
        current_path=classification.current_path,
        block=BlockView(start=block.start, end=block.start + block.size - 1, size=block.size),
        ports=port_views(entry),
        databases=[database_view(entry.path, record) for record in entry.databases],
        compose_project=None if entry.compose is None else entry.compose.project,
        env_file=None if entry.env_file is None else entry.env_file.path,
    )


def list_worktrees(cwd: str | Path | None = None) -> LsResult:
    """Return every registry entry with its status, and, when `cwd` is inside a repository, that
    repository's worktrees that have no entry, as `unprovisioned` (FR-048, FR-049).

    Entries come first, in the order of their port blocks. Raises `registry_busy` or
    `registry_unreadable`; it changes nothing (FR-050).
    """
    with registry_lock(create=False):
        entries = list(registry.load().worktrees.values())
    listings: dict[str, list[WorktreeRecord] | None] = {}
    for entry in entries:
        if entry.repository not in listings:
            listings[entry.repository] = git_listing(entry.repository)
    views = [
        worktree_view(entry, classify(entry, listings[entry.repository]))
        for entry in sorted(entries, key=lambda e: e.block.start)
    ]
    views += _unprovisioned(cwd, {entry.git_dir for entry in entries}, listings)
    return LsResult(ok=True, worktrees=views)


def _unprovisioned(
    cwd: str | Path | None,
    registered: set[str],
    listings: dict[str, list[WorktreeRecord] | None],
) -> list[WorktreeView]:
    """Return the worktrees of the repository around `cwd` that git lists, that exist on disk,
    and whose git directory is not a registry key. Outside a repository there are none."""
    try:
        identity = current_worktree(cwd)
    except WtenvError as error:
        if error.code is ErrorCode.NOT_IN_WORKTREE:
            return []
        raise
    if identity.repository not in listings:
        listings[identity.repository] = git_listing(identity.repository)
    return _without_entries(identity.repository, listings[identity.repository] or [], registered)


def _without_entries(
    repository: str, listing: Sequence[WorktreeRecord], registered: set[str]
) -> list[WorktreeView]:
    views = []
    for record in listing:
        path = os.path.realpath(record.path)
        git_dir = points_to(path)
        if record.bare or git_dir is None or git_dir in registered or not os.path.isdir(path):
            continue
        views.append(
            WorktreeView(
                repository=repository, git_dir=git_dir, path=path, status=Status.UNPROVISIONED
            )
        )
    return views
