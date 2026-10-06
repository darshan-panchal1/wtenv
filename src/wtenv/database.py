"""The databases wtenv creates for a worktree: names, the URL pattern, SQLite, and Postgres.

`psycopg` is never imported at module level (NFR-001); the Postgres functions import it inside
themselves. No function here puts a password into a message, a detail, or a `repr` (FR-019): the
registry has no field for one either.
"""

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import unquote, urlsplit

from wtenv.config import ENV_PLACEHOLDER, LOCAL_HOSTS, PLACEHOLDER
from wtenv.errors import ErrorCode, JsonValue, WtenvError
from wtenv.identity import short_id, slug
from wtenv.output import FailedItem, Item, ItemKind, KeptVolume

if TYPE_CHECKING:
    import psycopg

SQLITE_DIR = ".wtenv"  # in the worktree root; the copies live here (files.md, Names)
SQLITE_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")  # SQLite's own files beside a database file
POSTGRES_DEFAULT_PORT = 5432
MAINTENANCE_DATABASE = "postgres"  # the database wtenv connects to (research.md section 3)
_URL_SETTING = "database.url"


# --- names (files.md, Names; FR-022) ----------------------------------------------------------


def postgres_database_name(worktree_name: str, git_dir: str) -> str:
    """Return `wtenv_<slug>_<id8>` for the worktree directory `worktree_name` and its `git_dir`.

    The caller records the name when it creates the database and never computes it again, so the
    database survives a move of the worktree (FR-084).
    """
    return f"wtenv_{slug(worktree_name, '_')}_{short_id(git_dir, 8)}"


def sqlite_template_path(worktree: str | Path, template: str) -> Path:
    """Return the template file: `template` is relative to the worktree root, or absolute."""
    return Path(worktree) / template


def sqlite_copy_path(worktree: str | Path, template: str) -> Path:
    """Return the worktree's copy: `<worktree>/.wtenv/<template file name>` (FR-022)."""
    return Path(worktree) / SQLITE_DIR / Path(template).name


# --- the URL pattern (config.md, The URL pattern; FR-025, FR-026) -----------------------------


def _url_invalid(problem: str, config_file: str | None, **details: str) -> WtenvError:
    """Build `config_invalid` for `database.url`; `problem` never repeats the URL."""
    where = f"{config_file}: " if config_file else ""
    return WtenvError(
        ErrorCode.CONFIG_INVALID,
        f"{where}invalid value for {_URL_SETTING}: {problem}",
        hint="Fix wtenv.toml or the environment, then run `wtenv up` again. Nothing was changed.",
        details={"file": config_file, "setting": _URL_SETTING, **details},
    )


def resolve_url(
    pattern: str,
    *,
    name: str | None = None,
    path: str | None = None,
    environ: Mapping[str, str] | None = None,
    config_file: str | None = None,
) -> str:
    """Return `pattern` with `{name}`, `{path}`, and `{env:NAME}` replaced (FR-026).

    `environ` defaults to the process environment. Raises `config_invalid` for an unset
    variable (`details.variable`), or when the result holds a single quote or a line break,
    which the env file cannot carry; the message never repeats the URL.
    """
    variables = os.environ if environ is None else environ

    def replace(match: "re.Match[str]") -> str:
        text = match.group(1)
        if text == "name" and name is not None:
            return name
        if text == "path" and path is not None:
            return path
        variable = ENV_PLACEHOLDER.fullmatch(text)
        if variable is None:
            raise _url_invalid(f"{{{text}}} cannot be filled in here", config_file)
        value = variables.get(variable.group(1))
        if value is None:
            raise _url_invalid(
                f"the environment variable {variable.group(1)} is not set",
                config_file,
                variable=variable.group(1),
            )
        return value

    # One pass over the pattern: a value is never searched for placeholders itself.
    url = PLACEHOLDER.sub(replace, pattern)
    if "'" in url or "\n" in url or "\r" in url:
        raise _url_invalid(
            "the resolved URL holds a single quote or a line break; percent-encode it",
            config_file,
        )
    return url


@dataclass(frozen=True)
class PostgresTarget:
    """Where wtenv connects to create a database: the server named by the URL pattern."""

    host: str
    port: int
    user: str | None
    password: str | None = field(repr=False)

    @property
    def dbname(self) -> str:
        """The maintenance database; wtenv never connects to the template (research.md 3)."""
        return MAINTENANCE_DATABASE


