"""Small helpers shared by the integration tests of `wtenv up`."""

import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from wtenv.output import DownResult, GcResult, Item, LsResult, UpResult

TEMPLATE_DATABASE = "wtenv_test_template"
TEMPLATE_ROWS = [(1, "first"), (2, "second")]

BEGIN = "# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>"
END = "# <<< wtenv managed <<<"


def git(cwd: Path, *args: str) -> str:
    """Run git in `cwd` and return its standard output; fail with git's own message."""
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout


def commit_all(cwd: Path, message: str = "add files") -> None:
    """Commit every file under `cwd` (a worktree) on its current branch."""
    git(cwd, "add", "-A")
    git(cwd, "commit", "-m", message)


def parse_up(process: subprocess.CompletedProcess[str]) -> UpResult:
    """Return the `UpResult` that `wtenv up --json` printed; standard output is one document."""
    assert len(process.stdout.splitlines()) == 1, process.stdout
    return UpResult.model_validate_json(process.stdout)


def parse_down(process: subprocess.CompletedProcess[str]) -> DownResult:
    """Return the `DownResult` that `wtenv down --json` printed; standard output is one document."""
    assert len(process.stdout.splitlines()) == 1, process.stdout
    return DownResult.model_validate_json(process.stdout)


def parse_gc(process: subprocess.CompletedProcess[str]) -> GcResult:
    """Return the `GcResult` that `wtenv gc --json` printed; standard output is one document."""
    assert len(process.stdout.splitlines()) == 1, process.stdout
    return GcResult.model_validate_json(process.stdout)


def parse_ls(process: subprocess.CompletedProcess[str]) -> LsResult:
    """Return the `LsResult` that `wtenv ls --json` printed; standard output is one document."""
    assert len(process.stdout.splitlines()) == 1, process.stdout
    return LsResult.model_validate_json(process.stdout)


def snapshot_tree(root: Path) -> dict[str, tuple[str, bytes | str | None, int]]:
    """Describe everything under `root` without following a link, so a test can compare two states.

    Each entry is `(kind, content, mode)`: a file's bytes, a link's target, `None` for a directory.
    A symbolic link is never followed; a test uses this on a decoy that a link points at, so a
    write, a delete, or a new file anywhere under it shows as a difference (FR-086).
    """
    described: dict[str, tuple[str, bytes | str | None, int]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        mode = path.lstat().st_mode
        relative = str(path.relative_to(root))
        if path.is_symlink():
            described[relative] = ("link", os.readlink(path), mode)
        elif path.is_dir():
            described[relative] = ("dir", None, mode)
        else:
            described[relative] = ("file", path.read_bytes(), mode)
    return described


def keys(items: list[Item]) -> list[tuple[str, str]]:
    """Return `(kind, name)` of each item, so a test can compare lists of items briefly."""
    return [(item.kind.value, item.name) for item in items]


def exclude_file(repository: Path) -> Path:
    """Return the shared exclude file of the repository whose main worktree is `repository`."""
    return repository / ".git" / "info" / "exclude"


def block_lines(path: Path) -> list[str]:
    """Return the lines between wtenv's two markers in `path`; empty without a block."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if BEGIN not in lines:
        return []
    return lines[lines.index(BEGIN) + 1 : lines.index(END)]


def make_sqlite_template(path: Path, rows: int = 1) -> bytes:
    """Create a real SQLite database with a table `t`; return its bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("CREATE TABLE t (id INTEGER)")
        for number in range(rows):
            connection.execute("INSERT INTO t VALUES (?)", [number])
    connection.close()
    return path.read_bytes()


def sqlite_rows(path: Path) -> list[tuple[int]]:
    connection = sqlite3.connect(path)
    try:
        return [(int(row[0]),) for row in connection.execute("SELECT id FROM t ORDER BY id")]
    finally:
        connection.close()


@dataclass(frozen=True)
class PostgresServer:
    """A running local Postgres server, and the template database it holds."""

    host: str
    port: int
    user: str
    password: str
    template: str

    def connect(self, dbname: str = "postgres") -> psycopg.Connection[tuple[object, ...]]:
        """Open an autocommit connection to `dbname`; the caller closes it."""
        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=dbname,
            autocommit=True,
            connect_timeout=10,
        )

    def database_exists(self, name: str) -> bool:
        """Return whether a database called `name` exists on the server."""
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
            return row.fetchone() is not None


# --- docker compose ------------------------------------------------------------------------------

# Local images only: the tests never pull. `valkey` is small and declares a volume.
COMPOSE_IMAGE = "valkey/valkey:latest"
PROJECT_LABEL = "com.docker.compose.project"


def clean_environment() -> dict[str, str]:
    """Return the environment without any `COMPOSE_*` variable, which would beat the override."""
    return {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}


def compose_version() -> tuple[int, int, int] | None:
    """Return the version of `docker compose`, or None when it is not available."""
    try:
        result = subprocess.run(
            ["docker", "compose", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
    if result.returncode != 0 or match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def project_resources(project: str) -> dict[str, list[str]]:
    """Return the ids of the containers, networks, and volumes labelled with `project`."""
    label = f"label={PROJECT_LABEL}={project}"
    commands = {
        "containers": ["docker", "ps", "-a", "-q", "--filter", label],
        "networks": ["docker", "network", "ls", "-q", "--filter", label],
        "volumes": ["docker", "volume", "ls", "-q", "--filter", label],
    }
    return {
        kind: subprocess.run(command, capture_output=True, text=True, check=True).stdout.split()
        for kind, command in commands.items()
    }


@dataclass
class ComposeProjects:
    """The compose projects one test started; only these are ever removed."""

    names: list[str] = field(default_factory=list)

    def track(self, project: str) -> str:
        """Register `project`, a name wtenv generated for this test's worktree; return it."""
        assert project.startswith("wtenv-"), project
        if project not in self.names:
            self.names.append(project)
        return project

    def remove_all(self) -> None:
        """Take down each tracked project by name, from a directory with no compose file."""
        for project in self.names:
            with tempfile.TemporaryDirectory() as empty:
                subprocess.run(
                    ["docker", "compose", "-p", project, "down", "--volumes", "--remove-orphans"],
                    cwd=empty,
                    env=clean_environment(),
                    capture_output=True,
                    check=False,
                )

    def assert_nothing_left(self) -> None:
        """Fail if a tracked project still has a container, a network, or a volume."""
        for project in self.names:
            left = {kind: ids for kind, ids in project_resources(project).items() if ids}
            assert not left, f"compose project {project} left resources behind: {left}"
