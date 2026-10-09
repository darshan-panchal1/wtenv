"""`wtenv doctor`: checks the registry, the ports, and the dependencies, and changes nothing
(cli.md, `wtenv doctor`; FR-060, FR-061).

Every check is a function of what it is given: the registry entries, the classification `classify`
made, a command runner for `lsof` and `docker`, the free-port test, and the Postgres lookups. The
tests pass fakes for each, so they need no process, no Docker, and no real port. `diagnose` puts
the checks together with the real ones.

`doctor` reads the registry under its lock, and takes no worktree lock (FR-076). It writes
nothing: no registry, no env file, no exclude file, no hook, and no database (FR-060).
"""

import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from wtenv import database, registry
from wtenv.compose import PROJECT_LABEL, Runner, check_docker, run_command
from wtenv.config import Config, load_config
from wtenv.database import PostgresTarget
from wtenv.envfile import read_section
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.gitutil import MIN_GIT_VERSION, WorktreeRecord, git_listing
from wtenv.identity import current_worktree
from wtenv.locks import registry_lock
from wtenv.orphans import Classification, classify
from wtenv.output import (
    DependencyStatus,
    DoctorResult,
    ErrorInfo,
    Finding,
    FindingCode,
    ResourceState,
    Status,
)
from wtenv.ports import port_is_free
from wtenv.registry import DatabaseRecord, WorktreeEntry

# Asks whether the server of a recorded Postgres database holds it. Raises `dependency_unavailable`
# when the server cannot be asked.
PostgresLookup = Callable[[WorktreeEntry, DatabaseRecord], bool]
# Raises `dependency_unavailable` unless the server of a Postgres target answers.
ServerCheck = Callable[[PostgresTarget], None]
IsFree = Callable[[int], bool]

_PUBLISHED_PORTS = re.compile(r":(\d+)(?:-(\d+))?->")


def _problem(
    code: FindingCode, message: str, worktree: str | None = None, **details: object
) -> Finding:
    return Finding.model_validate(
        {
            "code": code,
            "severity": "problem",
            "message": message,
            "worktree": worktree,
            "details": details,
        }
    )


def _info(
    code: FindingCode, message: str, worktree: str | None = None, **details: object
) -> Finding:
    return Finding.model_validate(
        {
            "code": code,
            "severity": "info",
            "message": message,
            "worktree": worktree,
            "details": details,
        }
    )


# --- the registry (FR-060a, c, d; FR-054) ------------------------------------------------------


def block_overlaps(entries: Sequence[WorktreeEntry]) -> list[Finding]:
    """Return one `block_overlap` finding for each pair of entries whose blocks share a port."""
    found = []
    ordered = sorted(entries, key=lambda entry: (entry.block.start, entry.path))
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            low = max(first.block.start, second.block.start)
            high = min(first.block.start + first.block.size, second.block.start + second.block.size)
            if low >= high:
                continue
            ports = f"{low}-{high - 1}"
            found.append(
                _problem(
                    FindingCode.BLOCK_OVERLAP,
                    f"the port blocks of {first.path} and {second.path} share the ports {ports}",
                    worktrees=sorted([first.path, second.path]),
                    ports=ports,
                )
            )
    return found


def status_finding(entry: WorktreeEntry, found: Classification) -> Finding | None:
    """Return the finding for an entry that is `orphaned`, `unverifiable`, or `incomplete`.

    A `provisioned` entry, or one with no entry at all (`unprovisioned`), has none.
    """
    if found.status is Status.ORPHANED:
        return _problem(
            FindingCode.ORPHANED_WORKTREE,
            f"git no longer has the worktree {entry.path}; `wtenv gc` would release its entry",
            entry.path,
        )
    if found.status is Status.UNVERIFIABLE:
        assert found.reason is not None
        details: dict[str, object] = {"reason": found.reason.value}
        if found.current_path is not None:
            details["current_path"] = found.current_path
        return _problem(
            FindingCode.UNVERIFIABLE_WORKTREE,
            f"the worktree {entry.path} cannot be found as recorded, and its removal is not "
            f"confirmed ({found.reason.value}); see `wtenv gc`",
            entry.path,
            **details,
        )
    if found.status is Status.INCOMPLETE:
        return _problem(
            FindingCode.INCOMPLETE_WORKTREE,
            f"an `up` or `down` of {entry.path} did not finish; run `wtenv up` or `wtenv down` there",
            entry.path,
        )
    return None