def postgres_target(url: str, *, config_file: str | None = None) -> PostgresTarget:
    """Return wtenv's own connection for a resolved Postgres `url`.

    It uses the URL's user, password, host, and port. The driver suffix and the query
    parameters are not used; they stay in `DATABASE_URL`. A host that is not `localhost`,
    `127.0.0.1`, or `[::1]`, or a `host`, `hostaddr`, or `service` parameter, is
    `config_invalid` (FR-025).
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise _url_invalid("is not a valid URL", config_file) from None
    forbidden = {"host", "hostaddr", "service"}
    has_forbidden = any(pair.partition("=")[0] in forbidden for pair in parts.query.split("&"))
    if parts.hostname not in LOCAL_HOSTS or has_forbidden:
        raise _url_invalid(
            "the host must be named explicitly as localhost, 127.0.0.1, or [::1] (FR-025)",
            config_file,
        )
    return PostgresTarget(
        host=parts.hostname,
        port=POSTGRES_DEFAULT_PORT if port is None else port,
        user=None if parts.username is None else unquote(parts.username),
        password=None if parts.password is None else unquote(parts.password),
    )


# --- SQLite (FR-021 to FR-024; research.md section 11) -----------------------------------------


def check_sqlite_template(template: Path) -> None:
    """Raise unless `template` is a file that can be copied as it stands (FR-083).

    `template_missing` when it does not exist; `template_in_use` when a non-empty `-wal` or
    `-journal` file lies beside it, because a copy would miss or corrupt the data in it.
    """
    details: dict[str, JsonValue] = {"kind": "sqlite", "template": str(template)}
    if not template.is_file():
        raise WtenvError(
            ErrorCode.TEMPLATE_MISSING,
            f"the SQLite template {template} does not exist",
            hint="Create the template, or fix database.template in wtenv.toml.",
            details=details,
        )
    for suffix in ("-wal", "-journal"):
        side_file = Path(f"{template}{suffix}")
        if side_file.is_file() and side_file.stat().st_size > 0:
            raise WtenvError(
                ErrorCode.TEMPLATE_IN_USE,
                f"the SQLite template {template} has unfinished work in {side_file.name}",
                hint="Close whatever uses the template, then run `wtenv up` again.",
                details=details,
            )


def check_sqlite_target(target: Path) -> None:
    """Raise `ownership_conflict` when anything exists at `target`; it is not touched (FR-024).

    Call it only for a copy the registry does not record.
    """
    if os.path.lexists(target):
        raise WtenvError(
            ErrorCode.OWNERSHIP_CONFLICT,
            f"{target} exists, and wtenv has no record of creating it",
            hint="Rename or remove it by hand; wtenv will not touch it.",
            details={"kind": "sqlite_file", "name": str(target)},
        )


def create_sqlite_copy(template: Path, target: Path) -> None:
    """Copy `template` to `target` byte for byte, through a temporary file and an atomic rename.

    The template is only read (FR-027). The copy appears whole or not at all, so a copy that
    exists is never half written. Raises `ownership_conflict` rather than replace a file.
    """
    check_sqlite_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(descriptor, "wb") as file, open(template, "rb") as source:
            shutil.copyfileobj(source, file)
            file.flush()
            os.fsync(file.fileno())
        shutil.copymode(template, temporary)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


# --- removing databases (FR-039, FR-042; constitution v1.0.2, Principle II) --------------------


@dataclass(frozen=True)
class Removal:
    """What removing one recorded database did, or, when only listing, would do.

    `removed` holds the items deleted (or that would be); `already_absent` the recorded things
    that were not there (FR-042); `failed` the ones that could not be removed, each with a reason
    that never holds a password (FR-019). `kept_volumes` holds the Docker volumes of a compose
    project that were found and left alone (reading R6).
    """

    removed: list[Item] = field(default_factory=list)
    already_absent: list[Item] = field(default_factory=list)
    failed: list[FailedItem] = field(default_factory=list)
    kept_volumes: list[KeptVolume] = field(default_factory=list)


def _sqlite_item(path: Path) -> Item:
    return Item(kind=ItemKind.SQLITE_FILE, name=str(path))


def remove_sqlite_copy(copy: Path, *, dry_run: bool = False) -> Removal:
    """Remove the recorded SQLite copy `copy` and each existing side file beside it (FR-039).

    `copy` is the absolute path of a copy the registry records. Its side files are the files in
    the same directory named `<copy file name>-wal`, `-shm`, and `-journal`; each that exists is
    its own item, listed after the copy. Nothing else is touched. When the copy itself is already
    gone it is `already_absent` and its side files are left alone: side files go only together
    with the recorded database file (reading R2). The side files are deleted first and the copy
    last, so a failure leaves the copy in place and recorded, for the next `down` to finish.
    With `dry_run` nothing is deleted and the same items are returned.
    """
    if not os.path.lexists(copy):
        return Removal(already_absent=[_sqlite_item(copy)])
    side_files = [
        side
        for side in (Path(f"{copy}{suffix}") for suffix in SQLITE_SIDE_SUFFIXES)
        if os.path.lexists(side)
    ]
    items = [copy, *side_files]
    if dry_run:
        return Removal(removed=[_sqlite_item(path) for path in items])
    deleted: set[Path] = set()
    for path in [*side_files, copy]:
        try:
            os.unlink(path)
        except OSError as error:
            reason = error.strerror or type(error).__name__
            failed = FailedItem(kind=ItemKind.SQLITE_FILE, name=str(path), reason=reason)
            return Removal(
                removed=[_sqlite_item(item) for item in items if item in deleted], failed=[failed]
            )
        deleted.add(path)
    return Removal(removed=[_sqlite_item(path) for path in items])


# --- Postgres (FR-020, FR-025, FR-082; research.md section 3) ----------------------------------

MIN_POSTGRES_VERSION = 13  # `DROP DATABASE … WITH (FORCE)` needs it (research.md section 3)
_CONNECT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class PostgresState:
    """What the server holds that matters to creating a worktree's database."""

    template_exists: bool
    template_connections: int  # other sessions connected to the template now
    database_exists: bool  # a database with the target name
    # The server marks it invalid: a `DROP DATABASE` that was interrupted (`datconnlimit = -2`).
    database_invalid: bool = False


