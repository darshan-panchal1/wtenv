"""`wtenv up`: give the current worktree its port block, its env file section, its database, and
its compose project.

The order of work is that of contracts/cli.md, `wtenv up`. Everything that needs no resource is
checked first, so a failure there changes nothing (steps 1 to 6). Then the registry is written
before anything it records is created (data-model.md, "Write order of `up`"): an interruption at
any point leaves only resources the registry knows about, and the next `up` finishes the work
from the recorded states (FR-067, FR-069).

Steps that other modules perform are called through their module (`registry.transaction`,
`exclude.add_patterns`, `envfile.write_section`, `database.create_sqlite_copy`), so the recovery
tests can stop `up` right after any one of them. The compose module is imported only when
`[compose]` is configured or a compose project is recorded (research.md section 8).
"""

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from wtenv import database, envfile, exclude, ports, registry
from wtenv.config import CONFIG_FILE_NAME, Config, load_config
from wtenv.database import PostgresTarget
from wtenv.errors import ErrorCode, WtenvError
from wtenv.gitutil import git_path, is_tracked
from wtenv.identity import WorktreeIdentity, current_worktree, symlinked_part
from wtenv.listing import database_view, port_views
from wtenv.locks import WORKTREE_LOCK_TIMEOUT, registry_lock, worktree_lock
from wtenv.output import (
    BlockView,
    Item,
    ItemKind,
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
    PORT_RANGE_START,
    ComposeRecord,
    DatabaseRecord,
    EnvFileRecord,
    PortBlock,
    PublishedPort,
    Registry,
    VariablePort,
    WorktreeEntry,
)

if TYPE_CHECKING:
    from wtenv.compose import ComposeModel, PortMapping

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


@dataclass(frozen=True)
class _ComposePlan:
    """What the compose step needs, worked out before anything changes (step 5)."""

    file: str  # the compose file, relative to the worktree root
    override: str  # the override file beside it, relative to the worktree root
    project: str  # the recorded project name, or the one to record
    model: "ComposeModel"  # the compose file as Compose resolved it


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
    section = _read_section_or_none(env_path)  # also the markers check of step 4
    with registry_lock():
        known = registry.load().worktrees.get(identity.git_dir)
    _check_recorded_env_file(root, config, known)  # step 4, for a file `up` would remove
    _check_override_links(root, config, known)  # step 5, before the ownership check
    compose_plan = _compose_plan(root, identity, config, known)  # step 5
    _check_block_size(config, compose_plan)  # step 6
    plan = _database_plan(root, identity, config, known)  # step 7
    _check_sqlite_links(root, plan)  # step 7
    warnings = _warnings(identity, known, config, env_path, compose_plan)
    exclude_path = git_path(root, "info/exclude")

    if known is not None and _has_nothing_to_change(
        known, identity, config, section, plan, compose_plan
    ):
        # Nothing is saved. The exclude block is put back if someone removed it, which does not
        # take the worktree out of `provisioned`.
        changed = _add_exclude_patterns(exclude_path, known.exclude_patterns)
        changes = [
            _block_change(known.block, "unchanged"),
            _exclude_change(exclude_path, changed=changed, first_run=False),
        ]
        if plan is not None:
            changes.append(_database_change(root, plan, "unchanged"))
        if compose_plan is not None:
            changes.append(_compose_change(root, compose_plan, "unchanged"))
        changes.append(_env_change(env_path, "unchanged"))
        runs = _run_post_up(root, config.post_up, _variables(known.ports, plan), known.git_dir)
        return _result(identity, known, config.env_file, changes, warnings, runs)

    # Step 8: the registry first.
    with registry.transaction() as reg:
        before = reg.worktrees.get(identity.git_dir)
        after = _registry_step(reg, identity, config, before, section is not None, compose_plan)
    changes = _block_changes(before, after)

    # Step 9: the exclude block, with lines the entry already records.
    changed = _add_exclude_patterns(exclude_path, after.exclude_patterns)
    changes.append(_exclude_change(exclude_path, changed=changed, first_run=before is None))

    # Step 10: the database.
    changes += _database_step(root, config, plan, after)

    # Step 11: the compose override.
    changes += _compose_step(root, compose_plan, after)

    # Step 12: the env file.
    variables = _variables(after.ports, plan)
    changes += _env_file_step(root, config, before, after, variables)

    # Step 13: the post-up commands.
    runs = _run_post_up(root, config.post_up, variables, after.git_dir)

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


def _refuse_symlink(root: Path, relative: str) -> None:
    """Raise `env_file_unusable`, reason `symlink`, when `relative` is or sits under a link.

    The parts checked are those of `identity.symlinked_part`: every directory from the worktree
    root down to the path, and the path itself. `details.path` is the link found (FR-086).
    """
    link = symlinked_part(root, relative)
    if link is not None:
        raise envfile.unusable(link, "symlink")


