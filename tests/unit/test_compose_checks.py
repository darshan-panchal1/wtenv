"""The compose limit checks of `up` (research.md §4, "v1 limits", and §11; FR-033, FR-035).
The checks take the command runner and the environment as parameters, so no Docker is needed."""

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from wtenv import compose
from wtenv.errors import ErrorCode, WtenvError

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
def test_any_of_the_four_override_names_that_is_not_recorded_is_an_ownership_conflict(
    tmp_path: Path, name: str
) -> None:
    (tmp_path / name).write_text("services: {}\n")

    with pytest.raises(WtenvError) as caught:
        compose.check_override_files(tmp_path, "compose.yaml", None)

    error = caught.value
    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details["kind"] == "compose_override"
    assert error.details["name"] == str(tmp_path / name)
    assert (tmp_path / name).read_text() == "services: {}\n"


def test_the_recorded_override_passes(tmp_path: Path) -> None:
    (tmp_path / "compose.override.yaml").write_text("# wtenv\n")

    compose.check_override_files(tmp_path, "compose.yaml", "compose.override.yaml")


def test_no_override_file_passes(tmp_path: Path) -> None:
    compose.check_override_files(tmp_path, "compose.yaml", None)
    compose.check_override_files(tmp_path, "compose.yaml", "compose.override.yaml")


def test_a_second_override_name_beside_the_recorded_one_is_a_conflict(tmp_path: Path) -> None:
    (tmp_path / "compose.override.yaml").write_text("# wtenv\n")
    (tmp_path / "compose.override.yml").write_text("# developer\n")

    with pytest.raises(WtenvError) as caught:
        compose.check_override_files(tmp_path, "compose.yaml", "compose.override.yaml")

    assert caught.value.details["name"] == str(tmp_path / "compose.override.yml")


def test_only_the_directory_of_the_compose_file_is_searched(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "compose.override.yaml").write_text("# elsewhere\n")

    compose.check_override_files(tmp_path, "deploy/compose.yaml", None)

    (tmp_path / "deploy" / "compose.override.yaml").write_text("# developer\n")
    with pytest.raises(WtenvError) as caught:
        compose.check_override_files(tmp_path, "deploy/compose.yaml", None)
    assert caught.value.details["name"] == str(tmp_path / "deploy" / "compose.override.yaml")


def test_an_override_recorded_beside_the_old_compose_file_does_not_cover_the_new_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "compose.override.yaml").write_text("# developer\n")

    with pytest.raises(WtenvError) as caught:
        compose.check_override_files(tmp_path, "deploy/compose.yaml", "compose.override.yaml")

    assert caught.value.code is ErrorCode.OWNERSHIP_CONFLICT


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
