"""Resolving the URL pattern, and wtenv's own connection (config.md, The URL pattern; FR-025,
FR-026)."""

from functools import partial
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from wtenv.config import load_config
from wtenv.database import PostgresTarget, postgres_target, resolve_url
from wtenv.errors import ErrorCode, WtenvError

PATTERN = "postgresql://myapp:{env:DB_PASSWORD}@localhost:5432/{name}"


def config_invalid(call: object) -> WtenvError:
    assert callable(call)
    with pytest.raises(WtenvError) as caught:
        call()
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    return caught.value


# --- resolving the placeholders (FR-026) ------------------------------------------------


def test_name_is_replaced_by_the_database_name() -> None:
    url = resolve_url(
        "postgresql://u@localhost:5432/{name}", name="wtenv_feature_x_3f9a1c2b", environ={}
    )

    assert url == "postgresql://u@localhost:5432/wtenv_feature_x_3f9a1c2b"


def test_path_is_replaced_by_the_absolute_path_of_the_copy() -> None:
    url = resolve_url("sqlite:///{path}", path="/code/feature-x/.wtenv/dev.sqlite3", environ={})

    assert url == "sqlite:////code/feature-x/.wtenv/dev.sqlite3"


def test_env_is_replaced_by_the_value_of_the_variable() -> None:
    url = resolve_url(PATTERN, name="db1", environ={"DB_PASSWORD": "s3cret"})

    assert url == "postgresql://myapp:s3cret@localhost:5432/db1"


def test_an_empty_variable_counts_as_set() -> None:
    url = resolve_url(PATTERN, name="db1", environ={"DB_PASSWORD": ""})

    assert url == "postgresql://myapp:@localhost:5432/db1"


def test_the_process_environment_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "from-the-process")

    assert resolve_url(PATTERN, name="db1").endswith("from-the-process@localhost:5432/db1")


def test_an_unset_variable_is_config_invalid_and_names_it() -> None:
    error = config_invalid(lambda: resolve_url(PATTERN, name="db1", environ={}))

    assert error.details["variable"] == "DB_PASSWORD"
    assert error.details["setting"] == "database.url"
    assert "DB_PASSWORD" in error.message


def test_the_config_file_is_named_in_the_details() -> None:
    error = config_invalid(
        lambda: resolve_url(PATTERN, name="db1", environ={}, config_file="/w/wtenv.toml")
    )

    assert error.details["file"] == "/w/wtenv.toml"


def test_a_value_is_not_searched_for_placeholders() -> None:
    url = resolve_url(PATTERN, name="db1", environ={"DB_PASSWORD": "{name}"})

    assert url == "postgresql://myapp:{name}@localhost:5432/db1"


@pytest.mark.parametrize("value", ["it's", "two\nlines", "carriage\rreturn"])
def test_a_resolved_url_with_a_quote_or_line_break_is_config_invalid(value: str) -> None:
    error = config_invalid(lambda: resolve_url(PATTERN, name="db1", environ={"DB_PASSWORD": value}))

    assert error.details["setting"] == "database.url"
    assert value not in error.message  # the URL can hold a password (FR-019)
    assert value not in str(error.details)


def test_a_percent_encoded_quote_is_fine() -> None:
    url = resolve_url(PATTERN, name="db1", environ={"DB_PASSWORD": "it%27s"})

    assert "it%27s" in url


# --- wtenv's own connection -------------------------------------------------------------


def test_the_target_is_the_user_password_host_and_port_of_the_pattern() -> None:
    target = postgres_target("postgresql://myapp:s3cret@localhost:5433/wtenv_db")

    assert target == PostgresTarget(host="localhost", port=5433, user="myapp", password="s3cret")


def test_the_driver_suffix_does_not_matter_to_the_target() -> None:
    plain = postgres_target("postgresql://myapp:pw@127.0.0.1:5432/db")
    suffixed = postgres_target("postgresql+psycopg://myapp:pw@127.0.0.1:5432/db")

    assert plain == suffixed


def test_query_parameters_are_not_used() -> None:
    target = postgres_target(
        "postgresql://myapp:pw@localhost/db?sslmode=require&application_name=x"
    )

    assert target == PostgresTarget(host="localhost", port=5432, user="myapp", password="pw")


def test_the_port_defaults_to_5432_and_user_and_password_may_be_missing() -> None:
    target = postgres_target("postgres://localhost/db")

    assert target == PostgresTarget(host="localhost", port=5432, user=None, password=None)


def test_an_ipv6_host_loses_its_brackets() -> None:
    assert postgres_target("postgresql://u@[::1]:5432/db").host == "::1"


def test_user_and_password_are_percent_decoded() -> None:
    target = postgres_target("postgresql://a%40b:p%2Fw%27d@localhost/db")

    assert (target.user, target.password) == ("a@b", "p/w'd")


def test_the_maintenance_database_is_postgres() -> None:
    assert (
        PostgresTarget(host="localhost", port=5432, user=None, password=None).dbname == "postgres"
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u@db.example.com/db",
        "postgresql://u@10.1.2.3/db",
        "postgresql:///db",
        "postgresql://u@localhost/db?host=db.example.com",
        "postgresql://u@localhost/db?hostaddr=10.1.2.3",
        "postgresql://u@localhost/db?service=prod",
        "postgresql://%2Fvar%2Frun%2Fpostgresql/db",
    ],
)
def test_a_target_that_is_not_local_is_config_invalid(url: str) -> None:
    error = config_invalid(lambda: postgres_target(url))

    assert error.details["setting"] == "database.url"


