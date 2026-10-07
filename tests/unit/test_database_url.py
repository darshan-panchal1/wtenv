"""Resolving the URL pattern, and wtenv's own connection (config.md, The URL pattern; FR-025,
FR-026)."""

import pytest

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
