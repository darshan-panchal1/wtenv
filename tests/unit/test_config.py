"""Loading `wtenv.toml`: `ports`, `block_size`, and `env_file` (config.md; FR-005, FR-062 to FR-064)."""

from pathlib import Path

import pytest

from wtenv.config import Config, load_config
from wtenv.errors import ErrorCode, WtenvError


def write_config(root: Path, text: str) -> Path:
    path = root / "wtenv.toml"
    path.write_text(text, encoding="utf-8")
    return path


def invalid(root: Path) -> WtenvError:
    """Load the configuration in `root` and return the `config_invalid` error it raises."""
    with pytest.raises(WtenvError) as caught:
        load_config(root)
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    return caught.value


# --- defaults -------------------------------------------------------------------------


def test_no_file_gives_the_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config == Config(ports=["PORT"], block_size=10, env_file=".env.local")
    assert (config.ports, config.block_size, config.env_file) == (["PORT"], 10, ".env.local")


def test_an_empty_file_gives_the_defaults(tmp_path: Path) -> None:
    write_config(tmp_path, "")

    assert load_config(tmp_path) == Config()


def test_every_setting_can_be_given(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        'ports = ["PORT", "API_PORT"]\nblock_size = 25\nenv_file = "config/.env"\n',
    )

    config = load_config(tmp_path)

    assert config.ports == ["PORT", "API_PORT"]
    assert config.block_size == 25
    assert config.env_file == "config/.env"


def test_a_setting_left_out_keeps_its_default(tmp_path: Path) -> None:
    write_config(tmp_path, "block_size = 20\n")

    config = load_config(tmp_path)

    assert (config.ports, config.block_size, config.env_file) == (["PORT"], 20, ".env.local")


def test_the_file_is_read_from_the_given_root_only(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    write_config(tmp_path / "sub", "block_size = 20\n")

    assert load_config(tmp_path).block_size == 10


# --- ports ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["PORT", "port", "_PORT", "API_PORT_2", "a", "_", "Port9"])
def test_valid_port_names_are_accepted(tmp_path: Path, name: str) -> None:
    write_config(tmp_path, f'ports = ["{name}"]\n')

    assert load_config(tmp_path).ports == [name]


@pytest.mark.parametrize("name", ["", "1PORT", "MY-PORT", "MY PORT", "PORT=1", "PÖRT", "A.B"])
def test_a_port_name_must_be_a_variable_name(tmp_path: Path, name: str) -> None:
    write_config(tmp_path, f'ports = ["PORT", "{name}"]\n')

    assert invalid(tmp_path).details["setting"] == "ports"


def test_ports_needs_at_least_one_name(tmp_path: Path) -> None:
    write_config(tmp_path, "ports = []\n")

    assert invalid(tmp_path).details["setting"] == "ports"


def test_port_names_must_be_unique(tmp_path: Path) -> None:
    write_config(tmp_path, 'ports = ["PORT", "API_PORT", "PORT"]\n')

    error = invalid(tmp_path)

    assert error.details["setting"] == "ports"
    assert "PORT" in error.message


def test_database_url_is_not_a_port_name(tmp_path: Path) -> None:
    write_config(tmp_path, 'ports = ["PORT", "DATABASE_URL"]\n')

    error = invalid(tmp_path)

    assert error.details["setting"] == "ports"
    assert "DATABASE_URL" in error.message


def test_ports_must_be_a_list_of_strings(tmp_path: Path) -> None:
    write_config(tmp_path, "ports = [1, 2]\n")

    assert invalid(tmp_path).details["setting"] == "ports"


def test_ports_must_be_a_list(tmp_path: Path) -> None:
    write_config(tmp_path, 'ports = "PORT"\n')

    assert invalid(tmp_path).details["setting"] == "ports"


# --- block_size -----------------------------------------------------------------------


@pytest.mark.parametrize("size", [1, 10, 1000])
def test_block_sizes_from_1_to_1000_are_accepted(tmp_path: Path, size: int) -> None:
    write_config(tmp_path, f"block_size = {size}\n")

    assert load_config(tmp_path).block_size == size


@pytest.mark.parametrize("value", ["0", "-5", "1001", '"10"', "10.0", "true"])
def test_block_size_must_be_a_whole_number_from_1_to_1000(tmp_path: Path, value: str) -> None:
    write_config(tmp_path, f"block_size = {value}\n")

    assert invalid(tmp_path).details["setting"] == "block_size"


# --- env_file -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        (".env", ".env"),
        ("config/.env.dev", "config/.env.dev"),
        ("./.env", ".env"),
        ("a/../.env", ".env"),
        ("a//b/.env", "a/b/.env"),
    ],
)
def test_env_file_is_a_relative_path_inside_the_worktree(
    tmp_path: Path, given: str, kept: str
) -> None:
    write_config(tmp_path, f'env_file = "{given}"\n')

    assert load_config(tmp_path).env_file == kept


@pytest.mark.parametrize("given", ["/etc/app.env", "../.env", "a/../../.env", "..", ""])
def test_env_file_may_not_leave_the_worktree(tmp_path: Path, given: str) -> None:
    write_config(tmp_path, f'env_file = "{given}"\n')

    assert invalid(tmp_path).details["setting"] == "env_file"


def test_env_file_must_be_a_string(tmp_path: Path) -> None:
    write_config(tmp_path, "env_file = 3\n")

    assert invalid(tmp_path).details["setting"] == "env_file"


# --- errors name the file and the setting (FR-064) -------------------------------------


def test_an_unknown_setting_is_an_error_that_names_it(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ports = ['PORT']\nverbose = true\n")

    error = invalid(tmp_path)

    assert error.details == {"file": str(path), "setting": "verbose"}
    assert "verbose" in error.message


def test_invalid_toml_is_config_invalid_with_the_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ports = [\n")

    error = invalid(tmp_path)

    assert error.details["file"] == str(path)
    assert "setting" in error.details


def test_a_file_that_is_not_text_is_config_invalid(tmp_path: Path) -> None:
    (tmp_path / "wtenv.toml").write_bytes(b"\xff\xfe\x00")

    assert invalid(tmp_path).details["file"] == str(tmp_path / "wtenv.toml")


def test_an_error_has_a_hint(tmp_path: Path) -> None:
    write_config(tmp_path, "block_size = 0\n")

    assert invalid(tmp_path).hint


def test_the_first_error_is_reported_with_its_own_setting(tmp_path: Path) -> None:
    write_config(tmp_path, "block_size = 5000\n")

    error = invalid(tmp_path)

    assert error.details["setting"] == "block_size"
    assert "block_size" in error.message