def missing_resources(entry: WorktreeEntry, *, postgres_has: PostgresLookup) -> list[Finding]:
    """Return a finding for each resource the registry records as created that is not there.

    Checked: the env file and its section, each SQLite copy, the compose override, and each
    Postgres database, on a server that answers. A server that cannot be asked gives a
    `resource_check_skipped` finding of severity info, not a problem. A resource that is being
    created or removed is not checked: that is what `incomplete` says. The worktree must exist.
    """
    root = Path(entry.path)
    found = []
    if entry.env_file is not None and entry.env_file.state is ResourceState.CREATED:
        found += _missing_env(entry, root / entry.env_file.path)
    for record in entry.databases:
        if record.state is not ResourceState.CREATED:
            continue
        if record.kind == "sqlite" and record.path is not None:
            copy = root / record.path
            if not os.path.lexists(copy):
                found.append(_missing(entry, "sqlite_file", str(copy)))
        elif record.kind == "postgres" and record.name is not None:
            found += _missing_database(entry, record, postgres_has)
    compose = entry.compose
    if compose is not None and compose.override_state is ResourceState.CREATED:
        override = root / compose.override
        if not os.path.lexists(override):
            found.append(_missing(entry, "compose_override", str(override)))
    return found


def _missing(entry: WorktreeEntry, kind: str, name: str) -> Finding:
    return _problem(
        FindingCode.MISSING_RESOURCE,
        f"the {kind.replace('_', ' ')} {name}, recorded for {entry.path}, no longer exists",
        entry.path,
        kind=kind,
        name=name,
    )


def _missing_env(entry: WorktreeEntry, path: Path) -> list[Finding]:
    """Report an env file that is gone, or one without wtenv's section. Damaged markers are not
    a resource that no longer exists, so they are not reported here."""
    try:
        read_section(path)
    except WtenvError as error:
        if error.details.get("reason") == "missing":
            return [_missing(entry, "env_file", str(path))]
        if error.details.get("reason") == "no_section":
            return [_missing(entry, "env_section", str(path))]
    return []


def _missing_database(
    entry: WorktreeEntry, record: DatabaseRecord, postgres_has: PostgresLookup
) -> list[Finding]:
    name = str(record.name)
    try:
        exists = postgres_has(entry, record)
    except WtenvError as error:
        return [
            _info(
                FindingCode.RESOURCE_CHECK_SKIPPED,
                f"cannot check whether the database {name} exists: {error.message}",
                entry.path,
                kind="postgres_database",
                name=name,
                reason=error.details.get("reason"),
            )
        ]
    return [] if exists else [_missing(entry, "postgres_database", name)]


def remember_unreachable(lookup: PostgresLookup) -> PostgresLookup:
    """Wrap `lookup` so that a server that failed once is not asked again for another entry.

    Each failed connection can take seconds; the answer would be the same.
    """
    failed: dict[tuple[str | None, int | None, str | None], WtenvError] = {}

    def asked(entry: WorktreeEntry, record: DatabaseRecord) -> bool:
        server = (record.host, record.port, record.user)
        if server in failed:
            raise failed[server]
        try:
            return lookup(entry, record)
        except WtenvError as error:
            failed[server] = error
            raise

    return asked


