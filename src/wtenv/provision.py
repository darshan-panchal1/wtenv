"""`wtenv up`: give the current worktree its port block and its env file section.

The order of work is that of contracts/cli.md, `wtenv up`. Everything that needs no resource is
checked first, so a failure there changes nothing (steps 1 to 6). Then the registry is written
before anything it records is created (data-model.md, "Write order of `up`"): an interruption at
any point leaves only resources the registry knows about, and the next `up` finishes the work
from the recorded states (FR-067, FR-069).

Steps that other modules perform are called through their module (`registry.transaction`,
`exclude.add_patterns`, `envfile.write_section`), so the recovery tests can stop `up` right
after any one of them.
"""

import os
from pathlib import Path
from typing import Literal

from wtenv import envfile, exclude, ports, registry
from wtenv.config import Config, load_config
from wtenv.errors import WtenvError
from wtenv.gitutil import git_path, is_tracked
from wtenv.identity import WorktreeIdentity, current_worktree
from wtenv.locks import WORKTREE_LOCK_TIMEOUT, registry_lock, worktree_lock
from wtenv.output import (
    BlockView,
    Item,
    ItemKind,
    PortView,
    ResourceState,
    Status,
    UpChange,
    UpResult,
    WarningCode,
    WarningInfo,
    WorktreeView,
)
from wtenv.registry import EnvFileRecord, PortBlock, Registry, WorktreeEntry

Action = Literal["created", "updated", "unchanged", "released"]


def up(cwd: str | Path | None = None, *, lock_timeout: float = WORKTREE_LOCK_TIMEOUT) -> UpResult:
    """Provision the worktree that contains `cwd` (default: the current directory).

    Gives it a port block, assigns its port variables, and writes them to its env file. Running
    it again with nothing changed changes nothing (FR-011, FR-017). `lock_timeout` is how long to
    wait for the worktree lock before `worktree_busy`. Raises `WtenvError`; when a check fails,
    nothing has changed.
    """
    identity = current_worktree(cwd)  # step 1
    with worktree_lock(identity.git_dir, lock_timeout):  # step 2
        return _provision(identity)


def _provision(identity: WorktreeIdentity) -> UpResult:
    """Run `up` for `identity`, whose worktree lock is held."""
    root = Path(identity.path)
    config = load_config(root)  # step 3
    env_path = root / config.env_file
    _check_env_file(root, config.env_file)  # step 4
    ports.check_block_size(len(config.ports), config.block_size)  # step 6
    section = _read_section_or_none(env_path)  # also the markers check of step 4
    with registry_lock():
        known = registry.load().worktrees.get(identity.git_dir)
    warnings = _warnings(identity, known, config, env_path)
    exclude_path = git_path(root, "info/exclude")

    if known is not None and _has_nothing_to_change(known, identity, config, section):
        # Nothing is saved. The exclude block is put back if someone removed it, which does not
        # take the worktree out of `provisioned`.
        changed = _add_exclude_patterns(exclude_path, known.exclude_patterns)
        changes = [
            _block_change(known.block, "unchanged"),
            _exclude_change(exclude_path, changed=changed, first_run=False),
            _env_change(env_path, "unchanged"),
        ]
        return _result(identity, known, config.env_file, changes, warnings)

    # Step 8: the registry first.
    with registry.transaction() as reg:
        before = reg.worktrees.get(identity.git_dir)
        after = _registry_step(reg, identity, config, before, section is not None)
    changes = _block_changes(before, after)

    # Step 9: the exclude block, with lines the entry already records.
    changed = _add_exclude_patterns(exclude_path, after.exclude_patterns)
    changes.append(_exclude_change(exclude_path, changed=changed, first_run=before is None))

    # Step 12: the env file.
    changes += _env_file_step(root, config, before, after)

    # Step 14: the entry is `provisioned` only now.
    with registry.transaction() as reg:
        final = reg.worktrees[identity.git_dir]
        final.state = "provisioned"
    return _result(identity, final, config.env_file, changes, warnings)


# --- checks that change nothing (steps 4 and 6) -----------------------------------------------


