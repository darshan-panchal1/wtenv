"""Loading `wtenv.toml`: the `[database]` table and `post_up` (config.md; FR-025, FR-026, FR-036,
FR-063, FR-064)."""

from pathlib import Path

import pytest

from wtenv.config import DatabaseConfig, load_config
from wtenv.errors import ErrorCode, WtenvError

POSTGRES_URL = "postgresql://myapp:{env:DB_PASSWORD}@localhost:5432/{name}"


def write_config(root: Path, text: str) -> None:
    (root / "wtenv.toml").write_text(text, encoding="utf-8")


def database_table(kind: str = "postgres", url: str = POSTGRES_URL, template: str = "tmpl") -> str:
    return f'[database]\ntype = "{kind}"\ntemplate = "{template}"\nurl = \'{url}\'\n'


def invalid(root: Path) -> WtenvError:
    """Load the configuration in `root` and return the `config_invalid` error it raises."""
    with pytest.raises(WtenvError) as caught:
        load_config(root)
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    return caught.value


def setting_of(error: WtenvError) -> object:
    return error.details["setting"]


# --- defaults and valid settings ------------------------------------------------------------


def test_without_the_table_there_is_no_database_and_no_post_up(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.database is None
    assert config.post_up == []


def test_a_postgres_table_is_read(tmp_path: Path) -> None:
    write_config(tmp_path, database_table())

    config = load_config(tmp_path)

    assert config.database == DatabaseConfig(type="postgres", template="tmpl", url=POSTGRES_URL)


def test_a_sqlite_table_is_read(tmp_path: Path) -> None:
    write_config(tmp_path, database_table("sqlite", "sqlite:///{path}", "db/dev.sqlite3"))

    config = load_config(tmp_path)

    assert config.database == DatabaseConfig(
        type="sqlite", template="db/dev.sqlite3", url="sqlite:///{path}"
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@localhost/{name}",
        "postgres://u@127.0.0.1:5433/{name}",
        "postgresql://u@[::1]:5432/{name}",
        "postgresql+psycopg://u:{env:PW}@localhost:5432/{name}",
        "postgresql://u:{env:PW}@localhost/{name}?sslmode=disable",
        "postgresql://LOCALHOST/{name}",
    ],
)
def test_valid_postgres_patterns_are_accepted(tmp_path: Path, url: str) -> None:
    write_config(tmp_path, database_table(url=url))

    assert load_config(tmp_path).database is not None


@pytest.mark.parametrize("url", ["sqlite:///{path}", "sqlite+pysqlite:///{path}", "{path}"])
def test_valid_sqlite_patterns_are_accepted(tmp_path: Path, url: str) -> None:
    write_config(tmp_path, database_table("sqlite", url, "dev.db"))

    assert load_config(tmp_path).database is not None


def test_post_up_is_a_list_of_commands(tmp_path: Path) -> None:
    write_config(tmp_path, 'post_up = ["uv run alembic upgrade head", "echo done"]\n')

    assert load_config(tmp_path).post_up == ["uv run alembic upgrade head", "echo done"]


# --- required settings and unknown keys -----------------------------------------------------


@pytest.mark.parametrize("missing", ["type", "template", "url"])
def test_each_database_setting_is_required(tmp_path: Path, missing: str) -> None:
    values = {"type": "postgres", "template": "tmpl", "url": POSTGRES_URL}
    del values[missing]
    lines = "\n".join(f"{key} = '{value}'" for key, value in values.items())
    write_config(tmp_path, f"[database]\n{lines}\n")

    assert setting_of(invalid(tmp_path)) == f"database.{missing}"


def test_the_type_must_be_postgres_or_sqlite(tmp_path: Path) -> None:
    write_config(tmp_path, database_table("mysql", "mysql://localhost/{name}"))

    error = invalid(tmp_path)

    assert setting_of(error) == "database.type"
    assert "database.type" in error.message


def test_an_unknown_database_key_is_an_error(tmp_path: Path) -> None:
    write_config(tmp_path, database_table() + 'strategy = "file_copy"\n')

    assert setting_of(invalid(tmp_path)) == "database.strategy"


