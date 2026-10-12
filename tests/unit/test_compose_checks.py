"""The compose limit checks of `up` (research.md §4, "v1 limits", and §11; FR-033, FR-035).
The checks take the command runner and the environment as parameters, so no Docker is needed."""

import os
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from wtenv import compose, provision
from wtenv.config import load_config
from wtenv.errors import ErrorCode, WtenvError
from wtenv.identity import WorktreeIdentity

CONTEXT_COMMAND = ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
VERSION_COMMAND = ["docker", "compose", "version", "--short"]
ENGINE_COMMAND = ["docker", "info", "--format", "{{.ServerVersion}}"]


class FakeDocker:
    """A command runner that answers per command; a command with no answer is not installed."""

    def __init__(
        self,
        *,
        context: str | None = "unix:///var/run/docker.sock",
        compose_version: str | None = "2.24.4",
        engine_up: bool = True,
    ) -> None:
        self.answers: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {}
        if context is not None:
            self.answers[tuple(CONTEXT_COMMAND)] = _done(context)
        if compose_version is not None:
            self.answers[tuple(VERSION_COMMAND)] = _done(compose_version)
        self.answers[tuple(ENGINE_COMMAND)] = (
            _done("29.0.0") if engine_up else _done("", returncode=1)
        )
        self.calls: list[list[str]] = []

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        self.calls.append(list(command))
        answer = self.answers.get(tuple(command))
        if answer is None:
            raise FileNotFoundError(command[0])
        return answer


def _done(stdout: str, returncode: int = 0) -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess([], returncode, stdout + "\n", "")


def unavailable(runner: FakeDocker, environ: Mapping[str, str] | None = None) -> WtenvError:
    with pytest.raises(WtenvError) as caught:
        compose.check_docker(run=runner, environ=environ or {})
    assert caught.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.details["dependency"] == "docker"
    return caught.value


# --- the Docker endpoint (FR-035; research.md §11) ---------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "unix:///var/run/docker.sock",
        "tcp://localhost:2375",
        "tcp://127.0.0.1:2375",
        "tcp://[::1]:2375",
        "tcp://localhost",
    ],
)
def test_a_local_endpoint_from_the_context_passes(host: str) -> None:
    compose.check_docker(run=FakeDocker(context=host), environ={})


def test_docker_host_in_the_environment_is_used_instead_of_the_context() -> None:
    runner = FakeDocker(context="tcp://192.0.2.1:2375")

    compose.check_docker(run=runner, environ={"DOCKER_HOST": "unix:///run/user/1000/docker.sock"})

    assert CONTEXT_COMMAND not in runner.calls


@pytest.mark.parametrize(
    "host",
    [
        "tcp://192.0.2.1:2375",
        "tcp://example.com:2376",
        "ssh://user@remote",
        "tcp://localhost.evil.test:2375",
        "tcp://127.0.0.1.evil.test:2375",
        "npipe:////./pipe/docker_engine",
    ],
)
def test_a_remote_endpoint_in_docker_host_is_not_local(host: str) -> None:
    runner = FakeDocker()

    error = unavailable(runner, {"DOCKER_HOST": host})

    assert error.details["reason"] == "not_local"
    # The endpoint is judged first and the engine is never contacted.
    assert runner.calls == []


def test_a_remote_endpoint_from_the_context_is_not_local() -> None:
    runner = FakeDocker(context="tcp://192.0.2.1:2375")

    error = unavailable(runner)

    assert error.details["reason"] == "not_local"
    assert ENGINE_COMMAND not in runner.calls


def test_an_empty_docker_host_counts_as_unset() -> None:
    runner = FakeDocker()

    compose.check_docker(run=runner, environ={"DOCKER_HOST": ""})

    assert CONTEXT_COMMAND in runner.calls


# --- Docker and Compose are there (research.md §4) ---------------------------------------------


