"""Write the reference outputs of v0.1.1 for the compatibility tests of spec 002 (T002).

FR-008, FR-028, FR-029. Run it with the code of tag `v0.1.1`, never with the new code and never
by hand-writing a golden file:

    git worktree add --detach "$SCRATCH/v0.1.1" v0.1.1
    uv run --project "$SCRATCH/v0.1.1" python -I tests/fixtures/v0_1_1/make_golden.py --out DIR
    git worktree remove "$SCRATCH/v0.1.1"

For every `configs/<name>.toml` it writes `<name>.json` (the configuration dump, the ports, the
project name, the database name or path, and the env-section text) and, for a configuration with
a compose file, `<name>.override.yaml`. It also writes `registry.json`, saved by v0.1.1's
`registry.save` for four entries built from those results.

A compose configuration has `configs/<name>.compose.yaml` (its compose file) and
`configs/<name>.compose.json`, so this script needs no Docker. The JSON is what wtenv's own
resolution prints, with the marker of variable i set to i + 1 (compose.marker_environment):

    docker compose -f <file> --profile '*' config --format json

run in a directory named `app`, with the variables of `ports` set to 1, 2, and so on.
"""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# The inputs are fixed, so every run gives the same bytes.
BLOCK_START = 20000
WORKTREE_NAME = "feature-x"
GIT_DIR = "/golden/repo/.git/worktrees/feature-x"
REPOSITORY = "/golden/repo/.git"

HERE = Path(__file__).resolve().parent
CONFIGS = HERE / "configs"

# The registry's four entries: (configuration, worktree name, block start).
REGISTRY_ENTRIES = (
    ("us3_stack", "compose", 20000),
    ("postgres_pgpassword", "postgres", 20010),
    ("us1_sqlite", "sqlite", 20020),
    ("us3_no_compose", "plain", 20030),
)


def compute(
    name: str, worktree_name: str, git_dir: str, block_start: int, scratch: Path
) -> dict[str, Any]:
    """Run the shared public functions of v0.1.1 on `configs/<name>.toml`; return the results."""
    from wtenv import compose, config, database, envfile, ports
    from wtenv.registry import PortBlock

    root = Path(tempfile.mkdtemp(dir=scratch))
    shutil.copyfile(CONFIGS / f"{name}.toml", root / "wtenv.toml")
    loaded = config.load_config(root)
    block = PortBlock(start=block_start, size=loaded.block_size)
    assigned = ports.assign_variable_ports(loaded.ports, block)
    result: dict[str, Any] = {
        "config": loaded.model_dump(mode="json"),
        "ports": [entry.model_dump(mode="json") for entry in assigned],
        "published": [],
        "project": None,
        "warnings": [],
        "database": None,
        "env_section": None,
        "override": None,
    }

    compose_json = CONFIGS / f"{name}.compose.json"
    if loaded.compose is not None:
        document = json.loads(compose_json.read_text(encoding="utf-8"))
        model = compose.parse_model(document)
        published = ports.assign_published_ports(model.mappings, loaded.ports, block)
        project = compose.project_name(worktree_name, git_dir)
        # `assign_published_ports` returns its result in this order (service, container port,
        # protocol, host IP), so the sorted mappings line up with it.
        ordered = sorted(
            model.mappings,
            key=lambda m: (m.service, m.target, m.protocol, m.host_ip or ""),
        )
        pairs = list(zip(ordered, (entry.port for entry in published), strict=True))
        result["published"] = [entry.model_dump(mode="json") for entry in published]
        result["project"] = project
        result["warnings"] = [warning.model_dump(mode="json") for warning in model.warnings]
        result["override"] = compose.override_text(project, pairs)

    if loaded.database is not None:
        if loaded.database.type == "postgres":
            result["database"] = {
                "kind": "postgres",
                "name": database.postgres_database_name(worktree_name, git_dir),
            }
        else:
            copy = database.sqlite_copy_path(
                f"/golden/worktrees/{worktree_name}", loaded.database.template
            )
            result["database"] = {"kind": "sqlite", "path": str(copy)}

    env_path = Path(tempfile.mkdtemp(dir=scratch)) / ".env.local"
    variables = [(entry.variable, str(entry.port)) for entry in assigned]
    envfile.write_section(env_path, variables)
    result["env_section"] = env_path.read_text(encoding="utf-8")
    return result


def registry_entry(
    name: str, worktree_name: str, git_dir: str, block_start: int, results: dict[str, Any]
) -> Any:
    """Build one registry entry from the results `compute` returned for `name`."""
    from wtenv import compose
    from wtenv.output import ResourceState
    from wtenv.registry import (
        ComposeRecord,
        DatabaseRecord,
        EnvFileRecord,
        PortBlock,
        PublishedPort,
        VariablePort,
        WorktreeEntry,
    )

    loaded = results["config"]
    entry = WorktreeEntry(
        git_dir=git_dir,
        path=f"/golden/worktrees/{worktree_name}",
        repository=REPOSITORY,
        state="provisioned",
        block=PortBlock(start=block_start, size=loaded["block_size"]),
        ports=[VariablePort(**item) for item in results["ports"]],
        published=[PublishedPort(**item) for item in results["published"]],
        env_file=EnvFileRecord(
            path=loaded["env_file"],
            created_file=True,
            added_newline=False,
            state=ResourceState.CREATED,
        ),
    )
    if results["project"] is not None:
        entry.compose = ComposeRecord(
            project=results["project"],
            file=loaded["compose"]["file"],
            override=compose.override_path(loaded["compose"]["file"]),
            override_state=ResourceState.CREATED,
        )
    chosen = results["database"]
    if chosen is not None and chosen["kind"] == "postgres":
        # Host, port, and user come from the URL pattern at creation, with the placeholders
        # replaced by stand-ins; the password never reaches a record.
        url = loaded["database"]["url"].replace("{name}", "db")
        url = url.replace("{env:PGPASSWORD}", "x")
        parts = urlsplit(url)
        entry.databases.append(
            DatabaseRecord(
                kind="postgres",
                name=chosen["name"],
                host=parts.hostname,
                port=parts.port,
                user=parts.username,
                state=ResourceState.CREATED,
            )
        )
    elif chosen is not None:
        relative = os.path.relpath(chosen["path"], f"/golden/worktrees/{worktree_name}")
        entry.databases.append(
            DatabaseRecord(kind="sqlite", path=relative, state=ResourceState.CREATED)
        )
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="the directory to write into")
    out: Path = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        for toml in sorted(CONFIGS.glob("*.toml")):
            name = toml.stem
            result = compute(name, WORKTREE_NAME, GIT_DIR, BLOCK_START, scratch)
            override = result.pop("override")
            (out / f"{name}.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            if override is not None:
                (out / f"{name}.override.yaml").write_text(override, encoding="utf-8")

        # The registry, saved by v0.1.1's own `registry.save` under a state directory of its own.
        os.environ["XDG_STATE_HOME"] = str(scratch / "state")
        from wtenv import registry

        built = registry.Registry(version=registry.REGISTRY_VERSION)
        for name, label, block_start in REGISTRY_ENTRIES:
            git_dir = f"{REPOSITORY}/worktrees/{label}"
            results = compute(name, label, git_dir, block_start, scratch)
            built.worktrees[git_dir] = registry_entry(name, label, git_dir, block_start, results)
        registry.save(built)
        shutil.copyfile(registry.registry_path(), out / "registry.json")


if __name__ == "__main__":
    main()
