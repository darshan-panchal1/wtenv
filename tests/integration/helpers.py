"""Small helpers shared by the integration tests of `wtenv up`."""

import subprocess
from pathlib import Path

from wtenv.output import UpResult

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
