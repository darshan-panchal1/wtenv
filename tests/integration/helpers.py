"""Small helpers shared by the integration tests of `wtenv up`."""

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psycopg

from wtenv.output import UpResult

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
