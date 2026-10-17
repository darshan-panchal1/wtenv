"""Every `--json` document, from every command (T131; FR-058; US6 scenarios 1 and 2; SC-008).

For each command the test runs wtenv on real git worktrees and checks that standard output is
exactly one JSON document, that it validates against the matching model of the contract file
`contracts/json_models.py` itself (not wtenv's copy of it), that `schema_version` is present, that
`ok` is true exactly when the exit status is 0, and that everything else went to standard error.
"""

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from contract_helpers import document, git

from wtenv.identity import current_worktree
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


# --- spec 002: every new error code, reason and warning is one document (T091; SC-009) ----------
# Each is produced by its own command with `--json`; `run_wtenv` closes standard input, so a
# prompt would fail the test. The documents validate against the contract's models.

STACK = "services:\n  backend:\n    image: valkey/valkey:latest\n    pull_policy: never\n"
OVERRIDE = '    ports:\n      - "8000:6379"\n'
REPOSITORY_OVERRIDE_TEXT = "services:\n  backend:\n" + OVERRIDE
TOML = 'ports = ["PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'
ISOLATION = ".wtenv/compose.isolation.yaml"


def commit(worktree: Path) -> None:
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "files")


def exec_only(
    repo: Path,
    add_worktree: AddWorktree,
    name: str = "eo",
    toml: str = TOML,
    stack: str = STACK,
    extra: dict[str, str] | None = None,
) -> Path:
    """A worktree whose repository owns `compose.override.yml`."""
    worktree = add_worktree(repo, name, name.replace(":", "-"))
    files = {
        "compose.yaml": stack,
        "wtenv.toml": toml,
        "compose.override.yml": REPOSITORY_OVERRIDE_TEXT,
    }
    for relative, text in {**files, **(extra or {})}.items():
        (worktree / relative).write_text(text, encoding="utf-8")
    commit(worktree)
    return worktree


def run_up(run_wtenv: Run, worktree: Path) -> "subprocess.CompletedProcess[str]":
    return run_wtenv(["up", "--json"], worktree)


def failed_document(
    contract: ModuleType, process: "subprocess.CompletedProcess[str]", model: str, command: str
) -> Any:
    return document(contract, process, model, command)


