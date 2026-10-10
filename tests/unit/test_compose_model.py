"""Parsing the resolved compose model (research.md §4, "Observed"; data-model.md, Port
allocation; FR-033, FR-034). No Docker: the documents are shaped like the output of
`docker compose config --format json`, and the command runner is a stub."""

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from wtenv import compose, ports
from wtenv.compose import PortMapping
from wtenv.errors import ErrorCode, WtenvError
from wtenv.output import WarningCode
from wtenv.registry import PortBlock


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


# --- fixed volume names (T187; reading R8) -----------------------------------------------------


def with_volumes(**volumes: object) -> dict[str, object]:
    """A resolved model named `app` with these top-level volumes, as `docker compose config` shows."""
    return {**document(web={"ports": []}), "volumes": volumes}


def test_a_volume_with_a_name_other_than_the_default_is_a_warning_naming_it() -> None:
    model = compose.parse_model(
        with_volumes(data={"name": "app_data"}, shared={"name": "shared-pgdata"})
    )

    (warning,) = model.warnings
    assert warning.code is WarningCode.COMPOSE_FIXED_VOLUME_NAME
    assert warning.details == {"volume": "shared", "name": "shared-pgdata"}
    assert "shared" in warning.message and "shared-pgdata" in warning.message


def test_an_external_volume_gives_no_warning() -> None:
    model = compose.parse_model(with_volumes(kept={"name": "someone-elses", "external": True}))

    assert model.warnings == []


def test_a_volume_with_the_default_name_gives_no_warning() -> None:
    model = compose.parse_model(
        with_volumes(data={"name": "app_data"}, cache={"name": "app_cache"})
    )

    assert model.warnings == []


def test_a_name_that_only_starts_like_the_default_is_a_fixed_name() -> None:
    model = compose.parse_model(
        with_volumes(data={"name": "app_data_old"}, cache={"name": "app-cache"})
    )

    assert [w.details["volume"] for w in model.warnings] == ["cache", "data"]


def test_each_fixed_volume_has_its_own_warning_in_name_order_after_the_container_warnings() -> None:
    model = compose.parse_model(
        {
            **with_volumes(b={"name": "fixed-b"}, a={"name": "fixed-a"}),
            "services": {"web": {"container_name": "c", "ports": []}},
        }
    )

    assert [w.code for w in model.warnings] == [
        WarningCode.COMPOSE_FIXED_CONTAINER_NAME,
        WarningCode.COMPOSE_FIXED_VOLUME_NAME,
        WarningCode.COMPOSE_FIXED_VOLUME_NAME,
    ]
    assert [w.details.get("volume") for w in model.warnings[1:]] == ["a", "b"]


@pytest.mark.parametrize(
    "volumes",
    [None, {}, {"data": None}, {"data": {}}],
    ids=["null", "empty", "null-volume", "no-name"],
)
def test_a_model_without_volumes_or_without_their_names_gives_no_volume_warning(
    volumes: object,
) -> None:
    assert compose.parse_model({**document(web={"ports": []}), "volumes": volumes}).warnings == []


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


# --- the repository's override joins the model (FR-003, FR-015; research R1, R2) ----------------

OVERRIDE_HEADER = (
    "# Generated by wtenv for this worktree. Do not edit or commit; `wtenv up` rewrites it.\n"
)
COMPOSE_FILE = Path("/w/compose.yaml")
REPOSITORY_OVERRIDE = Path("/w/compose.override.yml")


def combined(**services: object) -> str:
    """The JSON that Compose prints for the compose file and the repository's override together."""
    return json.dumps(document(**services))


def resolve_combined(stdout: str, variables: Sequence[str] = ()) -> compose.ComposeModel:
    """Resolve the combined model of `COMPOSE_FILE` and `REPOSITORY_OVERRIDE` from `stdout`."""
    return compose.resolve_model(
        COMPOSE_FILE,
        variables,
        repository_override=REPOSITORY_OVERRIDE,
        run=StubRunner(stdout),
        environ={},
    )


def test_the_resolution_command_with_an_override_names_both_files_in_order() -> None:
    assert compose.resolution_command(COMPOSE_FILE, REPOSITORY_OVERRIDE) == [
        "docker",
        "compose",
        "-f",
        "/w/compose.yaml",
        "-f",
        "/w/compose.override.yml",
        "--profile",
        "*",
        "config",
        "--format",
        "json",
    ]


def test_the_resolution_command_without_an_override_is_exactly_v0_1_1s() -> None:
    v0_1_1 = ["docker", "compose", "-f", "/w/compose.yaml", "--profile", "*", "config"]
    v0_1_1 += ["--format", "json"]

    assert compose.resolution_command(COMPOSE_FILE) == v0_1_1
    assert compose.resolution_command(COMPOSE_FILE, None) == v0_1_1


