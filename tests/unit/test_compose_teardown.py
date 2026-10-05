"""Removing a recorded compose project (T090; research.md §4; FR-039, FR-042).

A fake Docker engine holds containers, networks, and volumes with their labels and answers the
commands `wtenv` runs, so no Docker is needed. Resources of another project and resources with no
compose label (an `external` volume or network) are in it too, and must never be named in a
removal command.
"""

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from wtenv import compose
from wtenv.output import Item, ItemKind

PROJECT = "wtenv-app-91c2d0aa"
OTHER = "wtenv-other-12345678"
LABEL = "com.docker.compose.project"
DOWN = ["docker", "compose", "-p", PROJECT, "down", "--volumes", "--remove-orphans"]


@dataclass
class Resource:
    id: str
    name: str
    project: str | None  # the value of the compose project label; None for no label


@dataclass
class FakeDocker:
    """A command runner backed by lists of resources; it records every call."""

    containers: list[Resource] = field(default_factory=list)
    networks: list[Resource] = field(default_factory=list)
    volumes: list[Resource] = field(default_factory=list)
    # What `docker compose down` leaves behind, and what no command can remove, by name.
    down_leaves: set[str] = field(default_factory=set)
    undeletable: set[str] = field(default_factory=set)
    calls: list[tuple[list[str], dict[str, str], Path | None]] = field(default_factory=list)

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        words = list(command)
        self.calls.append((words, dict(environ), cwd))
        if words[:2] == ["docker", "ps"]:
            return _listing(self._labelled(self.containers, words), ids=True)
        if words[:3] == ["docker", "network", "ls"]:
            return _listing(self._labelled(self.networks, words), ids=True)
        if words[:3] == ["docker", "volume", "ls"]:
            return _listing(self._labelled(self.volumes, words), ids=False)
        if words == DOWN:
            assert cwd is not None and cwd.is_dir() and not any(cwd.iterdir())
            for pool in (self.containers, self.networks, self.volumes):
                pool[:] = [r for r in pool if r.project != PROJECT or r.name in self.down_leaves]
            return _done("")
        if words[:2] == ["docker", "rm"]:
            return self._remove(self.containers, words[2])
        if words[:3] == ["docker", "network", "rm"]:
            return self._remove(self.networks, words[3])
        if words[:3] == ["docker", "volume", "rm"]:
            return self._remove(self.volumes, words[3])
        raise AssertionError(f"unexpected command {words}")

    def _labelled(self, pool: list[Resource], words: list[str]) -> list[Resource]:
        wanted = words[words.index("--filter") + 1]
        assert wanted.startswith(f"label={LABEL}=")
        project = wanted.removeprefix(f"label={LABEL}=")
        return [r for r in pool if r.project == project]

    def _remove(self, pool: list[Resource], key: str) -> "subprocess.CompletedProcess[str]":
        found = next((r for r in pool if key in (r.id, r.name)), None)
        if found is None:
            return _done("", returncode=1, stderr=f"Error: No such object: {key}")
        if found.name in self.undeletable:
            return _done("", returncode=1, stderr=f"Error: {found.name} is in use")
        pool.remove(found)
        return _done("")

    def removals(self) -> list[list[str]]:
        """Return the commands that remove something: everything except the listings."""
        listings = (["docker", "ps"], ["docker", "network", "ls"], ["docker", "volume", "ls"])
        return [
            words for words, _, _ in self.calls if not any(words[: len(p)] == p for p in listings)
        ]


def _done(stdout: str, returncode: int = 0, stderr: str = "") -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _listing(found: list[Resource], *, ids: bool) -> "subprocess.CompletedProcess[str]":
    lines = [f"{r.id} {r.name}" if ids else r.name for r in found]
    return _done("".join(f"{line}\n" for line in lines))


def stack() -> FakeDocker:
    """A project with a container, a network, and two volumes, beside things that are not its."""
    return FakeDocker(
        containers=[
            Resource("c1", "app-web-1", PROJECT),
            Resource("c9", "other-web-1", OTHER),
            Resource("c8", "postgres-local", None),
        ],
        networks=[
            Resource("n1", "app_default", PROJECT),
            Resource("n9", "other_default", OTHER),
            Resource("n8", "shared-external", None),
        ],
        volumes=[
            Resource("v1", "app_data", PROJECT),
            Resource("v2", "0123456789abcdef", PROJECT),  # an anonymous volume
            Resource("v9", "other_data", OTHER),
            Resource("v8", "external-data", None),  # declared `external`: no compose label
        ],
    )


def item(kind: ItemKind, name: str) -> Item:
    return Item(kind=kind, name=name)


REMOVED = [
    item(ItemKind.COMPOSE_CONTAINER, "app-web-1"),
    item(ItemKind.COMPOSE_NETWORK, "app_default"),
    item(ItemKind.COMPOSE_VOLUME, "app_data"),
    item(ItemKind.COMPOSE_VOLUME, "0123456789abcdef"),
]


def names(pool: list[Resource]) -> list[str]:
    return [r.name for r in pool]


# --- a project is taken down, and what went is listed ----------------------------------------------


def test_the_project_is_taken_down_and_each_removed_resource_is_an_item() -> None:
    docker = stack()

    result = compose.remove_project(PROJECT, run=docker, environ={})

    assert result.removed == REMOVED
    assert result.already_absent == [] and result.failed == []
    assert names(docker.containers) == ["other-web-1", "postgres-local"]
    assert names(docker.networks) == ["other_default", "shared-external"]
    assert names(docker.volumes) == ["other_data", "external-data"]