def _check_env_file(root: Path, env_rel: str) -> None:
    """Raise `env_file_unusable` unless the env file can be written (FR-081, FR-018, FR-086).

    Damaged markers are found when the section is read, right after this.
    """
    path = root / env_rel
    _refuse_symlink(root, env_rel)
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


def _check_recorded_env_file(root: Path, config: Config, known: WorktreeEntry | None) -> None:
    """Raise `env_file_unusable` when a changed `env_file` would remove a section through a link.

    A recorded env file other than the configured one has its section removed by this `up`
    (FR-065), so its path gets the same check as the new one (FR-086).
    """
    if known is not None and known.env_file is not None and known.env_file.path != config.env_file:
        _refuse_symlink(root, known.env_file.path)


def _check_override_links(root: Path, config: Config, known: WorktreeEntry | None) -> None:
    """Raise `env_file_unusable` when an override file `up` would write or remove is a link.

    That is the override beside the configured compose file, and a recorded one that differs
    from it, or that `up` removes because `[compose]` is gone (FR-086). Without either, the
    compose module is not loaded (NFR-001).
    """
    recorded = None if known is None or known.compose is None else known.compose.override
    if config.compose is not None:
        from wtenv import compose  # only now: the module is not loaded without `[compose]`

        _refuse_symlink(root, compose.override_path(config.compose.file))
    if recorded is not None:
        _refuse_symlink(root, recorded)


def _check_sqlite_links(root: Path, plan: _DatabasePlan | None) -> None:
    """Raise `env_file_unusable` when `.wtenv/` or the SQLite copy is a link (FR-086)."""
    if plan is not None and plan.kind == "sqlite":
        assert plan.path is not None
        _refuse_symlink(root, plan.path)


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


def _compose_plan(
    root: Path, identity: WorktreeIdentity, config: Config, known: WorktreeEntry | None
) -> _ComposePlan | None:
    """Step 5: check the compose limits and have Compose resolve the compose file.

    Returns None without `[compose]`. Raises `dependency_unavailable` (Docker, or Compose older
    than 2.24.4, or a remote engine), `ownership_conflict` (an override file that is not wtenv's,
    or resources already labelled with the project name while the worktree records no project),
    `unsupported` (`COMPOSE_PROJECT_NAME` or `COMPOSE_FILE`, a published range), or
    `config_invalid` (Compose cannot resolve the file). Nothing is changed. The project name is
    the recorded one, and never changes (data-model.md, Compose record).
    """
    settings = config.compose
    if settings is None:
        return None
    from wtenv import compose  # only now: the module is not loaded without `[compose]`

    compose.check_docker()
    recorded = None if known is None or known.compose is None else known.compose
    if recorded is None:
        # Before anything is recorded, nothing may exist under the name (data-model.md, Resource
        # states): resources found there are not wtenv's, and `down` would remove them.
        generated = compose.project_name(Path(identity.path).name, identity.git_dir)
        compose.check_project_is_unused(generated)
    compose.check_override_files(
        root, settings.file, None if recorded is None else recorded.override
    )
    compose.check_environment(root, settings.file, os.environ)
    model = compose.resolve_model(root / settings.file, config.ports)
    return _ComposePlan(
        file=settings.file,
        override=compose.override_path(settings.file),
        project=(
            recorded.project
            if recorded is not None
            else compose.project_name(Path(identity.path).name, identity.git_dir)
        ),
        model=model,
    )


def _check_block_size(config: Config, compose_plan: _ComposePlan | None) -> None:
    """Step 6: check that the block holds every port variable and every published port.

    With compose, a stand-in block of the configured size is assigned to the published ports. That
    runs the same check as the real assignment, needs no block, and finds a port clash now
    (data-model.md, Port allocation: "Steps 2 to 5 run before anything is changed").
    """
    if compose_plan is None:
        ports.check_block_size(len(config.ports), config.block_size)
        return
    stand_in = PortBlock(start=PORT_RANGE_START, size=config.block_size)
    ports.assign_published_ports(compose_plan.model.mappings, config.ports, stand_in)


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
    identity: WorktreeIdentity,
    known: WorktreeEntry | None,
    config: Config,
    env_path: Path,
    compose_plan: _ComposePlan | None,
) -> list[WarningInfo]:
    """Return the warnings: a moved worktree (FR-084), duplicate variables (FR-080), fixed names."""
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
    if compose_plan is not None:
        warnings += compose_plan.model.warnings
    return warnings


