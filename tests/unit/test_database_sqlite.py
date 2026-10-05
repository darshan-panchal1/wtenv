"""Creating the SQLite copy of a worktree (FR-021, FR-023, FR-024, FR-027, FR-083; research.md
section 11)."""

import os
import sqlite3
from pathlib import Path

import pytest

from wtenv import database
from wtenv.database import (
    check_sqlite_target,
    check_sqlite_template,
    create_sqlite_copy,
    sqlite_copy_path,
    sqlite_template_path,
)
from wtenv.errors import ErrorCode, WtenvError

CONTENT = bytes(range(256)) * 40  # not valid SQLite: the copy is byte for byte, whatever it holds


def make_template(root: Path, relative: str = "db/dev.sqlite3") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CONTENT)
    return path


def failure(call: object) -> WtenvError:
    assert callable(call)
    with pytest.raises(WtenvError) as caught:
        call()
    return caught.value


# --- the copy (FR-021) ------------------------------------------------------------------


def test_the_template_is_copied_byte_for_byte_to_the_wtenv_directory(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    target = sqlite_copy_path(tmp_path, "db/dev.sqlite3")

    create_sqlite_copy(template, target)

    assert target == tmp_path / ".wtenv" / "dev.sqlite3"
    assert target.read_bytes() == CONTENT
    assert template.read_bytes() == CONTENT


def test_an_absolute_template_is_copied_too(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    template = make_template(outside, "app.db")
    target = sqlite_copy_path(worktree, str(template))

    create_sqlite_copy(sqlite_template_path(worktree, str(template)), target)

    assert target.read_bytes() == CONTENT


def test_a_real_sqlite_database_keeps_its_rows_and_stays_independent(tmp_path: Path) -> None:
    template = tmp_path / "template.db"
    with sqlite3.connect(template) as connection:
        connection.execute("CREATE TABLE t (id INTEGER)")
        connection.execute("INSERT INTO t VALUES (1)")
    before = template.read_bytes()
    target = tmp_path / "wt" / ".wtenv" / "template.db"

    create_sqlite_copy(template, target)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT id FROM t").fetchall() == [(1,)]
        connection.execute("INSERT INTO t VALUES (2)")

    assert template.read_bytes() == before  # FR-027


def test_no_temporary_file_is_left_beside_the_copy(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")

    create_sqlite_copy(template, target)

    assert [p.name for p in target.parent.iterdir()] == ["dev.sqlite3"]


def test_the_copy_appears_by_an_atomic_rename_of_a_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = make_template(tmp_path)
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")
    renames: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        # At the moment of the rename the target does not exist yet, and the source is whole.
        assert not target.exists()
        assert Path(source).read_bytes() == CONTENT
        renames.append((str(source), str(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(database.os, "replace", spy)

    create_sqlite_copy(template, target)

    assert [destination for _, destination in renames] == [str(target)]
    assert renames[0][0] != str(target)
    assert Path(renames[0][0]).parent == target.parent  # same directory, so the rename is atomic


def test_a_failed_copy_leaves_no_target_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = make_template(tmp_path)
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")

    def explode(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(database.os, "replace", explode)

    with pytest.raises(OSError, match="disk full"):
        create_sqlite_copy(template, target)

    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_the_copy_keeps_the_permissions_of_the_template(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    template.chmod(0o640)
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")

    create_sqlite_copy(template, target)

    assert target.stat().st_mode & 0o777 == 0o640


# --- the template (FR-083, research.md section 11) --------------------------------------


def test_a_missing_template_is_template_missing_and_nothing_is_created(tmp_path: Path) -> None:
    template = tmp_path / "db" / "missing.sqlite3"

    error = failure(lambda: check_sqlite_template(template))

    assert error.code is ErrorCode.TEMPLATE_MISSING
    assert error.details == {"kind": "sqlite", "template": str(template)}
    assert not (tmp_path / ".wtenv").exists()


def test_a_directory_is_not_a_template(tmp_path: Path) -> None:
    error = failure(lambda: check_sqlite_template(tmp_path))

    assert error.code is ErrorCode.TEMPLATE_MISSING


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_a_non_empty_side_file_beside_the_template_is_template_in_use(
    tmp_path: Path, suffix: str
) -> None:
    template = make_template(tmp_path)
    Path(f"{template}{suffix}").write_bytes(b"pending")

    error = failure(lambda: check_sqlite_template(template))

    assert error.code is ErrorCode.TEMPLATE_IN_USE
    assert error.details == {"kind": "sqlite", "template": str(template)}


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_an_empty_side_file_does_not_matter(tmp_path: Path, suffix: str) -> None:
    template = make_template(tmp_path)
    Path(f"{template}{suffix}").write_bytes(b"")

    check_sqlite_template(template)


def test_a_shm_file_alone_does_not_matter(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    Path(f"{template}-shm").write_bytes(b"x" * 32768)

    check_sqlite_template(template)


def test_a_usable_template_passes_the_check(tmp_path: Path) -> None:
    check_sqlite_template(make_template(tmp_path))


# --- the target (FR-024) ----------------------------------------------------------------


def test_something_at_the_target_is_an_ownership_conflict_and_is_not_modified(
    tmp_path: Path,
) -> None:
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")
    target.parent.mkdir()
    target.write_bytes(b"somebody else's data")

    error = failure(lambda: check_sqlite_target(target))

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert error.details == {"kind": "sqlite_file", "name": str(target)}
    assert target.read_bytes() == b"somebody else's data"


def test_a_directory_at_the_target_is_an_ownership_conflict(tmp_path: Path) -> None:
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")
    target.mkdir(parents=True)

    assert failure(lambda: check_sqlite_target(target)).code is ErrorCode.OWNERSHIP_CONFLICT


def test_nothing_at_the_target_passes_the_check(tmp_path: Path) -> None:
    check_sqlite_target(sqlite_copy_path(tmp_path, "dev.sqlite3"))


def test_a_dangling_symlink_at_the_target_is_an_ownership_conflict(tmp_path: Path) -> None:
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")
    target.parent.mkdir()
    target.symlink_to(tmp_path / "nowhere")

    assert failure(lambda: check_sqlite_target(target)).code is ErrorCode.OWNERSHIP_CONFLICT


def test_creating_over_an_existing_file_is_refused_and_leaves_it_alone(tmp_path: Path) -> None:
    template = make_template(tmp_path)
    target = sqlite_copy_path(tmp_path, "dev.sqlite3")
    target.parent.mkdir()
    target.write_bytes(b"recorded copy with data")

    error = failure(lambda: create_sqlite_copy(template, target))

    assert error.code is ErrorCode.OWNERSHIP_CONFLICT
    assert target.read_bytes() == b"recorded copy with data"
