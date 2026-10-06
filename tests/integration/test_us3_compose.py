"""User story 3: docker-compose isolation, on real git worktrees and a real Docker (spec.md, User
Story 3; T079, T080).

A test that needs Compose requests `compose_docker` (or `compose_projects`) and is skipped, with a
message, when Docker or Compose 2.24.4 is not available. The tests start containers only from an
image that is already on the machine, only under project names wtenv generated for their own
worktrees, and remove exactly those projects at the end. They never look at other containers,
networks, or volumes.
"""

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    COMPOSE_IMAGE,
    ComposeProjects,
    block_lines,
    clean_environment,
    commit_all,
    exclude_file,
    git,
    parse_up,
    project_resources,
    snapshot_tree,
)

from wtenv.compose import project_name
from wtenv.envfile import read_section
from wtenv.identity import current_worktree
from wtenv.output import ItemKind, ResourceState, UpResult
from wtenv.registry import WorktreeEntry, load, registry_path, transaction

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

# `cache` publishes a port tied to the variable CACHE_PORT; `web` publishes a fixed one.
STACK = f"""\
services:
  cache:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    ports:
      - "${{CACHE_PORT:-6379}}:6379"
    volumes:
      - cache-data:/data
  web:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    ports:
      - "8080:6379"
volumes:
  cache-data:
"""
TOML = 'ports = ["PORT", "CACHE_PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def make_worktree(
    repo: Path,
    add_worktree: AddWorktree,
    name: str,
    *,
    toml: str = TOML,
    stack: str = STACK,
    stack_name: str = "compose.yaml",
) -> Path:
    """Add worktree `name` with a committed compose file and `wtenv.toml`."""
    worktree = add_worktree(repo, name, f"branch-{name}")
    (worktree / stack_name).write_text(stack, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(toml, encoding="utf-8")
    commit_all(worktree)
    return worktree


def up(run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None) -> UpResult:
    """Run `wtenv up --json` and return its result; the run must succeed."""
    process = run_wtenv(["up", "--json"], worktree, env)
    assert process.returncode == 0, process.stdout + process.stderr
    return parse_up(process)


def failed_up(
    run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    """Run `wtenv up --json` and return its exit status and `error` object."""
    process = run_wtenv(["up", "--json"], worktree, env)
    assert len(process.stdout.splitlines()) == 1, process.stdout
    error = json.loads(process.stdout)["error"]
    assert isinstance(error, dict)
    return process.returncode, error


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


def is_recorded(worktree: Path) -> bool:
    return current_worktree(worktree).git_dir in load().worktrees


def project_of(worktree: Path) -> str:
    compose = entry_of(worktree).compose
    assert compose is not None
    return compose.project


def resolved(worktree: Path) -> dict[str, Any]:
    """Return what plain `docker compose config` shows in `worktree`, as a developer would run it."""
    process = subprocess.run(
        ["docker", "compose", "--profile", "*", "config", "--format", "json"],
        cwd=worktree,
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=True,
    )
    document = json.loads(process.stdout)
    assert isinstance(document, dict)
    return document


def published_of(document: dict[str, Any], service: str) -> list[tuple[int, int]]:
    """Return (host port, container port) for each port `service` publishes."""
    return [(int(p["published"]), int(p["target"])) for p in document["services"][service]["ports"]]


def block_of(worktree: Path) -> range:
    block = entry_of(worktree).block
    return range(block.start, block.start + block.size)


@pytest.fixture
def two_worktrees(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> tuple[Path, Path]:
    """Two provisioned worktrees with the same compose file; their projects are tracked."""
    first = make_worktree(repo, add_worktree, "one")
    second = make_worktree(repo, add_worktree, "two")
    for worktree in (first, second):
        up(run_wtenv, worktree)
        compose_projects.track(project_of(worktree))
    return first, second


# --- scenario 1: a project name of its own and ports inside the block (FR-028, FR-029) ---------


def test_each_worktree_has_its_own_project_name_and_ports_in_its_block(
    two_worktrees: tuple[Path, Path],
) -> None:
    first, second = two_worktrees

    assert project_of(first) != project_of(second)
    for worktree in two_worktrees:
        shown = resolved(worktree)
        assert shown["name"] == project_of(worktree)
        for service, container_port in (("cache", 6379), ("web", 6379)):
            ((host_port, target),) = published_of(shown, service)
            assert host_port in block_of(worktree)
            assert target == container_port  # container ports are never changed
    assert set(block_of(first)).isdisjoint(block_of(second))


def test_the_project_name_is_wtenv_slug_and_id8(two_worktrees: tuple[Path, Path]) -> None:
    first, _ = two_worktrees

    name = project_of(first)

    assert name.startswith("wtenv-one-")
    assert len(name.rsplit("-", 1)[1]) == 8


# --- scenario 2: two stacks run at once (FR-030) ------------------------------------------------


def test_plain_docker_compose_runs_both_stacks_with_separate_resources(
    two_worktrees: tuple[Path, Path], compose_image: str
) -> None:
    for worktree in two_worktrees:
        subprocess.run(
            ["docker", "compose", "up", "-d", "--pull", "never"],
            cwd=worktree,
            env=clean_environment(),
            capture_output=True,
            text=True,
            check=True,
        )

    resources = [project_resources(project_of(worktree)) for worktree in two_worktrees]
    for kind in ("containers", "networks", "volumes"):
        first_ids, second_ids = (set(r[kind]) for r in resources)
        assert first_ids and second_ids, kind
        assert first_ids.isdisjoint(second_ids), kind
    for worktree in two_worktrees:
        listing = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.project={project_of(worktree)}"]
            + ["--format", "{{.Ports}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        shown = resolved(worktree)
        for service in ("cache", "web"):
            ((host_port, _),) = published_of(shown, service)
            assert f":{host_port}->6379/tcp" in listing


# --- scenario 3: a port tied to a variable (FR-031) --------------------------------------------


def test_a_variable_and_the_override_carry_the_same_port(
    two_worktrees: tuple[Path, Path],
) -> None:
    for worktree in two_worktrees:
        variables = dict(read_section(worktree / ".env.local") or [])
        ((host_port, _),) = published_of(resolved(worktree), "cache")

        assert int(variables["CACHE_PORT"]) == host_port
        assert int(variables["CACHE_PORT"]) == block_of(worktree)[1]


def test_a_port_that_is_not_tied_gets_the_next_port_after_the_variables(
    two_worktrees: tuple[Path, Path],
) -> None:
    first, _ = two_worktrees

    ((host_port, _),) = published_of(resolved(first), "web")

    assert host_port == block_of(first)[2]


# --- scenario 4: a repeat up changes nothing ---------------------------------------------------


def test_a_repeat_up_leaves_the_project_name_and_the_override_byte_identical(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "repeat")
    up(run_wtenv, worktree)
    project = compose_projects.track(project_of(worktree))
    override = worktree / "compose.override.yaml"
    first_bytes, first_entry = override.read_bytes(), entry_of(worktree)

    result = up(run_wtenv, worktree)

    assert override.read_bytes() == first_bytes
    assert project_of(worktree) == project
    assert entry_of(worktree) == first_entry
    overrides = [c for c in result.changes if c.item.kind is ItemKind.COMPOSE_OVERRIDE]
    assert [c.action for c in overrides] == ["unchanged"]


def test_the_first_up_reports_the_override_as_created(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "first")

    result = up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    overrides = [c for c in result.changes if c.item.kind is ItemKind.COMPOSE_OVERRIDE]
    assert [(c.action, c.item.name) for c in overrides] == [
        ("created", str(worktree / "compose.override.yaml"))
    ]
    assert result.worktree is not None
    assert result.worktree.compose_project == project_of(worktree)


# --- scenario 5: the compose file is not modified (FR-029) -------------------------------------


def test_the_compose_file_is_unchanged_and_git_status_is_empty(
    two_worktrees: tuple[Path, Path],
) -> None:
    for worktree in two_worktrees:
        assert (worktree / "compose.yaml").read_text(encoding="utf-8") == STACK
        assert git(worktree, "status", "--porcelain") == ""
        assert (worktree / "compose.override.yaml").exists()
        assert "/compose.override.yaml" in block_lines(exclude_file(worktree.parent / "app"))
        assert "/compose.override.yaml" in entry_of(worktree).exclude_patterns


# --- FR-034: up starts nothing -----------------------------------------------------------------


def test_up_starts_no_container_and_creates_no_network_or_volume(
    two_worktrees: tuple[Path, Path],
) -> None:
    for worktree in two_worktrees:
        assert project_resources(project_of(worktree)) == {
            "containers": [],
            "networks": [],
            "volumes": [],
        }


# --- FR-031: the ports in the result -----------------------------------------------------------


def test_published_ports_appear_in_the_up_result(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "view")

    result = up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    assert result.worktree is not None
    start = entry_of(worktree).block.start
    by_port = {p.port: p for p in result.worktree.ports}
    assert by_port[start].variable == "PORT" and by_port[start].service is None
    tied = by_port[start + 1]
    assert (tied.variable, tied.service, tied.target, tied.protocol) == (
        "CACHE_PORT",
        "cache",
        6379,
        "tcp",
    )
    untied = by_port[start + 2]
    assert (untied.variable, untied.service, untied.target, untied.protocol) == (
        None,
        "web",
        6379,
        "tcp",
    )


def test_the_registry_records_the_published_ports(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "recorded")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    start = entry_of(worktree).block.start

    published = entry_of(worktree).published

    assert [(p.service, p.target, p.port, p.variable) for p in published] == [
        ("cache", 6379, start + 1, "CACHE_PORT"),
        ("web", 6379, start + 2, None),
    ]
    compose = entry_of(worktree).compose
    assert compose is not None
    assert (compose.file, compose.override) == ("compose.yaml", "compose.override.yaml")
    assert compose.override_state.value == "created"


def test_the_text_output_shows_the_published_ports_and_the_compose_project(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "text")

    process = run_wtenv(["up"], worktree)

    assert process.returncode == 0, process.stderr
    compose_projects.track(project_of(worktree))
    start = entry_of(worktree).block.start
    lines = process.stdout.splitlines()
    assert any(
        line.startswith("  published")
        and f"cache:6379 -> {start + 1} (CACHE_PORT)" in line
        and f"web:6379 -> {start + 2}" in line
        for line in lines
    ), process.stdout
    assert f"  compose    {project_of(worktree)}  compose.override.yaml (created)" in lines


# --- research.md §8: compose code is loaded only when it is configured --------------------------


def test_up_without_compose_does_not_import_the_compose_module(
    repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "plain", "branch-plain")
    code = (
        "import sys\n"
        "from wtenv import provision\n"
        f"provision.up({str(worktree)!r})\n"
        "assert 'wtenv.compose' not in sys.modules, 'wtenv.compose was imported'\n"
    )

    process = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert process.returncode == 0, process.stderr


# =============================================================================================
# T080: the limits of compose isolation, each failing before the compose step changes anything
# =============================================================================================


def nothing_for_compose(worktree: Path) -> None:
    """Assert there is no override file and no compose record."""
    assert not list(worktree.glob("*override*"))
    if is_recorded(worktree):
        assert entry_of(worktree).compose is None


def test_another_compose_file_name_is_config_invalid_naming_compose_file(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run
) -> None:
    toml = '[compose]\nfile = "stack.yaml"\n'
    worktree = make_worktree(repo, add_worktree, "name", toml=toml, stack_name="stack.yaml")

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (3, "config_invalid")
    assert error["details"]["setting"] == "compose.file"
    assert not is_recorded(worktree)
    nothing_for_compose(worktree)


def test_a_developers_override_is_an_ownership_conflict_and_is_not_modified(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_docker: None
) -> None:
    worktree = make_worktree(repo, add_worktree, "mine")
    mine = worktree / "compose.override.yml"
    mine.write_text("services: {}\n", encoding="utf-8")
    commit_all(worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "compose_override", "name": str(mine)}
    assert mine.read_text(encoding="utf-8") == "services: {}\n"
    assert not (worktree / "compose.override.yaml").exists()
    assert not is_recorded(worktree)


def test_compose_project_name_in_the_environment_is_unsupported(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_docker: None
) -> None:
    worktree = make_worktree(repo, add_worktree, "envname")

    status, error = failed_up(run_wtenv, worktree, {"COMPOSE_PROJECT_NAME": "other"})

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"]["reason"] == "compose_env_override"
    assert not is_recorded(worktree)
    nothing_for_compose(worktree)


def test_compose_file_in_dot_env_is_unsupported_naming_the_file(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_docker: None
) -> None:
    worktree = make_worktree(repo, add_worktree, "dotenv")
    (worktree / ".env").write_text("COMPOSE_FILE=compose.yaml\n", encoding="utf-8")

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"]["reason"] == "compose_env_override"
    assert error["details"]["file"] == str(worktree / ".env")
    assert not is_recorded(worktree)
    nothing_for_compose(worktree)


def test_a_published_range_is_unsupported_naming_the_service(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_docker: None
) -> None:
    stack = 'services:\n  api:\n    image: x\n    ports:\n      - "8000-9000:80"\n'
    worktree = make_worktree(repo, add_worktree, "range", stack=stack)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"]["reason"] == "compose_port_range"
    assert error["details"]["service"] == "api"
    assert not is_recorded(worktree)
    nothing_for_compose(worktree)


def test_two_services_on_one_variable_port_are_a_clash(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_docker: None
) -> None:
    stack = (
        "services:\n"
        '  a:\n    image: x\n    ports: ["${CACHE_PORT:-1}:80"]\n'
        '  b:\n    image: x\n    ports: ["${CACHE_PORT:-1}:81"]\n'
    )
    worktree = make_worktree(repo, add_worktree, "clash", stack=stack)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"]["reason"] == "compose_port_clash"
    assert not is_recorded(worktree)


def test_a_remote_docker_engine_is_not_local_and_is_never_contacted(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, tmp_path: Path
) -> None:
    # A stand-in `docker` that logs every call: the log stays empty if no docker command runs.
    log = tmp_path / "docker-calls.log"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "docker"
    shim.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    worktree = make_worktree(repo, add_worktree, "remote")
    env = {
        "DOCKER_HOST": "tcp://192.0.2.1:2375",
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
    }

    status, error = failed_up(run_wtenv, worktree, env)

    assert (status, error["code"]) == (8, "dependency_unavailable")
    assert error["details"]["dependency"] == "docker"
    assert error["details"]["reason"] == "not_local"
    assert not log.exists()
    assert not is_recorded(worktree)
    nothing_for_compose(worktree)


def many_ports(count: int) -> str:
    ports = "".join(f'      - "{9000 + n}:80"\n' for n in range(count))
    return f"services:\n  web:\n    image: x\n    ports:\n{ports}"


def test_more_ports_than_the_block_holds_is_config_invalid_on_a_first_up(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_docker: None
) -> None:
    toml = 'ports = ["PORT"]\nblock_size = 3\n\n[compose]\nfile = "compose.yaml"\n'
    worktree = make_worktree(repo, add_worktree, "toomany", toml=toml, stack=many_ports(3))

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (3, "config_invalid")
    assert error["details"]["min_block_size"] == 4  # one variable and three published ports
    assert not is_recorded(worktree)
    nothing_for_compose(worktree)


def test_more_ports_than_the_block_holds_is_config_invalid_on_a_provisioned_worktree(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    toml = 'ports = ["PORT"]\nblock_size = 3\n\n[compose]\nfile = "compose.yaml"\n'
    worktree = make_worktree(repo, add_worktree, "grown", toml=toml, stack=many_ports(2))
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    before, override = entry_of(worktree), (worktree / "compose.override.yaml").read_bytes()
    (worktree / "compose.yaml").write_text(many_ports(3), encoding="utf-8")

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (3, "config_invalid")
    assert error["details"]["min_block_size"] == 4
    assert entry_of(worktree) == before
    assert (worktree / "compose.override.yaml").read_bytes() == override


def test_a_fixed_container_name_is_a_warning_not_a_failure(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    stack = 'services:\n  web:\n    image: x\n    container_name: fixed\n    ports: ["8080:80"]\n'
    worktree = make_worktree(repo, add_worktree, "fixed", stack=stack)

    result = up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    warnings = [w for w in result.warnings if w.code.value == "compose_fixed_container_name"]
    assert [w.details["service"] for w in warnings] == ["web"]
    assert "web" in warnings[0].message


# --- configuration changes (config.md, "Changing the configuration") ---------------------------


def test_a_new_compose_file_moves_the_override_and_keeps_the_project_name(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "moved")
    up(run_wtenv, worktree)
    project = compose_projects.track(project_of(worktree))
    (worktree / "docker-compose.yml").write_text(STACK, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(
        TOML.replace("compose.yaml", "docker-compose.yml"), encoding="utf-8"
    )
    commit_all(worktree)

    result = up(run_wtenv, worktree)

    assert project_of(worktree) == project
    assert not (worktree / "compose.override.yaml").exists()
    assert (worktree / "docker-compose.override.yml").exists()
    compose = entry_of(worktree).compose
    assert compose is not None
    assert (compose.file, compose.override) == ("docker-compose.yml", "docker-compose.override.yml")
    assert compose.override_state.value == "created"
    assert "/docker-compose.override.yml" in entry_of(worktree).exclude_patterns
    actions = {
        (c.action, c.item.name) for c in result.changes if c.item.kind is ItemKind.COMPOSE_OVERRIDE
    }
    assert actions == {
        ("released", str(worktree / "compose.override.yaml")),
        ("created", str(worktree / "docker-compose.override.yml")),
    }
    # The new override is in force when Compose is run the way a developer runs it.
    shown = resolved(worktree)
    assert shown["name"] == project
    ((host_port, _),) = published_of(shown, "cache")
    assert host_port in block_of(worktree)


def test_removing_compose_removes_the_override_and_keeps_the_project_recorded(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "dropped")
    up(run_wtenv, worktree)
    project = compose_projects.track(project_of(worktree))
    (worktree / "wtenv.toml").write_text('ports = ["PORT", "CACHE_PORT"]\n', encoding="utf-8")

    result = up(run_wtenv, worktree)

    assert not (worktree / "compose.override.yaml").exists()
    assert project_of(worktree) == project
    assert entry_of(worktree).published == []
    assert (worktree / "compose.yaml").read_text(encoding="utf-8") == STACK
    released = [c for c in result.changes if c.item.kind is ItemKind.COMPOSE_OVERRIDE]
    assert [(c.action, c.item.name) for c in released] == [
        ("released", str(worktree / "compose.override.yaml"))
    ]
    assert result.worktree is not None and result.worktree.compose_project == project
    # A repeat up has nothing left to do for compose.
    again = up(run_wtenv, worktree)
    assert [c for c in again.changes if c.item.kind is ItemKind.COMPOSE_OVERRIDE] == []
    assert project_of(worktree) == project


def test_adding_compose_to_a_provisioned_worktree_creates_the_override(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "late", toml='ports = ["PORT", "CACHE_PORT"]\n')
    up(run_wtenv, worktree)
    assert entry_of(worktree).compose is None
    (worktree / "wtenv.toml").write_text(TOML, encoding="utf-8")

    result = up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    assert (worktree / "compose.override.yaml").exists()
    overrides = [c for c in result.changes if c.item.kind is ItemKind.COMPOSE_OVERRIDE]
    assert [c.action for c in overrides] == ["created"]


# --- a compose project that already exists is not adopted (T164; LOW-4; FR-024, FR-039) ----------
#
# The project name is generated from the worktree, so containers started by hand under that name
# are not wtenv's. `up` must not record the project and later remove it on `down`: with resources
# already carrying the project label and no compose record, it stops with `ownership_conflict`.
# An entry that already records the project is wtenv's, and `up` keeps working on a running stack.

QUIET_STACK = f"""\
services:
  cache:
    image: {COMPOSE_IMAGE}
    pull_policy: never
"""
QUIET_TOML = 'ports = ["PORT"]\n\n[compose]\nfile = "compose.yaml"\n'


def start_by_hand(worktree: Path, project: str) -> None:
    """Start the stack of `worktree` under `project`, as a developer would, outside wtenv."""
    subprocess.run(
        ["docker", "compose", "-p", project, "up", "-d"],
        cwd=worktree,
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=True,
    )


def generated_project(worktree: Path) -> str:
    """The project name `up` would generate for `worktree`."""
    return project_name(worktree.name, current_worktree(worktree).git_dir)


def test_up_refuses_a_compose_project_that_was_started_before_it_and_down_leaves_it_alone(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    compose_image: str,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one", toml=QUIET_TOML, stack=QUIET_STACK)
    project = compose_projects.track(generated_project(worktree))
    start_by_hand(worktree, project)
    containers_before = project_resources(project)["containers"]
    assert containers_before

    status, error = failed_up(run_wtenv, worktree)

    assert status == 11 and error["code"] == "ownership_conflict"
    assert error["details"] == {"kind": "compose_project", "name": project}
    assert not is_recorded(worktree)  # nothing recorded, for compose or anything else
    assert not (worktree / "compose.override.yaml").exists()

    process = run_wtenv(["down", "--json"], worktree)  # wtenv has no record: it removes nothing
    assert process.returncode == 0, process.stderr
    assert project_resources(project)["containers"] == containers_before


def test_up_keeps_working_on_a_running_stack_it_already_records(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    compose_image: str,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one", toml=QUIET_TOML, stack=QUIET_STACK)
    project = compose_projects.track(generated_project(worktree))
    up(run_wtenv, worktree)  # records the project
    assert project_of(worktree) == project
    start_by_hand(worktree, project)

    again = up(run_wtenv, worktree)  # the project is recorded, so its resources are wtenv's

    assert again.worktree is not None and again.worktree.compose_project == project
    assert project_resources(project)["containers"]


def test_a_record_that_was_left_creating_adopts_the_resources_of_an_interrupted_run(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    compose_image: str,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one", toml=QUIET_TOML, stack=QUIET_STACK)
    project = compose_projects.track(generated_project(worktree))
    up(run_wtenv, worktree)
    start_by_hand(worktree, project)
    git_dir = current_worktree(worktree).git_dir
    with transaction() as registry:  # the state an `up` that was interrupted would have left
        entry = registry.worktrees[git_dir]
        assert entry.compose is not None
        entry.compose = entry.compose.model_copy(update={"override_state": ResourceState.CREATING})
        entry.state = "incomplete"

    result = up(run_wtenv, worktree)

    assert result.worktree is not None and result.worktree.compose_project == project
    assert entry_of(worktree).state == "provisioned"


# --- `up` refuses a recorded override or env file it must not remove (T183, T184; H2) --------------
#
# `up` removes the recorded override when `[compose]` is gone or the compose file moved, and the
# section of the recorded env file when `env_file` changed. The registry is a file a person can
# edit, so a recorded value of a form wtenv never records, or an override that is not wtenv's, stops
# `up` with `ownership_conflict` before anything changes (cli.md, `wtenv up`, step 4).

OWN_HEADER = (
    "# Generated by wtenv for this worktree. Do not edit or commit; `wtenv up` rewrites it.\n"
)
NO_COMPOSE_TOML = 'ports = ["PORT", "CACHE_PORT"]\nblock_size = 10\n'
MOVED_TOML = TOML.replace("compose.yaml", "docker-compose.yaml")

# What the entry records as its override, and what is planted there.
BAD_OVERRIDES = {
    "outside by ..": ("../compose.override.yaml", OWN_HEADER),
    "absolute": ("{outside}/compose.override.yaml", OWN_HEADER),
    "not one of the four names": ("src/main.py", OWN_HEADER),
    "no header": ("compose.override.yaml", "services: {}\n"),
}


def record_override(worktree: Path, value: str) -> None:
    """Edit the registry as a person could: the entry records `value` as its override."""
    with transaction() as registry:
        entry = registry.worktrees[current_worktree(worktree).git_dir]
        assert entry.compose is not None
        entry.compose = entry.compose.model_copy(update={"override": value})


@pytest.mark.parametrize("change", ["removed", "moved"])
@pytest.mark.parametrize("name", BAD_OVERRIDES)
def test_up_refuses_a_recorded_override_that_is_not_wtenvs_before_changing_anything(
    name: str,
    change: str,
    compose_docker: None,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    tmp_path: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one")
    up(run_wtenv, worktree)
    outside = tmp_path / "outside"
    outside.mkdir()
    value, text = BAD_OVERRIDES[name]
    value = value.format(outside=outside)
    record_override(worktree, value)
    planted = worktree / value if not Path(value).is_absolute() else Path(value)
    if planted.resolve() != (worktree / "compose.override.yaml").resolve():
        (worktree / "compose.override.yaml").unlink()
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(text, encoding="utf-8")
    if change == "removed":
        (worktree / "wtenv.toml").write_text(NO_COMPOSE_TOML, encoding="utf-8")
    else:
        (worktree / "docker-compose.yaml").write_text(STACK, encoding="utf-8")
        (worktree / "wtenv.toml").write_text(MOVED_TOML, encoding="utf-8")
    tree_before = snapshot_tree(worktree)
    outside_before = snapshot_tree(outside)
    planted_before = planted.read_bytes()
    registry_before = registry_path().read_bytes()

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "compose_override", "name": str(worktree / value)}
    assert planted.read_bytes() == planted_before
    assert snapshot_tree(worktree) == tree_before
    assert snapshot_tree(outside) == outside_before
    assert registry_path().read_bytes() == registry_before


# --- the recorded override that `up` is about to rewrite (FR-087; T212) ---------------------------

EDITED_OVERRIDES = {
    "developer content": "services:\n  web:\n    ports: []\n",
    "empty": "",
    "altered header": OWN_HEADER.replace("Generated", "Written") + 'name: "mine"\n',
}


@pytest.mark.parametrize("name", EDITED_OVERRIDES)
def test_up_leaves_a_recorded_override_that_lost_the_header_byte_identical(
    name: str,
    compose_docker: None,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one")
    up(run_wtenv, worktree)
    override = worktree / "compose.override.yaml"
    override.write_bytes(EDITED_OVERRIDES[name].encode())
    tree_before = snapshot_tree(worktree)
    registry_before = registry_path().read_bytes()

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "compose_override", "name": str(override)}
    assert str(override) in error["message"]
    assert override.read_bytes() == EDITED_OVERRIDES[name].encode()
    assert snapshot_tree(worktree) == tree_before
    assert registry_path().read_bytes() == registry_before


def test_up_rewrites_a_stale_override_that_still_has_the_header(
    compose_docker: None,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one")
    up(run_wtenv, worktree)
    override = worktree / "compose.override.yaml"
    wrote = override.read_bytes()
    override.write_text(OWN_HEADER + 'name: "stale"\n', encoding="utf-8")

    up(run_wtenv, worktree)

    assert override.read_bytes() == wrote


def test_up_creates_a_recorded_override_that_is_missing_again(
    compose_docker: None,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one")
    up(run_wtenv, worktree)
    override = worktree / "compose.override.yaml"
    wrote = override.read_bytes()
    override.unlink()

    up(run_wtenv, worktree)

    assert override.read_bytes() == wrote


@pytest.mark.parametrize("name", ["outside by ..", "absolute", "a part that is `..`"])
def test_up_refuses_a_recorded_env_file_of_a_form_wtenv_never_records_when_env_file_changed(
    name: str,
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    tmp_path: Path,
) -> None:
    worktree = add_worktree(repo, "one", "one")
    (worktree / "wtenv.toml").write_text('ports = ["PORT"]\n', encoding="utf-8")
    up(run_wtenv, worktree)
    outside = tmp_path / "outside"
    outside.mkdir()
    value = {
        "outside by ..": "../outside/shared.env",
        "absolute": str(outside / "shared.env"),
        "a part that is `..`": "sub/../../outside/shared.env",
    }[name]
    decoy = outside / "shared.env"
    decoy.write_bytes(
        b"SHARED=1\n# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>\nPORT=1\n"
        b"# <<< wtenv managed <<<\n"
    )
    with transaction() as registry:
        entry = registry.worktrees[current_worktree(worktree).git_dir]
        assert entry.env_file is not None
        entry.env_file = entry.env_file.model_copy(update={"path": value})
    (worktree / "wtenv.toml").write_text('ports = ["PORT"]\nenv_file = ".env.other"\n')
    tree_before = snapshot_tree(worktree)
    outside_before = snapshot_tree(outside)
    registry_before = registry_path().read_bytes()

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "env_section", "name": str(worktree / value)}
    assert snapshot_tree(outside) == outside_before
    assert snapshot_tree(worktree) == tree_before
    assert registry_path().read_bytes() == registry_before


def test_up_warns_about_a_volume_with_a_fixed_name_and_not_about_an_external_one(
    compose_docker: None, run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    """T187, T188; reading R8: `up` starts nothing, so no volume is made or removed."""
    # Compose leaves a volume that no service mounts out of its resolved model.
    stack = STACK.replace(
        "      - cache-data:/data\n",
        "      - cache-data:/data\n      - shared:/shared\n      - kept:/kept\n",
    ).replace(
        "volumes:\n  cache-data:\n",
        "volumes:\n  cache-data:\n  shared:\n    name: wtenv-test-never-created-volume\n"
        "  kept:\n    external: true\n    name: wtenv-test-never-created-external\n",
    )
    worktree = make_worktree(repo, add_worktree, "one", stack=stack)

    result = up(run_wtenv, worktree)

    fixed = [w for w in result.warnings if w.code.value == "compose_fixed_volume_name"]
    assert [w.details for w in fixed] == [
        {"volume": "shared", "name": "wtenv-test-never-created-volume"}
    ]