def test_docker_missing_is_not_installed() -> None:
    error = unavailable(FakeDocker(context=None, compose_version=None))

    assert error.details["reason"] == "not_installed"


def test_docker_without_the_compose_plugin_is_not_installed() -> None:
    runner = FakeDocker(compose_version=None)

    assert unavailable(runner).details["reason"] == "not_installed"


def test_a_compose_command_that_fails_is_not_installed() -> None:
    runner = FakeDocker()
    runner.answers[tuple(VERSION_COMMAND)] = _done("", returncode=1)

    assert unavailable(runner).details["reason"] == "not_installed"


def test_an_engine_that_does_not_answer_is_not_running() -> None:
    error = unavailable(FakeDocker(engine_up=False))

    assert error.details["reason"] == "not_running"


def _engine_answers(stdout: str, returncode: int) -> "subprocess.CompletedProcess[str]":
    """What `docker info --format {{.ServerVersion}}` printed, byte for byte."""
    return subprocess.CompletedProcess([], returncode, stdout, "")


# Docker CLI 26.x to 28.0.x exits 0 with empty stdout when the daemon is dead (T229).
@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [("", 0), ("  \n\t\n", 0), ("", 1)],
    ids=["exit_0_empty_stdout", "exit_0_whitespace_only", "exit_1"],
)
def test_an_engine_answer_without_a_server_version_is_not_running(
    stdout: str, returncode: int
) -> None:
    runner = FakeDocker()
    runner.answers[tuple(ENGINE_COMMAND)] = _engine_answers(stdout, returncode)

    error = unavailable(runner)

    assert error.details["reason"] == "not_running"


def test_an_engine_that_exits_0_with_a_server_version_passes() -> None:
    runner = FakeDocker()
    runner.answers[tuple(ENGINE_COMMAND)] = _engine_answers("29.0.0\n", 0)

    compose.check_docker(run=runner, environ={})


def test_up_reports_a_docker_that_exits_0_with_no_server_version_as_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `up` calls `check_docker()` with the real runner, so a stand-in `docker` goes first on PATH.
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "docker"
    shim.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  context) echo unix:///var/run/docker.sock;;\n"
        "  compose) echo 2.30.0;;\n"
        "  info) ;;\n"  # exit 0, nothing on stdout
        "  *) exit 1;;\n"
        "esac\n",
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    root = tmp_path / "feature-x"
    root.mkdir()
    (root / "wtenv.toml").write_text('[compose]\nfile = "compose.yaml"\n', encoding="utf-8")
    where = WorktreeIdentity(
        git_dir="/repos/app/.git/worktrees/feature-x", path=str(root), repository="/repos/app/.git"
    )

    with pytest.raises(WtenvError) as caught:
        provision._compose_plan(root, where, load_config(root), None)

    assert caught.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.details["dependency"] == "docker"
    assert caught.value.details["reason"] == "not_running"
    # Not the later "cannot list the project's resources": `check_docker` itself must refuse.
    assert "does not answer" in caught.value.message


@pytest.mark.parametrize("found", ["2.24.3", "2.23.9", "2.0.0", "1.29.2", "v2.24.3"])
def test_compose_older_than_2_24_4_is_too_old(found: str) -> None:
    error = unavailable(FakeDocker(compose_version=found))

    assert error.details["reason"] == "too_old"
    assert error.details["required"] == "2.24.4"
    assert error.details["found"] == found.removeprefix("v")


@pytest.mark.parametrize(
    "found", ["2.24.4", "2.24.5", "2.25.0", "5.1.4", "v2.30.1", "2.24.4-desktop.1"]
)
def test_compose_2_24_4_or_later_passes(found: str) -> None:
    compose.check_docker(run=FakeDocker(compose_version=found), environ={})


def test_a_version_that_cannot_be_read_is_not_installed() -> None:
    assert unavailable(FakeDocker(compose_version="dev")).details["reason"] == "not_installed"


