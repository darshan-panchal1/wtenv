"""Mapping what the Postgres server and driver say to wtenv's errors (research.md section 3;
FR-019, FR-082)."""

import psycopg
import pytest
from psycopg import errors

from wtenv.database import PostgresTarget, check_postgres_version, map_postgres_error
from wtenv.errors import ErrorCode, WtenvError

PASSWORD = "hunter2-very-secret"
TARGET = PostgresTarget(host="localhost", port=5432, user="myapp", password=PASSWORD)
TEMPLATE = "myapp_template"
NAME = "wtenv_feature_x_3f9a1c2b"


def mapped(error: Exception, connections: int | None = None) -> WtenvError:
    result = map_postgres_error(
        error, target=TARGET, template=TEMPLATE, name=NAME, connections=connections
    )
    assert result is not None
    return result


# --- the table of research.md section 3 -------------------------------------------------


def test_object_in_use_is_template_in_use_with_the_connection_count() -> None:
    error = mapped(errors.ObjectInUse("source database is being accessed"), connections=2)

    assert error.code is ErrorCode.TEMPLATE_IN_USE
    assert error.details == {"kind": "postgres", "template": TEMPLATE, "connections": 2}
    assert TEMPLATE in error.message and "2" in error.message


def test_object_in_use_without_a_known_count_leaves_the_count_out() -> None:
    error = mapped(errors.ObjectInUse("in use"))

    assert error.code is ErrorCode.TEMPLATE_IN_USE
    assert error.details == {"kind": "postgres", "template": TEMPLATE}


def test_invalid_catalog_name_is_template_missing() -> None:
    error = mapped(errors.InvalidCatalogName("database does not exist"))

    assert error.code is ErrorCode.TEMPLATE_MISSING
    assert error.details == {"kind": "postgres", "template": TEMPLATE}


def test_duplicate_database_is_an_ownership_conflict() -> None:
    error = mapped(errors.DuplicateDatabase("already exists"))

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "postgres_database", "name": NAME}


def test_permission_denied_says_the_template_needs_is_template_or_ownership() -> None:
    error = mapped(errors.InsufficientPrivilege("permission denied to copy database"))

    assert error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert error.details == {"dependency": "postgres", "reason": "permission_denied"}
    assert "IS_TEMPLATE" in error.message and "ownership" in error.message


def test_a_refused_connection_is_cannot_connect() -> None:
    error = mapped(psycopg.OperationalError("connection failed: Connection refused"))

    assert error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert error.details == {"dependency": "postgres", "reason": "cannot_connect"}
    assert "localhost" in error.message and "5432" in error.message


@pytest.mark.parametrize(
    "text",
    [
        'connection failed: FATAL:  password authentication failed for user "myapp"',
        "connection failed: fe_sendauth: no password supplied",
    ],
)
def test_a_failed_login_is_authentication_failed(text: str) -> None:
    error = mapped(psycopg.OperationalError(text))

    assert error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert error.details == {"dependency": "postgres", "reason": "authentication_failed"}


def test_an_error_from_the_server_login_class_is_authentication_failed() -> None:
    error = mapped(errors.InvalidPassword("bad password"))

    assert error.details["reason"] == "authentication_failed"


def test_an_error_wtenv_does_not_know_is_not_mapped() -> None:
    result = map_postgres_error(
        errors.SyntaxError("oops"), target=TARGET, template=TEMPLATE, name=NAME
    )

    assert result is None
    assert map_postgres_error(ValueError("x"), target=TARGET, template=TEMPLATE, name=NAME) is None


# --- the server version (research.md section 3: 13 or later) ----------------------------


def test_a_server_older_than_13_is_too_old() -> None:
    with pytest.raises(WtenvError) as caught:
        check_postgres_version(120004, TARGET)

    error = caught.value
    assert error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert error.details == {
        "dependency": "postgres",
        "reason": "too_old",
        "required": "13",
        "found": "12.4",
    }


@pytest.mark.parametrize("version", [130000, 130021, 170011, 180001])
def test_server_13_and_later_are_accepted(version: int) -> None:
    check_postgres_version(version, TARGET)


# --- no password anywhere (FR-019) ------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [
        errors.ObjectInUse(f"in use {PASSWORD}"),
        errors.InvalidCatalogName(f"missing {PASSWORD}"),
        errors.DuplicateDatabase(f"exists {PASSWORD}"),
        errors.InsufficientPrivilege(f"denied {PASSWORD}"),
        errors.InvalidPassword(f"bad {PASSWORD}"),
        psycopg.OperationalError(f"connection failed {PASSWORD}"),
    ],
)
def test_no_message_or_detail_contains_the_password(raised: Exception) -> None:
    error = mapped(raised, connections=1)

    assert PASSWORD not in error.message
    assert PASSWORD not in str(error.hint)
    assert PASSWORD not in str(error.details)


def test_the_too_old_error_does_not_contain_the_password() -> None:
    with pytest.raises(WtenvError) as caught:
        check_postgres_version(110000, TARGET)

    assert PASSWORD not in caught.value.message
    assert PASSWORD not in str(caught.value.details)
