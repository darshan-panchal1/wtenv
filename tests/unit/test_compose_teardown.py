"""Removing a recorded compose project (T090, T162; research.md §4; FR-039, FR-041, FR-042).

A fake Docker engine holds containers, networks, and volumes with their labels and answers the
commands `wtenv` runs, so no Docker is needed. Resources of another project and resources with no
compose label (an `external` volume or network) are in it too, and must never be named in a
removal command. Like the real engine, the fake labels only named volumes: an anonymous volume and
an external volume carry no `com.docker.compose.project`, and are tied to a project only by the
mounts of its containers (reading R6).
"""

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from wtenv import compose, registry, teardown
from wtenv.identity import short_id
from wtenv.output import Item, ItemKind, KeptVolume, ResourceState
from wtenv.registry import ComposeRecord, PortBlock, WorktreeEntry

GIT_DIR = "/repos/app/.git/worktrees/app"
PROJECT = compose.project_name("app", GIT_DIR)
OTHER = "wtenv-other-12345678"
LABEL = "com.docker.compose.project"
DOWN = ["docker", "compose", "-p", PROJECT, "down", "--remove-orphans"]
INSPECT = ["docker", "container", "inspect"]


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
    # The volumes each container mounts, by container id.
    mounts: dict[str, list[str]] = field(default_factory=dict)
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
        if words[:3] == INSPECT:
            return self._inspect(words[words.index("--format") + 2 :])
        if words == DOWN:
            assert cwd is not None and cwd.is_dir() and not any(cwd.iterdir())
            # Without `--volumes`, `docker compose down` leaves every volume, labelled or not.
            for pool in (self.containers, self.networks):
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

    def _inspect(self, ids: list[str]) -> "subprocess.CompletedProcess[str]":
        """Print the name of each volume the given containers mount, as the format asks."""
        known = {r.id for r in self.containers}
        missing = [i for i in ids if i not in known]
        if missing:
            return _done("", returncode=1, stderr=f"Error: No such container: {missing[0]}")
        return _done("".join(f"{name}\n" for i in ids for name in self.mounts.get(i, [])))

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
        listings = (
            ["docker", "ps"],
            ["docker", "network", "ls"],
            ["docker", "volume", "ls"],
            INSPECT,
        )
        return [
            words for words, _, _ in self.calls if not any(words[: len(p)] == p for p in listings)
        ]


def _done(stdout: str, returncode: int = 0, stderr: str = "") -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _listing(found: list[Resource], *, ids: bool) -> "subprocess.CompletedProcess[str]":
    lines = [f"{r.id} {r.name}" if ids else r.name for r in found]
    return _done("".join(f"{line}\n" for line in lines))


def stack() -> FakeDocker:
    """A project with a container, a network, and three volumes, beside things that are not its.

    Its container mounts a named volume (labelled), an anonymous volume, and an `external` one
    (neither labelled). `stray-data` carries no label and no project's container mounts it.
    """
    return FakeDocker(
        containers=[
            Resource("c1", "app-web-1", PROJECT),
            Resource("c9", "other-web-1", OTHER),
            Resource("c8", "postgres-local", None),
        ],
        mounts={
            "c1": ["app_data", "0123456789abcdef", "external-data"],
            "c9": ["other_data"],
            "c8": ["postgres-data"],
        },
        networks=[
            Resource("n1", "app_default", PROJECT),
            Resource("n9", "other_default", OTHER),
            Resource("n8", "shared-external", None),
        ],
        volumes=[
            Resource("v1", "app_data", PROJECT),
            Resource("v2", "0123456789abcdef", None),  # an anonymous volume: no compose label
            Resource("v9", "other_data", OTHER),
            Resource("v8", "external-data", None),  # declared `external`: no compose label
            Resource("v7", "stray-data", None),
            Resource("v6", "postgres-data", None),
        ],
    )


def item(kind: ItemKind, name: str) -> Item:
    return Item(kind=kind, name=name)


REMOVED = [
    item(ItemKind.COMPOSE_CONTAINER, "app-web-1"),
    item(ItemKind.COMPOSE_NETWORK, "app_default"),
    item(ItemKind.COMPOSE_VOLUME, "app_data"),
]
# Mounted by the project's container, and without its label: found, kept, never removed.
KEPT = [
    KeptVolume(name="0123456789abcdef", project=PROJECT, reason="unlabelled"),
    KeptVolume(name="external-data", project=PROJECT, reason="unlabelled"),
]
UNLABELLED = {"0123456789abcdef", "v2", "external-data", "v8", "stray-data", "v7"}


