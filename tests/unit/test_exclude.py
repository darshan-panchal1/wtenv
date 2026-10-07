"""The wtenv block in `.git/info/exclude` (files.md, `.git/info/exclude` block; FR-018, FR-085)."""

import stat
from pathlib import Path

import pytest

from wtenv.errors import ErrorCode, WtenvError
from wtenv.exclude import add_patterns, remove_block

BEGIN = "# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>"
END = "# <<< wtenv managed <<<"


def block(*patterns: str) -> str:
    """The text of the block that holds `patterns`, each already in its place."""
    return "\n".join([BEGIN, *patterns, END]) + "\n"


def exclude_path(tmp_path: Path) -> Path:
    return tmp_path / "repo" / ".git" / "info" / "exclude"


def damaged(path: Path) -> WtenvError:
    """Return the error that touching the block of `path` raises."""
    with pytest.raises(WtenvError) as caught:
        add_patterns(path, ["/.env.local"])
    assert caught.value.code is ErrorCode.UNSUPPORTED
    return caught.value


# --- adding patterns -------------------------------------------------------------------


def test_a_missing_file_and_directory_are_created(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.parent.mkdir(parents=True)  # .git exists; info/ does not

    changed = add_patterns(path, ["/.env.local"])

    assert path.read_text(encoding="utf-8") == block("/.env.local")
    assert changed is True


def test_a_missing_file_in_an_existing_directory_is_created(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)

    add_patterns(path, ["/.env.local"])

    assert path.read_text(encoding="utf-8") == block("/.env.local")


def test_the_block_holds_the_sorted_union_of_the_patterns(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)

    add_patterns(path, ["/.wtenv/", "/.env.local", "/compose.override.yaml", "/.env.local"])

    assert path.read_text(encoding="utf-8") == block(
        "/.env.local", "/.wtenv/", "/compose.override.yaml"
    )


def test_later_patterns_are_added_to_what_the_block_holds(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    add_patterns(path, ["/b", "/d"])

    changed = add_patterns(path, ["/c", "/a"])

    assert path.read_text(encoding="utf-8") == block("/a", "/b", "/c", "/d")
    assert changed is True


def test_every_other_line_is_kept_and_the_block_follows_them(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# git's own comment\n*.log\n\n/build/\n")

    add_patterns(path, ["/.env.local"])

    assert path.read_text(encoding="utf-8") == (
        "# git's own comment\n*.log\n\n/build/\n" + block("/.env.local")
    )


def test_a_file_without_a_final_line_break_gets_one_first(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"*.log")

    add_patterns(path, ["/.env.local"])

    assert path.read_text(encoding="utf-8") == "*.log\n" + block("/.env.local")


def test_adding_the_same_patterns_again_changes_nothing(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"*.log\n")
    add_patterns(path, ["/.env.local", "/.wtenv/"])
    first = path.read_bytes()
    first_mtime = path.stat().st_mtime_ns

    changed = add_patterns(path, ["/.wtenv/", "/.env.local"])

    assert changed is False
    assert path.read_bytes() == first
    assert path.stat().st_mtime_ns == first_mtime


def test_adding_never_removes_a_line(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    add_patterns(path, ["/from-another-worktree", "/.env.local"])

    add_patterns(path, ["/.env.local"])
    add_patterns(path, [])

    assert path.read_text(encoding="utf-8") == block("/.env.local", "/from-another-worktree")


def test_a_block_in_the_middle_is_rewritten_where_it_is(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(f"top\n{block('/b')}bottom\n", encoding="utf-8")

    add_patterns(path, ["/a"])

    assert path.read_text(encoding="utf-8") == f"top\n{block('/a', '/b')}bottom\n"


@pytest.mark.parametrize("trailer", [" ", "\t", "\r"])
def test_markers_are_recognised_with_trailing_whitespace_or_a_carriage_return(
    tmp_path: Path, trailer: str
) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(f"top\n{BEGIN}{trailer}\n/b\n{END}{trailer}\nbottom\n".encode())

    add_patterns(path, ["/a"])

    assert path.read_text(encoding="utf-8") == f"top\n{block('/a', '/b')}bottom\n"


def test_no_patterns_and_no_block_writes_nothing(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)

    changed = add_patterns(path, [])

    assert changed is False
    assert not path.exists()


def test_an_existing_file_keeps_its_mode(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("*.log\n", encoding="utf-8")
    path.chmod(0o640)

    add_patterns(path, ["/.env.local"])

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)

    add_patterns(path, ["/a"])
    add_patterns(path, ["/b"])

    assert [entry.name for entry in path.parent.iterdir()] == ["exclude"]


@pytest.mark.parametrize("pattern", ["", ".env.local", "build/", "*.log"])
def test_a_pattern_must_start_with_a_slash(tmp_path: Path, pattern: str) -> None:
    path = exclude_path(tmp_path)

    with pytest.raises(ValueError, match="start with"):
        add_patterns(path, [pattern])

    assert not path.exists()


# --- damaged markers -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"*.log\n{BEGIN}\n/a\n",
        f"*.log\n/a\n{END}\n",
        f"{block('/a')}{block('/b')}",
        f"{END}\n/a\n{BEGIN}\n",
    ],
    ids=["begin only", "end only", "two blocks", "end before begin"],
)
def test_damaged_markers_are_unsupported_and_leave_the_file_unchanged(
    tmp_path: Path, text: str
) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")

    error = damaged(path)

    assert error.details["reason"] == "markers_damaged"
    assert error.details["file"] == str(path)
    assert error.hint
    assert path.read_text(encoding="utf-8") == text


# --- removing the block (FR-085) -------------------------------------------------------


def test_removing_takes_out_the_markers_and_the_lines_between_them(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(f"top\n\n{block('/a', '/b')}bottom\n", encoding="utf-8")

    removed = remove_block(path)

    assert path.read_text(encoding="utf-8") == "top\n\nbottom\n"
    assert removed is True


def test_adding_and_then_removing_gives_back_the_original_bytes(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    original = b"# comment\n*.log\n\xff raw bytes\n"
    path.write_bytes(original)
    add_patterns(path, ["/.env.local", "/.wtenv/"])

    remove_block(path)

    assert path.read_bytes() == original


def test_removing_when_there_is_no_block_changes_nothing(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"*.log")

    removed = remove_block(path)

    assert removed is False
    assert path.read_bytes() == b"*.log"


def test_removing_from_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)

    assert remove_block(path) is False
    assert not path.exists()


def test_removing_with_damaged_markers_is_unsupported_and_changes_nothing(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(f"*.log\n{BEGIN}\n/a\n", encoding="utf-8")

    with pytest.raises(WtenvError) as caught:
        remove_block(path)

    assert caught.value.code is ErrorCode.UNSUPPORTED
    assert caught.value.details["reason"] == "markers_damaged"
    assert path.read_text(encoding="utf-8") == f"*.log\n{BEGIN}\n/a\n"


# --- listing only (FR-040) ---------------------------------------------------------------


def test_listing_only_says_whether_a_block_is_there_and_changes_nothing(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(("# mine\n" + block("/.env.local")).encode())
    before = path.read_bytes()

    assert remove_block(path, dry_run=True) is True
    assert path.read_bytes() == before
    assert remove_block(path) is True


def test_listing_only_without_a_block_or_a_file_is_false(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    assert remove_block(path, dry_run=True) is False
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# mine\n")
    assert remove_block(path, dry_run=True) is False


# --- a linked exclude file or `info` directory is never written through (T205; L6; FR-086) ----------


def linked_exclude(tmp_path: Path, *, directory: bool) -> tuple[Path, Path, bytes]:
    """Make `info/exclude` (or `info` itself) a link to something outside the repository.

    Returns the exclude path, the link, and the bytes of the file the link leads to.
    """
    path = exclude_path(tmp_path)
    git_dir = path.parent.parent
    git_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    data = (block("/x") + "mine\n").encode()
    (outside / "exclude").write_bytes(data)
    if directory:
        (git_dir / "info").symlink_to(outside)
        return path, git_dir / "info", data
    path.parent.mkdir()
    path.symlink_to(outside / "exclude")
    return path, path, data


@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_adding_patterns_through_a_link_raises_symlink_and_changes_nothing(
    tmp_path: Path, directory: bool
) -> None:
    path, link, data = linked_exclude(tmp_path, directory=directory)

    with pytest.raises(WtenvError) as caught:
        add_patterns(path, ["/.env.local"])

    assert caught.value.code is ErrorCode.ENV_FILE_UNUSABLE
    assert caught.value.details == {"path": str(link), "reason": "symlink"}
    assert (tmp_path / "outside" / "exclude").read_bytes() == data
    assert sorted(p.name for p in (tmp_path / "outside").iterdir()) == ["exclude"]


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_removing_the_block_through_a_link_raises_symlink_and_changes_nothing(
    tmp_path: Path, directory: bool, dry_run: bool
) -> None:
    path, link, data = linked_exclude(tmp_path, directory=directory)

    with pytest.raises(WtenvError) as caught:
        remove_block(path, dry_run=dry_run)

    assert caught.value.code is ErrorCode.ENV_FILE_UNUSABLE
    assert caught.value.details == {"path": str(link), "reason": "symlink"}
    assert (tmp_path / "outside" / "exclude").read_bytes() == data


def test_a_dangling_link_counts_too(tmp_path: Path) -> None:
    path = exclude_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "nowhere")

    with pytest.raises(WtenvError) as caught:
        add_patterns(path, ["/.env.local"])

    assert caught.value.details["reason"] == "symlink"
    assert not (tmp_path / "nowhere").exists()  # nothing was created through it


def test_a_linked_git_directory_above_info_is_the_developers_own_layout_and_is_allowed(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real-git"
    (real / "info").mkdir(parents=True)
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".git").symlink_to(real)

    changed = add_patterns(exclude_path(tmp_path), ["/.env.local"])

    assert changed and (real / "info" / "exclude").read_text() == block("/.env.local")
