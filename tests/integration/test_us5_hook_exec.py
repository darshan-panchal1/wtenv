"""User story 5: the `post-checkout` hook and `wtenv exec`, on real git worktrees (spec.md, User
Story 5; T118, T121).

Every test uses a temporary repository and the temporary state directory of `tests/conftest.py`;
none touches the developer's own registry.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import commit_all, git, make_sqlite_template

from wtenv.errors import ErrorCode
from wtenv.hooks import BLOCK
from wtenv.identity import current_worktree
from wtenv.locks import worktree_lock
from wtenv.output import ExecResult, HookInstallResult, HookUninstallResult, ItemKind
from wtenv.registry import WorktreeEntry, load, registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

ZEROS = "0" * 40


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


# --- helpers ------------------------------------------------------------------------------


def wtenv_bin() -> str:
    """The directory of the test environment's `wtenv` script."""
    directory = Path(sys.executable).parent
    assert (directory / "wtenv").exists(), "the project is not installed: run `uv sync`"
    return str(directory)


def git_env_with_wtenv() -> dict[str, str]:
    """The environment of a git command whose hooks find `wtenv` first on `PATH`."""
    return {**os.environ, "PATH": os.pathsep.join([wtenv_bin(), os.environ["PATH"]])}


def git_env_without_wtenv() -> dict[str, str]:
    """An environment where git works and `wtenv` is not on `PATH`."""
    git_path = shutil.which("git")
    assert git_path is not None
    path = os.pathsep.join([str(Path(git_path).parent), "/usr/bin", "/bin"])
    assert shutil.which("wtenv", path=path) is None, "wtenv is installed outside the test venv"
    return {**os.environ, "PATH": path}


