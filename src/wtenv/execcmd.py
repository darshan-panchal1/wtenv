"""`wtenv exec`: run a command with the worktree's variables (cli.md, `wtenv exec`; FR-056, FR-057;
reading R1).

Every variable comes from wtenv's section of the env file that the registry records for the
worktree, ports included, unquoted as files.md describes. `wtenv.toml` is not read, and the
registry supplies only the path of the env file, because it holds no credentials (FR-019). The
developer's own lines in the env file are not loaded.
"""

import errno
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from wtenv import registry
from wtenv.envfile import read_section
from wtenv.errors import EXEC_COMMAND_NOT_EXECUTABLE, EXEC_COMMAND_NOT_FOUND, ErrorCode, WtenvError
from wtenv.gitutil import git_listing
from wtenv.identity import current_worktree
from wtenv.locks import registry_lock
from wtenv.orphans import classify
from wtenv.output import Status


def command_environment(cwd: str | Path | None = None) -> dict[str, str]:
    """Return the environment for the command: the current one plus wtenv's section.

    The worktree is the one that contains `cwd` (default: the current directory) and must be
    `provisioned`, as `classify` says (FR-057). A variable of the section replaces one of the
    same name in the current environment. `exec` takes the registry lock only to read, and no
    worktree lock (FR-076).

    Raises `not_in_worktree`; `not_provisioned` (`details.status` is `unprovisioned`,
    `incomplete`, `unverifiable`, or `orphaned`); `env_file_unusable` with reason `missing`,
    `no_section`, or `markers_damaged`; and the registry errors.
    """
    identity = current_worktree(cwd)
    with registry_lock():
        entry = registry.load().worktrees.get(identity.git_dir)
    if entry is None:
        raise _not_provisioned(identity.path, Status.UNPROVISIONED)
    status = classify(entry, git_listing(entry.repository)).status
    if status is not Status.PROVISIONED or entry.env_file is None:
        raise _not_provisioned(entry.path, Status.INCOMPLETE if entry.env_file is None else status)
    variables = read_section(Path(entry.path) / entry.env_file.path)
    return {**os.environ, **dict(variables)}


def replace_process(command: Sequence[str], environment: Mapping[str, str]) -> int:
    """Replace this process with `command`, so that its signals and streams are the command's.

    Returns only when the command could not be started, and then it is the exit status to use,
    as `env(1)` does: 127 when the command was not found, 126 when it was found but could not
    be run. The reason goes to standard error. `command` is not empty.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execvpe(command[0], list(command), dict(environment))
    except (FileNotFoundError, NotADirectoryError) as error:
        status = EXEC_COMMAND_NOT_FOUND
        reason = error.strerror or "No such file or directory"
    except OSError as error:
        status = EXEC_COMMAND_NOT_EXECUTABLE
        reason = error.strerror or errno.errorcode.get(error.errno or 0, "cannot be run")
    print(f"wtenv: cannot run {command[0]}: {reason}", file=sys.stderr)
    return status


def _not_provisioned(path: str, status: Status) -> WtenvError:
    return WtenvError(
        ErrorCode.NOT_PROVISIONED,
        f"the worktree {path} is {status.value}, so the command was not run",
        hint="Run `wtenv up` in this worktree, then run the command again.",
        details={"path": path, "status": status.value},
    )