def _check_env_file(root: Path, env_rel: str) -> None:
    """Raise `env_file_unusable` unless the env file can be written (FR-081, FR-018).

    Damaged markers are found when the section is read, right after this.
    """
    path = root / env_rel
    if not path.parent.is_dir():
        raise envfile.unusable(path, "parent_missing")
    if path.is_dir():
        raise envfile.unusable(path, "is_directory")
    if is_tracked(root, env_rel):
        raise envfile.unusable(path, "tracked_by_git")
    if path.exists():
        writable = os.access(path, os.R_OK | os.W_OK)
    else:
        writable = os.access(path.parent, os.W_OK | os.X_OK)
    if not writable:
        raise envfile.unusable(path, "not_writable")


def _read_section_or_none(path: Path) -> list[tuple[str, str]] | None:
    """Return wtenv's section of the env file, or None when there is no file or no section.

    Damaged markers raise `env_file_unusable`.
    """
    try:
        return envfile.read_section(path)
    except WtenvError as error:
        if error.details.get("reason") in ("missing", "no_section"):
            return None
        raise


def _warnings(
    identity: WorktreeIdentity, known: WorktreeEntry | None, config: Config, env_path: Path
) -> list[WarningInfo]:
    """Return the warnings of this run: a moved worktree (FR-084), duplicate variables (FR-080)."""
    warnings = []
    if known is not None and known.path != identity.path:
        warnings.append(
            WarningInfo(
                code=WarningCode.WORKTREE_MOVED,
                message=(
                    f"the worktree moved from {known.path} to {identity.path}; "
                    "its port block and resources are kept"
                ),
                details={"from": known.path, "to": identity.path},
            )
        )
    for name in envfile.find_duplicates(env_path, config.ports):
        warnings.append(
            WarningInfo(
                code=WarningCode.ENV_DUPLICATE_VARIABLE,
                message=(
                    f"{name} is also set outside wtenv's section of {config.env_file}; "
                    "wtenv leaves that line unchanged"
                ),
                details={"variable": name, "file": config.env_file},
            )
        )
    return warnings


def _has_nothing_to_change(
    known: WorktreeEntry,
    identity: WorktreeIdentity,
    config: Config,
    section: list[tuple[str, str]] | None,
) -> bool:
    """Return whether a repeat `up` finds everything as it should be (data-model.md, Entry states).

    The block, the ports, the env-file record, and the exclude pattern are as recorded, the
    worktree is where it was, and the env file's section holds the values the ports call for.
    """
    if known.block.size != config.block_size:
        return False
    assigned = ports.assign_variable_ports(config.ports, known.block)
    record = known.env_file
    return (
        known.state == "provisioned"
        and known.path == identity.path
        and known.ports == assigned
        and record is not None
        and record.path == config.env_file
        and record.state is ResourceState.CREATED
        and f"/{config.env_file}" in known.exclude_patterns
        and section == [(port.variable, str(port.port)) for port in assigned]
    )


# --- step 8: the registry ---------------------------------------------------------------------


def _registry_step(
    reg: Registry,
    identity: WorktreeIdentity,
    config: Config,
    before: WorktreeEntry | None,
    has_section: bool,
) -> WorktreeEntry:
    """Save the entry as `incomplete`, with its block, ports, patterns, and env-file record.

    Call it inside a registry transaction. The block is searched for here, so the search and
    the save share one lock (FR-013).
    """
    if before is not None and before.block.size == config.block_size:
        block = before.block
    else:
        # The block this worktree held before is replaced, so it does not count as taken.
        others = [e.block for git_dir, e in reg.worktrees.items() if git_dir != identity.git_dir]
        block = ports.find_block(config.block_size, others)
    old_record = None if before is None else before.env_file
    if old_record is not None and old_record.path != config.env_file:
        # `env_file` changed: keep the record of the old section until it is removed.
        record = old_record.model_copy(update={"state": ResourceState.REMOVING})
    else:
        record = _env_record(Path(identity.path), config.env_file, old_record, has_section)
    patterns = {f"/{config.env_file}"}
    if before is not None:
        patterns |= set(before.exclude_patterns)
    entry = WorktreeEntry(
        git_dir=identity.git_dir,
        path=identity.path,
        repository=identity.repository,
        state="incomplete",
        block=block,
        ports=ports.assign_variable_ports(config.ports, block),
        published=[] if before is None else before.published,
        env_file=record,
        databases=[] if before is None else before.databases,
        compose=None if before is None else before.compose,
        exclude_patterns=sorted(patterns),
    )
    reg.worktrees[identity.git_dir] = entry
    return entry


