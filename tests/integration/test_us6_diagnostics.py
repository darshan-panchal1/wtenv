"""User story 6: `wtenv doctor`, on real git worktrees (spec.md, User Story 6; T125; FR-060, FR-061).

Every test uses a temporary repository and the temporary state directory of `tests/conftest.py`.
The listeners are processes the test starts on ports of its own worktrees; nothing else on the
machine is looked at, and nothing outside the temporary directories is changed. The Postgres tests
use the throwaway server of the `postgres_server` fixture.
"""

import json
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from helpers import (
    PostgresServer,
    exclude_file,
    git,
    make_sqlite_template,
    parse_doctor,
    parse_up,
    snapshot_tree,
)

from wtenv.database import postgres_database_name
from wtenv.errors import ErrorCode
from wtenv.identity import current_worktree
from wtenv.output import DoctorResult, Finding, FindingCode
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

# Listens on 127.0.0.1:<port>, says so, and runs until its standard input is closed.
LISTENER = """
import socket, sys

listener = socket.socket()
listener.bind(("127.0.0.1", int(sys.argv[1])))
listener.listen()
print("ready", flush=True)
sys.stdin.read()
"""

# Takes the worktree lock, says so, and holds it until it is killed.
LOCK_HOLDER = """
import sys, time
from wtenv.locks import worktree_lock

with worktree_lock(sys.argv[1], timeout=5):
    print("locked", flush=True)
    time.sleep(60)
"""

PASSWORD_VARIABLE = "MYAPP_DB_PASSWORD"


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


@pytest.fixture
def lsof() -> None:
    """Skip the requesting test unless `lsof` is installed: without it a port's holder is unknown."""
    if shutil.which("lsof") is None:
        pytest.skip("lsof is not installed, so no port holder can be found")


# --- helpers ------------------------------------------------------------------------------------


def up(run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None) -> None:
    """Run `wtenv up` in `worktree`; it must succeed."""
    process = run_wtenv(["up", "--json"], worktree, env)
    assert process.returncode == 0, process.stdout + process.stderr
    parse_up(process)


def doctor(
    run_wtenv: Run, cwd: Path, env: dict[str, str] | None = None
) -> tuple[int, DoctorResult, str]:
    """Run `wtenv doctor --json` in `cwd`; return the exit status, the one document, and stderr."""
    process = run_wtenv(["doctor", "--json"], cwd, env)
    return process.returncode, parse_doctor(process), process.stderr


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


def codes(result: DoctorResult, severity: str | None = None) -> list[FindingCode]:
    return [f.code for f in result.findings if severity in (None, f.severity)]


def finding(result: DoctorResult, code: FindingCode) -> Finding:
    """Return the one finding with `code`."""
    found = [f for f in result.findings if f.code is code]
    assert len(found) == 1, result.findings
    return found[0]


def dependency(result: DoctorResult, name: str) -> tuple[str, str | None]:
    """Return `(status, detail)` of the dependency `name`."""
    (found,) = [d for d in result.dependencies if d.name == name]
    return found.status, found.detail