def git_run(
    cwd: Path, args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run git in `cwd`, with hooks finding `wtenv` unless `env` says otherwise; never raises."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=git_env_with_wtenv() if env is None else env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def hook_file(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-checkout"


def registry_bytes() -> bytes | None:
    """The registry file as it is, or None when there is none."""
    path = registry_path()
    return path.read_bytes() if path.exists() else None


def entry_of(worktree: Path) -> WorktreeEntry | None:
    """The registry entry of `worktree`, or None when it has none."""
    return load().worktrees.get(current_worktree(worktree).git_dir)


def install(run_wtenv: Run, cwd: Path) -> HookInstallResult:
    """Run `wtenv hook install --json` in `cwd`; standard output is one document."""
    process = run_wtenv(["hook", "install", "--json"], cwd)
    assert len(process.stdout.splitlines()) == 1, process.stdout
    return HookInstallResult.model_validate_json(process.stdout)


def uninstall(run_wtenv: Run, cwd: Path, *args: str) -> HookUninstallResult:
    """Run `wtenv hook uninstall --json` in `cwd`; standard output is one document."""
    process = run_wtenv(["hook", "uninstall", "--json", *args], cwd)
    assert len(process.stdout.splitlines()) == 1, process.stdout
    return HookUninstallResult.model_validate_json(process.stdout)


def add_with_hook(
    repo: Path, name: str, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `git worktree add ... ../<name>` from `repo`; the worktree is at `repo.parent/name`."""
    return git_run(repo, ["worktree", "add", *args, str(repo.parent / name)], env)


# --- scenario 1 (FR-051): the hook provisions new worktrees ---------------------------------


def test_a_new_worktree_with_a_branch_is_provisioned_by_the_hook(
    run_wtenv: Run, repo: Path
) -> None:
    assert install(run_wtenv, repo).action == "installed"

    process = add_with_hook(repo, "feature-x", "-b", "feature-x")

    worktree = (repo.parent / "feature-x").resolve()
    assert process.returncode == 0, process.stderr
    entry = entry_of(worktree)
    assert entry is not None and entry.state == "provisioned"
    assert "PORT=" in (worktree / ".env.local").read_text(encoding="utf-8")


def test_a_detached_worktree_is_provisioned_by_the_hook(run_wtenv: Run, repo: Path) -> None:
    install(run_wtenv, repo)

    process = add_with_hook(repo, "detached", "--detach")

    worktree = (repo.parent / "detached").resolve()
    assert process.returncode == 0, process.stderr
    entry = entry_of(worktree)
    assert entry is not None and entry.state == "provisioned"


def test_the_hook_sends_the_output_of_up_to_standard_error(run_wtenv: Run, repo: Path) -> None:
    install(run_wtenv, repo)

    process = add_with_hook(repo, "feature-x", "-b", "feature-x")

    assert "wtenv" not in process.stdout  # git's own "HEAD is now at" line is on stdout
    assert "wtenv: provisioned" in process.stderr


def test_the_hook_works_from_a_linked_worktree_too(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    other = add_worktree(repo, "other", "other")
    install(run_wtenv, other)

    process = add_with_hook(other, "second", "-b", "second")

    assert process.returncode == 0, process.stderr
    assert hook_file(repo).exists()
    entry = entry_of((repo.parent / "second").resolve())
    assert entry is not None and entry.state == "provisioned"


# --- scenario 2 (FR-052): the hook always lets git exit 0 -----------------------------------


def test_a_failing_up_does_not_fail_git_worktree_add(run_wtenv: Run, repo: Path) -> None:
    (repo / "wtenv.toml").write_text("ports = 5\n", encoding="utf-8")
    commit_all(repo, "add a config that is invalid")
    install(run_wtenv, repo)

    process = add_with_hook(repo, "feature-x", "-b", "feature-x")

    worktree = repo.parent / "feature-x"
    assert process.returncode == 0, process.stderr
    assert worktree.is_dir()
    assert "wtenv: error [config_invalid]" in process.stderr
    assert entry_of(worktree.resolve()) is None


def test_wtenv_missing_from_path_does_not_fail_git_worktree_add(run_wtenv: Run, repo: Path) -> None:
    install(run_wtenv, repo)
    before = registry_bytes()

    process = add_with_hook(repo, "feature-x", "-b", "feature-x", env=git_env_without_wtenv())

    worktree = repo.parent / "feature-x"
    assert process.returncode == 0, process.stderr
    assert worktree.is_dir()
    assert "wtenv: not found on PATH; this worktree was not provisioned" in process.stderr
    assert registry_bytes() == before


# --- scenario 3 (FR-051): other checkouts provision nothing ---------------------------------


def test_switching_a_file_checkout_and_a_clone_provision_nothing(
    run_wtenv: Run, repo: Path, tmp_path: Path
) -> None:
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    commit_all(repo, "add a file")
    install(run_wtenv, repo)
    assert add_with_hook(repo, "feature-x", "-b", "feature-x").returncode == 0
    worktree = (repo.parent / "feature-x").resolve()
    before = registry_bytes()
    assert before is not None

    switched = git_run(worktree, ["switch", "-c", "other"])
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    checked_out = git_run(worktree, ["checkout", "HEAD", "--", "tracked.txt"])

    assert switched.returncode == 0, switched.stderr
    assert checked_out.returncode == 0, checked_out.stderr
    assert registry_bytes() == before

    # Git does not copy hooks into a clone, so a template carries ours, plus a line that proves
    # the hook ran: the clone is the only checkout here whose git directory is the common one.
    fired = tmp_path / "hook-fired"
    template = tmp_path / "template"
    (template / "hooks").mkdir(parents=True)
    copy = template / "hooks" / "post-checkout"
    copy.write_text(hook_file(repo).read_text() + f"echo fired >> {fired}\n", encoding="utf-8")
    copy.chmod(0o755)
    cloned = git_run(tmp_path, ["clone", f"--template={template}", str(repo), "cloned"])

    assert cloned.returncode == 0, cloned.stderr
    assert fired.read_text().strip() == "fired"
    assert "wtenv" not in cloned.stderr
    assert registry_bytes() == before


@pytest.mark.parametrize("option", ["--no-checkout", "--orphan"])
def test_worktree_add_without_a_checkout_runs_no_hook_and_is_silent(
    run_wtenv: Run, repo: Path, option: str
) -> None:
    """Git runs no `post-checkout` for `--no-checkout` and `--orphan` (research.md, section 1)."""
    install(run_wtenv, repo)
    before = registry_bytes()

    process = add_with_hook(repo, "plain", option, "-b", "plain")

    worktree = repo.parent / "plain"
    assert process.returncode == 0, process.stderr
    assert worktree.is_dir()
    assert "wtenv" not in process.stderr + process.stdout
    assert registry_bytes() == before
    assert entry_of(worktree.resolve()) is None


# --- scenario 7 (FR-053): an existing hook is kept ---------------------------------------


def test_an_existing_hook_keeps_running_its_own_lines_and_is_restored_by_uninstall(
    run_wtenv: Run, repo: Path, tmp_path: Path
) -> None:
    ran = tmp_path / "own-hook-ran"
    original = f"#!/bin/sh\n# my hook\necho own >> {ran}\nexit 0\n"
    hook_file(repo).write_text(original, encoding="utf-8")
    hook_file(repo).chmod(0o755)

    result = install(run_wtenv, repo)
    process = add_with_hook(repo, "feature-x", "-b", "feature-x")

    assert result.action == "installed"
    assert process.returncode == 0, process.stderr
    assert ran.read_text().strip() == "own"
    assert entry_of((repo.parent / "feature-x").resolve()) is not None
    text = hook_file(repo).read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n" + BLOCK)
    assert text.endswith("# my hook\n" + f"echo own >> {ran}\nexit 0\n")

    removed = uninstall(run_wtenv, repo)

    assert removed.action == "removed"
    assert [item.kind for item in removed.removed] == [ItemKind.HOOK_BLOCK]
    assert hook_file(repo).read_text(encoding="utf-8") == original
    assert hook_file(repo).stat().st_mode & 0o777 == 0o755


# --- install and uninstall: files, records, options ------------------------------------------


def test_install_creates_an_executable_hook_and_uninstall_deletes_it(
    run_wtenv: Run, repo: Path
) -> None:
    result = install(run_wtenv, repo)

    path = hook_file(repo)
    assert result.ok and result.error is None
    assert result.action == "installed"
    assert result.hook_file == str(path.resolve())
    assert path.read_text(encoding="utf-8") == "#!/bin/sh\n" + BLOCK
    assert path.stat().st_mode & 0o777 == 0o755
    record = load().hooks[str(repo / ".git")]
    assert record.hook_file == str(path.resolve()) and record.created_file is True

    removed = uninstall(run_wtenv, repo)

    assert removed.ok and removed.action == "removed" and removed.dry_run is False
    assert [(item.kind, item.name) for item in removed.removed] == [
        (ItemKind.HOOK_BLOCK, str(path.resolve())),
        (ItemKind.HOOK_FILE, str(path.resolve())),
    ]
    assert not path.exists()
    assert str(repo / ".git") not in load().hooks


def test_uninstall_keeps_a_file_that_wtenv_created_when_more_than_the_shebang_is_left(
    run_wtenv: Run, repo: Path
) -> None:
    install(run_wtenv, repo)
    with hook_file(repo).open("a", encoding="utf-8") as file:
        file.write("echo added later\n")

    removed = uninstall(run_wtenv, repo)

    assert [item.kind for item in removed.removed] == [ItemKind.HOOK_BLOCK]
    assert hook_file(repo).read_text(encoding="utf-8") == "#!/bin/sh\necho added later\n"


def test_uninstall_dry_run_lists_what_it_would_remove_and_changes_nothing(
    run_wtenv: Run, repo: Path
) -> None:
    install(run_wtenv, repo)
    content, registry = hook_file(repo).read_bytes(), registry_bytes()

    result = uninstall(run_wtenv, repo, "--dry-run")

    path = str(hook_file(repo).resolve())
    assert result.ok and result.dry_run is True
    assert [(item.kind, item.name) for item in result.would_remove] == [
        (ItemKind.HOOK_BLOCK, path),
        (ItemKind.HOOK_FILE, path),
    ]
    assert result.removed == []
    assert hook_file(repo).read_bytes() == content
    assert registry_bytes() == registry


def test_uninstall_dry_run_of_a_hook_that_was_not_created_by_wtenv_lists_only_the_block(
    run_wtenv: Run, repo: Path
) -> None:
    hook_file(repo).write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    install(run_wtenv, repo)
    content = hook_file(repo).read_bytes()

    result = uninstall(run_wtenv, repo, "--dry-run")

    assert [item.kind for item in result.would_remove] == [ItemKind.HOOK_BLOCK]
    assert hook_file(repo).read_bytes() == content


def test_uninstall_without_a_block_is_success_and_absent(run_wtenv: Run, repo: Path) -> None:
    for existing in (None, "#!/bin/sh\necho mine\n"):
        if existing is not None:
            hook_file(repo).write_text(existing, encoding="utf-8")

        result = uninstall(run_wtenv, repo)

        assert result.ok and result.action == "absent"
        assert result.removed == [] and result.would_remove == []
        assert result.hook_file == str(hook_file(repo).resolve())
        if existing is not None:
            assert hook_file(repo).read_text(encoding="utf-8") == existing


def test_installing_twice_is_unchanged(run_wtenv: Run, repo: Path) -> None:
    install(run_wtenv, repo)
    content, registry = hook_file(repo).read_bytes(), registry_bytes()

    again = install(run_wtenv, repo)

    assert again.ok and again.action == "unchanged"
    assert hook_file(repo).read_bytes() == content
    assert registry_bytes() == registry


def test_an_outdated_block_is_updated_in_place(run_wtenv: Run, repo: Path) -> None:
    install(run_wtenv, repo)
    path = hook_file(repo)
    path.write_text(path.read_text().replace("wtenv up 1>&2 || true", "wtenv up || true"))

    result = install(run_wtenv, repo)

    assert result.action == "updated"
    assert path.read_text(encoding="utf-8") == "#!/bin/sh\n" + BLOCK


def test_installing_over_an_existing_hook_does_not_make_wtenv_delete_it_later(
    run_wtenv: Run, repo: Path
) -> None:
    hook_file(repo).write_text("#!/bin/sh\n", encoding="utf-8")
    install(run_wtenv, repo)

    assert load().hooks[str(repo / ".git")].created_file is False
    uninstall(run_wtenv, repo)

    assert hook_file(repo).read_text(encoding="utf-8") == "#!/bin/sh\n"


def test_no_other_command_installs_the_hook(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")

    for command in (["up"], ["ls"], ["gc"], ["down"]):
        assert run_wtenv([*command, "--json"], worktree).returncode == 0

    assert not hook_file(repo).exists()
    assert load().hooks == {}


# --- unsupported setups -----------------------------------------------------------------------


def test_a_redirected_hooks_path_exits_19_and_writes_nothing(
    run_wtenv: Run, repo: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "shared-hooks"
    elsewhere.mkdir()
    git(repo, "config", "core.hooksPath", str(elsewhere))

    process = run_wtenv(["hook", "install", "--json"], repo)

    result = HookInstallResult.model_validate_json(process.stdout)
    assert process.returncode == 19
    assert result.ok is False and result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED
    assert result.error.exit_status == 19
    assert result.error.details["reason"] == "hooks_path_redirected"
    assert result.error.hint is not None and BLOCK in result.error.hint
    assert "hint:" in process.stderr and BLOCK.rstrip() in process.stderr
    assert not hook_file(repo).exists()
    assert list(elsewhere.iterdir()) == []
    assert registry_bytes() is None


def test_a_relative_hooks_path_inside_the_worktree_is_redirected_too(
    run_wtenv: Run, repo: Path
) -> None:
    git(repo, "config", "core.hooksPath", ".husky")

    process = run_wtenv(["hook", "install", "--json"], repo)

    assert process.returncode == 19
    result = HookInstallResult.model_validate_json(process.stdout)
    assert result.error is not None and result.error.details["reason"] == "hooks_path_redirected"
    assert not (repo / ".husky").exists()


def test_a_hook_that_is_not_a_shell_script_exits_19_and_stays_as_it_is(
    run_wtenv: Run, repo: Path
) -> None:
    original = "#!/usr/bin/env python3\nprint('mine')\n"
    hook_file(repo).write_text(original, encoding="utf-8")

    process = run_wtenv(["hook", "install", "--json"], repo)

    result = HookInstallResult.model_validate_json(process.stdout)
    assert process.returncode == 19
    assert result.error is not None
    assert result.error.details["reason"] == "hook_not_shell"
    assert result.error.hint is not None and BLOCK in result.error.hint
    assert hook_file(repo).read_text(encoding="utf-8") == original
    assert registry_bytes() is None


@pytest.mark.parametrize("command", [["install"], ["uninstall"]])
def test_damaged_markers_exit_19_and_leave_the_hook_as_it_is(
    run_wtenv: Run, repo: Path, command: list[str]
) -> None:
    damaged = "#!/bin/sh\n# >>> wtenv managed (written by `wtenv hook install`; do not edit) >>>\n"
    hook_file(repo).write_text(damaged, encoding="utf-8")

    process = run_wtenv(["hook", *command, "--json"], repo)

    assert process.returncode == 19
    assert hook_file(repo).read_text(encoding="utf-8") == damaged
    assert '"reason":"markers_damaged"' in process.stdout


def test_uninstall_does_not_need_the_default_hooks_directory_to_be_the_active_one(
    run_wtenv: Run, repo: Path, tmp_path: Path
) -> None:
    """`hook uninstall` removes what `install` wrote even after `core.hooksPath` moved away."""
    install(run_wtenv, repo)
    git(repo, "config", "core.hooksPath", str(tmp_path / "elsewhere"))

    result = uninstall(run_wtenv, repo)

    assert result.ok and result.action == "removed"
    assert not hook_file(repo).exists()


def test_an_unreadable_registry_exits_16_before_the_hook_is_written(
    run_wtenv: Run, repo: Path
) -> None:
    registry_path().parent.mkdir(parents=True)
    registry_path().write_text("not json", encoding="utf-8")

    process = run_wtenv(["hook", "install", "--json"], repo)

    assert process.returncode == 16
    assert not hook_file(repo).exists()
    assert registry_path().read_text(encoding="utf-8") == "not json"


# --- outside any worktree (cli.md, "Where it runs") ------------------------------------------


@pytest.mark.parametrize("command", [["install"], ["uninstall"], ["uninstall", "--dry-run"]])
def test_hook_commands_outside_a_worktree_exit_4_and_write_nothing(
    run_wtenv: Run, tmp_path: Path, command: list[str]
) -> None:
    outside = tmp_path / "not-a-repository"
    outside.mkdir()

    process = run_wtenv(
        ["hook", *command, "--json"], outside, env={"GIT_CEILING_DIRECTORIES": str(tmp_path)}
    )

    document = (
        HookInstallResult if command == ["install"] else HookUninstallResult
    ).model_validate_json(process.stdout)
    assert process.returncode == 4
    assert document.error is not None and document.error.code is ErrorCode.NOT_IN_WORKTREE
    assert document.command == f"hook {command[0]}"
    assert registry_bytes() is None
    assert list(outside.iterdir()) == []


def test_hook_commands_print_text_for_people_without_json(run_wtenv: Run, repo: Path) -> None:
    installed = run_wtenv(["hook", "install"], repo)
    removed = run_wtenv(["hook", "uninstall"], repo)
    absent = run_wtenv(["hook", "uninstall"], repo)

    path = str(hook_file(repo).resolve())
    assert installed.returncode == removed.returncode == absent.returncode == 0
    assert path in installed.stdout and "installed" in installed.stdout
    assert path in removed.stdout and "removed" in removed.stdout
    assert "no wtenv block" in absent.stdout
    assert installed.stderr == removed.stderr == absent.stderr == ""


# === wtenv exec (T121) =========================================================================

SQLITE_TOML = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'
BEGIN = "# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>"
END = "# <<< wtenv managed <<<"


def provisioned(
    run_wtenv: Run,
    repo: Path,
    add_worktree: AddWorktree,
    toml: str | None = None,
    name: str = "feature-x",
) -> Path:
    """Add a worktree of `repo` (whose commit holds `toml`, when given) and run `up` in it."""
    if toml is not None:
        (repo / "wtenv.toml").write_text(toml, encoding="utf-8")
        make_sqlite_template(repo / "db" / "dev.sqlite3")
        commit_all(repo)
    worktree = add_worktree(repo, name, name.replace(" ", "-"))
    process = run_wtenv(["up", "--json"], worktree)
    assert process.returncode == 0, process.stderr
    return worktree


def env_file(worktree: Path) -> Path:
    return worktree / ".env.local"


def section_lines(worktree: Path) -> dict[str, str]:
    """The raw `NAME=value` lines between the markers of the env file, as written, split once.

    Read straight from the file, without wtenv's own reader: the values are still quoted.
    """
    lines = env_file(worktree).read_text(encoding="utf-8").splitlines()
    inside = lines[lines.index(BEGIN) + 1 : lines.index(END)]
    return dict(line.split("=", 1) for line in inside)


def child_env(process: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """Parse the output of `env`."""
    return dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)


def exec_env(run_wtenv: Run, cwd: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """Run `wtenv exec -- env` in `cwd` and return the environment the command saw."""
    process = run_wtenv(["exec", "--", "env"], cwd, env)
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    return child_env(process)


def failed_exec(process: subprocess.CompletedProcess[str]) -> ExecResult:
    """Return the `ExecResult` that `wtenv exec --json` printed when wtenv itself failed."""
    assert process.returncode == 125, process.stderr
    assert len(process.stdout.splitlines()) == 1, process.stdout
    document = ExecResult.model_validate_json(process.stdout)
    assert document.ok is False and document.command == "exec"
    assert document.error is not None and document.error.exit_status == 125
    return document


def not_run(worktree: Path) -> Path:
    """The file that a command `touch`es when it runs."""
    return worktree / "command-ran"


TOUCH = ["sh", "-c", "touch command-ran"]


# --- scenario 5 (FR-056): the command sees the section's variables ---------------------------


def test_the_command_sees_every_variable_of_the_section_and_the_developers_lines_are_not_loaded(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(
        run_wtenv, repo, add_worktree, 'ports = ["PORT", "API_PORT", "WEB_PORT"]\n'
    )
    with env_file(worktree).open("a", encoding="utf-8") as file:
        file.write("DEVELOPER_VARIABLE=mine\n")
    section = section_lines(worktree)

    seen = exec_env(run_wtenv, worktree)

    assert set(section) == {"PORT", "API_PORT", "WEB_PORT"}
    for name, value in section.items():
        assert seen[name] == value
    assert "DEVELOPER_VARIABLE" not in seen
    assert seen["PATH"] == os.environ["PATH"]  # the current environment is kept


def test_a_variable_of_the_section_replaces_the_one_already_in_the_environment(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    seen = exec_env(run_wtenv, worktree, {"PORT": "1", "UNRELATED": "kept"})

    assert seen["PORT"] == section_lines(worktree)["PORT"] != "1"
    assert seen["UNRELATED"] == "kept"


def test_exec_works_from_a_subdirectory(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    (worktree / "deep" / "er").mkdir(parents=True)

    seen = exec_env(run_wtenv, worktree / "deep" / "er")

    assert seen["PORT"] == section_lines(worktree)["PORT"]


def test_standard_streams_pass_through_and_the_exit_status_is_the_commands(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "wtenv",
            "exec",
            "--",
            "sh",
            "-c",
            "cat; echo to-stderr >&2; exit 7",
        ],
        cwd=worktree,
        input="from-stdin\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 7
    assert process.stdout == "from-stdin\n"
    assert process.stderr == "to-stderr\n"


def test_wtenv_replaces_itself_with_the_command_so_signals_reach_it(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    with subprocess.Popen(
        [sys.executable, "-m", "wtenv", "exec", "--", "sh", "-c", "echo $$"],
        cwd=worktree,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        output, _ = process.communicate()

    assert int(output) == process.pid


def test_exec_does_not_read_wtenv_toml_and_takes_no_worktree_lock(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    (worktree / "wtenv.toml").write_text("this is not toml = [", encoding="utf-8")

    # `up` and `down` hold this lock; `exec` would wait for it up to 60 seconds (FR-076).
    with worktree_lock(current_worktree(worktree).git_dir):
        process = subprocess.run(
            [sys.executable, "-m", "wtenv", "exec", "--", "env"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    assert process.returncode == 0, process.stderr
    assert child_env(process)["PORT"] == section_lines(worktree)["PORT"]


# --- reading R1: every case is checked against the env file, not the registry ----------------


def test_a_value_in_single_quotes_reaches_the_command_without_them(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree, SQLITE_TOML, name="my feature")
    assert " " in str(worktree)
    section = section_lines(worktree)
    quoted = section["DATABASE_URL"]
    assert quoted.startswith("'") and quoted.endswith("'")  # the space forced quoting

    seen = exec_env(run_wtenv, worktree)

    assert seen["DATABASE_URL"] == quoted[1:-1]
    assert seen["DATABASE_URL"] == f"sqlite:///{worktree / '.wtenv' / 'dev.sqlite3'}"
    assert seen["PORT"] == section["PORT"]


def test_a_value_edited_by_hand_inside_the_section_reaches_the_command_as_written(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    path = env_file(worktree)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(f"PORT={section_lines(worktree)['PORT']}", "PORT=29999"))

    seen = exec_env(run_wtenv, worktree)

    assert seen["PORT"] == "29999"


def test_damaged_markers_exit_125_with_env_file_unusable_and_the_command_does_not_run(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    path = env_file(worktree)
    path.write_text(path.read_text(encoding="utf-8").replace(END + "\n", ""), encoding="utf-8")

    process = run_wtenv(["exec", "--json", "--", *TOUCH], worktree)

    result = failed_exec(process)
    assert result.error is not None
    assert result.error.code is ErrorCode.ENV_FILE_UNUSABLE
    assert result.error.details["reason"] == "markers_damaged"
    assert result.error.details["path"] == str(path)
    assert "wtenv: error [env_file_unusable]" in process.stderr
    assert not not_run(worktree).exists()


def test_a_deleted_env_file_exits_125_with_reason_missing(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    env_file(worktree).unlink()

    process = run_wtenv(["exec", "--json", "--", *TOUCH], worktree)

    result = failed_exec(process)
    assert result.error is not None
    assert result.error.code is ErrorCode.ENV_FILE_UNUSABLE
    assert result.error.details["reason"] == "missing"
    assert not not_run(worktree).exists()


def test_an_env_file_without_a_section_exits_125_with_reason_no_section(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    env_file(worktree).write_text("DEVELOPER_VARIABLE=mine\n", encoding="utf-8")

    process = run_wtenv(["exec", "--json", "--", *TOUCH], worktree)

    result = failed_exec(process)
    assert result.error is not None
    assert result.error.code is ErrorCode.ENV_FILE_UNUSABLE
    assert result.error.details["reason"] == "no_section"
    assert not not_run(worktree).exists()


def test_without_database_in_the_config_after_up_the_command_gets_no_database_url(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree, SQLITE_TOML)
    assert "DATABASE_URL" in exec_env(run_wtenv, worktree)
    (worktree / "wtenv.toml").write_text("", encoding="utf-8")
    assert run_wtenv(["up", "--json"], worktree).returncode == 0
    assert entry_of(worktree) is not None and entry_of(worktree).databases  # type: ignore[union-attr]
    assert "DATABASE_URL" not in section_lines(worktree)

    seen = exec_env(run_wtenv, worktree)

    assert "DATABASE_URL" not in seen
    assert "DATABASE_URL" not in os.environ
    assert seen["PORT"] == section_lines(worktree)["PORT"]


# --- scenario 6 (FR-057): a worktree that is not provisioned -----------------------------------


def test_an_unprovisioned_worktree_exits_125_and_the_command_does_not_run(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")

    process = run_wtenv(["exec", "--json", "--", *TOUCH], worktree)

    result = failed_exec(process)
    assert result.error is not None
    assert result.error.code is ErrorCode.NOT_PROVISIONED
    assert result.error.details["status"] == "unprovisioned"
    assert result.error.details["path"] == str(worktree)
    assert "wtenv: error [not_provisioned]" in process.stderr
    assert not not_run(worktree).exists()


def test_an_incomplete_worktree_exits_125_with_status_incomplete(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    (repo / "wtenv.toml").write_text('post_up = ["exit 3"]\n', encoding="utf-8")
    commit_all(repo)
    worktree = add_worktree(repo, "feature-x", "feature-x")
    assert run_wtenv(["up", "--json"], worktree).returncode == 12  # post_up_failed

    process = run_wtenv(["exec", "--json", "--", *TOUCH], worktree)

    result = failed_exec(process)
    assert result.error is not None and result.error.code is ErrorCode.NOT_PROVISIONED
    assert result.error.details["status"] == "incomplete"
    assert not not_run(worktree).exists()


def test_a_worktree_that_moved_since_up_exits_125_with_status_unverifiable(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    moved = repo.parent / "moved"
    git(repo, "worktree", "move", str(worktree), str(moved))

    process = run_wtenv(["exec", "--json", "--", *TOUCH], moved)

    result = failed_exec(process)
    assert result.error is not None and result.error.code is ErrorCode.NOT_PROVISIONED
    assert result.error.details["status"] == "unverifiable"
    assert not (moved / "command-ran").exists()


def test_exec_outside_a_worktree_exits_125_with_not_in_worktree(
    run_wtenv: Run, tmp_path: Path
) -> None:
    outside = tmp_path / "not-a-repository"
    outside.mkdir()

    process = run_wtenv(
        ["exec", "--json", "--", "env"], outside, env={"GIT_CEILING_DIRECTORIES": str(tmp_path)}
    )

    result = failed_exec(process)
    assert result.error is not None and result.error.code is ErrorCode.NOT_IN_WORKTREE


def test_without_json_a_wtenv_failure_goes_to_standard_error_only(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "feature-x", "feature-x")

    process = run_wtenv(["exec", "--", "env"], worktree)

    assert process.returncode == 125
    assert process.stdout == ""
    assert process.stderr.startswith("wtenv: error [not_provisioned]: ")
    assert "hint: Run `wtenv up`" in process.stderr


# --- the command line and the exit statuses of env(1) --------------------------------------------


@pytest.mark.parametrize(
    "args", [["exec", "env"], ["exec"], ["exec", "--"], ["exec", "--bogus", "--", "env"]]
)
def test_a_usage_error_in_exec_exits_125(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree, args: list[str]
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(args, worktree)

    assert process.returncode == 125
    assert process.stdout == ""
    assert process.stderr.startswith("wtenv: error [usage_error]: ")


def test_a_usage_error_in_exec_with_json_is_an_exec_result_with_status_125(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["exec", "--json", "env"], worktree)

    result = failed_exec(process)
    assert result.error is not None and result.error.code is ErrorCode.USAGE_ERROR


def test_json_after_the_double_dash_belongs_to_the_command(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["exec", "--", "sh", "-c", 'echo "$1"', "sh", "--json"], worktree)

    assert (process.returncode, process.stdout, process.stderr) == (0, "--json\n", "")


def test_a_command_that_is_not_found_exits_127(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["exec", "--", "wtenv-no-such-command"], worktree)

    assert process.returncode == 127
    assert process.stdout == ""
    assert "wtenv-no-such-command" in process.stderr


def test_a_command_that_is_found_but_cannot_run_exits_126(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)
    script = worktree / "not-executable"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o644)

    process = run_wtenv(["exec", "--", "./not-executable"], worktree)

    assert process.returncode == 126
    assert process.stdout == ""
    assert "not-executable" in process.stderr


def test_a_directory_as_the_command_exits_126(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["exec", "--", "."], worktree)

    assert process.returncode == 126


def test_a_command_not_found_with_json_prints_no_document(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    """`--json` is for wtenv's own failures; a command that cannot start is not one (cli.md)."""
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["exec", "--json", "--", "wtenv-no-such-command"], worktree)

    assert process.returncode == 127
    assert process.stdout == ""


def test_when_the_command_runs_wtenv_prints_nothing_of_its_own(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["exec", "--json", "--", "true"], worktree)

    assert (process.returncode, process.stdout, process.stderr) == (0, "", "")
