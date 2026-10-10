"""`wtenv.toml`: the optional settings file in the root of a worktree (contracts/config.md).

The file is parsed with `tomllib` and checked by a pydantic model that rejects unknown keys. Any
problem is `config_invalid`, naming the file and the setting, and nothing is read further
(FR-064).
"""

import posixpath
import re
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from wtenv.errors import ErrorCode, WtenvError

CONFIG_FILE_NAME = "wtenv.toml"

DEFAULT_PORTS = ["PORT"]
DEFAULT_BLOCK_SIZE = 10
DEFAULT_ENV_FILE = ".env.local"
MAX_BLOCK_SIZE = 1000

_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESERVED_VARIABLE = "DATABASE_URL"  # written by the database step, so not a port variable

# Text in braces in `database.url`: `{name}`, `{path}`, and `{env:NAME}` are placeholders.
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
ENV_PLACEHOLDER = re.compile(r"env:([A-Za-z_][A-Za-z0-9_]*)")
# `{compose_env:NAME}`, for type compose only: a value of the database service's environment.
COMPOSE_ENV_PLACEHOLDER = re.compile(r"compose_env:([A-Za-z_][A-Za-z0-9_]*)")
# The only hosts a Postgres pattern may name (FR-025); `urlsplit` gives IPv6 without brackets.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_POSTGRES_SCHEME = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?")
_FORBIDDEN_QUERY_KEYS = ("host", "hostaddr", "service")
# Where a compose pattern names the server: a local host, then `{port}` and nothing else.
_COMPOSE_HOST_AND_PORT = re.compile(r"(\[[^\]]*\]|[^:\[\]]*)(?::(.*))?")
# What a placeholder is replaced with while the pattern is only being checked.
_STAND_IN = "x"

# Plain `docker compose` finds a compose file only under these names (research.md §4).
COMPOSE_FILE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


