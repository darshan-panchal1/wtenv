"""The environment that makes Compose load the isolation file (data-model.md, "Isolation
environment"; FR-003, FR-033). The `exec` checks are added in T052."""

import os
from pathlib import Path

import pytest

from wtenv import isolation
from wtenv.errors import ErrorCode, WtenvError
from wtenv.output import ResourceState
from wtenv.registry import ComposeRecord

ROOT = Path("/code/app-feature")
ALLOWED_KEYS = {"COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR", "COMPOSE_ENV_FILES"}


def record(
    file: str = "compose.yaml", repository_override: str | None = "compose.override.yml"
) -> ComposeRecord:
    """A compose record in the exec-only arrangement."""
    return ComposeRecord(
        project="wtenv-app-feature-3f9a1c2b",
        file=file,
        override=isolation.ISOLATION_FILE,
        override_state=ResourceState.CREATED,
        repository_override=repository_override,
    )


def test_compose_file_names_the_three_files_in_order_as_absolute_paths() -> None:
    environment = isolation.compose_environment(ROOT, record())

    assert environment["COMPOSE_FILE"] == (
        "/code/app-feature/compose.yaml"
        ":/code/app-feature/compose.override.yml"
        ":/code/app-feature/.wtenv/compose.isolation.yaml"
    )


def test_the_path_separator_is_a_colon() -> None:
    assert isolation.compose_environment(ROOT, record())["COMPOSE_PATH_SEPARATOR"] == ":"


def test_a_nested_compose_file_and_override_keep_their_directory() -> None:
    environment = isolation.compose_environment(
        ROOT, record("deploy/compose.yaml", "deploy/docker-compose.override.yml")
    )

    assert environment["COMPOSE_FILE"] == (
        "/code/app-feature/deploy/compose.yaml"
        ":/code/app-feature/deploy/docker-compose.override.yml"
        ":/code/app-feature/.wtenv/compose.isolation.yaml"
    )


def test_every_key_is_one_the_isolation_environment_may_hold() -> None:
    # T052/T120 add COMPOSE_ENV_FILES, so the key set is not asserted to be the first two.
    assert set(isolation.compose_environment(ROOT, record())) <= ALLOWED_KEYS


def test_the_project_name_is_never_set() -> None:
    assert "COMPOSE_PROJECT_NAME" not in isolation.compose_environment(ROOT, record())


def test_a_record_without_a_repository_override_has_no_isolation_environment() -> None:
    with pytest.raises(ValueError, match="repository"):
        isolation.compose_environment(ROOT, record(repository_override=None))


def test_the_isolation_file_is_the_one_override_problem_accepts() -> None:
    assert isolation.ISOLATION_FILE == ".wtenv/compose.isolation.yaml"


# --- the exec checks (FR-033; data-model.md, "`exec` checks (FR-033), in order") ------------------

GOOD_HEADER = isolation.OVERRIDE_HEADER


def exec_only_root(tmp_path: Path) -> tuple[Path, ComposeRecord]:
    """A worktree root with a compose file, a repository override and a good isolation file."""
    root = tmp_path / "wt"
    (root / ".wtenv").mkdir(parents=True)
    (root / "compose.yaml").write_text("services: {}\n")
    (root / "compose.override.yml").write_text("services: {}\n")
    (root / isolation.ISOLATION_FILE).write_text(GOOD_HEADER + 'name: "x"\n')
    return root, record()


def refusal(root: Path, rec: ComposeRecord, environ: dict[str, str] | None = None) -> WtenvError:
    """Run the checks and return the error they raise."""
    with pytest.raises(WtenvError) as caught:
        isolation.check_isolation(root, rec, environ or {})
    error = caught.value
    assert error.code is ErrorCode.ISOLATION_UNAVAILABLE
    return error


def test_a_good_exec_only_worktree_passes_every_check(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)

    isolation.check_isolation(root, rec, {"HOME": "/h", "COMPOSE_PROJECT_NAME": ""})


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
def test_check_1_a_compose_variable_in_the_calling_environment_is_refused(
    tmp_path: Path, variable: str
) -> None:
    root, rec = exec_only_root(tmp_path)

    error = refusal(root, rec, {variable: "elsewhere"})

    assert error.details["reason"] == "compose_env_override"
    assert error.details["variable"] == variable


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
def test_check_1_a_compose_variable_in_dot_env_beside_the_compose_file_is_refused(
    tmp_path: Path, variable: str
) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / ".env").write_text(f"OTHER=1\n{variable}=elsewhere\n")

    error = refusal(root, rec)

    assert error.details["reason"] == "compose_env_override"
    assert error.details["variable"] == variable
    assert error.details["path"] == str(root / ".env")


