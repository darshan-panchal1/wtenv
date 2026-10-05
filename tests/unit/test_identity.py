"""`points_to`, the identity parser, `short_id`, and `slug` (data-model.md; files.md, Names)."""

import os
from pathlib import Path

import pytest

from wtenv.identity import WorktreeIdentity, parse_identity, points_to, short_id, slug

# --- points_to ------------------------------------------------------------------------


def test_points_to_a_git_directory_is_that_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    assert points_to(tmp_path) == os.path.realpath(tmp_path / ".git")


def test_points_to_reads_an_absolute_gitdir_file(tmp_path: Path) -> None:
    git_dir = tmp_path / "repo" / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    worktree = tmp_path / "feature"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n")

    assert points_to(worktree) == os.path.realpath(git_dir)


def test_points_to_resolves_a_relative_gitdir_against_the_worktree(tmp_path: Path) -> None:
    git_dir = tmp_path / "repo" / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    worktree = tmp_path / "feature"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../repo/.git/worktrees/feature\n")

    assert points_to(worktree) == os.path.realpath(git_dir)


def test_points_to_keeps_spaces_in_the_gitdir_path(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /some where/.git/worktrees/wt\n")

    assert points_to(worktree) == "/some where/.git/worktrees/wt"


def test_points_to_follows_symbolic_links_like_the_identity_does(tmp_path: Path) -> None:
    real = tmp_path / "real" / ".git"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real")

    assert points_to(link) == os.path.realpath(real)


def test_points_to_nothing_when_there_is_no_dot_git(tmp_path: Path) -> None:
    assert points_to(tmp_path) is None


def test_points_to_nothing_when_the_directory_is_missing(tmp_path: Path) -> None:
    assert points_to(tmp_path / "does-not-exist") is None


def test_points_to_nothing_when_dot_git_is_not_readable(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can read any file")
    dot_git = tmp_path / ".git"
    dot_git.write_text("gitdir: /x\n")
    dot_git.chmod(0o000)

    assert points_to(tmp_path) is None


def test_points_to_nothing_when_dot_git_is_not_a_gitdir_file(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("this is not a gitdir line\n")

    assert points_to(tmp_path) is None


def test_points_to_nothing_when_dot_git_is_not_text(tmp_path: Path) -> None:
    (tmp_path / ".git").write_bytes(b"\xff\xfe\x00gitdir")

    assert points_to(tmp_path) is None


# --- the identity parser --------------------------------------------------------------


def test_the_identity_parser_maps_the_three_lines(tmp_path: Path) -> None:
    git_dir = tmp_path / "app" / ".git" / "worktrees" / "feature"
    path = tmp_path / "feature"
    repository = tmp_path / "app" / ".git"
    text = f"{git_dir}\n{path}\n{repository}\n"

    identity = parse_identity(text)

    assert identity == WorktreeIdentity(
        git_dir=os.path.realpath(git_dir),
        path=os.path.realpath(path),
        repository=os.path.realpath(repository),
    )


def test_the_identity_parser_resolves_symbolic_links(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / ".git").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    text = f"{link}/.git\n{link}\n{link}/.git\n"

    identity = parse_identity(text)

    assert identity.git_dir == os.path.realpath(real / ".git")
    assert identity.path == os.path.realpath(real)
    assert identity.repository == os.path.realpath(real / ".git")


def test_the_identity_parser_does_not_need_a_final_line_break(tmp_path: Path) -> None:
    text = f"{tmp_path}/a\n{tmp_path}/b\n{tmp_path}/c"

    assert parse_identity(text).repository == os.path.realpath(tmp_path / "c")


@pytest.mark.parametrize("text", ["", "/only/one\n", "/a\n/b\n/c\n/d\n"])
def test_the_identity_parser_rejects_output_that_is_not_three_lines(text: str) -> None:
    with pytest.raises(ValueError, match="three lines"):
        parse_identity(text)


# --- short_id -------------------------------------------------------------------------

GIT_DIR = "/code/app/.git/worktrees/feature-x"
GIT_DIR_SHA256 = "e7e51217aacb419808fe2052510f7912c14a9c382b6d80d6476b597e49993c66"


def test_short_id_is_the_first_digits_of_the_sha256_of_the_git_directory() -> None:
    assert short_id(GIT_DIR, 8) == GIT_DIR_SHA256[:8] == "e7e51217"
    assert short_id(GIT_DIR, 16) == GIT_DIR_SHA256[:16] == "e7e51217aacb4198"


def test_short_id_is_the_same_every_time_and_differs_between_directories() -> None:
    assert short_id(GIT_DIR, 8) == short_id(GIT_DIR, 8)
    assert short_id(GIT_DIR, 8) != short_id("/tmp/x/.git", 8)


# --- slug -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "separator", "expected"),
    [
        ("feature-x", "_", "feature_x"),
        ("feature-x", "-", "feature-x"),
        ("Feature-X", "_", "feature_x"),
        ("Feature_X", "-", "feature-x"),
        ("feature/login (v2)", "_", "feature_login_v2"),
        ("feature/login (v2)", "-", "feature-login-v2"),
        ("a---b___c", "_", "a_b_c"),
        ("__lead and trail__", "_", "lead_and_trail"),
        ("--lead and trail--", "-", "lead-and-trail"),
        ("ticket-1234", "_", "ticket_1234"),
        ("über-wörk", "_", "ber_w_rk"),
    ],
)
def test_slug_lowercases_and_replaces_each_run_of_other_characters(
    name: str, separator: str, expected: str
) -> None:
    assert slug(name, separator) == expected


@pytest.mark.parametrize("name", ["", "---", "___", " /.", "日本語"])
@pytest.mark.parametrize("separator", ["_", "-"])
def test_an_empty_slug_becomes_wt(name: str, separator: str) -> None:
    assert slug(name, separator) == "wt"


def test_slug_is_cut_to_40_characters() -> None:
    assert slug("a" * 60, "_") == "a" * 40
    assert slug("a" * 40, "_") == "a" * 40
    # The cut at 40 lands right after a "-"; the trim after the cut removes it.
    assert slug("word-" * 20, "-") == "word-" * 7 + "word"


def test_slug_is_trimmed_again_after_the_cut() -> None:
    # The cut can land right after a separator; that separator is removed too.
    assert slug("a" * 39 + "-b", "_") == "a" * 39
    assert slug("a" * 39 + "-b", "-") == "a" * 39


@pytest.mark.parametrize("separator", ["_", "-"])
def test_slug_cut_after_a_run_of_separators_has_no_trailing_separator(separator: str) -> None:
    # Name characters other than a-z and 0-9 collapse to one separator, so the cut can land
    # on that one separator only; the result must still end in a letter or digit.
    result = slug("a" * 39 + " /// cd", separator)

    assert result == "a" * 39
    assert not result.endswith(separator)


@pytest.mark.parametrize("separator", ["_", "-"])
@pytest.mark.parametrize("length", range(1, 60))
def test_slug_is_always_valid_and_never_empty(length: int, separator: str) -> None:
    name = "x" * length + "-" + "y" * 5
    result = slug(name, separator)

    assert 0 < len(result) <= 40
    assert result[0] not in "_-"
    assert result[-1] not in "_-"
