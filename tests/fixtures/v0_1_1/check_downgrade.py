"""Check what v0.1.1 does with a registry that the new code saved (T108; FR-029).

Two modes, in two codebases:

    # with the new code: save the two registries, each under its own XDG_STATE_HOME
    XDG_STATE_HOME=$SCRATCH/down-auto uv run python -I tests/fixtures/v0_1_1/check_downgrade.py make
    XDG_STATE_HOME=$SCRATCH/down-exec uv run python -I tests/fixtures/v0_1_1/check_downgrade.py make --exec

    # with v0.1.1's code, in a detached `v0.1.1` worktree: load each one
    XDG_STATE_HOME=$SCRATCH/down-auto uv run --project "$SCRATCH/v0.1.1" python -I tests/fixtures/v0_1_1/check_downgrade.py check

`make` writes the entries of the golden registry (all in the automatic arrangement), and with
`--exec` one more compose entry that records `repository_override`. `check` calls v0.1.1's
`registry.load` and prints `loaded`, or the error code and its `details.reason`, then the
SHA-256 of `registry.json` before and after, so a changed file shows.
"""

import hashlib
import sys
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden" / "registry.json"


def make(with_exec_entry: bool) -> None:
    """Save the golden registry, and optionally an exec-only compose entry, with `registry.save`."""
    from wtenv import registry

    saved = registry.Registry.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
    if with_exec_entry:
        template = next(entry for entry in saved.worktrees.values() if entry.compose is not None)
        git_dir = f"{template.repository}/worktrees/exec-only"
        assert template.compose is not None
        entry = template.model_copy(
            update={
                "git_dir": git_dir,
                "path": "/golden/worktrees/exec-only",
                "compose": template.compose.model_copy(
                    update={
                        "override": ".wtenv/compose.isolation.yaml",
                        "repository_override": "compose.override.yaml",
                    }
                ),
            }
        )
        saved.worktrees[git_dir] = entry
    registry.save(saved)


def check() -> None:
    """Load the registry with v0.1.1's `registry.load` and print what happened."""
    from wtenv import registry
    from wtenv.errors import WtenvError

    path = registry.registry_path()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        registry.load()
        print("loaded")
    except WtenvError as error:
        print(f"{error.code.value} (reason: {error.details.get('reason')})")
    print(f"sha256 before: {before}")
    print(f"sha256 after:  {hashlib.sha256(path.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["make"]:
        make("--exec" in sys.argv[2:])
    elif sys.argv[1:2] == ["check"]:
        check()
    else:
        sys.exit("usage: check_downgrade.py make [--exec] | check")