def test_check_1_reads_dot_env_beside_a_nested_compose_file_only(tmp_path: Path) -> None:
    root, _ = exec_only_root(tmp_path)
    (root / "deploy").mkdir()
    (root / "deploy" / "compose.yaml").write_text("services: {}\n")
    (root / "deploy" / "compose.override.yml").write_text("services: {}\n")
    nested = record("deploy/compose.yaml", "deploy/compose.override.yml")
    (root / ".env").write_text("COMPOSE_PROJECT_NAME=elsewhere\n")  # not beside the compose file

    isolation.check_isolation(root, nested, {})

    (root / "deploy" / ".env").write_text("COMPOSE_PROJECT_NAME=elsewhere\n")
    assert refusal(root, nested).details["path"] == str(root / "deploy" / ".env")


def test_check_1_ignores_an_empty_value_and_a_comment_in_dot_env(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / ".env").write_text("COMPOSE_PROJECT_NAME=\n# COMPOSE_FILE=x\nMY_COMPOSE_FILE=x\n")

    isolation.check_isolation(root, rec, {})


def test_check_2_an_override_that_is_not_the_isolation_path_is_refused(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)

    error = refusal(root, rec.model_copy(update={"override": "compose.override.yaml"}))

    assert error.details["reason"] == "recorded_value_invalid"
    assert os.path.isabs(str(error.details["path"]))


@pytest.mark.parametrize(
    "update",
    [
        {"repository_override": "/etc/override.yml"},
        {"repository_override": "../override.yml"},
        {"file": "/etc/compose.yaml"},
        {"file": "a/../compose.yaml"},
        {"repository_override": ""},
    ],
    ids=["absolute override", "dotdot override", "absolute file", "dotdot file", "empty override"],
)
def test_check_2_a_recorded_path_of_a_form_wtenv_never_records_is_refused(
    tmp_path: Path, update: dict[str, str]
) -> None:
    root, rec = exec_only_root(tmp_path)

    error = refusal(root, rec.model_copy(update=update))

    assert error.details["reason"] == "recorded_value_invalid"
    assert os.path.isabs(str(error.details["path"]))


def test_check_3_a_symbolic_link_from_the_root_down_to_the_isolation_file_is_refused(
    tmp_path: Path,
) -> None:
    root, rec = exec_only_root(tmp_path)
    target = tmp_path / "elsewhere.yaml"
    target.write_text(GOOD_HEADER)  # a header read through the link would pass
    (root / isolation.ISOLATION_FILE).unlink()
    (root / isolation.ISOLATION_FILE).symlink_to(target)

    error = refusal(root, rec)

    assert error.details["reason"] == "symlink"
    assert error.details["path"] == str(root / isolation.ISOLATION_FILE)


def test_check_3_a_symbolic_link_at_wtenv_is_refused(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)
    moved = tmp_path / "moved"
    (root / ".wtenv").rename(moved)
    (root / ".wtenv").symlink_to(moved)

    error = refusal(root, rec)

    assert error.details["reason"] == "symlink"
    assert error.details["path"] == str(root / ".wtenv")


def test_check_4_a_missing_isolation_file_is_refused(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / isolation.ISOLATION_FILE).unlink()

    error = refusal(root, rec)

    assert error.details["reason"] == "isolation_file_missing"
    assert error.details["path"] == str(root / isolation.ISOLATION_FILE)


@pytest.mark.parametrize(
    "text", ["services: {}\n", "", GOOD_HEADER.replace("Generated", "Written")]
)
def test_check_5_an_isolation_file_without_the_header_is_refused(tmp_path: Path, text: str) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / isolation.ISOLATION_FILE).write_text(text)

    error = refusal(root, rec)

    assert error.details["reason"] == "isolation_file_not_wtenvs"
    assert error.details["path"] == str(root / isolation.ISOLATION_FILE)


def test_check_6_a_missing_repository_override_is_refused(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / "compose.override.yml").unlink()

    error = refusal(root, rec)

    assert error.details["reason"] == "repository_override_missing"
    assert error.details["path"] == str(root / "compose.override.yml")


def test_check_6_a_dangling_link_counts_as_present(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / "compose.override.yml").unlink()
    (root / "compose.override.yml").symlink_to(tmp_path / "nowhere.yml")

    isolation.check_isolation(root, rec, {})  # Compose, not wtenv, reports an unreadable file


