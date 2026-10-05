"""`wtenv up` in several processes at once (SC-005; FR-013, FR-076 to FR-078; spec Edge Cases)."""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import BEGIN, END
from wtenv.provision import up

from wtenv.errors import ErrorCode, WtenvError
from wtenv.identity import current_worktree
from wtenv.registry import load, registry_path

pytestmark = pytest.mark.integration

TRIALS = 20
WORKTREES = 5

# Takes the worktree lock, says so, and holds it until it is killed.
LOCK_HOLDER = """
import sys, time
from wtenv.locks import worktree_lock

with worktree_lock(sys.argv[1], timeout=5):
    print("locked", flush=True)
    time.sleep(120)
"""


def start_up(worktree: Path, state: Path) -> "subprocess.Popen[str]":
    """Start `wtenv up --json` in `worktree` with its own state directory, without waiting."""
    return subprocess.Popen(
        [sys.executable, "-m", "wtenv", "up", "--json"],
        cwd=worktree,
        env={**os.environ, "XDG_STATE_HOME": str(state)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_five_worktrees_provisioned_at_once_never_share_a_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
) -> None:
    repo = make_repo("app")
    worktrees = [add_worktree(repo, f"wt{i}", f"branch-{i}") for i in range(WORKTREES)]

    for trial in range(TRIALS):
        state = tmp_path / f"trial-{trial}"
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        for worktree in worktrees:
            (worktree / ".env.local").unlink(missing_ok=True)

        processes = [start_up(worktree, state) for worktree in worktrees]
        outputs = [process.communicate() for process in processes]

        for process, (_, stderr) in zip(processes, outputs, strict=True):
            assert process.returncode == 0, f"trial {trial}: {stderr}"
        entries = load().worktrees
        assert len(entries) == WORKTREES, f"trial {trial}"
        used: list[int] = []
        for entry in entries.values():
            used.extend(range(entry.block.start, entry.block.start + entry.block.size))
        assert len(used) == len(set(used)), f"trial {trial}: two blocks share a port"
        assert {entry.state for entry in entries.values()} == {"provisioned"}


def test_two_up_runs_in_one_worktree_give_one_block_and_one_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
) -> None:
    worktree = add_worktree(make_repo("app"), "feature-x", "feature-x")
    state = tmp_path / "shared-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    processes = [start_up(worktree, state) for _ in range(2)]
    outputs = [process.communicate() for process in processes]

    for process, (_, stderr) in zip(processes, outputs, strict=True):
        assert process.returncode == 0, stderr
    entries = load().worktrees
    assert len(entries) == 1
    (entry,) = entries.values()
    assert entry.state == "provisioned"
    lines = (worktree / ".env.local").read_text(encoding="utf-8").splitlines()
    assert lines == [BEGIN, f"PORT={entry.block.start}", END]


def test_up_gives_up_with_worktree_busy_while_another_process_holds_the_lock(
    make_repo: Callable[[str], Path],
    add_worktree: Callable[[Path, str, str], Path],
) -> None:
    worktree = add_worktree(make_repo("app"), "feature-x", "feature-x")
    git_dir = current_worktree(worktree).git_dir
    holder = subprocess.Popen(
        [sys.executable, "-c", LOCK_HOLDER, git_dir],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        with pytest.raises(WtenvError) as caught:
            up(worktree, lock_timeout=0.3)

        assert caught.value.code is ErrorCode.WORKTREE_BUSY
        assert not registry_path().exists()
        assert not (worktree / ".env.local").exists()

        holder.kill()  # SIGKILL: the lock must not outlive its holder (FR-078)
        holder.wait()
        started = time.monotonic()
        result = up(worktree, lock_timeout=5)

        assert time.monotonic() - started < 5
        assert result.ok
        assert (worktree / ".env.local").exists()
    finally:
        holder.kill()
        holder.wait()