def closed_port() -> int:
    """Return a local port that nothing listens on now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def listener_in(directory: Path, port: int) -> Iterator[None]:
    """Run a process with working directory `directory` that listens on `127.0.0.1:port`."""
    process = subprocess.Popen(
        [sys.executable, "-c", LISTENER, str(port)],
        cwd=directory,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stdin is not None
    try:
        assert process.stdout.readline().strip() == "ready"
        yield
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def state_of(repo: Path, *worktrees: Path) -> dict[str, object]:
    """Everything `doctor` could touch, as bytes: the registry, every worktree, the exclude file
    and the hooks directory of the repository."""
    registry = registry_path()
    exclude = exclude_file(repo)
    return {
        "registry": registry.read_bytes() if registry.exists() else None,
        "exclude": exclude.read_bytes() if exclude.exists() else None,
        "hooks": snapshot_tree(repo / ".git" / "hooks"),
        **{str(worktree): snapshot_tree(worktree) for worktree in worktrees},
    }


def edit_registry(change: Callable[[dict[str, dict[str, object]]], None]) -> None:
    """Rewrite the registry file after `change` altered its entries (a test of a damaged state)."""
    path = registry_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["worktrees"])
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --- scenario 3: a healthy setup ----------------------------------------------------------------


def test_a_healthy_setup_has_no_problem_and_exits_0(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, lsof: None
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    up(run_wtenv, first)
    up(run_wtenv, second)

    status, result, stderr = doctor(run_wtenv, first)

    assert status == 0, (result, stderr)
    assert result.ok and result.error is None and result.schema_version == 1
    assert codes(result, "problem") == []
    assert dependency(result, "git")[0] == "ok"
    assert dependency(result, "docker")[0] == "not_required"
    assert dependency(result, "postgres")[0] == "not_required"
    assert stderr == ""


def test_the_text_output_of_a_healthy_setup_says_so_and_exits_0(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)

    process = run_wtenv(["doctor"], worktree)

    assert process.returncode == 0, process.stderr
    assert "no problems" in process.stdout
    assert not process.stdout.lstrip().startswith("{")


# --- scenario 4: each problem has its own code, and nothing is changed --------------------------


def test_a_listener_outside_the_worktree_on_an_assigned_port_is_a_port_conflict(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, tmp_path: Path, lsof: None
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    port = entry_of(worktree).ports[0].port
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with listener_in(elsewhere, port):
        status, result, stderr = doctor(run_wtenv, worktree)

    assert status == 17
    assert not result.ok and result.error is not None
    assert result.error.code is ErrorCode.PROBLEMS_FOUND and result.error.exit_status == 17
    assert result.error.details == {"problems": 1}
    conflict = finding(result, FindingCode.PORT_CONFLICT)
    assert conflict.severity == "problem" and conflict.worktree == str(worktree)
    assert conflict.details["port"] == port
    assert conflict.details["directory"] == str(elsewhere.resolve())
    assert "problems_found" in stderr


def test_a_listener_inside_the_worktree_is_not_a_conflict(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, lsof: None
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    (worktree / "web").mkdir()

    with listener_in(worktree / "web", entry_of(worktree).ports[0].port):
        status, result, _ = doctor(run_wtenv, worktree)

    assert status == 0, result
    assert FindingCode.PORT_CONFLICT not in codes(result)


def test_an_entry_whose_worktree_was_removed_with_git_is_an_orphaned_worktree(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    gone = add_worktree(repo, "gone", "gone")
    up(run_wtenv, gone)
    git(repo, "worktree", "remove", "--force", str(gone))

    status, result, _ = doctor(run_wtenv, repo)

    assert status == 17
    orphan = finding(result, FindingCode.ORPHANED_WORKTREE)
    assert orphan.severity == "problem" and orphan.worktree == str(gone)


def test_an_unverifiable_entry_is_reported_with_its_reason(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, tmp_path: Path
) -> None:
    moved = add_worktree(repo, "moved", "moved")
    up(run_wtenv, moved)
    shutil.move(moved, tmp_path / "somewhere-else")  # git is not told

    status, result, _ = doctor(run_wtenv, repo)

    assert status == 17
    unverifiable = finding(result, FindingCode.UNVERIFIABLE_WORKTREE)
    assert unverifiable.severity == "problem" and unverifiable.worktree == str(moved)
    assert unverifiable.details["reason"] == "git_still_lists"


def test_an_entry_whose_up_failed_is_an_incomplete_worktree(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    (worktree / "wtenv.toml").write_text('post_up = ["false"]\n', encoding="utf-8")
    assert run_wtenv(["up"], worktree).returncode == 12

    status, result, _ = doctor(run_wtenv, worktree)

    assert status == 17
    assert finding(result, FindingCode.INCOMPLETE_WORKTREE).worktree == str(worktree)


def test_an_env_file_that_was_deleted_is_a_missing_resource(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    (worktree / ".env.local").unlink()

    status, result, _ = doctor(run_wtenv, worktree)

    assert status == 17
    missing = finding(result, FindingCode.MISSING_RESOURCE)
    assert missing.details == {"kind": "env_file", "name": str(worktree / ".env.local")}


def test_two_blocks_that_share_a_port_are_a_block_overlap(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    up(run_wtenv, first)
    up(run_wtenv, second)
    key = current_worktree(second).git_dir
    start = entry_of(first).block.start

    def overlap(worktrees: dict[str, dict[str, object]]) -> None:
        worktrees[key]["block"] = {"start": start, "size": 10}

    edit_registry(overlap)

    status, result, _ = doctor(run_wtenv, first)

    assert status == 17
    overlapping = finding(result, FindingCode.BLOCK_OVERLAP)
    assert overlapping.details["worktrees"] == sorted([str(first), str(second)])


def test_a_postgres_server_that_a_configuration_needs_and_that_is_down_is_a_problem(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    url = f"postgresql://dev:hunter2@127.0.0.1:{closed_port()}/{{name}}"
    (worktree / "wtenv.toml").write_text(
        f'[database]\ntype = "postgres"\ntemplate = "app_template"\nurl = "{url}"\n',
        encoding="utf-8",
    )

    process = run_wtenv(["doctor", "--json"], worktree)

    assert process.returncode == 17
    result = parse_doctor(process)
    needed = finding(result, FindingCode.DEPENDENCY_UNAVAILABLE)
    assert needed.severity == "problem" and needed.details["dependency"] == "postgres"
    assert dependency(result, "postgres")[0] == "unavailable"
    assert dependency(result, "docker")[0] == "not_required"
    assert "hunter2" not in process.stdout + process.stderr  # FR-019


def test_a_docker_engine_that_a_configuration_needs_and_that_does_not_answer_is_a_problem(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    (worktree / "wtenv.toml").write_text('[compose]\nfile = "compose.yaml"\n', encoding="utf-8")
    dead_engine = {"DOCKER_HOST": f"tcp://127.0.0.1:{closed_port()}"}

    status, result, _ = doctor(run_wtenv, worktree, dead_engine)

    assert status == 17
    assert finding(result, FindingCode.DEPENDENCY_UNAVAILABLE).details["dependency"] == "docker"
    assert dependency(result, "docker")[0] == "unavailable"


def test_a_configuration_that_is_not_valid_does_not_make_doctor_fail(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    (worktree / "wtenv.toml").write_text("ports = 5\n", encoding="utf-8")

    status, result, _ = doctor(run_wtenv, worktree)

    assert status == 0, result
    docker_status, detail = dependency(result, "docker")
    assert docker_status == "not_required" and detail is not None and "wtenv.toml" in detail


def test_every_kind_of_problem_at_once_is_reported_with_its_own_code_and_changes_nothing(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    tmp_path: Path,
    lsof: None,
) -> None:
    """US6 scenario 4: a foreign listener, an entry whose worktree is gone, and a configuration
    that needs Postgres while it is unavailable."""
    live = add_worktree(repo, "live", "live")
    gone = add_worktree(repo, "gone", "gone")
    up(run_wtenv, live)
    up(run_wtenv, gone)
    git(repo, "worktree", "remove", "--force", str(gone))
    url = f"postgresql://dev@127.0.0.1:{closed_port()}/{{name}}"
    (live / "wtenv.toml").write_text(
        f'[database]\ntype = "postgres"\ntemplate = "app_template"\nurl = "{url}"\n',
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    before = state_of(repo, live)

    with listener_in(elsewhere, entry_of(live).ports[0].port):
        status, result, _ = doctor(run_wtenv, live)

    assert status == 17
    assert {
        FindingCode.PORT_CONFLICT,
        FindingCode.ORPHANED_WORKTREE,
        FindingCode.DEPENDENCY_UNAVAILABLE,
    } <= set(codes(result, "problem"))
    assert result.error is not None and result.error.details == {
        "problems": len(codes(result, "problem"))
    }
    assert state_of(repo, live) == before  # FR-060: nothing changed


# --- FR-060: doctor changes nothing --------------------------------------------------------------


def test_doctor_leaves_the_registry_env_files_exclude_file_hooks_and_sqlite_copies_alone(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, tmp_path: Path
) -> None:
    template = repo / "db" / "dev.sqlite3"
    make_sqlite_template(template)
    (repo / "wtenv.toml").write_text(
        '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n',
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "add the template")
    first = add_worktree(repo, "one", "one")
    second = add_worktree(repo, "two", "two")
    up(run_wtenv, first)
    up(run_wtenv, second)
    (second / ".env.local").unlink()  # a problem, so that doctor has something to report
    assert (first / ".wtenv" / "dev.sqlite3").exists()
    before = state_of(repo, first, second)

    healthy_or_not = [doctor(run_wtenv, first)[0], run_wtenv(["doctor"], second).returncode]

    assert healthy_or_not == [17, 17]
    assert state_of(repo, first, second) == before
    assert not (repo / ".git" / "hooks" / "post-checkout").exists()


def test_doctor_does_not_create_a_registry_where_there_is_none(
    run_wtenv: Run, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    status, result, _ = doctor(run_wtenv, plain)

    assert status == 0, result
    assert not registry_path().exists()


def test_doctor_creates_nothing_on_a_fresh_state_directory(
    run_wtenv: Run, tmp_path: Path, state_home: Path
) -> None:
    """T233 (FR-060): not the state directory, not `registry.lock`, in text or in JSON."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not state_home.exists()

    text = run_wtenv(["doctor"], plain)
    json_status, _, _ = doctor(run_wtenv, plain)

    assert (text.returncode, json_status) == (0, 0), text.stdout + text.stderr
    assert sorted(state_home.rglob("*")) == []
    assert not state_home.exists()