def _has_nothing_to_change(
    known: WorktreeEntry,
    identity: WorktreeIdentity,
    config: Config,
    section: list[tuple[str, str]] | None,
    plan: _DatabasePlan | None,
    compose_plan: _ComposePlan | None,
) -> bool:
    """Return whether a repeat `up` finds everything as it should be (data-model.md, Entry states).

    The block, the ports, the env-file record, the exclude patterns, the configured database, and
    the compose override are as recorded, the worktree is where it was, and the env file's
    section holds the values the ports and the database call for.
    """
    if known.block.size != config.block_size:
        return False
    assigned = ports.assign_variable_ports(config.ports, known.block)
    record = known.env_file
    return (
        _database_is_as_recorded(known, plan)
        and _compose_is_as_recorded(known, compose_plan, config.ports)
        and (
            known.state == "provisioned"
            and known.path == identity.path
            and known.ports == assigned
            and record is not None
            and record.path == config.env_file
            and record.state is ResourceState.CREATED
            and f"/{config.env_file}" in known.exclude_patterns
            and section == _variables(assigned, plan)
        )
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


def _compose_is_as_recorded(
    known: WorktreeEntry, compose_plan: _ComposePlan | None, variables: list[str]
) -> bool:
    """Return whether the compose override is as it should be, so that nothing is to be done.

    With compose configured, the record, the published ports, and the exclude pattern are as
    the plan calls for, and the override file on disk holds exactly the text `up` would write.
    Without it, any override of wtenv's is gone and no published port is left.
    """
    record = known.compose
    root = Path(known.path)
    if compose_plan is None:
        return not known.published and (
            record is None
            or (
                record.override_state is ResourceState.REMOVING
                and not os.path.lexists(root / record.override)
            )
        )
    if (
        record is None
        or record.file != compose_plan.file
        or record.override != compose_plan.override
        or record.override_state is not ResourceState.CREATED
        or f"/{compose_plan.override}" not in known.exclude_patterns
    ):
        return False
    assigned = ports.assign_published_ports(compose_plan.model.mappings, variables, known.block)
    if known.published != assigned:
        return False
    from wtenv import compose

    pairs = _override_pairs(compose_plan.model, assigned)
    expected = compose.override_text(compose_plan.project, pairs)
    try:
        return (root / compose_plan.override).read_text(encoding="utf-8") == expected
    except OSError:
        return False


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
    compose_plan: _ComposePlan | None,
) -> WorktreeEntry:
    """Save the entry as `incomplete`, with its block, ports, patterns, and env-file record.

    With compose, it also records the published ports, the project, and the override file as
    `creating` (a moved compose file keeps the old override recorded as `removing`).

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
    if compose_plan is not None:
        patterns.add(f"/{compose_plan.override}")
    if before is not None:
        patterns |= set(before.exclude_patterns)
    entry = WorktreeEntry(
        git_dir=identity.git_dir,
        path=identity.path,
        repository=identity.repository,
        state="incomplete",
        block=block,
        ports=ports.assign_variable_ports(config.ports, block),
        published=_published(config, compose_plan, block),
        env_file=record,
        databases=[] if before is None else before.databases,
        compose=_compose_record(None if before is None else before.compose, compose_plan),
        exclude_patterns=sorted(patterns),
    )
    reg.worktrees[identity.git_dir] = entry
    return entry


def _published(
    config: Config, compose_plan: _ComposePlan | None, block: PortBlock
) -> list[PublishedPort]:
    """Return the host port of every published port in `block`; none without compose."""
    if compose_plan is None:
        return []
    return ports.assign_published_ports(compose_plan.model.mappings, config.ports, block)


def _compose_record(
    old: ComposeRecord | None, compose_plan: _ComposePlan | None
) -> ComposeRecord | None:
    """Return the compose record to save before the compose step runs.

    The project is recorded first and never changes. A new override is recorded as `creating`.
    When the compose file moved, the old override stays recorded, as `removing`, until the
    compose step has removed it. Without `[compose]`, the record stays until `down`.
    """
    if compose_plan is None:
        return old
    if old is None:
        return ComposeRecord(
            project=compose_plan.project,
            file=compose_plan.file,
            override=compose_plan.override,
            override_state=ResourceState.CREATING,
        )
    if old.override != compose_plan.override:
        return old.model_copy(update={"override_state": ResourceState.REMOVING})
    return old


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
            if record.state is ResourceState.REMOVING and state.database_invalid:
                raise _interrupted_removal(name)
            # An interrupted run made it (or an interrupted `down` left it whole): the record was
            # written before the server was asked.
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


def _interrupted_removal(name: str) -> WtenvError:
    """Build `unsupported` (`interrupted_removal`) for a database an interrupted drop half removed.

    The server marks such a database invalid and it cannot be used again; only `down` can finish
    removing it (data-model.md, Resource states).
    """
    return WtenvError(
        ErrorCode.UNSUPPORTED,
        f"the database {name} was half removed by an interrupted `wtenv down`, and cannot be used",
        hint="Run `wtenv down` to finish removing it, then run `wtenv up` again.",
        details={"reason": "interrupted_removal", "name": name},
    )


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


# --- step 11: the compose override ------------------------------------------------------------


def _override_pairs(
    model: "ComposeModel", assigned: list[PublishedPort]
) -> list[tuple["PortMapping", int]]:
    """Pair each port mapping of the compose model with its host port.

    `assigned` is in the order of `ports.published_order`, so the sorted mappings line up.
    """
    ordered = sorted(model.mappings, key=ports.published_order)
    return list(zip(ordered, (published.port for published in assigned), strict=True))


def _compose_step(
    root: Path, compose_plan: _ComposePlan | None, entry: WorktreeEntry
) -> list[UpChange]:
    """Write and verify the override, move it when the compose file moved, or remove it.

    The record was saved in step 8. Here the override is recorded as `creating` before it is
    written, and as `created` once it is verified. Without `[compose]`, wtenv's own override is
    removed and the project stays recorded until `down` (FR-065).
    """
    record = entry.compose
    if record is None:
        return []
    if compose_plan is None:
        return _remove_override(root, entry.git_dir, record)
    from wtenv import compose

    changes: list[UpChange] = []
    if record.override != compose_plan.override:
        # The compose file moved: the old override goes, the project stays.
        changes += _remove_override(root, entry.git_dir, record)
        record = ComposeRecord(
            project=record.project,
            file=compose_plan.file,
            override=compose_plan.override,
            override_state=ResourceState.CREATING,
        )
        _save_compose_record(entry.git_dir, record)
    pairs = _override_pairs(compose_plan.model, entry.published)
    text = compose.override_text(record.project, pairs)
    action = compose.write_and_verify_override(
        root, record.file, record.override, record.project, pairs, text
    )
    if record.override_state is not ResourceState.CREATED:
        _save_compose_record(
            entry.git_dir, record.model_copy(update={"override_state": ResourceState.CREATED})
        )
        action = "created"  # it is complete only now
    return [*changes, _compose_change(root, compose_plan, action)]


def _remove_override(root: Path, git_dir: str, record: ComposeRecord) -> list[UpChange]:
    """Remove wtenv's recorded override file, recording `removing` first; report it if it was there."""
    path = root / record.override
    if record.override_state is not ResourceState.REMOVING:
        _save_compose_record(
            git_dir, record.model_copy(update={"override_state": ResourceState.REMOVING})
        )
    if not os.path.lexists(path):
        return []
    path.unlink()
    item = Item(kind=ItemKind.COMPOSE_OVERRIDE, name=str(path))
    return [UpChange(item=item, action="released")]


