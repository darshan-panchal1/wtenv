"""`wtenv up`: give the current worktree its port block, its env file section, and its database.

The order of work is that of contracts/cli.md, `wtenv up`. Everything that needs no resource is
checked first, so a failure there changes nothing (steps 1 to 6). Then the registry is written
before anything it records is created (data-model.md, "Write order of `up`"): an interruption at
any point leaves only resources the registry knows about, and the next `up` finishes the work
from the recorded states (FR-067, FR-069).

Steps that other modules perform are called through their module (`registry.transaction`,
`exclude.add_patterns`, `envfile.write_section`, `database.create_sqlite_copy`), so the recovery
tests can stop `up` right after any one of them.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from wtenv import database, envfile, exclude, ports, registry
from wtenv.config import CONFIG_FILE_NAME, Config, load_config
from wtenv.database import PostgresTarget
from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import git_path, is_tracked
from wtenv.identity import WorktreeIdentity, current_worktree
from wtenv.locks import WORKTREE_LOCK_TIMEOUT, registry_lock, worktree_lock
from wtenv.output import (
    BlockView,
    DatabaseView,
    Item,
    ItemKind,
    PortView,
    PostUpRun,
    ResourceState,
    Status,
    UpChange,
    UpResult,
    WarningCode,
    WarningInfo,
    WorktreeView,
)
from wtenv.registry import (
    DatabaseRecord,
    EnvFileRecord,
    PortBlock,
    Registry,
    VariablePort,
    WorktreeEntry,
)

Action = Literal["created", "updated", "unchanged", "released"]
DatabaseKind = Literal["postgres", "sqlite"]

DATABASE_URL = "DATABASE_URL"  # the variable the database step writes (config.md)
SQLITE_EXCLUDE_PATTERN = f"/{database.SQLITE_DIR}/"
# What a Postgres `CREATE` can answer that proves nothing was created, so its record goes again.
_CREATE_REFUSED = (
    ErrorCode.TEMPLATE_IN_USE,
    ErrorCode.TEMPLATE_MISSING,
    ErrorCode.OWNERSHIP_CONFLICT,
)


@dataclass(frozen=True)
class _DatabasePlan:
    """What the database step needs, worked out before anything changes (step 7)."""

    kind: DatabaseKind
    # The value of `DATABASE_URL`. It can hold a password: it goes to the env file and the
    # post-up commands, and is never printed or recorded (FR-019).
    url: str = field(repr=False)
    name: str | None = None  # Postgres: the recorded name, or the name to create
    path: str | None = None  # SQLite: the copy, relative to the worktree root
    target: PostgresTarget | None = None  # Postgres: wtenv's own connection


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
    plan = _database_plan(root, identity, config, known)  # step 7
    warnings = _warnings(identity, known, config, env_path)
    exclude_path = git_path(root, "info/exclude")

    if known is not None and _has_nothing_to_change(known, identity, config, section, plan):
        # Nothing is saved. The exclude block is put back if someone removed it, which does not
        # take the worktree out of `provisioned`.
        changed = _add_exclude_patterns(exclude_path, known.exclude_patterns)
        changes = [
            _block_change(known.block, "unchanged"),
            _exclude_change(exclude_path, changed=changed, first_run=False),
        ]
        if plan is not None:
            changes.append(_database_change(root, plan, "unchanged"))
        changes.append(_env_change(env_path, "unchanged"))
        runs: list[PostUpRun] = []
        return _result(identity, known, config.env_file, changes, warnings, runs)

    # Step 8: the registry first.
    with registry.transaction() as reg:
        before = reg.worktrees.get(identity.git_dir)
        after = _registry_step(reg, identity, config, before, section is not None)
    changes = _block_changes(before, after)

    # Step 9: the exclude block, with lines the entry already records.
    changed = _add_exclude_patterns(exclude_path, after.exclude_patterns)
    changes.append(_exclude_change(exclude_path, changed=changed, first_run=before is None))

    # Step 10: the database.
    changes += _database_step(root, config, plan, after)

    # Step 12: the env file.
    variables = _variables(after.ports, plan)
    changes += _env_file_step(root, config, before, after, variables)

    runs = []

    # Step 14: the entry is `provisioned` only now.
    with registry.transaction() as reg:
        final = reg.worktrees[identity.git_dir]
        final.state = "provisioned"
    return _result(identity, final, config.env_file, changes, warnings, runs)


def _variables(assigned: list[VariablePort], plan: _DatabasePlan | None) -> list[tuple[str, str]]:
    """Return the variables of wtenv's section: the ports in order, then `DATABASE_URL` (files.md)."""
    variables = [(port.variable, str(port.port)) for port in assigned]
    if plan is not None:
        variables.append((DATABASE_URL, plan.url))
    return variables


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


