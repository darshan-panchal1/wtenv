"""Writing and reading wtenv's section of the env file (files.md, Env file section; FR-016, FR-017,
FR-019, FR-079, FR-081; reading R1)."""

import stat
from pathlib import Path

import pytest

from wtenv import envfile
from wtenv.envfile import read_section, write_section
from wtenv.errors import ErrorCode, WtenvError

BEGIN = "# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>"
END = "# <<< wtenv managed <<<"

# What each variable looks like when the section is written (files.md).
PORTS = [("PORT", "20010"), ("API_PORT", "20011")]
SECTION = f"{BEGIN}\nPORT=20010\nAPI_PORT=20011\n{END}\n"


def unusable(path: Path, reason: str) -> None:
    """Assert that using the section of `path` fails with `env_file_unusable` for `reason`."""
    with pytest.raises(WtenvError) as caught:
        write_section(path, PORTS)
    assert caught.value.code is ErrorCode.ENV_FILE_UNUSABLE
    assert caught.value.details["reason"] == reason
    assert caught.value.details["path"] == str(path)


# --- creating the file (FR-016, FR-019) -----------------------------------------------


def test_a_missing_file_is_created_with_only_the_section(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"

    result = write_section(path, PORTS)

    assert path.read_text(encoding="utf-8") == SECTION
    assert (result.action, result.created_file, result.added_newline) == ("created", True, False)


def test_a_created_file_has_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"

    write_section(path, PORTS)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    write_section(tmp_path / ".env.local", PORTS)
    write_section(tmp_path / ".env.local", [("PORT", "20020")])

    assert [entry.name for entry in tmp_path.iterdir()] == [".env.local"]


def test_the_variables_are_written_in_the_order_given(tmp_path: Path) -> None:
    path = tmp_path / ".env"

    write_section(path, [("Z", "1"), ("A", "2"), ("M", "3")])

    assert path.read_text(encoding="utf-8") == f"{BEGIN}\nZ=1\nA=2\nM=3\n{END}\n"


# --- appending to the developer's file (FR-016, FR-079) --------------------------------


def test_developer_lines_are_kept_and_the_section_follows_them(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"# mine\nSECRET=abc\n\nOTHER = 'x y'\n")

    result = write_section(path, PORTS)

    assert path.read_bytes() == b"# mine\nSECRET=abc\n\nOTHER = 'x y'\n" + SECTION.encode()
    assert (result.action, result.created_file, result.added_newline) == ("created", False, False)


def test_a_file_without_a_final_line_break_gets_one_first(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"SECRET=abc")

    result = write_section(path, PORTS)

    assert path.read_bytes() == b"SECRET=abc\n" + SECTION.encode()
    assert result.added_newline is True


def test_an_empty_existing_file_gets_no_extra_line_break(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"")

    result = write_section(path, PORTS)

    assert path.read_bytes() == SECTION.encode()
    assert (result.created_file, result.added_newline) == (False, False)


def test_bytes_outside_the_section_are_preserved_exactly(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    original = b"A=1\r\n\xff\xfe not utf-8\r\n\t  indented = yes  \n"
    path.write_bytes(original)

    write_section(path, PORTS)

    assert path.read_bytes().startswith(original)


def test_an_existing_file_keeps_its_mode(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text("A=1\n", encoding="utf-8")
    path.chmod(0o644)

    write_section(path, PORTS)

    assert stat.S_IMODE(path.stat().st_mode) == 0o644


# --- rewriting an existing section (FR-017, FR-079) ------------------------------------


def test_the_same_values_twice_give_identical_bytes(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"A=1\n")
    write_section(path, PORTS)
    first = path.read_bytes()
    first_mtime = path.stat().st_mtime_ns

    result = write_section(path, PORTS)

    assert path.read_bytes() == first
    assert result.action == "unchanged"
    assert path.stat().st_mtime_ns == first_mtime


def test_new_values_rewrite_the_section_in_place(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(b"A=1\n")
    write_section(path, PORTS)

    result = write_section(path, [("PORT", "20050")])

    assert path.read_text(encoding="utf-8") == f"A=1\n{BEGIN}\nPORT=20050\n{END}\n"
    assert (result.action, result.created_file, result.added_newline) == ("updated", False, False)


def test_a_section_the_developer_moved_is_rewritten_where_it_is(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"TOP=1\n{BEGIN}\nPORT=1\n{END}\nBOTTOM=2\n", encoding="utf-8")

    write_section(path, PORTS)

    assert path.read_text(encoding="utf-8") == f"TOP=1\n{SECTION}BOTTOM=2\n"


def test_lines_added_after_the_section_stay_after_it(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    write_section(path, PORTS)
    with path.open("a", encoding="utf-8") as file:
        file.write("LATER=3\n")

    write_section(path, [("PORT", "20099")])

    assert path.read_text(encoding="utf-8") == f"{BEGIN}\nPORT=20099\n{END}\nLATER=3\n"


def test_lines_edited_inside_the_section_are_restored(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"{BEGIN}\nPORT=1\nINTRUDER=yes\n# note\n{END}\n", encoding="utf-8")

    result = write_section(path, PORTS)

    assert path.read_text(encoding="utf-8") == SECTION
    assert result.action == "updated"


def test_the_final_line_break_of_the_end_marker_is_restored(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"A=1\n{BEGIN}\nPORT=1\n{END}", encoding="utf-8")

    write_section(path, PORTS)

    assert path.read_text(encoding="utf-8") == f"A=1\n{SECTION}"


@pytest.mark.parametrize("trailer", [" ", "\t ", "\r", "  \r"])
def test_markers_are_recognised_with_trailing_whitespace_or_a_carriage_return(
    tmp_path: Path, trailer: str
) -> None:
    path = tmp_path / ".env.local"
    path.write_bytes(f"A=1\n{BEGIN}{trailer}\nPORT=1\n{END}{trailer}\nB=2\n".encode())

    write_section(path, PORTS)

    assert path.read_text(encoding="utf-8") == f"A=1\n{SECTION}B=2\n"


# --- damaged markers (FR-081) ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"A=1\n{BEGIN}\nPORT=1\n",
        f"A=1\nPORT=1\n{END}\n",
        f"{BEGIN}\nPORT=1\n{END}\n{BEGIN}\nPORT=2\n{END}\n",
        f"{END}\nPORT=1\n{BEGIN}\n",
        f"{BEGIN}\n{BEGIN}\nPORT=1\n{END}\n",
        f"{BEGIN}\nPORT=1\n{END}\n{END}\n",
    ],
    ids=["begin only", "end only", "two sections", "end before begin", "two begins", "two ends"],
)
def test_damaged_markers_fail_and_leave_the_file_unchanged(tmp_path: Path, text: str) -> None:
    path = tmp_path / ".env.local"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()

    unusable(path, "markers_damaged")

    assert path.read_bytes() == before


# --- quoting (files.md) ----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "20010",
        "abc_DEF-123",
        "a.b/c:d@e%f+g=h,i~j-k",
        "postgresql://myapp:s3cr%3Ft@localhost:5432/wtenv_feature_x_3f9a1c2b",
        "sqlite:////code/feature-x/.wtenv/dev.sqlite3",
    ],
)
def test_a_value_of_safe_characters_is_written_bare(tmp_path: Path, value: str) -> None:
    path = tmp_path / ".env"

    write_section(path, [("V", value)])

    assert f"\nV={value}\n" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "two words",
        "a#b",
        "$HOME",
        'say "hi"',
        "a?b=c&d",
        "x;y",
        "(x)",
        "ünï",
        "tab\there",
        "a\\b",
    ],
)
def test_any_other_value_is_written_in_single_quotes(tmp_path: Path, value: str) -> None:
    path = tmp_path / ".env"

    write_section(path, [("V", value)])

    assert f"\nV='{value}'\n" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["it's", "two\nlines", "carriage\rreturn"])