def server_has_database(entry: WorktreeEntry, record: DatabaseRecord) -> bool:
    """Ask the server a Postgres record names whether it holds the database. Changes nothing.

    The registry holds no password. As `down` does, it is taken from the worktree's `wtenv.toml`
    when that gives one, and otherwise libpq looks in `PGPASSWORD`, `PGPASSFILE`, and `~/.pgpass`.
    """
    target = PostgresTarget(
        host=record.host or "127.0.0.1",
        port=record.port or database.POSTGRES_DEFAULT_PORT,
        user=record.user,
        password=_recorded_password(entry, record),
    )
    return database.postgres_database_exists(target, str(record.name))


def _recorded_password(entry: WorktreeEntry, record: DatabaseRecord) -> str | None:
    """Return the password the worktree's `wtenv.toml` gives for its Postgres URL, if it gives one."""
    try:
        settings = load_config(entry.path).database
        if settings is None or settings.type != "postgres":
            return None
        url = database.resolve_url(settings.url, name=record.name)
        return database.postgres_target(url).password
    except WtenvError:
        return None


# --- the ports (FR-060b; research.md section 11) -----------------------------------------------


def port_findings(
    entry: WorktreeEntry, *, run: Runner, environ: Mapping[str, str], is_free: IsFree
) -> list[Finding]:
    """Return a finding for each assigned port of `entry` that is in use by something else.

    A busy port belongs to the worktree when a container of its compose project publishes it, or
    when the working directory of the process that listens on it, as `lsof` shows, lies inside the
    worktree. A listener working anywhere else is a `port_conflict` (problem). When `lsof` is
    missing, lists nobody, or cannot say where a listener works, the holder is unknown and the
    port is `port_in_use` (info).
    """
    assigned = {port.port for port in entry.ports} | {port.port for port in entry.published}
    busy = [port for port in sorted(assigned) if not is_free(port)]
    if not busy:
        return []
    published = _container_ports(entry, run, environ)
    found = []
    for port in busy:
        if any(port in published_range for published_range in published):
            continue
        finding = _holder_finding(entry, port, run, environ)
        if finding is not None:
            found.append(finding)
    return found


def _container_ports(entry: WorktreeEntry, run: Runner, environ: Mapping[str, str]) -> list[range]:
    """Return the host ports that the containers of the entry's compose project publish."""
    if entry.compose is None:
        return []
    command = [
        "docker",
        "ps",
        "--filter",
        f"label={PROJECT_LABEL}={entry.compose.project}",
        "--format",
        "{{.Ports}}",
    ]
    try:
        process = run(command, environ, None)
    except FileNotFoundError:
        return []
    if process.returncode != 0:
        return []
    ranges = []
    for first, last in _PUBLISHED_PORTS.findall(process.stdout):
        ranges.append(range(int(first), int(last or first) + 1))
    return ranges


class _Holder:
    """A process that listens on a port: its id, its command, and where it works (None: unknown)."""

    def __init__(self, pid: int, command: str, directory: str | None) -> None:
        self.pid = pid
        self.command = command
        self.directory = directory


def _holder_finding(
    entry: WorktreeEntry, port: int, run: Runner, environ: Mapping[str, str]
) -> Finding | None:
    holders = _listeners(port, run, environ)
    outside = [
        holder
        for holder in holders
        if holder.directory is not None and not _is_inside(holder.directory, entry.path)
    ]
    if outside:
        holder = outside[0]
        return _problem(
            FindingCode.PORT_CONFLICT,
            f"port {port} of {entry.path} is held by {holder.command} (pid {holder.pid}), which "
            f"works in {holder.directory}, outside the worktree",
            entry.path,
            port=port,
            pid=holder.pid,
            command=holder.command,
            directory=holder.directory,
        )
    if not holders or any(holder.directory is None for holder in holders):
        return _info(
            FindingCode.PORT_IN_USE,
            f"port {port} of {entry.path} is in use; the process that holds it cannot be found",
            entry.path,
            port=port,
        )
    return None  # every listener works inside the worktree