def test_the_resources_are_listed_before_and_after_the_down_from_a_directory_with_no_compose_file(
    tmp_path: Path,
) -> None:
    docker = stack()

    compose.remove_project(PROJECT, run=docker, environ={})

    commands = [words for words, _, _ in docker.calls]
    down_at = commands.index(DOWN)
    assert any(c[:2] == ["docker", "ps"] for c in commands[:down_at])
    assert any(c[:2] == ["docker", "ps"] for c in commands[down_at + 1 :])
    assert any(c[:3] == ["docker", "volume", "ls"] for c in commands[:down_at])
    assert any(c[:3] == ["docker", "volume", "ls"] for c in commands[down_at + 1 :])
    down_cwd = docker.calls[down_at][2]
    assert down_cwd is not None and not down_cwd.exists()  # a temporary directory, now gone


def test_a_compose_environment_variable_is_not_passed_on() -> None:
    docker = stack()
    environ = {"PATH": "/usr/bin", "COMPOSE_FILE": "other.yaml", "COMPOSE_PROJECT_NAME": OTHER}

    compose.remove_project(PROJECT, run=docker, environ=environ)

    assert all(
        "COMPOSE_FILE" not in env and "COMPOSE_PROJECT_NAME" not in env
        for _, env, _ in docker.calls
    )
    assert all(env.get("PATH") == "/usr/bin" for _, env, _ in docker.calls)


def test_a_project_with_nothing_left_is_already_absent_and_nothing_is_run() -> None:
    docker = FakeDocker(containers=[Resource("c9", "other-web-1", OTHER)])

    result = compose.remove_project(PROJECT, run=docker, environ={})

    assert result.removed == [] and result.failed == []
    assert result.already_absent == [item(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert docker.removals() == []
    assert names(docker.containers) == ["other-web-1"]


# --- only the recorded project's resources are ever named (FR-039) ------------------------------------


def test_no_removal_command_names_a_resource_of_another_project_or_one_with_no_label() -> None:
    docker = stack()
    docker.down_leaves = {"app-web-1", "app_default", "app_data"}  # force the one-by-one removal

    compose.remove_project(PROJECT, run=docker, environ={})

    mine = {"app-web-1", "c1", "app_default", "n1", "app_data", "v1", "0123456789abcdef", "v2"}
    for words in docker.removals():
        if words == DOWN:
            continue
        assert words[-1] in mine, words
        assert words[:2] == ["docker", "rm"] or words[:3] in (
            ["docker", "network", "rm"],
            ["docker", "volume", "rm"],
        )
    assert names(docker.containers) == ["other-web-1", "postgres-local"]
    assert "external-data" in names(docker.volumes) and "shared-external" in names(docker.networks)


def test_a_project_not_in_the_registry_is_not_touched_because_only_the_given_name_is_asked_for() -> (
    None
):
    docker = stack()

    compose.remove_project(PROJECT, run=docker, environ={})

    assert [r.id for r in docker.containers if r.project == OTHER] == ["c9"]
    assert [r.id for r in docker.networks if r.project == OTHER] == ["n9"]
    assert [r.id for r in docker.volumes if r.project == OTHER] == ["v9"]
    for words, _, _ in docker.calls:
        assert OTHER not in " ".join(words)


# --- what compose down leaves ---------------------------------------------------------------------------


def test_labelled_resources_that_remain_are_removed_one_by_one() -> None:
    docker = stack()
    docker.down_leaves = {"app-web-1", "app_default", "app_data"}

    result = compose.remove_project(PROJECT, run=docker, environ={})

    assert result.removed == REMOVED
    assert ["docker", "rm", "c1"] in docker.removals()
    assert ["docker", "network", "rm", "n1"] in docker.removals()
    assert ["docker", "volume", "rm", "app_data"] in docker.removals()
    assert result.failed == []


def test_what_cannot_be_removed_is_failed_with_a_reason_and_the_rest_is_removed() -> None:
    docker = stack()
    docker.down_leaves = {"app_default"}
    docker.undeletable = {"app_default"}

    result = compose.remove_project(PROJECT, run=docker, environ={})

    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_NETWORK, "app_default")]
    assert "in use" in result.failed[0].reason
    assert [i.name for i in result.removed] == ["app-web-1", "app_data", "0123456789abcdef"]


# --- listing only ---------------------------------------------------------------------------------------


def test_listing_only_returns_the_items_a_removal_would_and_runs_no_removal() -> None:
    listed = stack()
    real = stack()

    planned = compose.remove_project(PROJECT, dry_run=True, run=listed, environ={})
    actual = compose.remove_project(PROJECT, run=real, environ={})

    assert planned.removed == actual.removed == REMOVED
    assert listed.removals() == []
    assert len(listed.containers) == 3 and len(listed.networks) == 3 and len(listed.volumes) == 4


def test_listing_only_for_a_project_with_nothing_left_is_already_absent() -> None:
    docker = FakeDocker()

    result = compose.remove_project(PROJECT, dry_run=True, run=docker, environ={})

    assert result.already_absent == [item(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert docker.removals() == []


# --- no Docker ----------------------------------------------------------------------------------------


def not_installed(
    command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
) -> "subprocess.CompletedProcess[str]":
    raise FileNotFoundError(command[0])


def test_without_docker_the_project_is_failed_and_stays_recorded() -> None:
    result = compose.remove_project(PROJECT, run=not_installed, environ={})

    assert result.removed == [] and result.already_absent == []
    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert "docker" in result.failed[0].reason.lower()


def test_listing_only_without_docker_lists_the_recorded_project() -> None:
    result = compose.remove_project(PROJECT, dry_run=True, run=not_installed, environ={})

    assert result.removed == [item(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert result.failed == []