def test_a_value_with_a_single_quote_or_a_line_break_is_refused(tmp_path: Path, value: str) -> None:
    path = tmp_path / ".env"

    with pytest.raises(ValueError, match="V"):
        write_section(path, [("V", value)])

    assert not path.exists()


# --- links ----------------------------------------------------------------------------


def test_a_symbolic_link_is_refused_with_the_reason_symlink_naming_the_link(
    tmp_path: Path,
) -> None:
    link = tmp_path / ".env.local"

    error = envfile.unusable(link, "symlink")

    assert error.code is ErrorCode.ENV_FILE_UNUSABLE
    assert error.details == {"path": str(link), "reason": "symlink"}
    assert str(link) in error.message
    assert error.hint is not None


# --- reading (reading R1) --------------------------------------------------------------


def test_what_was_written_is_read_back_unchanged(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    values = [
        ("PORT", "20010"),
        ("DATABASE_URL", "postgresql://u:p%40ss@localhost:5432/db"),
        ("SPACED", "two words and #hash"),
        ("EMPTY", ""),
        ("WEIRD", '$HOME;(x) "y" ünï\\z'),
        ("EQUALS", "a=b=c"),
    ]
    write_section(path, values)

    assert read_section(path) == values


def test_lines_outside_the_section_are_not_returned(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"BEFORE=1\n{BEGIN}\nPORT=20010\n{END}\nAFTER=2\n", encoding="utf-8")

    assert read_section(path) == [("PORT", "20010")]


def test_variables_are_returned_in_file_order(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"{BEGIN}\nB=2\nA=1\nC=3\n{END}\n", encoding="utf-8")

    assert [name for name, _ in read_section(path)] == ["B", "A", "C"]


def test_a_value_edited_by_hand_is_returned_as_it_stands(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(
        f"{BEGIN}\nPORT=9999\nNAME=\"double quoted\"\nLOOSE=a b\nQ='one'\n{END}\n",
        encoding="utf-8",
    )

    assert read_section(path) == [
        ("PORT", "9999"),
        ("NAME", '"double quoted"'),
        ("LOOSE", "a b"),
        ("Q", "one"),
    ]


def test_blank_and_comment_lines_inside_the_section_are_not_variables(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"{BEGIN}\n\n# note\nPORT=1\n   \n{END}\n", encoding="utf-8")

    assert read_section(path) == [("PORT", "1")]


def test_an_empty_section_has_no_variables(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(f"{BEGIN}\n{END}\n", encoding="utf-8")

    assert read_section(path) == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (f"{BEGIN}\nPORT=1\n", "markers_damaged"),
        (f"PORT=1\n{END}\n", "markers_damaged"),
        (f"{BEGIN}\n{END}\n{BEGIN}\n{END}\n", "markers_damaged"),
        ("PORT=1\n", "no_section"),
        ("", "no_section"),
    ],
)
def test_reading_a_file_that_cannot_give_a_section_fails(
    tmp_path: Path, text: str, reason: str
) -> None:
    path = tmp_path / ".env.local"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(WtenvError) as caught:
        read_section(path)

    assert caught.value.code is ErrorCode.ENV_FILE_UNUSABLE
    assert caught.value.details == {"path": str(path), "reason": reason}


def test_reading_a_missing_file_fails_with_reason_missing(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"

    with pytest.raises(WtenvError) as caught:
        read_section(path)

    assert caught.value.code is ErrorCode.ENV_FILE_UNUSABLE
    assert caught.value.details == {"path": str(path), "reason": "missing"}
