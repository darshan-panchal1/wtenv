"""One triggering case for every error code, checking its code and its exit status (FR-059).

T132: the codes that need no git worktree (1, 2, 4, 16). T133: the codes of `up` (3, 6 to 12).
T134: the codes of the other commands (13, 14, 15, 17, 18, 19), and 5 inside `exec`'s 125.

Every case checks the same four things: the exit status, the error code and `exit_status` in the
one JSON document, that the document validates against the contract's models, and that standard
error carries `wtenv: error [<code>]`. Each test works in a temporary repository with a temporary
state directory.
"""

import json
import re
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from contract_helpers import END_MARKER, document, git

from wtenv import cli, listing, locks, provision
from wtenv.errors import EXIT_STATUS, ErrorCode
from wtenv.identity import current_worktree
from wtenv.locks import ensure_state_dir
from wtenv.registry import registry_path

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

CLI_CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "001-worktree-runtime-isolation"
    / "contracts"
    / "cli.md"
)


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


@pytest.fixture
def worktree(repo: Path, add_worktree: AddWorktree) -> Path:
    return add_worktree(repo, "one", "one")


def fails(
    run_wtenv: Run,
    contract: ModuleType,
    args: list[str],
    cwd: Path,
    *,
    model: str,
    command: str | None,
    code: ErrorCode,
    env: dict[str, str] | None = None,
    status: int | None = None,
) -> "subprocess.CompletedProcess[str]":
    """Run wtenv and check that it fails with `code` and the exit status of that code.

    `status` is for `exec`, which reports every failure of wtenv as 125.
    """
    expected = EXIT_STATUS[code] if status is None else status
    process = run_wtenv(args, cwd, env)
    assert process.returncode == expected, (process.returncode, process.stdout, process.stderr)
    parsed = document(contract, process, model, command)
    assert parsed.error.code is not None and parsed.error.code.value == code.value
    assert parsed.error.exit_status == expected
    assert parsed.ok is False
    return process


