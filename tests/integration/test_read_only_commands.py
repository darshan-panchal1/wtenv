"""T237 (FR-040, FR-050, FR-060): a command that must not write leaves a fresh state directory alone.

`XDG_STATE_HOME` points at a directory that does not exist. After the command it must still not
exist: no state directory, no `registry.lock`.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

READ_ONLY_COMMANDS = [
    ["ls"],
    ["ls", "--json"],
    ["doctor"],
    ["doctor", "--json"],
    ["gc", "--dry-run"],
    ["gc", "--dry-run", "--json"],
    ["down", "--dry-run"],
    ["down", "--dry-run", "--json"],
    ["--version"],
    ["--version", "--json"],
    ["--help"],
]


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


@pytest.mark.parametrize("args", READ_ONLY_COMMANDS, ids=" ".join)
def test_a_read_only_command_creates_nothing_on_a_fresh_state_directory(
    args: list[str],
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    state_home: Path,
) -> None:
    worktree = add_worktree(repo, "one", "one")
    assert not state_home.exists()

    result = run_wtenv(args, worktree)

    assert result.returncode == 0, result.stdout + result.stderr
    listing = sorted(str(path.relative_to(state_home)) for path in state_home.rglob("*"))
    assert listing == []
    assert not state_home.exists()
