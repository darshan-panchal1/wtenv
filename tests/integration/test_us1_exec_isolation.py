"""User story 1, `wtenv exec` and post-up commands in a worktree whose repository owns its override
(FR-004, FR-033; cli.md, `wtenv exec`; T051, T131).

`wtenv exec` adds `COMPOSE_FILE` and `COMPOSE_PATH_SEPARATOR` to the command's environment in the
exec-only arrangement, after seven checks that fail closed: the command is then not run, and the
exit status is 125. A sentinel command (`touch <file>`) proves that nothing ran.

Compose runs for real in some tests; the Compose version that ran is `docker compose version
--short`. Every test uses throwaway repositories under `tmp_path`, a temporary `XDG_STATE_HOME`
(the autouse fixture of `tests/conftest.py`), and projects that wtenv generated for its own
worktrees. The repository's override is compared by SHA-256 before and after.
"""

import hashlib
import json
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    COMPOSE_IMAGE,
    ComposeProjects,
    commit_all,
    compose_version,
    parse_up,
)

from wtenv import isolation
from wtenv.envfile import read_section
from wtenv.identity import current_worktree
from wtenv.output import UpResult
from wtenv.registry import ComposeRecord, WorktreeEntry, load, registry_path, transaction

pytestmark = pytest.mark.integration

Run = Callable[..., "subprocess.CompletedProcess[str]"]
AddWorktree = Callable[[Path, str, str], Path]

ISOLATION_FILE = isolation.ISOLATION_FILE
REPOSITORY_OVERRIDE = "compose.override.yml"

STACK = f"""\
services:
  backend:
    image: {COMPOSE_IMAGE}
    pull_policy: never
"""
OVERRIDE = """\
services:
  backend:
    ports:
      - "8000:6379"
    command: ["valkey-server", "--save", "", "--appendonly", "no"]
"""
TOML = 'ports = ["PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repo(make_repo: Callable[[str], Path]) -> Path:
    return make_repo("app")