def _record_of(entry: WorktreeEntry | None, kind: DatabaseKind) -> DatabaseRecord | None:
    """Return the entry's database record of `kind`, if it has one."""
    if entry is None:
        return None
    return next((record for record in entry.databases if record.kind == kind), None)


def _database_plan(
    root: Path, identity: WorktreeIdentity, config: Config, known: WorktreeEntry | None
) -> _DatabasePlan | None:
    """Step 7: resolve the URL pattern and check that it names a local host.

    Returns None without `[database]`. Raises `config_invalid` for an unset `{env:NAME}`
    variable, a URL the env file cannot carry, or a Postgres host that is not local (FR-025).
    A database that is recorded keeps its name or path, so the URL follows the database.
    """
    settings = config.database
    if settings is None:
        return None
    config_file = str(root / CONFIG_FILE_NAME)
    record = _record_of(known, settings.type)
    if settings.type == "sqlite":
        relative = (
            record.path
            if record is not None and record.path is not None
            else f"{database.SQLITE_DIR}/{Path(settings.template).name}"
        )
        url = database.resolve_url(settings.url, path=str(root / relative), config_file=config_file)
        return _DatabasePlan(kind="sqlite", url=url, path=relative)
    name = (
        record.name
        if record is not None and record.name is not None
        else database.postgres_database_name(Path(identity.path).name, identity.git_dir)
    )
    url = database.resolve_url(settings.url, name=name, config_file=config_file)
    target = database.postgres_target(url, config_file=config_file)
    return _DatabasePlan(kind="postgres", url=url, name=name, target=target)


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
    plan: _DatabasePlan | None,
) -> bool:
    """Return whether a repeat `up` finds everything as it should be (data-model.md, Entry states).

    The block, the ports, the env-file record, the exclude patterns, and the configured
    database are as recorded, the worktree is where it was, and the env file's section holds the
    values the ports and the database call for.
    """
    if known.block.size != config.block_size:
        return False
    assigned = ports.assign_variable_ports(config.ports, known.block)
    record = known.env_file
    return _database_is_as_recorded(known, plan) and (
        known.state == "provisioned"
        and known.path == identity.path
        and known.ports == assigned
        and record is not None
        and record.path == config.env_file
        and record.state is ResourceState.CREATED
        and f"/{config.env_file}" in known.exclude_patterns
        and section == _variables(assigned, plan)
    )


def _database_is_as_recorded(known: WorktreeEntry, plan: _DatabasePlan | None) -> bool:
    """Return whether the configured database is recorded `created`, and a SQLite copy is there.

    A Postgres database is not looked for: a repeat `up` does not need the server, and a
    database that has gone missing is `wtenv doctor`'s to report.
    """
    if plan is None:
        return True
    record = _record_of(known, plan.kind)
    if record is None or record.state is not ResourceState.CREATED:
        return False
    if plan.kind == "sqlite":
        return SQLITE_EXCLUDE_PATTERN in known.exclude_patterns and _copy_exists(known, record)
    return True


def _copy_exists(known: WorktreeEntry, record: DatabaseRecord) -> bool:
    assert record.path is not None
    return (Path(known.path) / record.path).exists()


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
    if config.database is not None and config.database.type == "sqlite":
        patterns.add(SQLITE_EXCLUDE_PATTERN)  # saved here, before the exclude block is written
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
    root: Path,
    config: Config,
    before: WorktreeEntry | None,
    after: WorktreeEntry,
    variables: list[tuple[str, str]],
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
    written = envfile.write_section(env_path, variables)
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


# --- step 10: the database ---------------------------------------------------------------------


def _database_step(
    root: Path, config: Config, plan: _DatabasePlan | None, entry: WorktreeEntry
) -> list[UpChange]:
    """Create the configured database if it is not recorded; keep it if it is (FR-023).

    `up` never removes a database, whatever state it is in (data-model.md, Resource states). A
    database of another kind than the configured one stays recorded and untouched (FR-065).
    """
    settings = config.database
    if plan is None or settings is None:
        return []
    if plan.kind == "sqlite":
        template = database.sqlite_template_path(root, settings.template)
        return _sqlite_step(root, template, plan, entry)
    return _postgres_step(settings.template, plan, entry)


