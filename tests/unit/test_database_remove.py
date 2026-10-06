"""Removing a worktree's recorded databases (FR-019, FR-039, FR-042; constitution v1.0.2,
Principle II).

Every test here works in a temporary directory and deletes only files it made itself. The
Postgres cases that need a server are in `tests/integration/test_database_postgres_remove.py`.
"""

import socket
from pathlib import Path

import pytest

from wtenv.database import PostgresTarget, remove_postgres_database, remove_sqlite_copy
from wtenv.output import Item, ItemKind

SIDE_SUFFIXES = ("-wal", "-shm", "-journal")


def make_copy(
    root: Path, name: str = "dev.sqlite3", sides: tuple[str, ...] = SIDE_SUFFIXES
) -> Path:
    """Create `<root>/.wtenv/<name>` with the given side files beside it; return the copy."""
    directory = root / ".wtenv"
    directory.mkdir(parents=True, exist_ok=True)
    copy = directory / name
    copy.write_bytes(b"database")
    for suffix in sides:
        Path(f"{copy}{suffix}").write_bytes(b"side file " + suffix.encode())
    return copy


def sqlite_item(path: Path | str) -> Item:
    return Item(kind=ItemKind.SQLITE_FILE, name=str(path))


def listing(root: Path) -> dict[str, bytes]:
    """Return every file under `root` with its bytes, to compare before and after."""
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- SQLite: the copy and its side files (FR-039) ------------------------------------------------


def test_the_copy_and_each_existing_side_file_are_removed_as_separate_items(
    tmp_path: Path,
) -> None:
    copy = make_copy(tmp_path)

    result = remove_sqlite_copy(copy)

    assert result.removed == [sqlite_item(copy)] + [
        sqlite_item(f"{copy}{suffix}") for suffix in SIDE_SUFFIXES
    ]
    assert result.already_absent == []
    assert result.failed == []
    assert listing(tmp_path) == {}


def test_every_item_is_a_sqlite_file_named_by_its_absolute_path(tmp_path: Path) -> None:
    copy = make_copy(tmp_path)

    result = remove_sqlite_copy(copy)

    assert all(item.kind is ItemKind.SQLITE_FILE for item in result.removed)
    assert all(Path(item.name).is_absolute() for item in result.removed)


def test_a_side_file_that_does_not_exist_is_not_listed(tmp_path: Path) -> None:
    copy = make_copy(tmp_path, sides=("-wal",))

    result = remove_sqlite_copy(copy)

    assert result.removed == [sqlite_item(copy), sqlite_item(f"{copy}-wal")]


def test_other_files_in_the_directory_are_not_touched(tmp_path: Path) -> None:
    copy = make_copy(tmp_path, sides=("-wal",))
    bystanders = [
        copy.parent / "other.sqlite3-wal",
        Path(f"{copy}-wal.bak"),
        Path(f"{copy}.backup"),
        copy.parent / "notes.txt",
    ]
    for bystander in bystanders:
        bystander.write_bytes(b"keep me")

    remove_sqlite_copy(copy)

    assert all(bystander.read_bytes() == b"keep me" for bystander in bystanders)


def test_listing_only_returns_the_same_items_and_deletes_nothing(tmp_path: Path) -> None:
    copy = make_copy(tmp_path)
    before = listing(tmp_path)

    planned = remove_sqlite_copy(copy, dry_run=True)

    assert listing(tmp_path) == before
    assert remove_sqlite_copy(copy).removed == planned.removed


def test_a_recorded_copy_that_is_gone_is_already_absent_and_its_side_files_stay(
    tmp_path: Path,
) -> None:
    copy = make_copy(tmp_path)
    copy.unlink()
    before = listing(tmp_path)

    for dry_run in (False, True):
        result = remove_sqlite_copy(copy, dry_run=dry_run)

        assert result.removed == []
        assert result.already_absent == [sqlite_item(copy)]
        assert result.failed == []
    assert listing(tmp_path) == before  # FR-039: never on their own (reading R2)


