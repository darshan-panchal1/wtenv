"""Every `--json` document, from every command (T131; FR-058; US6 scenarios 1 and 2; SC-008).

For each command the test runs wtenv on real git worktrees and checks that standard output is
exactly one JSON document, that it validates against the matching model of the contract file
`contracts/json_models.py` itself (not wtenv's copy of it), that `schema_version` is present, that
`ok` is true exactly when the exit status is 0, and that everything else went to standard error.
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from contract_helpers import document, git

from wtenv.locks import ensure_state_dir
from wtenv.registry import registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def provisioned(run_wtenv: Run, repo: Path, add_worktree: AddWorktree, name: str = "one") -> Path:
    """Add a worktree and run `wtenv up` in it."""
    worktree = add_worktree(repo, name, name)
    assert run_wtenv(["up"], worktree).returncode == 0
    return worktree


# --- successful documents -----------------------------------------------------------------------


def test_version(run_wtenv: Run, contract: ModuleType, tmp_path: Path) -> None:
    process = run_wtenv(["--version", "--json"], tmp_path)

    assert process.returncode == 0
    assert document(contract, process, "VersionResult", "version").version


def test_up(run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree) -> None:
    worktree = add_worktree(repo, "one", "one")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    assert document(contract, process, "UpResult", "up").worktree is not None
    # A second run is a document too: everything is unchanged.
    again = run_wtenv(["up", "--json"], worktree)
    assert again.returncode == 0
    document(contract, again, "UpResult", "up")


def test_down_dry_run_and_down(
    run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    planned = run_wtenv(["down", "--dry-run", "--json"], worktree)
    real = run_wtenv(["down", "--json"], worktree)

    assert planned.returncode == real.returncode == 0
    assert document(contract, planned, "DownResult", "down").dry_run is True
    assert document(contract, real, "DownResult", "down").dry_run is False


def test_gc_dry_run_and_gc(
    run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    gone = provisioned(run_wtenv, repo, add_worktree, "gone")
    git(repo, "worktree", "remove", "--force", str(gone))

    planned = run_wtenv(["gc", "--dry-run", "--json"], repo)
    real = run_wtenv(["gc", "--json"], repo)

    assert planned.returncode == real.returncode == 0
    assert document(contract, planned, "GcResult", "gc").would_release == [str(gone)]
    assert document(contract, real, "GcResult", "gc").released == [str(gone)]


def test_gc_release(
    run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree, tmp_path: Path
) -> None:
    moved = provisioned(run_wtenv, repo, add_worktree, "moved")
    shutil.move(moved, tmp_path / "elsewhere")  # git is not told ...
    git(repo, "worktree", "prune")  # ... until it prunes: `--release` refuses while git has it (L3)

    process = run_wtenv(["gc", "--release", str(moved), "--json"], repo)

    assert process.returncode == 0, process.stderr
    assert document(contract, process, "GcResult", "gc").released == [str(moved)]


def test_ls(run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["ls", "--json"], worktree)

    assert process.returncode == 0
    worktrees = document(contract, process, "LsResult", "ls").worktrees
    # The one with an entry, and the repository's own worktree, which has none.
    assert sorted(w.status.value for w in worktrees) == ["provisioned", "unprovisioned"]


def test_ls_with_nothing_recorded(run_wtenv: Run, contract: ModuleType, tmp_path: Path) -> None:
    process = run_wtenv(["ls", "--json"], tmp_path)

    assert process.returncode == 0
    assert document(contract, process, "LsResult", "ls").worktrees == []


def test_doctor_when_healthy(
    run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["doctor", "--json"], worktree)

    assert process.returncode == 0, process.stderr
    parsed = document(contract, process, "DoctorResult", "doctor")
    assert [d.name for d in parsed.dependencies] == ["git", "docker", "postgres"]


def test_doctor_when_it_finds_a_problem_is_not_ok_and_exits_17(
    run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    gone = provisioned(run_wtenv, repo, add_worktree, "gone")
    git(repo, "worktree", "remove", "--force", str(gone))

    process = run_wtenv(["doctor", "--json"], repo)

    assert process.returncode == 17
    parsed = document(contract, process, "DoctorResult", "doctor")
    assert parsed.error.code.value == "problems_found"
    assert [f.code.value for f in parsed.findings] == ["orphaned_worktree"]


def test_hook_install_and_both_forms_of_uninstall(
    run_wtenv: Run, contract: ModuleType, repo: Path
) -> None:
    installed = run_wtenv(["hook", "install", "--json"], repo)
    planned = run_wtenv(["hook", "uninstall", "--dry-run", "--json"], repo)
    removed = run_wtenv(["hook", "uninstall", "--json"], repo)

    assert installed.returncode == planned.returncode == removed.returncode == 0
    assert document(contract, installed, "HookInstallResult", "hook install").action == "installed"
    would = document(contract, planned, "HookUninstallResult", "hook uninstall")
    assert would.dry_run is True and would.would_remove
    gone = document(contract, removed, "HookUninstallResult", "hook uninstall")
    assert gone.action == "removed" and gone.removed


def test_exec_prints_a_document_only_when_wtenv_itself_fails(
    run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")  # never provisioned

    process = run_wtenv(["exec", "--json", "--", "true"], worktree)

    assert process.returncode == 125
    parsed = document(contract, process, "ExecResult", "exec")
    assert parsed.error.code.value == "not_provisioned" and parsed.error.exit_status == 125


def test_exec_prints_nothing_of_its_own_when_the_command_runs(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = provisioned(run_wtenv, repo, add_worktree)

    process = run_wtenv(["exec", "--json", "--", "echo", "from the command"], worktree)

    assert process.returncode == 0
    assert process.stdout == "from the command\n"  # the command owns standard output


# --- failing documents: still exactly one, with ok false -----------------------------------------

MODELS = {
    "up": "UpResult",
    "down": "DownResult",
    "gc": "GcResult",
    "ls": "LsResult",
    "doctor": "DoctorResult",
    "hook install": "HookInstallResult",
    "hook uninstall": "HookUninstallResult",
}


@pytest.mark.parametrize("command", ["up", "down", "hook install", "hook uninstall"])
def test_a_command_that_needs_a_worktree_fails_with_a_document_outside_one(
    run_wtenv: Run, contract: ModuleType, tmp_path: Path, command: str
) -> None:
    process = run_wtenv([*command.split(), "--json"], tmp_path)

    assert process.returncode == 4
    parsed = document(contract, process, MODELS[command], command)
    assert parsed.error.code.value == "not_in_worktree"


@pytest.mark.parametrize("command", list(MODELS))
def test_a_registry_that_is_not_json_fails_every_command_with_a_document(
    run_wtenv: Run, contract: ModuleType, repo: Path, command: str
) -> None:
    ensure_state_dir()
    registry_path().write_text("this is not json", encoding="utf-8")

    process = run_wtenv([*command.split(), "--json"], repo)

    assert process.returncode == 16, process.stderr
    parsed = document(contract, process, MODELS[command], command)
    assert parsed.error.code.value == "registry_unreadable"
    assert registry_path().read_text(encoding="utf-8") == "this is not json"


@pytest.mark.parametrize(
    ("args", "command"),
    [
        (["up", "--bogus", "--json"], "up"),
        (["bogus", "--json"], None),
        (["--json"], None),
        (["ls", "extra", "--json"], "ls"),
        (["hook", "--json"], None),
    ],
)
def test_a_usage_error_is_one_document_with_exit_status_2(
    run_wtenv: Run, contract: ModuleType, tmp_path: Path, args: list[str], command: str | None
) -> None:
    process = run_wtenv(args, tmp_path)

    assert process.returncode == 2, process.stderr
    parsed = document(contract, process, "Result", command)
    assert parsed.error.code.value == "usage_error"


def test_a_failed_up_is_one_document_and_the_rest_is_on_standard_error(
    run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")
    (worktree / "wtenv.toml").write_text("ports = 5\n", encoding="utf-8")

    process = run_wtenv(["up", "--json"], worktree)

    assert process.returncode == 3
    parsed = document(contract, process, "UpResult", "up")
    assert parsed.error.code.value == "config_invalid"
    assert "hint:" in process.stderr


def test_without_json_standard_output_is_text_and_not_a_document(
    run_wtenv: Run, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "one", "one")

    for command in (["up"], ["ls"], ["doctor"], ["down"]):
        process = run_wtenv(command, worktree)
        assert process.returncode == 0, (command, process.stderr)
        assert not process.stdout.lstrip().startswith("{"), command