def names(pool: list[Resource]) -> list[str]:
    return [r.name for r in pool]


# --- a project is taken down, and what went is listed ----------------------------------------------


def test_the_project_is_taken_down_and_each_removed_resource_is_an_item() -> None:
    docker = stack()

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert result.removed == REMOVED
    assert result.already_absent == [] and result.failed == []
    assert names(docker.containers) == ["other-web-1", "postgres-local"]
    assert names(docker.networks) == ["other_default", "shared-external"]
    assert names(docker.volumes) == [
        "0123456789abcdef",  # anonymous: kept
        "other_data",
        "external-data",
        "stray-data",
        "postgres-data",
    ]


def test_the_resources_are_listed_before_and_after_the_down_from_a_directory_with_no_compose_file(
    tmp_path: Path,
) -> None:
    docker = stack()

    compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

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

    compose.remove_project(PROJECT, GIT_DIR, run=docker, environ=environ)

    assert all(
        "COMPOSE_FILE" not in env and "COMPOSE_PROJECT_NAME" not in env
        for _, env, _ in docker.calls
    )
    assert all(env.get("PATH") == "/usr/bin" for _, env, _ in docker.calls)


def test_a_project_with_nothing_left_is_already_absent_and_nothing_is_run() -> None:
    docker = FakeDocker(containers=[Resource("c9", "other-web-1", OTHER)])

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert result.removed == [] and result.failed == []
    assert result.already_absent == [item(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert docker.removals() == []
    assert names(docker.containers) == ["other-web-1"]


# --- only the recorded project's resources are ever named (FR-039) ------------------------------------


def test_no_removal_command_names_a_resource_of_another_project_or_one_with_no_label() -> None:
    docker = stack()
    docker.down_leaves = {"app-web-1", "app_default", "app_data"}  # force the one-by-one removal

    compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    mine = {"app-web-1", "c1", "app_default", "n1", "app_data", "v1"}
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

    compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert [r.id for r in docker.containers if r.project == OTHER] == ["c9"]
    assert [r.id for r in docker.networks if r.project == OTHER] == ["n9"]
    assert [r.id for r in docker.volumes if r.project == OTHER] == ["v9"]
    for words, _, _ in docker.calls:
        assert OTHER not in " ".join(words)


# --- what compose down leaves ---------------------------------------------------------------------------


def test_labelled_resources_that_remain_are_removed_one_by_one() -> None:
    docker = stack()
    docker.down_leaves = {"app-web-1", "app_default", "app_data"}

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert result.removed == REMOVED
    assert ["docker", "rm", "c1"] in docker.removals()
    assert ["docker", "network", "rm", "n1"] in docker.removals()
    assert ["docker", "volume", "rm", "app_data"] in docker.removals()
    assert result.failed == []


def test_what_cannot_be_removed_is_failed_with_a_reason_and_the_rest_is_removed() -> None:
    docker = stack()
    docker.down_leaves = {"app_default"}
    docker.undeletable = {"app_default"}

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_NETWORK, "app_default")]
    assert "in use" in result.failed[0].reason
    assert [i.name for i in result.removed] == ["app-web-1", "app_data"]


# --- listing only ---------------------------------------------------------------------------------------


def test_listing_only_returns_the_items_a_removal_would_and_runs_no_removal() -> None:
    listed = stack()
    real = stack()

    planned = compose.remove_project(PROJECT, GIT_DIR, dry_run=True, run=listed, environ={})
    actual = compose.remove_project(PROJECT, GIT_DIR, run=real, environ={})

    assert planned.removed == actual.removed == REMOVED
    assert listed.removals() == []
    assert len(listed.containers) == 3 and len(listed.networks) == 3 and len(listed.volumes) == 6