def test_resolving_with_an_override_runs_the_two_file_command_in_the_compose_files_directory() -> (
    None
):
    runner = StubRunner(combined(web={"ports": [mapping(target=80, published="1")]}))

    compose.resolve_model(
        COMPOSE_FILE,
        ["API_PORT"],
        repository_override=REPOSITORY_OVERRIDE,
        run=runner,
        environ={"HOME": "/h"},
    )

    ((command, environ, cwd),) = runner.calls
    assert command == compose.resolution_command(COMPOSE_FILE, REPOSITORY_OVERRIDE)
    assert environ == {"HOME": "/h", "API_PORT": "1"}
    assert cwd == Path("/w")


def test_a_compose_failure_with_an_override_is_config_invalid_naming_the_compose_file() -> None:
    runner = StubRunner(returncode=1, stderr="open /w/compose.override.yml: no such file")

    with pytest.raises(WtenvError) as caught:
        compose.resolve_model(
            COMPOSE_FILE, ["PORT"], repository_override=REPOSITORY_OVERRIDE, run=runner, environ={}
        )

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details["setting"] == "compose.file"
    assert "no such file" in caught.value.message


def test_a_port_that_only_the_repository_override_publishes_is_in_the_model() -> None:
    model = resolve_combined(
        combined(
            web={"ports": [mapping(target=80, published="8080")]},
            worker={"ports": [mapping(target=9000, published="9001")]},  # from the override
        )
    )

    assert [(m.service, m.target, m.published) for m in model.mappings] == [
        ("web", 80, "8080"),
        ("worker", 9000, "9001"),
    ]


def test_a_fixed_host_port_is_replaced_and_the_rest_of_the_entry_is_kept() -> None:
    model = resolve_combined(
        combined(
            backend={
                "ports": [
                    mapping(
                        target=8000,
                        published="8000",  # the repository override's fixed host port
                        protocol="udp",
                        mode="host",
                        host_ip="127.0.0.1",
                        name="api",
                        app_protocol="http",
                    )
                ]
            }
        )
    )

    text = compose.override_text("wtenv-x", [(model.mappings[0], 20002)])

    assert text == (
        OVERRIDE_HEADER
        + 'name: "wtenv-x"\n'
        + "services:\n"
        + '  "backend":\n'
        + "    ports: !override\n"
        + '      - {"target": 8000, "published": "20002", "protocol": "udp", "mode": "host", '
        + '"host_ip": "127.0.0.1", "name": "api", "app_protocol": "http"}\n'
    )


def test_a_host_port_taken_from_a_variable_is_replaced_too() -> None:
    model = resolve_combined(
        combined(backend={"ports": [mapping(target=8000, published="1")]}),  # marker of API_PORT
        ["API_PORT"],
    )

    text = compose.override_text("wtenv-x", [(model.mappings[0], 20000)])

    assert '"published": "20000"' in text
    assert '"published": "1"' not in text


def test_a_range_published_by_the_repository_override_is_unsupported_naming_the_service() -> None:
    with pytest.raises(WtenvError) as caught:
        resolve_combined(
            combined(
                web={"ports": [mapping(target=80, published="8080")]},
                api={"ports": [mapping(target=7000, published="7000-7005")]},
            )
        )

    error = caught.value
    assert error.code is ErrorCode.UNSUPPORTED
    assert error.details == {"reason": "compose_port_range", "service": "api"}


def test_the_block_size_counts_the_ports_of_the_combined_model() -> None:
    model = resolve_combined(
        combined(
            web={"ports": [mapping(target=80, published="8080")]},
            worker={"ports": [mapping(target=9000, published="9001")]},  # only in the override
            cache={"ports": [mapping(target=6379, published="6379")]},
        )
    )
    block = PortBlock(start=20000, size=2)

    with pytest.raises(WtenvError) as caught:
        ports.assign_published_ports(model.mappings, [], block)

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details == {"setting": "block_size", "min_block_size": 3}


def test_the_same_container_port_published_again_by_the_override_is_a_port_variable_conflict() -> (
    None
):
    """E1.6b: the file publishes web:80 on 8080 and the override publishes it on 9090 too."""
    model = resolve_combined(
        combined(
            web={
                "ports": [
                    mapping(target=80, published="8080"),
                    mapping(target=80, published="9090"),
                ]
            }
        )
    )

    with pytest.raises(WtenvError) as caught:
        ports.check_port_variables(model.mappings, {})

    error = caught.value
    assert error.code is ErrorCode.PORT_VARIABLE_CONFLICT
    assert error.details["variable"] == "WTENV_PORT_WEB_80_TCP"
