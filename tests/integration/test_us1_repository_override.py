"""User story 1: a repository that owns its override file (spec.md, User Story 1; T032).

The worktrees here have a compose file that publishes nothing and a committed override that
publishes a port and sets a command, so `up` chooses the exec-only arrangement: it writes
`.wtenv/compose.isolation.yaml` and never touches the repository's override. Each test that runs
`up` compares the SHA-256 of the repository's override before and after.

Compose is run in the worktree with the environment that `isolation.compose_environment` returns,
which is what `wtenv exec` will apply (T051/T052 add the `wtenv exec` versions; T072 covers
scenarios 2 and 3 through `wtenv exec`). A test never sets `COMPOSE_FILE` or
`COMPOSE_PATH_SEPARATOR` itself.

Every test uses throwaway repositories and worktrees under `tmp_path`, a temporary
`XDG_STATE_HOME` (the autouse fixture of `tests/conftest.py`), and compose projects that wtenv
generated for its own worktrees.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from helpers import (
    COMPOSE_IMAGE,
    ComposeProjects,
    block_lines,
    clean_environment,
    commit_all,
    exclude_file,
    git,
    parse_down,
    parse_gc,
    parse_ls,
    parse_up,
    project_resources,
    snapshot_tree,
)

from wtenv import isolation
from wtenv.identity import current_worktree
from wtenv.output import FailedItem, Item, KeptItem, ResourceState, UpResult
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

ISOLATION_FILE = isolation.ISOLATION_FILE
REPOSITORY_OVERRIDE = "compose.override.yml"

# The compose file publishes nothing. The repository's override publishes `backend` on the fixed
# host port 8000 and sets a command (User Story 1, Independent Test).
STACK = f"""\
services:
  backend:
    image: {COMPOSE_IMAGE}
    pull_policy: never
"""
OVERRIDE = """\
services:
  backend:
    ports:
      - "8000:6379"
    command: ["valkey-server", "--save", "", "--appendonly", "no"]
"""
COMMAND = ["valkey-server", "--save", "", "--appendonly", "no"]
TOML = 'ports = ["PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'


def sha256(path: Path) -> str:
    """Return the SHA-256 of the file at `path`."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def make_worktree(
    repo: Path,
    add_worktree: AddWorktree,
    name: str,
    *,
    stack: str = STACK,
    override: str | None = OVERRIDE,
    override_name: str = REPOSITORY_OVERRIDE,
    toml: str = TOML,
) -> Path:
    """Add worktree `name` with a committed compose file, `wtenv.toml` and repository override."""
    worktree = add_worktree(repo, name, "branch-" + name.replace(":", "-"))  # no ":" in a ref
    (worktree / "compose.yaml").write_text(stack, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(toml, encoding="utf-8")
    if override is not None:
        (worktree / override_name).write_text(override, encoding="utf-8")
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


def block_of(worktree: Path) -> range:
    block = entry_of(worktree).block
    return range(block.start, block.start + block.size)


def isolated_compose(worktree: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    """Run `docker compose <args>` in `worktree` with the isolation environment, nothing more.

    The environment is the one without any `COMPOSE_*` variable plus exactly what
    `isolation.compose_environment` returns for the entry's record.
    """
    record = entry_of(worktree).compose
    assert record is not None
    environment = {**clean_environment(), **isolation.compose_environment(worktree, record)}
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=worktree,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def free_port() -> int:
    """Return a host port that is free now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --- scenario 1: `up` succeeds beside the repository's override, which stays as it is -----------


def test_up_succeeds_beside_a_repository_override_and_never_touches_it(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "one")
    override = worktree / REPOSITORY_OVERRIDE
    before = sha256(override)

    result = up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    assert result.ok
    assert sha256(override) == before
    record = entry_of(worktree).compose
    assert record is not None
    assert record.file == "compose.yaml"
    assert record.override == ISOLATION_FILE
    assert record.override_state is ResourceState.CREATED
    assert record.repository_override == REPOSITORY_OVERRIDE
    written = (worktree / ISOLATION_FILE).read_text(encoding="utf-8")
    assert written.startswith(isolation.OVERRIDE_HEADER)
    assert f'name: "{record.project}"' in written
    (published,) = entry_of(worktree).published
    assert (published.service, published.target) == ("backend", 6379)
    assert published.port in block_of(worktree)
    assert f'"published": "{published.port}"' in written
    assert not (worktree / "compose.override.yaml").exists()  # v0.1.1's name is not used here
    patterns = block_lines(exclude_file(repo))
    assert f"/{ISOLATION_FILE}" in patterns
    assert f"/{REPOSITORY_OVERRIDE}" not in patterns  # the repository's file is never excluded
    assert f"/{REPOSITORY_OVERRIDE}" not in entry_of(worktree).exclude_patterns


def test_git_status_is_clean_after_up_in_the_exec_only_arrangement(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "clean")
    override = worktree / REPOSITORY_OVERRIDE
    before = sha256(override)

    up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    assert (worktree / ISOLATION_FILE).exists()
    assert git(worktree, "status", "--short") == ""
    assert sha256(override) == before
    assert f"/{ISOLATION_FILE}" in block_lines(exclude_file(repo))


# --- scenarios 2 and 8: the stack gets a block port and keeps the override's settings ------------


def test_the_isolation_environment_gives_the_override_a_block_port_and_keeps_its_command(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """FR-003: the port is the assigned one, not 8000, and the override's `command` is kept."""
    worktree = make_worktree(repo, add_worktree, "ports")
    override = worktree / REPOSITORY_OVERRIDE
    before = sha256(override)
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))

    process = isolated_compose(worktree, "--profile", "*", "config", "--format", "json")

    assert process.returncode == 0, process.stderr
    document = json.loads(process.stdout)
    assert document["name"] == project_of(worktree)
    backend = document["services"]["backend"]
    ports = [(int(p["published"]), int(p["target"])) for p in backend["ports"]]
    (published,) = entry_of(worktree).published
    assert ports == [(published.port, 6379)]
    assert 8000 not in [host for host, _ in ports]
    assert backend["command"] == COMMAND
    assert sha256(override) == before


# --- scenario 4: a repeat `up` changes nothing ---------------------------------------------------


def test_a_repeat_up_gives_identical_ports_project_and_isolation_file_bytes(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "repeat")
    override = worktree / REPOSITORY_OVERRIDE
    before = sha256(override)
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    first = entry_of(worktree)
    isolation_bytes = (worktree / ISOLATION_FILE).read_bytes()
    env_bytes = (worktree / ".env.local").read_bytes()
    registry_bytes = registry_path().read_bytes()

    up(run_wtenv, worktree)

    second = entry_of(worktree)
    assert (second.block, second.published, second.ports) == (
        first.block,
        first.published,
        first.ports,
    )
    assert second.compose == first.compose
    assert (worktree / ISOLATION_FILE).read_bytes() == isolation_bytes
    assert (worktree / ".env.local").read_bytes() == env_bytes
    assert registry_path().read_bytes() == registry_bytes
    assert sha256(override) == before


# --- scenario 6: no repository override, so v0.1.1's file, name and bytes ------------------------

TWO_PORTS = f"""\
services:
  cache:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    ports:
      - "16379:6379"
  db:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    ports:
      - "15432:5432"
"""
GOLDEN = Path(__file__).parents[1] / "unit" / "golden" / "compose.override.yaml"


