"""User story 2: every published port is available as a named variable, in the env file and in
`wtenv exec` (T019; spec.md, User Story 2; FR-010, FR-012, FR-013, FR-015, FR-016, FR-028, SC-002).

The tests use real git worktrees and a real Docker. A test starts containers only from an image
that is already on the machine, only under the project names wtenv generated for its own
worktrees, and removes exactly those projects at the end.
"""

import json
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import (
    COMPOSE_IMAGE,
    ComposeProjects,
    clean_environment,
    commit_all,
    exclude_file,
    parse_ls,
    parse_up,
    snapshot_tree,
)

from wtenv.envfile import read_section
from wtenv.errors import ErrorCode
from wtenv.identity import current_worktree
from wtenv.output import ItemKind, UpResult, WarningCode
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v0_1_1"
ENV_FILE = ".env.local"

# `backend` publishes 8000 tied to BACKEND_PORT (001:FR-031); `mailpit` publishes two untied
# ports. The valkey server listens on the container port, so a request can reach it (SC-002).
SERVER = '["valkey-server", "--port", "{port}", "--protected-mode", "no"]'
STACK = f"""\
services:
  backend:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    command: {SERVER.format(port=8000)}
    ports:
      - "${{BACKEND_PORT:-8000}}:8000"
  mailpit:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    command: {SERVER.format(port=8025)}
    ports:
      - "8025:8025"
      - "1025:1025"
"""
TOML = 'ports = ["PORT", "BACKEND_PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'
NAMES = [
    "PORT",
    "BACKEND_PORT",
    "WTENV_PORT_BACKEND_8000_TCP",
    "WTENV_PORT_MAILPIT_1025_TCP",
    "WTENV_PORT_MAILPIT_8025_TCP",
]


def colliding_stack(first: str, second: str) -> str:
    """A stack of two services that publish container port 6379 on host ports of their own."""
    service = (
        '  {name}:\n    image: %s\n    pull_policy: never\n    ports:\n      - "{host}:6379"\n'
    )
    return "services:\n" + "".join(
        service.format(name=name, host=host) % COMPOSE_IMAGE
        for name, host in ((first, 8001), (second, 8002))
    )


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def make_worktree(
    repo: Path, add_worktree: AddWorktree, name: str, *, toml: str = TOML, stack: str = STACK
) -> Path:
    """Add worktree `name` with a committed compose file and `wtenv.toml`."""
    worktree = add_worktree(repo, name, f"branch-{name}")
    (worktree / "compose.yaml").write_text(stack, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(toml, encoding="utf-8")
    commit_all(worktree)
    return worktree


def up(run_wtenv: Run, worktree: Path, compose_projects: ComposeProjects) -> UpResult:
    """Run `wtenv up --json`, which must succeed; track the project; return the `UpResult`."""
    process = run_wtenv(["up", "--json"], worktree)
    assert process.returncode == 0, process.stdout + process.stderr
    compose_projects.track(project_of(worktree))
    return parse_up(process)


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


def project_of(worktree: Path) -> str:
    compose = entry_of(worktree).compose
    assert compose is not None
    return compose.project


def section(worktree: Path) -> list[tuple[str, str]]:
    """The `(name, value)` lines of wtenv's section of the env file, in file order."""
    found = read_section(worktree / ENV_FILE)
    assert found is not None
    return found


def exec_env(run_wtenv: Run, worktree: Path) -> dict[str, str]:
    """Run `wtenv exec -- env` and return the environment the command saw."""
    process = run_wtenv(["exec", "--", "env"], worktree)
    assert process.returncode == 0, process.stderr
    return dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)


def optional_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def start_backend(worktree: Path) -> None:
    """Start the `backend` service of the worktree's own project, as a developer would."""
    subprocess.run(
        ["docker", "compose", "up", "-d", "--pull", "never", "backend"],
        cwd=worktree,
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=True,
    )


def stop_backend(worktree: Path) -> None:
    subprocess.run(
        ["docker", "compose", "stop", "backend"],
        cwd=worktree,
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=True,
    )


