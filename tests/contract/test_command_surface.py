"""The command surface is exactly the synopsis of cli.md, and nothing more (T130; FR-001).

The synopsis is read from `contracts/cli.md` itself, and compared with the commands and options of
the Typer application, so adding, renaming, or dropping a command or an option fails here until
the contract says so too.
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.core import TyperGroup
from typer.main import get_command

from wtenv import cli

CLI_CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "001-worktree-runtime-isolation"
    / "contracts"
    / "cli.md"
)

RunWtenv = Callable[..., subprocess.CompletedProcess[str]]


def synopsis() -> dict[str, set[str]]:
    """Return `{command words: options}` from the first `text` block of cli.md.

    The root (`wtenv --version [--json]`) is the empty string. Options are the `--name` words;
    the `--` before `exec`'s command is not one.
    """
    text = CLI_CONTRACT.read_text(encoding="utf-8")
    block = re.search(r"```text\n(wtenv .*?)```", text, flags=re.DOTALL)
    assert block is not None, "cli.md has no synopsis block"
    commands: dict[str, set[str]] = {}
    for line in block.group(1).strip().splitlines():
        words = re.match(r"wtenv((?: [a-z]+)*)", line)
        assert words is not None, line
        name = words.group(1).strip()
        assert name not in commands, f"{name!r} is listed twice"
        commands[name] = set(re.findall(r"--[a-z]+(?:-[a-z]+)*", line))
    return commands


def surface() -> dict[str, set[str]]:
    """Return the same dictionary from the application: every command and its options."""
    root = get_command(cli.app)
    assert isinstance(root, TyperGroup)
    found = {"": options_of(root)}

    def walk(group: TyperGroup, prefix: str) -> None:
        for name, command in group.commands.items():
            assert not command.hidden, f"{prefix}{name} is hidden"
            if isinstance(command, TyperGroup):
                walk(command, f"{prefix}{name} ")
            else:
                found[f"{prefix}{name}"] = options_of(command)

    walk(root, "")
    return found


def options_of(command: object) -> set[str]:
    """Return the option names of a click command, without `--help`."""
    names: set[str] = set()
    for parameter in getattr(command, "params"):  # noqa: B009 - click is not imported here
        if parameter.param_type_name == "option":
            names |= set(parameter.opts) | set(parameter.secondary_opts)
    return names - {"--help"}


def test_the_commands_and_options_are_exactly_the_synopsis_of_cli_md() -> None:
    assert surface() == synopsis()


def test_the_synopsis_lists_the_nine_commands_of_the_contract() -> None:
    assert sorted(synopsis()) == sorted(
        ["", "up", "down", "gc", "ls", "exec", "doctor", "hook install", "hook uninstall"]
    )


def test_only_exec_takes_a_command_as_arguments() -> None:
    root = get_command(cli.app)
    assert isinstance(root, TyperGroup)
    hook = root.commands["hook"]
    assert isinstance(hook, TyperGroup)
    commands = {**root.commands, **{f"hook {n}": c for n, c in hook.commands.items()}}
    arguments = {
        name: [p.name for p in command.params if p.param_type_name == "argument"]
        for name, command in commands.items()
        if name != "hook"
    }

    assert {name: found for name, found in arguments.items() if found} == {"exec": ["command"]}


def test_release_may_be_given_more_than_once() -> None:
    root = get_command(cli.app)
    assert isinstance(root, TyperGroup)
    release = next(p for p in root.commands["gc"].params if "--release" in p.opts)

    assert getattr(release, "multiple") is True  # noqa: B009


@pytest.mark.parametrize("command", [name for name in synopsis() if name])
def test_every_command_answers_help(run_wtenv: RunWtenv, tmp_path: Path, command: str) -> None:
    process = run_wtenv([*command.split(), "--help"], cwd=tmp_path)

    assert process.returncode == 0, process.stderr
    assert "Usage:" in process.stdout


@pytest.mark.usefixtures("ci_like_terminal")
def test_the_root_answers_help_and_names_every_command(run_wtenv: RunWtenv, tmp_path: Path) -> None:
    process = run_wtenv(["--help"], cwd=tmp_path)

    assert process.returncode == 0
    for name in ("up", "down", "gc", "ls", "exec", "doctor", "hook"):
        assert re.search(rf"│\s*{name}\s", process.stdout), name


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GITHUB_ACTIONS", "true"),
        ("CI", "true"),
        ("FORCE_COLOR", "1"),
        ("PY_COLORS", "1"),
        ("TTY_COMPATIBLE", "1"),
        ("COLUMNS", "60"),
        ("TERMINAL_WIDTH", "60"),
    ],
)
def test_help_is_plain_and_unwrapped_whatever_the_callers_terminal(
    run_wtenv: RunWtenv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    process = run_wtenv(["--help"], cwd=tmp_path)

    assert process.returncode == 0
    assert "\x1b" not in process.stdout
    row = next(line for line in process.stdout.splitlines() if re.search(r"│\s*up\s", line))
    assert "bring it up to date." in row


def test_no_completion_options_are_installed(run_wtenv: RunWtenv, tmp_path: Path) -> None:
    process = run_wtenv(["--help"], cwd=tmp_path)

    assert "--install-completion" not in process.stdout
    assert "--show-completion" not in process.stdout
