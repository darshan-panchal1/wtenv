"""Removing wtenv's section from the env file, and finding duplicate variables (files.md; FR-038,
FR-042, FR-079, FR-080, FR-081)."""

from pathlib import Path

import pytest

from wtenv.envfile import find_duplicates, remove_section, write_section
from wtenv.errors import ErrorCode, WtenvError

BEGIN = "# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>"
END = "# <<< wtenv managed <<<"
PORTS = [("PORT", "20010"), ("API_PORT", "20011")]


# --- removing the section (FR-079) -----------------------------------------------------


def test_the_section_and_both_markers_are_removed_and_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(f"A=1\n\nB=2\n{BEGIN}\nPORT=1\n{END}\nC=3\n\n".encode())

    result = remove_section(path)

    assert path.read_bytes() == b"A=1\n\nB=2\nC=3\n\n"
    assert (result.removed, result.deleted_file) == (True, False)


def test_a_line_break_that_wtenv_added_is_removed_again(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"SECRET=abc")
    write_section(path, PORTS)

    remove_section(path, added_newline=True)

    assert path.read_bytes() == b"SECRET=abc"


def test_an_added_line_break_stays_when_lines_follow_the_section(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"SECRET=abc")
    write_section(path, PORTS)
    with path.open("ab") as file:
        file.write(b"LATER=3\n")

    remove_section(path, added_newline=True)

    assert path.read_bytes() == b"SECRET=abc\nLATER=3\n"


def test_without_the_record_the_line_break_stays(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"SECRET=abc")
    write_section(path, PORTS)

    remove_section(path, added_newline=False)

    assert path.read_bytes() == b"SECRET=abc\n"


def test_a_section_the_developer_moved_is_removed_where_it_is(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"TOP=1\n{BEGIN}\nPORT=1\n{END}\nBOTTOM=2\n", encoding="utf-8")

    remove_section(path)

    assert path.read_text(encoding="utf-8") == "TOP=1\nBOTTOM=2\n"


def test_markers_with_trailing_whitespace_are_recognised(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(f"A=1\n{BEGIN} \r\nPORT=1\n{END}\t\r\nB=2\n".encode())

    remove_section(path)

    assert path.read_bytes() == b"A=1\nB=2\n"


# --- deleting a file wtenv created (FR-038) --------------------------------------------


def test_a_file_wtenv_created_that_holds_nothing_else_is_deleted(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    write_section(path, PORTS)

    result = remove_section(path, created_file=True)

    assert not path.exists()
    assert (result.removed, result.deleted_file) == (True, True)


def test_a_file_wtenv_created_that_holds_other_lines_is_kept(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    write_section(path, PORTS)
    with path.open("a", encoding="utf-8") as file:
        file.write("MINE=1\n")

    result = remove_section(path, created_file=True)

    assert path.read_text(encoding="utf-8") == "MINE=1\n"
    assert result.deleted_file is False


def test_a_file_the_developer_had_is_kept_even_when_nothing_is_left(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"")
    write_section(path, PORTS)

    result = remove_section(path, created_file=False)

    assert path.read_bytes() == b""
    assert result.deleted_file is False


# --- nothing to remove (FR-042) --------------------------------------------------------


def test_a_missing_file_is_already_absent(tmp_path: Path) -> None:
    result = remove_section(tmp_path / ".env.local", created_file=True)

    assert (result.removed, result.deleted_file) == (False, False)


def test_a_file_without_a_section_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"A=1\nB=2")

    result = remove_section(path, created_file=True, added_newline=True)

    assert path.read_bytes() == b"A=1\nB=2"
    assert (result.removed, result.deleted_file) == (False, False)


# --- damaged markers (FR-081) ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"A=1\n{BEGIN}\nPORT=1\n",
        f"A=1\nPORT=1\n{END}\n",
        f"{BEGIN}\n{END}\n{BEGIN}\n{END}\n",
    ],
    ids=["begin only", "end only", "two sections"],
)
def test_damaged_markers_leave_the_file_unchanged_and_fail(tmp_path: Path, text: str) -> None:
    path = tmp_path / ".env.local"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(WtenvError) as caught:
        remove_section(path, created_file=True)

    assert caught.value.code is ErrorCode.ENV_FILE_UNUSABLE
    assert caught.value.details == {"path": str(path), "reason": "markers_damaged"}
    assert path.read_text(encoding="utf-8") == text