def hand_edit(worktree: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = registry_path()
    registry = json.loads(path.read_text(encoding="utf-8"))
    change(registry["worktrees"][current_worktree(worktree).git_dir])
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def spoil(worktree: Path, reason: str, decoy: Path) -> None:
    """Break the isolation settings of an exec-only worktree in the way `reason` names."""
    isolation = worktree / ISOLATION
    if reason == "recorded_value_invalid":
        hand_edit(worktree, lambda entry: entry["compose"].update(override="deploy/other.yaml"))
    elif reason == "symlink":
        isolation.unlink()
        decoy.write_text("services: {}\n", encoding="utf-8")
        isolation.symlink_to(decoy)
    elif reason == "isolation_file_missing":
        isolation.unlink()
    elif reason == "isolation_file_not_wtenvs":
        isolation.write_text("services: {}\n", encoding="utf-8")
    elif reason == "repository_override_missing":
        (worktree / "compose.override.yml").unlink()
    elif reason == "override_appeared":
        (worktree / "docker-compose.override.yaml").write_text("services: {}\n", encoding="utf-8")


ISOLATION_REASONS = [
    "compose_env_override",
    "recorded_value_invalid",
    "symlink",
    "isolation_file_missing",
    "isolation_file_not_wtenvs",
    "repository_override_missing",
    "override_appeared",
]


@pytest.mark.parametrize("reason", ISOLATION_REASONS)
def test_every_isolation_unavailable_reason_of_exec_is_one_document(
    reason: str,
    docker: None,
    run_wtenv: Run,
    contract: ModuleType,
    repo: Path,
    add_worktree: AddWorktree,
    tmp_path: Path,
) -> None:
    worktree = exec_only(repo, add_worktree)
    assert run_up(run_wtenv, worktree).returncode == 0
    env = {"COMPOSE_PROJECT_NAME": "x"} if reason == "compose_env_override" else {}
    spoil(worktree, reason, tmp_path / "decoy.yaml")

    process = run_wtenv(["exec", "--json", "--", "true"], worktree, env)

    assert process.returncode == 125, process.stdout + process.stderr
    parsed = document(contract, process, "ExecResult", "exec")
    assert parsed.error.code.value == "isolation_unavailable"
    assert parsed.error.exit_status == 125
    assert parsed.error.details["reason"] == reason


def test_isolation_unavailable_of_up_has_exit_status_20_and_is_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    other = exec_only(repo, add_worktree, "other")
    assert run_up(run_wtenv, other).returncode == 0
    # Without `[compose]`, so that only check A (before the first post-up command) can refuse.
    worktree = add_worktree(repo, "mine", "mine")
    (worktree / "wtenv.toml").write_text('ports = ["PORT"]\npost_up = ["true"]\n', encoding="utf-8")
    commit(worktree)
    env = {"COMPOSE_FILE": str(other / ISOLATION)}  # another worktree's isolation file

    process = run_wtenv(["up", "--json"], worktree, env)

    assert process.returncode == 20, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert parsed.error.code.value == "isolation_unavailable"
    assert parsed.error.details["reason"] == "compose_env_override"


def test_override_ambiguous_is_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = exec_only(
        repo, add_worktree, extra={"docker-compose.override.yaml": REPOSITORY_OVERRIDE_TEXT}
    )

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 21, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert parsed.error.code.value == "override_ambiguous"
    assert len(parsed.error.details["files"]) == 2


def test_port_variable_conflict_is_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    toml = TOML.replace('ports = ["PORT"]', 'ports = ["PORT", "WTENV_PORT_BACKEND_6379_TCP"]')
    worktree = exec_only(repo, add_worktree, toml=toml)

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 22, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert parsed.error.code.value == "port_variable_conflict"


DATABASE = (
    '\n[database]\ntype = "compose"\nservice = "db"\n'
    'url = "postgresql://postgres:pw@localhost:{port}/postgres"\n'
)
DB_SERVICE = (
    "  db:\n    image: postgres:17\n    pull_policy: never\n"
    '    ports:\n      - "5432:5432"\n{volumes}'
)
STORAGES = {
    "fixed_name_volume": "    volumes:\n      - data:/d\nvolumes:\n  data:\n    name: wtenv-contract-fixed\n",
    "external_volume": "    volumes:\n      - data:/d\nvolumes:\n  data:\n    external: true\n",
    "device_not_absolute": (
        "    volumes:\n      - data:/d\nvolumes:\n  data:\n    driver_opts:\n"
        "      type: none\n      o: bind\n      device: ./data\n"
    ),
    "host_directory": "    volumes:\n      - /var/tmp:/d\n",
}


@pytest.mark.parametrize("storage", list(STORAGES))
def test_database_storage_shared_is_one_document_for_each_storage(
    storage: str,
    docker: None,
    run_wtenv: Run,
    contract: ModuleType,
    repo: Path,
    add_worktree: AddWorktree,
) -> None:
    worktree = add_worktree(repo, "db", "db")
    (worktree / "compose.yaml").write_text(
        "services:\n" + DB_SERVICE.format(volumes=STORAGES[storage]), encoding="utf-8"
    )
    (worktree / "wtenv.toml").write_text(TOML + DATABASE, encoding="utf-8")
    commit(worktree)

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 23, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert parsed.error.code.value == "database_storage_shared"
    assert parsed.error.details["storage"] == storage


def test_compose_path_separator_is_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = exec_only(repo, add_worktree, "has:colon")

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 19, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert parsed.error.code.value == "unsupported"
    assert parsed.error.details["reason"] == "compose_path_separator"


def warning_codes(parsed: Any) -> list[str]:
    return [warning.code.value for warning in parsed.warnings]


def test_the_exec_only_and_the_database_warnings_are_in_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    stack = (
        "services:\n"
        + DB_SERVICE.format(volumes="")
        + "  backend:\n    image: valkey/valkey:latest\n    pull_policy: never\n"
    )
    worktree = exec_only(repo, add_worktree, toml=TOML + DATABASE, stack=stack)

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert {"compose_isolation_exec_only", "compose_database_data_kept"} <= set(
        warning_codes(parsed)
    )
    assert parsed.compose_database.service == "db"


def test_the_adopted_warning_is_in_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "adopt", "adopt")
    (worktree / "compose.yaml").write_text(STACK + OVERRIDE, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(TOML, encoding="utf-8")
    commit(worktree)
    assert run_up(run_wtenv, worktree).returncode == 0  # wtenv's own compose.override.yaml
    (worktree / "compose.override.yaml").write_text(REPOSITORY_OVERRIDE_TEXT, encoding="utf-8")
    git(worktree, "add", "--force", "compose.override.yaml")  # a checkout brought the repository's

    process = run_up(run_wtenv, worktree)

    assert process.returncode == 0, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert "compose_override_adopted" in warning_codes(parsed)


def test_the_excluded_warning_is_in_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    first = add_worktree(repo, "first", "first")
    (first / "compose.yaml").write_text(STACK + OVERRIDE, encoding="utf-8")
    (first / "wtenv.toml").write_text(TOML, encoding="utf-8")
    commit(first)
    assert run_up(run_wtenv, first).returncode == 0  # adds /compose.override.yaml to the block
    other = add_worktree(repo, "other", "other")
    (other / "compose.yaml").write_text(STACK, encoding="utf-8")
    (other / "wtenv.toml").write_text(TOML, encoding="utf-8")
    commit(other)
    (other / "compose.override.yaml").write_text(REPOSITORY_OVERRIDE_TEXT, encoding="utf-8")

    process = run_up(run_wtenv, other)

    assert process.returncode == 0, process.stdout + process.stderr
    parsed = document(contract, process, "UpResult", "up")
    assert "compose_override_excluded" in warning_codes(parsed)


def test_kept_items_are_in_one_document(
    docker: None, run_wtenv: Run, contract: ModuleType, repo: Path, add_worktree: AddWorktree
) -> None:
    worktree = add_worktree(repo, "kept", "kept")
    (worktree / "compose.yaml").write_text(STACK + OVERRIDE, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(TOML, encoding="utf-8")
    commit(worktree)
    assert run_up(run_wtenv, worktree).returncode == 0
    (worktree / "compose.override.yaml").write_text(REPOSITORY_OVERRIDE_TEXT, encoding="utf-8")
    git(worktree, "add", "--force", "compose.override.yaml")

    process = run_wtenv(["down", "--dry-run", "--json"], worktree)

    assert process.returncode == 0, process.stdout + process.stderr
    parsed = document(contract, process, "DownResult", "down")
    assert [item.reason for item in parsed.kept_items] == ["repository_owned"]