def _unavailable(reason: str, message: str, **details: str) -> WtenvError:
    """Build `dependency_unavailable` for the Postgres server."""
    return WtenvError(
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        message,
        hint="Start or fix the Postgres server, then run `wtenv up` again.",
        details={"dependency": "postgres", "reason": reason, **details},
    )


def _server_text(target: PostgresTarget) -> str:
    """Name the server in a message: host, port, and user, never the password."""
    user = "" if target.user is None else f" as {target.user}"
    return f"{target.host}:{target.port}{user}"


def check_postgres_version(version: int, target: PostgresTarget) -> None:
    """Raise `dependency_unavailable` (`too_old`) unless the server is version 13 or later.

    `version` is the number the server reports, for example `170011` for 17.11 and `120004`
    for 12.4.
    """
    major, minor = divmod(version, 10000)
    if major < MIN_POSTGRES_VERSION:
        raise _unavailable(
            "too_old",
            f"the Postgres server at {_server_text(target)} is version {major}.{minor}; "
            f"wtenv needs {MIN_POSTGRES_VERSION} or later",
            required=str(MIN_POSTGRES_VERSION),
            found=f"{major}.{minor}",
        )


def map_postgres_error(
    error: Exception,
    *,
    target: PostgresTarget,
    template: str,
    name: str,
    connections: int | None = None,
) -> WtenvError | None:
    """Return the wtenv error for something the server or driver raised, or None when unknown.

    The table is in research.md section 3. `connections` is the number of other sessions on
    the template, when the caller knows it. The text of `error` is never copied into the
    result, so a password in it cannot reach a message or a detail (FR-019).
    """
    from psycopg import OperationalError, errors

    if isinstance(error, errors.ObjectInUse):
        return _template_in_use(template, connections)
    if isinstance(error, errors.InvalidCatalogName):
        return _template_missing(template)
    if isinstance(error, errors.DuplicateDatabase):
        return _database_conflict(name)
    if isinstance(error, errors.InsufficientPrivilege):
        return _unavailable(
            "permission_denied",
            f"permission denied: the role {target.user or '(default)'} may not copy the template "
            f"database {template}; the template needs IS_TEMPLATE set, or ownership by this role "
            "(wtenv does not change the template)",
        )
    if isinstance(error, errors.Error) and (error.sqlstate or "").startswith("28"):
        return _authentication_failed(target)
    if isinstance(error, OperationalError):
        text = str(error).lower()
        if "authentication failed" in text or "no password supplied" in text:
            return _authentication_failed(target)
        return _unavailable(
            "cannot_connect", f"cannot connect to the Postgres server at {_server_text(target)}"
        )
    return None


def _template_in_use(template: str, connections: int | None) -> WtenvError:
    """Build `template_in_use` for a Postgres template; `connections` is left out when unknown."""
    details: dict[str, JsonValue] = {"kind": "postgres", "template": template}
    count = ""
    if connections is not None:
        details["connections"] = connections
        count = f" ({connections} open)"
    return WtenvError(
        ErrorCode.TEMPLATE_IN_USE,
        f"the template database {template} has other sessions connected{count}, "
        "and cannot be copied while they are",
        hint="Close the connections to the template, then run `wtenv up` again.",
        details=details,
    )