# --- duplicates (FR-080) ---------------------------------------------------------------


def test_a_managed_variable_defined_outside_the_section_is_reported(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"PORT=3000\n{BEGIN}\nPORT=20010\nAPI_PORT=20011\n{END}\n", encoding="utf-8")

    assert find_duplicates(path, ["PORT", "API_PORT"]) == ["PORT"]


def test_a_variable_defined_only_inside_the_section_is_not_a_duplicate(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    write_section(path, PORTS)

    assert find_duplicates(path, ["PORT", "API_PORT"]) == []


def test_the_line_that_duplicates_a_variable_is_left_unchanged(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"PORT=3000\n{BEGIN}\nPORT=20010\n{END}\n", encoding="utf-8")
    before = path.read_bytes()

    find_duplicates(path, ["PORT"])

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "line",
    ["PORT=3000", "export PORT=3000", "  PORT = 3000", "PORT=", "\texport   PORT='x'"],
)
def test_the_usual_ways_of_defining_a_variable_are_found(tmp_path: Path, line: str) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"{line}\n", encoding="utf-8")

    assert find_duplicates(path, ["PORT"]) == ["PORT"]


@pytest.mark.parametrize(
    "line", ["# PORT=3000", "OTHER_PORT=3000", "PORTS=1", "PORT_X=1", "echo PORT=1", "PORT"]
)
def test_lines_that_do_not_define_the_variable_are_not_duplicates(
    tmp_path: Path, line: str
) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"{line}\n", encoding="utf-8")

    assert find_duplicates(path, ["PORT"]) == []


def test_duplicates_are_reported_once_each_in_the_order_asked(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text("B=1\nA=2\nB=3\nC=4\n", encoding="utf-8")

    assert find_duplicates(path, ["A", "B", "Z"]) == ["A", "B"]


def test_a_file_without_a_section_is_searched_whole(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text("PORT=3000\n", encoding="utf-8")

    assert find_duplicates(path, ["PORT"]) == ["PORT"]


def test_a_missing_file_has_no_duplicates(tmp_path: Path) -> None:
    assert find_duplicates(tmp_path / ".env.local", ["PORT"]) == []


def test_duplicates_in_a_file_with_damaged_markers_fail(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"PORT=1\n{BEGIN}\n", encoding="utf-8")

    with pytest.raises(WtenvError) as caught:
        find_duplicates(path, ["PORT"])

    assert caught.value.details["reason"] == "markers_damaged"


# --- listing only (FR-040): the same answer, and nothing written ------------------------


def test_listing_only_returns_what_a_removal_would_do_and_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(f"A=1\n{BEGIN}\nPORT=1\n{END}\nC=3\n".encode())
    before = path.read_bytes()

    planned = remove_section(path, dry_run=True)

    assert path.read_bytes() == before
    assert remove_section(path) == planned
    assert (planned.removed, planned.deleted_file) == (True, False)


def test_listing_only_says_that_a_file_wtenv_created_would_be_deleted(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    write_section(path, PORTS)

    planned = remove_section(path, created_file=True, dry_run=True)

    assert path.exists()
    assert (planned.removed, planned.deleted_file) == (True, True)


def test_listing_only_for_a_missing_file_or_section_is_already_absent(tmp_path: Path) -> None:
    missing = tmp_path / "missing.env"
    plain = tmp_path / ".env.local"
    plain.write_bytes(b"A=1\n")

    for path in (missing, plain):
        planned = remove_section(path, dry_run=True)
        assert (planned.removed, planned.deleted_file) == (False, False)


def test_listing_only_still_refuses_damaged_markers(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(f"A=1\n{BEGIN}\nPORT=1\n".encode())

    with pytest.raises(WtenvError) as caught:
        remove_section(path, dry_run=True)

    assert caught.value.code is ErrorCode.ENV_FILE_UNUSABLE
    assert caught.value.details["reason"] == "markers_damaged"
