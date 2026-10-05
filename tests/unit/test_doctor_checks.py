"""The checks of `wtenv doctor` (T124; FR-060, FR-061; cli.md, `wtenv doctor`).

Every check takes what it needs as a parameter: the entries, the classification, the command
runner for `lsof` and `docker`, the free-port test, and the Postgres lookups. So no process, no
Docker, and no git runs here, and no real port is bound.
"""

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from wtenv import doctor
from wtenv.config import Config
from wtenv.database import PostgresTarget
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.orphans import Classification
from wtenv.output import (
    DependencyStatus,
    DoctorResult,
    Finding,
    FindingCode,
    ResourceState,
    Status,
    UnverifiableReason,
)
from wtenv.registry import (
    ComposeRecord,
    DatabaseRecord,
    EnvFileRecord,
    PortBlock,
    PublishedPort,
    VariablePort,
    WorktreeEntry,
)

ENVIRON: Mapping[str, str] = {"PATH": "/usr/bin"}
SECTION = b"# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>\nPORT=20000\n"
SECTION += b"# <<< wtenv managed <<<\n"


# --- helpers ------------------------------------------------------------------------------------


def make_entry(
    path: Path,
    *,
    start: int = 20000,
    size: int = 10,
    state: str = "provisioned",
    ports: Sequence[tuple[str, int]] = (("PORT", 20000),),
    published: Sequence[PublishedPort] = (),
    env_file: EnvFileRecord | None = None,
    databases: Sequence[DatabaseRecord] = (),
    compose: ComposeRecord | None = None,
) -> WorktreeEntry:
    """Return an entry recorded at `path`; the git directory is made up from the path."""
    return WorktreeEntry(
        git_dir=f"{path}.git/worktrees/{path.name}",
        path=str(path),
        repository=f"{path}.git",
        state="provisioned" if state == "provisioned" else "incomplete",
        block=PortBlock(start=start, size=size),
        ports=[VariablePort(variable=name, port=port) for name, port in ports],
        published=list(published),
        env_file=env_file,
        databases=list(databases),
        compose=compose,
    )


def env_record(state: ResourceState = ResourceState.CREATED) -> EnvFileRecord:
    return EnvFileRecord(path=".env.local", created_file=True, added_newline=False, state=state)


def findings_of(found: Sequence[Finding]) -> list[tuple[FindingCode, str]]:
    return [(finding.code, finding.severity) for finding in found]


def process(stdout: str = "", returncode: int = 0) -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class FakeRunner:
    """A command runner that answers `lsof` and `docker` from a script and records every call.

    `listener` is `(pid, command name, working directory)` of the process listening on `port`,
    and `lsof` is not installed when `lsof_missing`. `containers` is what `docker ps` prints as
    published ports, one line per container; `docker_missing` makes `docker` not installed.
    """

    def __init__(
        self,
        *,
        port: int | None = None,
        listener: tuple[int, str, str | None] | None = None,
        lsof_missing: bool = False,
        containers: Sequence[str] = (),
        docker_missing: bool = False,
    ) -> None:
        self.port = port
        self.listener = listener
        self.lsof_missing = lsof_missing
        self.containers = list(containers)
        self.docker_missing = docker_missing
        self.calls: list[list[str]] = []

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        words = list(command)
        self.calls.append(words)
        if words[0] == "lsof":
            return self._lsof(words)
        if words[0] == "docker":
            if self.docker_missing:
                raise FileNotFoundError("docker")
            return process("\n".join(self.containers) + "\n" if self.containers else "")
        raise AssertionError(f"unexpected command {words}")

    def _lsof(self, words: list[str]) -> "subprocess.CompletedProcess[str]":
        if self.lsof_missing:
            raise FileNotFoundError("lsof")
        if self.listener is None:
            return process("", returncode=1)  # lsof prints nothing and exits 1: no such listener
        pid, name, directory = self.listener
        if any(word.startswith("-iTCP:") for word in words):
            assert f"-iTCP:{self.port}" in words, f"asked about another port: {words}"
            return process(f"p{pid}\nc{name}\n")
        assert str(pid) in words
        if directory is None:
            return process("", returncode=1)
        return process(f"p{pid}\nfcwd\nn{directory}\n")

    def called(self, program: str) -> bool:
        return any(call[0] == program for call in self.calls)