def _listeners(port: int, run: Runner, environ: Mapping[str, str]) -> list[_Holder]:
    """Return the processes that listen on `port`; none when `lsof` is missing or lists nobody."""
    command = ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"]
    try:
        process = run(command, environ, None)
    except FileNotFoundError:
        return []
    holders = []
    pid: int | None = None
    for line in process.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("c") and pid is not None:
            holders.append(_Holder(pid, line[1:], _working_directory(pid, run, environ)))
            pid = None
    return holders


def _working_directory(pid: int, run: Runner, environ: Mapping[str, str]) -> str | None:
    """Return the working directory of process `pid` as `lsof` shows it, or None."""
    try:
        process = run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], environ, None)
    except FileNotFoundError:
        return None
    for line in process.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def _is_inside(directory: str, root: str) -> bool:
    """Return whether `directory` is `root` or lies under it; `/a/app-x` is not inside `/a/app`."""
    return Path(os.path.realpath(directory)).is_relative_to(os.path.realpath(root))


# --- the dependencies (FR-060e) ----------------------------------------------------------------


def git_status(run: Runner, environ: Mapping[str, str]) -> DependencyStatus:
    """Return whether git is installed and is 2.31 or later; git is always needed."""
    try:
        process = run(["git", "--version"], environ, None)
    except FileNotFoundError:
        return DependencyStatus(name="git", status="unavailable", detail="git is not installed")
    text = process.stdout.strip()
    if process.returncode != 0:
        return DependencyStatus(name="git", status="unavailable", detail="`git --version` failed")
    match = re.search(r"git version (\d+)\.(\d+)", text)
    if match is not None and (int(match.group(1)), int(match.group(2))) < MIN_GIT_VERSION:
        required = ".".join(map(str, MIN_GIT_VERSION))
        return DependencyStatus(
            name="git", status="unavailable", detail=f"{text} is older than {required}"
        )
    return DependencyStatus(name="git", status="ok", detail=text or None)


def docker_status(
    config: Config | None,
    *,
    run: Runner,
    environ: Mapping[str, str],
    unreadable: str | None = None,
) -> DependencyStatus:
    """Return the state of Docker: needed when `config` has `[compose]`, otherwise not required.

    `config` is None outside a repository, or when `wtenv.toml` cannot be read (`unreadable` says
    why): what the configuration needs is then not known, and nothing is reported as missing.
    """
    if config is None or config.compose is None:
        return DependencyStatus(
            name="docker", status="not_required", detail=_not_checked(unreadable)
        )
    try:
        check_docker(run=run, environ=environ)
    except WtenvError as error:
        return DependencyStatus(name="docker", status="unavailable", detail=error.message)
    return DependencyStatus(name="docker", status="ok")


def postgres_status(
    config: Config | None, *, server_check: ServerCheck, unreadable: str | None = None
) -> DependencyStatus:
    """Return the state of Postgres: needed when `config` has a Postgres `[database]`.

    The server is the one the URL pattern names. A pattern that cannot be filled in here, for
    example an unset `{env:NAME}`, is `config_invalid` for `up`; `doctor` reports no dependency
    problem for it and says so in `detail`.
    """
    settings = None if config is None else config.database
    if settings is None or settings.type != "postgres":
        return DependencyStatus(
            name="postgres", status="not_required", detail=_not_checked(unreadable)
        )
    try:
        url = database.resolve_url(settings.url, name="wtenv_doctor")
        server_check(database.postgres_target(url))
    except WtenvError as error:
        if error.code is ErrorCode.CONFIG_INVALID:
            return DependencyStatus(
                name="postgres",
                status="not_required",
                detail=_not_checked(error.message),
            )
        return DependencyStatus(name="postgres", status="unavailable", detail=error.message)
    return DependencyStatus(name="postgres", status="ok")