@pytest.mark.parametrize(
    "name", ["compose.override.yaml", "docker-compose.override.yaml", "docker-compose.override.yml"]
)
def test_check_7_another_override_name_beside_the_compose_file_is_refused(
    tmp_path: Path, name: str
) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / name).write_text("services: {}\n")

    error = refusal(root, rec)

    assert error.details["reason"] == "override_appeared"
    assert error.details["path"] == str(root / name)


def test_when_two_conditions_hold_the_first_in_table_order_is_reported(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / isolation.ISOLATION_FILE).unlink()  # check 4
    (root / "compose.override.yml").unlink()  # check 6
    (root / "docker-compose.override.yml").write_text("x: 1\n")  # check 7

    assert refusal(root, rec).details["reason"] == "isolation_file_missing"
    assert refusal(root, rec, {"COMPOSE_FILE": "x"}).details["reason"] == "compose_env_override"
    assert (
        refusal(root, rec.model_copy(update={"override": "x.yaml"}), {}).details["reason"]
        == "recorded_value_invalid"
    )


# --- check A: an inherited COMPOSE_FILE that names another worktree's isolation file (M1) --------

OTHER = "/code/other-feature/.wtenv/compose.isolation.yaml"


def own_isolation(root: Path) -> str:
    return str(root / isolation.ISOLATION_FILE)


def test_check_a_another_worktrees_isolation_file_in_compose_file_is_refused(
    tmp_path: Path,
) -> None:
    value = f"/code/other-feature/compose.yaml:/code/other-feature/compose.override.yml:{OTHER}"

    with pytest.raises(WtenvError) as caught:
        isolation.check_inherited_compose_file(tmp_path, {"COMPOSE_FILE": value})

    error = caught.value
    assert error.code is ErrorCode.ISOLATION_UNAVAILABLE
    assert error.details == {
        "reason": "compose_env_override",
        "variable": "COMPOSE_FILE",
        "path": OTHER,
    }


def test_check_a_splits_on_the_path_separator_in_the_environment(tmp_path: Path) -> None:
    environ = {"COMPOSE_FILE": f"/a/compose.yaml;{OTHER}", "COMPOSE_PATH_SEPARATOR": ";"}

    with pytest.raises(WtenvError) as caught:
        isolation.check_inherited_compose_file(tmp_path, environ)

    assert caught.value.details["path"] == OTHER


def test_check_a_applies_the_rule_to_the_entries_as_split(tmp_path: Path) -> None:
    """A `;` list under the default `:` separator is one entry. It still ends in the isolation
    path and is not this worktree's own file, so it is refused; Compose would fail on it anyway."""
    value = f"/a/compose.yaml;{OTHER}"

    with pytest.raises(WtenvError) as caught:
        isolation.check_inherited_compose_file(tmp_path, {"COMPOSE_FILE": value})

    assert caught.value.details["path"] == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/code/wt/compose.yaml",
        "/code/wt/compose.yaml:/code/wt/compose.override.yml",
        "/code/elsewhere/compose.isolation.yaml",  # not in a .wtenv directory
        "/code/elsewhere/.wtenv/other.yaml",
        ".wtenv/compose.isolation.yaml",  # a relative name with no directory part
    ],
)
def test_check_a_any_other_value_passes(tmp_path: Path, value: str) -> None:
    isolation.check_inherited_compose_file(tmp_path, {"COMPOSE_FILE": value})


def test_check_a_this_worktrees_own_isolation_file_passes(tmp_path: Path) -> None:
    isolation.check_inherited_compose_file(tmp_path, {"COMPOSE_FILE": own_isolation(tmp_path)})
    isolation.check_inherited_compose_file(
        tmp_path, {"COMPOSE_FILE": f"/x/compose.yaml:{own_isolation(tmp_path)}"}
    )


def test_check_a_without_compose_file_passes(tmp_path: Path) -> None:
    isolation.check_inherited_compose_file(tmp_path, {})
    isolation.check_inherited_compose_file(tmp_path, {"COMPOSE_PATH_SEPARATOR": ";"})


def test_check_a_a_relative_entry_is_reported_as_an_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(WtenvError) as caught:
        isolation.check_inherited_compose_file(
            tmp_path, {"COMPOSE_FILE": "../sibling/.wtenv/compose.isolation.yaml"}
        )

    assert os.path.isabs(str(caught.value.details["path"]))
    assert str(caught.value.details["path"]).endswith("/sibling/.wtenv/compose.isolation.yaml")


