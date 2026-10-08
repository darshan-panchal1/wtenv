"""The state directory and the registry and worktree locks (files.md; research.md section 9)."""

import errno
import fcntl
import inspect
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import platformdirs
import pytest

from wtenv.errors import ErrorCode, WtenvError
from wtenv.identity import short_id
from wtenv.locks import (
    REGISTRY_LOCK_TIMEOUT,
    WORKTREE_LOCK_TIMEOUT,
    ensure_state_dir,
    registry_lock,
    state_dir,
    try_worktree_lock,
    worktree_lock,
)

GIT_DIR = "/code/app/.git/worktrees/feature"
OTHER_GIT_DIR = "/code/app/.git/worktrees/other"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@contextmanager
def _held_by_another_process(context: str) -> Iterator[subprocess.Popen[str]]:
    """Start a process that takes a lock (`context` is the `with` expression) and holds it."""
    code = (
        "import time\n"
        "from wtenv.locks import registry_lock, worktree_lock\n"
        f"with {context}:\n"
        "    print('locked', flush=True)\n"
        "    time.sleep(120)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    try:
        assert process.stdout.readline().strip() == "locked"
        yield process
    finally:
        process.kill()
        process.wait()
        process.stdout.close()


REGISTRY_HOLDER = "registry_lock()"
WORKTREE_HOLDER = f"worktree_lock({GIT_DIR!r})"

# --- the state directory --------------------------------------------------------------


def test_the_state_directory_is_the_platformdirs_user_state_path(state_home: Path) -> None:
    assert state_dir() == platformdirs.user_state_path("wtenv", appauthor=False)
    assert state_dir() == state_home / "wtenv"


def test_xdg_state_home_moves_the_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = tmp_path / "elsewhere"
    monkeypatch.setenv("XDG_STATE_HOME", str(moved))

    assert state_dir() == moved / "wtenv"


def test_asking_for_the_state_directory_creates_nothing(state_home: Path) -> None:
    state_dir()

    assert not state_home.exists()


def test_ensure_state_dir_creates_it_with_mode_0700(state_home: Path) -> None:
    created = ensure_state_dir()

    assert created == state_home / "wtenv"
    assert created.is_dir()
    assert _mode(created) == 0o700


def test_ensure_state_dir_leaves_an_existing_directory_alone(state_home: Path) -> None:
    existing = state_home / "wtenv"
    existing.mkdir(parents=True)
    existing.chmod(0o755)

    assert ensure_state_dir() == existing
    assert _mode(existing) == 0o755


# --- the lock files -------------------------------------------------------------------


def test_the_registry_lock_file_is_in_the_state_directory_with_mode_0600() -> None:
    with registry_lock():
        pass

    lock_file = state_dir() / "registry.lock"
    assert lock_file.is_file()
    assert _mode(lock_file) == 0o600
    assert _mode(state_dir()) == 0o700


def test_the_worktree_lock_file_is_named_by_the_short_id_with_mode_0600() -> None:
    with worktree_lock(GIT_DIR):
        pass

    lock_file = state_dir() / "locks" / f"{short_id(GIT_DIR, 16)}.lock"
    assert lock_file.is_file()
    assert _mode(lock_file) == 0o600


def test_lock_files_are_never_deleted() -> None:
    with registry_lock():
        pass
    with worktree_lock(GIT_DIR):
        pass

    assert (state_dir() / "registry.lock").exists()
    assert (state_dir() / "locks" / f"{short_id(GIT_DIR, 16)}.lock").exists()


# --- the registry lock ----------------------------------------------------------------


def test_the_registry_wait_bound_defaults_to_ten_seconds_and_is_a_parameter() -> None:
    assert REGISTRY_LOCK_TIMEOUT == 10
    assert inspect.signature(registry_lock).parameters["timeout"].default == REGISTRY_LOCK_TIMEOUT


def test_the_registry_lock_past_its_bound_is_registry_busy_with_the_wait() -> None:
    with _held_by_another_process(REGISTRY_HOLDER):
        started = time.monotonic()
        with pytest.raises(WtenvError) as raised, registry_lock(timeout=0.3):
            pass
        waited = time.monotonic() - started

    assert raised.value.code is ErrorCode.REGISTRY_BUSY
    assert raised.value.details == {"waited_seconds": 0.3}
    assert waited >= 0.3


def test_the_registry_lock_is_released_when_the_block_ends() -> None:
    with registry_lock():
        pass

    with registry_lock(timeout=0.2):
        pass


def test_the_registry_lock_is_released_when_the_block_raises() -> None:
    with pytest.raises(KeyError), registry_lock():
        raise KeyError("inside")

    with registry_lock(timeout=0.2):
        pass


def test_the_registry_lock_cannot_be_taken_twice() -> None:
    with registry_lock(), pytest.raises(WtenvError) as raised, registry_lock(timeout=0.1):
        pass

    assert raised.value.code is ErrorCode.REGISTRY_BUSY


def test_a_registry_lock_that_may_not_create_creates_nothing_when_there_is_no_lock_file(
    state_home: Path,
) -> None:
    with registry_lock(create=False):
        pass

    assert not state_home.exists()  # T233, FR-060


def test_a_registry_lock_that_may_not_create_still_takes_the_lock_when_the_file_exists() -> None:
    with registry_lock():
        pass

    with (
        registry_lock(create=False),
        pytest.raises(WtenvError) as raised,
        registry_lock(timeout=0.1),
    ):
        pass

    assert raised.value.code is ErrorCode.REGISTRY_BUSY


def test_a_registry_lock_that_may_not_create_waits_for_a_holder_and_then_busy() -> None:
    with registry_lock():
        pass

    with (
        _held_by_another_process(REGISTRY_HOLDER),
        pytest.raises(WtenvError) as raised,
        registry_lock(timeout=0.3, create=False),
    ):
        pass

    assert raised.value.code is ErrorCode.REGISTRY_BUSY


# --- the worktree lock ----------------------------------------------------------------


def test_the_worktree_wait_bound_defaults_to_sixty_seconds_and_is_a_parameter() -> None:
    assert WORKTREE_LOCK_TIMEOUT == 60
    assert inspect.signature(worktree_lock).parameters["timeout"].default == WORKTREE_LOCK_TIMEOUT


def test_the_worktree_lock_past_its_bound_is_worktree_busy_with_the_wait() -> None:
    with (
        _held_by_another_process(WORKTREE_HOLDER),
        pytest.raises(WtenvError) as raised,
        worktree_lock(GIT_DIR, timeout=0.3),
    ):
        pass

    assert raised.value.code is ErrorCode.WORKTREE_BUSY
    assert raised.value.details == {"waited_seconds": 0.3}


def test_a_worktree_lock_does_not_block_another_worktree() -> None:
    with _held_by_another_process(WORKTREE_HOLDER), worktree_lock(OTHER_GIT_DIR, timeout=0.3):
        pass


def test_a_worktree_lock_does_not_block_the_registry_lock() -> None:
    with _held_by_another_process(WORKTREE_HOLDER), registry_lock(timeout=0.3):
        pass


def test_the_worktree_lock_is_released_when_the_block_ends() -> None:
    with worktree_lock(GIT_DIR):
        pass

    with worktree_lock(GIT_DIR, timeout=0.2):
        pass


def test_the_no_wait_form_reports_held_without_raising_or_waiting() -> None:
    with _held_by_another_process(WORKTREE_HOLDER):
        started = time.monotonic()
        with try_worktree_lock(GIT_DIR) as acquired:
            elapsed = time.monotonic() - started

    assert acquired is False
    assert elapsed < 1


def test_the_no_wait_form_takes_a_free_lock_and_holds_it_for_the_block() -> None:
    with try_worktree_lock(GIT_DIR) as acquired:
        assert acquired is True
        with pytest.raises(WtenvError) as raised, worktree_lock(GIT_DIR, timeout=0.1):
            pass

    assert raised.value.code is ErrorCode.WORKTREE_BUSY
    with worktree_lock(GIT_DIR, timeout=0.2):
        pass


# --- a killed holder (FR-078) ---------------------------------------------------------


def test_a_registry_lock_whose_holder_was_killed_can_be_taken_at_once() -> None:
    with _held_by_another_process(REGISTRY_HOLDER) as holder:
        holder.send_signal(signal.SIGKILL)
        holder.wait()

        with registry_lock(timeout=0.5):
            pass

    assert (state_dir() / "registry.lock").exists()


def test_a_worktree_lock_whose_holder_was_killed_can_be_taken_at_once() -> None:
    with _held_by_another_process(WORKTREE_HOLDER) as holder:
        holder.send_signal(signal.SIGKILL)
        holder.wait()

        with worktree_lock(GIT_DIR, timeout=0.5):
            pass

    assert (state_dir() / "locks" / f"{short_id(GIT_DIR, 16)}.lock").exists()


# --- a filesystem without flock -------------------------------------------------------


@pytest.fixture
def no_flock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every `flock` fail the way a filesystem without it does."""

    def unsupported(fd: int, operation: int) -> None:
        raise OSError(errno.ENOSYS, "Function not implemented")

    monkeypatch.setattr(fcntl, "flock", unsupported)


def test_a_filesystem_without_flock_is_registry_unreadable_for_the_registry_lock(
    no_flock: None,
) -> None:
    with pytest.raises(WtenvError) as raised, registry_lock():
        pass

    assert raised.value.code is ErrorCode.REGISTRY_UNREADABLE
    assert raised.value.details == {
        "path": str(state_dir() / "registry.lock"),
        "reason": "lock_unsupported",
    }


def test_a_filesystem_without_flock_is_registry_unreadable_for_the_worktree_lock(
    no_flock: None,
) -> None:
    with pytest.raises(WtenvError) as raised, worktree_lock(GIT_DIR):
        pass

    assert raised.value.code is ErrorCode.REGISTRY_UNREADABLE
    assert raised.value.details == {
        "path": str(state_dir() / "locks" / f"{short_id(GIT_DIR, 16)}.lock"),
        "reason": "lock_unsupported",
    }


def test_a_filesystem_without_flock_is_not_worked_around_with_a_soft_lock(
    no_flock: None,
) -> None:
    with pytest.raises(WtenvError), registry_lock():
        pass

    # A soft lock would have replaced the lock file with an existence marker.
    assert (state_dir() / "registry.lock").read_bytes() == b""
