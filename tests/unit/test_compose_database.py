"""The compose-hosted database service: its port and where it keeps its data (FR-021, FR-026,
FR-027; data-model.md, "Compose-hosted database" and "Data storage classification"; research R4).

No Docker: the documents are shaped like `docker compose -p <project> config --format json`, in
which a project-scoped volume is named `<project>_<key>` and an explicit `name:` is kept.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from wtenv import compose
from wtenv.compose import ComposeModel
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.output import WarningCode, WarningInfo

PROJECT = "wtenv-feature-x-3f9a1c2b"
DATA = "/var/lib/postgresql/data"


def port(**fields: object) -> dict[str, object]:
    return {"mode": "ingress", "protocol": "tcp", "target": 5432, "published": "20003", **fields}


def volume_mount(source: str | None = None, **fields: object) -> dict[str, object]:
    mount: dict[str, object] = {"type": "volume", "target": DATA, "volume": {}, **fields}
    if source is not None:
        mount["source"] = source
    return mount


def bind_mount(source: Path | str, **fields: object) -> dict[str, object]:
    return {"type": "bind", "source": str(source), "target": DATA, **fields}


def model_of(
    mounts: list[dict[str, object]] | None = None,
    volumes: dict[str, Any] | None = None,
    ports: list[dict[str, object]] | None = None,
    **other_services: object,
) -> ComposeModel:
    db: dict[str, object] = {"image": "postgres:17", "ports": [port()] if ports is None else ports}
    if mounts is not None:
        db["volumes"] = mounts
    document: dict[str, object] = {"name": PROJECT, "services": {"db": db, **other_services}}
    if volumes is not None:
        document["volumes"] = volumes
    return compose.parse_model(document)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "work" / "feature-x"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "elsewhere" / "pgdata"
    path.mkdir(parents=True)
    return path


def storage(model: ComposeModel, root: Path) -> list[WarningInfo]:
    return compose.database_storage(model, "db", root=root, project=PROJECT)


def refused(model: ComposeModel, root: Path) -> WtenvError:
    with pytest.raises(WtenvError) as caught:
        storage(model, root)
    assert caught.value.code is ErrorCode.DATABASE_STORAGE_SHARED
    assert EXIT_STATUS[caught.value.code] == 23
    assert caught.value.details["service"] == "db"
    return caught.value


# --- database_port (FR-021) -----------------------------------------------------------------------


def test_the_one_published_port_of_the_service_is_returned() -> None:
    mapping = compose.database_port(model_of(), "db")

    assert (mapping.service, mapping.target, mapping.protocol, mapping.published) == (
        "db",
        5432,
        "tcp",
        "20003",
    )


def test_a_service_that_is_not_in_the_model_is_config_invalid_and_named() -> None:
    with pytest.raises(WtenvError) as caught:
        compose.database_port(model_of(), "postgres", config_file="/w/wtenv.toml")

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details["setting"] == "database.service"
    assert caught.value.details["file"] == "/w/wtenv.toml"
    assert "postgres" in caught.value.message and "no service" in caught.value.message


@pytest.mark.parametrize(
    ("ports", "found"),
    [
        ([], "no published port"),
        ([{"mode": "ingress", "protocol": "tcp", "target": 5432}], "no published port"),
        ([port(), port(target=5433, published="20004")], "2 published ports"),
    ],
    ids=["none", "container port only", "two"],
)
def test_zero_or_several_published_ports_are_config_invalid_and_say_what_was_found(
    ports: list[dict[str, object]], found: str
) -> None:
    with pytest.raises(WtenvError) as caught:
        compose.database_port(model_of(ports=ports), "db")

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details["setting"] == "database.service"
    assert "db" in caught.value.message and found in caught.value.message


# --- one case per row of the classification table -----------------------------------------------


def kept(storage_class: str, **details: str) -> dict[str, object]:
    return {"service": "db", "storage": storage_class, **details}


def test_no_data_mount_at_all_warns_no_volume(root: Path) -> None:
    (warning,) = storage(model_of(), root)

    assert warning.code is WarningCode.COMPOSE_DATABASE_DATA_KEPT
    assert warning.details == kept("no_volume")


def test_a_volume_without_a_source_warns_anonymous_volume(root: Path) -> None:
    (warning,) = storage(model_of([volume_mount()]), root)

    assert warning.code is WarningCode.COMPOSE_DATABASE_DATA_KEPT
    assert warning.details == kept("anonymous_volume")


def test_an_external_volume_is_refused_naming_its_resolved_name(root: Path) -> None:
    model = model_of(
        [volume_mount("pgdata")], {"pgdata": {"name": "shared-pgdata", "external": True}}
    )

    error = refused(model, root)

    assert error.details["storage"] == "external_volume"
    assert error.details["name"] == "shared-pgdata"


def test_a_volume_with_a_fixed_name_is_refused_naming_it(root: Path) -> None:
    model = model_of([volume_mount("pgdata")], {"pgdata": {"name": "app_pgdata"}})

    error = refused(model, root)

    assert error.details["storage"] == "fixed_name_volume"
    assert error.details["name"] == "app_pgdata"


def test_a_project_scoped_volume_is_accepted_without_a_warning(root: Path) -> None:
    model = model_of([volume_mount("pgdata")], {"pgdata": {"name": f"{PROJECT}_pgdata"}})

    assert storage(model, root) == []


def test_a_bind_inside_the_worktree_warns_worktree_directory(root: Path) -> None:
    (root / "data").mkdir()

    (warning,) = storage(model_of([bind_mount(root / "data")]), root)

    assert warning.code is WarningCode.COMPOSE_DATABASE_DATA_KEPT
    assert warning.details == kept("worktree_directory", name=os.path.realpath(root / "data"))


def test_a_bind_outside_the_worktree_is_refused_naming_the_real_path(
    root: Path, outside: Path
) -> None:
    error = refused(model_of([bind_mount(outside)]), root)

    assert error.details["storage"] == "host_directory"
    assert error.details["name"] == os.path.realpath(outside)


def test_a_bind_through_a_symlink_to_outside_the_worktree_is_refused(
    root: Path, outside: Path
) -> None:
    (root / "data").symlink_to(outside)  # it looks like a directory inside the worktree

    error = refused(model_of([bind_mount(root / "data")]), root)

    assert error.details["storage"] == "host_directory"
    assert error.details["name"] == os.path.realpath(outside)


def test_a_bind_named_like_the_worktree_root_but_outside_it_is_refused(root: Path) -> None:
    sibling = Path(f"{root}-other")  # shares the prefix of the root, and is not inside it
    sibling.mkdir()

    error = refused(model_of([bind_mount(sibling)]), root)

    assert error.details["storage"] == "host_directory"


def device_volume(device: Path, **driver_opts: str) -> dict[str, Any]:
    return {
        "pgdata": {
            "name": f"{PROJECT}_pgdata",
            "driver": "local",
            "driver_opts": {"type": "none", "o": "bind", "device": str(device), **driver_opts},
        }
    }


def test_a_project_named_volume_whose_device_is_outside_the_worktree_is_refused(
    root: Path, outside: Path
) -> None:
    error = refused(model_of([volume_mount("pgdata")], device_volume(outside)), root)

    assert error.details["storage"] == "host_directory"
    assert error.details["name"] == os.path.realpath(outside)


def test_a_device_inside_the_worktree_is_judged_as_a_bind_and_warns(root: Path) -> None:
    (root / "pgdata").mkdir()

    (warning,) = storage(model_of([volume_mount("pgdata")], device_volume(root / "pgdata")), root)

    assert warning.details == kept("worktree_directory", name=os.path.realpath(root / "pgdata"))


# --- the order and the exceptions -------------------------------------------------------------------


def test_the_first_refusal_in_mount_order_is_reported(root: Path, outside: Path) -> None:
    model = model_of(
        [volume_mount(), volume_mount("ext"), bind_mount(outside), volume_mount("fixed")],
        {"ext": {"name": "ext", "external": True}, "fixed": {"name": "app_fixed"}},
    )

    error = refused(model, root)

    assert (error.details["storage"], error.details["name"]) == ("external_volume", "ext")


def test_warnings_are_collected_for_every_accepted_mount(root: Path) -> None:
    (root / "data").mkdir()
    model = model_of([volume_mount(), bind_mount(root / "data")])

    warnings = storage(model, root)

    assert [w.details["storage"] for w in warnings] == ["anonymous_volume", "worktree_directory"]


def test_a_read_only_bind_outside_is_ignored(root: Path, outside: Path) -> None:
    model = model_of(
        [bind_mount(outside, read_only=True), volume_mount("pgdata")],
        {"pgdata": {"name": f"{PROJECT}_pgdata"}},
    )

    assert storage(model, root) == []


def test_a_tmpfs_mount_is_not_a_data_mount(root: Path) -> None:
    model = model_of([{"type": "tmpfs", "target": "/tmp"}])

    (warning,) = storage(model, root)

    assert warning.details == kept("no_volume")


def test_another_services_fixed_name_volume_only_warns(root: Path) -> None:
    web = {"image": "web", "volumes": [volume_mount("shared")]}
    model = model_of(
        [volume_mount("pgdata")],
        {"pgdata": {"name": f"{PROJECT}_pgdata"}, "shared": {"name": "app_shared"}},
        web=web,
    )

    assert storage(model, root) == []
    assert [w.code for w in model.warnings] == [WarningCode.COMPOSE_FIXED_VOLUME_NAME]


# --- volume names are judged under the wtenv project (T112; FR-027; safety finding H1) ------------

WTENV_PROJECT = "wtenv-feature-x-3f9a1c2b"


class AsComposeAnswers:
    """A command runner that answers `config` as Compose does (research R4, R7).

    The model is named by `-p <project>` when given, else by the compose file's `name:` (here
    `app`). A volume without a `name:` resolves to `<model name>_<key>`; a `name:` is kept.
    """

    def __init__(self, volumes: dict[str, dict[str, str]]) -> None:
        self.volumes = volumes
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], environ: object, cwd: object) -> Any:
        import json
        import subprocess

        self.commands.append(list(command))
        name = command[command.index("-p") + 1] if "-p" in command else "app"
        volumes = {
            key: {"name": settings.get("name", f"{name}_{key}"), **settings}
            for key, settings in self.volumes.items()
        }
        document = {
            "name": name,
            "services": {
                "db": {
                    "ports": [{"target": 5432, "published": "1", "protocol": "tcp"}],
                    "volumes": [{"type": "volume", "source": "pgdata", "target": DATA}],
                }
            },
            "volumes": volumes,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(document), "")


def resolved(volumes: dict[str, dict[str, str]], project: str | None) -> ComposeModel:
    return compose.resolve_model(
        Path("/w/compose.yaml"), [], project=project, run=AsComposeAnswers(volumes), environ={}
    )


def test_with_a_project_the_model_and_the_unnamed_volume_carry_the_wtenv_project() -> None:
    model = resolved({"pgdata": {}}, WTENV_PROJECT)

    assert model.document["name"] == WTENV_PROJECT
    assert model.document["volumes"]["pgdata"]["name"] == f"{WTENV_PROJECT}_pgdata"


def test_without_a_project_they_carry_the_compose_files_name() -> None:
    model = resolved({"pgdata": {}}, None)

    assert model.document["name"] == "app"
    assert model.document["volumes"]["pgdata"]["name"] == "app_pgdata"


@pytest.mark.parametrize("project", [WTENV_PROJECT, None])
def test_an_explicit_name_is_kept_as_written_either_way(project: str | None) -> None:
    model = resolved({"pgdata": {"name": "app_pgdata"}}, project)

    assert model.document["volumes"]["pgdata"]["name"] == "app_pgdata"


def test_a_name_that_starts_with_the_repositorys_own_project_is_shared_under_the_wtenv_project(
    root: Path,
) -> None:
    """H1: compose `name: app`, volume `name: app_pgdata`; the wtenv project is not `app`."""
    model = resolved({"pgdata": {"name": "app_pgdata"}}, WTENV_PROJECT)

    with pytest.raises(WtenvError) as caught:
        compose.database_storage(model, "db", root=root, project=WTENV_PROJECT)

    assert caught.value.code is ErrorCode.DATABASE_STORAGE_SHARED
    assert (caught.value.details["storage"], caught.value.details["name"]) == (
        "fixed_name_volume",
        "app_pgdata",
    )


def test_an_unnamed_volume_is_project_scoped_under_the_wtenv_project(root: Path) -> None:
    model = resolved({"pgdata": {}}, WTENV_PROJECT)

    assert compose.database_storage(model, "db", root=root, project=WTENV_PROJECT) == []


# --- a volume device that is not an absolute path (T125; FR-027, safety finding M4) -------------

RELATIVE_DEVICES = {
    "nfs, no path": {"type": "nfs", "o": "addr=10.0.0.5,rw", "device": ":/exports/pg"},
    "nfs, relative": {"type": "nfs", "o": "addr=10.0.0.5,rw", "device": "exports/pg"},
    "cifs, relative": {"type": "cifs", "o": "addr=10.0.0.6", "device": "nas/pg"},
    "bind, dot slash": {"type": "none", "o": "bind", "device": "./data"},
    "bind, bare name": {"type": "none", "o": "bind", "device": "data"},
}


@pytest.mark.parametrize("options", RELATIVE_DEVICES.values(), ids=list(RELATIVE_DEVICES))
def test_a_device_that_is_not_an_absolute_path_is_refused_as_written(
    options: dict[str, str], root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The current directory is the worktree root, so a device resolved against it would be
    judged to be inside the worktree. It is resolved against nothing."""
    monkeypatch.chdir(root)
    (root / "data").mkdir()
    (root / "exports" / "pg").mkdir(parents=True)
    (root / "nas" / "pg").mkdir(parents=True)
    volumes = {"pgdata": {"name": f"{PROJECT}_pgdata", "driver_opts": options}}

    error = refused(model_of([volume_mount("pgdata")], volumes), root)

    assert error.details["storage"] == "device_not_absolute"
    assert error.details["name"] == options["device"]


