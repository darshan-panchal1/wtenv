"""Fixtures for the contract tests."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

CONTRACTS = (
    Path(__file__).resolve().parents[2] / "specs" / "001-worktree-runtime-isolation" / "contracts"
)


@pytest.fixture(scope="session")
def contract() -> ModuleType:
    """Load `contracts/json_models.py` from its path, as the module it is."""
    name = "wtenv_contract_json_models"
    spec = importlib.util.spec_from_file_location(name, CONTRACTS / "json_models.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # pydantic resolves the contract's string annotations through `sys.modules`, so the module
    # has to be registered before it runs.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
