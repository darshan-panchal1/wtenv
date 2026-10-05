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
from helpers import commit_all, git

from wtenv.errors import ErrorCode
from wtenv.hooks import BLOCK
from wtenv.identity import current_worktree
from wtenv.output import HookInstallResult, HookUninstallResult, ItemKind
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