def ping(port: int, *, seconds: float = 20.0) -> str | None:
    """Send `PING` to 127.0.0.1:`port` until it is answered; None when it never is."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.sendall(b"PING\r\n")
                answer = connection.recv(64)
        except OSError:
            answer = b""
        if answer:
            return answer.decode()
        time.sleep(0.3)
    return None


def refuses(port: int, *, seconds: float = 20.0) -> bool:
    """Return whether a connection to 127.0.0.1:`port` is refused, within `seconds`."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
        except ConnectionRefusedError:
            return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


# --- scenario 1: one variable per published port, in the env file and in exec (FR-010, FR-013) --


def test_the_section_has_one_variable_per_published_port_and_exec_sets_the_same(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "one")
    up(run_wtenv, worktree, compose_projects)
    start = entry_of(worktree).block.start

    lines = section(worktree)

    # FR-013: the `ports` variables in `ports` order, then the port variables sorted by
    # (service, container port, protocol).
    assert [name for name, _ in lines] == NAMES
    assert dict(lines) == {
        "PORT": str(start),
        "BACKEND_PORT": str(start + 1),
        "WTENV_PORT_BACKEND_8000_TCP": str(start + 1),
        "WTENV_PORT_MAILPIT_1025_TCP": str(start + 2),
        "WTENV_PORT_MAILPIT_8025_TCP": str(start + 3),
    }
    seen = exec_env(run_wtenv, worktree)
    assert {name: seen.get(name) for name, _ in lines} == dict(lines)


# --- scenario 2: a port tied to a variable has both (001:FR-031) -------------------------------


def test_a_tied_port_has_its_rule_derived_variable_with_the_same_number(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "tied")
    up(run_wtenv, worktree, compose_projects)

    variables = dict(section(worktree))

    assert "WTENV_PORT_BACKEND_8000_TCP" in variables
    assert variables["WTENV_PORT_BACKEND_8000_TCP"] == variables["BACKEND_PORT"]


# --- scenario 3: two worktrees, the same names and different values (SC-002) -------------------


def test_two_worktrees_have_the_same_names_and_each_request_reaches_its_own_container(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    compose_image: str,
) -> None:
    first = make_worktree(repo, add_worktree, "one")
    second = make_worktree(repo, add_worktree, "two")
    for worktree in (first, second):
        up(run_wtenv, worktree, compose_projects)
    seen = [dict(section(worktree)) for worktree in (first, second)]

    assert set(seen[0]) == set(seen[1]) == set(NAMES)
    for name in NAMES:
        assert seen[0][name] != seen[1][name], name
    for worktree in (first, second):
        start_backend(worktree)
    ports = [int(variables["WTENV_PORT_BACKEND_8000_TCP"]) for variables in seen]
    assert [ping(port) for port in ports] == ["+PONG\r\n", "+PONG\r\n"]
    # Worktree 2's container is stopped: its port refuses, and worktree 1's still answers.
    stop_backend(second)
    assert refuses(ports[1])
    assert ping(ports[0]) == "+PONG\r\n"


# --- scenario 4: a repeat up changes nothing (001:FR-017) --------------------------------------


