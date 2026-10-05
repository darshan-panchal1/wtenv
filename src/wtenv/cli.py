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
from wtenv.errors import EXEC_WTENV_FAILED, EXIT_STATUS, EXIT_SUCCESS, ErrorCode, WtenvError

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


@app.command()
def up(
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document."),
) -> None:
    """Give the current worktree its ports and env file, or bring it up to date."""
    from wtenv.output import (
        UpResult,
        failed_result,
        print_error,
        print_result,
        print_warnings,
        render_up_text,
    )
    from wtenv.provision import up as provision_up

    try:
        result = provision_up()
    except WtenvError as error:
        print_error(error)
        print_result(failed_result(UpResult, error), json_mode=json_output)
        raise typer.Exit(EXIT_STATUS[error.code]) from error
    print_warnings(result.warnings)
    print_result(result, json_mode=json_output, text=render_up_text(result))


@app.command()
def down(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Change nothing; list what `down` would remove."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document."),
) -> None:
    """Release everything wtenv recorded for the current worktree."""
    from wtenv.output import (
        DownResult,
        failed_result,
        print_error,
        print_result,
        print_warnings,
        render_down_text,
    )
    from wtenv.teardown import down as release_worktree

    try:
        result = release_worktree(dry_run=dry_run)
    except WtenvError as error:
        print_error(error)
        print_result(failed_result(DownResult, error), json_mode=json_output)
        raise typer.Exit(EXIT_STATUS[error.code]) from error
    print_warnings(result.warnings)
    if result.error is not None:
        # Some items could not be removed: the document still lists everything that happened.
        print_error(WtenvError(result.error.code, result.error.message, hint=result.error.hint))
    print_result(result, json_mode=json_output, text=render_down_text(result))
    if result.error is not None:
        raise typer.Exit(result.error.exit_status)


@app.command()
def gc(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Change nothing; list what `gc` would remove."
    ),
    release: list[str] | None = typer.Option(  # noqa: B008 - Typer reads options from defaults
        None,
        "--release",
        metavar="PATH",
        help=(
            "Release the entry recorded at PATH even though it is unverifiable. "
            "May be given several times; then only the named entries are acted on."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document."),
) -> None:
    """Release the entries of worktrees that git confirms are gone, in every repository."""
    from wtenv import orphans
    from wtenv.output import GcResult, failed_result, print_error, print_result, render_gc_text

    try:
        if release:
            result = orphans.gc_release(release, dry_run=dry_run)
        else:
            result = orphans.gc(dry_run=dry_run)
    except WtenvError as error:
        print_error(error)
        print_result(failed_result(GcResult, error), json_mode=json_output)
        raise typer.Exit(EXIT_STATUS[error.code]) from error
    if result.error is not None:
        # Some items could not be removed: the document still lists everything that happened.
        print_error(WtenvError(result.error.code, result.error.message, hint=result.error.hint))
    print_result(result, json_mode=json_output, text=render_gc_text(result))
    if result.error is not None:
        raise typer.Exit(result.error.exit_status)


@app.command()
def ls(
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document."),
) -> None:
    """List every worktree wtenv knows, and this repository's worktrees that have no entry."""
    from wtenv.listing import list_worktrees
    from wtenv.output import LsResult, failed_result, print_error, print_result, render_ls_text

    try:
        result = list_worktrees()
    except WtenvError as error:
        print_error(error)
        print_result(failed_result(LsResult, error), json_mode=json_output)
        raise typer.Exit(EXIT_STATUS[error.code]) from error
    print_result(result, json_mode=json_output, text=render_ls_text(result))


@app.command("exec")
def exec_command(
    command: list[str] = typer.Argument(  # noqa: B008 - Typer reads arguments from defaults
        ...,
        metavar="COMMAND [ARG]...",
        help="The command to run, after `--`. Everything after `--` belongs to it.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Describe a failure of wtenv itself as a JSON document."
    ),
) -> None:
    """Run a command with the worktree's variables added to the environment.

    Write `--` before the command: `wtenv exec -- npm run dev`.

    Exit status: the command's own. 125: wtenv itself failed. 126: the command could not be run. 127: the command was not found.
    """
    from wtenv.execcmd import command_environment, replace_process
    from wtenv.output import ExecResult, failed_result, print_error, print_result

    try:
        environment = command_environment()
    except WtenvError as error:
        print_error(error)
        failed = failed_result(ExecResult, error, exit_status=EXEC_WTENV_FAILED)
        print_result(failed, json_mode=json_output)
        raise typer.Exit(EXEC_WTENV_FAILED) from error
    raise typer.Exit(replace_process(command, environment))


hook_app = typer.Typer(
    help="Install or remove the git hook that provisions new worktrees.", no_args_is_help=True
)
app.add_typer(hook_app, name="hook")


@hook_app.command("install")
def hook_install(
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document."),
) -> None:
    """Install the post-checkout hook that runs `wtenv up` in each new worktree."""
    from wtenv.hooks import install
    from wtenv.output import (
        HookInstallResult,
        failed_result,
        print_error,
        print_result,
        render_hook_install_text,
    )

    try:
        result = install()
    except WtenvError as error:
        print_error(error)
        print_result(failed_result(HookInstallResult, error), json_mode=json_output)
        raise typer.Exit(EXIT_STATUS[error.code]) from error
    print_result(result, json_mode=json_output, text=render_hook_install_text(result))


@hook_app.command("uninstall")
def hook_uninstall(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Change nothing; list what `hook uninstall` would remove."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document."),
) -> None:
    """Remove the block that `hook install` added, and the hook file if wtenv created it."""
    from wtenv.hooks import uninstall
    from wtenv.output import (
        HookUninstallResult,
        failed_result,
        print_error,
        print_result,
        render_hook_uninstall_text,
    )

    try:
        result = uninstall(dry_run=dry_run)
    except WtenvError as error:
        print_error(error)
        print_result(failed_result(HookUninstallResult, error), json_mode=json_output)
        raise typer.Exit(EXIT_STATUS[error.code]) from error
    print_result(result, json_mode=json_output, text=render_hook_uninstall_text(result))


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
        _require_exec_separator(args)
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
    """Print a failure the way the command line asks for, and return its exit status.

    `exec` is a wrapper that follows `env(1)`: any failure of wtenv itself is 125, whatever its
    code, and the real code stays in the error (cli.md, `wtenv exec`).
    """
    from wtenv.output import Result, failed_result, print_error, print_result

    status = EXEC_WTENV_FAILED if _is_exec(args) else EXIT_STATUS[error.code]
    print_error(error)
    if _wants_json(args):
        result = failed_result(Result, error, exit_status=status).model_copy(
            update={"command": _command_name(args)}
        )
        print_result(result, json_mode=True)
    return status


def _is_exec(args: list[str]) -> bool:
    """Return whether the command line is a `wtenv exec` one: its first word is `exec`.

    Scanned, not parsed, like `_wants_json`: a usage error must still exit with 125.
    """
    words = [word for word in _before_double_dash(args) if not word.startswith("-")]
    return words[:1] == ["exec"]


def _require_exec_separator(args: list[str]) -> None:
    """Raise a usage error when `wtenv exec` has no `--` before its command.

    Typer would run `wtenv exec env` too, but without the separator a command's own options
    could be taken for wtenv's. `--help` needs no separator.
    """
    if _is_exec(args) and "--" not in args and "--help" not in args:
        raise WtenvError(
            ErrorCode.USAGE_ERROR,
            "exec needs `--` before the command",
            hint="Write `wtenv exec -- COMMAND [ARG]...`.",
        )


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
