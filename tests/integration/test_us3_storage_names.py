"""Volume names are judged under the wtenv project (T112; FR-027, SC-003, SC-004; safety H1).

The compose file names its own project `app`, and the database volume is given the name
`app_pgdata`: a name that starts with the repository's project name, and that the main checkout
and every worktree would share. The wtenv project is the generated `wtenv-feature-x-<hash>`.
Only `docker compose config` runs: nothing is created, started or pulled.
"""

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import commit_all

from wtenv import compose, provision
from wtenv.errors import ErrorCode, WtenvError

pytestmark = pytest.mark.integration

MakeRepo = Callable[[str], Path]
AddWorktree = Callable[[Path, str, str], Path]

PROJECT = "wtenv-feature-x-3f9a1c2b"


def stack(*, name: str | None, volume: str) -> str:
    header = f"name: {name}\n" if name else ""
    return (
        f"{header}services:\n  db:\n    image: postgres:17\n    pull_policy: never\n"
        '    ports:\n      - "5432"\n    volumes:\n      - pgdata:/var/lib/postgresql/data\n'
        f"volumes:\n  pgdata:{volume}\n"
    )


def judge(tmp_path: Path, text: str, project: str | None = PROJECT) -> None:
    directory = tmp_path / "app"  # the directory name is the default project name of Compose
    directory.mkdir()
    (directory / "compose.yaml").write_text(text, encoding="utf-8")
    model = compose.resolve_model(directory / "compose.yaml", [], project=project)
    compose.database_storage(model, "db", root=directory, project=PROJECT)


@pytest.mark.parametrize("named", [True, False], ids=["compose name: app", "directory named app"])
def test_a_volume_named_like_the_repositorys_own_project_is_shared(
    named: bool, compose_docker: None, tmp_path: Path
) -> None:
    text = stack(name="app" if named else None, volume="\n    name: app_pgdata")

    with pytest.raises(WtenvError) as caught:
        judge(tmp_path, text)

    assert caught.value.code is ErrorCode.DATABASE_STORAGE_SHARED
    assert caught.value.details["storage"] == "fixed_name_volume"
    assert caught.value.details["name"] == "app_pgdata"


@pytest.mark.parametrize("named", [True, False], ids=["compose name: app", "directory named app"])
def test_a_volume_without_a_name_is_project_scoped_and_accepted_silently(
    named: bool, compose_docker: None, tmp_path: Path
) -> None:
    judge(tmp_path, stack(name="app" if named else None, volume=" {}"))  # no exception, no warning


# --- step 5 resolves under the wtenv project only for a compose-hosted database (T113) ---------

DB_STACK = """\
services:
  db:
    image: postgres:17
    pull_policy: never
    ports:
      - "5432"
"""
COMPOSE_DATABASE = (
    'ports = ["PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n\n[database]\n'
    'type = "compose"\nservice = "db"\nurl = "postgresql://postgres:pw@localhost:{port}/app"\n'
)
NO_DATABASE = 'ports = ["PORT"]\nblock_size = 10\n\n[compose]\nfile = "compose.yaml"\n'


def projects_resolved_under(
    toml: str, make_repo: MakeRepo, add_worktree: AddWorktree, monkeypatch: pytest.MonkeyPatch
) -> list[str | None]:
    """Run `provision.up` in a new worktree; return the `project` of each model resolution."""
    repo = make_repo("app")
    worktree = add_worktree(repo, "feature-x", "feature-x")
    (worktree / "compose.yaml").write_text(DB_STACK, encoding="utf-8")
    (worktree / "wtenv.toml").write_text(toml, encoding="utf-8")
    commit_all(worktree)
    seen: list[str | None] = []
    resolve: Callable[..., Any] = compose.resolve_model

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("project"))
        return resolve(*args, **kwargs)

    monkeypatch.setattr(compose, "resolve_model", spy)
    with contextlib.suppress(WtenvError):
        provision.up(worktree)
    return seen


def test_a_compose_hosted_database_resolves_under_the_generated_wtenv_project(
    compose_docker: None,
    make_repo: MakeRepo,
    add_worktree: AddWorktree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project,) = projects_resolved_under(COMPOSE_DATABASE, make_repo, add_worktree, monkeypatch)

    assert project is not None
    assert project.startswith("wtenv-feature-x-") and len(project.rsplit("-", 1)[1]) == 8


def test_without_a_compose_hosted_database_the_command_is_v0_1_1s(
    compose_docker: None,
    make_repo: MakeRepo,
    add_worktree: AddWorktree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert projects_resolved_under(NO_DATABASE, make_repo, add_worktree, monkeypatch) == [None]