def busy(*ports: int) -> Callable[[int], bool]:
    """A free-port test under which only `ports` are in use."""
    return lambda port: port not in ports


def checked(
    entry: WorktreeEntry, runner: FakeRunner, is_free: Callable[[int], bool]
) -> list[Finding]:
    return doctor.port_findings(entry, run=runner, environ=ENVIRON, is_free=is_free)


# --- block_overlap (FR-060a) -------------------------------------------------------------------


def test_blocks_that_share_a_port_are_a_block_overlap(tmp_path: Path) -> None:
    first = make_entry(tmp_path / "a", start=20000, size=10)
    second = make_entry(tmp_path / "b", start=20005, size=5)

    found = doctor.block_overlaps([first, second])

    assert findings_of(found) == [(FindingCode.BLOCK_OVERLAP, "problem")]
    assert found[0].details == {
        "worktrees": sorted([first.path, second.path]),
        "ports": "20005-20009",
    }


def test_blocks_next_to_each_other_do_not_overlap(tmp_path: Path) -> None:
    first = make_entry(tmp_path / "a", start=20000, size=10)
    second = make_entry(tmp_path / "b", start=20010, size=10)

    assert doctor.block_overlaps([first, second]) == []


def test_three_blocks_on_one_port_give_one_finding_per_pair(tmp_path: Path) -> None:
    entries = [make_entry(tmp_path / name, start=20000, size=10) for name in "abc"]

    assert len(doctor.block_overlaps(entries)) == 3


# --- orphaned, unverifiable, and incomplete entries (FR-060c, d; FR-054) ------------------------


def test_a_provisioned_entry_has_no_status_finding(tmp_path: Path) -> None:
    entry = make_entry(tmp_path / "a")

    assert doctor.status_finding(entry, Classification(Status.PROVISIONED)) is None


def test_an_orphaned_entry_is_an_orphaned_worktree_problem(tmp_path: Path) -> None:
    entry = make_entry(tmp_path / "a")

    finding = doctor.status_finding(entry, Classification(Status.ORPHANED))

    assert finding is not None
    assert (finding.code, finding.severity) == (FindingCode.ORPHANED_WORKTREE, "problem")
    assert finding.worktree == entry.path


@pytest.mark.parametrize("reason", list(UnverifiableReason))
def test_an_unverifiable_entry_names_its_reason(tmp_path: Path, reason: UnverifiableReason) -> None:
    entry = make_entry(tmp_path / "a")

    finding = doctor.status_finding(entry, Classification(Status.UNVERIFIABLE, reason))

    assert finding is not None
    assert (finding.code, finding.severity) == (FindingCode.UNVERIFIABLE_WORKTREE, "problem")
    assert finding.details["reason"] == reason.value
    assert finding.worktree == entry.path


def test_a_moved_entry_names_where_the_worktree_is_now(tmp_path: Path) -> None:
    entry = make_entry(tmp_path / "a")
    found = Classification(Status.UNVERIFIABLE, UnverifiableReason.MOVED, str(tmp_path / "b"))

    finding = doctor.status_finding(entry, found)

    assert finding is not None
    assert finding.details == {"reason": "moved", "current_path": str(tmp_path / "b")}


def test_an_incomplete_entry_is_an_incomplete_worktree_problem(tmp_path: Path) -> None:
    entry = make_entry(tmp_path / "a", state="incomplete")

    finding = doctor.status_finding(entry, Classification(Status.INCOMPLETE))

    assert finding is not None
    assert (finding.code, finding.severity) == (FindingCode.INCOMPLETE_WORKTREE, "problem")