def make_worktree(
    repo: Path,
    add_worktree: AddWorktree,
    name: str,
    *,
    override: str | None = OVERRIDE,
    toml: str = TOML,
    stack: str = STACK,
) -> Path:
    """Add worktree `name` with a committed compose file, `wtenv.toml` and (maybe) an override."""
    worktree = add_worktree(repo, name, f"branch-{name}")
    (worktree / "compose.yaml").write_text(stack, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(toml, encoding="utf-8")
    if override is not None:
        (worktree / REPOSITORY_OVERRIDE).write_text(override, encoding="utf-8")
    commit_all(worktree)
    return worktree


def up(run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None) -> UpResult:
    """Run `wtenv up --json`; the run must succeed."""
    process = run_wtenv(["up", "--json"], worktree, env)
    assert process.returncode == 0, process.stdout + process.stderr
    return parse_up(process)


def provisioned(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    projects: ComposeProjects,
    name: str,
    **kwargs: Any,
) -> Path:
    """An exec-only worktree (unless `override=None` is passed), brought up and tracked."""
    worktree = make_worktree(repo, add_worktree, name, **kwargs)
    up(run_wtenv, worktree)
    record = entry_of(worktree).compose
    if record is not None:  # a worktree without `[compose]` has no project to clean up
        projects.track(record.project)
    return worktree


def entry_of(worktree: Path) -> WorktreeEntry:
    return load().worktrees[current_worktree(worktree).git_dir]


def record_of(worktree: Path) -> ComposeRecord:
    record = entry_of(worktree).compose
    assert record is not None
    return record


def edit_record(worktree: Path, **updates: Any) -> None:
    """Edit the registry as a person could: replace fields of the entry's compose record."""
    with transaction() as registry:
        entry = registry.worktrees[current_worktree(worktree).git_dir]
        assert entry.compose is not None
        entry.compose = entry.compose.model_copy(update=updates)


def exec_in(
    run_wtenv: Run,
    worktree: Path,
    command: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> "subprocess.CompletedProcess[str]":
    """Run `wtenv exec --json -- <command>` in `cwd` (default: the worktree root)."""
    return run_wtenv(["exec", "--json", "--", *command], cwd or worktree, env)


def refusal(process: "subprocess.CompletedProcess[str]") -> dict[str, Any]:
    """Return `error.details` of an `exec` that wtenv refused: 125 and `isolation_unavailable`."""
    assert process.returncode == 125, process.stdout + process.stderr
    error = json.loads(process.stdout)["error"]
    assert error["code"] == "isolation_unavailable", error
    assert error["exit_status"] == 125
    details = error["details"]
    assert isinstance(details, dict)
    return details


def printed_environment(process: "subprocess.CompletedProcess[str]") -> dict[str, str]:
    """Parse the output of `env` into a dictionary."""
    assert process.returncode == 0, process.stdout + process.stderr
    pairs = (line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)
    return {key: value for key, value in pairs}


# --- the seven checks fail closed (FR-033; data-model.md) ----------------------------------------

CASES = [
    "COMPOSE_PROJECT_NAME in the environment",
    "COMPOSE_FILE in the environment",
    "COMPOSE_PROJECT_NAME in .env",
    "COMPOSE_FILE in .env",
    "override is not the isolation path",
    "repository override is a path with ..",
    "file is an absolute path",
    "isolation file is a link",
    "isolation file is missing",
    "isolation file is not wtenv's",
    "repository override is missing",
    "another override name appeared",
]


def break_isolation(
    case: str, worktree: Path, decoy: Path
) -> tuple[dict[str, str], dict[str, str]]:
    """Make `case` true after `up`; return the extra environment and the expected details."""
    root = worktree
    if case == "COMPOSE_PROJECT_NAME in the environment":
        return {"COMPOSE_PROJECT_NAME": "elsewhere"}, {
            "reason": "compose_env_override",
            "variable": "COMPOSE_PROJECT_NAME",
        }
    if case == "COMPOSE_FILE in the environment":
        return {"COMPOSE_FILE": "elsewhere.yaml"}, {
            "reason": "compose_env_override",
            "variable": "COMPOSE_FILE",
        }
    if case.endswith("in .env"):
        variable = case.split()[0]
        (root / ".env").write_text(f"{variable}=elsewhere\n", encoding="utf-8")
        return {}, {
            "reason": "compose_env_override",
            "variable": variable,
            "path": str(root / ".env"),
        }
    if case == "override is not the isolation path":
        edit_record(worktree, override="compose.override.yaml")
        return {}, {"reason": "recorded_value_invalid", "path": str(root / "compose.override.yaml")}
    if case == "repository override is a path with ..":
        edit_record(worktree, repository_override="../outside.yml")
        return {}, {"reason": "recorded_value_invalid", "path": str(root / "../outside.yml")}
    if case == "file is an absolute path":
        edit_record(worktree, file="/etc/compose.yaml")
        return {}, {"reason": "recorded_value_invalid", "path": "/etc/compose.yaml"}
    isolation_file = root / ISOLATION_FILE
    if case == "isolation file is a link":
        target = decoy / "wtenv-header.yaml"
        target.write_text(isolation.OVERRIDE_HEADER + 'name: "elsewhere"\n', encoding="utf-8")
        isolation_file.unlink()
        isolation_file.symlink_to(target)
        return {}, {"reason": "symlink", "path": str(isolation_file)}
    if case == "isolation file is missing":
        isolation_file.unlink()
        return {}, {"reason": "isolation_file_missing", "path": str(isolation_file)}
    if case == "isolation file is not wtenv's":
        isolation_file.write_text("services: {}\n", encoding="utf-8")
        return {}, {"reason": "isolation_file_not_wtenvs", "path": str(isolation_file)}
    if case == "repository override is missing":
        (root / REPOSITORY_OVERRIDE).unlink()
        return {}, {
            "reason": "repository_override_missing",
            "path": str(root / REPOSITORY_OVERRIDE),
        }
    assert case == "another override name appeared"
    (root / "docker-compose.override.yml").write_text("services: {}\n", encoding="utf-8")
    return {}, {"reason": "override_appeared", "path": str(root / "docker-compose.override.yml")}


@pytest.mark.parametrize("case", CASES)
def test_exec_does_not_run_the_command_when_isolation_cannot_apply(
    case: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    decoy: Path,
    tmp_path: Path,
) -> None:
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "refused")
    repository_override = worktree / REPOSITORY_OVERRIDE
    override_before = sha256(repository_override) if repository_override.exists() else None
    env, expected = break_isolation(case, worktree, decoy)
    sentinel = tmp_path / "sentinel"
    registry_before = registry_path().read_bytes()
    decoy_before = {p: p.read_bytes() for p in decoy.rglob("*") if p.is_file()}

    process = exec_in(run_wtenv, worktree, ["touch", str(sentinel)], env)

    assert refusal(process) == expected
    assert not sentinel.exists()  # the command was never run
    assert registry_path().read_bytes() == registry_before
    assert {p: p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == decoy_before
    if "path" in expected:
        assert os.path.isabs(expected["path"])
    if override_before is not None and repository_override.exists():
        assert sha256(repository_override) == override_before


def test_when_two_conditions_hold_exec_reports_the_first_in_table_order(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "ordered")
    (worktree / ISOLATION_FILE).unlink()  # check 4
    (worktree / REPOSITORY_OVERRIDE).unlink()  # check 6
    sentinel = tmp_path / "sentinel"

    first = refusal(exec_in(run_wtenv, worktree, ["touch", str(sentinel)]))
    second = refusal(
        exec_in(run_wtenv, worktree, ["touch", str(sentinel)], {"COMPOSE_PROJECT_NAME": "x"})
    )

    assert first["reason"] == "isolation_file_missing"
    assert second["reason"] == "compose_env_override"
    assert not sentinel.exists()


# --- what the command gets -----------------------------------------------------------------------


def test_exec_sets_compose_file_and_the_separator_as_the_isolation_builder_says(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "env")
    expected = isolation.compose_environment(worktree, record_of(worktree))

    child = printed_environment(exec_in(run_wtenv, worktree, ["env"]))

    assert child.get("COMPOSE_FILE") == expected["COMPOSE_FILE"]
    assert child.get("COMPOSE_PATH_SEPARATOR") == ":"
    assert (child.get("COMPOSE_FILE") or "").split(":") == [
        str(worktree / "compose.yaml"),
        str(worktree / REPOSITORY_OVERRIDE),
        str(worktree / ISOLATION_FILE),
    ]
    section = dict(read_section(worktree / ".env.local"))
    assert all(child.get(name) == value for name, value in section.items())


def test_exec_never_sets_compose_project_name(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "noname")

    child = printed_environment(exec_in(run_wtenv, worktree, ["env"]))

    assert "COMPOSE_PROJECT_NAME" not in child


def test_exec_in_an_automatic_worktree_sets_exactly_the_section(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """FR-008, 001:FR-030: without a repository override the environment is v0.1.1's."""
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "auto", override=None)
    section = dict(read_section(worktree / ".env.local"))

    child = printed_environment(exec_in(run_wtenv, worktree, ["env"]))

    added = set(child) - set(os.environ)
    assert added == (set(section) | {"NO_COLOR", "COLUMNS"}) - set(os.environ)
    assert all(child[name] == value for name, value in section.items())
    assert not [name for name in added if name.startswith("COMPOSE_")]


def test_the_environment_reaches_a_child_shell_and_works_from_a_subdirectory(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """Research R2, E2.3 and E2.4: absolute paths survive `sh -c` and a change of directory."""
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "child")
    (worktree / "sub").mkdir()
    expected = isolation.compose_environment(worktree, record_of(worktree))["COMPOSE_FILE"]

    shell = exec_in(run_wtenv, worktree, ["sh", "-c", "sh -c 'printf %s \"$COMPOSE_FILE\"'"])
    config = exec_in(
        run_wtenv,
        worktree,
        ["docker", "compose", "--profile", "*", "config", "--format", "json"],
        cwd=worktree / "sub",
    )

    assert (shell.returncode, shell.stdout) == (0, expected)
    assert config.returncode == 0, config.stderr
    document = json.loads(config.stdout)
    assert document["name"] == record_of(worktree).project
    (published,) = entry_of(worktree).published
    assert [int(p["published"]) for p in document["services"]["backend"]["ports"]] == [
        published.port
    ]


def test_a_childs_own_f_makes_compose_ignore_compose_file(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
) -> None:
    """Research R2, E2.7, against the Docker Compose installed here (`docker compose version`).

    A Compose command that passes its own `-f` is not isolated: it ignores `COMPOSE_FILE`, so the
    repository's fixed port and Compose's own project name come back. The README says so.
    """
    version = compose_version()
    assert version is not None and version >= (2, 24, 4)
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "ownf")

    process = exec_in(
        run_wtenv,
        worktree,
        ["docker", "compose", "-f", "compose.yaml", "-f", REPOSITORY_OVERRIDE]
        + ["--profile", "*", "config", "--format", "json"],
    )

    assert process.returncode == 0, process.stderr
    document = json.loads(process.stdout)
    assert [int(p["published"]) for p in document["services"]["backend"]["ports"]] == [8000]
    assert document["name"] != record_of(worktree).project


# --- post-up commands (001:FR-036; FR-033; User Story 1 scenario 10) -----------------------------


def post_up_toml(command: str) -> str:
    """`wtenv.toml` with one post-up command; a top-level key, so it goes before `[compose]`."""
    return f"post_up = [{json.dumps(command)}]\n" + TOML


def test_post_up_commands_get_the_same_compose_environment_as_exec(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    printed = tmp_path / "post-up.txt"
    command = (
        'printf "%s\\n%s\\n%s\\n" "$COMPOSE_FILE" "$COMPOSE_PATH_SEPARATOR" "$COMPOSE_ENV_FILES" '
        f"> {shlex.quote(str(printed))}"
    )
    worktree = provisioned(
        repo, add_worktree, run_wtenv, compose_projects, "postup", toml=post_up_toml(command)
    )
    child = printed_environment(exec_in(run_wtenv, worktree, ["env"]))

    assert printed.read_text(encoding="utf-8").splitlines() == [
        child.get("COMPOSE_FILE"),
        child.get("COMPOSE_PATH_SEPARATOR"),
        child.get("COMPOSE_ENV_FILES"),
    ]
    assert child.get("COMPOSE_PATH_SEPARATOR") == ":"
    assert child.get("COMPOSE_ENV_FILES") == "/dev/null"  # no `.env` beside the compose file


def test_post_up_commands_get_the_isolation_environment_on_a_repeat_up(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    """Safety finding M6: the early-return branch of `up` runs the commands too."""
    printed = tmp_path / "post-up-env.txt"
    worktree = provisioned(
        repo,
        add_worktree,
        run_wtenv,
        compose_projects,
        "repeat",
        toml=post_up_toml(f"env > {shlex.quote(str(printed))}"),
    )
    first = printed_environment_of_file(printed)
    printed.unlink()
    override_before = sha256(worktree / REPOSITORY_OVERRIDE)

    second_up = up(run_wtenv, worktree)

    assert all(change.action == "unchanged" for change in second_up.changes)  # nothing changed
    second = printed_environment_of_file(printed)
    expected = isolation.compose_environment(worktree, record_of(worktree))
    assert second.get("COMPOSE_FILE") == expected["COMPOSE_FILE"]
    assert second.get("COMPOSE_PATH_SEPARATOR") == ":"
    assert second.get("COMPOSE_ENV_FILES") == expected["COMPOSE_ENV_FILES"]
    wanted = {
        name for name, _ in read_section(worktree / ".env.local") if name.startswith("WTENV_PORT_")
    }
    assert wanted and wanted <= set(second)
    assert {name: second[name] for name in wanted} == {name: first[name] for name in wanted}
    assert sha256(worktree / REPOSITORY_OVERRIDE) == override_before


def printed_environment_of_file(path: Path) -> dict[str, str]:
    pairs = (
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    return {key: value for key, value in pairs}


# --- check A: another worktree's isolation file in an inherited COMPOSE_FILE (M1; T114) ----------

NO_COMPOSE_TOML = 'ports = ["PORT"]\n'


def three_worktrees(
    repo: Path, add_worktree: AddWorktree, run_wtenv: Run, projects: ComposeProjects
) -> tuple[Path, Path, Path]:
    """A exec-only, B automatic, C without `[compose]`, all provisioned."""
    first = provisioned(repo, add_worktree, run_wtenv, projects, "alpha")
    second = provisioned(repo, add_worktree, run_wtenv, projects, "bravo", override=None)
    third = provisioned(
        repo, add_worktree, run_wtenv, projects, "charlie", override=None, toml=NO_COMPOSE_TOML
    )
    return first, second, third


def compose_file_left_by_exec(run_wtenv: Run, worktree: Path) -> str:
    """The `COMPOSE_FILE` that a shell started by `wtenv exec -- sh` leaves in `worktree`."""
    process = exec_in(run_wtenv, worktree, ["sh", "-c", 'printf %s "$COMPOSE_FILE"'])
    assert process.returncode == 0, process.stdout + process.stderr
    return process.stdout


@pytest.mark.parametrize("separator", [":", ";"])
@pytest.mark.parametrize("victim", ["automatic", "without compose"])
def test_exec_refuses_another_worktrees_isolation_file_in_compose_file(
    victim: str,
    separator: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    first, second, third = three_worktrees(repo, add_worktree, run_wtenv, compose_projects)
    worktree = second if victim == "automatic" else third
    value = compose_file_left_by_exec(run_wtenv, first).replace(":", separator)
    env = {"COMPOSE_FILE": value}
    if separator != ":":
        env["COMPOSE_PATH_SEPARATOR"] = separator
    sentinel = tmp_path / "sentinel"

    details = refusal(exec_in(run_wtenv, worktree, ["touch", str(sentinel)], env))

    assert details == {
        "reason": "compose_env_override",
        "variable": "COMPOSE_FILE",
        "path": str(first / ISOLATION_FILE),
    }
    assert not sentinel.exists()


@pytest.mark.parametrize("victim", ["automatic", "without compose"])
def test_exec_passes_any_other_compose_file_through_as_v0_1_1_does(
    victim: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    _, second, third = three_worktrees(repo, add_worktree, run_wtenv, compose_projects)
    worktree = second if victim == "automatic" else third
    values = [
        str(worktree / "compose.yaml"),  # its own compose file
        str(tmp_path / "x" / "compose.isolation.yaml"),  # not in a `.wtenv/` directory
        str(worktree / ISOLATION_FILE),  # its own isolation file name
    ]
    for value in values:
        process = exec_in(
            run_wtenv, worktree, ["sh", "-c", 'printf %s "$COMPOSE_FILE"'], {"COMPOSE_FILE": value}
        )

        assert (process.returncode, process.stdout) == (0, value)  # passed through unchanged


# --- check A before post-up commands (M1, decided; 001:FR-036, FR-037; T148) ---------------------


def failed_up(
    run_wtenv: Run, worktree: Path, env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    """Run `wtenv up --json` and return its exit status and `error` object."""
    process = run_wtenv(["up", "--json"], worktree, env)
    assert len(process.stdout.splitlines()) == 1, process.stdout
    error = json.loads(process.stdout)["error"]
    assert isinstance(error, dict)
    return process.returncode, error


def post_up_only_toml(command: str) -> str:
    """`wtenv.toml` without `[compose]`, with one post-up command."""
    return f"post_up = [{json.dumps(command)}]\n" + NO_COMPOSE_TOML


@pytest.mark.parametrize("separator", [":", ";"])
@pytest.mark.parametrize("call_site", ["first up", "repeat up"])
def test_up_runs_no_post_up_command_when_compose_file_names_another_worktrees_isolation_file(
    call_site: str,
    separator: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    first = provisioned(repo, add_worktree, run_wtenv, compose_projects, "alpha")
    value = compose_file_left_by_exec(run_wtenv, first).replace(":", separator)
    env = {"COMPOSE_FILE": value}
    if separator != ":":
        env["COMPOSE_PATH_SEPARATOR"] = separator
    sentinel = tmp_path / "sentinel"
    toml = post_up_only_toml(f"touch {shlex.quote(str(sentinel))}")
    third = make_worktree(repo, add_worktree, "charlie", override=None, toml=toml)
    if call_site == "repeat up":
        up(run_wtenv, third)  # a normal first `up`: the command runs once
        assert sentinel.exists()
        sentinel.unlink()
        assert entry_of(third).state == "provisioned"

    status, error = failed_up(run_wtenv, third, env)

    assert (status, error["code"], error["exit_status"]) == (20, "isolation_unavailable", 20)
    assert error["details"] == {
        "reason": "compose_env_override",
        "variable": "COMPOSE_FILE",
        "path": str(first / ISOLATION_FILE),
    }
    assert not sentinel.exists()  # no post-up command ran
    entry = entry_of(third)
    assert entry.state == "incomplete"  # as after a failing post-up command (001:FR-037)
    assert entry.block.size == 10  # its port block and env file are left in place
    assert dict(read_section(third / ".env.local"))["PORT"] == str(entry.block.start)


def test_up_passes_any_other_compose_file_to_the_post_up_commands_as_v0_1_1_does(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    tmp_path: Path,
) -> None:
    printed = tmp_path / "printed"
    toml = post_up_only_toml(f'printf %s "$COMPOSE_FILE" > {shlex.quote(str(printed))}')
    third = make_worktree(repo, add_worktree, "charlie", override=None, toml=toml)
    values = [
        str(third / "compose.yaml"),
        str(tmp_path / "x" / "compose.isolation.yaml"),
        str(third / ISOLATION_FILE),
    ]
    for value in values:
        up(run_wtenv, third, {"COMPOSE_FILE": value})

        assert printed.read_text(encoding="utf-8") == value
        printed.unlink()


def test_in_the_automatic_arrangement_the_same_compose_file_is_still_unsupported_for_up(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    first = provisioned(repo, add_worktree, run_wtenv, compose_projects, "alpha")
    value = compose_file_left_by_exec(run_wtenv, first)
    sentinel = tmp_path / "sentinel"
    toml = f"post_up = [{json.dumps('touch ' + shlex.quote(str(sentinel)))}]\n" + TOML
    second = make_worktree(repo, add_worktree, "bravo", override=None, toml=toml)

    status, error = failed_up(run_wtenv, second, {"COMPOSE_FILE": value})

    assert (status, error["code"]) == (19, "unsupported")  # v0.1.1's refusal, unchanged
    assert error["details"]["reason"] == "compose_env_override"
    assert not sentinel.exists()


# --- the `.env` line forms and COMPOSE_ENV_FILES (safety finding M2; research R7; T117) ----------

DOT_ENV_FORMS = {
    "colon": "{k}: elsewhere\n",
    "export, colon": "export {k}: elsewhere\n",
    "spaces around a colon": "   {k} : elsewhere\n",
    "CRLF line end": "{k}=elsewhere\r\n",
    "byte-order mark": "\ufeff{k}=elsewhere\n",
}


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
@pytest.mark.parametrize("form", list(DOT_ENV_FORMS))
def test_exec_refuses_every_dot_env_form_that_compose_honours(
    form: str,
    variable: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "forms")
    (worktree / ".env").write_bytes(DOT_ENV_FORMS[form].format(k=variable).encode("utf-8"))
    sentinel = tmp_path / "sentinel"

    details = refusal(exec_in(run_wtenv, worktree, ["touch", str(sentinel)]))

    assert details == {
        "reason": "compose_env_override",
        "variable": variable,
        "path": str(worktree / ".env"),
    }
    assert not sentinel.exists()


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
@pytest.mark.parametrize("form", list(DOT_ENV_FORMS))
def test_up_refuses_every_dot_env_form_that_compose_honours_and_changes_nothing(
    form: str,
    variable: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_docker: None,
) -> None:
    worktree = make_worktree(repo, add_worktree, "upforms")
    (worktree / ".env").write_bytes(DOT_ENV_FORMS[form].format(k=variable).encode("utf-8"))
    override_before = sha256(worktree / REPOSITORY_OVERRIDE)

    status, error = failed_up(run_wtenv, worktree)

    assert (status, error["code"]) == (19, "unsupported")
    assert error["details"]["reason"] == "compose_env_override"
    assert error["details"]["variable"] == variable
    assert not registry_path().exists()
    assert not (worktree / ".wtenv").exists()
    assert sha256(worktree / REPOSITORY_OVERRIDE) == override_before


def test_exec_refuses_an_inherited_compose_env_files(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "envfiles")
    sentinel = tmp_path / "sentinel"

    details = refusal(
        exec_in(run_wtenv, worktree, ["touch", str(sentinel)], {"COMPOSE_ENV_FILES": "/x/.env"})
    )

    assert details == {"reason": "compose_env_override", "variable": "COMPOSE_ENV_FILES"}
    assert not sentinel.exists()


LABELLED_STACK = f"""\
services:
  backend:
    image: {COMPOSE_IMAGE}
    pull_policy: never
    labels:
      wtenv.test.value: "${{WTENV_TEST_VALUE:-unset}}"
"""


def test_a_subdirectorys_dot_env_cannot_change_the_project_through_exec(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    """Research R7, E6.3b and E6.3c, against the Docker Compose installed here.

    `exec` sets `COMPOSE_ENV_FILES` to the project's `.env`, so Compose reads no other `.env`:
    the `sub/.env` that sets a project name is ignored, and the project's own `.env` is
    interpolated. Without it, Compose merges (5.1.4) or reads only (2.24.4) the current
    directory's `.env`.
    """
    worktree = make_worktree(repo, add_worktree, "subenv", stack=LABELLED_STACK)
    (worktree / ".env").write_text("WTENV_TEST_VALUE=from-the-project-env\n", encoding="utf-8")
    up(run_wtenv, worktree)
    compose_projects.track(record_of(worktree).project)
    sub = worktree / "sub"
    sub.mkdir()
    (sub / ".env").write_text("COMPOSE_PROJECT_NAME=wtenv-test-elsewhere\n", encoding="utf-8")

    process = exec_in(
        run_wtenv,
        worktree,
        ["sh", "-c", "cd sub && docker compose --profile '*' config --format json"],
    )

    assert process.returncode == 0, process.stderr
    document = json.loads(process.stdout)
    assert document["name"] == record_of(worktree).project
    labels = document["services"]["backend"]["labels"]
    assert labels["wtenv.test.value"] == "from-the-project-env"


# --- check B: an override that appeared in the automatic arrangement (M5; research R7, E6.2; T128) -

AUTOMATIC_CASES = [
    "tracked compose.override.yml added",
    "untracked docker-compose.override.yaml added",
    "compose.override.yaml replaced by a file without the header",
    "compose.override.yaml replaced by a link",
]


@pytest.mark.parametrize("case", AUTOMATIC_CASES)
def test_exec_refuses_an_override_that_appeared_in_an_automatic_worktree(
    case: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    decoy: Path,
    tmp_path: Path,
) -> None:
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "auto", override=None)
    recorded = worktree / "compose.override.yaml"
    if case.startswith("tracked"):
        (worktree / "compose.override.yml").write_text("services: {}\n", encoding="utf-8")
        commit_all(worktree)
        expected = {"reason": "override_appeared", "path": str(worktree / "compose.override.yml")}
    elif case.startswith("untracked"):
        (worktree / "docker-compose.override.yaml").write_text("services: {}\n", encoding="utf-8")
        expected = {
            "reason": "override_appeared",
            "path": str(worktree / "docker-compose.override.yaml"),
        }
    elif "without the header" in case:
        recorded.write_text("services: {}\n", encoding="utf-8")
        expected = {"reason": "override_appeared", "path": str(recorded)}
    else:
        target = decoy / "wtenv-header.yaml"
        target.write_text(isolation.OVERRIDE_HEADER + 'name: "elsewhere"\n', encoding="utf-8")
        recorded.unlink()
        recorded.symlink_to(target)
        expected = {"reason": "symlink", "path": str(recorded)}
    sentinel = tmp_path / "sentinel"
    decoy_before = {p: p.read_bytes() for p in decoy.rglob("*") if p.is_file()}

    details = refusal(exec_in(run_wtenv, worktree, ["touch", str(sentinel)]))

    assert details == expected
    assert not sentinel.exists()
    assert {p: p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == decoy_before


@pytest.mark.parametrize(
    "case", ["nothing changed", "recorded override deleted", "without compose"]
)
def test_exec_runs_the_command_as_in_v0_1_1_when_no_override_appeared(
    case: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    toml = NO_COMPOSE_TOML if case == "without compose" else TOML
    worktree = provisioned(
        repo, add_worktree, run_wtenv, compose_projects, "quiet", override=None, toml=toml
    )
    if case == "recorded override deleted":
        (worktree / "compose.override.yaml").unlink()
    sentinel = tmp_path / "sentinel"

    process = exec_in(run_wtenv, worktree, ["touch", str(sentinel)])

    assert process.returncode == 0, process.stdout + process.stderr
    assert sentinel.exists()


def test_exec_ignores_the_developers_own_overrides_once_compose_was_removed(
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    """Once `[compose]` is removed the record is `removing`, and wtenv's file is gone: check B
    would only refuse the developer's own override files, which are theirs."""
    worktree = provisioned(
        repo, add_worktree, run_wtenv, compose_projects, "dropped", override=None
    )
    (worktree / "wtenv.toml").write_text(NO_COMPOSE_TOML, encoding="utf-8")
    commit_all(worktree)
    up(run_wtenv, worktree)
    (worktree / "compose.override.yml").write_text("services: {}\n", encoding="utf-8")
    sentinel = tmp_path / "sentinel"

    process = exec_in(run_wtenv, worktree, ["touch", str(sentinel)])

    assert process.returncode == 0, process.stdout + process.stderr
    assert sentinel.exists()


# --- check 2: the repository override is an override name beside the compose file (L4; T140) ------

NAME_REFUSALS = {
    "a name that is no override name, in a directory": ("compose.yaml", "deploy/compose.prod.yml"),
    "a name that is no override name": ("compose.yaml", "compose.prod.yml"),
    "an override at the root, the compose file in deploy": (
        "deploy/compose.yaml",
        "compose.override.yml",
    ),
    "an override in deploy, the compose file at the root": (
        "compose.yaml",
        "deploy/compose.override.yml",
    ),
}
NAME_ACCEPTED = {
    "an override name beside a root compose file": ("compose.yaml", "compose.override.yml"),
    "another override name beside a compose file in deploy": (
        "deploy/compose.yaml",
        "deploy/docker-compose.override.yaml",
    ),
}


def hand_edit_files(worktree: Path, compose_file: str, repository_override: str) -> None:
    """Edit the registry and create the files it now names, so that only check 2 can object."""
    for relative in (compose_file, repository_override):
        path = worktree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("services: {}\n", encoding="utf-8")
    edit_record(worktree, file=compose_file, repository_override=repository_override)


@pytest.mark.parametrize("case", list(NAME_REFUSALS))
def test_exec_refuses_a_repository_override_that_is_not_an_override_name_beside_the_compose_file(
    case: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    compose_file, repository_override = NAME_REFUSALS[case]
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "names")
    hand_edit_files(worktree, compose_file, repository_override)
    sentinel = tmp_path / "sentinel"

    details = refusal(exec_in(run_wtenv, worktree, ["touch", str(sentinel)]))

    assert details == {
        "reason": "recorded_value_invalid",
        "path": str(worktree / repository_override),
    }
    assert not sentinel.exists()


@pytest.mark.parametrize("case", list(NAME_ACCEPTED))
def test_exec_accepts_an_override_name_beside_the_compose_file(
    case: str,
    repo: Path,
    add_worktree: AddWorktree,
    run_wtenv: Run,
    compose_projects: ComposeProjects,
    tmp_path: Path,
) -> None:
    compose_file, repository_override = NAME_ACCEPTED[case]
    worktree = provisioned(repo, add_worktree, run_wtenv, compose_projects, "accepted")
    hand_edit_files(worktree, compose_file, repository_override)
    sentinel = tmp_path / "sentinel"

    process = exec_in(run_wtenv, worktree, ["touch", str(sentinel)])

    assert process.returncode == 0, process.stdout + process.stderr
    assert sentinel.exists()
