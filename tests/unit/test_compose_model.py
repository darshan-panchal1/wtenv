"""Parsing the resolved compose model (research.md §4, "Observed"; data-model.md, Port
allocation; FR-033, FR-034). No Docker: the documents are shaped like the output of
`docker compose config --format json`, and the command runner is a stub."""

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from wtenv import compose
from wtenv.compose import PortMapping
from wtenv.errors import ErrorCode, WtenvError
from wtenv.output import WarningCode


def document(**services: object) -> dict[str, object]:
    """A resolved compose model with these services."""
    return {"name": "app", "services": services}


def mapping(**fields: object) -> dict[str, object]:
    """One entry of a service's `ports`, as Compose reports it in long syntax."""
    return {"mode": "ingress", "protocol": "tcp", **fields}


# --- one record per mapping ------------------------------------------------------------------


def test_one_record_per_mapping_with_every_field_compose_reported() -> None:
    model = compose.parse_model(
        document(
            db={
                "image": "postgres:17",
                "ports": [
                    mapping(
                        name="pg",
                        target=5432,
                        published="5433",
                        host_ip="127.0.0.1",
                        app_protocol="pgwire",
                    )
                ],
            }
        )
    )

    assert model.mappings == [
        PortMapping(
            service="db",
            target=5432,
            protocol="tcp",
            host_ip="127.0.0.1",
            published="5433",
            mode="ingress",
            name="pg",
            app_protocol="pgwire",
        )
    ]


def test_fields_compose_did_not_report_are_none() -> None:
    model = compose.parse_model(document(web={"ports": [mapping(target=80, published="8080")]}))

    (record,) = model.mappings
    assert (record.host_ip, record.name, record.app_protocol) == (None, None, None)
    assert (record.mode, record.protocol) == ("ingress", "tcp")


def test_a_mapping_without_published_is_kept_as_having_no_host_port() -> None:
    model = compose.parse_model(document(web={"ports": [mapping(target=3000)]}))

    (record,) = model.mappings
    assert record.published is None
    assert record.target == 3000


def test_services_come_in_name_order_and_ports_in_the_order_reported() -> None:
    model = compose.parse_model(
        document(
            web={"ports": [mapping(target=81, published="2"), mapping(target=80, published="1")]},
            cache={"ports": [mapping(target=6379, published="3")]},
        )
    )

    assert [(m.service, m.target) for m in model.mappings] == [
        ("cache", 6379),
        ("web", 81),
        ("web", 80),
    ]


def test_services_without_ports_add_no_record() -> None:
    model = compose.parse_model(document(idle={"image": "busybox"}, web={"ports": []}))

    assert model.mappings == []


def test_a_document_without_services_has_no_records() -> None:
    assert compose.parse_model({"name": "app"}).mappings == []


def test_a_udp_mapping_keeps_its_protocol() -> None:
    model = compose.parse_model(
        document(dns={"ports": [mapping(target=53, published="5353", protocol="udp")]})
    )

    assert model.mappings[0].protocol == "udp"


# --- ranges ----------------------------------------------------------------------------------


def test_a_published_range_is_unsupported_naming_the_service() -> None:
    with pytest.raises(WtenvError) as caught:
        compose.parse_model(
            document(
                api={"ports": [mapping(target=7000, published="7000-7005")]},
                web={"ports": [mapping(target=80, published="8080")]},
            )
        )

    error = caught.value
    assert error.code is ErrorCode.UNSUPPORTED
    assert error.details["reason"] == "compose_port_range"
    assert error.details["service"] == "api"
    assert "api" in error.message


def test_a_range_on_one_port_of_several_still_fails() -> None:
    with pytest.raises(WtenvError) as caught:
        compose.parse_model(
            document(
                web={
                    "ports": [
                        mapping(target=80, published="8080"),
                        mapping(target=81, published="8000-9000"),
                    ]
                }
            )
        )

    assert caught.value.details["service"] == "web"


# --- fixed container names -------------------------------------------------------------------


def test_container_name_is_a_warning_naming_the_service() -> None:
    model = compose.parse_model(
        document(web={"container_name": "fixed", "ports": [mapping(target=80, published="1")]})
    )

    (warning,) = model.warnings
    assert warning.code is WarningCode.COMPOSE_FIXED_CONTAINER_NAME
    assert "web" in warning.message
    assert warning.details["service"] == "web"


def test_each_service_with_a_container_name_has_its_own_warning_in_name_order() -> None:
    model = compose.parse_model(
        document(
            web={"container_name": "a"},
            db={"container_name": "b"},
            cache={"image": "redis"},
        )
    )

    assert [w.details["service"] for w in model.warnings] == ["db", "web"]


def test_no_container_name_no_warning() -> None:
    assert compose.parse_model(document(web={"ports": []})).warnings == []


# --- the resolution command ------------------------------------------------------------------


def test_the_resolution_command_names_the_file_and_all_profiles() -> None:
    assert compose.resolution_command(Path("/w/deploy/compose.yaml")) == [
        "docker",
        "compose",
        "-f",
        "/w/deploy/compose.yaml",
        "--profile",
        "*",
        "config",
        "--format",
        "json",
    ]


def test_variable_i_is_set_to_the_marker_i_plus_one() -> None:
    assert compose.marker_environment(["PORT", "DB_PORT", "VITE_PORT"]) == {
        "PORT": "1",
        "DB_PORT": "2",
        "VITE_PORT": "3",
    }


class StubRunner:
    """A command runner that records its calls and answers with a canned process."""

    def __init__(self, stdout: str = "{}", returncode: int = 0, stderr: str = "") -> None:
        self.answer = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.calls: list[tuple[list[str], dict[str, str], Path | None]] = []

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], cwd: Path | None
    ) -> "subprocess.CompletedProcess[str]":
        self.calls.append((list(command), dict(environ), cwd))
        return self.answer


def test_resolving_runs_the_command_with_the_markers_in_the_environment() -> None:
    runner = StubRunner('{"services": {"web": {"ports": [{"target": 80, "published": "1"}]}}}')

    model = compose.resolve_model(
        Path("/w/compose.yaml"), ["API_PORT"], run=runner, environ={"HOME": "/h", "API_PORT": "9"}
    )

    ((command, environ, cwd),) = runner.calls
    assert command == compose.resolution_command(Path("/w/compose.yaml"))
    assert environ == {"HOME": "/h", "API_PORT": "1"}
    assert cwd == Path("/w")
    assert [(m.service, m.published) for m in model.mappings] == [("web", "1")]


def test_a_failed_resolution_is_config_invalid_naming_the_compose_file() -> None:
    runner = StubRunner(returncode=1, stderr="no configuration file provided: not found")

    with pytest.raises(WtenvError) as caught:
        compose.resolve_model(Path("/w/compose.yaml"), ["PORT"], run=runner, environ={})

    error = caught.value
    assert error.code is ErrorCode.CONFIG_INVALID
    assert error.details["setting"] == "compose.file"
    assert "no configuration file provided" in error.message


def test_output_that_is_not_json_is_config_invalid_naming_the_compose_file() -> None:
    with pytest.raises(WtenvError) as caught:
        compose.resolve_model(
            Path("/w/compose.yaml"), ["PORT"], run=StubRunner("not json"), environ={}
        )

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details["setting"] == "compose.file"