def _save_compose_record(git_dir: str, record: ComposeRecord) -> None:
    """Record `record` as the entry's compose record; the registry lock is held only here."""
    with registry.transaction() as reg:
        reg.worktrees[git_dir].compose = record


def _compose_change(root: Path, compose_plan: _ComposePlan, action: Action) -> UpChange:
    item = Item(kind=ItemKind.COMPOSE_OVERRIDE, name=str(root / compose_plan.override))
    return UpChange(item=item, action=action)


# --- step 13: the post-up commands --------------------------------------------------------------


def _run_post_up(
    root: Path, commands: list[str], variables: list[tuple[str, str]], git_dir: str
) -> list[PostUpRun]:
    """Run each command through `sh -c`, in order, and stop at the first that fails (FR-036).

    They run in the worktree root with its variables added to the environment, with standard
    input closed and standard output sent to wtenv's standard error, so `--json` output stays one
    document. A failure makes the entry `incomplete` and raises `post_up_failed` (FR-037).
    """
    environment = {**os.environ, **dict(variables)}
    runs = []
    for command in commands:
        sys.stderr.flush()
        finished = subprocess.run(
            ["sh", "-c", command],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=sys.stderr,
            check=False,
        )
        if finished.returncode != 0:
            with registry.transaction() as reg:
                reg.worktrees[git_dir].state = "incomplete"
            raise WtenvError(
                ErrorCode.POST_UP_FAILED,
                f"the post-up command exited with status {finished.returncode}: {command}",
                hint="Fix the command, then run `wtenv up` again.",
                details={"command": command, "exit_status": finished.returncode},
            )
        runs.append(PostUpRun(command=command, exit_status=finished.returncode))
    return runs


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
        ports=port_views(entry),
        databases=[database_view(identity.path, record) for record in entry.databases],
        compose_project=None if entry.compose is None else entry.compose.project,
        env_file=env_rel,
    )
    return UpResult(ok=True, worktree=view, changes=changes, warnings=warnings, post_up=post_up)
