"""`wtenv.output` defines the models of `contracts/json_models.py`, unchanged (FR-058)."""

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
