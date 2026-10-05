"""Loading `wtenv.toml`: the `[compose]` table (config.md; FR-028, FR-030, FR-064; research.md §4,
v1 limits)."""

from pathlib import Path

import pytest

from wtenv.config import ComposeConfig, load_config
from wtenv.errors import ErrorCode, WtenvError


def write_config(root: Path, text: str) -> None:
    (root / "wtenv.toml").write_text(text, encoding="utf-8")


def compose_table(file: str) -> str:
    return f'[compose]\nfile = "{file}"\n'


def invalid(root: Path) -> WtenvError:
    """Load the configuration in `root` and return the `config_invalid` error it raises."""
    with pytest.raises(WtenvError) as caught:
        load_config(root)
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    return caught.value


def test_without_the_table_there_is_no_compose(tmp_path: Path) -> None:
    assert load_config(tmp_path).compose is None


@pytest.mark.parametrize(
    "file",
    [
        "compose.yaml",
        "compose.yml",
        "docker-compose.yaml",
        "docker-compose.yml",
        "deploy/compose.yaml",
        "./services/docker-compose.yml",
    ],
)
def test_the_four_default_file_names_are_accepted(tmp_path: Path, file: str) -> None:
    write_config(tmp_path, compose_table(file))

    config = load_config(tmp_path)

    assert config.compose is not None
    assert config.compose.file.endswith(Path(file).name)


def test_the_file_is_kept_in_its_plain_form(tmp_path: Path) -> None:
    write_config(tmp_path, compose_table("./deploy/../compose.yaml"))

    assert load_config(tmp_path).compose == ComposeConfig(file="compose.yaml")


@pytest.mark.parametrize(
    "file",
    ["stack.yaml", "compose.override.yaml", "my-compose.yml", "compose.json", "docker-compose"],
)
def test_any_other_file_name_is_config_invalid_naming_compose_file(
    tmp_path: Path, file: str
) -> None:
    write_config(tmp_path, compose_table(file))

    error = invalid(tmp_path)

    assert error.details["setting"] == "compose.file"
    assert "compose.yaml" in error.message


def test_the_file_is_required(tmp_path: Path) -> None:
    write_config(tmp_path, "[compose]\n")

    assert invalid(tmp_path).details["setting"] == "compose.file"


@pytest.mark.parametrize(
    "file", ["", "/abs/compose.yaml", "../compose.yaml", "a/../../compose.yaml"]
)
def test_the_file_must_be_a_relative_path_inside_the_worktree(tmp_path: Path, file: str) -> None:
    write_config(tmp_path, compose_table(file))

    assert invalid(tmp_path).details["setting"] == "compose.file"


def test_the_file_must_be_a_string(tmp_path: Path) -> None:
    write_config(tmp_path, "[compose]\nfile = 3\n")

    assert invalid(tmp_path).details["setting"] == "compose.file"


def test_an_unknown_compose_key_is_config_invalid(tmp_path: Path) -> None:
    write_config(tmp_path, '[compose]\nfile = "compose.yaml"\nproject = "x"\n')

    assert invalid(tmp_path).details["setting"] == "compose.project"