# --- an override file that is not wtenv's (FR-033; research.md §4) -----------------------------


@pytest.mark.parametrize("name", list(compose.OVERRIDE_FILE_NAMES))
def test_any_of_the_four_override_names_that_is_not_recorded_is_the_repository_override(
    tmp_path: Path, name: str
) -> None:
    """Was `..._is_an_ownership_conflict`: v0.1.x refused the file; now it selects exec-only (FR-001)."""
    (tmp_path / name).write_text("services: {}\n")

    inventory = compose.override_inventory(tmp_path, "compose.yaml", None)

    assert inventory.arrangement == "exec_only"
    assert inventory.repository_override == name
    assert inventory.remove == ()
    assert (tmp_path / name).read_text() == "services: {}\n"


def test_the_recorded_override_passes(tmp_path: Path) -> None:
    """Calls `override_inventory` since T033 removed `check_override_files`; same outcome.

    No refusal, and wtenv's own recorded file is not the repository's, so the arrangement is
    automatic. The file now carries the real header line: the old fixture, `# wtenv`, was not
    wtenv's header, which the inventory (like 001:FR-087) does not accept as wtenv's.
    """
    (tmp_path / "compose.override.yaml").write_text(OWN_HEADER)

    inventory = compose.override_inventory(tmp_path, "compose.yaml", "compose.override.yaml")

    assert inventory.arrangement == "automatic"
    assert inventory.repository_override is None
    assert inventory.remove == ()


def test_no_override_file_passes(tmp_path: Path) -> None:
    """Calls `override_inventory` since T033 removed `check_override_files`; same outcome."""
    for recorded in (None, "compose.override.yaml"):
        inventory = compose.override_inventory(tmp_path, "compose.yaml", recorded)

        assert inventory.files == ()
        assert inventory.arrangement == "automatic"


def test_a_second_override_name_beside_the_recorded_one_is_honoured_and_wtenvs_own_is_removed(
    tmp_path: Path,
) -> None:
    """Was `test_a_second_override_name_beside_the_recorded_one_is_a_conflict` (FR-007)."""
    wtenvs = tmp_path / "compose.override.yaml"
    developers = tmp_path / "compose.override.yml"
    wtenvs.write_text(OWN_HEADER + 'name: "old"\n')
    developers.write_text("# developer\n")

    inventory = compose.override_inventory(tmp_path, "compose.yaml", "compose.override.yaml")

    assert inventory.arrangement == "exec_only"
    assert inventory.repository_override == "compose.override.yml"
    assert inventory.remove == ("compose.override.yaml",)
    assert developers.read_text() == "# developer\n"
    assert wtenvs.read_text() == OWN_HEADER + 'name: "old"\n'  # marked, not removed here


def test_a_root_override_beside_a_nested_compose_file_is_not_counted_but_one_beside_it_is(
    tmp_path: Path,
) -> None:
    """Was `test_only_the_directory_of_the_compose_file_is_searched` (FR-001)."""
    (tmp_path / "deploy").mkdir()
    (tmp_path / "compose.override.yaml").write_text("# elsewhere\n")

    elsewhere = compose.override_inventory(tmp_path, "deploy/compose.yaml", None)

    assert elsewhere.arrangement == "automatic"
    assert elsewhere.repository_override is None

    (tmp_path / "deploy" / "compose.override.yaml").write_text("# developer\n")
    beside = compose.override_inventory(tmp_path, "deploy/compose.yaml", None)

    assert beside.arrangement == "exec_only"
    assert beside.repository_override == "deploy/compose.override.yaml"


