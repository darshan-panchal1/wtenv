"""Loading `wtenv.toml`: the `[database]` table and `post_up` (config.md; FR-025, FR-026, FR-036,
FR-063, FR-064)."""

import json
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


# --- `type = "compose"` (T012; FR-017, FR-018, FR-020, FR-028; config.md) -----------------------

COMPOSE_URL = "postgresql://postgres:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/app"
COMPOSE_TABLE = '[compose]\nfile = "compose.yaml"\n'


def compose_database(
    url: str = COMPOSE_URL, *, service: str | None = "db", template: str | None = None
) -> str:
    """A `[database]` table of type compose; `service` and `template` are left out when None."""
    lines = ['type = "compose"']
    if service is not None:
        lines.append(f'service = "{service}"')
    if template is not None:
        lines.append(f'template = "{template}"')
    lines.append(f"url = '{url}'")
    return "[database]\n" + "\n".join(lines) + "\n"


def test_a_compose_database_with_a_service_and_a_url_is_read(tmp_path: Path) -> None:
    write_config(tmp_path, compose_database() + COMPOSE_TABLE)

    config = load_config(tmp_path)

    assert config.database is not None
    assert config.database.type == "compose"
    assert config.database.service == "db"
    assert config.database.template is None
    assert config.database.url == COMPOSE_URL
    assert config.compose is not None


def test_a_compose_database_needs_the_compose_table(tmp_path: Path) -> None:
    write_config(tmp_path, compose_database())

    error = invalid(tmp_path)

    assert setting_of(error) == "database.type"
    assert "needs [compose]" in error.message


def test_a_template_is_not_a_setting_of_type_compose(tmp_path: Path) -> None:
    write_config(tmp_path, compose_database(template="tmpl") + COMPOSE_TABLE)

    error = invalid(tmp_path)

    assert setting_of(error) == "database.template"
    assert "not a setting of type compose" in error.message


@pytest.mark.parametrize("kind", ["postgres", "sqlite"])
def test_a_service_is_not_a_setting_of_type_postgres_or_sqlite(tmp_path: Path, kind: str) -> None:
    url = POSTGRES_URL if kind == "postgres" else "sqlite:///{path}"
    write_config(tmp_path, database_table(kind, url) + 'service = "db"\n')

    assert setting_of(invalid(tmp_path)) == "database.service"


def test_a_compose_database_needs_a_service(tmp_path: Path) -> None:
    write_config(tmp_path, compose_database(service=None) + COMPOSE_TABLE)

    assert setting_of(invalid(tmp_path)) == "database.service"


def test_a_compose_database_needs_a_url(tmp_path: Path) -> None:
    write_config(tmp_path, '[database]\ntype = "compose"\nservice = "db"\n' + COMPOSE_TABLE)

    assert setting_of(invalid(tmp_path)) == "database.url"


def test_the_service_may_not_be_empty(tmp_path: Path) -> None:
    write_config(tmp_path, compose_database(service="") + COMPOSE_TABLE)

    assert setting_of(invalid(tmp_path)) == "database.service"


@pytest.mark.parametrize(
    "url",
    [
        COMPOSE_URL,
        "postgres://postgres:{env:PGPASSWORD}@localhost:{port}/app",
        "postgresql+psycopg://u:{compose_env:POSTGRES_PASSWORD}@127.0.0.1:{port}/app",
        "postgresql://u@[::1]:{port}/app",
        "postgresql://localhost:{port}/app",
        "postgresql://u:{env:A}{compose_env:B}@LOCALHOST:{port}/app",
        "postgresql://u@localhost:{port}/app?sslmode=disable",
    ],
)
def test_valid_compose_patterns_are_accepted(tmp_path: Path, url: str) -> None:
    write_config(tmp_path, compose_database(url) + COMPOSE_TABLE)

    config = load_config(tmp_path)

    assert config.database is not None
    assert config.database.url == url


@pytest.mark.parametrize(
    "url",
    [
        "mysql://u@localhost:{port}/app",
        "localhost:{port}/app",
        "postgresql://u@db.example.com:{port}/app",
        "postgresql://u@10.0.0.5:{port}/app",
        "postgresql://u@[::2]:{port}/app",
        "postgresql://u@{env:DB_HOST}:{port}/app",
        "postgresql://u@{compose_env:DB_HOST}:{port}/app",
        "postgresql://u@:{port}/app",
        "postgresql:///app",
        "postgresql://u@localhost:5432/app",
        "postgresql://u@localhost/app",
        "postgresql://u@localhost:{env:PORT}/app",
        "postgresql://u@localhost:1{port}/app",
        "postgresql://u@localhost:{port}x/app",
        "postgresql://u@localhost:{port}",
        "postgresql://u@localhost:{port}/",
        "postgresql://u@localhost:{port}/app?host=db.example.com",
        "postgresql://u@localhost:{port}/app?hostaddr=10.0.0.5",
        "postgresql://u@localhost:{port}/app?service=prod",
        "postgresql://u@localhost:{port}/app?sslmode=disable&host=/var/run/postgresql",
        "postgresql://u@localhost:{port}/{name}",
        "postgresql://u@localhost:{port}/app?x={path}",
        "postgresql://u:{password}@localhost:{port}/app",
        "postgresql://u:{compose_env:}@localhost:{port}/app",
        "postgresql://u:{compose_env:1X}@localhost:{port}/app",
    ],
)
def test_a_compose_pattern_must_name_a_local_host_the_port_and_a_database(
    tmp_path: Path, url: str
) -> None:
    write_config(tmp_path, compose_database(url) + COMPOSE_TABLE)

    error = invalid(tmp_path)

    assert setting_of(error) == "database.url"
    assert "database.url" in error.message


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:hunter2@db.example.com:{port}/app",
        "postgresql://u:hunter2@localhost:5432/app",
        "postgresql://u:hunter2@localhost:{port}/{name}",
        "postgresql://u:hunter2@localhost:{port}/app?host=db.example.com",
        "mysql://u:hunter2@localhost:{port}/app",
        "postgresql://u:hunter2@localhost:{port}/",
        "postgresql://u:hunter2{password}@localhost:{port}/app",
    ],
)
def test_a_rejected_compose_pattern_does_not_reveal_a_password(tmp_path: Path, url: str) -> None:
    write_config(tmp_path, compose_database(url) + COMPOSE_TABLE)

    error = invalid(tmp_path)

    assert setting_of(error) == "database.url"
    assert "hunter2" not in error.message
    assert "hunter2" not in str(error.details)


# --- every configuration v0.1.1 accepts is accepted unchanged (FR-028, part 1) ---------------

V0_1_1_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v0_1_1"
V0_1_1_CONFIGS = sorted((V0_1_1_FIXTURES / "configs").glob("*.toml"))


def test_the_v0_1_1_configurations_are_there() -> None:
    # Guards the test below against passing because it found nothing to compare.
    assert len(V0_1_1_CONFIGS) >= 13


@pytest.mark.parametrize("path", V0_1_1_CONFIGS, ids=lambda path: path.stem)
def test_a_v0_1_1_configuration_loads_and_dumps_as_v0_1_1_did(tmp_path: Path, path: Path) -> None:
    write_config(tmp_path, path.read_text(encoding="utf-8"))
    golden = json.loads((V0_1_1_FIXTURES / "golden" / f"{path.stem}.json").read_text("utf-8"))

    config = load_config(tmp_path)

    assert config.model_dump(mode="json") == golden["config"]
