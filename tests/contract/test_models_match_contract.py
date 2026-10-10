"""`wtenv.output` defines the models of spec 002's `contracts/json_models.py` (FR-058)."""

import enum
import inspect
from types import ModuleType

import pytest
from pydantic import BaseModel, ValidationError

from wtenv import errors, output


def _classes_defined_in(contract: ModuleType, base: type) -> dict[str, type]:
    """Return the subclasses of `base` that the contract module itself defines."""
    return {
        name: cls
        for name, cls in inspect.getmembers(contract, inspect.isclass)
        if issubclass(cls, base) and cls.__module__ == contract.__name__
    }


def test_the_contract_defines_models_and_enums(contract: ModuleType) -> None:
    # Guards the tests below against passing because they found nothing to compare.
    assert len(_classes_defined_in(contract, BaseModel)) >= 24
    assert len(_classes_defined_in(contract, enum.Enum)) >= 7


def test_every_model_exists_with_an_equal_json_schema(contract: ModuleType) -> None:
    problems = []
    for name, expected in _classes_defined_in(contract, BaseModel).items():
        actual = getattr(output, name, None)
        if not (inspect.isclass(actual) and issubclass(actual, BaseModel)):
            problems.append(f"{name}: missing from wtenv.output")
        elif actual.model_json_schema() != expected.model_json_schema():  # type: ignore[attr-defined]
            problems.append(f"{name}: JSON schema differs")

    assert problems == []


def test_every_enum_has_the_same_members_and_values(contract: ModuleType) -> None:
    problems = []
    for name, expected in _classes_defined_in(contract, enum.Enum).items():
        actual = getattr(output, name, None)
        if not (inspect.isclass(actual) and issubclass(actual, enum.Enum)):
            problems.append(f"{name}: missing from wtenv.output")
            continue
        expected_members = {member.name: member.value for member in expected}  # type: ignore[attr-defined]
        actual_members = {member.name: member.value for member in actual}
        if actual_members != expected_members:
            problems.append(f"{name}: members differ")

    assert problems == []


def test_exit_statuses_match_the_contract(contract: ModuleType) -> None:
    expected = {code.value: status for code, status in contract.EXIT_STATUS.items()}
    actual = {code.value: status for code, status in errors.EXIT_STATUS.items()}

    assert actual == expected
    assert errors.EXIT_SUCCESS == contract.EXIT_SUCCESS
    assert errors.EXEC_WTENV_FAILED == contract.EXEC_WTENV_FAILED
    assert errors.EXEC_COMMAND_NOT_EXECUTABLE == contract.EXEC_COMMAND_NOT_EXECUTABLE
    assert errors.EXEC_COMMAND_NOT_FOUND == contract.EXEC_COMMAND_NOT_FOUND


def test_the_schema_version_is_one(contract: ModuleType) -> None:
    assert output.SCHEMA_VERSION == 1
    assert output.SCHEMA_VERSION == contract.SCHEMA_VERSION


def test_error_codes_are_imported_from_the_errors_module_not_defined_twice() -> None:
    assert output.ErrorCode is errors.ErrorCode
    assert output.EXIT_STATUS is errors.EXIT_STATUS


def test_kept_volume_exists_with_the_contracts_schema(contract: ModuleType) -> None:
    assert issubclass(output.KeptVolume, BaseModel)
    assert output.KeptVolume.model_json_schema() == contract.KeptVolume.model_json_schema()
    kept = output.KeptVolume(name="data", project="wtenv-x-1", reason="unlabelled")
    assert kept.model_dump() == {"name": "data", "project": "wtenv-x-1", "reason": "unlabelled"}


def test_down_and_gc_results_have_empty_kept_volumes_by_default(contract: ModuleType) -> None:
    for name in ("DownResult", "GcResult"):
        model = getattr(output, name)
        assert "kept_volumes" in model.model_fields, name
        assert model(ok=True).kept_volumes == [], name
        assert model.model_json_schema() == getattr(contract, name).model_json_schema(), name


def test_kept_volumes_take_only_the_reason_unlabelled() -> None:
    with pytest.raises(ValidationError):
        output.KeptVolume(name="data", project="p", reason="in-use")  # type: ignore[arg-type]


def test_kept_volumes_take_the_reasons_unlabelled_and_fixed_name_and_nothing_else() -> None:
    # Reading R8 (T174).
    for reason in ("unlabelled", "fixed_name"):
        assert output.KeptVolume(name="v", project="p", reason=reason).reason == reason  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        output.KeptVolume(name="v", project="p", reason="other")  # type: ignore[arg-type]


def test_the_fixed_volume_name_warning_code_exists() -> None:
    # Reading R8 (T174).
    assert output.WarningCode.COMPOSE_FIXED_VOLUME_NAME.value == "compose_fixed_volume_name"


def test_the_parent_missing_reason_exists() -> None:
    # Reading R9 (T174).
    assert output.UnverifiableReason.PARENT_MISSING.value == "parent_missing"


