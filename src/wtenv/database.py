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
from urllib.parse import unquote, urlsplit

from wtenv.config import ENV_PLACEHOLDER, LOCAL_HOSTS, PLACEHOLDER
from wtenv.errors import ErrorCode, JsonValue, WtenvError
from wtenv.identity import short_id, slug

SQLITE_DIR = ".wtenv"  # in the worktree root; the copies live here (files.md, Names)
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
