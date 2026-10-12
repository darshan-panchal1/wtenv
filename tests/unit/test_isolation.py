"""The environment that makes Compose load the isolation file (data-model.md, "Isolation
environment"; FR-003, FR-033). The `exec` checks are added in T052."""

from pathlib import Path

import pytest

from wtenv import isolation
from wtenv.output import ResourceState
from wtenv.registry import ComposeRecord

ROOT = Path("/code/app-feature")
ALLOWED_KEYS = {"COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR", "COMPOSE_ENV_FILES"}


def record(
    file: str = "compose.yaml", repository_override: str | None = "compose.override.yml"
) -> ComposeRecord:
    """A compose record in the exec-only arrangement."""
    return ComposeRecord(
        project="wtenv-app-feature-3f9a1c2b",
        file=file,
        override=isolation.ISOLATION_FILE,
        override_state=ResourceState.CREATED,
        repository_override=repository_override,
    )


def test_compose_file_names_the_three_files_in_order_as_absolute_paths() -> None:
    environment = isolation.compose_environment(ROOT, record())

    assert environment["COMPOSE_FILE"] == (
        "/code/app-feature/compose.yaml"
        ":/code/app-feature/compose.override.yml"
        ":/code/app-feature/.wtenv/compose.isolation.yaml"
    )


def test_the_path_separator_is_a_colon() -> None:
    assert isolation.compose_environment(ROOT, record())["COMPOSE_PATH_SEPARATOR"] == ":"


def test_a_nested_compose_file_and_override_keep_their_directory() -> None:
    environment = isolation.compose_environment(
        ROOT, record("deploy/compose.yaml", "deploy/docker-compose.override.yml")
    )

    assert environment["COMPOSE_FILE"] == (
        "/code/app-feature/deploy/compose.yaml"
        ":/code/app-feature/deploy/docker-compose.override.yml"
        ":/code/app-feature/.wtenv/compose.isolation.yaml"
    )


def test_every_key_is_one_the_isolation_environment_may_hold() -> None:
    # T052/T120 add COMPOSE_ENV_FILES, so the key set is not asserted to be the first two.
    assert set(isolation.compose_environment(ROOT, record())) <= ALLOWED_KEYS


def test_the_project_name_is_never_set() -> None:
    assert "COMPOSE_PROJECT_NAME" not in isolation.compose_environment(ROOT, record())


def test_a_record_without_a_repository_override_has_no_isolation_environment() -> None:
    with pytest.raises(ValueError, match="repository"):
        isolation.compose_environment(ROOT, record(repository_override=None))


def test_the_isolation_file_is_the_one_override_problem_accepts() -> None:
    assert isolation.ISOLATION_FILE == ".wtenv/compose.isolation.yaml"