def test_listing_only_for_a_project_with_nothing_left_is_already_absent() -> None:
    docker = FakeDocker()

    result = compose.remove_project(PROJECT, GIT_DIR, dry_run=True, run=docker, environ={})

    assert result.already_absent == [item(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert docker.removals() == []


# --- volumes: only the labelled ones are removed, the others are kept (T162; reading R6) ------------


def test_docker_compose_down_runs_without_volumes() -> None:
    docker = stack()

    compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    downs = [words for words, _, _ in docker.calls if words[:3] == ["docker", "compose", "-p"]]
    assert downs == [DOWN]
    assert all("--volumes" not in words and "-v" not in words for words, _, _ in docker.calls)


def test_a_labelled_volume_is_removed_by_name_as_its_own_item() -> None:
    docker = stack()

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert item(ItemKind.COMPOSE_VOLUME, "app_data") in result.removed
    assert ["docker", "volume", "rm", "app_data"] in docker.removals()
    assert "app_data" not in names(docker.volumes)


def test_a_mounted_volume_without_the_label_is_kept_reported_and_never_removed() -> None:
    docker = stack()

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert result.kept_volumes == KEPT  # in name order, with the recorded project name
    assert result.failed == []
    assert not any(
        i.kind is ItemKind.COMPOSE_VOLUME and i.name in UNLABELLED for i in result.removed
    )
    for words in docker.removals():
        assert not UNLABELLED & set(words), words
    assert {"0123456789abcdef", "external-data", "stray-data", "postgres-data"} <= set(
        names(docker.volumes)
    )


def test_a_volume_that_no_container_of_the_project_mounts_is_not_reported() -> None:
    docker = stack()

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    reported = {k.name for k in result.kept_volumes}
    assert "stray-data" not in reported  # unlabelled, but nothing ties it to the project
    assert (
        "postgres-data" not in reported
    )  # mounted by `postgres-local`, which is not the project's
    assert "other_data" not in reported  # another project's
    assert "app_data" not in reported  # labelled: removed, not kept


def test_the_mounts_are_inspected_before_the_down_while_the_containers_exist() -> None:
    docker = stack()

    compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    commands = [words for words, _, _ in docker.calls]
    inspections = [i for i, words in enumerate(commands) if words[:3] == INSPECT]
    assert len(inspections) == 1 and inspections[0] < commands.index(DOWN)
    inspected = commands[inspections[0]]
    assert inspected[-1] == "c1"  # only the project's container, named by id
    assert "c8" not in inspected and "c9" not in inspected


def test_listing_only_reports_the_same_kept_volumes_and_removes_nothing() -> None:
    docker = stack()

    planned = compose.remove_project(PROJECT, GIT_DIR, dry_run=True, run=docker, environ={})

    assert planned.kept_volumes == KEPT
    assert planned.removed == REMOVED
    assert docker.removals() == []
    assert any(words[:3] == INSPECT for words, _, _ in docker.calls)
    assert len(docker.volumes) == 6 and len(docker.containers) == 3 and len(docker.networks) == 3


def test_a_project_with_no_container_has_nothing_to_inspect() -> None:
    docker = FakeDocker(
        volumes=[Resource("v1", "app_data", PROJECT), Resource("v2", "0123456789abcdef", None)]
    )

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert result.removed == [item(ItemKind.COMPOSE_VOLUME, "app_data")]
    assert result.kept_volumes == []
    assert not any(words[:3] == INSPECT for words, _, _ in docker.calls)
    assert names(docker.volumes) == ["0123456789abcdef"]


def test_a_volume_that_cannot_be_removed_is_failed_and_the_kept_ones_are_still_reported() -> None:
    docker = stack()
    docker.undeletable = {"app_data"}

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_VOLUME, "app_data")]
    assert result.kept_volumes == KEPT


def test_when_the_mounts_cannot_be_inspected_the_project_is_failed_and_nothing_is_removed() -> None:
    docker = stack()
    docker.mounts = {}
    docker.containers = [Resource("c1", "app-web-1", PROJECT)]
    real = docker.__call__

    def inspect_fails(
        command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        if list(command)[:3] == INSPECT:
            return _done("", returncode=1, stderr="Error: cannot connect to the daemon")
        return real(command, environ, cwd)

    result = compose.remove_project(PROJECT, GIT_DIR, run=inspect_fails, environ={})

    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert "inspect" in result.failed[0].reason
    assert docker.removals() == []  # the down did not run: nothing was removed unlisted
    assert result.kept_volumes == []


# --- no Docker ----------------------------------------------------------------------------------------


def not_installed(
    command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
) -> "subprocess.CompletedProcess[str]":
    raise FileNotFoundError(command[0])


def test_without_docker_the_project_is_failed_and_stays_recorded() -> None:
    result = compose.remove_project(PROJECT, GIT_DIR, run=not_installed, environ={})

    assert result.removed == [] and result.already_absent == []
    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert "docker" in result.failed[0].reason.lower()


def test_listing_only_without_docker_fails_the_project_and_does_not_list_it() -> None:
    """LOW-2, FR-040: a dry run that cannot ask Docker must not promise a removal."""
    result = compose.remove_project(PROJECT, GIT_DIR, dry_run=True, run=not_installed, environ={})

    assert result.removed == [] and result.already_absent == []
    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert "docker" in result.failed[0].reason.lower()


def engine_down(
    command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
) -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(
        list(command), 1, stdout="", stderr="Cannot connect to the Docker daemon at unix:///x\n"
    )


def test_listing_only_with_an_engine_that_does_not_answer_fails_the_project() -> None:
    result = compose.remove_project(PROJECT, GIT_DIR, dry_run=True, run=engine_down, environ={})

    assert result.removed == [] and result.already_absent == []
    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_PROJECT, PROJECT)]
    assert "docker" in result.failed[0].reason.lower()