def test_a_repeat_up_leaves_the_env_file_byte_identical(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    # The same check, for the project name and the override, is in `test_us3_compose.py`.
    worktree = make_worktree(repo, add_worktree, "repeat")
    up(run_wtenv, worktree, compose_projects)
    first = (worktree / ENV_FILE).read_bytes()

    result = up(run_wtenv, worktree, compose_projects)

    assert (worktree / ENV_FILE).read_bytes() == first
    assert b"WTENV_PORT_BACKEND_8000_TCP" in first
    env_changes = [c for c in result.changes if c.item.kind is ItemKind.ENV_SECTION]
    assert [c.action for c in env_changes] == ["unchanged"]


# --- scenario 5: a port that is no longer published loses its variable -------------------------


def test_a_published_port_removed_from_the_compose_file_loses_its_variable(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "removed")
    up(run_wtenv, worktree, compose_projects)
    assert "WTENV_PORT_MAILPIT_1025_TCP" in dict(section(worktree))
    (worktree / "compose.yaml").write_text(
        STACK.replace('      - "1025:1025"\n', ""), encoding="utf-8"
    )

    up(run_wtenv, worktree, compose_projects)

    remaining = [name for name, _ in section(worktree)]
    assert remaining == [name for name in NAMES if name != "WTENV_PORT_MAILPIT_1025_TCP"]
    assert "WTENV_PORT_MAILPIT_1025_TCP" not in exec_env(run_wtenv, worktree)


# --- scenario 6: a name collision changes nothing (FR-012) -------------------------------------

# (case, wtenv.toml, stack, the variable, the two sources)
COLLISIONS = {
    "punctuation": (
        'block_size = 10\n\n[compose]\nfile = "compose.yaml"\n',
        colliding_stack("my_api", "my-api"),
        "WTENV_PORT_MY_API_6379_TCP",
        ["service my-api, container port 6379/tcp", "service my_api, container port 6379/tcp"],
    ),
    "one container port published twice": (
        'block_size = 10\n\n[compose]\nfile = "compose.yaml"\n',
        (
            f"services:\n  web:\n    image: {COMPOSE_IMAGE}\n    pull_policy: never\n"
            '    ports:\n      - "8001:6379"\n      - "8002:6379"\n'
        ),
        "WTENV_PORT_WEB_6379_TCP",
        ["service web, container port 6379/tcp", "service web, container port 6379/tcp"],
    ),
    "a ports entry": (
        (
            'ports = ["PORT", "WTENV_PORT_BACKEND_8000_TCP"]\nblock_size = 10\n\n'
            '[compose]\nfile = "compose.yaml"\n'
        ),
        STACK,
        "WTENV_PORT_BACKEND_8000_TCP",
        ["service backend, container port 8000/tcp", "wtenv.toml ports"],
    ),
}


@pytest.mark.parametrize("when", ["first_up", "repeat_up"])
@pytest.mark.parametrize("case", COLLISIONS)
def test_a_name_collision_changes_nothing(
    case: str,
    when: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    toml, stack, variable, sources = COLLISIONS[case]
    if when == "first_up":
        worktree = make_worktree(repo, add_worktree, "clash", toml=toml, stack=stack)
    else:
        worktree = make_worktree(repo, add_worktree, "clash")
        up(run_wtenv, worktree, compose_projects)
        (worktree / "wtenv.toml").write_text(toml, encoding="utf-8")
        (worktree / "compose.yaml").write_text(stack, encoding="utf-8")
    exclude = exclude_file(repo)
    before = (snapshot_tree(worktree), optional_bytes(registry_path()), optional_bytes(exclude))

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 22, process.stdout + process.stderr
    document = json.loads(process.stdout)
    assert document["error"]["code"] == ErrorCode.PORT_VARIABLE_CONFLICT.value
    assert document["error"]["details"] == {"variable": variable, "sources": sources}
    after = (snapshot_tree(worktree), optional_bytes(registry_path()), optional_bytes(exclude))
    assert after == before  # worktree tree, env file, override, registry and exclude block


# --- FR-016: without compose, nothing changes --------------------------------------------------


def test_without_compose_the_section_is_as_in_v0_1_1(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run
) -> None:
    worktree = add_worktree(repo, "plain", "branch-plain")
    (worktree / "wtenv.toml").write_text(
        (FIXTURES / "configs" / "us3_no_compose.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    process = run_wtenv(["up", "--json"], worktree)
    assert process.returncode == 0, process.stdout + process.stderr
    start = entry_of(worktree).block.start

    lines = section(worktree)

    assert lines == [("PORT", str(start)), ("CACHE_PORT", str(start + 1))]
    assert not any(name.startswith("WTENV_PORT_") for name, _ in lines)


# --- FR-028: an upgrade from v0.1.1 only adds lines --------------------------------------------


def test_a_section_written_by_v0_1_1_is_updated_and_the_only_new_lines_are_port_variables(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    compose_image: str,
) -> None:
    configs = FIXTURES / "configs"
    worktree = add_worktree(repo, "upgrade", "branch-upgrade")
    (worktree / "wtenv.toml").write_text(
        (configs / "us3_stack.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (worktree / "compose.yaml").write_text(
        (configs / "us3_stack.compose.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    commit_all(worktree)
    up(run_wtenv, worktree, compose_projects)
    start = entry_of(worktree).block.start
    # What v0.1.1 wrote (the env section of the T002 golden), with this block's numbers.
    golden = json.loads((FIXTURES / "golden" / "us3_stack.json").read_text(encoding="utf-8"))
    old = [
        line.split("=", 1)
        for line in golden["env_section"].splitlines()
        if "=" in line and not line.startswith("#")
    ]
    old_lines = [(name, str(start + int(value) - 20000)) for name, value in old]
    envfile_path = worktree / ENV_FILE
    text = envfile_path.read_text(encoding="utf-8")
    inside = "".join(f"{name}={value}\n" for name, value in old_lines)
    head, rest = text.split("PORT=", 1)
    tail = rest.split("# <<< wtenv managed <<<", 1)[1]
    envfile_path.write_text(head + inside + "# <<< wtenv managed <<<" + tail, encoding="utf-8")
    assert section(worktree) == old_lines

    result = up(run_wtenv, worktree, compose_projects)

    env_changes = [c for c in result.changes if c.item.kind is ItemKind.ENV_SECTION]
    assert [c.action for c in env_changes] == ["updated"]
    new_lines = section(worktree)
    assert [line for line in new_lines if line in old_lines] == old_lines
    added = [name for name, _ in new_lines if (name, _) not in old_lines]
    assert added and all(name.startswith("WTENV_PORT_") for name in added)


# --- edge case: a developer's line outside the section (001:FR-080) ----------------------------


def test_a_port_variable_set_outside_the_section_is_left_alone_with_a_warning(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "own")
    (worktree / ENV_FILE).write_bytes(b"WTENV_PORT_BACKEND_8000_TCP=3000\n")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stdout + process.stderr
    compose_projects.track(project_of(worktree))
    result = parse_up(process)
    assert [w.code for w in result.warnings] == [WarningCode.ENV_DUPLICATE_VARIABLE]
    assert "WTENV_PORT_BACKEND_8000_TCP" in result.warnings[0].message
    assert (worktree / ENV_FILE).read_bytes().startswith(b"WTENV_PORT_BACKEND_8000_TCP=3000\n")
    assert dict(section(worktree))["WTENV_PORT_BACKEND_8000_TCP"] != "3000"


# --- scenario 7: `up` and `ls` show each published port with its variable (FR-014) -------------

PUBLISHED = {
    ("backend", 8000): "WTENV_PORT_BACKEND_8000_TCP",
    ("mailpit", 1025): "WTENV_PORT_MAILPIT_1025_TCP",
    ("mailpit", 8025): "WTENV_PORT_MAILPIT_8025_TCP",
}


def test_up_shows_each_published_port_with_its_variable_in_text_and_json(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "shown")
    process = run_wtenv(["up"], worktree)
    assert process.returncode == 0, process.stdout + process.stderr
    compose_projects.track(project_of(worktree))
    start = entry_of(worktree).block.start

    published = [line for line in process.stdout.splitlines() if line.startswith("  published")]

    assert published == [
        (
            f"  published  backend:8000 -> {start + 1} (BACKEND_PORT, WTENV_PORT_BACKEND_8000_TCP)"
            f"  mailpit:1025 -> {start + 2} (WTENV_PORT_MAILPIT_1025_TCP)"
            f"  mailpit:8025 -> {start + 3} (WTENV_PORT_MAILPIT_8025_TCP)"
        )
    ]
    result = parse_up(run_wtenv(["up", "--json"], worktree))
    assert result.worktree is not None
    shown = {
        (port.service, port.target): port.port_variable
        for port in result.worktree.ports
        if port.service is not None
    }
    assert shown == PUBLISHED
    assert [p.port_variable for p in result.worktree.ports if p.service is None] == [None]


def test_ls_shows_each_published_port_with_its_variable_in_text_and_json(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, compose_projects: ComposeProjects
) -> None:
    worktree = make_worktree(repo, add_worktree, "listed")
    up(run_wtenv, worktree, compose_projects)
    start = entry_of(worktree).block.start

    text = run_wtenv(["ls"], worktree)
    document = parse_ls(run_wtenv(["ls", "--json"], worktree))

    assert text.returncode == 0, text.stderr
    assert f"backend:8000->{start + 1} (WTENV_PORT_BACKEND_8000_TCP)" in text.stdout
    assert f"mailpit:8025->{start + 3} (WTENV_PORT_MAILPIT_8025_TCP)" in text.stdout
    (view,) = [w for w in document.worktrees if w.path == str(worktree)]
    shown = {
        (port.service, port.target): port.port_variable
        for port in view.ports
        if port.service is not None
    }
    assert shown == PUBLISHED
    assert [p.port_variable for p in view.ports if p.service is None] == [None]
