"""`wtenv up` interrupted at each step, then run again (data-model.md, Write order of `up`;
FR-067, FR-069)."""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import block_lines, exclude_file, parse_up

from wtenv.envfile import read_section
from wtenv.identity import current_worktree
from wtenv.registry import WorktreeEntry, load

pytestmark = pytest.mark.integration

KILLED = 137

# Runs `wtenv up` in a child process with one function wrapped. With `when` "after", the wrapper
# calls the real function and then exits like a killed process. With "provisioned" (for
# `registry.save`), it exits instead of saving the entry as `provisioned`.
CHILD = """
import os, sys
import wtenv.envfile, wtenv.exclude, wtenv.registry

module = {module}
name = "{function}"
real = getattr(module, name)

def wrapper(*args, **kwargs):
    if "{when}" == "provisioned":
        saved = args[0]
        if any(entry.state == "provisioned" for entry in saved.worktrees.values()):
            os._exit({killed})
        return real(*args, **kwargs)
    real(*args, **kwargs)
    os._exit({killed})

setattr(module, name, wrapper)
from wtenv.cli import main
sys.exit(main(["up"]))
"""

POINTS = {
    "after the registry step": ("wtenv.registry", "save", "after"),
    "after the exclude step": ("wtenv.exclude", "add_patterns", "after"),
    "after the env-file step": ("wtenv.envfile", "write_section", "after"),
    "before the completion step": ("wtenv.registry", "save", "provisioned"),
}


def interrupt_up(worktree: Path, module: str, function: str, when: str) -> None:
    """Run `wtenv up` in `worktree` in a child process that dies at the given point."""
    code = CHILD.format(module=module, function=function, when=when, killed=KILLED)
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == KILLED, process.stderr


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


@pytest.mark.parametrize("point", list(POINTS))
def test_up_interrupted_at_any_point_leaves_only_recorded_resources_and_recovers(
    point: str,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
    run_wtenv: Callable[..., "subprocess.CompletedProcess[str]"],
) -> None:
    repo = make_repo("app")
    worktree = add_worktree(repo, "feature-x", "feature-x")
    env_file = worktree / ".env.local"

    interrupt_up(worktree, *POINTS[point])

    # What the interrupted run left: an entry with a block, and nothing the entry does not record.
    entry = entry_of(worktree)
    assert entry.state == "incomplete"
    assert entry.block.size == 10
    assert set(block_lines(exclude_file(repo))) <= set(entry.exclude_patterns)
    if env_file.exists():
        assert entry.env_file is not None
        assert entry.env_file.path == ".env.local"
    if point == "after the registry step":
        assert block_lines(exclude_file(repo)) == []
        assert not env_file.exists()
        assert entry.env_file is not None and entry.env_file.state.value == "creating"
    if point == "after the exclude step":
        assert block_lines(exclude_file(repo)) == ["/.env.local"]
        assert not env_file.exists()
    if point == "after the env-file step":
        assert [name for name, _ in read_section(env_file)] == ["PORT"]
        assert entry.env_file is not None and entry.env_file.state.value == "creating"
    if point == "before the completion step":
        assert entry.env_file is not None and entry.env_file.state.value == "created"
    interrupted_block = entry.block

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    result = parse_up(process)
    assert result.ok
    entry = entry_of(worktree)
    assert entry.state == "provisioned"
    assert entry.block == interrupted_block
    assert block_lines(exclude_file(repo)) == ["/.env.local"]
    assert read_section(env_file) == [("PORT", str(interrupted_block.start))]
    assert entry.env_file is not None
    assert entry.env_file.state.value == "created"
    # The file was created by the interrupted run, which recorded that before writing.
    assert entry.env_file.created_file is True
    assert os.stat(env_file).st_mode & 0o777 == 0o600
