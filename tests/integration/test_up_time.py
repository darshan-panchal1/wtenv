"""Provisioning time of `wtenv up` on the sample app (NFR-002, SC-002; T138).

The sample app is `tests/fixtures/sample_app/`, with its database URL pointed at the test
Postgres server. The post-up commands are switched off by the fixture itself (it has none), so
what is timed is wtenv's own work. Two measurements, each under 5 seconds of wall-clock time:

(a) a first `up` in a new worktree with database isolation switched off (no `[database]`);
(b) a repeat `up` in a fully provisioned worktree with the full configuration.
"""

import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from helpers import PostgresServer, commit_all

from wtenv.database import postgres_database_name
from wtenv.identity import current_worktree

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_app"
LIMIT_SECONDS = 5.0
PASSWORD_VARIABLE = "QS_PG_PASSWORD"


def sample_toml(server: PostgresServer) -> str:
    """Return the fixture's `wtenv.toml` with the database URL and template of the test server."""
    text = (FIXTURE / "wtenv.toml").read_text(encoding="utf-8")
    url = f"{server.user}:{{env:{PASSWORD_VARIABLE}}}@{server.host}:{server.port}"
    text, replaced = re.subn(r"postgres:\{env:QS_PG_PASSWORD\}@localhost:15432", url, text)
    assert replaced == 1
    return text.replace('template = "app_template"', f'template = "{server.template}"')


def without_database(toml: str) -> str:
    """Return `toml` without its `[database]` table."""
    return re.sub(r"\[database\]\n(?:(?!\[).*\n?)*", "", toml)


@pytest.fixture
def sample_repo(make_repo: Callable[[str], Path]) -> Path:
    """A repository holding the sample app files, committed so new worktrees contain them."""
    repo = make_repo("sample")
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    commit_all(repo, "sample app")
    return repo


@pytest.fixture
def database_cleanup(postgres_server: PostgresServer) -> Iterator[list[str]]:
    """Collect database names a test creates; drop them when the test ends."""
    names: list[str] = []
    yield names
    with postgres_server.connect() as connection:
        for name in names:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def timed_up(
    run_wtenv: Callable[..., subprocess.CompletedProcess[str]],
    cwd: Path,
    password: str,
) -> float:
    """Run `wtenv up` in `cwd`, check it succeeded, and return its wall-clock seconds."""
    started = time.perf_counter()
    process = run_wtenv(["up", "--json"], cwd, {PASSWORD_VARIABLE: password})
    elapsed = time.perf_counter() - started
    assert process.returncode == 0, process.stderr
    return elapsed


def test_a_first_up_without_database_isolation_takes_under_five_seconds(
    run_wtenv: Callable[..., subprocess.CompletedProcess[str]],
    sample_repo: Path,
    add_worktree: Callable[[Path, str, str], Path],
    postgres_server: PostgresServer,
    compose_docker: None,
) -> None:
    worktree = add_worktree(sample_repo, "sample-a", "a")
    (worktree / "wtenv.toml").write_text(
        without_database(sample_toml(postgres_server)), encoding="utf-8"
    )

    elapsed = timed_up(run_wtenv, worktree, postgres_server.password)

    assert elapsed < LIMIT_SECONDS, f"first up took {elapsed:.2f} s"


def test_a_repeat_up_with_the_full_configuration_takes_under_five_seconds(
    run_wtenv: Callable[..., subprocess.CompletedProcess[str]],
    sample_repo: Path,
    add_worktree: Callable[[Path, str, str], Path],
    postgres_server: PostgresServer,
    database_cleanup: list[str],
    compose_docker: None,
) -> None:
    worktree = add_worktree(sample_repo, "sample-b", "b")
    (worktree / "wtenv.toml").write_text(sample_toml(postgres_server), encoding="utf-8")
    database_cleanup.append(
        postgres_database_name(worktree.name, current_worktree(worktree).git_dir)
    )
    # The first `up` copies the database; that time is excluded (NFR-002).
    timed_up(run_wtenv, worktree, postgres_server.password)

    elapsed = timed_up(run_wtenv, worktree, postgres_server.password)

    assert elapsed < LIMIT_SECONDS, f"repeat up took {elapsed:.2f} s"
