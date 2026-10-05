"""docker-compose isolation: reading the compose model through Compose itself (research.md §4).

Compose runs as a subprocess. wtenv has no YAML library and no Docker SDK: it reads the output of
`docker compose config --format json`, so what it sees is what Compose will later run. This
module is imported only when `[compose]` is configured (research.md §8).
"""

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from wtenv.errors import ErrorCode, WtenvError
from wtenv.output import WarningCode, WarningInfo

_FILE_SETTING = "compose.file"


class Runner(Protocol):
    """Runs a command with an environment and a working directory and returns the process.

    The checks and the resolution take the runner as a parameter, so tests need no Docker.
    """

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        """Run `command`; never raise for a non-zero exit status."""
        ...


def run_command(
    command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
) -> "subprocess.CompletedProcess[str]":
    """Run `command` with exactly `environ`, in `cwd`, and capture its text output."""
    return subprocess.run(
        list(command),
        env=dict(environ),
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@dataclass(frozen=True)
class PortMapping:
    """One port mapping of a service, with the fields Compose reported for it."""

    service: str
    target: int  # the container port
    protocol: str
    host_ip: str | None
    # The host port as Compose reports it: a string, or None when the mapping has none ("3000").
    published: str | None
    mode: str | None
    name: str | None
    app_protocol: str | None


@dataclass(frozen=True)
class ComposeModel:
    """What wtenv needs of a resolved compose file: its port mappings and its warnings."""

    mappings: list[PortMapping]
    warnings: list[WarningInfo]


# --- resolving the compose file (research.md §4, Decision) -------------------------------------


def resolution_command(file: str | Path) -> list[str]:
    """Return the command that resolves `file` with every profile (research.md §4)."""
    return [
        "docker",
        "compose",
        "-f",
        str(file),
        "--profile",
        "*",
        "config",
        "--format",
        "json",
    ]


def marker_environment(variables: Sequence[str]) -> dict[str, str]:
    """Return the marker values: variable `i` of `variables` is set to `i + 1` (data-model.md).

    A published port that resolves to the marker of variable `i` is tied to that variable.
    Markers are used instead of real ports, so the analysis needs no block.
    """
    return {name: str(index + 1) for index, name in enumerate(variables)}


def resolve_model(
    file: Path,
    variables: Sequence[str],
    *,
    run: Runner = run_command,
    environ: Mapping[str, str] | None = None,
) -> ComposeModel:
    """Have Compose resolve `file` with the markers of `variables`, and parse the result.

    Raises `config_invalid` (naming `compose.file`) when Compose cannot resolve the file, and
    `unsupported` for a published range (FR-033). `environ` defaults to the process environment.
    """
    base = os.environ if environ is None else environ
    process = run(
        resolution_command(file),
        {**base, **marker_environment(variables)},
        file.parent,
    )
    if process.returncode != 0:
        raise _unresolvable(file, process.stderr.strip() or f"exit status {process.returncode}")
    try:
        document = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise _unresolvable(file, f"its resolved form is not JSON ({error})") from error
    if not isinstance(document, dict):
        raise _unresolvable(file, "its resolved form is not a JSON object")
    return parse_model(document)


def _unresolvable(file: Path, problem: str) -> WtenvError:
    """Build `config_invalid` for a compose file that Compose cannot resolve."""
    return WtenvError(
        ErrorCode.CONFIG_INVALID,
        f"{file}: Compose cannot resolve it: {problem}",
        hint="Check that `docker compose config` works on the file, then run `wtenv up` again. "
        "Nothing was changed.",
        details={"file": str(file), "setting": _FILE_SETTING},
    )


# --- parsing the resolved model ----------------------------------------------------------------


def parse_model(document: Mapping[str, Any]) -> ComposeModel:
    """Return the port mappings and warnings in a resolved compose model.

    Mappings come in service-name order and, within a service, in the order Compose reported.
    A mapping without a host port is kept with `published` None. A published range raises
    `unsupported` (`compose_port_range`, FR-033). A service with `container_name` gets the
    warning `compose_fixed_container_name`.
    """
    services: Mapping[str, Mapping[str, Any]] = document.get("services") or {}
    mappings: list[PortMapping] = []
    warnings: list[WarningInfo] = []
    for service in sorted(services):
        settings = services[service]
        if settings.get("container_name"):
            warnings.append(_fixed_container_name(service, str(settings["container_name"])))
        for entry in settings.get("ports") or []:
            mapping = _mapping(service, entry)
            if mapping.published is not None and "-" in mapping.published:
                raise _port_range(service, mapping.published)
            mappings.append(mapping)
    return ComposeModel(mappings=mappings, warnings=warnings)


def _mapping(service: str, entry: Mapping[str, Any]) -> PortMapping:
    published = entry.get("published")
    return PortMapping(
        service=service,
        target=int(entry["target"]),
        protocol=str(entry.get("protocol", "tcp")),
        host_ip=entry.get("host_ip"),
        published=None if published is None else str(published),
        mode=entry.get("mode"),
        name=entry.get("name"),
        app_protocol=entry.get("app_protocol"),
    )


def _port_range(service: str, published: str) -> WtenvError:
    return WtenvError(
        ErrorCode.UNSUPPORTED,
        f"service {service} publishes the host range {published}; Compose picks any free port "
        "of a range when it starts, so wtenv cannot pin it",
        hint=f"Publish one host port per container port in service {service}.",
        details={"reason": "compose_port_range", "service": service},
    )


def _fixed_container_name(service: str, container_name: str) -> WarningInfo:
    return WarningInfo(
        code=WarningCode.COMPOSE_FIXED_CONTAINER_NAME,
        message=(
            f"service {service} sets container_name {container_name}; two worktrees cannot "
            "both run it"
        ),
        details={"service": service, "container_name": container_name},
    )
