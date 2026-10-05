"""The wtenv command line: the Typer app, `--version`, and the mapping to exit statuses.

Start-up must stay under 300 ms (NFR-001), so this module imports only `typer`, the standard
library, `wtenv.__version__`, and `wtenv.errors` at module level. Each command imports its
implementation inside its own function, and the pydantic models are imported only when a
result or an error is printed. Typer ships its own copy of click: wtenv never imports `click`
(research.md section 7).
"""

import sys
from collections.abc import Sequence

import typer
from typer.core import TyperGroup
from typer.exceptions import TyperException
from typer.main import get_command

from wtenv import __version__
from wtenv.errors import EXIT_STATUS, EXIT_SUCCESS, ErrorCode, WtenvError

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)

_HELP_HINT = "Run `wtenv --help` to see the commands and options."


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Print the version and exit."),
    json_output: bool = typer.Option(
        False, "--json", help="With --version, print a JSON document instead of text."
    ),
) -> None:
    """Give each git worktree its own ports, env file, database, and compose project."""
    if json_output and not version:
        raise WtenvError(
            ErrorCode.USAGE_ERROR,
            "--json is accepted before a command only together with --version",
            hint="Give --json after the command: `wtenv <command> --json`.",
        )
    if version:
        _print_version(json_output)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        raise WtenvError(ErrorCode.USAGE_ERROR, "missing command", hint=_HELP_HINT)


def _print_version(json_mode: bool) -> None:
    """Print `wtenv <version>`, or a `VersionResult` document with `--json`."""
    if json_mode:
        from wtenv.output import VersionResult, print_result

        print_result(VersionResult(ok=True, version=__version__), json_mode=True)
    else:
        print(f"wtenv {__version__}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run wtenv with `argv` (default: the process arguments) and return the exit status.

    wtenv alone decides every exit status. A `WtenvError` gives the status of its code, a usage
    error from Typer gives `usage_error`, and anything else gives `internal_error`. The error
    goes to standard error; with `--json`, the failed result goes to standard output too.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        status = app(args, prog_name="wtenv", standalone_mode=False)
    except WtenvError as error:
        return _report(error, args)
    except TyperException as error:
        return _report(WtenvError(ErrorCode.USAGE_ERROR, str(error), hint=_HELP_HINT), args)
    except Exception as error:  # noqa: BLE001 - anything unexpected is `internal_error`
        name = type(error).__name__
        internal = WtenvError(
            ErrorCode.INTERNAL_ERROR,
            f"unexpected {name}: {error}",
            hint="This is a bug in wtenv. Please report it.",
            details={"exception": name},
        )
        return _report(internal, args)
    return status if isinstance(status, int) else EXIT_SUCCESS


def _report(error: WtenvError, args: list[str]) -> int:
    """Print a failure the way the command line asks for, and return its exit status."""
    from wtenv.output import Result, failed_result, print_error, print_result

    print_error(error)
    if _wants_json(args):
        result = failed_result(Result, error).model_copy(update={"command": _command_name(args)})
        print_result(result, json_mode=True)
    return EXIT_STATUS[error.code]


def _before_double_dash(args: list[str]) -> list[str]:
    """Return the arguments before the first `--`; what follows belongs to another program."""
    return args[: args.index("--")] if "--" in args else args


def _wants_json(args: list[str]) -> bool:
    """Return whether `--json` is among the arguments before any `--`.

    The arguments are scanned, not parsed, so that a usage error can still print a JSON document.
    """
    return "--json" in _before_double_dash(args)


def _command_name(args: list[str]) -> str | None:
    """Return the known command the arguments name, such as `up` or `hook install`, else None."""
    root_command = get_command(app)
    if not isinstance(root_command, TyperGroup):
        return None
    known = _command_names(root_command)
    words = [word for word in _before_double_dash(args) if not word.startswith("-")]
    for length in (2, 1):
        candidate = " ".join(words[:length])
        if candidate in known:
            return candidate
    return None


def _command_names(group: TyperGroup, prefix: str = "") -> set[str]:
    """Return the names of the commands in `group`, including those of its sub-groups."""
    names: set[str] = set()
    for name, command in group.commands.items():
        if isinstance(command, TyperGroup):
            names |= _command_names(command, f"{prefix}{name} ")
        else:
            names.add(f"{prefix}{name}")
    return names