def _not_checked(why: str | None) -> str | None:
    return None if why is None else f"not checked: {why}"


def dependency_findings(statuses: Sequence[DependencyStatus]) -> list[Finding]:
    """Return a `dependency_unavailable` finding for each needed dependency that is unavailable."""
    return [
        _problem(
            FindingCode.DEPENDENCY_UNAVAILABLE,
            f"{status.name} is needed here but is unavailable: {status.detail}",
            dependency=status.name,
            reason=status.detail or "unavailable",
        )
        for status in statuses
        if status.status == "unavailable"
    ]


# --- the result (FR-061) -----------------------------------------------------------------------


def make_result(
    findings: Sequence[Finding], dependencies: Sequence[DependencyStatus]
) -> DoctorResult:
    """Return the result: `ok` unless a finding is a problem, which is `problems_found` (17)."""
    problems = sum(1 for finding in findings if finding.severity == "problem")
    result = DoctorResult(
        ok=problems == 0, findings=list(findings), dependencies=list(dependencies)
    )
    if problems:
        result.error = ErrorInfo(
            code=ErrorCode.PROBLEMS_FOUND,
            exit_status=EXIT_STATUS[ErrorCode.PROBLEMS_FOUND],
            message=f"{problems} problem(s) found",
            hint="Read `findings`.",
            details={"problems": problems},
        )
    return result


def diagnose(
    cwd: str | Path | None = None,
    *,
    run: Runner = run_command,
    environ: Mapping[str, str] | None = None,
    is_free: IsFree | None = None,
    postgres_has: PostgresLookup = server_has_database,
    server_check: ServerCheck = database.check_postgres_server,
) -> DoctorResult:
    """Run every check and return the result. It reads the registry under its lock and changes
    nothing: no worktree lock is taken, and `cwd` need not be inside a repository (FR-003).

    The configuration that decides which dependencies are needed is the `wtenv.toml` of the
    worktree that contains `cwd`. Raises `registry_busy` and `registry_unreadable`.
    """
    env = os.environ if environ is None else environ
    free = port_is_free if is_free is None else is_free
    git = git_status(run, env)
    with registry_lock(create=False):
        entries = sorted(
            registry.load().worktrees.values(), key=lambda entry: (entry.block.start, entry.path)
        )

    findings = block_overlaps(entries)
    listings: dict[str, list[WorktreeRecord] | None] = {}
    lookup = remember_unreachable(postgres_has)
    for entry in entries:
        if git.status == "ok":  # without git, nothing can be said about a worktree's status
            if entry.repository not in listings:
                listings[entry.repository] = git_listing(entry.repository)
            found = classify(entry, listings[entry.repository])
            problem = status_finding(entry, found)
            if problem is not None:
                findings.append(problem)
            if found.status in (Status.PROVISIONED, Status.INCOMPLETE):
                findings += missing_resources(entry, postgres_has=lookup)
        findings += port_findings(entry, run=run, environ=env, is_free=free)

    config, unreadable = _current_config(cwd) if git.status == "ok" else (None, None)
    dependencies = [
        git,
        docker_status(config, run=run, environ=env, unreadable=unreadable),
        postgres_status(config, server_check=server_check, unreadable=unreadable),
    ]
    findings += dependency_findings(dependencies)
    return make_result(findings, dependencies)


def _current_config(cwd: str | Path | None) -> tuple[Config | None, str | None]:
    """Return `(config, None)` for the worktree around `cwd`, `(None, None)` outside one, and
    `(None, message)` when its `wtenv.toml` is invalid."""
    try:
        identity = current_worktree(cwd)
    except WtenvError as error:
        if error.code in (ErrorCode.NOT_IN_WORKTREE, ErrorCode.DEPENDENCY_UNAVAILABLE):
            return None, None
        raise
    try:
        return load_config(identity.path), None
    except WtenvError as error:
        return None, error.message