def _sqlite_step(
    root: Path, template: Path, plan: _DatabasePlan, entry: WorktreeEntry
) -> list[UpChange]:
    """Copy the template for a worktree that has no recorded copy, or whose copy has gone."""
    assert plan.path is not None
    target = root / plan.path
    created = [_database_change(root, plan, "created")]
    record = _record_of(entry, "sqlite")
    if record is not None:
        if target.exists():
            if record.state is ResourceState.CREATED:
                return [_database_change(root, plan, "unchanged")]
            # An interrupted run made it: the copy appears whole or not at all.
            _set_database_state(entry.git_dir, "sqlite", ResourceState.CREATED)
            return created
        # Recorded, but gone: copy it again.
        database.check_sqlite_template(template)
        database.create_sqlite_copy(template, target)
        _set_database_state(entry.git_dir, "sqlite", ResourceState.CREATED)
        return created
    database.check_sqlite_template(template)
    database.check_sqlite_target(target)
    _save_database_record(
        entry.git_dir, DatabaseRecord(kind="sqlite", path=plan.path, state=ResourceState.CREATING)
    )
    database.create_sqlite_copy(template, target)
    _set_database_state(entry.git_dir, "sqlite", ResourceState.CREATED)
    return created


def _postgres_step(template: str, plan: _DatabasePlan, entry: WorktreeEntry) -> list[UpChange]:
    """Create the worktree's Postgres database, recording it first (FR-067)."""
    assert plan.name is not None and plan.target is not None
    name, target = plan.name, plan.target
    item = Item(kind=ItemKind.POSTGRES_DATABASE, name=name)
    record = _record_of(entry, "postgres")
    if record is not None and record.state is ResourceState.CREATED:
        return [UpChange(item=item, action="unchanged")]
    state = database.inspect_postgres(target, template=template, name=name)
    if record is not None:
        if state.database_exists:
            # An interrupted run made it: the record was written before the server was asked.
            _set_database_state(entry.git_dir, "postgres", ResourceState.CREATED)
            return [UpChange(item=item, action="created")]
    else:
        database.check_creatable(state, template=template, name=name)
        _save_database_record(
            entry.git_dir,
            DatabaseRecord(
                kind="postgres",
                name=name,
                host=target.host,
                port=target.port,
                user=target.user,
                state=ResourceState.CREATING,
            ),
        )
    try:
        database.create_postgres_database(target, template=template, name=name)
    except WtenvError as error:
        if record is None and error.code in _CREATE_REFUSED:
            # The server refused, so nothing was created and nothing should stay recorded.
            _drop_database_record(entry.git_dir, "postgres")
        raise
    _set_database_state(entry.git_dir, "postgres", ResourceState.CREATED)
    return [UpChange(item=item, action="created")]


def _save_database_record(git_dir: str, record: DatabaseRecord) -> None:
    """Record `record`, replacing the record of its kind; the registry lock is held only here."""
    with registry.transaction() as reg:
        entry = reg.worktrees[git_dir]
        entry.databases = [d for d in entry.databases if d.kind != record.kind] + [record]


def _set_database_state(git_dir: str, kind: DatabaseKind, state: ResourceState) -> None:
    with registry.transaction() as reg:
        entry = reg.worktrees[git_dir]
        entry.databases = [
            d.model_copy(update={"state": state}) if d.kind == kind else d for d in entry.databases
        ]


def _drop_database_record(git_dir: str, kind: DatabaseKind) -> None:
    with registry.transaction() as reg:
        entry = reg.worktrees[git_dir]
        entry.databases = [d for d in entry.databases if d.kind != kind]


def _database_change(root: Path, plan: _DatabasePlan, action: Action) -> UpChange:
    """Report the database of `plan`: the name of a Postgres database, the path of a SQLite copy."""
    if plan.kind == "postgres":
        assert plan.name is not None
        return UpChange(item=Item(kind=ItemKind.POSTGRES_DATABASE, name=plan.name), action=action)
    assert plan.path is not None
    return UpChange(item=Item(kind=ItemKind.SQLITE_FILE, name=str(root / plan.path)), action=action)


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
    post_up: list[PostUpRun],
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
        databases=[_database_view(identity, record) for record in entry.databases],
        env_file=env_rel,
    )
    return UpResult(ok=True, worktree=view, changes=changes, warnings=warnings, post_up=post_up)


def _database_view(identity: WorktreeIdentity, record: DatabaseRecord) -> DatabaseView:
    """Describe a recorded database: kind, name, host, and port, or the copy's path; no URL."""
    path = None if record.path is None else str(Path(identity.path) / record.path)
    return DatabaseView(
        kind=record.kind,
        state=record.state,
        name=record.name,
        host=record.host,
        port=record.port,
        path=path,
    )