def test_a_database_that_is_not_the_recorded_one_keeps_its_side_files(tmp_path: Path) -> None:
    recorded = make_copy(tmp_path, "dev.sqlite3")
    decoy = make_copy(tmp_path, "decoy.sqlite3")
    decoy_before = {path: data for path, data in listing(tmp_path).items() if "decoy" in path}

    remove_sqlite_copy(recorded)

    assert {path: data for path, data in listing(tmp_path).items()} == decoy_before
    assert decoy.exists()
    assert Path(f"{decoy}-wal").exists() and Path(f"{decoy}-journal").exists()


def test_a_symbolic_link_is_removed_but_not_what_it_points_to(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.sqlite3"
    elsewhere.write_bytes(b"not wtenv's")
    copy = tmp_path / ".wtenv" / "dev.sqlite3"
    copy.parent.mkdir()
    copy.symlink_to(elsewhere)

    result = remove_sqlite_copy(copy)

    assert result.removed == [sqlite_item(copy)]
    assert not copy.is_symlink()
    assert elsewhere.read_bytes() == b"not wtenv's"


def test_a_side_file_that_cannot_be_removed_is_failed_and_the_copy_is_kept(
    tmp_path: Path,
) -> None:
    copy = make_copy(tmp_path, sides=("-shm",))
    stuck = Path(f"{copy}-wal")
    stuck.mkdir()  # `unlink` cannot remove a directory

    result = remove_sqlite_copy(copy)

    assert [item.name for item in result.failed] == [str(stuck)]
    assert all(item.kind is ItemKind.SQLITE_FILE and item.reason for item in result.failed)
    assert copy.exists()  # the record stays, so the next `down` finishes the job (FR-042)
    assert stuck.is_dir()


# --- Postgres: failures never carry the password (FR-019) ----------------------------------------


def refused_port() -> int:
    """Return a local port on which nothing listens."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


SECRET = "s3cret-pa55word"


def unreachable_target() -> PostgresTarget:
    return PostgresTarget(host="127.0.0.1", port=refused_port(), user="wtenv", password=SECRET)


def test_a_failed_connection_is_a_failure_reason_without_the_password() -> None:
    result = remove_postgres_database(unreachable_target(), "wtenv_app_91c2d0aa")

    item = Item(kind=ItemKind.POSTGRES_DATABASE, name="wtenv_app_91c2d0aa")
    assert result.removed == [] and result.already_absent == []
    assert [(failed.kind, failed.name) for failed in result.failed] == [(item.kind, item.name)]
    assert result.failed[0].reason
    assert SECRET not in result.failed[0].reason
    assert SECRET not in repr(result)


def test_listing_only_with_no_server_to_ask_fails_the_database_and_does_not_list_it() -> None:
    """LOW-2, FR-040: a dry run that cannot ask the server must not promise a removal."""
    result = remove_postgres_database(unreachable_target(), "wtenv_app_91c2d0aa", dry_run=True)

    assert result.removed == [] and result.already_absent == []
    assert [(f.kind, f.name) for f in result.failed] == [
        (ItemKind.POSTGRES_DATABASE, "wtenv_app_91c2d0aa")
    ]
    assert "postgres" in result.failed[0].reason.lower()
    assert SECRET not in result.failed[0].reason and SECRET not in repr(result)


def test_listing_only_gives_the_same_failure_as_the_real_run_when_the_server_is_not_there() -> None:
    target = unreachable_target()

    planned = remove_postgres_database(target, "wtenv_app_91c2d0aa", dry_run=True)
    actual = remove_postgres_database(target, "wtenv_app_91c2d0aa")

    assert planned.failed == actual.failed and planned.removed == actual.removed == []


@pytest.mark.parametrize("name", ["postgres", "template1", "my_app"])
def test_a_name_wtenv_would_not_have_generated_is_never_dropped(name: str) -> None:
    """Every database wtenv creates is `wtenv_<slug>_<id8>` (FR-022); nothing else is dropped."""
    result = remove_postgres_database(unreachable_target(), name)

    assert result.removed == []
    assert [failed.name for failed in result.failed] == [name]
    assert "wtenv_" in result.failed[0].reason


# --- the drop guard: only a database wtenv named, on a server on this machine (T168; LOW-6) ------
#
# The registry is a file a developer can edit. Whatever it says, wtenv must never drop a database
# whose name wtenv could not have generated (`wtenv_<slug>_<id8>`, files.md, Names), and never
# connect to a host that is not this machine (FR-025, FR-039). The guard comes before any
# connection: a spy stands in for the connection and fails the test if it is opened.

GENERATED = "wtenv_app_91c2d0aa"


class ConnectionSpy:
    """Stands in for `database._connect`: records each call and then refuses, like a dead server."""

    def __init__(self) -> None:
        self.targets: list[PostgresTarget] = []

    def __call__(self, target: PostgresTarget) -> None:
        import psycopg

        self.targets.append(target)
        raise psycopg.OperationalError("connection refused")


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> ConnectionSpy:
    recorded = ConnectionSpy()
    monkeypatch.setattr("wtenv.database._connect", recorded)
    return recorded


def local_target(host: str = "127.0.0.1") -> PostgresTarget:
    return PostgresTarget(host=host, port=5432, user="wtenv", password=None)


@pytest.mark.parametrize(
    "name",
    [
        "postgres",
        "template1",
        "my_app",
        "wtenv_",
        "wtenv_precious",  # a developer's own database that happens to start with the prefix
        "wtenv_app",
        "wtenv__91c2d0aa",  # no slug
        "wtenv_app_91c2d0a",  # seven hex digits
        "wtenv_app_91c2d0aaa",  # nine
        "wtenv_app_91c2d0AA",  # upper case
        "wtenv_app_91c2d0ag",  # not hex
        "wtenv_app-x_91c2d0aa",  # a hyphen
        "wtenv_app_91c2d0aa; DROP DATABASE postgres",
        "Wtenv_app_91c2d0aa",
        "wtenv_" + "a" * 41 + "_91c2d0aa",  # a slug longer than wtenv makes
    ],
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_recorded_name_wtenv_would_not_have_generated_is_failed_without_a_connection(
    spy: ConnectionSpy, name: str, dry_run: bool
) -> None:
    result = remove_postgres_database(local_target(), name, dry_run=dry_run)

    assert spy.targets == []  # no connection was made
    assert result.removed == [] and result.already_absent == []
    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.POSTGRES_DATABASE, name)]
    assert "wtenv_" in result.failed[0].reason


@pytest.mark.parametrize(
    "name",
    [GENERATED, "wtenv_a_00000000", "wtenv_" + "a" * 40 + "_91c2d0aa", "wtenv_a_b_c_91c2d0aa"],
)
def test_every_name_wtenv_generates_passes_the_name_guard(spy: ConnectionSpy, name: str) -> None:
    remove_postgres_database(local_target(), name)

    assert len(spy.targets) == 1  # the guard let it through to the server


@pytest.mark.parametrize(
    "host",
    ["db.example.com", "192.0.2.10", "127.0.0.2", "localhost.example.com", "0.0.0.0", ""],
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_recorded_host_that_is_not_this_machine_is_failed_without_a_connection(
    spy: ConnectionSpy, host: str, dry_run: bool
) -> None:
    result = remove_postgres_database(local_target(host), GENERATED, dry_run=dry_run)

    assert spy.targets == []
    assert result.removed == [] and result.already_absent == []
    assert [(f.kind, f.name) for f in result.failed] == [(ItemKind.POSTGRES_DATABASE, GENERATED)]
    assert "local" in result.failed[0].reason.lower()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_the_three_local_hosts_pass_the_host_guard(spy: ConnectionSpy, host: str) -> None:
    remove_postgres_database(local_target(host), GENERATED)

    assert [target.host for target in spy.targets] == [host]
