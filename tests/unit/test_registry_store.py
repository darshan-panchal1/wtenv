"""Loading, saving, and transactions on the registry file (FR-066 to FR-070)."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import wtenv.registry as registry_module
from wtenv.errors import ErrorCode, WtenvError
from wtenv.locks import registry_lock, state_dir
from wtenv.registry import (
    HookRecord,
    PortBlock,
    Registry,
    WorktreeEntry,
    load,
    registry_path,
    save,
    transaction,
)

GIT_DIR = "/code/app/.git/worktrees/feature"


def make_entry(git_dir: str = GIT_DIR) -> WorktreeEntry:
    return WorktreeEntry(
        git_dir=git_dir,
        path="/code/feature",
        repository="/code/app/.git",
        state="incomplete",
        block=PortBlock(start=20010, size=10),
    )


def registry_with_entry() -> Registry:
    registry = Registry(version=1)
    registry.worktrees[GIT_DIR] = make_entry()
    return registry


def write_registry_file(content: bytes) -> Path:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --- where the file is, and loading ---------------------------------------------------


def test_the_registry_file_is_registry_json_in_the_state_directory() -> None:
    assert registry_path() == state_dir() / "registry.json"


def test_no_file_loads_an_empty_registry_and_creates_nothing(state_home: Path) -> None:
    registry = load()

    assert registry == Registry(version=1)
    assert registry.version == 1
    assert not state_home.exists()


def test_what_was_saved_loads_back_equal() -> None:
    registry = registry_with_entry()
    registry.hooks["/code/app/.git"] = HookRecord(
        hook_file="/code/app/.git/hooks/post-checkout", created_file=True
    )

    save(registry)

    assert load() == registry


# --- saving ---------------------------------------------------------------------------


def test_save_creates_the_state_directory_and_a_file_with_mode_0600() -> None:
    save(registry_with_entry())

    assert stat.S_IMODE(state_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(registry_path().stat().st_mode) == 0o600


def test_save_writes_one_json_document_ending_in_a_line_break() -> None:
    save(registry_with_entry())

    text = registry_path().read_text()
    assert text.endswith("\n")
    document = json.loads(text)
    assert document["version"] == 1
    assert document["worktrees"][GIT_DIR]["block"] == {"start": 20010, "size": 10}


def test_save_replaces_an_existing_file_and_gives_it_mode_0600() -> None:
    path = write_registry_file(b'{"version": 1, "worktrees": {}, "hooks": {}}')
    path.chmod(0o644)

    save(registry_with_entry())

    assert load() == registry_with_entry()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_leaves_no_temporary_file_behind() -> None:
    save(registry_with_entry())
    save(registry_with_entry())

    assert [p.name for p in state_dir().iterdir()] == ["registry.json"]


def test_save_writes_a_temporary_file_syncs_it_then_renames_it_over_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        calls.append(("fsync", os.fstat(fd).st_size))
        real_fsync(fd)

    def replace(source: Any, destination: Any) -> None:
        calls.append(("replace", (Path(source), Path(destination))))
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)

    save(registry_with_entry())

    assert [name for name, _ in calls] == ["fsync", "replace"]
    synced_size = calls[0][1]
    source, destination = calls[1][1]
    assert synced_size > 0  # the content was already written when it was synced
    assert destination == registry_path()
    assert source.parent == state_dir()
    assert source != destination
    assert not source.exists()


def test_a_failure_while_saving_leaves_the_old_file_and_no_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save(Registry(version=1))
    before = registry_path().read_bytes()

    def fail(source: Any, destination: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)

    with pytest.raises(OSError, match="disk full"):
        save(registry_with_entry())

    assert registry_path().read_bytes() == before
    assert [p.name for p in state_dir().iterdir()] == ["registry.json"]


# --- a registry that cannot be read (FR-070) ------------------------------------------

UNREADABLE = [
    pytest.param(b"{not json", "invalid_json", id="not-json"),
    pytest.param(b"", "invalid_json", id="empty"),
    pytest.param(b'{"version": 1, "worktrees": ', "invalid_json", id="truncated"),
    pytest.param(b"\xff\xfe\x00{", "invalid_json", id="not-utf8"),
    pytest.param(b"[]", "invalid_schema", id="not-an-object"),
    pytest.param(b'{"version": 1, "worktrees": {"/x": 5}}', "invalid_schema", id="bad-entry"),
    pytest.param(b'{"version": 1, "colour": "red"}', "invalid_schema", id="unknown-field"),
    pytest.param(b'{"worktrees": {}}', "invalid_schema", id="no-version"),
    pytest.param(b'{"version": 2, "worktrees": {}}', "unknown_version", id="future-version"),
    pytest.param(b'{"version": 0, "worktrees": {}}', "unknown_version", id="past-version"),
    pytest.param(b'{"version": 2, "something": "new"}', "unknown_version", id="newer-format"),
]


@pytest.mark.parametrize(("content", "reason"), UNREADABLE)
def test_an_unreadable_registry_stops_with_the_path_and_reason(content: bytes, reason: str) -> None:
    path = write_registry_file(content)

    with pytest.raises(WtenvError) as raised:
        load()

    assert raised.value.code is ErrorCode.REGISTRY_UNREADABLE
    assert raised.value.details == {"path": str(path), "reason": reason}
    assert raised.value.hint is not None


def test_a_registry_that_cannot_be_read_from_disk_is_not_readable() -> None:
    path = registry_path()
    path.parent.mkdir(parents=True)
    path.mkdir()  # a directory where the file should be

    with pytest.raises(WtenvError) as raised:
        load()

    assert raised.value.code is ErrorCode.REGISTRY_UNREADABLE
    assert raised.value.details == {"path": str(path), "reason": "not_readable"}


@pytest.mark.parametrize(("content", "reason"), UNREADABLE)
def test_an_unreadable_registry_is_never_rewritten(
    content: bytes, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_registry_file(content)

    def must_not_save(registry: Registry) -> None:
        raise AssertionError("save was called")

    monkeypatch.setattr(registry_module, "save", must_not_save)

    with pytest.raises(WtenvError), transaction():
        pass

    assert path.read_bytes() == content


# --- transactions (FR-068) ------------------------------------------------------------


def test_a_transaction_saves_what_its_block_changed() -> None:
    with transaction() as registry:
        registry.worktrees[GIT_DIR] = make_entry()

    assert load() == registry_with_entry()


def test_a_transaction_starts_from_what_is_on_disk() -> None:
    save(registry_with_entry())

    with transaction() as registry:
        assert list(registry.worktrees) == [GIT_DIR]
        registry.worktrees["/code/app/.git/worktrees/two"] = make_entry(
            "/code/app/.git/worktrees/two"
        )

    assert sorted(load().worktrees) == [GIT_DIR, "/code/app/.git/worktrees/two"]


def test_a_transaction_on_a_missing_file_starts_from_an_empty_registry() -> None:
    with transaction() as registry:
        assert registry == Registry(version=1)


def test_an_exception_inside_a_transaction_saves_nothing() -> None:
    save(registry_with_entry())
    before = registry_path().read_bytes()

    with pytest.raises(RuntimeError, match="check failed"), transaction() as registry:
        registry.worktrees.clear()
        raise RuntimeError("check failed")

    assert registry_path().read_bytes() == before


def test_an_exception_inside_a_first_transaction_creates_no_file() -> None:
    with pytest.raises(RuntimeError), transaction() as registry:
        registry.worktrees[GIT_DIR] = make_entry()
        raise RuntimeError("check failed")

    assert not registry_path().exists()


def test_a_transaction_calls_save_once_when_it_succeeds_and_not_when_it_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[Registry] = []
    monkeypatch.setattr(registry_module, "save", saved.append)

    with transaction():
        pass
    with pytest.raises(RuntimeError), transaction():
        raise RuntimeError("check failed")

    assert len(saved) == 1


def test_the_registry_lock_is_held_for_the_whole_transaction() -> None:
    with transaction(), pytest.raises(WtenvError) as raised, registry_lock(timeout=0.1):
        pass

    assert raised.value.code is ErrorCode.REGISTRY_BUSY


def test_the_registry_lock_is_released_after_a_transaction_succeeds_or_fails() -> None:
    with transaction():
        pass
    with registry_lock(timeout=0.2):
        pass

    with pytest.raises(RuntimeError), transaction():
        raise RuntimeError("check failed")
    with registry_lock(timeout=0.2):
        pass


def test_a_transaction_that_cannot_get_the_lock_in_time_is_registry_busy_and_saves_nothing() -> (
    None
):
    with registry_lock(), pytest.raises(WtenvError) as raised, transaction(timeout=0.1):
        pytest.fail("the block must not run")

    assert raised.value.code is ErrorCode.REGISTRY_BUSY
    assert raised.value.details == {"waited_seconds": 0.1}
    assert not registry_path().exists()


def test_simultaneous_transactions_in_other_processes_lose_no_update() -> None:
    script = (
        "import sys\n"
        "from wtenv.registry import HookRecord, transaction\n"
        "name = sys.argv[1]\n"
        "with transaction() as registry:\n"
        "    registry.hooks[name] = HookRecord(hook_file='/hooks/' + name, created_file=True)\n"
    )
    processes = [subprocess.Popen([sys.executable, "-c", script, f"/repo/{n}"]) for n in range(10)]

    assert [process.wait(timeout=60) for process in processes] == [0] * 10
    assert sorted(load().hooks) == sorted(f"/repo/{n}" for n in range(10))
