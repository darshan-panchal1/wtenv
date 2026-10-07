"""Helpers shared by the contract tests that run wtenv on real git worktrees."""

import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

# The line that closes wtenv's section of an env file (files.md, Env file section).
END_MARKER = "# <<< wtenv managed <<<"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def document(
    contract: ModuleType,
    process: "subprocess.CompletedProcess[str]",
    model: str,
    command: str | None,
) -> Any:
    """Check the one-document rule for `process` and return the document as the contract's model.

    `model` is the name of the model in `json_models.py`, and `command` the value its `command`
    field must have.
    """
    assert process.stdout.endswith("\n") and process.stdout.count("\n") == 1, process.stdout
    raw = json.loads(process.stdout)  # exactly one JSON document, and nothing else
    assert raw["schema_version"] == 1
    parsed = getattr(contract, model).model_validate(raw)
    assert parsed.command == command
    assert parsed.ok is (process.returncode == 0), (process.returncode, raw)
    if parsed.ok:
        assert parsed.error is None
        assert "wtenv: error" not in process.stderr
    else:
        assert parsed.error is not None
        assert parsed.error.exit_status == process.returncode
        assert f"wtenv: error [{parsed.error.code.value}]" in process.stderr
    return parsed