def test_an_override_recorded_beside_the_old_compose_file_is_not_counted_in_the_new_directory(
    tmp_path: Path,
) -> None:
    """Was `test_an_override_recorded_beside_the_old_compose_file_does_not_cover_the_new_directory`.

    The file in `deploy/` is the repository's override (FR-001). The root file that the record
    names is beside the old compose file, so it is not counted and not marked for removal here
    (FR-007; the move itself is the path-change branch of step 11).
    """
    (tmp_path / "deploy").mkdir()
    (tmp_path / "compose.override.yaml").write_text(OWN_HEADER)
    (tmp_path / "deploy" / "compose.override.yaml").write_text("# developer\n")

    inventory = compose.override_inventory(tmp_path, "deploy/compose.yaml", "compose.override.yaml")

    assert inventory.arrangement == "exec_only"
    assert inventory.repository_override == "deploy/compose.override.yaml"
    assert inventory.remove == ()


# --- a recorded override that is not wtenv's is not rewritten (FR-087; T212) --------------------

OWN_HEADER = (
    "# Generated by wtenv for this worktree. Do not edit or commit; `wtenv up` rewrites it.\n"
)


@pytest.mark.parametrize(
    "text",
    [
        "services: {}\n",
        "",
        OWN_HEADER.replace("Generated", "Written"),
        "\n" + OWN_HEADER,
        OWN_HEADER.rstrip("\n"),
    ],
    ids=["developer content", "empty", "altered header", "header not first", "no newline"],
)
def test_a_recorded_override_without_the_header_line_is_an_ownership_conflict(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "compose.override.yaml"
    path.write_bytes(text.encode())

    with pytest.raises(WtenvError) as caught:
        compose.check_recorded_override(tmp_path, "compose.override.yaml")

    error = caught.value
    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "compose_override", "name": str(path)}
    assert str(path) in error.message
    assert path.read_bytes() == text.encode()


def test_a_stale_override_that_keeps_the_header_passes(tmp_path: Path) -> None:
    (tmp_path / "compose.override.yaml").write_text(OWN_HEADER + 'name: "old"\n')

    compose.check_recorded_override(tmp_path, "compose.override.yaml")


def test_a_recorded_override_that_is_missing_passes(tmp_path: Path) -> None:
    compose.check_recorded_override(tmp_path, "compose.override.yaml")


# --- the isolation file's path is accepted exactly (FR-009; plan.md S4; analysis O1) -----------

ISOLATION_FILE = ".wtenv/compose.isolation.yaml"


def test_a_missing_isolation_file_has_no_problem(tmp_path: Path) -> None:
    assert compose.override_problem(tmp_path, ISOLATION_FILE) is None
    compose.check_recorded_override(tmp_path, ISOLATION_FILE)


def test_an_isolation_file_with_the_header_has_no_problem(tmp_path: Path) -> None:
    (tmp_path / ".wtenv").mkdir()
    (tmp_path / ISOLATION_FILE).write_text(OWN_HEADER + 'name: "old"\n')

    assert compose.override_problem(tmp_path, ISOLATION_FILE) is None
    compose.check_recorded_override(tmp_path, ISOLATION_FILE)


@pytest.mark.parametrize("text", ["services: {}\n", "", OWN_HEADER.replace("Generated", "Written")])
def test_an_isolation_file_without_the_header_is_a_problem_and_an_ownership_conflict(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / ISOLATION_FILE
    path.parent.mkdir()
    path.write_bytes(text.encode())

    assert compose.override_problem(tmp_path, ISOLATION_FILE) is not None
    with pytest.raises(WtenvError) as caught:
        compose.check_recorded_override(tmp_path, ISOLATION_FILE)

    error = caught.value
    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "compose_override", "name": str(path)}
    assert path.read_bytes() == text.encode()