def _env_record(
    root: Path, env_rel: str, old: EnvFileRecord | None, has_section: bool
) -> EnvFileRecord:
    """Return the record of the env file, as it is saved before the file is written.

    What wtenv is about to do to the file, creating it or adding a line break, is observed now
    and recorded as `creating`, so an interruption after the write still leaves the facts that
    `down` needs (FR-067, FR-079). A record that is `created` already, or whose section an
    interrupted run wrote, keeps what it says.
    """
    if old is not None and old.path == env_rel:
        if old.state is ResourceState.CREATED:
            return old
        if has_section:
            return old.model_copy(update={"state": ResourceState.CREATING})
    try:
        content = (root / env_rel).read_bytes()
    except FileNotFoundError:
        return EnvFileRecord(
            path=env_rel, created_file=True, added_newline=False, state=ResourceState.CREATING
        )
    return EnvFileRecord(
        path=env_rel,
        created_file=False,
        added_newline=bool(content) and not content.endswith(b"\n"),
        state=ResourceState.CREATING,
    )


def _add_exclude_patterns(exclude_path: Path, patterns: list[str]) -> bool:
    """Add `patterns` to the exclude block under the registry lock; return whether it changed."""
    with registry_lock():
        return exclude.add_patterns(exclude_path, patterns)


# --- step 12: the env file ---------------------------------------------------------------------


def _env_file_step(
    root: Path, config: Config, before: WorktreeEntry | None, after: WorktreeEntry
) -> list[UpChange]:
    """Write the section, move it when `env_file` changed, and mark the record `created`."""
    changes = []
    env_path = root / config.env_file
    record = after.env_file
    assert record is not None  # `_registry_step` always records the env file
    old_record = None if before is None else before.env_file
    if old_record is not None and old_record.path != config.env_file:
        old_path = root / old_record.path
        envfile.remove_section(
            old_path, created_file=old_record.created_file, added_newline=old_record.added_newline
        )
        changes.append(_env_change(old_path, "released"))
        record = _env_record(root, config.env_file, None, has_section=False)
        with registry.transaction() as reg:
            reg.worktrees[after.git_dir].env_file = record
    written = envfile.write_section(env_path, [(p.variable, str(p.port)) for p in after.ports])
    created = EnvFileRecord(
        path=config.env_file,
        created_file=record.created_file or written.created_file,
        added_newline=record.added_newline or written.added_newline,
        state=ResourceState.CREATED,
    )
    if created != record:
        with registry.transaction() as reg:
            reg.worktrees[after.git_dir].env_file = created
    changes.append(_env_change(env_path, written.action))
    return changes


# --- the result --------------------------------------------------------------------------------


def _block_change(block: PortBlock, action: Action) -> UpChange:
    name = f"{block.start}-{block.start + block.size - 1}"
    return UpChange(item=Item(kind=ItemKind.PORT_BLOCK, name=name), action=action)


def _block_changes(before: WorktreeEntry | None, after: WorktreeEntry) -> list[UpChange]:
    """Report the block: new, kept, or replaced because `block_size` changed (FR-065)."""
    if before is None:
        return [_block_change(after.block, "created")]
    if before.block == after.block:
        return [_block_change(after.block, "unchanged")]
    return [_block_change(before.block, "released"), _block_change(after.block, "created")]


def _exclude_change(path: Path, *, changed: bool, first_run: bool) -> UpChange:
    item = Item(kind=ItemKind.EXCLUDE_ENTRIES, name=str(path))
    if not changed:
        return UpChange(item=item, action="unchanged")
    return UpChange(item=item, action="created" if first_run else "updated")


def _env_change(path: Path, action: Action) -> UpChange:
    return UpChange(item=Item(kind=ItemKind.ENV_SECTION, name=str(path)), action=action)


def _result(
    identity: WorktreeIdentity,
    entry: WorktreeEntry,
    env_rel: str,
    changes: list[UpChange],
    warnings: list[WarningInfo],
) -> UpResult:
    """Build the `UpResult` of a provisioned worktree."""
    block = entry.block
    view = WorktreeView(
        repository=identity.repository,
        git_dir=identity.git_dir,
        path=identity.path,
        status=Status.PROVISIONED,
        block=BlockView(start=block.start, end=block.start + block.size - 1, size=block.size),
        ports=[PortView(port=p.port, variable=p.variable) for p in entry.ports],
        env_file=env_rel,
    )
    return UpResult(ok=True, worktree=view, changes=changes, warnings=warnings)