def test_an_absolute_device_is_still_judged_as_a_bind(
    root: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(root)
    (root / "pgdata").mkdir()
    inside = device_volume(root / "pgdata")
    far = device_volume(outside)

    (warning,) = storage(model_of([volume_mount("pgdata")], inside), root)
    error = refused(model_of([volume_mount("pgdata")], far), root)

    assert warning.details["storage"] == "worktree_directory"
    assert error.details["storage"] == "host_directory"


def test_a_volume_without_driver_opts_is_unchanged(root: Path) -> None:
    model = model_of([volume_mount("pgdata")], {"pgdata": {"name": f"{PROJECT}_pgdata"}})

    assert storage(model, root) == []


def test_an_external_volume_is_refused_even_when_its_name_looks_project_scoped(root: Path) -> None:
    """`external: true` is judged by the flag: a volume that already exists under a name like
    `<project>_<key>` is still not this project's to create or to remove."""
    model = model_of(
        [volume_mount("pgdata")], {"pgdata": {"name": f"{PROJECT}_pgdata", "external": True}}
    )

    error = refused(model, root)

    assert error.details["storage"] == "external_volume"
    assert error.details["name"] == f"{PROJECT}_pgdata"


def test_a_model_resolved_under_another_name_is_never_accepted(root: Path) -> None:
    """The comparison is with `<project>_<key>`, the wtenv project, and not with the model's own
    name: an unnamed volume of a model resolved without `-p` is `app_pgdata`, which is not it."""
    model = resolved({"pgdata": {}}, None)

    with pytest.raises(WtenvError) as caught:
        compose.database_storage(model, "db", root=root, project=WTENV_PROJECT)

    assert caught.value.details["storage"] == "fixed_name_volume"
    assert caught.value.details["name"] == "app_pgdata"