@pytest.mark.parametrize(
    "relative",
    [
        ".wtenv/notes.yaml",
        ".wtenv/sub/compose.isolation.yaml",
        "../.wtenv/compose.isolation.yaml",
        "compose.isolation.yaml",
    ],
)
@pytest.mark.parametrize(
    "contents", [None, "services: {}\n", OWN_HEADER], ids=["missing", "plain", "header"]
)
def test_any_other_path_under_or_near_wtenv_is_a_problem_whatever_it_holds(
    tmp_path: Path, relative: str, contents: str | None
) -> None:
    root = tmp_path / "root"
    (root / ".wtenv" / "sub").mkdir(parents=True)
    path = root / relative
    if contents is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    assert compose.override_problem(root, relative) is not None
    with pytest.raises(WtenvError) as caught:
        compose.check_recorded_override(root, relative)

    assert caught.value.code is ErrorCode.OWNERSHIP_CONFLICT
    assert (path.read_text() if contents is not None else None) == contents  # left alone


@pytest.mark.parametrize("name", list(compose.OVERRIDE_FILE_NAMES))
def test_the_four_override_names_keep_their_v0_1_1_cases(tmp_path: Path, name: str) -> None:
    assert compose.override_problem(tmp_path, name) is None  # missing: already gone
    (tmp_path / name).write_text("services: {}\n")
    assert compose.override_problem(tmp_path, name) is not None  # not wtenv's
    (tmp_path / name).write_text(OWN_HEADER)
    assert compose.override_problem(tmp_path, name) is None
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / name).write_text(OWN_HEADER)
    assert compose.override_problem(tmp_path, f"deploy/{name}") is None


# --- COMPOSE_PROJECT_NAME and COMPOSE_FILE (research.md §4, "v1 limits") -----------------------


def env_override(root: Path, environ: Mapping[str, str]) -> WtenvError:
    with pytest.raises(WtenvError) as caught:
        compose.check_environment(root, "compose.yaml", environ)
    assert caught.value.code is ErrorCode.UNSUPPORTED
    assert caught.value.details["reason"] == "compose_env_override"
    return caught.value


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
def test_the_variable_in_the_environment_is_unsupported(tmp_path: Path, variable: str) -> None:
    error = env_override(tmp_path, {variable: "x"})

    assert variable in error.message
    assert "file" not in error.details


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
@pytest.mark.parametrize("line", ["{}=x", "export {}=x", '{}="x"', "  {}=x", "{} = x"])
def test_the_variable_in_dot_env_beside_the_compose_file_is_unsupported(
    tmp_path: Path, variable: str, line: str
) -> None:
    (tmp_path / ".env").write_text("A=1\n" + line.format(variable) + "\nB=2\n")

    error = env_override(tmp_path, {})

    assert error.details["file"] == str(tmp_path / ".env")
    assert variable in error.message


def test_dot_env_is_looked_for_beside_the_compose_file(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / ".env").write_text("COMPOSE_FILE=x\n")  # not beside the compose file
    compose.check_environment(tmp_path, "deploy/compose.yaml", {})

    (tmp_path / "deploy" / ".env").write_text("COMPOSE_PROJECT_NAME=x\n")
    with pytest.raises(WtenvError) as caught:
        compose.check_environment(tmp_path, "deploy/compose.yaml", {})
    assert caught.value.details["file"] == str(tmp_path / "deploy" / ".env")


def test_other_variables_comments_and_look_alikes_pass(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "# COMPOSE_FILE=x\nCOMPOSE_FILE_EXTRA=1\nMY_COMPOSE_PROJECT_NAME=x\nCOMPOSE_PROFILES=a\n"
    )

    compose.check_environment(tmp_path, "compose.yaml", {"COMPOSE_PROFILES": "a", "PATH": "/bin"})