def test_the_models_that_carry_the_new_members_match_the_contracts_schemas(
    contract: ModuleType,
) -> None:
    # Reading R8, reading R9, FR-058 (T174).
    for name in (
        "KeptVolume",
        "WarningInfo",
        "KeptEntry",
        "WorktreeView",
        "DownResult",
        "GcResult",
    ):
        actual = getattr(output, name).model_json_schema()
        assert actual == getattr(contract, name).model_json_schema(), name


# --- spec 002 additions (T003; FR-031, FR-032) -------------------------------------------------
#
# The contract tests above compare every model and enum with 002's `json_models.py`. These name
# the members that spec 002 adds, so a missing one is reported by name, not as a schema diff.


def test_the_four_error_codes_of_spec_002_exist_with_their_values() -> None:
    expected = {
        "ISOLATION_UNAVAILABLE": "isolation_unavailable",
        "OVERRIDE_AMBIGUOUS": "override_ambiguous",
        "PORT_VARIABLE_CONFLICT": "port_variable_conflict",
        "DATABASE_STORAGE_SHARED": "database_storage_shared",
    }

    actual = {member.name: member.value for member in errors.ErrorCode if member.name in expected}

    assert actual == expected


def test_the_warning_codes_of_spec_002_exist_with_their_values() -> None:
    expected = {
        "COMPOSE_ISOLATION_EXEC_ONLY": "compose_isolation_exec_only",
        "COMPOSE_DATABASE_DATA_KEPT": "compose_database_data_kept",
        "COMPOSE_OVERRIDE_ADOPTED": "compose_override_adopted",
        "COMPOSE_OVERRIDE_EXCLUDED": "compose_override_excluded",
    }

    actual = {member.name: member.value for member in output.WarningCode if member.name in expected}

    assert actual == expected


def _property(model: type[BaseModel], name: str) -> object:
    """Return the JSON-schema property `name` of `model`, or "missing"."""
    return model.model_json_schema()["properties"].get(name, "missing")


def test_a_port_view_has_a_port_variable_that_defaults_to_none(contract: ModuleType) -> None:
    assert _property(output.PortView, "port_variable") == _property(
        contract.PortView, "port_variable"
    )
    assert output.PortView(port=20001, port_variable="WTENV_PORT_DB_5432_TCP").port_variable == (
        "WTENV_PORT_DB_5432_TCP"
    )
    assert output.PortView(port=20001).port_variable is None


def test_a_worktree_view_has_the_compose_isolation_fields(contract: ModuleType) -> None:
    for name in ("compose_isolation", "compose_repository_override"):
        assert _property(output.WorktreeView, name) == _property(contract.WorktreeView, name), name
    isolation = _property(output.WorktreeView, "compose_isolation")
    assert isinstance(isolation, dict)
    assert {"automatic", "exec_only"} <= {
        value for branch in isolation["anyOf"] for value in branch.get("enum", [])
    }


def test_a_kept_item_is_an_item_with_the_reason_repository_owned(contract: ModuleType) -> None:
    kept_item = getattr(output, "KeptItem", None)
    assert kept_item is not None, "KeptItem is missing from wtenv.output"
    assert issubclass(kept_item, output.Item)
    assert kept_item.model_json_schema() == contract.KeptItem.model_json_schema()
    kept = kept_item(
        kind=output.ItemKind.COMPOSE_OVERRIDE,
        name="/code/x/compose.override.yaml",
        reason="repository_owned",
    )
    assert kept.reason == "repository_owned"
    with pytest.raises(ValidationError):
        kept_item(kind=output.ItemKind.COMPOSE_OVERRIDE, name="x", reason="other")


def test_a_compose_database_view_shows_names_and_ports_only(contract: ModuleType) -> None:
    view_class = getattr(output, "ComposeDatabaseView", None)
    assert view_class is not None, "ComposeDatabaseView is missing from wtenv.output"
    assert view_class.model_json_schema() == contract.ComposeDatabaseView.model_json_schema()
    view = view_class(
        service="db",
        target=5432,
        protocol="tcp",
        port=20003,
        port_variable="WTENV_PORT_DB_5432_TCP",
    )
    assert view.model_dump() == {
        "service": "db",
        "target": 5432,
        "protocol": "tcp",
        "port": 20003,
        "port_variable": "WTENV_PORT_DB_5432_TCP",
        "url_variable": "DATABASE_URL",
    }


def test_the_up_result_has_a_compose_database_that_defaults_to_none(contract: ModuleType) -> None:
    assert _property(output.UpResult, "compose_database") == _property(
        contract.UpResult, "compose_database"
    )
    assert getattr(output.UpResult(ok=True), "compose_database", "missing") is None


def test_down_and_gc_results_have_kept_items_that_default_to_an_empty_list(
    contract: ModuleType,
) -> None:
    for name in ("DownResult", "GcResult"):
        model = getattr(output, name)
        assert _property(model, "kept_items") == _property(getattr(contract, name), "kept_items")
        assert getattr(model(ok=True), "kept_items", "missing") == [], name