def test_doctor_leaves_an_existing_state_directory_byte_identical(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, state_home: Path
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    before = snapshot_tree(state_home)

    status, _, _ = doctor(run_wtenv, worktree)

    assert status == 0
    assert snapshot_tree(state_home) == before


# --- FR-003: anywhere; FR-076: no worktree lock --------------------------------------------------


def test_doctor_works_outside_any_repository(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, tmp_path: Path
) -> None:
    gone = add_worktree(repo, "gone", "gone")
    up(run_wtenv, gone)
    git(repo, "worktree", "remove", "--force", str(gone))
    plain = tmp_path / "plain"
    plain.mkdir()

    status, result, _ = doctor(run_wtenv, plain)

    assert status == 17
    assert codes(result, "problem") == [FindingCode.ORPHANED_WORKTREE]
    assert dependency(result, "git")[0] == "ok"
    assert dependency(result, "docker")[0] == "not_required"
    assert dependency(result, "postgres")[0] == "not_required"


def test_doctor_takes_no_worktree_lock(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    up(run_wtenv, worktree)
    git_dir = current_worktree(worktree).git_dir
    holder = subprocess.Popen(
        [sys.executable, "-c", LOCK_HOLDER, git_dir],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    try:
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()

        status, result, _ = doctor(run_wtenv, worktree)

        assert status == 0, result
        assert time.monotonic() - started < 30  # it did not wait for the 60-second lock
    finally:
        holder.kill()
        holder.wait()
        holder.stdout.close()


# --- Postgres (needs Docker) ---------------------------------------------------------------------


def postgres_toml(server: PostgresServer) -> str:
    url = f"postgresql://{server.user}:{{env:{PASSWORD_VARIABLE}}}@{server.host}:{server.port}/{{name}}"
    return f'[database]\ntype = "postgres"\ntemplate = "{server.template}"\nurl = "{url}"\n'


@pytest.fixture
def postgres_worktree(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """A provisioned worktree with its own database on the test server; dropped at the end."""
    monkeypatch.setenv(PASSWORD_VARIABLE, postgres_server.password)
    worktree = add_worktree(repo, "pg", "pg")
    (worktree / "wtenv.toml").write_text(postgres_toml(postgres_server), encoding="utf-8")
    name = postgres_database_name(worktree.name, current_worktree(worktree).git_dir)
    try:
        up(run_wtenv, worktree)
        assert postgres_server.database_exists(name)
        yield worktree
    finally:
        with postgres_server.connect() as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def database_names(server: PostgresServer) -> list[str]:
    with server.connect() as connection:
        rows = connection.execute("SELECT datname FROM pg_database ORDER BY datname").fetchall()
    return [str(row[0]) for row in rows]


def test_a_database_that_is_on_the_server_is_not_reported_and_doctor_drops_nothing(
    run_wtenv: Run, postgres_worktree: Path, postgres_server: PostgresServer
) -> None:
    before = database_names(postgres_server)

    status, result, _ = doctor(run_wtenv, postgres_worktree)

    assert status == 0, result
    assert dependency(result, "postgres")[0] == "ok"
    assert codes(result, "problem") == []
    assert database_names(postgres_server) == before


def test_a_database_that_was_dropped_by_hand_is_a_missing_resource(
    run_wtenv: Run, postgres_worktree: Path, postgres_server: PostgresServer
) -> None:
    name = postgres_database_name(
        postgres_worktree.name, current_worktree(postgres_worktree).git_dir
    )
    with postgres_server.connect() as connection:
        connection.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    before = database_names(postgres_server)

    status, result, _ = doctor(run_wtenv, postgres_worktree)

    assert status == 17
    missing = finding(result, FindingCode.MISSING_RESOURCE)
    assert missing.details == {"kind": "postgres_database", "name": name}
    assert database_names(postgres_server) == before


def test_a_recorded_database_on_a_server_that_cannot_be_reached_is_only_skipped(
    run_wtenv: Run, postgres_worktree: Path, postgres_server: PostgresServer
) -> None:
    key = current_worktree(postgres_worktree).git_dir
    dead = closed_port()

    def point_at_a_dead_server(worktrees: dict[str, dict[str, object]]) -> None:
        databases = worktrees[key]["databases"]
        assert isinstance(databases, list)
        databases[0]["port"] = dead

    edit_registry(point_at_a_dead_server)
    (postgres_worktree / "wtenv.toml").unlink()  # no configuration needs Postgres now

    status, result, _ = doctor(run_wtenv, postgres_worktree)

    assert status == 0, result
    skipped = finding(result, FindingCode.RESOURCE_CHECK_SKIPPED)
    assert skipped.severity == "info" and skipped.worktree == str(postgres_worktree)
    assert codes(result, "problem") == []
    assert dependency(result, "postgres")[0] == "not_required"