def test_listing_only_gives_the_same_failure_as_the_real_run_when_docker_is_not_there() -> None:
    planned = compose.remove_project(PROJECT, GIT_DIR, dry_run=True, run=engine_down, environ={})
    actual = compose.remove_project(PROJECT, GIT_DIR, run=engine_down, environ={})

    assert planned.failed == actual.failed and planned.removed == actual.removed == []


# --- the recorded project must be a name wtenv generates, for the entry's own git directory (T179) --

NOT_GENERATED = "not a project name wtenv generates"
HEADER = "# Generated by wtenv for this worktree. Do not edit or commit; `wtenv up` rewrites it.\n"
OTHERS_ID = short_id("/repos/other/.git/worktrees/other", 8)
BAD_PROJECTS = [
    "decoy-1a2b3c4d",  # another project altogether
    "wtenv-app",  # no id
    f"wtenv-app-{OTHERS_ID}",  # the right form, with the id of another git directory
    f"wtenv-app-{short_id(GIT_DIR, 8).upper()}",  # id in upper case
    f"wtenv-{'a' * 41}-{short_id(GIT_DIR, 8)}",  # slug too long
    f"wtenv-app-{short_id(GIT_DIR, 8)}\n",  # trailing line break
    f"xwtenv-app-{short_id(GIT_DIR, 8)}",  # not at the start
    "",
]


@pytest.mark.parametrize("project", BAD_PROJECTS)
@pytest.mark.parametrize("dry_run", [True, False])
def test_a_project_that_is_not_the_entrys_own_runs_no_docker_command_at_all(
    project: str, dry_run: bool
) -> None:
    docker = stack()

    result = compose.remove_project(project, GIT_DIR, dry_run=dry_run, run=docker, environ={})

    assert docker.calls == []  # not even a listing
    assert result.removed == [] and result.already_absent == [] and result.kept_volumes == []
    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.COMPOSE_PROJECT, project)]
    assert NOT_GENERATED in result.failed[0].reason
    assert names(docker.containers) == ["app-web-1", "other-web-1", "postgres-local"]


def test_a_project_of_the_right_form_for_its_own_git_directory_is_removed_as_before() -> None:
    docker = stack()

    result = compose.remove_project(PROJECT, GIT_DIR, run=docker, environ={})

    assert result.failed == [] and result.removed == REMOVED


def entry_recording(project: str, root: Path) -> WorktreeEntry:
    """An entry of `GIT_DIR` at `root` that records `project` and an override file wtenv wrote."""
    (root / "compose.override.yaml").write_text(HEADER + 'name: "x"\n', encoding="utf-8")
    return WorktreeEntry(
        git_dir=GIT_DIR,
        path=str(root),
        repository="/repos/app/.git",
        state="provisioned",
        block=PortBlock(start=20000, size=10),
        compose=ComposeRecord(
            project=project,
            file="compose.yaml",
            override="compose.override.yaml",
            override_state=ResourceState.CREATED,
        ),
    )


@pytest.mark.parametrize("project", BAD_PROJECTS)
@pytest.mark.parametrize("dry_run", [True, False])
def test_teardown_keeps_the_override_and_the_compose_record_of_a_project_it_did_not_name(
    project: str, dry_run: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_docker(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"a command was run: {args}")

    monkeypatch.setattr(compose.subprocess, "run", no_docker)
    entry = entry_recording(project, tmp_path)
    with registry.transaction() as reg:
        reg.worktrees[GIT_DIR] = entry
    override = tmp_path / "compose.override.yaml"
    before = override.read_bytes()

    release = teardown.plan_release(entry) if dry_run else teardown.release_entry(entry)

    compose_failures = [f for f in release.failed if f.kind is ItemKind.COMPOSE_PROJECT]
    assert [f.name for f in compose_failures] == [project]
    assert NOT_GENERATED in compose_failures[0].reason
    assert release.released is False
    assert not any(i.kind is ItemKind.COMPOSE_OVERRIDE for i in release.removed)  # not even listed
    assert override.read_bytes() == before
    recorded = registry.load().worktrees[GIT_DIR]
    assert recorded.compose is not None and recorded.compose.project == project