def test_without_a_repository_override_the_override_name_and_bytes_are_v0_1_1s(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """FR-008: the golden file with this worktree's project name and its two assigned ports."""
    worktree = make_worktree(repo, add_worktree, "plain", stack=TWO_PORTS, override=None)

    up(run_wtenv, worktree)

    project = project_of(worktree)
    compose_projects.track(project)
    ports = {p.service: p.port for p in entry_of(worktree).published}
    expected = (
        GOLDEN.read_text(encoding="utf-8")
        .replace("wtenv-feature-x-3f9a1c2b", project)
        .replace('"20011"', f'"{ports["db"]}"')
        .replace('"20012"', f'"{ports["cache"]}"')
    )
    assert (worktree / "compose.override.yaml").read_text(encoding="utf-8") == expected
    assert not (worktree / ".wtenv").exists()
    record = entry_of(worktree).compose
    assert record is not None
    assert (record.override, record.repository_override) == ("compose.override.yaml", None)
    assert "/compose.override.yaml" in block_lines(exclude_file(repo))


# --- R1 pin: the isolation file replaces the override's ports on this Compose --------------------

TWO_SERVICES = f"""\
services:
  first:
    image: {COMPOSE_IMAGE}
    pull_policy: never
  second:
    image: {COMPOSE_IMAGE}
    pull_policy: never
"""


def test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """Research R1: a plain `ports:` list is appended; `!override` in the third file replaces it."""
    fixed = (free_port(), free_port())
    override = (
        f'services:\n  first:\n    ports:\n      - "{fixed[0]}:6379"\n'
        f'  second:\n    ports:\n      - "{fixed[1]}:6379"\n'
    )
    worktree = make_worktree(repo, add_worktree, "pin", stack=TWO_SERVICES, override=override)
    repository_override = worktree / REPOSITORY_OVERRIDE
    before = sha256(repository_override)
    up(run_wtenv, worktree)
    project = compose_projects.track(project_of(worktree))

    started = isolated_compose(worktree, "up", "-d")

    assert started.returncode == 0, started.stderr
    listing = subprocess.run(
        ["docker", "ps", "--filter", f"label=com.docker.compose.project={project}"]
        + ["--format", "{{.Ports}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    host_ports = sorted(
        {int(port) for port in re.findall(r":(\d+)->", listing.stdout)}
    )  # v4 and v6
    assert len(host_ports) == 2, listing.stdout
    assert all(port in block_of(worktree) for port in host_ports)
    assert not set(host_ports) & set(fixed)
    assert sha256(repository_override) == before


# --- S1 guards (plan.md, Safety) -----------------------------------------------------------------


@pytest.mark.parametrize("contents", ["services: {}\n", isolation.OVERRIDE_HEADER + 'name: "x"\n'])
def test_an_unrecorded_file_at_the_isolation_path_is_an_ownership_conflict_and_is_kept(
    contents: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
) -> None:
    worktree = make_worktree(repo, add_worktree, "planted")
    override = worktree / REPOSITORY_OVERRIDE
    planted = worktree / ISOLATION_FILE
    planted.parent.mkdir()
    planted.write_text(contents, encoding="utf-8")
    override_before = sha256(override)
    tree_before = snapshot_tree(worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "compose_override", "name": str(planted)}
    assert planted.read_text(encoding="utf-8") == contents
    assert snapshot_tree(worktree) == tree_before
    assert sha256(override) == override_before
    assert not is_recorded(worktree)


@pytest.mark.parametrize("what", ["directory", "file"])
def test_a_symlinked_wtenv_directory_is_refused_in_the_exec_only_arrangement(
    what: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
    decoy: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "linked")
    override = worktree / REPOSITORY_OVERRIDE
    override_before = sha256(override)
    if what == "directory":
        link = worktree / ".wtenv"
        link.symlink_to(decoy / "sub")
    else:
        (worktree / ".wtenv").mkdir()
        link = worktree / ISOLATION_FILE
        link.symlink_to(decoy / "notes.txt")
    decoy_before = snapshot_tree(decoy)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (7, "env_file_unusable")
    assert error["details"] == {"path": str(link), "reason": "symlink"}
    assert snapshot_tree(decoy) == decoy_before
    assert link.is_symlink()
    assert sha256(override) == override_before
    assert not is_recorded(worktree)


# --- COMPOSE_PROJECT_NAME and COMPOSE_FILE stay unsupported (spec, Edge cases) -------------------


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
@pytest.mark.parametrize("where", ["environment", ".env"])
def test_compose_variables_are_still_unsupported_in_the_exec_only_arrangement(
    variable: str,
    where: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
) -> None:
    worktree = make_worktree(repo, add_worktree, "envvars")
    override = worktree / REPOSITORY_OVERRIDE
    override_before = sha256(override)
    env: dict[str, str] | None = None
    if where == "environment":
        env = {variable: "elsewhere"}
    else:
        (worktree / ".env").write_text(f"{variable}=elsewhere\n", encoding="utf-8")
    tree_before = snapshot_tree(worktree)

    status, error = failed_up(run_wtenv, worktree, env)

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"]["reason"] == "compose_env_override"
    assert error["details"]["variable"] == variable
    assert snapshot_tree(worktree) == tree_before
    assert sha256(override) == override_before
    assert not is_recorded(worktree)


# --- a ':' in a path COMPOSE_FILE would carry (T030, T033; research R2) --------------------------


def test_a_colon_in_the_worktree_path_is_unsupported_in_the_exec_only_arrangement(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
) -> None:
    worktree = make_worktree(repo, add_worktree, "has:colon")
    override = worktree / REPOSITORY_OVERRIDE
    override_before = sha256(override)
    tree_before = snapshot_tree(worktree)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"]["reason"] == "compose_path_separator"
    assert error["details"]["file"] == str(worktree / "compose.yaml")
    assert snapshot_tree(worktree) == tree_before
    assert sha256(override) == override_before
    assert not is_recorded(worktree)


# --- links (001:FR-086, FR-009; spec, Edge cases; analysis O2; T106) -----------------------------


def test_a_link_at_an_unrecorded_override_name_is_the_repository_override(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    decoy: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "linked-override", override=None)
    target = decoy / "repository-override.yml"
    target.write_text(OVERRIDE, encoding="utf-8")
    link = worktree / "compose.override.yaml"  # untracked, and the name v0.1.1 would generate
    link.symlink_to(target)
    decoy_before = snapshot_tree(decoy)
    before = sha256(link)

    up(run_wtenv, worktree)

    compose_projects.track(project_of(worktree))
    record = entry_of(worktree).compose
    assert record is not None
    assert record.override == ISOLATION_FILE
    assert record.repository_override == "compose.override.yaml"
    assert link.is_symlink() and os.readlink(link) == str(target)
    assert snapshot_tree(decoy) == decoy_before
    assert sha256(link) == before
    assert "/compose.override.yaml" not in block_lines(exclude_file(repo))
    assert "/compose.override.yaml" not in entry_of(worktree).exclude_patterns


def planted_header_target(decoy: Path) -> Path:
    """A file in `decoy` that starts with wtenv's header: a header read through a link would pass."""
    target = decoy / "wtenv-header.yaml"
    target.write_text(isolation.OVERRIDE_HEADER + 'name: "elsewhere"\n', encoding="utf-8")
    return target


@pytest.mark.parametrize("arrangement", ["automatic", "exec_only"])
def test_a_link_at_a_recorded_override_path_is_refused_in_both_arrangements(
    arrangement: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    decoy: Path,
) -> None:
    if arrangement == "automatic":
        worktree = make_worktree(repo, add_worktree, "recorded", stack=TWO_PORTS, override=None)
        recorded = worktree / "compose.override.yaml"
    else:
        worktree = make_worktree(repo, add_worktree, "recorded")
        recorded = worktree / ISOLATION_FILE
    repository_override = worktree / REPOSITORY_OVERRIDE
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    recorded.unlink()
    recorded.symlink_to(planted_header_target(decoy))
    decoy_before = snapshot_tree(decoy)
    registry_before = registry_path().read_bytes()
    env_before = (worktree / ".env.local").read_bytes()
    override_before = sha256(repository_override) if repository_override.exists() else None

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (7, "env_file_unusable")
    assert error["details"] == {"path": str(recorded), "reason": "symlink"}
    assert snapshot_tree(decoy) == decoy_before
    assert registry_path().read_bytes() == registry_before
    assert (worktree / ".env.local").read_bytes() == env_before
    assert recorded.is_symlink()
    if override_before is not None:
        assert sha256(repository_override) == override_before


def test_a_link_at_the_isolation_path_is_refused_on_a_first_up(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
    decoy: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "first-link")
    (worktree / ".wtenv").mkdir()
    link = worktree / ISOLATION_FILE
    link.symlink_to(planted_header_target(decoy))
    override_before = sha256(worktree / REPOSITORY_OVERRIDE)
    decoy_before = snapshot_tree(decoy)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (7, "env_file_unusable")
    assert error["details"] == {"path": str(link), "reason": "symlink"}
    assert snapshot_tree(decoy) == decoy_before
    assert link.is_symlink()
    assert sha256(worktree / REPOSITORY_OVERRIDE) == override_before
    assert not registry_path().exists()


# --- an untracked repository override that wtenv's exclude block hides (FR-002, M3; T122) --------

EXCLUDED = "compose_override_excluded"


def untracked_override(worktree: Path, name: str) -> Path:
    """Write the repository's override into `worktree` and leave it untracked."""
    path = worktree / name
    path.write_text(OVERRIDE, encoding="utf-8")
    return path


def lines_of(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def provisioned_automatic_worktree(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> Path:
    """Worktree A: no override of its own, so its `up` adds `/compose.override.yaml` to the block."""
    worktree = make_worktree(repo, add_worktree, "automatic", override=None)
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    assert "/compose.override.yaml" in block_lines(exclude_file(repo))
    return worktree


def test_an_untracked_repository_override_ignored_through_wtenvs_block_is_warned(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    provisioned_automatic_worktree(repo, add_worktree, run_wtenv, compose_projects)
    other = make_worktree(repo, add_worktree, "other", override=None)
    override = untracked_override(other, "compose.override.yaml")
    before = sha256(override)
    exclude_before = lines_of(exclude_file(repo))

    result = up(run_wtenv, other)

    compose_projects.track(project_of(other))
    record = entry_of(other).compose
    assert record is not None
    assert (record.override, record.repository_override) == (
        ISOLATION_FILE,
        "compose.override.yaml",
    )
    (warning,) = [w for w in result.warnings if w.code.value == EXCLUDED]
    assert warning.details == {"file": str(override), "pattern": "/compose.override.yaml"}
    assert sha256(override) == before
    assert set(exclude_before) <= set(lines_of(exclude_file(repo)))  # no line was removed


@pytest.mark.parametrize("case", ["tracked", "own exclude line", "gitignore line", "no line"])
def test_an_override_that_wtenvs_block_does_not_hide_is_not_warned(
    case: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    provisioned_automatic_worktree(repo, add_worktree, run_wtenv, compose_projects)
    other = make_worktree(repo, add_worktree, "other", override=None)
    name = "compose.override.yaml" if case == "tracked" else "compose.override.yml"
    override = untracked_override(other, name)
    if case == "tracked":
        git(other, "add", "--force", name)  # the block hides it from `add -A`, so force it
        git(other, "commit", "-m", "track the override")  # git tracks it: the block line is moot
    if case == "own exclude line":
        path = exclude_file(repo)  # a line of the developer's, above wtenv's block
        path.write_text("/compose.override.yml\n" + path.read_text(encoding="utf-8"))
    if case == "gitignore line":
        (other / ".gitignore").write_text("compose.override.yml\n", encoding="utf-8")
        git(other, "add", ".gitignore")
        git(other, "commit", "-m", "ignore the override")
    before = sha256(override)

    result = up(run_wtenv, other)

    compose_projects.track(project_of(other))
    assert [w.code.value for w in result.warnings if w.code.value == EXCLUDED] == []
    record = entry_of(other).compose
    assert record is not None and record.repository_override == name
    assert sha256(override) == before


# --- the record follows the files when the isolation path stays the same (T033) ------------------


@pytest.mark.parametrize("change", ["compose file moved", "repository override renamed"])
def test_a_moved_compose_file_or_renamed_override_is_recorded_and_verified_on_the_next_up(
    change: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """The isolation path is the same in both, so only the record's `file` and
    `repository_override` can follow. Verification must then use the new files: with a stale
    record, `COMPOSE_FILE` would name a file that no longer exists."""
    worktree = make_worktree(repo, add_worktree, "follows")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    git(worktree, "mv", "compose.override.yml", "docker-compose.override.yml")
    if change == "compose file moved":
        git(worktree, "mv", "compose.yaml", "docker-compose.yaml")
        (worktree / "wtenv.toml").write_text(
            TOML.replace("compose.yaml", "docker-compose.yaml"), encoding="utf-8"
        )
    commit_all(worktree)
    new_file = "docker-compose.yaml" if change == "compose file moved" else "compose.yaml"
    override = worktree / "docker-compose.override.yml"
    before = sha256(override)

    up(run_wtenv, worktree)

    record = entry_of(worktree).compose
    assert record is not None
    assert (record.file, record.repository_override) == (new_file, "docker-compose.override.yml")
    assert (record.override, record.override_state) == (ISOLATION_FILE, ResourceState.CREATED)
    assert sha256(override) == before
    process = isolated_compose(worktree, "--profile", "*", "config", "--format", "json")
    assert process.returncode == 0, process.stderr
    backend = json.loads(process.stdout)["services"]["backend"]
    assert backend["command"] == COMMAND
    (published,) = entry_of(worktree).published
    assert [int(p["published"]) for p in backend["ports"]] == [published.port]


# --- changes of arrangement (FR-002, FR-007, FR-008, FR-009; data-model.md, Transitions; T041) ---

# The repository's override for the transitions: no ports of its own, so that the isolation file
# and v0.1.1's override hold the same ports (the two stacks differ only in the file they use).
COMMAND_OVERRIDE = (
    'services:\n  cache:\n    command: ["valkey-server", "--save", "", "--appendonly", "no"]\n'
)
OLD_OVERRIDE = (
    "compose.override.yaml"  # v0.1.1's override name: wtenv's file in the automatic arrangement
)


def golden_for(worktree: Path) -> str:
    """The golden override of files.md with this worktree's project name and assigned ports."""
    ports = {p.service: p.port for p in entry_of(worktree).published}
    return (
        GOLDEN.read_text(encoding="utf-8")
        .replace("wtenv-feature-x-3f9a1c2b", project_of(worktree))
        .replace('"20011"', f'"{ports["db"]}"')
        .replace('"20012"', f'"{ports["cache"]}"')
    )


def kinds_and_actions(result: UpResult) -> list[tuple[str, str]]:
    """`(item kind, action)` of every change of an `up` that concerns a compose override."""
    return [
        (c.item.kind.value, c.action) for c in result.changes if "override" in c.item.kind.value
    ]


def test_a_repository_override_appearing_removes_only_wtenvs_own_override(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "appears", stack=TWO_PORTS, override=None)
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    own = worktree / OLD_OVERRIDE
    assert own.read_text(encoding="utf-8") == golden_for(worktree)
    repository_override = worktree / REPOSITORY_OVERRIDE
    repository_override.write_text(COMMAND_OVERRIDE, encoding="utf-8")
    commit_all(worktree)
    before = sha256(repository_override)

    result = up(run_wtenv, worktree)

    assert not own.exists()  # wtenv's own file was removed, under the header check
    isolation_text = (worktree / ISOLATION_FILE).read_text(encoding="utf-8")
    assert isolation_text.startswith(isolation.OVERRIDE_HEADER)
    assert f'name: "{project_of(worktree)}"' in isolation_text
    record = entry_of(worktree).compose
    assert record is not None
    assert (record.override, record.repository_override) == (ISOLATION_FILE, REPOSITORY_OVERRIDE)
    assert record.override_state is ResourceState.CREATED
    assert sha256(repository_override) == before
    assert ("compose_override", "released") in kinds_and_actions(result)
    assert f"/{REPOSITORY_OVERRIDE}" not in entry_of(worktree).exclude_patterns
    assert git(worktree, "status", "--short") == ""


def test_the_repository_override_going_returns_to_v0_1_1s_override(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "goes", stack=TWO_PORTS, override=COMMAND_OVERRIDE)
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    assert (worktree / ISOLATION_FILE).exists()
    git(worktree, "rm", "-q", REPOSITORY_OVERRIDE)
    git(worktree, "commit", "-m", "drop the override")

    result = up(run_wtenv, worktree)

    assert not (worktree / ISOLATION_FILE).exists()
    assert (worktree / OLD_OVERRIDE).read_text(encoding="utf-8") == golden_for(worktree)
    record = entry_of(worktree).compose
    assert record is not None
    assert (record.override, record.repository_override) == (OLD_OVERRIDE, None)
    assert record.override_state is ResourceState.CREATED
    assert ("compose_override", "released") in kinds_and_actions(result)
    assert "/compose.override.yaml" in entry_of(worktree).exclude_patterns
    assert git(worktree, "status", "--short") == ""


@pytest.mark.parametrize("new_text", ["same text", "text changes"])
def test_another_override_name_updates_the_record_and_rewrites_only_a_changed_isolation_file(
    new_text: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(
        repo, add_worktree, "renamed", stack=TWO_PORTS, override=COMMAND_OVERRIDE
    )
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    isolation_file = worktree / ISOLATION_FILE
    written = isolation_file.read_bytes()
    stat_before = isolation_file.stat()
    git(worktree, "mv", REPOSITORY_OVERRIDE, "docker-compose.override.yml")
    renamed = worktree / "docker-compose.override.yml"
    if new_text == "text changes":  # the new override publishes one more port
        renamed.write_text(
            COMMAND_OVERRIDE + '    ports:\n      - "16380:6380"\n', encoding="utf-8"
        )
    commit_all(worktree)
    before = sha256(renamed)

    result = up(run_wtenv, worktree)

    record = entry_of(worktree).compose
    assert record is not None
    assert (record.override, record.repository_override) == (
        ISOLATION_FILE,
        "docker-compose.override.yml",
    )
    assert sha256(renamed) == before
    action = dict(kinds_and_actions(result))["compose_override"]
    if new_text == "same text":
        assert (action, isolation_file.read_bytes()) == ("unchanged", written)
        assert isolation_file.stat().st_mtime_ns == stat_before.st_mtime_ns
    else:
        assert action == "updated"
        assert isolation_file.read_bytes() != written


def test_compose_removed_in_exec_only_removes_the_isolation_file_and_clears_the_repository_override(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(
        repo, add_worktree, "nocompose", stack=TWO_PORTS, override=COMMAND_OVERRIDE
    )
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    project = project_of(worktree)
    repository_override = worktree / REPOSITORY_OVERRIDE
    before = sha256(repository_override)
    (worktree / "wtenv.toml").write_text('ports = ["PORT"]\n', encoding="utf-8")
    commit_all(worktree)

    result = up(run_wtenv, worktree)

    assert not (worktree / ISOLATION_FILE).exists()
    record = entry_of(worktree).compose
    assert record is not None  # the project stays recorded until `down` (FR-065)
    assert record.project == project
    assert record.override == ISOLATION_FILE
    assert record.override_state is ResourceState.REMOVING
    assert record.repository_override is None
    assert sha256(repository_override) == before
    assert ("compose_override", "released") in kinds_and_actions(result)
    assert (worktree / REPOSITORY_OVERRIDE).exists()


# --- an `up` interrupted in each transition, then run again (FR-067, FR-069; T041) ---------------

KILLED = 137
# Runs `wtenv up` in a child that dies at one point: right after the registry is saved (step 8),
# right after wtenv unlinks one of its override files, or right after a compose record is saved
# as `creating` (step 11, before the new file is written).
INTERRUPT_CHILD = """
import os, sys, pathlib
import wtenv.provision, wtenv.registry

hook = "{hook}"
if hook == "registry":
    real_save = wtenv.registry.save

    def save(*args, **kwargs):
        real_save(*args, **kwargs)
        os._exit({killed})

    wtenv.registry.save = save
elif hook == "unlink":
    real_unlink = pathlib.Path.unlink

    def unlink(self, *args, **kwargs):
        real_unlink(self, *args, **kwargs)
        if self.name in ("compose.override.yaml", "compose.isolation.yaml"):
            os._exit({killed})

    pathlib.Path.unlink = unlink
elif hook == "creating":
    real_record = wtenv.provision._save_compose_record

    def record(git_dir, compose):
        real_record(git_dir, compose)
        if compose.override_state.value == "creating":
            os._exit({killed})

    wtenv.provision._save_compose_record = record
from wtenv.cli import main
sys.exit(main(["up"]))
"""


def interrupt_up(worktree: Path, hook: str) -> None:
    """Run `wtenv up` in `worktree` in a child process that dies at `hook`."""
    code = INTERRUPT_CHILD.format(hook=hook, killed=KILLED)
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == KILLED, process.stdout + process.stderr


def setup_transition(
    transition: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    projects: ComposeProjects,
) -> Path:
    """Provision a worktree, change its files for `transition`, and return it (not yet `up`)."""
    exec_only_first = transition != "repository override appears"
    worktree = make_worktree(
        repo,
        add_worktree,
        "interrupted",
        stack=TWO_PORTS,
        override=COMMAND_OVERRIDE if exec_only_first else None,
    )
    up(run_wtenv, worktree)
    projects.track(project_of(worktree))
    if transition == "repository override appears":
        (worktree / REPOSITORY_OVERRIDE).write_text(COMMAND_OVERRIDE, encoding="utf-8")
    elif transition == "repository override goes":
        git(worktree, "rm", "-q", REPOSITORY_OVERRIDE)
    elif transition == "another override name":
        git(worktree, "mv", REPOSITORY_OVERRIDE, "docker-compose.override.yml")
    else:  # compose removed
        (worktree / "wtenv.toml").write_text('ports = ["PORT"]\n', encoding="utf-8")
    commit_all(worktree) if transition != "repository override goes" else git(
        worktree, "commit", "-m", "drop the override"
    )
    return worktree


# Per transition: the points where a run can die, with what the record and the files then show.
# `(hook, record state, repository_override, old file there, new file there)`.
INTERRUPTIONS = {
    "repository override appears": [
        ("registry", "removing", None, True, False),
        ("unlink", "removing", None, False, False),
        ("creating", "creating", REPOSITORY_OVERRIDE, False, False),
    ],
    "repository override goes": [
        ("registry", "removing", REPOSITORY_OVERRIDE, True, False),
        ("unlink", "removing", REPOSITORY_OVERRIDE, False, False),
        ("creating", "creating", None, False, False),
    ],
    "another override name": [
        ("registry", "created", "docker-compose.override.yml", True, True),
    ],
    "compose removed": [
        ("registry", "created", REPOSITORY_OVERRIDE, True, False),
        ("unlink", "removing", None, False, False),
    ],
}
INTERRUPTION_CASES = [
    pytest.param(transition, *point, id=f"{transition}: after {point[0]}")
    for transition, points in INTERRUPTIONS.items()
    for point in points
]
OLD_FILE = {
    "repository override appears": OLD_OVERRIDE,
    "repository override goes": ISOLATION_FILE,
    "another override name": ISOLATION_FILE,
    "compose removed": ISOLATION_FILE,
}
NEW_FILE = {
    "repository override appears": ISOLATION_FILE,
    "repository override goes": OLD_OVERRIDE,
    "another override name": ISOLATION_FILE,
    "compose removed": None,
}


@pytest.mark.parametrize(
    ("transition", "hook", "state", "repository_override", "old_there", "new_there"),
    INTERRUPTION_CASES,
)
def test_an_up_interrupted_in_a_transition_leaves_a_recorded_state_and_the_next_up_finishes(
    transition: str,
    hook: str,
    state: str,
    repository_override: str | None,
    old_there: bool,
    new_there: bool,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = setup_transition(transition, repo, add_worktree, run_wtenv, compose_projects)
    repository_file = worktree / REPOSITORY_OVERRIDE
    repository_before = sha256(repository_file) if repository_file.exists() else None
    old, new = worktree / OLD_FILE[transition], NEW_FILE[transition]

    interrupt_up(worktree, hook)

    # What the dead run left: a record in `creating` or `removing`, and files that agree with it.
    entry = entry_of(worktree)
    assert entry.state == "incomplete"
    assert entry.compose is not None
    assert entry.compose.override_state.value == state
    assert entry.compose.repository_override == repository_override
    if hook != "registry" or transition != "another override name":
        assert old.exists() == old_there
    if new is not None and transition != "another override name":
        assert (worktree / new).exists() == new_there
    if repository_before is not None and repository_file.exists():
        assert sha256(repository_file) == repository_before

    result = up(run_wtenv, worktree)

    entry = entry_of(worktree)
    assert result.ok and entry.state == "provisioned"
    assert entry.compose is not None
    expected_state = "removing" if transition == "compose removed" else "created"
    assert entry.compose.override_state.value == expected_state
    if new is None:  # `[compose]` removed: wtenv's file is gone and stays gone
        assert not old.exists()
        assert entry.compose.repository_override is None
    else:
        assert (worktree / new).exists()
        assert entry.compose.override == new
    if transition in ("repository override appears", "repository override goes"):
        assert not old.exists()
    if transition == "another override name":
        assert entry.compose.repository_override == "docker-compose.override.yml"
    if repository_before is not None and repository_file.exists():
        assert sha256(repository_file) == repository_before


# --- adopting a tracked file at the recorded path (FR-006; data-model.md, Transitions; T046) ------


def provisioned_automatic(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, projects: ComposeProjects, name: str
) -> Path:
    """An automatic worktree, brought up: wtenv's own `compose.override.yaml` is recorded."""
    worktree = make_worktree(repo, add_worktree, name, override=None)
    up(run_wtenv, worktree)
    projects.track(project_of(worktree))
    assert (
        (worktree / OLD_OVERRIDE).read_text(encoding="utf-8").startswith(isolation.OVERRIDE_HEADER)
    )
    return worktree


def test_an_untracked_headerless_file_at_the_recorded_path_is_still_an_ownership_conflict(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = provisioned_automatic(repo, add_worktree, run_wtenv, compose_projects, "conflict")
    recorded = worktree / OLD_OVERRIDE
    recorded.write_text(OVERRIDE, encoding="utf-8")  # replaced, and git does not track it
    tree_before = snapshot_tree(worktree)
    registry_before = registry_path().read_bytes()

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "compose_override", "name": str(recorded)}
    assert recorded.read_text(encoding="utf-8") == OVERRIDE
    assert snapshot_tree(worktree) == tree_before
    assert registry_path().read_bytes() == registry_before


def test_a_tracked_headerless_file_at_the_recorded_path_is_adopted_as_the_repositorys(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = provisioned_automatic(repo, add_worktree, run_wtenv, compose_projects, "adopt")
    recorded = worktree / OLD_OVERRIDE
    recorded.write_text(OVERRIDE, encoding="utf-8")  # a checkout brought the repository's file
    git(worktree, "add", "--force", OLD_OVERRIDE)  # wtenv's block hides it from `add -A`
    git(worktree, "commit", "-m", "track the override")
    before, stat_before = sha256(recorded), recorded.stat()
    block_before = block_lines(exclude_file(repo))

    result = up(run_wtenv, worktree)

    record = entry_of(worktree).compose
    assert record is not None
    assert (record.override, record.repository_override) == (ISOLATION_FILE, OLD_OVERRIDE)
    assert record.override_state is ResourceState.CREATED
    patterns = entry_of(worktree).exclude_patterns
    assert f"/{OLD_OVERRIDE}" not in patterns and f"/{ISOLATION_FILE}" in patterns
    assert block_lines(exclude_file(repo)) == sorted({*block_before, f"/{ISOLATION_FILE}"})
    (warning,) = [w for w in result.warnings if w.code.value == "compose_override_adopted"]
    assert warning.details == {"file": str(recorded)}
    # Adoption changed only the registry: the file is the same bytes, mode and modification time.
    stat_after = recorded.stat()
    assert sha256(recorded) == before
    assert (stat_after.st_mode, stat_after.st_mtime_ns) == (
        stat_before.st_mode,
        stat_before.st_mtime_ns,
    )
    assert (
        (worktree / ISOLATION_FILE)
        .read_text(encoding="utf-8")
        .startswith(isolation.OVERRIDE_HEADER)
    )
    assert ("compose_override", "released") not in kinds_and_actions(result)
    assert git(worktree, "status", "--short") == ""

    again = up(run_wtenv, worktree)  # a repeat is stable and no longer an adoption

    assert [
        w.code.value for w in again.warnings if w.code.value == "compose_override_adopted"
    ] == []
    assert sha256(recorded) == before


def test_an_adopted_file_and_another_repository_override_are_ambiguous(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = provisioned_automatic(repo, add_worktree, run_wtenv, compose_projects, "two")
    recorded = worktree / OLD_OVERRIDE
    recorded.write_text(OVERRIDE, encoding="utf-8")
    other = worktree / REPOSITORY_OVERRIDE
    other.write_text(OVERRIDE, encoding="utf-8")
    git(worktree, "add", "--force", OLD_OVERRIDE, REPOSITORY_OVERRIDE)
    git(worktree, "commit", "-m", "track both")
    tree_before = snapshot_tree(worktree)
    registry_before = registry_path().read_bytes()

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (21, "override_ambiguous")
    assert error["details"]["files"] == sorted([str(recorded), str(other)])
    assert snapshot_tree(worktree) == tree_before
    assert registry_path().read_bytes() == registry_before


# --- scenario 9: `up` and `ls` report the arrangement (T062; FR-004, FR-005) -------------------

EXEC_ONLY_WARNING = "compose_isolation_exec_only"
NO_COMPOSE_TOML = 'ports = ["PORT"]\nblock_size = 10\n'


def arrangement_warnings(result: UpResult) -> list[dict[str, Any]]:
    return [w.details for w in result.warnings if w.code.value == EXEC_ONLY_WARNING]


def listed(run_wtenv: Run, worktree: Path) -> Any:
    """The `ls --json` row of `worktree`; the listing must succeed."""
    process = run_wtenv(["ls", "--json"], worktree)
    assert process.returncode == 0, process.stdout + process.stderr
    (row,) = [view for view in parse_ls(process).worktrees if view.path == str(worktree)]
    return row


def test_up_json_reports_the_exec_only_arrangement_and_warns_on_every_up(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "report")
    override = worktree / REPOSITORY_OVERRIDE
    before = sha256(override)

    first = up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    again = up(run_wtenv, worktree)

    for result in (first, again):
        assert result.worktree is not None
        assert result.worktree.compose_isolation == "exec_only"
        assert result.worktree.compose_repository_override == REPOSITORY_OVERRIDE
        (details,) = arrangement_warnings(result)
        assert details == {
            "repository_override": REPOSITORY_OVERRIDE,
            "isolation_file": ISOLATION_FILE,
        }
        (warning,) = [w for w in result.warnings if w.code.value == EXEC_ONLY_WARNING]
        assert "without isolation" in warning.message
        assert "wtenv exec" in warning.message
    assert sha256(override) == before


def test_up_text_says_a_plain_docker_compose_runs_without_isolation(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "text")

    process = run_wtenv(["up"], worktree)

    compose_projects.track(project_of(worktree))
    assert process.returncode == 0, process.stdout + process.stderr
    lines = process.stdout.splitlines()
    start = lines.index(
        f"  isolation  exec only: the repository owns {REPOSITORY_OVERRIDE}. Run Compose through"
    )
    assert lines[start + 1] == (
        "             `wtenv exec -- docker compose ...`; "
        "a plain `docker compose` here runs WITHOUT isolation."
    )
    assert any(line.startswith("  compose    ") and ISOLATION_FILE in line for line in lines)
    assert f"{EXEC_ONLY_WARNING}" in process.stdout + process.stderr


def test_ls_json_reports_the_arrangement_of_each_worktree(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    owned = make_worktree(repo, add_worktree, "owned")
    plain = make_worktree(repo, add_worktree, "plain", override=None)
    bare = make_worktree(repo, add_worktree, "bare", override=None, toml=NO_COMPOSE_TOML)
    for worktree in (owned, plain, bare):
        up(run_wtenv, worktree)
    compose_projects.track(project_of(owned))
    compose_projects.track(project_of(plain))

    rows = {
        name: listed(run_wtenv, worktree)
        for name, worktree in (("owned", owned), ("plain", plain), ("bare", bare))
    }

    assert (rows["owned"].compose_isolation, rows["owned"].compose_repository_override) == (
        "exec_only",
        REPOSITORY_OVERRIDE,
    )
    assert (rows["plain"].compose_isolation, rows["plain"].compose_repository_override) == (
        "automatic",
        None,
    )
    assert (rows["bare"].compose_isolation, rows["bare"].compose_repository_override) == (
        None,
        None,
    )


def test_the_ls_text_table_has_no_isolation_column(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "table")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))

    process = run_wtenv(["ls"], worktree)

    assert process.returncode == 0, process.stdout + process.stderr
    header = process.stdout.splitlines()[0].split()
    assert header[-2:] == ["COMPOSE", "PATH"]
    assert "ISOLATION" not in header and "exec_only" not in process.stdout


def test_without_a_repository_override_up_reports_automatic_and_says_nothing_more(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "auto", override=None)

    result = up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    text = run_wtenv(["up"], worktree)

    assert result.worktree is not None
    assert result.worktree.compose_isolation == "automatic"
    assert result.worktree.compose_repository_override is None
    assert arrangement_warnings(result) == []
    assert "isolation" not in text.stdout and EXEC_ONLY_WARNING not in text.stdout + text.stderr


def test_without_compose_up_reports_neither_field(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run
) -> None:
    worktree = make_worktree(repo, add_worktree, "nocompose", override=None, toml=NO_COMPOSE_TOML)

    result = up(run_wtenv, worktree)

    assert result.worktree is not None
    assert result.worktree.compose_isolation is None
    assert result.worktree.compose_repository_override is None
    assert arrangement_warnings(result) == []


def test_the_report_follows_the_repository_override_when_it_goes_away(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(
        repo, add_worktree, "follows", stack=TWO_PORTS, override=COMMAND_OVERRIDE
    )
    assert up(run_wtenv, worktree).worktree is not None
    compose_projects.track(project_of(worktree))
    git(worktree, "rm", "-q", REPOSITORY_OVERRIDE)
    git(worktree, "commit", "-m", "drop the override")

    result = up(run_wtenv, worktree)

    assert result.worktree is not None
    assert result.worktree.compose_isolation == "automatic"
    assert result.worktree.compose_repository_override is None
    assert arrangement_warnings(result) == []
    assert listed(run_wtenv, worktree).compose_isolation == "automatic"


# --- scenario 5: `down` and `gc` remove the isolation file and keep the repository's override ----
# (T065, T110, T137; FR-002, FR-006, FR-009, SC-007; plan.md S3, S4)

TEARDOWN_MODES = ("dry-run", "down", "gc", "gc-release")
COMPOSE_OVERRIDE = "compose_override"


class Outcome(NamedTuple):
    """What one teardown command reported: `listed` is `would_remove` for a dry run."""

    status: int
    listed: list[Item]
    already_absent: list[Item]
    failed: list[FailedItem]
    kept_items: list[KeptItem]

    def names(self) -> set[str]:
        """Every path any list of the result names."""
        everything = [*self.listed, *self.already_absent, *self.failed, *self.kept_items]
        return {item.name for item in everything}


def release(mode: str, repo: Path, worktree: Path, tmp_path: Path, run_wtenv: Run) -> Outcome:
    """Run `down --dry-run`, `down`, `gc` (after `git worktree remove`) or `gc --release`.

    For `gc --release` the repository is deleted first, so the worktree is no longer one.
    """
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    if mode in ("dry-run", "down"):
        arguments = ["down", "--json"] + (["--dry-run"] if mode == "dry-run" else [])
        process = run_wtenv(arguments, worktree)
        down = parse_down(process)
        listed = down.would_remove if mode == "dry-run" else down.removed
        return Outcome(
            process.returncode, listed, down.already_absent, down.failed, down.kept_items
        )
    if mode == "gc":
        git(repo, "worktree", "remove", "--force", str(worktree))
        process = run_wtenv(["gc", "--json"], outside)
    else:
        shutil.rmtree(repo)
        process = run_wtenv(["gc", "--release", str(worktree), "--json"], outside)
    gc = parse_gc(process)
    return Outcome(process.returncode, gc.removed, gc.already_absent, gc.failed, gc.kept_items)


def file_state(path: Path) -> tuple[str, int, int]:
    """The SHA-256, mode and modification time of a file, to compare before and after."""
    assert path.exists(), f"{path} is gone"
    status = path.stat()
    return sha256(path), status.st_mode, status.st_mtime_ns


def git_dir_of(worktree: Path) -> str:
    return current_worktree(worktree).git_dir


@pytest.mark.parametrize("mode", TEARDOWN_MODES)
def test_down_removes_the_isolation_file_and_never_the_repository_override(
    mode: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "teardown")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    git_dir = git_dir_of(worktree)
    override = worktree / REPOSITORY_OVERRIDE
    override_before = file_state(override)
    isolated = worktree / ISOLATION_FILE

    outcome = release(mode, repo, worktree, tmp_path, run_wtenv)

    assert outcome.status == 0
    assert outcome.failed == [] and outcome.kept_items == []
    assert str(override) not in outcome.names()  # in no list: removed, absent, failed or kept
    if mode == "gc":  # `git worktree remove` removed the directory, and the file with it
        assert (COMPOSE_OVERRIDE, str(isolated)) in [
            (item.kind.value, item.name) for item in outcome.already_absent
        ]
    else:
        assert (COMPOSE_OVERRIDE, str(isolated)) in [
            (item.kind.value, item.name) for item in outcome.listed
        ]
        assert file_state(override) == override_before  # bytes, mode and modification time
    assert isolated.exists() is (mode == "dry-run")
    assert (git_dir in load().worktrees) is (mode == "dry-run")


@pytest.mark.parametrize("mode", TEARDOWN_MODES)
def test_a_tracked_headerless_file_at_the_recorded_path_is_kept_by_down_and_reported_in_kept_items(
    mode: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = provisioned_automatic(repo, add_worktree, run_wtenv, compose_projects, "kept")
    recorded = worktree / OLD_OVERRIDE
    recorded.write_text(OVERRIDE, encoding="utf-8")  # a checkout brought the repository's file
    git(worktree, "add", "--force", OLD_OVERRIDE)
    git(worktree, "commit", "-m", "track the override")
    git_dir = git_dir_of(worktree)
    before = file_state(recorded)

    outcome = release(mode, repo, worktree, tmp_path, run_wtenv)

    if mode in ("dry-run", "down"):
        assert outcome.status == 0
        assert [(k.kind.value, k.name, k.reason) for k in outcome.kept_items] == [
            (COMPOSE_OVERRIDE, str(recorded), "repository_owned")
        ]
        assert outcome.failed == []
        assert str(recorded) not in {item.name for item in outcome.listed}
        assert file_state(recorded) == before
        assert (git_dir in load().worktrees) is (mode == "dry-run")
    elif mode == "gc":  # `git worktree remove` removed the directory: nothing to fail or keep
        assert outcome.status == 0
        assert outcome.failed == [] and outcome.kept_items == []
        assert git_dir not in load().worktrees
    else:  # `gc --release`: git cannot say the file is tracked, so it is preserved and failed
        assert outcome.status == 13
        assert [(f.kind.value, f.name) for f in outcome.failed] == [
            (COMPOSE_OVERRIDE, str(recorded))
        ]
        assert "header" in outcome.failed[0].reason
        assert outcome.kept_items == []
        assert file_state(recorded) == before
        assert git_dir in load().worktrees


def link_case(
    what: str, repo: Path, worktree: Path, decoy: Path, run_wtenv: Run
) -> tuple[Path, Path | None]:
    """Replace a path wtenv recorded by a link into `decoy`; return the link and the override.

    The header at the target is wtenv's, so a header read through the link would pass.
    """
    target = planted_header_target(decoy)
    if what == "wtenv directory":
        shutil.rmtree(worktree / ".wtenv")
        (decoy / "sub" / "compose.isolation.yaml").write_text(
            target.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (worktree / ".wtenv").symlink_to(decoy / "sub")
        return worktree / ISOLATION_FILE, worktree / REPOSITORY_OVERRIDE
    if what == "isolation file":
        (worktree / ISOLATION_FILE).unlink()
        (worktree / ISOLATION_FILE).symlink_to(target)
        return worktree / ISOLATION_FILE, worktree / REPOSITORY_OVERRIDE
    (worktree / OLD_OVERRIDE).unlink()  # the recorded wtenv path of the automatic arrangement
    (worktree / OLD_OVERRIDE).symlink_to(target)
    return worktree / OLD_OVERRIDE, None


@pytest.mark.parametrize("mode", TEARDOWN_MODES)
@pytest.mark.parametrize("what", ["wtenv directory", "isolation file", "automatic recorded file"])
def test_a_link_at_a_recorded_wtenv_path_is_failed_by_teardown_and_its_target_untouched(
    what: str,
    mode: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    decoy: Path,
    tmp_path: Path,
) -> None:
    if what == "automatic recorded file":
        worktree = provisioned_automatic(repo, add_worktree, run_wtenv, compose_projects, "ln")
    else:
        worktree = make_worktree(repo, add_worktree, "ln")
        up(run_wtenv, worktree)
        compose_projects.track(project_of(worktree))
    git_dir = git_dir_of(worktree)
    link, override = link_case(what, repo, worktree, decoy, run_wtenv)
    override_before = None if override is None else file_state(override)
    decoy_before = snapshot_tree(decoy)

    outcome = release(mode, repo, worktree, tmp_path, run_wtenv)

    assert snapshot_tree(decoy) == decoy_before  # nothing written, read or removed through it
    if mode == "gc":  # the directory, and the link with it, is gone; the target is not
        assert outcome.failed == [] and git_dir not in load().worktrees
        return
    assert outcome.status == (0 if mode == "dry-run" else 13)
    assert [(f.kind.value, f.name, f.reason) for f in outcome.failed] == [
        (COMPOSE_OVERRIDE, str(link), "symlink")
    ]
    assert link.is_symlink() or what == "wtenv directory"
    assert str(link) not in {item.name for item in outcome.listed}
    assert git_dir in load().worktrees  # the record stays
    if override is not None:
        assert file_state(override) == override_before


@pytest.mark.parametrize("mode", TEARDOWN_MODES)
def test_an_untracked_link_at_a_repository_override_name_is_the_repositorys_and_survives_teardown(
    mode: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    decoy: Path,
    tmp_path: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "repo-link", override=None)
    target = decoy / "repository-override.yml"
    target.write_text(OVERRIDE, encoding="utf-8")
    link = worktree / "compose.override.yml"  # untracked, a name Compose loads
    link.symlink_to(target)
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    assert entry_of(worktree).compose is not None
    assert entry_of(worktree).compose.repository_override == "compose.override.yml"  # type: ignore[union-attr]
    decoy_before = snapshot_tree(decoy)

    outcome = release(mode, repo, worktree, tmp_path, run_wtenv)

    assert outcome.status == 0 and outcome.failed == []
    assert str(link) not in outcome.names()
    assert snapshot_tree(decoy) == decoy_before
    if mode != "gc":
        assert link.is_symlink() and os.readlink(link) == str(target)


@pytest.mark.parametrize("mode", TEARDOWN_MODES)
def test_an_untracked_headerless_file_at_the_isolation_path_is_failed_and_kept_by_teardown(
    mode: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "headerless")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    git_dir = git_dir_of(worktree)
    isolated = worktree / ISOLATION_FILE
    isolated.write_text(OVERRIDE, encoding="utf-8")  # replaced by a file that is not wtenv's
    override_before = file_state(worktree / REPOSITORY_OVERRIDE)
    before = file_state(isolated)

    outcome = release(mode, repo, worktree, tmp_path, run_wtenv)

    if mode == "gc":  # the directory is gone
        assert outcome.failed == [] and git_dir not in load().worktrees
        return
    assert outcome.status == (0 if mode == "dry-run" else 13)
    assert [(f.kind.value, f.name) for f in outcome.failed] == [(COMPOSE_OVERRIDE, str(isolated))]
    assert "header" in outcome.failed[0].reason
    assert outcome.kept_items == []
    assert file_state(isolated) == before
    assert file_state(worktree / REPOSITORY_OVERRIDE) == override_before
    assert git_dir in load().worktrees


# --- a git-tracked file that keeps wtenv's header (T137; FR-006, safety finding L2, SC-007) -------


def track_wtenvs_file(worktree: Path, relative: str) -> None:
    """Force-add wtenv's own file (header intact); its exclude line hides it from `add -A`."""
    git(worktree, "add", "--force", relative)
    git(worktree, "commit", "-m", "track wtenv's file")


def test_a_tracked_automatic_override_that_keeps_the_header_is_adopted_by_the_next_up(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = provisioned_automatic(repo, add_worktree, run_wtenv, compose_projects, "keeps")
    recorded = worktree / OLD_OVERRIDE
    track_wtenvs_file(worktree, OLD_OVERRIDE)
    before = file_state(recorded)

    result = up(run_wtenv, worktree)

    record = entry_of(worktree).compose
    assert record is not None
    assert (record.override, record.repository_override) == (ISOLATION_FILE, OLD_OVERRIDE)
    (warning,) = [w for w in result.warnings if w.code.value == "compose_override_adopted"]
    assert warning.details == {"file": str(recorded)}
    assert result.worktree is not None and result.worktree.compose_isolation == "exec_only"
    assert file_state(recorded) == before  # bytes, mode and modification time
    assert ("compose_override", "released") not in kinds_and_actions(result)


@pytest.mark.parametrize("mode", TEARDOWN_MODES)
def test_a_tracked_file_that_keeps_wtenvs_header_is_kept_by_down_and_not_by_gc_release(
    mode: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = provisioned_automatic(repo, add_worktree, run_wtenv, compose_projects, "kept2")
    recorded = worktree / OLD_OVERRIDE
    track_wtenvs_file(worktree, OLD_OVERRIDE)
    git_dir = git_dir_of(worktree)
    before = file_state(recorded)

    outcome = release(mode, repo, worktree, tmp_path, run_wtenv)

    assert outcome.status == 0 and outcome.failed == []
    if mode in ("dry-run", "down"):
        assert [(k.kind.value, k.name, k.reason) for k in outcome.kept_items] == [
            (COMPOSE_OVERRIDE, str(recorded), "repository_owned")
        ]
        assert str(recorded) not in {item.name for item in outcome.listed}
        assert file_state(recorded) == before
        assert (git_dir in load().worktrees) is (mode == "dry-run")
    elif mode == "gc":  # the directory is gone: nothing to fail or keep
        assert outcome.kept_items == [] and git_dir not in load().worktrees
    else:  # `gc --release` cannot see that the file is tracked: it keeps the header, so it goes
        assert outcome.kept_items == []
        assert not recorded.exists()  # known limit, as in v0.1.1 (tasks.md, "Deferred")
        assert git_dir not in load().worktrees


def test_a_tracked_isolation_file_is_an_ownership_conflict_for_up(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = make_worktree(repo, add_worktree, "tracked-isolation")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    track_wtenvs_file(worktree, ISOLATION_FILE)
    tree_before = snapshot_tree(worktree)
    registry_before = registry_path().read_bytes()

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (11, "ownership_conflict")
    assert error["details"] == {"kind": "compose_override", "name": str(worktree / ISOLATION_FILE)}
    assert snapshot_tree(worktree) == tree_before
    assert registry_path().read_bytes() == registry_before


@pytest.mark.parametrize("mode", TEARDOWN_MODES)
def test_a_tracked_isolation_file_is_kept_by_down_and_never_removed_there(
    mode: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "tracked-isolation-down")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    track_wtenvs_file(worktree, ISOLATION_FILE)
    isolated = worktree / ISOLATION_FILE
    override_before = file_state(worktree / REPOSITORY_OVERRIDE)
    before = file_state(isolated)

    outcome = release(mode, repo, worktree, tmp_path, run_wtenv)

    assert outcome.status == 0 and outcome.failed == []
    if mode in ("dry-run", "down"):
        assert [(k.kind.value, k.name) for k in outcome.kept_items] == [
            (COMPOSE_OVERRIDE, str(isolated))
        ]
        assert file_state(isolated) == before
        assert file_state(worktree / REPOSITORY_OVERRIDE) == override_before
    else:
        assert outcome.kept_items == []


# --- teardown of a record that is `removing` or `incomplete`, and of a rerun (T065; plan.md S4) ---


def hand_edit_registry(worktree: Path, change: Callable[[dict[str, Any]], None]) -> None:
    """Edit the entry of `worktree` in `registry.json` as a person with a text editor would."""
    path = registry_path()
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document["worktrees"][git_dir_of(worktree)])
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("file_there", [True, False], ids=["file there", "file already gone"])
@pytest.mark.parametrize(
    ("entry_state", "override_state"),
    [("incomplete", "creating"), ("incomplete", "removing"), ("provisioned", "removing")],
)
def test_a_record_left_in_a_transient_state_is_torn_down_safely(
    entry_state: str,
    override_state: str,
    file_there: bool,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "transient")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    git_dir = git_dir_of(worktree)
    isolated = worktree / ISOLATION_FILE
    if not file_there:
        isolated.unlink()
    hand_edit_registry(
        worktree,
        lambda entry: (
            entry.update(state=entry_state)
            or entry["compose"].update(override_state=override_state)
        ),
    )
    override_before = file_state(worktree / REPOSITORY_OVERRIDE)

    outcome = release("down", repo, worktree, tmp_path, run_wtenv)

    assert outcome.status == 0 and outcome.failed == [] and outcome.kept_items == []
    assert (COMPOSE_OVERRIDE, str(isolated)) in [
        (item.kind.value, item.name)
        for item in (outcome.listed if file_there else outcome.already_absent)
    ]
    assert not isolated.exists() and git_dir not in load().worktrees
    assert file_state(worktree / REPOSITORY_OVERRIDE) == override_before


def test_a_rerun_after_a_partial_failure_converges_and_touches_nothing_else(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    decoy: Path,
    tmp_path: Path,
) -> None:
    worktree = make_worktree(repo, add_worktree, "converge")
    up(run_wtenv, worktree)
    compose_projects.track(project_of(worktree))
    git_dir = git_dir_of(worktree)
    isolated = worktree / ISOLATION_FILE
    wtenvs = isolated.read_bytes()
    isolated.write_text("services: {}\n", encoding="utf-8")  # a checkout replaced it: not wtenv's
    (worktree / "notes.txt").write_text("mine\n", encoding="utf-8")  # theirs, not recorded
    override_before = file_state(worktree / REPOSITORY_OVERRIDE)
    notes_before = file_state(worktree / "notes.txt")
    decoy_before = snapshot_tree(decoy)

    first = release("down", repo, worktree, tmp_path, run_wtenv)

    assert first.status == 13
    assert [(f.kind.value, f.name) for f in first.failed] == [(COMPOSE_OVERRIDE, str(isolated))]
    assert git_dir in load().worktrees  # the entry stays, so that the rerun can finish
    assert isolated.read_text(encoding="utf-8") == "services: {}\n"

    isolated.write_bytes(wtenvs)  # the cause is fixed
    second = release("down", repo, worktree, tmp_path, run_wtenv)

    assert second.status == 0 and second.failed == []
    assert not isolated.exists() and git_dir not in load().worktrees
    assert file_state(worktree / REPOSITORY_OVERRIDE) == override_before
    assert file_state(worktree / "notes.txt") == notes_before
    assert snapshot_tree(decoy) == decoy_before

    third = run_wtenv(["down", "--json"], worktree)  # nothing is recorded any more

    assert third.returncode != 0 or parse_down(third).removed == []


def test_down_removes_nothing_the_repositorys_compose_files_name(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """A volume and a network the override declares `external`, and one it gives a fixed name,
    are the repository's: `down` removes the project's containers and nothing they name."""
    token = uuid.uuid4().hex[:10]
    external_volume, external_network = f"wtenv-test-{token}-ext", f"wtenv-test-{token}-net"
    fixed_volume = f"wtenv-test-{token}-fixed"
    override = f"""\
services:
  backend:
    ports:
      - "8000:6379"
    command: {json.dumps(COMMAND)}
    volumes:
      - ext:/ext
      - fixed:/fixed
    networks:
      - default
      - outer
volumes:
  ext:
    external: true
    name: {external_volume}
  fixed:
    name: {fixed_volume}
networks:
  outer:
    external: true
    name: {external_network}
"""
    docker = ["docker"]
    subprocess.run([*docker, "volume", "create", external_volume], check=True, capture_output=True)
    subprocess.run(
        [*docker, "network", "create", external_network], check=True, capture_output=True
    )
    try:
        worktree = make_worktree(repo, add_worktree, "names", override=override)
        up(run_wtenv, worktree)
        project = compose_projects.track(project_of(worktree))
        started = isolated_compose(worktree, "up", "-d")
        assert started.returncode == 0, started.stderr
        assert project_resources(project)["containers"]
        override_before = file_state(worktree / REPOSITORY_OVERRIDE)

        process = run_wtenv(["down", "--json"], worktree)

        result = parse_down(process)
        assert process.returncode == 0, process.stdout + process.stderr
        assert project_resources(project)["containers"] == []
        for kind, name in (("volume", external_volume), ("network", external_network)):
            found = subprocess.run(
                [*docker, kind, "inspect", name], capture_output=True, check=False
            )
            assert found.returncode == 0, f"{kind} {name} was removed"
        found = subprocess.run(
            [*docker, "volume", "inspect", fixed_volume], capture_output=True, check=False
        )
        assert found.returncode == 0, "the volume with a fixed name was removed"
        assert (fixed_volume, "fixed_name") in [(v.name, v.reason) for v in result.kept_volumes]
        removed_names = {item.name for item in result.removed}
        assert not {external_volume, external_network, fixed_volume} & removed_names
        assert file_state(worktree / REPOSITORY_OVERRIDE) == override_before
    finally:
        subprocess.run(
            [*docker, "volume", "rm", "-f", external_volume, fixed_volume],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            [*docker, "network", "rm", external_network], capture_output=True, check=False
        )
