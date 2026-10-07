"""The command-line shell: `--version`, usage errors, exit statuses (cli.md; FR-058, FR-059)."""

import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import typer

import wtenv
from wtenv import cli
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.output import Result, VersionResult

RunWtenv = Callable[..., subprocess.CompletedProcess[str]]


# --- --version ------------------------------------------------------------------------


def test_version_prints_the_name_and_version(run_wtenv: RunWtenv, tmp_path: Path) -> None:
    result = run_wtenv(["--version"], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == f"wtenv {wtenv.__version__}\n"
    assert result.stderr == ""


def test_version_with_json_prints_a_version_result(run_wtenv: RunWtenv, tmp_path: Path) -> None:
    result = run_wtenv(["--version", "--json"], cwd=tmp_path)

    assert result.returncode == 0
    document = VersionResult.model_validate_json(result.stdout)
    assert document.version == wtenv.__version__
    assert document.ok is True
    assert document.command == "version"
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""


def test_json_may_come_before_version(run_wtenv: RunWtenv, tmp_path: Path) -> None:
    result = run_wtenv(["--json", "--version"], cwd=tmp_path)

    assert result.returncode == 0
    assert VersionResult.model_validate_json(result.stdout).version == wtenv.__version__


def test_python_dash_m_wtenv_and_the_console_script_behave_the_same(
    run_wtenv: RunWtenv, tmp_path: Path
) -> None:
    script = Path(sys.executable).parent / "wtenv"
    assert script.exists(), "the project is not installed: run `uv sync`"

    from_script = subprocess.run(
        [str(script), "--version"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    from_module = run_wtenv(["--version"], cwd=tmp_path)

    assert (from_script.returncode, from_script.stdout) == (0, f"wtenv {wtenv.__version__}\n")
    assert (from_module.returncode, from_module.stdout) == (0, from_script.stdout)


# --- usage errors ---------------------------------------------------------------------


@pytest.mark.parametrize("args", [["frobnicate"], ["--frobnicate"], []])
def test_a_usage_error_exits_2_with_the_error_on_stderr(
    run_wtenv: RunWtenv, tmp_path: Path, args: list[str]
) -> None:
    result = run_wtenv(args, cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("wtenv: error [usage_error]: ")


@pytest.mark.parametrize(
    "args",
    [
        ["frobnicate", "--json"],
        ["--json", "frobnicate"],
        ["--frobnicate", "--json"],
        ["--json"],
    ],
)
def test_a_usage_error_with_json_prints_one_document_with_no_command(
    run_wtenv: RunWtenv, tmp_path: Path, args: list[str]
) -> None:
    result = run_wtenv(args, cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout.count("\n") == 1
    document = Result.model_validate_json(result.stdout)
    assert document.ok is False
    assert document.command is None
    assert document.error is not None
    assert document.error.code is ErrorCode.USAGE_ERROR
    assert document.error.exit_status == 2
    assert result.stderr.startswith("wtenv: error [usage_error]: ")


def test_json_after_a_double_dash_is_not_the_json_flag(run_wtenv: RunWtenv, tmp_path: Path) -> None:
    result = run_wtenv(["frobnicate", "--", "--json"], cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""


def test_json_at_the_root_is_accepted_only_with_version(
    run_wtenv: RunWtenv, tmp_path: Path
) -> None:
    result = run_wtenv(["--json"], cwd=tmp_path)

    document = Result.model_validate_json(result.stdout)
    assert result.returncode == 2
    assert document.error is not None
    assert document.error.code is ErrorCode.USAGE_ERROR
    assert "--version" in document.error.message


# --- --help ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ci_like_terminal")
def test_help_works_and_offers_no_shell_completion(run_wtenv: RunWtenv, tmp_path: Path) -> None:
    result = run_wtenv(["--help"], cwd=tmp_path)

    assert result.returncode == 0
    assert "--version" in result.stdout
    assert "--help" in result.stdout
    assert "completion" not in result.stdout.lower()
    assert "wtenv" in result.stdout


# --- failures inside commands ---------------------------------------------------------


@pytest.fixture
def failing_commands() -> Iterator[None]:
    """Register commands that fail, to exercise the exit-status mapping; removed afterwards."""

    @cli.app.command("boom")
    def boom(json_output: bool = typer.Option(False, "--json")) -> None:
        raise ZeroDivisionError("division by zero")

    @cli.app.command("refuse")
    def refuse(code: str, json_output: bool = typer.Option(False, "--json")) -> None:
        raise WtenvError(
            ErrorCode(code),
            "the command refused",
            hint="Do it differently.",
            details={"reason": "test"},
        )

    registered = len(cli.app.registered_commands)
    yield
    del cli.app.registered_commands[registered - 2 :]


def test_an_unexpected_exception_exits_1_with_internal_error(
    failing_commands: None, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["boom"])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert captured.err.startswith("wtenv: error [internal_error]: ")
    assert "division by zero" in captured.err


def test_an_unexpected_exception_with_json_names_the_exception_class(
    failing_commands: None, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["boom", "--json"])

    captured = capsys.readouterr()
    document = Result.model_validate_json(captured.out)
    assert status == 1
    assert captured.out.count("\n") == 1
    assert document.ok is False
    assert document.command == "boom"
    assert document.error is not None
    assert document.error.code is ErrorCode.INTERNAL_ERROR
    assert document.error.exit_status == 1
    assert document.error.details == {"exception": "ZeroDivisionError"}


@pytest.mark.parametrize("code", list(ErrorCode))
def test_a_wtenv_error_exits_with_the_status_of_its_code(
    failing_commands: None, capsys: pytest.CaptureFixture[str], code: ErrorCode
) -> None:
    status = cli.main(["refuse", code.value])

    captured = capsys.readouterr()
    assert status == EXIT_STATUS[code]
    assert captured.err == (
        f"wtenv: error [{code.value}]: the command refused\nhint: Do it differently.\n"
    )
    assert captured.out == ""


def test_a_wtenv_error_with_json_prints_its_code_status_hint_and_details(
    failing_commands: None, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["refuse", "config_invalid", "--json"])

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert status == 3
    assert captured.out.count("\n") == 1
    assert document["command"] == "refuse"
    assert document["ok"] is False
    assert document["error"] == {
        "code": "config_invalid",
        "exit_status": 3,
        "message": "the command refused",
        "hint": "Do it differently.",
        "details": {"reason": "test"},
    }


def test_a_usage_error_inside_a_known_command_names_the_command(
    failing_commands: None, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["refuse", "--frobnicate", "--json"])

    document = Result.model_validate_json(capsys.readouterr().out)
    assert status == 2
    assert document.command == "refuse"
    assert document.error is not None
    assert document.error.code is ErrorCode.USAGE_ERROR


@pytest.fixture
def hook_group() -> Iterator[None]:
    """Register a `hook install` command, to check commands inside a group; removed afterwards."""
    group = typer.Typer()

    @group.command("install")
    def install(json_output: bool = typer.Option(False, "--json")) -> None:
        raise WtenvError(ErrorCode.CONFIG_INVALID, "bad")

    cli.app.add_typer(group, name="hook")
    yield
    cli.app.registered_groups.pop()


def test_a_command_inside_a_group_is_named_with_the_group(
    hook_group: None, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["hook", "install", "--json"])

    document = Result.model_validate_json(capsys.readouterr().out)
    assert status == 3
    assert document.command == "hook install"


def test_a_group_without_a_known_command_names_no_command(
    hook_group: None, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(["hook", "frobnicate", "--json"])

    document = Result.model_validate_json(capsys.readouterr().out)
    assert status == 2
    assert document.command is None


def test_a_successful_run_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == f"wtenv {wtenv.__version__}\n"