# --- the `.env` line forms and COMPOSE_ENV_FILES (safety finding M2; research R7, E6.1, E6.3) ----

# Every form of an assignment that Compose honours (E6.1), with its line end. `{k}` is the name.
DOT_ENV_FORMS = {
    "colon": "{k}: elsewhere\n",
    "colon, no space": "{k}:elsewhere\n",
    "export, colon": "export {k}: elsewhere\n",
    "spaces around a colon": "   {k} : elsewhere\n",
    "CRLF line end": "{k}=elsewhere\r\n",
    "byte-order mark": "\ufeff{k}=elsewhere\n",
}


@pytest.mark.parametrize("variable", ["COMPOSE_PROJECT_NAME", "COMPOSE_FILE"])
@pytest.mark.parametrize("form", list(DOT_ENV_FORMS))
def test_check_1_refuses_every_dot_env_form_that_compose_honours(
    tmp_path: Path, variable: str, form: str
) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / ".env").write_bytes(DOT_ENV_FORMS[form].format(k=variable).encode("utf-8"))

    error = refusal(root, rec)

    assert error.details == {
        "reason": "compose_env_override",
        "variable": variable,
        "path": str(root / ".env"),
    }


@pytest.mark.parametrize(
    "text",
    [
        "# COMPOSE_PROJECT_NAME: x\n",
        "COMPOSE_PROJECT_NAME=\n",
        "COMPOSE_PROJECT_NAME:\n",
        "COMPOSE_PROJECT_NAMES: x\n",
        "MY_COMPOSE_FILE: x\n",
    ],
    ids=["comment", "empty with =", "empty with :", "longer name", "other name"],
)
def test_check_1_still_accepts_a_comment_an_empty_value_and_other_names(
    tmp_path: Path, text: str
) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / ".env").write_text(text)

    isolation.check_isolation(root, rec, {})


def test_check_1_refuses_an_inherited_compose_env_files(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)

    error = refusal(root, rec, {"COMPOSE_ENV_FILES": "/elsewhere/.env"})

    assert error.details == {"reason": "compose_env_override", "variable": "COMPOSE_ENV_FILES"}


def test_an_empty_inherited_compose_env_files_is_not_set(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)

    isolation.check_isolation(root, rec, {"COMPOSE_ENV_FILES": ""})


def test_compose_env_files_is_the_dot_env_beside_the_compose_file_when_there_is_one(
    tmp_path: Path,
) -> None:
    root, rec = exec_only_root(tmp_path)
    (root / ".env").write_text("A=1\n")

    environment = isolation.compose_environment(root, rec)

    assert environment.get("COMPOSE_ENV_FILES") == str(root / ".env")


def test_compose_env_files_is_dev_null_without_a_dot_env(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)

    assert isolation.compose_environment(root, rec).get("COMPOSE_ENV_FILES") == "/dev/null"


def test_compose_env_files_names_the_dot_env_beside_a_nested_compose_file(tmp_path: Path) -> None:
    root, _ = exec_only_root(tmp_path)
    (root / "deploy").mkdir()
    (root / "deploy" / ".env").write_text("A=1\n")
    (root / ".env").write_text("B=2\n")  # not beside the compose file
    nested = record("deploy/compose.yaml", "deploy/compose.override.yml")

    assert isolation.compose_environment(root, nested).get("COMPOSE_ENV_FILES") == str(
        root / "deploy" / ".env"
    )


def test_the_exec_environment_has_exactly_the_three_compose_variables(tmp_path: Path) -> None:
    root, rec = exec_only_root(tmp_path)

    assert set(isolation.compose_environment(root, rec)) == ALLOWED_KEYS


def test_the_override_names_pair_with_the_compose_file_names() -> None:
    from wtenv.config import COMPOSE_FILE_NAMES

    assert isolation.OVERRIDE_FILE_NAMES == tuple(
        name.replace(".y", ".override.y") for name in COMPOSE_FILE_NAMES
    )


# --- check B: an override appeared in the automatic arrangement (M5) -----------------------------


def automatic_root(
    tmp_path: Path, state: ResourceState = ResourceState.CREATED
) -> tuple[Path, ComposeRecord]:
    """A worktree root with wtenv's own `compose.override.yaml`, recorded as the override."""
    root = tmp_path / "wt"
    root.mkdir()
    (root / "compose.yaml").write_text("services: {}\n")
    (root / "compose.override.yaml").write_text(GOOD_HEADER + 'name: "x"\n')
    rec = ComposeRecord(
        project="wtenv-app-3f9a1c2b",
        file="compose.yaml",
        override="compose.override.yaml",
        override_state=state,
    )
    return root, rec


