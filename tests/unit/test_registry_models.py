"""The registry models and their rules (data-model.md, "Registry file" and "Registry entry")."""

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import wtenv.registry as registry_module
from wtenv.registry import (
    ComposeRecord,
    DatabaseRecord,
    EnvFileRecord,
    HookRecord,
    PortBlock,
    PublishedPort,
    Registry,
    VariablePort,
    WorktreeEntry,
)

# The example of data-model.md, "Registry file", unchanged.
EXAMPLE = """
{
  "version": 1,
  "worktrees": {
    "/code/app/.git/worktrees/feature-x": {
      "git_dir": "/code/app/.git/worktrees/feature-x",
      "path": "/code/feature-x",
      "repository": "/code/app/.git",
      "state": "provisioned",
      "block": {"start": 20010, "size": 10},
      "ports": [
        {"variable": "PORT", "port": 20010},
        {"variable": "DB_PORT", "port": 20011}
      ],
      "published": [
        {"service": "cache", "target": 6379, "protocol": "tcp", "host_ip": null, "port": 20012, "variable": null},
        {"service": "db", "target": 5432, "protocol": "tcp", "host_ip": null, "port": 20011, "variable": "DB_PORT"}
      ],
      "env_file": {"path": ".env.local", "created_file": true, "added_newline": false, "state": "created"},
      "databases": [
        {"kind": "postgres", "name": "wtenv_feature_x_3f9a1c2b", "host": "localhost", "port": 5432, "user": "myapp", "path": null, "state": "created"}
      ],
      "compose": {
        "project": "wtenv-feature-x-3f9a1c2b",
        "file": "compose.yaml",
        "override": "compose.override.yaml",
        "override_state": "created"
      },
      "exclude_patterns": ["/.env.local", "/compose.override.yaml"]
    }
  },
  "hooks": {
    "/code/app/.git": {"hook_file": "/code/app/.git/hooks/post-checkout", "created_file": true}
  }
}
"""

KEY = "/code/app/.git/worktrees/feature-x"


def entry_data(**changes: Any) -> dict[str, Any]:
    """A valid entry as plain data, with `changes` applied."""
    data: dict[str, Any] = {
        "git_dir": KEY,
        "path": "/code/feature-x",
        "repository": "/code/app/.git",
        "state": "incomplete",
        "block": {"start": 20010, "size": 10},
    }
    data.update(changes)
    return data


def registry_data(**entry_changes: Any) -> dict[str, Any]:
    """A valid registry holding one entry, with `entry_changes` applied to the entry."""
    return {"version": 1, "worktrees": {KEY: entry_data(**entry_changes)}}


def postgres(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": "postgres",
        "name": "wtenv_feature_x_3f9a1c2b",
        "host": "localhost",
        "port": 5432,
        "user": "myapp",
        "state": "created",
    }
    data.update(changes)
    return data


def sqlite(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"kind": "sqlite", "path": ".wtenv/dev.sqlite3", "state": "created"}
    data.update(changes)
    return data


def rejects(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Registry.model_validate(data)


# --- the example ----------------------------------------------------------------------


def test_the_example_loads_and_dumps_back_to_equal_json() -> None:
    registry = Registry.model_validate_json(EXAMPLE)

    assert json.loads(registry.model_dump_json()) == json.loads(EXAMPLE)


def test_the_example_loads_into_the_documented_models() -> None:
    registry = Registry.model_validate_json(EXAMPLE)
    entry = registry.worktrees[KEY]

    assert registry.version == 1
    assert isinstance(entry, WorktreeEntry)
    assert entry.block == PortBlock(start=20010, size=10)
    assert entry.ports == [
        VariablePort(variable="PORT", port=20010),
        VariablePort(variable="DB_PORT", port=20011),
    ]
    assert isinstance(entry.published[0], PublishedPort)
    assert isinstance(entry.env_file, EnvFileRecord)
    assert isinstance(entry.databases[0], DatabaseRecord)
    assert isinstance(entry.compose, ComposeRecord)
    assert isinstance(registry.hooks["/code/app/.git"], HookRecord)


def test_an_empty_registry_is_version_one_with_nothing_in_it() -> None:
    registry = Registry(version=1)

    assert registry.version == 1
    assert registry.worktrees == {}
    assert registry.hooks == {}


# --- unknown fields, and no credentials (FR-019) --------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        {"version": 1, "extra": 1},
        registry_data(extra=1),
        registry_data(block={"start": 20010, "size": 10, "extra": 1}),
        registry_data(ports=[{"variable": "PORT", "port": 20010, "extra": 1}]),
        registry_data(
            env_file={
                "path": "p",
                "created_file": True,
                "added_newline": False,
                "state": "created",
                "extra": 1,
            }
        ),
        registry_data(databases=[postgres(extra=1)]),
        registry_data(
            compose={
                "project": "p",
                "file": "f",
                "override": "o",
                "override_state": "created",
                "extra": 1,
            }
        ),
        {"version": 1, "hooks": {"/r": {"hook_file": "/h", "created_file": True, "extra": 1}}},
    ],
)
def test_unknown_fields_are_rejected(data: dict[str, Any]) -> None:
    rejects(data)


def _all_models() -> list[type[BaseModel]]:
    return [
        value
        for value in vars(registry_module).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value is not BaseModel
        and value.__module__ == registry_module.__name__
    ]


def test_no_field_is_named_like_a_credential() -> None:
    names = {name for model in _all_models() for name in model.model_fields}

    assert len(_all_models()) >= 9
    assert [
        n for n in names if any(w in n.lower() for w in ("pass", "secret", "token", "pwd"))
    ] == []


