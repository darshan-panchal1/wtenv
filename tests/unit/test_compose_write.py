"""Writing the override file and verifying it with plain Compose (research.md §4; T081; FR-033).
The command runner is a stub, so no Docker is needed."""

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from wtenv import compose
from wtenv.compose import PortMapping
from wtenv.errors import ErrorCode, WtenvError

PROJECT = "wtenv-feature-x-3f9a1c2b"
PLAIN_COMMAND = ["docker", "compose", "--profile", "*", "config", "--format", "json"]


def mapping(service: str, target: int, published: str | None = "1") -> PortMapping:
    return PortMapping(
        service=service,
        target=target,
        protocol="tcp",
        host_ip=None,
        published=published,
        mode="ingress",
        name=None,
        app_protocol=None,
    )


ASSIGNED = [(mapping("cache", 6379), 20012), (mapping("db", 5432), 20011)]


def shown(name: str = PROJECT, **published: list[tuple[int, str]]) -> dict[str, Any]:
    """What plain `docker compose config` prints: a project name and each service's ports."""
    return {
        "name": name,
        "services": {
            service: {
                "ports": [
                    {"mode": "ingress", "target": target, "published": port, "protocol": "tcp"}
                    for target, port in ports
                ]
            }
            for service, ports in published.items()
        },
    }


GOOD = shown(cache=[(6379, "20012")], db=[(5432, "20011")])


class Stub:
    def __init__(self, document: Mapping[str, Any] | None, returncode: int = 0) -> None:
        text = "" if document is None else json.dumps(document)
        self.answer = subprocess.CompletedProcess([], returncode, text, "boom")
        self.calls: list[tuple[list[str], dict[str, str], Path | None]] = []

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        self.calls.append((list(command), dict(environ), cwd))
        return self.answer


# --- writing ---------------------------------------------------------------------------------


def test_a_new_file_is_created(tmp_path: Path) -> None:
    path = tmp_path / "compose.override.yaml"

    assert compose.write_override(path, "text\n") == "created"
    assert path.read_text() == "text\n"


def test_the_same_text_is_unchanged_and_the_file_is_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "compose.override.yaml"
    path.write_text("text\n")
    before = path.stat().st_mtime_ns

    assert compose.write_override(path, "text\n") == "unchanged"
    assert path.stat().st_mtime_ns == before


def test_other_text_replaces_the_file(tmp_path: Path) -> None:
    path = tmp_path / "compose.override.yaml"
    path.write_text("old\n")

    assert compose.write_override(path, "new\n") == "updated"
    assert path.read_text() == "new\n"


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    compose.write_override(tmp_path / "compose.override.yaml", "text\n")

    assert [p.name for p in tmp_path.iterdir()] == ["compose.override.yaml"]


def test_a_failure_before_the_rename_keeps_the_old_file_and_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "compose.override.yaml"
    path.write_text("old\n")

    def refuse(source: object, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        compose.write_override(path, "new\n")

    assert path.read_text() == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["compose.override.yaml"]


# --- verifying -------------------------------------------------------------------------------


def verify(stub: Stub, tmp_path: Path, compose_file: str = "compose.yaml") -> None:
    compose.verify_override(
        tmp_path, compose_file, PROJECT, ASSIGNED, run=stub, environ={"HOME": "/h"}
    )


def test_a_plain_compose_run_in_the_compose_files_directory_that_matches_passes(
    tmp_path: Path,
) -> None:
    stub = Stub(GOOD)

    verify(stub, tmp_path, "deploy/compose.yaml")

    assert stub.calls == [(PLAIN_COMMAND, {"HOME": "/h"}, tmp_path / "deploy")]


def failed(stub: Stub, tmp_path: Path) -> WtenvError:
    with pytest.raises(WtenvError) as caught:
        verify(stub, tmp_path)
    assert caught.value.code is ErrorCode.UNSUPPORTED
    assert caught.value.details["reason"] == "compose_verification_failed"
    assert caught.value.details["file"] == str(tmp_path / "compose.yaml")
    return caught.value


def test_another_project_name_fails(tmp_path: Path) -> None:
    other = shown("someone-else", cache=[(6379, "20012")], db=[(5432, "20011")])

    assert "someone-else" in failed(Stub(other), tmp_path).message


def test_a_published_port_that_was_not_assigned_fails(tmp_path: Path) -> None:
    other = shown(cache=[(6379, "6379")], db=[(5432, "20011")])

    assert "cache" in failed(Stub(other), tmp_path).message


def test_an_extra_published_port_fails(tmp_path: Path) -> None:
    extra = shown(cache=[(6379, "20012")], db=[(5432, "20011")], web=[(80, "8080")])

    assert "web" in failed(Stub(extra), tmp_path).message


def test_a_missing_port_fails(tmp_path: Path) -> None:
    failed(Stub(shown(cache=[(6379, "20012")])), tmp_path)


def test_a_command_that_fails_or_prints_no_json_fails(tmp_path: Path) -> None:
    failed(Stub(None, returncode=1), tmp_path)
    failed(Stub(None), tmp_path)


# --- writing and verifying together ----------------------------------------------------------


def write_and_verify(stub: Stub, tmp_path: Path, text: str = "new\n") -> str:
    return compose.write_and_verify_override(
        tmp_path,
        "compose.yaml",
        "compose.override.yaml",
        PROJECT,
        ASSIGNED,
        text,
        run=stub,
        environ={},
    )


def test_a_verified_override_stays_and_the_action_is_returned(tmp_path: Path) -> None:
    assert write_and_verify(Stub(GOOD), tmp_path) == "created"
    assert (tmp_path / "compose.override.yaml").read_text() == "new\n"
    assert write_and_verify(Stub(GOOD), tmp_path) == "unchanged"


def test_an_override_that_fails_verification_is_removed_and_the_error_is_raised(
    tmp_path: Path,
) -> None:
    with pytest.raises(WtenvError) as caught:
        write_and_verify(Stub(shown("other")), tmp_path)

    assert caught.value.details["reason"] == "compose_verification_failed"
    assert not (tmp_path / "compose.override.yaml").exists()


def test_a_replaced_override_that_fails_verification_is_removed_too(tmp_path: Path) -> None:
    (tmp_path / "compose.override.yaml").write_text("old\n")

    with pytest.raises(WtenvError):
        write_and_verify(Stub(shown("other")), tmp_path)

    assert not (tmp_path / "compose.override.yaml").exists()