def closed_port() -> int:
    """Return a local port that nothing listens on now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def write_config(root: Path, text: str) -> None:
    (root / "wtenv.toml").write_text(text, encoding="utf-8")


# --- the table of cli.md is the table of wtenv --------------------------------------------------


def test_the_nineteen_codes_and_statuses_of_cli_md_are_the_ones_wtenv_uses(
    contract: ModuleType,
) -> None:
    text = CLI_CONTRACT.read_text(encoding="utf-8")
    section = text.split("## Error codes and exit statuses")[1].split("### Error details")[0]
    documented = {
        match.group(2): int(match.group(1))
        for match in re.finditer(r"^\| (\d+) \| `([a-z_]+)` \|", section, flags=re.MULTILINE)
    }

    assert len(documented) == 19
    assert documented == {code.value: status for code, status in EXIT_STATUS.items()}
    assert {code.value for code in contract.ErrorCode} == set(documented)


# --- T132: no git worktree needed ----------------------------------------------------------------


def test_1_internal_error_is_an_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], contract: ModuleType
) -> None:
    def broken() -> None:
        raise RuntimeError("something nobody expected")

    monkeypatch.setattr("wtenv.listing.list_worktrees", broken)

    status = cli.main(["ls", "--json"])

    captured = capsys.readouterr()
    assert status == 1
    parsed = contract.Result.model_validate_json(captured.out)
    assert parsed.error.code.value == "internal_error" and parsed.error.exit_status == 1
    assert parsed.error.details == {"exception": "RuntimeError"}
    assert "wtenv: error [internal_error]" in captured.err


def test_2_usage_error_is_an_unknown_option(
    run_wtenv: Run, contract: ModuleType, tmp_path: Path
) -> None:
    fails(
        run_wtenv,
        contract,
        ["up", "--bogus", "--json"],
        tmp_path,
        model="Result",
        command="up",
        code=ErrorCode.USAGE_ERROR,
    )


def test_4_not_in_worktree_is_a_command_outside_any_repository(
    run_wtenv: Run, contract: ModuleType, tmp_path: Path
) -> None:
    process = fails(
        run_wtenv,
        contract,
        ["up", "--json"],
        tmp_path,
        model="UpResult",
        command="up",
        code=ErrorCode.NOT_IN_WORKTREE,
    )

    assert json.loads(process.stdout)["error"]["details"] == {"cwd": str(tmp_path)}


def test_16_registry_unreadable_is_a_registry_that_is_not_json(
    run_wtenv: Run, contract: ModuleType, tmp_path: Path
) -> None:
    ensure_state_dir()
    registry_path().write_text("this is not json", encoding="utf-8")

    process = fails(
        run_wtenv,
        contract,
        ["ls", "--json"],
        tmp_path,
        model="LsResult",
        command="ls",
        code=ErrorCode.REGISTRY_UNREADABLE,
    )

    details = json.loads(process.stdout)["error"]["details"]
    assert details == {"path": str(registry_path()), "reason": "invalid_json"}


# --- T133: the codes of `up` ---------------------------------------------------------------------


def up_fails(
    run_wtenv: Run, contract: ModuleType, worktree: Path, code: ErrorCode
) -> "subprocess.CompletedProcess[str]":
    return fails(
        run_wtenv,
        contract,
        ["up", "--json"],
        worktree,
        model="UpResult",
        command="up",
        code=code,
    )


def test_3_config_invalid_is_a_setting_wtenv_does_not_know(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    write_config(worktree, "ports = 5\n")

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.CONFIG_INVALID)

    assert json.loads(process.stdout)["error"]["details"]["setting"] == "ports"


def test_6_no_free_block_is_a_range_with_every_block_taken(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    write_config(worktree, "block_size = 1000\n")
    ensure_state_dir()
    taken = {}
    for number in range(10):  # ten blocks of 1000 fill 20000-29999
        git_dir = f"/nowhere/repo-{number}.git/worktrees/w{number}"
        taken[git_dir] = {
            "git_dir": git_dir,
            "path": f"/nowhere/w{number}",
            "repository": f"/nowhere/repo-{number}.git",
            "state": "provisioned",
            "block": {"start": 20000 + number * 1000, "size": 1000},
        }
    registry_path().write_text(json.dumps({"version": 1, "worktrees": taken}), encoding="utf-8")

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.NO_FREE_BLOCK)

    details = json.loads(process.stdout)["error"]["details"]
    assert details == {"block_size": 1000, "range": "20000-29999"}


def test_7_env_file_unusable_is_an_env_file_that_is_a_directory(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    (worktree / ".env.local").mkdir()

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.ENV_FILE_UNUSABLE)

    assert json.loads(process.stdout)["error"]["details"]["reason"] == "is_directory"


def test_8_dependency_unavailable_is_a_postgres_server_on_a_closed_port(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    url = f"postgresql://dev@127.0.0.1:{closed_port()}/{{name}}"
    write_config(worktree, f'[database]\ntype = "postgres"\ntemplate = "t"\nurl = "{url}"\n')

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.DEPENDENCY_UNAVAILABLE)

    details = json.loads(process.stdout)["error"]["details"]
    assert details["dependency"] == "postgres" and details["reason"] == "cannot_connect"


SQLITE = '[database]\ntype = "sqlite"\ntemplate = "db/dev.sqlite3"\nurl = "sqlite:///{path}"\n'


def test_9_template_missing_is_a_sqlite_template_that_does_not_exist(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    write_config(worktree, SQLITE)

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.TEMPLATE_MISSING)

    assert json.loads(process.stdout)["error"]["details"]["kind"] == "sqlite"


def test_10_template_in_use_is_a_sqlite_template_with_an_unfinished_wal_file(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    write_config(worktree, SQLITE)
    (worktree / "db").mkdir()
    (worktree / "db" / "dev.sqlite3").write_bytes(b"")
    (worktree / "db" / "dev.sqlite3-wal").write_bytes(b"unfinished work")

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.TEMPLATE_IN_USE)

    assert json.loads(process.stdout)["error"]["details"]["kind"] == "sqlite"


def test_11_ownership_conflict_is_a_file_wtenv_did_not_create_where_it_would(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    write_config(worktree, SQLITE)
    (worktree / "db").mkdir()
    (worktree / "db" / "dev.sqlite3").write_bytes(b"")
    (worktree / ".wtenv").mkdir()
    (worktree / ".wtenv" / "dev.sqlite3").write_bytes(b"not wtenv's")

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.OWNERSHIP_CONFLICT)

    assert json.loads(process.stdout)["error"]["details"]["kind"] == "sqlite_file"
    assert (worktree / ".wtenv" / "dev.sqlite3").read_bytes() == b"not wtenv's"


def test_12_post_up_failed_is_a_command_that_exits_non_zero(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    write_config(worktree, 'post_up = ["exit 3"]\n')

    process = up_fails(run_wtenv, contract, worktree, ErrorCode.POST_UP_FAILED)

    details = json.loads(process.stdout)["error"]["details"]
    assert details == {"command": "exit 3", "exit_status": 3}


# --- T134: the codes of the other commands -------------------------------------------------------


def test_13_partial_failure_is_a_down_that_cannot_remove_an_env_section(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    assert run_wtenv(["up"], worktree).returncode == 0
    env_file = worktree / ".env.local"
    text = env_file.read_text(encoding="utf-8")
    assert END_MARKER in text
    env_file.write_text(text.replace(f"{END_MARKER}\n", ""), encoding="utf-8")  # damaged markers

    process = fails(
        run_wtenv,
        contract,
        ["down", "--json"],
        worktree,
        model="DownResult",
        command="down",
        code=ErrorCode.PARTIAL_FAILURE,
    )

    failed = json.loads(process.stdout)["failed"]
    assert [item["kind"] for item in failed] == ["env_section"]


def test_17_problems_found_is_a_doctor_that_finds_an_orphaned_worktree(
    run_wtenv: Run, contract: ModuleType, repo: Path, worktree: Path
) -> None:
    assert run_wtenv(["up"], worktree).returncode == 0
    git(repo, "worktree", "remove", "--force", str(worktree))

    process = fails(
        run_wtenv,
        contract,
        ["doctor", "--json"],
        repo,
        model="DoctorResult",
        command="doctor",
        code=ErrorCode.PROBLEMS_FOUND,
    )

    assert json.loads(process.stdout)["error"]["details"] == {"problems": 1}


def test_18_worktree_exists_is_a_release_of_a_worktree_that_is_still_there(
    run_wtenv: Run, contract: ModuleType, repo: Path, worktree: Path
) -> None:
    assert run_wtenv(["up"], worktree).returncode == 0

    process = fails(
        run_wtenv,
        contract,
        ["gc", "--release", str(worktree), "--json"],
        repo,
        model="GcResult",
        command="gc",
        code=ErrorCode.WORKTREE_EXISTS,
    )

    assert json.loads(process.stdout)["error"]["details"] == {"path": str(worktree)}


def test_19_unsupported_is_a_hook_install_with_core_hooks_path_elsewhere(
    run_wtenv: Run, contract: ModuleType, repo: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "shared-hooks"
    elsewhere.mkdir()
    git(repo, "config", "core.hooksPath", str(elsewhere))

    process = fails(
        run_wtenv,
        contract,
        ["hook", "install", "--json"],
        repo,
        model="HookInstallResult",
        command="hook install",
        code=ErrorCode.UNSUPPORTED,
    )

    assert json.loads(process.stdout)["error"]["details"]["reason"] == "hooks_path_redirected"


def test_5_not_provisioned_inside_exec_is_reported_with_status_125(
    run_wtenv: Run, contract: ModuleType, worktree: Path
) -> None:
    process = fails(
        run_wtenv,
        contract,
        ["exec", "--json", "--", "true"],
        worktree,
        model="ExecResult",
        command="exec",
        code=ErrorCode.NOT_PROVISIONED,
        status=125,
    )

    details = json.loads(process.stdout)["error"]["details"]
    assert details["status"] == "unprovisioned"


def test_14_registry_busy_is_a_registry_lock_that_stays_held(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    contract: ModuleType,
) -> None:
    # The same command, in this process, with a wait of a fraction of a second instead of ten.
    monkeypatch.setattr(listing, "registry_lock", lambda: locks.registry_lock(0.2))

    with locks.registry_lock():
        status = cli.main(["ls", "--json"])

    captured = capsys.readouterr()
    assert status == 14
    parsed = contract.LsResult.model_validate_json(captured.out)
    assert parsed.ok is False and parsed.error.code.value == "registry_busy"
    assert parsed.error.exit_status == 14 and parsed.error.details == {"waited_seconds": 0.2}
    assert "wtenv: error [registry_busy]" in captured.err


def test_15_worktree_busy_is_a_worktree_lock_that_stays_held(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    contract: ModuleType,
    worktree: Path,
) -> None:
    git_dir = current_worktree(worktree).git_dir
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(
        provision, "worktree_lock", lambda git_dir, timeout=0.0: locks.worktree_lock(git_dir, 0.2)
    )

    with locks.worktree_lock(git_dir):
        status = cli.main(["up", "--json"])

    captured = capsys.readouterr()
    assert status == 15
    parsed = contract.UpResult.model_validate_json(captured.out)
    assert parsed.ok is False and parsed.error.code.value == "worktree_busy"
    assert parsed.error.exit_status == 15 and parsed.error.details == {"waited_seconds": 0.2}
    assert "wtenv: error [worktree_busy]" in captured.err
