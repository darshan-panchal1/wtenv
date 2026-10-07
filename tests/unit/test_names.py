"""Names of the databases wtenv creates (files.md, Names; FR-022)."""

import re
from pathlib import Path

from wtenv.database import (
    SQLITE_DIR,
    postgres_database_name,
    sqlite_copy_path,
    sqlite_template_path,
)
from wtenv.identity import short_id, slug


def test_the_postgres_name_is_wtenv_slug_and_id8() -> None:
    git_dir = "/code/app/.git/worktrees/feature-x"

    name = postgres_database_name("Feature-X", git_dir)

    assert name == f"wtenv_feature_x_{short_id(git_dir, 8)}"
    assert name == f"wtenv_{slug('Feature-X', '_')}_{short_id(git_dir, 8)}"


def test_the_postgres_name_matches_the_documented_example_shape() -> None:
    name = postgres_database_name("feature x!", "/some/git/dir")

    assert re.fullmatch(r"wtenv_feature_x_[0-9a-f]{8}", name)


def test_an_empty_slug_becomes_wt() -> None:
    assert postgres_database_name("!!!", "/g").startswith("wtenv_wt_")


def test_a_long_worktree_name_fits_a_postgres_identifier() -> None:
    name = postgres_database_name("x" * 200, "/g")

    assert len(name) <= 63


def test_the_same_inputs_give_the_same_name_and_other_git_dirs_do_not() -> None:
    first = postgres_database_name("app", "/g/one")

    assert first == postgres_database_name("app", "/g/one")
    assert first != postgres_database_name("app", "/g/two")


def test_the_sqlite_copy_is_in_the_wtenv_directory_under_the_template_file_name(
    tmp_path: Path,
) -> None:
    copy = sqlite_copy_path(tmp_path, "db/dev.sqlite3")

    assert copy == tmp_path / ".wtenv" / "dev.sqlite3"
    assert SQLITE_DIR == ".wtenv"


def test_an_absolute_template_gives_the_same_copy_name(tmp_path: Path) -> None:
    assert sqlite_copy_path(tmp_path, "/data/templates/app.db") == tmp_path / ".wtenv" / "app.db"


def test_a_relative_template_is_resolved_against_the_worktree_root(tmp_path: Path) -> None:
    assert sqlite_template_path(tmp_path, "db/dev.sqlite3") == tmp_path / "db" / "dev.sqlite3"


def test_an_absolute_template_stays_as_it_is(tmp_path: Path) -> None:
    assert sqlite_template_path(tmp_path, "/data/app.db") == Path("/data/app.db")