# --- missing_resource (FR-060c) ----------------------------------------------------------------


def never_asked(entry: WorktreeEntry, record: DatabaseRecord) -> bool:
    raise AssertionError("the Postgres server must not be asked")


def worktree_dir(tmp_path: Path) -> Path:
    path = tmp_path / "app"
    path.mkdir()
    return path


def test_a_recorded_env_file_that_is_gone_is_a_missing_resource(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    entry = make_entry(root, env_file=env_record())

    found = doctor.missing_resources(entry, postgres_has=never_asked)

    assert findings_of(found) == [(FindingCode.MISSING_RESOURCE, "problem")]
    assert found[0].worktree == entry.path
    assert found[0].details == {"kind": "env_file", "name": str(root / ".env.local")}


def test_an_env_file_without_the_section_is_a_missing_env_section(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    (root / ".env.local").write_bytes(b"OTHER=1\n")
    entry = make_entry(root, env_file=env_record())

    found = doctor.missing_resources(entry, postgres_has=never_asked)

    assert [finding.details["kind"] for finding in found] == ["env_section"]


def test_an_env_file_with_the_section_is_not_missing_anything(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    (root / ".env.local").write_bytes(b"OTHER=1\n" + SECTION)
    entry = make_entry(root, env_file=env_record())

    assert doctor.missing_resources(entry, postgres_has=never_asked) == []


def test_an_env_file_with_damaged_markers_is_not_reported_as_missing(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    (root / ".env.local").write_bytes(SECTION.splitlines(keepends=True)[0] + b"PORT=20000\n")
    entry = make_entry(root, env_file=env_record())

    assert doctor.missing_resources(entry, postgres_has=never_asked) == []


@pytest.mark.parametrize("state", [ResourceState.CREATING, ResourceState.REMOVING])
def test_a_resource_that_is_not_created_yet_or_is_being_removed_is_not_checked(
    tmp_path: Path, state: ResourceState
) -> None:
    root = worktree_dir(tmp_path)
    entry = make_entry(root, env_file=env_record(state))

    assert doctor.missing_resources(entry, postgres_has=never_asked) == []


def test_a_recorded_sqlite_copy_that_is_gone_is_a_missing_resource(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    copy = DatabaseRecord(kind="sqlite", path=".wtenv/dev.sqlite3", state=ResourceState.CREATED)
    entry = make_entry(root, databases=[copy])

    found = doctor.missing_resources(entry, postgres_has=never_asked)

    assert found[0].code is FindingCode.MISSING_RESOURCE
    assert found[0].details == {"kind": "sqlite_file", "name": str(root / ".wtenv/dev.sqlite3")}


def test_a_sqlite_copy_that_is_there_is_not_missing(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    (root / ".wtenv").mkdir()
    (root / ".wtenv" / "dev.sqlite3").write_bytes(b"")
    copy = DatabaseRecord(kind="sqlite", path=".wtenv/dev.sqlite3", state=ResourceState.CREATED)

    assert (
        doctor.missing_resources(make_entry(root, databases=[copy]), postgres_has=never_asked) == []
    )


def test_a_recorded_override_that_is_gone_is_a_missing_resource(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    compose = ComposeRecord(
        project="app_aa11bb22",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )

    found = doctor.missing_resources(make_entry(root, compose=compose), postgres_has=never_asked)

    assert found[0].details == {
        "kind": "compose_override",
        "name": str(root / "compose.override.yaml"),
    }


def postgres_record() -> DatabaseRecord:
    return DatabaseRecord(
        kind="postgres",
        name="wtenv_app_aa11bb22",
        host="127.0.0.1",
        port=5432,
        user="dev",
        state=ResourceState.CREATED,
    )


def test_a_postgres_database_the_server_does_not_have_is_a_missing_resource(
    tmp_path: Path,
) -> None:
    entry = make_entry(worktree_dir(tmp_path), databases=[postgres_record()])
    asked: list[DatabaseRecord] = []

    def lookup(entry: WorktreeEntry, record: DatabaseRecord) -> bool:
        asked.append(record)
        return False

    found = doctor.missing_resources(entry, postgres_has=lookup)

    assert asked == [postgres_record()]
    assert found[0].code is FindingCode.MISSING_RESOURCE
    assert found[0].details == {"kind": "postgres_database", "name": "wtenv_app_aa11bb22"}


def test_a_postgres_database_the_server_has_is_not_missing(tmp_path: Path) -> None:
    entry = make_entry(worktree_dir(tmp_path), databases=[postgres_record()])

    assert doctor.missing_resources(entry, postgres_has=lambda entry, record: True) == []


def test_a_server_that_cannot_be_reached_skips_the_check_without_a_problem(
    tmp_path: Path,
) -> None:
    entry = make_entry(worktree_dir(tmp_path), databases=[postgres_record()])

    def unreachable(entry: WorktreeEntry, record: DatabaseRecord) -> bool:
        raise WtenvError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "cannot connect to the Postgres server at 127.0.0.1:5432",
            details={"dependency": "postgres", "reason": "cannot_connect"},
        )

    found = doctor.missing_resources(entry, postgres_has=unreachable)

    assert findings_of(found) == [(FindingCode.RESOURCE_CHECK_SKIPPED, "info")]
    assert found[0].worktree == entry.path
    assert found[0].details["name"] == "wtenv_app_aa11bb22"
    assert found[0].details["reason"] == "cannot_connect"


def test_an_unreachable_server_is_asked_only_once(tmp_path: Path) -> None:
    asked = []

    def unreachable(entry: WorktreeEntry, record: DatabaseRecord) -> bool:
        asked.append(record)
        raise WtenvError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "cannot connect",
            details={"dependency": "postgres", "reason": "cannot_connect"},
        )

    lookup = doctor.remember_unreachable(unreachable)
    entries = [make_entry(tmp_path / name, databases=[postgres_record()]) for name in "ab"]

    found = [f for entry in entries for f in doctor.missing_resources(entry, postgres_has=lookup)]

    assert len(asked) == 1
    assert [f.code for f in found] == [FindingCode.RESOURCE_CHECK_SKIPPED] * 2


# --- ports (FR-060b; research.md section 11) ---------------------------------------------------


def test_ports_that_are_free_need_no_command(tmp_path: Path) -> None:
    runner = FakeRunner()

    assert checked(make_entry(tmp_path / "app"), runner, busy()) == []
    assert runner.calls == []


def test_a_busy_port_whose_listener_works_inside_the_worktree_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    root = worktree_dir(tmp_path)
    runner = FakeRunner(port=20000, listener=(4242, "node", str(root / "web")))

    assert checked(make_entry(root), runner, busy(20000)) == []


def test_a_busy_port_whose_listener_works_elsewhere_is_a_port_conflict(tmp_path: Path) -> None:
    root = worktree_dir(tmp_path)
    runner = FakeRunner(port=20000, listener=(4242, "node", str(tmp_path / "elsewhere")))

    found = checked(make_entry(root), runner, busy(20000))

    assert findings_of(found) == [(FindingCode.PORT_CONFLICT, "problem")]
    assert found[0].worktree == str(root)
    assert found[0].details == {
        "port": 20000,
        "pid": 4242,
        "command": "node",
        "directory": str(tmp_path / "elsewhere"),
    }


def test_a_directory_that_only_starts_with_the_worktree_path_is_outside_it(
    tmp_path: Path,
) -> None:
    root = worktree_dir(tmp_path)
    runner = FakeRunner(port=20000, listener=(4242, "node", f"{root}-other"))

    found = checked(make_entry(root), runner, busy(20000))

    assert [finding.code for finding in found] == [FindingCode.PORT_CONFLICT]


def test_a_busy_port_without_lsof_is_in_use_and_not_a_conflict(tmp_path: Path) -> None:
    runner = FakeRunner(lsof_missing=True)

    found = checked(make_entry(worktree_dir(tmp_path)), runner, busy(20000))

    assert findings_of(found) == [(FindingCode.PORT_IN_USE, "info")]
    assert found[0].details["port"] == 20000


def test_a_busy_port_that_lsof_cannot_attribute_is_in_use_and_not_a_conflict(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(listener=None)  # lsof lists nobody

    found = checked(make_entry(worktree_dir(tmp_path)), runner, busy(20000))

    assert findings_of(found) == [(FindingCode.PORT_IN_USE, "info")]


def test_a_listener_whose_directory_cannot_be_read_is_in_use_and_not_a_conflict(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(port=20000, listener=(4242, "node", None))

    found = checked(make_entry(worktree_dir(tmp_path)), runner, busy(20000))

    assert findings_of(found) == [(FindingCode.PORT_IN_USE, "info")]


def test_a_port_published_by_a_container_of_the_project_belongs_to_the_worktree(
    tmp_path: Path,
) -> None:
    compose = ComposeRecord(
        project="app_aa11bb22",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )
    entry = make_entry(worktree_dir(tmp_path), compose=compose)
    runner = FakeRunner(containers=["0.0.0.0:20000->5432/tcp, [::]:20000->5432/tcp"])

    assert checked(entry, runner, busy(20000)) == []
    assert not runner.called("lsof")
    asked = next(call for call in runner.calls if call[0] == "docker")
    assert "label=com.docker.compose.project=app_aa11bb22" in asked


def test_a_published_range_covers_the_ports_inside_it(tmp_path: Path) -> None:
    compose = ComposeRecord(
        project="app_aa11bb22",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )
    entry = make_entry(worktree_dir(tmp_path), compose=compose)
    runner = FakeRunner(containers=["0.0.0.0:19999-20002->80-83/tcp"])

    assert checked(entry, runner, busy(20000)) == []


def test_a_container_that_publishes_another_port_does_not_explain_this_one(
    tmp_path: Path,
) -> None:
    compose = ComposeRecord(
        project="app_aa11bb22",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )
    root = worktree_dir(tmp_path)
    runner = FakeRunner(
        port=20000,
        listener=(4242, "node", str(tmp_path / "elsewhere")),
        containers=["0.0.0.0:20007->80/tcp"],
    )

    found = checked(make_entry(root, compose=compose), runner, busy(20000))

    assert [finding.code for finding in found] == [FindingCode.PORT_CONFLICT]


def test_without_docker_the_check_falls_back_to_lsof(tmp_path: Path) -> None:
    compose = ComposeRecord(
        project="app_aa11bb22",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=ResourceState.CREATED,
    )
    root = worktree_dir(tmp_path)
    runner = FakeRunner(port=20000, listener=(4242, "node", str(root)), docker_missing=True)

    assert checked(make_entry(root, compose=compose), runner, busy(20000)) == []


def test_every_assigned_port_is_checked_and_only_those(tmp_path: Path) -> None:
    published = PublishedPort(
        service="db", target=5432, protocol="tcp", host_ip=None, port=20002, variable=None
    )
    entry = make_entry(
        worktree_dir(tmp_path),
        ports=(("PORT", 20000), ("API_PORT", 20001)),
        published=[published],
    )
    runner = FakeRunner(lsof_missing=True)
    probed: list[int] = []

    def is_free(port: int) -> bool:
        probed.append(port)
        return True

    checked(entry, runner, is_free)

    assert sorted(probed) == [20000, 20001, 20002]  # not the rest of the block


def test_a_port_that_is_both_a_variable_and_published_is_checked_once(tmp_path: Path) -> None:
    published = PublishedPort(
        service="db", target=5432, protocol="tcp", host_ip=None, port=20000, variable="PORT"
    )
    entry = make_entry(worktree_dir(tmp_path), published=[published])
    probed: list[int] = []

    def is_free(port: int) -> bool:
        probed.append(port)
        return True

    checked(entry, FakeRunner(), is_free)

    assert probed == [20000]


# --- dependencies (FR-060e) --------------------------------------------------------------------


def versions(
    *, git: str = "git version 2.50.1", compose: str = "v2.30.0", engine_up: bool = True
) -> Callable[[Sequence[str], Mapping[str, str], Path | None], "subprocess.CompletedProcess[str]"]:
    """A runner that answers the version and engine commands of the dependency checks."""

    def run(
        command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        words = list(command)
        if words[:2] == ["git", "--version"]:
            return process(git + "\n")
        if words[:3] == ["docker", "context", "inspect"]:
            return process("unix:///var/run/docker.sock\n")
        if words[:3] == ["docker", "compose", "version"]:
            return process(compose + "\n")
        if words[:2] == ["docker", "info"]:
            return process("27.0.1\n", returncode=0 if engine_up else 1)
        raise AssertionError(f"unexpected command {words}")

    return run


def missing_program(
    command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
) -> "subprocess.CompletedProcess[str]":
    raise FileNotFoundError(command[0])


def test_git_that_answers_is_ok_and_shows_its_version() -> None:
    status = doctor.git_status(versions(), ENVIRON)

    assert status == DependencyStatus(name="git", status="ok", detail="git version 2.50.1")


def test_git_that_is_not_installed_is_unavailable() -> None:
    status = doctor.git_status(missing_program, ENVIRON)

    assert (status.name, status.status) == ("git", "unavailable")
    assert status.detail is not None and "not installed" in status.detail


def test_git_older_than_2_31_is_unavailable() -> None:
    status = doctor.git_status(versions(git="git version 2.30.2"), ENVIRON)

    assert status.status == "unavailable"
    assert status.detail is not None and "2.31" in status.detail


def test_docker_is_not_required_without_compose_in_the_configuration() -> None:
    status = doctor.docker_status(Config(), run=versions(), environ=ENVIRON)

    assert (status.name, status.status) == ("docker", "not_required")


def test_docker_is_not_required_outside_a_repository() -> None:
    status = doctor.docker_status(None, run=missing_program, environ=ENVIRON)

    assert (status.name, status.status) == ("docker", "not_required")


def compose_config() -> Config:
    return Config.model_validate({"compose": {"file": "compose.yaml"}})


def test_docker_that_a_configuration_needs_and_that_runs_is_ok() -> None:
    status = doctor.docker_status(compose_config(), run=versions(), environ=ENVIRON)

    assert (status.name, status.status) == ("docker", "ok")


def test_docker_that_a_configuration_needs_and_that_does_not_answer_is_unavailable() -> None:
    status = doctor.docker_status(compose_config(), run=versions(engine_up=False), environ=ENVIRON)

    assert status.status == "unavailable"
    assert status.detail is not None and "does not answer" in status.detail


def test_docker_that_a_configuration_needs_and_that_is_missing_is_unavailable() -> None:
    status = doctor.docker_status(compose_config(), run=missing_program, environ=ENVIRON)

    assert status.status == "unavailable"
    assert status.detail is not None and "not installed" in status.detail


def postgres_config() -> Config:
    return Config.model_validate(
        {
            "database": {
                "type": "postgres",
                "template": "app_template",
                "url": "postgresql://dev@127.0.0.1:5432/{name}",
            }
        }
    )


def test_postgres_is_not_required_without_a_postgres_database_in_the_configuration() -> None:
    sqlite = Config.model_validate(
        {"database": {"type": "sqlite", "template": "db.sqlite3", "url": "sqlite:///{path}"}}
    )

    for config in (None, Config(), sqlite):
        status = doctor.postgres_status(config, server_check=lambda target: pytest.fail("asked"))
        assert (status.name, status.status) == ("postgres", "not_required")


def test_postgres_that_a_configuration_needs_and_that_answers_is_ok() -> None:
    asked: list[PostgresTarget] = []

    status = doctor.postgres_status(postgres_config(), server_check=asked.append)

    assert (status.name, status.status) == ("postgres", "ok")
    assert [(target.host, target.port, target.user) for target in asked] == [
        ("127.0.0.1", 5432, "dev")
    ]


def test_postgres_that_a_configuration_needs_and_that_refuses_is_unavailable() -> None:
    def refuse(target: PostgresTarget) -> None:
        raise WtenvError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "cannot connect to the Postgres server at 127.0.0.1:5432 as dev",
            details={"dependency": "postgres", "reason": "cannot_connect"},
        )

    status = doctor.postgres_status(postgres_config(), server_check=refuse)

    assert status.status == "unavailable"
    assert status.detail == "cannot connect to the Postgres server at 127.0.0.1:5432 as dev"


def test_a_url_that_cannot_be_resolved_is_not_a_dependency_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pattern = {
        "database": {
            "type": "postgres",
            "template": "app_template",
            "url": "postgresql://dev:{env:WTENV_DOCTOR_TEST_PASSWORD}@127.0.0.1:5432/{name}",
        }
    }
    monkeypatch.delenv("WTENV_DOCTOR_TEST_PASSWORD", raising=False)

    status = doctor.postgres_status(
        Config.model_validate(pattern), server_check=lambda target: pytest.fail("asked")
    )

    assert status.status == "not_required"
    assert status.detail is not None and "WTENV_DOCTOR_TEST_PASSWORD" in status.detail


@pytest.mark.parametrize("name", ["git", "docker", "postgres"])
def test_a_needed_dependency_that_is_unavailable_is_a_dependency_unavailable_problem(
    name: str,
) -> None:
    unavailable = DependencyStatus.model_validate(
        {"name": name, "status": "unavailable", "detail": "it does not answer"}
    )
    fine = [
        DependencyStatus(name="git", status="ok"),
        DependencyStatus(name="docker", status="not_required"),
        DependencyStatus(name="postgres", status="ok"),
    ]
    statuses = [unavailable if status.name == name else status for status in fine]

    found = doctor.dependency_findings(statuses)

    assert findings_of(found) == [(FindingCode.DEPENDENCY_UNAVAILABLE, "problem")]
    assert found[0].details == {"dependency": name, "reason": "it does not answer"}


def test_ok_and_not_required_dependencies_are_never_findings() -> None:
    statuses = [
        DependencyStatus(name="git", status="ok"),
        DependencyStatus(name="docker", status="not_required"),
        DependencyStatus(name="postgres", status="not_required"),
    ]

    assert doctor.dependency_findings(statuses) == []


# --- the result and its exit status (FR-061) ---------------------------------------------------


def problem(code: FindingCode = FindingCode.ORPHANED_WORKTREE) -> Finding:
    return Finding(code=code, severity="problem", message="a problem")


def info() -> Finding:
    return Finding(code=FindingCode.PORT_IN_USE, severity="info", message="in use")


def test_no_findings_is_ok() -> None:
    result = doctor.make_result([], [])

    assert (result.ok, result.error) == (True, None)
    assert result.command == "doctor" and result.schema_version == 1


def test_info_findings_alone_are_ok() -> None:
    result = doctor.make_result([info()], [])

    assert result.ok is True and result.error is None
    assert len(result.findings) == 1


def test_problem_findings_make_the_result_problems_found_with_exit_status_17() -> None:
    result = doctor.make_result([problem(), info(), problem(FindingCode.PORT_CONFLICT)], [])

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.PROBLEMS_FOUND
    assert result.error.exit_status == EXIT_STATUS[ErrorCode.PROBLEMS_FOUND] == 17
    assert result.error.details == {"problems": 2}
    assert len(result.findings) == 3


def test_the_result_validates_against_the_model_after_a_round_trip() -> None:
    result = doctor.make_result(
        [problem(), info()],
        [DependencyStatus(name="git", status="ok", detail="git version 2.50.1")],
    )

    assert DoctorResult.model_validate_json(result.model_dump_json()) == result