class DatabaseConfig(BaseModel):
    """The `[database]` table: which kind of database, its template, and the `DATABASE_URL`.

    A worktree has one database mode (FR-017). `postgres` and `sqlite` need a `template`;
    `compose` needs a `service` instead, and the two settings are refused for the other types.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["postgres", "sqlite", "compose"]
    # Only the settings of the chosen type are dumped, so a v0.1.1 configuration dumps as before.
    template: str | None = Field(
        default=None, validate_default=True, exclude_if=lambda value: value is None
    )
    service: str | None = Field(
        default=None, validate_default=True, exclude_if=lambda value: value is None
    )
    url: str

    @field_validator("template")
    @classmethod
    def _template_belongs_to_postgres_and_sqlite(
        cls, template: str | None, info: ValidationInfo
    ) -> str | None:
        """Require a template for `postgres` and `sqlite`, and refuse it for `compose`."""
        kind = info.data.get("type")
        if kind == "compose":
            if template is not None:
                raise ValueError("not a setting of type compose")
        elif kind is not None:
            if template is None:
                raise ValueError("is required")
            if not template.strip():
                raise ValueError("must not be empty")
        return template

    @field_validator("service")
    @classmethod
    def _service_belongs_to_compose(cls, service: str | None, info: ValidationInfo) -> str | None:
        """Require a service for `compose`, and refuse it for `postgres` and `sqlite`."""
        kind = info.data.get("type")
        if kind == "compose":
            if service is None:
                raise ValueError("is required for type compose")
            if not service.strip():
                raise ValueError("must not be empty")
        elif kind is not None and service is not None:
            raise ValueError(f"not a setting of type {kind}")
        return service

    @field_validator("url")
    @classmethod
    def _url_pattern_is_valid(cls, url: str, info: ValidationInfo) -> str:
        """Check the pattern for the chosen type; with an invalid `type`, `type` reports it."""
        kind = info.data.get("type")
        problem = None
        if kind == "postgres":
            problem = _postgres_pattern_problem(url)
        elif kind == "sqlite":
            problem = _sqlite_pattern_problem(url)
        elif kind == "compose":
            problem = _compose_pattern_problem(url)
        if problem is not None:
            raise ValueError(problem)
        return url


class ComposeConfig(BaseModel):
    """The `[compose]` table: where the compose file is."""

    model_config = ConfigDict(extra="forbid", strict=True)

    file: str

    @field_validator("file")
    @classmethod
    def _file_is_a_default_compose_file_inside_the_worktree(cls, path: str) -> str:
        """Return the path in its plain form; only the four default file names are allowed."""
        plain = posixpath.normpath(path) if path else ""
        if not plain or plain == ".." or plain.startswith(("/", "../")):
            raise ValueError("must be a relative path inside the worktree")
        if posixpath.basename(plain) not in COMPOSE_FILE_NAMES:
            raise ValueError(f"its file name must be one of {', '.join(COMPOSE_FILE_NAMES)}")
        return plain


class Config(BaseModel):
    """The settings of `wtenv.toml`, with the defaults that apply when the file is absent."""

    # `strict`: TOML values already have types, so "10" or `true` is not a block size.
    model_config = ConfigDict(extra="forbid", strict=True)

    ports: list[str] = Field(default_factory=lambda: list(DEFAULT_PORTS))
    block_size: int = Field(default=DEFAULT_BLOCK_SIZE, ge=1, le=MAX_BLOCK_SIZE)
    env_file: str = DEFAULT_ENV_FILE
    post_up: list[str] = Field(default_factory=list)
    database: DatabaseConfig | None = None
    compose: ComposeConfig | None = None

    @field_validator("ports")
    @classmethod
    def _ports_are_unique_variable_names(cls, names: list[str]) -> list[str]:
        if not names:
            raise ValueError("needs at least one name")
        for name in names:
            if _VARIABLE_NAME.fullmatch(name) is None:
                raise ValueError(f"{name!r} is not a variable name")
            if name == _RESERVED_VARIABLE:
                raise ValueError(f"{_RESERVED_VARIABLE} is set by the database step")
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"names must be unique; repeated: {', '.join(repeated)}")
        return names

    @field_validator("env_file")
    @classmethod
    def _env_file_stays_inside_the_worktree(cls, path: str) -> str:
        """Return the path in its plain form (`./a/../.env` becomes `.env`)."""
        plain = posixpath.normpath(path) if path else ""
        if not plain or plain == ".." or plain.startswith(("/", "../")):
            raise ValueError("must be a relative path inside the worktree")
        return plain

    @field_validator("post_up")
    @classmethod
    def _post_up_commands_are_not_empty(cls, commands: list[str]) -> list[str]:
        if any(not command.strip() for command in commands):
            raise ValueError("each command must be non-empty")
        return commands


def _unknown_placeholder(pattern: str, allowed: tuple[str, ...]) -> str | None:
    """Return a problem if `pattern` has braces around anything but an allowed placeholder."""
    for match in PLACEHOLDER.finditer(pattern):
        text = match.group(1)
        if text not in allowed and ENV_PLACEHOLDER.fullmatch(text) is None:
            return f"{{{text}}} is not a placeholder; use {', '.join(f'{{{a}}}' for a in allowed)}"
    return None


def _postgres_pattern_problem(pattern: str) -> str | None:
    """Return what is wrong with a Postgres URL pattern, or None (config.md, The URL pattern).

    The message never repeats the pattern, which can hold a password (FR-019).
    """
    problem = _unknown_placeholder(pattern, ("name", "env:NAME"))
    if problem is not None:
        return problem
    # Stand-ins make the pattern a plain URL, so it can be taken apart.
    database = "wtenv_database"
    plain = PLACEHOLDER.sub(_STAND_IN, pattern.replace("{name}", database))
    try:
        parts = urlsplit(plain)
        _ = parts.port  # raises ValueError for a port that is not a number
    except ValueError:
        return "is not a valid URL"
    if _POSTGRES_SCHEME.fullmatch(parts.scheme) is None:
        return "must start with postgresql:// or postgres:// (a driver suffix is allowed)"
    if parts.hostname not in LOCAL_HOSTS:
        return "must name the host explicitly as localhost, 127.0.0.1, or [::1] (FR-025)"
    if any(key in _FORBIDDEN_QUERY_KEYS for key, _ in parse_qsl(parts.query)):
        return "must not have a host, hostaddr, or service query parameter (FR-025)"
    if parts.path != f"/{database}":
        return "must have {name} as the database name"
    return None


def _compose_pattern_problem(pattern: str) -> str | None:
    """Return what is wrong with a URL pattern of type compose, or None (config.md).

    The message never repeats the pattern, which can hold a password (FR-019, FR-020).
    """
    for match in PLACEHOLDER.finditer(pattern):
        text = match.group(1)
        if (
            text != "port"
            and ENV_PLACEHOLDER.fullmatch(text) is None
            and COMPOSE_ENV_PLACEHOLDER.fullmatch(text) is None
        ):
            return f"{{{text}}} is not a placeholder; use {{port}}, {{env:NAME}} or {{compose_env:NAME}}"
    # Every placeholder but `{port}` stands for a value; `{port}` stays so its place can be checked.
    plain = PLACEHOLDER.sub(lambda m: m.group(0) if m.group(1) == "port" else _STAND_IN, pattern)
    try:
        parts = urlsplit(plain)
    except ValueError:
        return "is not a valid URL"
    if _POSTGRES_SCHEME.fullmatch(parts.scheme) is None:
        return "must start with postgresql:// or postgres:// (a driver suffix is allowed)"
    server = _COMPOSE_HOST_AND_PORT.fullmatch(parts.netloc.rpartition("@")[2])
    host = "" if server is None else server.group(1).lower()
    if server is None or host not in ("localhost", "127.0.0.1", "[::1]"):
        return "must name the host explicitly as localhost, 127.0.0.1, or [::1] (FR-025)"
    if server.group(2) != "{port}":
        return "must have {port} as the port, and nothing else there"
    if any(key in _FORBIDDEN_QUERY_KEYS for key, _ in parse_qsl(parts.query)):
        return "must not have a host, hostaddr, or service query parameter (FR-025)"
    if len(parts.path) < 2:
        return "must have a database name after the port"
    return None


def _sqlite_pattern_problem(pattern: str) -> str | None:
    """Return what is wrong with a SQLite URL pattern, or None."""
    problem = _unknown_placeholder(pattern, ("path", "env:NAME"))
    if problem is not None:
        return problem
    if "{path}" not in pattern:
        return "must contain {path}"
    return None


def load_config(worktree: str | Path) -> Config:
    """Read `wtenv.toml` from the root of `worktree`; with no file, return the defaults.

    Raises `config_invalid` for invalid TOML, an unknown setting, or an invalid value
    (FR-064). `details` names the `file` and the `setting` (null when no single setting is at
    fault, as with a TOML syntax error).
    """
    path = Path(worktree) / CONFIG_FILE_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError) as error:
        raise _invalid(path, None, f"cannot read it ({type(error).__name__})") from error
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise _invalid(path, None, f"it is not valid TOML ({error})") from error
    try:
        config = Config.model_validate(data)
    except ValidationError as error:
        raise _first_error(path, error) from error
    if config.database is not None and config.database.type == "compose" and config.compose is None:
        raise _invalid(
            path, "database.type", "invalid value for database.type: compose needs [compose]"
        )
    return config


def _first_error(path: Path, error: ValidationError) -> WtenvError:
    """Build `config_invalid` from the first problem pydantic found, naming its setting."""
    first = error.errors()[0]
    setting = ".".join(str(part) for part in first["loc"] if not isinstance(part, int)) or None
    if first["type"] == "extra_forbidden":
        return _invalid(path, setting, f"{setting} is not a setting")
    reason = first["msg"].removeprefix("Value error, ")
    return _invalid(path, setting, f"invalid value for {setting}: {reason}")


def _invalid(path: Path, setting: str | None, problem: str) -> WtenvError:
    """Build the `config_invalid` error for `path`."""
    return WtenvError(
        ErrorCode.CONFIG_INVALID,
        f"{path}: {problem}",
        hint="Fix wtenv.toml, then run `wtenv up` again. Nothing was changed.",
        details={"file": str(path), "setting": setting},
    )
