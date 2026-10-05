"""`wtenv.toml`: the optional settings file in the root of a worktree (contracts/config.md).

The file is parsed with `tomllib` and checked by a pydantic model that rejects unknown keys. Any
problem is `config_invalid`, naming the file and the setting, and nothing is read further
(FR-064).
"""

import posixpath
import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from wtenv.errors import ErrorCode, WtenvError

CONFIG_FILE_NAME = "wtenv.toml"

DEFAULT_PORTS = ["PORT"]
DEFAULT_BLOCK_SIZE = 10
DEFAULT_ENV_FILE = ".env.local"
MAX_BLOCK_SIZE = 1000

_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESERVED_VARIABLE = "DATABASE_URL"  # written by the database step, so not a port variable


class Config(BaseModel):
    """The settings of `wtenv.toml`, with the defaults that apply when the file is absent."""

    # `strict`: TOML values already have types, so "10" or `true` is not a block size.
    model_config = ConfigDict(extra="forbid", strict=True)

    ports: list[str] = Field(default_factory=lambda: list(DEFAULT_PORTS))
    block_size: int = Field(default=DEFAULT_BLOCK_SIZE, ge=1, le=MAX_BLOCK_SIZE)
    env_file: str = DEFAULT_ENV_FILE

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