def test_a_password_cannot_be_stored_in_a_database_record() -> None:
    rejects(registry_data(databases=[postgres(password="s3cr3t")]))


# --- rule: git_dir equals its key -----------------------------------------------------


def test_git_dir_equal_to_its_key_is_accepted() -> None:
    Registry.model_validate(registry_data())


def test_git_dir_different_from_its_key_is_rejected() -> None:
    rejects(registry_data(git_dir="/code/app/.git/worktrees/other"))


# --- rule: entry state ----------------------------------------------------------------


@pytest.mark.parametrize("state", ["incomplete", "provisioned"])
def test_an_entry_state_of_incomplete_or_provisioned_is_accepted(state: str) -> None:
    Registry.model_validate(registry_data(state=state))


@pytest.mark.parametrize("state", ["creating", "created", "unprovisioned", "orphaned", ""])
def test_any_other_entry_state_is_rejected(state: str) -> None:
    rejects(registry_data(state=state))


# --- rule: the port block -------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "size"),
    [
        (20000, 10),
        (20010, 10),
        (29990, 10),
        (20000, 1),
        (29999, 1),
        (20000, 1000),
        (29000, 1000),
        (20007, 7),
        (29989, 7),
        (29500, 500),
    ],
)
def test_a_block_that_starts_on_a_multiple_of_its_size_inside_the_range_is_accepted(
    start: int, size: int
) -> None:
    PortBlock(start=start, size=size)


@pytest.mark.parametrize(
    ("start", "size"),
    [(20005, 10), (20001, 10), (19990, 10), (19999, 1), (10000, 10), (30000, 10)],
)
def test_a_block_that_does_not_start_at_20000_plus_a_multiple_of_its_size_is_rejected(
    start: int, size: int
) -> None:
    with pytest.raises(ValidationError):
        PortBlock(start=start, size=size)


@pytest.mark.parametrize(("start", "size"), [(29996, 7), (29999, 3), (30000, 500), (30000, 1)])
def test_a_block_that_ends_after_29999_is_rejected(start: int, size: int) -> None:
    with pytest.raises(ValidationError):
        PortBlock(start=start, size=size)


@pytest.mark.parametrize("size", [0, -1, 1001, 5000])
def test_a_block_size_outside_1_to_1000_is_rejected(size: int) -> None:
    with pytest.raises(ValidationError):
        PortBlock(start=20000, size=size)


def test_a_block_in_an_entry_is_checked_too() -> None:
    rejects(registry_data(block={"start": 20005, "size": 10}))


# --- rule: published ports ------------------------------------------------------------


def published(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "service": "db",
        "target": 5432,
        "protocol": "tcp",
        "host_ip": None,
        "port": 20011,
        "variable": None,
    }
    data.update(changes)
    return data


@pytest.mark.parametrize("protocol", ["tcp", "udp"])
def test_a_published_protocol_of_tcp_or_udp_is_accepted(protocol: str) -> None:
    Registry.model_validate(registry_data(published=[published(protocol=protocol)]))


@pytest.mark.parametrize("protocol", ["sctp", "TCP", ""])
def test_any_other_published_protocol_is_rejected(protocol: str) -> None:
    rejects(registry_data(published=[published(protocol=protocol)]))


def test_a_published_port_may_have_a_host_ip_and_a_variable() -> None:
    port = PublishedPort.model_validate(published(host_ip="127.0.0.1", variable="DB_PORT"))

    assert port.host_ip == "127.0.0.1"
    assert port.variable == "DB_PORT"


# --- rule: databases ------------------------------------------------------------------


def test_one_database_of_each_kind_is_accepted() -> None:
    Registry.model_validate(registry_data(databases=[postgres(), sqlite()]))


@pytest.mark.parametrize("databases", [[postgres(), postgres(name="other")], [sqlite(), sqlite()]])
def test_two_databases_of_one_kind_are_rejected(databases: list[dict[str, Any]]) -> None:
    rejects(registry_data(databases=databases))


@pytest.mark.parametrize("kind", ["mysql", "Postgres", ""])
def test_a_database_kind_other_than_postgres_or_sqlite_is_rejected(kind: str) -> None:
    rejects(registry_data(databases=[postgres(kind=kind)]))


def test_a_sqlite_record_has_a_path_and_no_server() -> None:
    record = DatabaseRecord.model_validate(sqlite())

    assert record.path == ".wtenv/dev.sqlite3"
    assert (record.name, record.host, record.port, record.user) == (None, None, None, None)


# --- rule: resource states ------------------------------------------------------------


@pytest.mark.parametrize("state", ["creating", "created", "removing"])
def test_every_resource_state_is_accepted(state: str) -> None:
    Registry.model_validate(
        registry_data(
            env_file={
                "path": ".env.local",
                "created_file": True,
                "added_newline": False,
                "state": state,
            },
            databases=[postgres(state=state)],
            compose={
                "project": "p",
                "file": "compose.yaml",
                "override": "o.yaml",
                "override_state": state,
            },
        )
    )


@pytest.mark.parametrize("state", ["gone", "provisioned", "", "CREATED"])
def test_any_other_resource_state_is_rejected(state: str) -> None:
    rejects(
        registry_data(
            env_file={"path": "p", "created_file": True, "added_newline": False, "state": state}
        )
    )
    rejects(registry_data(databases=[postgres(state=state)]))
    rejects(
        registry_data(
            compose={"project": "p", "file": "f", "override": "o", "override_state": state}
        )
    )
