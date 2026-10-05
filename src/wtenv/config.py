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
# The only hosts a Postgres pattern may name (FR-025); `urlsplit` gives IPv6 without brackets.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_POSTGRES_SCHEME = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?")
_FORBIDDEN_QUERY_KEYS = ("host", "hostaddr", "service")
# What a placeholder is replaced with while the pattern is only being checked.
_STAND_IN = "x"


class DatabaseConfig(BaseModel):
    """The `[database]` table: which kind of database, its template, and the `DATABASE_URL`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["postgres", "sqlite"]
    template: str
    url: str

    @field_validator("template")
    @classmethod
    def _template_is_not_empty(cls, template: str) -> str:
        if not template.strip():
            raise ValueError("must not be empty")
        return template

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
        if problem is not None:
            raise ValueError(problem)
        return url


class Config(BaseModel):
    """The settings of `wtenv.toml`, with the defaults that apply when the file is absent."""

    # `strict`: TOML values already have types, so "10" or `true` is not a block size.
    model_config = ConfigDict(extra="forbid", strict=True)

    ports: list[str] = Field(default_factory=lambda: list(DEFAULT_PORTS))
    block_size: int = Field(default=DEFAULT_BLOCK_SIZE, ge=1, le=MAX_BLOCK_SIZE)
    env_file: str = DEFAULT_ENV_FILE
    post_up: list[str] = Field(default_factory=list)
    database: DatabaseConfig | None = None

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
        return Config.model_validate(data)
    except ValidationError as error:
        raise _first_error(path, error) from error


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
