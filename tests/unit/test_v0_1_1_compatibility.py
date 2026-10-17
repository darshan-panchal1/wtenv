"""Every v0.1.1 configuration still gives v0.1.1's results (T091; FR-008, FR-028, SC-006).

The golden files in `tests/fixtures/v0_1_1/golden/` were produced by v0.1.1's own code (T002).
This test calls the public functions both versions share on the same fixed inputs, and compares
what they return: the ports, the project name, the database name or path, the override bytes and
the env-file section. FR-028 allows exactly one difference, the `port_variable_conflict` refusal
of three setups, so the test fails if a configuration outside those three is refused, and if one
of the three is accepted.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from wtenv import compose, config, database, envfile, ports, provision
from wtenv.errors import ErrorCode, WtenvError
from wtenv.registry import PortBlock

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v0_1_1"
CONFIGS = FIXTURES / "configs"
GOLDEN = FIXTURES / "golden"

# The inputs of `make_golden.py`.
BLOCK_START = 20000
WORKTREE_NAME = "feature-x"
GIT_DIR = "/golden/repo/.git/worktrees/feature-x"

# The three setups FR-028 refuses; v0.1.1 accepted them.
EXCEPTIONS = ("dup_port", "colliding_names", "ports_named_like_a_variable")
NAMES = sorted(path.stem for path in CONFIGS.glob("*.toml"))
OTHERS = [name for name in NAMES if name not in EXCEPTIONS]


def results(name: str, tmp_path: Path) -> dict[str, Any]:
    """The new code's results for `name`, in the shape of the golden `<name>.json`."""
    shutil.copyfile(CONFIGS / f"{name}.toml", tmp_path / "wtenv.toml")
    loaded = config.load_config(tmp_path)
    block = PortBlock(start=BLOCK_START, size=loaded.block_size)
    assigned = ports.assign_variable_ports(loaded.ports, block)
    found: dict[str, Any] = {
        "config": loaded.model_dump(mode="json"),
        "ports": [entry.model_dump(mode="json") for entry in assigned],
        "published": [],
        "project": None,
        "warnings": [],
        "database": None,
        "env_section": None,
        "override": None,
        "model": None,
    }
    if loaded.compose is not None:
        document = json.loads((CONFIGS / f"{name}.compose.json").read_text(encoding="utf-8"))
        model = compose.parse_model(document)
        published = ports.assign_published_ports(model.mappings, loaded.ports, block)
        project = compose.project_name(WORKTREE_NAME, GIT_DIR)
        ordered = sorted(
            model.mappings, key=lambda m: (m.service, m.target, m.protocol, m.host_ip or "")
        )
        pairs = list(zip(ordered, (entry.port for entry in published), strict=True))
        found.update(
            published=[entry.model_dump(mode="json") for entry in published],
            project=project,
            warnings=[warning.model_dump(mode="json") for warning in model.warnings],
            override=compose.override_text(project, pairs),
            model=model,
        )
    if loaded.database is not None:
        if loaded.database.type == "postgres":
            found["database"] = {
                "kind": "postgres",
                "name": database.postgres_database_name(WORKTREE_NAME, GIT_DIR),
            }
        else:
            copy = database.sqlite_copy_path(
                f"/golden/worktrees/{WORKTREE_NAME}", loaded.database.template
            )
            found["database"] = {"kind": "sqlite", "path": str(copy)}
    # The section of the new code: the `ports` variables, then the port variables of the
    # published ports. The golden has the first only; the second are the additions of FR-014.
    from wtenv.registry import PublishedPort, VariablePort

    variables = provision._variables(
        [VariablePort(**item) for item in found["ports"]],
        [PublishedPort(**item) for item in found["published"]],
        None,
    )
    found["variables"] = variables
    env_path = tmp_path / ".env.local"
    envfile.write_section(env_path, variables)
    found["env_section"] = env_path.read_text(encoding="utf-8")
    return found


def golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def without_port_variable_lines(section: str) -> str:
    """The env section without the `WTENV_PORT_…` lines that spec 002 adds."""
    return "".join(
        line for line in section.splitlines(keepends=True) if not line.startswith("WTENV_PORT_")
    )


def test_the_fixtures_are_the_ones_t002_captured() -> None:
    assert len(EXCEPTIONS) == 3 and set(EXCEPTIONS) <= set(NAMES) and len(OTHERS) >= 8
    assert {path.stem for path in GOLDEN.glob("*.json")} == set(NAMES) | {"registry"}


@pytest.mark.parametrize("name", OTHERS)
def test_every_v0_1_1_config_fixture_is_accepted_and_provisions_identically(
    name: str, tmp_path: Path
) -> None:
    expected = golden(name)

    got = results(name, tmp_path)

    for key in ("config", "ports", "published", "project", "warnings", "database"):
        assert got[key] == expected[key], key
    override = GOLDEN / f"{name}.override.yaml"
    assert got["override"] == (override.read_text(encoding="utf-8") if override.exists() else None)
    # The env section is the golden, apart from the added `WTENV_PORT_…` lines.
    assert without_port_variable_lines(got["env_section"]) == expected["env_section"]
    names = [name for name, _ in got["variables"]]
    extra = [n for n in names if n.startswith("WTENV_PORT_")]
    assert len(extra) == len(got["published"])  # one added variable per published port


def check(got: dict[str, Any], tmp_path: Path) -> None:
    """Run the variable-name collision check the way `up` does (step 5)."""
    model = got["model"]
    has_database = got["config"]["database"] is not None
    ports.check_port_variables(
        model.mappings,
        ports.configured_variable_names(got["config"]["ports"], has_database=has_database),
    )


@pytest.mark.parametrize("name", EXCEPTIONS)
def test_the_three_setups_of_fr_028_are_refused_and_nothing_else_differs(
    name: str, tmp_path: Path
) -> None:
    got = results(name, tmp_path)

    with pytest.raises(WtenvError) as caught:
        check(got, tmp_path)

    assert caught.value.code is ErrorCode.PORT_VARIABLE_CONFLICT
    expected = golden(name)  # the only difference is the refusal: the rest equals the golden
    for key in ("config", "ports", "published", "project", "warnings"):
        assert got[key] == expected[key], key
    assert got["override"] == (GOLDEN / f"{name}.override.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", [n for n in OTHERS if (CONFIGS / f"{n}.compose.json").exists()])
def test_no_other_compose_configuration_is_refused(name: str, tmp_path: Path) -> None:
    check(results(name, tmp_path), tmp_path)  # does not raise