def test_the_template_may_not_be_empty(tmp_path: Path) -> None:
    write_config(tmp_path, database_table(template=""))

    assert setting_of(invalid(tmp_path)) == "database.template"


def test_database_must_be_a_table(tmp_path: Path) -> None:
    write_config(tmp_path, 'database = "postgres"\n')

    assert setting_of(invalid(tmp_path)) == "database"


# --- placeholders ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u@localhost/{database}",
        "postgresql://u:{password}@localhost/{name}",
        "postgresql://u:{ENV:PW}@localhost/{name}",
        "postgresql://u:{env:}@localhost/{name}",
        "postgresql://u:{env:1X}@localhost/{name}",
        "postgresql://u@localhost/{name}{}",
    ],
)
def test_any_other_text_in_braces_is_config_invalid(tmp_path: Path, url: str) -> None:
    write_config(tmp_path, database_table(url=url))

    error = invalid(tmp_path)

    assert setting_of(error) == "database.url"


def test_the_path_placeholder_is_for_sqlite_only(tmp_path: Path) -> None:
    write_config(tmp_path, database_table(url="postgresql://u@localhost/{name}?x={path}"))

    assert setting_of(invalid(tmp_path)) == "database.url"


def test_the_name_placeholder_is_for_postgres_only(tmp_path: Path) -> None:
    write_config(tmp_path, database_table("sqlite", "sqlite:///{path}?name={name}", "dev.db"))

    assert setting_of(invalid(tmp_path)) == "database.url"


# --- Postgres patterns (FR-025) -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "mysql://u@localhost/{name}",
        "sqlite:///{name}",
        "localhost/{name}",
        "postgresql://u@db.example.com/{name}",
        "postgresql://u@10.0.0.5/{name}",
        "postgresql://u@[::2]/{name}",
        "postgresql://u@{env:DB_HOST}/{name}",
        "postgresql:///{name}",
        "postgresql://u@/{name}",
        "postgresql://%2Fvar%2Frun%2Fpostgresql/{name}",
        "postgresql://u@localhost/{name}?host=db.example.com",
        "postgresql://u@localhost/{name}?hostaddr=10.0.0.5",
        "postgresql://u@localhost/{name}?service=prod",
        "postgresql://u@localhost/{name}?sslmode=disable&host=/var/run/postgresql",
        "postgresql://u@localhost:notaport/{name}",
        "postgresql://u@localhost/mydb",
        "postgresql://u@localhost/",
        "postgresql://u@localhost/{name}_copy",
    ],
)
def test_a_postgres_pattern_must_name_a_local_host_and_the_database(
    tmp_path: Path, url: str
) -> None:
    write_config(tmp_path, database_table(url=url))

    error = invalid(tmp_path)

    assert setting_of(error) == "database.url"
    assert "database.url" in error.message


def test_a_rejected_pattern_does_not_reveal_a_password(tmp_path: Path) -> None:
    write_config(tmp_path, database_table(url="postgresql://u:hunter2@db.example.com/{name}"))

    error = invalid(tmp_path)

    assert "hunter2" not in error.message
    assert "hunter2" not in str(error.details)


# --- SQLite patterns ------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["sqlite:///dev.db", "sqlite:///tmp/other.db", ""])
def test_a_sqlite_pattern_must_contain_the_path_placeholder(tmp_path: Path, url: str) -> None:
    write_config(tmp_path, database_table("sqlite", url, "dev.db"))

    assert setting_of(invalid(tmp_path)) == "database.url"


# --- post_up (FR-036) -----------------------------------------------------------------------


@pytest.mark.parametrize("commands", ['[""]', '["make seed", ""]', '["   "]'])
def test_each_post_up_command_must_be_non_empty(tmp_path: Path, commands: str) -> None:
    write_config(tmp_path, f"post_up = {commands}\n")

    error = invalid(tmp_path)

    assert setting_of(error) == "post_up"
    assert "post_up" in error.message


@pytest.mark.parametrize("value", ['"make seed"', "[1]", "true"])
def test_post_up_must_be_a_list_of_strings(tmp_path: Path, value: str) -> None:
    write_config(tmp_path, f"post_up = {value}\n")

    assert setting_of(invalid(tmp_path)) == "post_up"
