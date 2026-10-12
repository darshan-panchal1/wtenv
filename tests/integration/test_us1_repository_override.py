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
import socket
import subprocess
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
    snapshot_tree,
)

from wtenv import isolation
from wtenv.identity import current_worktree
from wtenv.output import ResourceState, UpResult
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