def _template_missing(template: str) -> WtenvError:
    """Build `template_missing` for a Postgres template."""
    return WtenvError(
        ErrorCode.TEMPLATE_MISSING,
        f"the template database {template} does not exist",
        hint="Create the template database, or fix database.template in wtenv.toml.",
        details={"kind": "postgres", "template": template},
    )


def _authentication_failed(target: PostgresTarget) -> WtenvError:
    return _unavailable(
        "authentication_failed",
        f"the Postgres server at {_server_text(target)} refused the login; "
        "check the user and password in database.url",
    )


def _database_conflict(name: str) -> WtenvError:
    """Build `ownership_conflict` for a Postgres database wtenv has no record of creating."""
    return WtenvError(
        ErrorCode.OWNERSHIP_CONFLICT,
        f"a database called {name} exists, and wtenv has no record of creating it",
        hint="Rename or drop it by hand; wtenv will not touch it.",
        details={"kind": "postgres_database", "name": name},
    )


def check_creatable(state: PostgresState, *, template: str, name: str) -> None:
    """Raise unless a database `name` may be created from `template`, given what the server holds.

    `template_missing` when the template is not there, `template_in_use` when other sessions are
    connected to it (FR-082), `ownership_conflict` when `name` exists (FR-024). Call it only for
    a database the registry does not record, and before anything is recorded.
    """
    if not state.template_exists:
        raise _template_missing(template)
    if state.template_connections > 0:
        raise _template_in_use(template, state.template_connections)
    if state.database_exists:
        raise _database_conflict(name)


def _connect(target: PostgresTarget) -> "psycopg.Connection[tuple[object, ...]]":
    """Open an autocommit connection to the maintenance database, never to the template."""
    import psycopg

    return psycopg.connect(
        host=target.host,
        port=target.port,
        user=target.user,
        password=target.password,
        dbname=target.dbname,
        autocommit=True,
        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
    )


def _count_sessions(connection: "psycopg.Connection[tuple[object, ...]]", template: str) -> int:
    """Count the client sessions other than this one that are connected to `template`."""
    row = connection.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid() AND backend_type = 'client backend'",
        [template],
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def _exists(connection: "psycopg.Connection[tuple[object, ...]]", name: str) -> bool:
    row = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
    return row.fetchone() is not None


def _is_invalid(connection: "psycopg.Connection[tuple[object, ...]]", name: str) -> bool:
    """Return whether the server marks database `name` invalid, as it does after an interrupted drop."""
    row = connection.execute(
        "SELECT datconnlimit = -2 FROM pg_database WHERE datname = %s", [name]
    ).fetchone()
    return row is not None and bool(row[0])


def _raise_mapped(
    error: Exception, target: PostgresTarget, template: str, name: str, connections: int | None
) -> NoReturn:
    """Raise the wtenv error for `error`, or let it propagate when wtenv has none for it."""
    mapped = map_postgres_error(
        error, target=target, template=template, name=name, connections=connections
    )
    if mapped is None:
        raise error
    raise mapped from None


def inspect_postgres(target: PostgresTarget, *, template: str, name: str) -> PostgresState:
    """Look at the server without changing it: the template, its sessions, and the target name.

    Raises `dependency_unavailable` when the server cannot be reached, refuses the login, or is
    older than 13. The caller raises `template_missing`, `template_in_use`, or
    `ownership_conflict` from the answer, because what to do depends on the registry.
    """
    import psycopg

    try:
        with _connect(target) as connection:
            check_postgres_version(connection.info.server_version, target)
            template_exists = _exists(connection, template)
            database_exists = _exists(connection, name)
            return PostgresState(
                template_exists=template_exists,
                template_connections=_count_sessions(connection, template)
                if template_exists
                else 0,
                database_exists=database_exists,
                database_invalid=database_exists and _is_invalid(connection, name),
            )
    except psycopg.Error as error:
        _raise_mapped(error, target, template, name, None)


def postgres_database_exists(target: PostgresTarget, name: str) -> bool:
    """Return whether the server holds a database called `name`. It changes nothing.

    Raises `dependency_unavailable` when the server cannot be reached, refuses the login, is
    older than 13, or cannot answer; `wtenv doctor` asks it of the databases the registry records.
    """
    import psycopg

    try:
        with _connect(target) as connection:
            check_postgres_version(connection.info.server_version, target)
            return _exists(connection, name)
    except psycopg.Error as error:
        mapped = map_postgres_error(error, target=target, template="", name=name)
        if mapped is None:
            mapped = _unavailable(
                "cannot_connect", f"cannot ask the Postgres server at {_server_text(target)}"
            )
        raise mapped from None