def test_the_password_is_not_shown_when_the_target_is_printed() -> None:
    target = PostgresTarget(host="localhost", port=5432, user="u", password="hunter2")

    assert "hunter2" not in repr(target)
    assert "hunter2" not in str(target)


# --- the URL of a compose-hosted database (T075; FR-018, FR-020; config.md) ----------------------

COMPOSE_PATTERN = "postgresql://postgres:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/app"
PASSWORD = "p@ss/w:rd%41 &#?"  # holds @ / : % and more, none of which may leave the password


def compose_url(pattern: str = COMPOSE_PATTERN, **overrides: object) -> str:
    arguments: dict[str, object] = {
        "port": 20003,
        "compose_environment": {"POSTGRES_PASSWORD": PASSWORD},
        "environ": {},
    }
    return resolve_url(pattern, **{**arguments, **overrides})  # type: ignore[arg-type]


def test_port_is_the_host_port() -> None:
    url = compose_url("postgresql://u:pw@localhost:{port}/app")

    assert url == "postgresql://u:pw@localhost:20003/app"


def test_env_is_still_inserted_as_it_stands_in_a_compose_url() -> None:
    url = compose_url(
        "postgresql://u:{env:DB_PASSWORD}@localhost:{port}/app", environ={"DB_PASSWORD": "a b"}
    )

    assert url == "postgresql://u:a b@localhost:20003/app"


def test_a_compose_env_value_is_percent_encoded_completely() -> None:
    url = compose_url()

    encoded = "p%40ss%2Fw%3Ard%2541%20%26%23%3F"
    assert url == f"postgresql://postgres:{encoded}@localhost:20003/app"
    assert unquote(encoded) == PASSWORD


def test_a_compose_env_value_cannot_move_the_host_or_the_port() -> None:
    hostile = "x@evil.example:5432/other?host=evil.example#"

    url = compose_url(compose_environment={"POSTGRES_PASSWORD": hostile})

    parts = urlsplit(url)
    assert (parts.hostname, parts.port, parts.path, parts.query) == ("localhost", 20003, "/app", "")
    assert unquote(parts.password or "") == hostile


def test_a_compose_url_must_name_a_local_host(tmp_path: Path) -> None:
    """001:FR-025 applied by FR-018: a remote host is refused when the file is read, and
    nothing a value holds can make the resolved URL name another one."""
    (tmp_path / "wtenv.toml").write_text(
        '[compose]\nfile = "compose.yaml"\n\n[database]\ntype = "compose"\nservice = "db"\n'
        'url = "postgresql://postgres:pw@db.example.com:{port}/app"\n',
        encoding="utf-8",
    )

    with pytest.raises(WtenvError) as caught:
        load_config(tmp_path)

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert "db.example.com" not in caught.value.message  # the message never repeats the pattern
    assert urlsplit(compose_url()).hostname == "localhost"


def test_a_name_the_service_does_not_have_is_config_invalid_and_names_the_variable() -> None:
    error = config_invalid(lambda: compose_url(compose_environment={"OTHER": "x"}))

    assert error.details["setting"] == "database.url"
    assert error.details["variable"] == "POSTGRES_PASSWORD"


def test_an_empty_compose_env_value_counts_as_set() -> None:
    url = compose_url(compose_environment={"POSTGRES_PASSWORD": ""})

    assert url == "postgresql://postgres:@localhost:20003/app"


def test_an_unset_env_variable_is_config_invalid_in_a_compose_url_too() -> None:
    error = config_invalid(
        lambda: compose_url("postgresql://u:{env:DB_PASSWORD}@localhost:{port}/app")
    )

    assert error.details["variable"] == "DB_PASSWORD"


STAND_IN_CASES = [
    (COMPOSE_PATTERN, {"OTHER": "x"}, "POSTGRES_PASSWORD"),
    (
        "postgresql://u:{env:DB_PASSWORD}@localhost:{port}/app",
        {"POSTGRES_PASSWORD": "x"},
        "DB_PASSWORD",
    ),
]


@pytest.mark.parametrize(("pattern", "environment", "variable"), STAND_IN_CASES)
def test_the_stand_in_port_of_step_7_fails_for_the_same_two_cases(
    pattern: str, environment: dict[str, str], variable: str
) -> None:
    """Step 7 resolves the pattern with a port of the stand-in block before anything changes."""
    error = config_invalid(
        partial(resolve_url, pattern, port=20000, compose_environment=environment, environ={})
    )

    assert error.details["variable"] == variable


def test_no_error_message_contains_the_resolved_password() -> None:
    secret = "s3cr3t-value-123"
    errors = [
        config_invalid(
            lambda: compose_url(
                "postgresql://u:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/{compose_env:MISSING}",
                compose_environment={"POSTGRES_PASSWORD": secret},
            )
        ),
        config_invalid(
            lambda: compose_url(
                "postgresql://u:{compose_env:POSTGRES_PASSWORD}:{env:GONE}@localhost:{port}/app",
                compose_environment={"POSTGRES_PASSWORD": secret},
            )
        ),
        config_invalid(
            lambda: compose_url(
                "postgresql://u:{env:RAW}@localhost:{port}/app", environ={"RAW": f"{secret}'"}
            )
        ),
    ]

    for error in errors:
        assert secret not in error.message and secret not in str(error.details)
        assert secret not in (error.hint or "")
