"""`wtenv.output` defines the models of `contracts/json_models.py`, unchanged (FR-058)."""

import enum
import inspect
from types import ModuleType

from pydantic import BaseModel

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