def check_postgres_server(target: PostgresTarget) -> None:
    """Raise `dependency_unavailable` unless the server answers, accepts the login, and is 13+."""
    postgres_database_exists(target, MAINTENANCE_DATABASE)


def create_postgres_database(target: PostgresTarget, *, template: str, name: str) -> None:
    """Run `CREATE DATABASE <name> TEMPLATE <template>` on one autocommit connection.

    Other sessions on the template are counted first; above zero, it raises `template_in_use`
    at once and issues nothing (FR-082). A template that is not on the server is
    `template_missing`. If the server still answers that the template is in use, or that the
    name exists, the same codes are raised (`ownership_conflict` for a name). The template is
    never altered (FR-027), and sessions are never terminated.
    """
    import psycopg
    from psycopg import sql

    connections: int | None = None
    try:
        with _connect(target) as connection:
            check_postgres_version(connection.info.server_version, target)
            if not _exists(connection, template):
                raise psycopg.errors.InvalidCatalogName(template)
            connections = _count_sessions(connection, template)
            if connections > 0:
                raise psycopg.errors.ObjectInUse(template)
            try:
                connection.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                        sql.Identifier(name), sql.Identifier(template)
                    )
                )
            except psycopg.errors.ObjectInUse:
                # A session arrived after the count: report how many are there now.
                connections = _count_sessions(connection, template)
                raise
    except psycopg.Error as error:
        _raise_mapped(error, target, template, name, connections)


def _drop_failure_reason(error: Exception, target: PostgresTarget, *, dry_run: bool = False) -> str:
    """Say why the drop failed, or with `dry_run` why the server could not be asked.

    The text of `error` is never copied: it could hold a password.
    """
    from psycopg import OperationalError, errors

    server = _server_text(target)
    if isinstance(error, errors.InsufficientPrivilege):
        return f"permission denied: the role {target.user or '(default)'} may not drop the database"
    refused = isinstance(error, errors.Error) and (error.sqlstate or "").startswith("28")
    text = str(error).lower() if isinstance(error, OperationalError) else ""
    if refused or "authentication failed" in text or "no password supplied" in text:
        return (
            f"the Postgres server at {server} refused the login; "
            "put the password in wtenv.toml, or set PGPASSWORD or ~/.pgpass, "
            "then run the command again"
        )
    if isinstance(error, OperationalError):
        return f"cannot connect to the Postgres server at {server}"
    if dry_run:
        return f"the Postgres server could not be asked ({type(error).__name__})"
    return f"the server did not drop the database ({type(error).__name__})"


def remove_postgres_database(
    target: PostgresTarget, name: str, *, dry_run: bool = False
) -> Removal:
    """Drop the recorded database `name` with `DROP DATABASE IF EXISTS … WITH (FORCE)`.

    `target` is the server the registry records for it; with no password in it, libpq looks in
    `PGPASSWORD`, `PGPASSFILE`, and `~/.pgpass`. A database that is not on the server is
    `already_absent` (FR-042). Only `name` is ever dropped, and only a name wtenv would have
    generated (`wtenv_<slug>_<id8>`, FR-022); the template is never touched (FR-027). Every
    failure is returned in `failed`, never raised, with a reason that holds no password. With
    `dry_run` the server is asked and nothing is dropped; when it cannot be asked, the database is
    `failed` as in a real run, and not listed as removed (FR-040).
    """
    import psycopg
    from psycopg import sql

    item = Item(kind=ItemKind.POSTGRES_DATABASE, name=name)
    if not name.startswith("wtenv_"):
        reason = "not a database name wtenv generates (wtenv_<name>_<id>); it was left alone"
        return Removal(failed=[FailedItem(kind=item.kind, name=name, reason=reason)])
    try:
        with _connect(target) as connection:
            check_postgres_version(connection.info.server_version, target)
            if not _exists(connection, name):
                return Removal(already_absent=[item])
            if not dry_run:
                connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
    except WtenvError as error:
        # An unsupported server version: the message names no credentials.
        return Removal(failed=[FailedItem(kind=item.kind, name=name, reason=error.message)])
    except psycopg.Error as error:
        # A dry run that cannot ask the server fails the item, as the real run would (FR-040).
        reason = _drop_failure_reason(error, target, dry_run=dry_run)
        return Removal(failed=[FailedItem(kind=item.kind, name=name, reason=reason)])
    return Removal(removed=[item])
