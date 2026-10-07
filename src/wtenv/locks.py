"""The state directory, the registry lock, and the worktree locks (research.md section 9).

Both locks are `flock` locks, so a process that is killed releases them (FR-078). Lock files
are never deleted: removing one while another process waits on it would let two processes hold
"the" lock.
"""

import errno
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import filelock
import platformdirs

from wtenv.errors import ErrorCode, WtenvError
from wtenv.identity import short_id

# How long a command waits for a lock before it gives up. Parameters of the lock functions, so
# tests can pass small values; there is no flag and no environment variable (research.md
# section 9).
REGISTRY_LOCK_TIMEOUT = 10.0
WORKTREE_LOCK_TIMEOUT = 60.0

_PRIVATE_DIRECTORY = 0o700
_PRIVATE_FILE = 0o600


def state_dir() -> Path:
    """Return the per-user state directory, which `XDG_STATE_HOME` moves. Creates nothing."""
    return platformdirs.user_state_path("wtenv", appauthor=False)


def ensure_state_dir() -> Path:
    """Return the state directory, creating it with mode 0700 when it does not exist."""
    path = state_dir()
    path.mkdir(mode=_PRIVATE_DIRECTORY, parents=True, exist_ok=True)
    return path


def _worktree_lock_path(git_dir: str) -> Path:
    locks = ensure_state_dir() / "locks"
    locks.mkdir(mode=_PRIVATE_DIRECTORY, exist_ok=True)
    return locks / f"{short_id(git_dir, 16)}.lock"


def _acquire(path: Path, timeout: float) -> filelock.FileLock | None:
    """Take the lock on `path`, waiting up to `timeout` seconds; None when it stayed held.

    `fallback_to_soft=False`: on a filesystem without `flock`, filelock would switch to a lock
    that a killed process leaves behind. wtenv stops with `registry_unreadable` instead.
    """
    lock = filelock.FileLock(path, mode=_PRIVATE_FILE, fallback_to_soft=False)
    try:
        lock.acquire(timeout=timeout)
    except filelock.Timeout:
        return None
    except OSError as error:
        if error.errno != errno.ENOSYS:
            raise
        raise WtenvError(
            ErrorCode.REGISTRY_UNREADABLE,
            f"cannot lock {path}: the filesystem does not support flock",
            hint="Set XDG_STATE_HOME to a directory on a local filesystem.",
            details={"path": str(path), "reason": "lock_unsupported"},
        ) from error
    return lock


@contextmanager
def registry_lock(timeout: float = REGISTRY_LOCK_TIMEOUT) -> Iterator[None]:
    """Hold the registry lock for the block. Every read-check-write of the registry takes it.

    Raises `registry_busy` when another process still holds it after `timeout` seconds.
    """
    lock = _acquire(ensure_state_dir() / "registry.lock", timeout)
    if lock is None:
        raise WtenvError(
            ErrorCode.REGISTRY_BUSY,
            f"another wtenv process holds the registry lock (waited {timeout:g} seconds)",
            hint="Try again.",
            details={"waited_seconds": timeout},
        )
    try:
        yield
    finally:
        lock.release()


@contextmanager
def worktree_lock(git_dir: str, timeout: float = WORKTREE_LOCK_TIMEOUT) -> Iterator[None]:
    """Hold the lock of one worktree, named by its git directory, for the block.

    `up` and `down` take it for their whole run. Raises `worktree_busy` when another process
    still holds it after `timeout` seconds.
    """
    lock = _acquire(_worktree_lock_path(git_dir), timeout)
    if lock is None:
        raise WtenvError(
            ErrorCode.WORKTREE_BUSY,
            f"another wtenv command is working on this worktree (waited {timeout:g} seconds)",
            hint="Try again.",
            details={"waited_seconds": timeout},
        )
    try:
        yield
    finally:
        lock.release()


@contextmanager
def try_worktree_lock(git_dir: str) -> Iterator[bool]:
    """Try to take the lock of one worktree without waiting, for `gc` (FR-077).

    Yields True, with the lock held for the block, or False when it is held elsewhere.
    """
    lock = _acquire(_worktree_lock_path(git_dir), 0)
    try:
        yield lock is not None
    finally:
        if lock is not None:
            lock.release()