def test_an_empty_value_is_not_an_override(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("COMPOSE_FILE=\n")

    compose.check_environment(tmp_path, "compose.yaml", {"COMPOSE_PROJECT_NAME": ""})


def test_no_dot_env_and_a_clean_environment_pass(tmp_path: Path) -> None:
    compose.check_environment(tmp_path, "compose.yaml", {})


def test_the_environment_is_reported_before_dot_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("COMPOSE_FILE=x\n")

    error = env_override(tmp_path, {"COMPOSE_PROJECT_NAME": "x"})

    assert "file" not in error.details


# --- a project name that already has resources is not wtenv's (T164, T165; LOW-4) ----------------

PROJECT_NAME = "wtenv-app-91c2d0aa"


class ProjectListing:
    """A runner that answers the three label listings of `docker`, one line per resource."""

    def __init__(self, *, containers: Sequence[str] = (), fail: bool = False) -> None:
        self.containers = list(containers)
        self.fail = fail
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        words = list(command)
        self.calls.append((words, dict(environ)))
        assert f"label=com.docker.compose.project={PROJECT_NAME}" in words
        if self.fail:
            return _done("", returncode=1)
        lines = self.containers if words[:2] == ["docker", "ps"] else []
        return _done("".join(f"{line}\n" for line in lines))


def test_a_project_with_no_resources_is_unused() -> None:
    compose.check_project_is_unused(PROJECT_NAME, run=ProjectListing(), environ={})


@pytest.mark.parametrize("found", ["abc123 app-web-1"])
def test_a_project_with_a_container_is_an_ownership_conflict_naming_the_project(
    found: str,
) -> None:
    with pytest.raises(WtenvError) as raised:
        compose.check_project_is_unused(
            PROJECT_NAME, run=ProjectListing(containers=[found]), environ={}
        )

    assert raised.value.code is ErrorCode.OWNERSHIP_CONFLICT
    assert raised.value.details == {"kind": "compose_project", "name": PROJECT_NAME}


def test_the_listing_does_not_pass_compose_variables_on() -> None:
    runner = ProjectListing()

    compose.check_project_is_unused(
        PROJECT_NAME, run=runner, environ={"COMPOSE_PROJECT_NAME": "other", "PATH": "/bin"}
    )

    assert runner.calls and all(env == {"PATH": "/bin"} for _, env in runner.calls)


def test_a_listing_that_fails_is_dependency_unavailable_and_not_a_free_name() -> None:
    with pytest.raises(WtenvError) as raised:
        compose.check_project_is_unused(PROJECT_NAME, run=ProjectListing(fail=True), environ={})

    assert raised.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert raised.value.details["dependency"] == "docker"


# --- a ':' in a path COMPOSE_FILE would have to carry (research R2; FR-003) ---------------------

COMPOSE = Path("/w/app/compose.yaml")
REPOSITORY_OVERRIDE = Path("/w/app/compose.override.yml")
ISOLATION = Path("/w/app/.wtenv/compose.isolation.yaml")


def path_separator(files: Sequence[Path]) -> WtenvError:
    with pytest.raises(WtenvError) as caught:
        compose.check_path_separator(files)
    return caught.value


def test_paths_without_a_colon_pass() -> None:
    compose.check_path_separator([COMPOSE, REPOSITORY_OVERRIDE, ISOLATION])


@pytest.mark.parametrize(
    ("position", "bad"),
    [
        (0, Path("/w:x/app/compose.yaml")),
        (1, Path("/w/app/compose.override:v2.yml")),
        (2, Path("/w/app:1/.wtenv/compose.isolation.yaml")),
    ],
    ids=["compose file", "repository override", "isolation file"],
)
def test_an_absolute_path_with_a_colon_is_unsupported_and_names_the_path(
    position: int, bad: Path
) -> None:
    files = [COMPOSE, REPOSITORY_OVERRIDE, ISOLATION]
    files[position] = bad

    error = path_separator(files)

    assert error.code is ErrorCode.UNSUPPORTED
    assert error.details == {"reason": "compose_path_separator", "file": str(bad)}
    assert str(bad) in error.message


def test_the_first_path_with_a_colon_is_the_one_named() -> None:
    first, second = Path("/a:b/compose.yaml"), Path("/c:d/compose.override.yml")

    assert path_separator([COMPOSE, first, second]).details["file"] == str(first)