def b_refusal(root: Path, rec: ComposeRecord) -> WtenvError:
    with pytest.raises(WtenvError) as caught:
        isolation.check_automatic_override(root, rec)
    assert caught.value.code is ErrorCode.ISOLATION_UNAVAILABLE
    return caught.value


def test_check_b_a_worktree_nothing_changed_in_passes(tmp_path: Path) -> None:
    root, rec = automatic_root(tmp_path)

    isolation.check_automatic_override(root, rec)


def test_check_b_a_deleted_recorded_override_passes_as_in_v0_1_1(tmp_path: Path) -> None:
    root, rec = automatic_root(tmp_path)
    (root / "compose.override.yaml").unlink()

    isolation.check_automatic_override(root, rec)


@pytest.mark.parametrize(
    "name", ["compose.override.yml", "docker-compose.override.yaml", "docker-compose.override.yml"]
)
def test_check_b_another_override_name_that_appeared_is_refused(tmp_path: Path, name: str) -> None:
    root, rec = automatic_root(tmp_path)
    (root / name).write_text("services: {}\n")

    error = b_refusal(root, rec)

    assert error.details == {"reason": "override_appeared", "path": str(root / name)}


def test_check_b_an_override_beside_another_compose_file_is_not_counted(tmp_path: Path) -> None:
    root, rec = automatic_root(tmp_path)
    (root / "deploy").mkdir()
    (root / "deploy" / "compose.override.yml").write_text("services: {}\n")

    isolation.check_automatic_override(root, rec)


@pytest.mark.parametrize(
    "text", ["services: {}\n", "", GOOD_HEADER.replace("Generated", "Written")]
)
def test_check_b_the_recorded_override_replaced_by_a_file_without_the_header_is_refused(
    tmp_path: Path, text: str
) -> None:
    root, rec = automatic_root(tmp_path)
    (root / "compose.override.yaml").write_text(text)

    error = b_refusal(root, rec)

    assert error.details == {
        "reason": "override_appeared",
        "path": str(root / "compose.override.yaml"),
    }


def test_check_b_the_recorded_override_replaced_by_a_link_is_refused_without_reading_it(
    tmp_path: Path,
) -> None:
    root, rec = automatic_root(tmp_path)
    target = tmp_path / "elsewhere.yaml"
    target.write_text(GOOD_HEADER)  # a header read through the link would pass
    (root / "compose.override.yaml").unlink()
    (root / "compose.override.yaml").symlink_to(target)

    error = b_refusal(root, rec)

    assert error.details == {"reason": "symlink", "path": str(root / "compose.override.yaml")}


def test_check_b_a_record_that_is_being_removed_is_not_checked(tmp_path: Path) -> None:
    """`[compose]` was removed: wtenv's file is gone, and the developer's own overrides are theirs."""
    root, rec = automatic_root(tmp_path, ResourceState.REMOVING)
    (root / "compose.override.yml").write_text("services: {}\n")

    isolation.check_automatic_override(root, rec)


@pytest.mark.parametrize(
    ("compose_file", "repository_override"),
    [
        ("compose.yaml", "deploy/compose.prod.yml"),
        ("compose.yaml", "compose.prod.yml"),
        ("deploy/compose.yaml", "compose.override.yml"),
        ("compose.yaml", "deploy/compose.override.yml"),
    ],
)
def test_check_2_a_repository_override_must_be_an_override_name_beside_the_compose_file(
    tmp_path: Path, compose_file: str, repository_override: str
) -> None:
    root, _ = exec_only_root(tmp_path)
    rec = record(compose_file, repository_override)
    for relative in (compose_file, repository_override):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).touch()

    error = refusal(root, rec)

    assert error.details == {
        "reason": "recorded_value_invalid",
        "path": str(root / repository_override),
    }


@pytest.mark.parametrize(
    ("compose_file", "repository_override"),
    [
        ("compose.yaml", "compose.override.yml"),
        ("deploy/compose.yaml", "deploy/docker-compose.override.yaml"),
    ],
)
def test_check_2_accepts_an_override_name_beside_the_compose_file(
    tmp_path: Path, compose_file: str, repository_override: str
) -> None:
    root, _ = exec_only_root(tmp_path)
    for relative in (compose_file, repository_override):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).touch()

    isolation.check_isolation(root, record(compose_file, repository_override), {})
