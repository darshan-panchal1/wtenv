"""The variable name of every published port, and the collisions between names (T015; FR-011,
FR-012, FR-015, SC-008; research R5; files.md, "Env file section").

The name is a pure function of the service name, the container port and the protocol. Two
published ports never share a name, and a name never equals one the configuration defines: those
cases are refused with `port_variable_conflict` before anything is changed. No Docker is needed;
the compose models are the resolved ones captured in `tests/fixtures/v0_1_1/configs/`.
"""

import ast
import json
import shutil
from pathlib import Path

import pytest

from wtenv.compose import PortMapping, parse_model
from wtenv.config import load_config
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.ports import check_port_variables, configured_variable_names, port_variable_name

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = ROOT / "tests" / "fixtures" / "v0_1_1" / "configs"
US3_COMPOSE_TESTS = ROOT / "tests" / "integration" / "test_us3_compose.py"
COMPOSE_IMAGE = "valkey/valkey:latest"


def mapping(
    service: str,
    target: int,
    protocol: str = "tcp",
    published: str | None = None,
    host_ip: str | None = None,
) -> PortMapping:
    return PortMapping(
        service=service,
        target=target,
        protocol=protocol,
        host_ip=host_ip,
        published=published,
        mode="ingress",
        name=None,
        app_protocol=None,
    )


def conflict_of(mappings: list[PortMapping], configured: dict[str, str]) -> WtenvError:
    """Run the check; return the error it raises, failing the test if it raises none."""
    with pytest.raises(WtenvError) as raised:
        check_port_variables(mappings, configured)
    return raised.value


# --- the rule (FR-011; research R5, worked examples 1 to 12) ---------------------------------

WORKED_EXAMPLES = {
    "1 adminer": ("adminer", 8080, "tcp", "WTENV_PORT_ADMINER_8080_TCP"),
    "2 backend": ("backend", 8000, "tcp", "WTENV_PORT_BACKEND_8000_TCP"),
    "3 db": ("db", 5432, "tcp", "WTENV_PORT_DB_5432_TCP"),
    "4 mailpit smtp web": ("mailpit", 8025, "tcp", "WTENV_PORT_MAILPIT_8025_TCP"),
    "5 mailpit smtp": ("mailpit", 1025, "tcp", "WTENV_PORT_MAILPIT_1025_TCP"),
    "6 playwright": ("playwright", 9323, "tcp", "WTENV_PORT_PLAYWRIGHT_9323_TCP"),
    "7 proxy 80": ("proxy", 80, "tcp", "WTENV_PORT_PROXY_80_TCP"),
    "8 proxy 8080": ("proxy", 8080, "tcp", "WTENV_PORT_PROXY_8080_TCP"),
    "9 coredns udp": ("coredns", 53, "udp", "WTENV_PORT_COREDNS_53_UDP"),
    "9 coredns tcp": ("coredns", 53, "tcp", "WTENV_PORT_COREDNS_53_TCP"),
    "10 a dot in the name": ("api.v2", 8000, "tcp", "WTENV_PORT_API_V2_8000_TCP"),
    "11 a digit first": ("2fa", 9000, "tcp", "WTENV_PORT_2FA_9000_TCP"),
    "12 a-b": ("a-b", 1, "tcp", "WTENV_PORT_A_B_1_TCP"),
    "12 a--b": ("a--b", 1, "tcp", "WTENV_PORT_A__B_1_TCP"),
}


@pytest.mark.parametrize("case", WORKED_EXAMPLES)
def test_the_variable_name_depends_only_on_service_port_and_protocol(case: str) -> None:
    # Research R5: upper case, every character other than A-Z and 0-9 replaced by `_` (one `_`
    # per character; nothing is trimmed or collapsed).
    service, target, protocol, expected = WORKED_EXAMPLES[case]

    assert port_variable_name(service, target, protocol) == expected


def test_every_character_other_than_ascii_letters_and_digits_becomes_one_underscore() -> None:
    # Not `str.upper()` then a regex: `ß` would become `SS` and `ſ` would become `S`.
    assert port_variable_name("straße", 80, "tcp") == "WTENV_PORT_STRA_E_80_TCP"
    assert port_variable_name("ſ", 80, "tcp") == "WTENV_PORT___80_TCP"
    assert port_variable_name("a b.c", 80, "udp") == "WTENV_PORT_A_B_C_80_UDP"


def test_the_name_is_the_same_for_any_block_host_port_host_ip_and_service_order() -> None:
    first = [
        mapping("web", 80, published="1", host_ip=None),
        mapping("db", 5432, published="20011", host_ip="127.0.0.1"),
    ]
    second = [
        mapping("db", 5432, published=None, host_ip="0.0.0.0"),
        mapping("web", 80, published="8080", host_ip="::1"),
    ]

    def names(mappings: list[PortMapping]) -> set[str]:
        return {port_variable_name(m.service, m.target, m.protocol) for m in mappings}

    assert names(first) == names(second) == {"WTENV_PORT_WEB_80_TCP", "WTENV_PORT_DB_5432_TCP"}


def test_every_derived_name_is_a_valid_variable_name_with_the_prefix() -> None:
    for service in ("web", "2fa", "a-b", "api.v2", "ünï", "x y", "DATABASE_URL", "PORT", ""):
        name = port_variable_name(service, 80, "tcp")

        assert name.startswith("WTENV_PORT_")
        assert name.isascii() and name.replace("_", "A").isalnum()


# --- the names that distinct ports get (research R5, worked examples 12 to 15) ------------------


def test_names_that_differ_by_one_character_are_distinct() -> None:
    check_port_variables([mapping("a-b", 1), mapping("a--b", 1)], {})


def test_tcp_and_udp_on_one_container_port_are_two_variables() -> None:
    check_port_variables([mapping("coredns", 53, "udp"), mapping("coredns", 53, "tcp")], {})


# --- the collisions (FR-012; exit 22) ----------------------------------------------------------


def test_port_variable_conflict_has_exit_status_22() -> None:
    assert ErrorCode.PORT_VARIABLE_CONFLICT.value == "port_variable_conflict"
    assert EXIT_STATUS[ErrorCode.PORT_VARIABLE_CONFLICT] == 22


def test_service_names_that_differ_only_in_punctuation_collide() -> None:
    # Worked example 13.
    error = conflict_of([mapping("my_api", 8000), mapping("my-api", 8000)], {})

    assert error.code is ErrorCode.PORT_VARIABLE_CONFLICT
    assert error.details == {
        "variable": "WTENV_PORT_MY_API_8000_TCP",
        "sources": [
            "service my-api, container port 8000/tcp",
            "service my_api, container port 8000/tcp",
        ],
    }


def test_service_names_that_differ_only_in_case_collide() -> None:
    # Worked example 14.
    error = conflict_of([mapping("api", 80), mapping("API", 80)], {})

    assert error.code is ErrorCode.PORT_VARIABLE_CONFLICT
    assert error.details == {
        "variable": "WTENV_PORT_API_80_TCP",
        "sources": [
            "service API, container port 80/tcp",
            "service api, container port 80/tcp",
        ],
    }


def test_one_container_port_published_twice_collides() -> None:
    # Worked example 15 (E1.6b): two host addresses, one container port.
    error = conflict_of(
        [mapping("web", 6379, host_ip="127.0.0.1"), mapping("web", 6379, host_ip="::1")], {}
    )

    assert error.code is ErrorCode.PORT_VARIABLE_CONFLICT
    assert error.details == {
        "variable": "WTENV_PORT_WEB_6379_TCP",
        "sources": [
            "service web, container port 6379/tcp",
            "service web, container port 6379/tcp",
        ],
    }


def test_a_name_equal_to_a_ports_entry_collides() -> None:
    configured = configured_variable_names(["PORT", "WTENV_PORT_WEB_80_TCP"], has_database=False)

    error = conflict_of([mapping("web", 80)], configured)

    assert error.code is ErrorCode.PORT_VARIABLE_CONFLICT
    assert error.details == {
        "variable": "WTENV_PORT_WEB_80_TCP",
        "sources": ["service web, container port 80/tcp", "wtenv.toml ports"],
    }


def test_an_error_message_names_the_variable_and_both_sources() -> None:
    error = conflict_of([mapping("my-api", 8000), mapping("my_api", 8000)], {})

    assert "WTENV_PORT_MY_API_8000_TCP" in error.message
    assert "my-api" in error.message and "my_api" in error.message
    assert error.hint


# --- DATABASE_URL is a configured name, as data (FR-012) -----------------------------------------


def test_the_configured_names_are_the_ports_entries_and_database_url_when_a_database_is_set() -> (
    None
):
    assert configured_variable_names(["PORT", "CACHE_PORT"], has_database=False) == {
        "PORT": "wtenv.toml ports",
        "CACHE_PORT": "wtenv.toml ports",
    }
    assert configured_variable_names(["PORT"], has_database=True) == {
        "PORT": "wtenv.toml ports",
        "DATABASE_URL": "DATABASE_URL",
    }


def test_no_derived_name_can_equal_database_url() -> None:
    # Every derived name starts with `WTENV_PORT_`, so the comparison with `DATABASE_URL` can
    # never find a collision, whatever the service is called, even `DATABASE_URL` itself.
    configured = configured_variable_names([], has_database=True)
    services = ["database", "DATABASE_URL", "url", "x"]

    assert "DATABASE_URL" in configured
    assert all(port_variable_name(s, 1, "tcp") != "DATABASE_URL" for s in services)
    check_port_variables([mapping(service, 1) for service in services], configured)


# --- SC-008: never one name for two ports, over every compose model under tests/ ----------------


def stack_constants() -> dict[str, str]:
    """Return the `*STACK` constants of the US3 compose tests, as the text they hold."""
    tree = ast.parse(US3_COMPOSE_TESTS.read_text(encoding="utf-8"))
    stacks = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
            if name.endswith("STACK") and isinstance(value, ast.JoinedStr):
                # An f-string whose only replacement field is COMPOSE_IMAGE.
                stacks[name] = "".join(
                    part.value if isinstance(part, ast.Constant) else COMPOSE_IMAGE
                    for part in value.values
                )
    return stacks


def test_every_stack_constant_has_a_resolved_model_among_the_fixtures() -> None:
    # T002 resolved each configuration's compose file once, with Docker; the unit test then needs
    # none. A new `*STACK` constant without a fixture fails here, so SC-008 covers it.
    fixtures = [path.read_text(encoding="utf-8") for path in CONFIGS.glob("*.compose.yaml")]
    stacks = stack_constants()

    assert set(stacks) == {"STACK", "REMAPPED_STACK", "QUIET_STACK"}
    for name, text in stacks.items():
        assert text in fixtures, f"{name} has no *.compose.yaml fixture"


REFUSED_BY_FR_028 = {"dup_port", "colliding_names", "ports_named_like_a_variable"}


@pytest.mark.parametrize("path", sorted(CONFIGS.glob("*.compose.json")), ids=lambda p: p.name)
def test_every_compose_model_gives_distinct_names_or_is_refused(path: Path, tmp_path: Path) -> None:
    name = path.name.removesuffix(".compose.json")
    shutil.copy(CONFIGS / f"{name}.toml", tmp_path / "wtenv.toml")
    config = load_config(tmp_path)
    mappings = parse_model(json.loads(path.read_text(encoding="utf-8"))).mappings
    configured = configured_variable_names(config.ports, has_database=config.database is not None)

    if name in REFUSED_BY_FR_028:
        assert conflict_of(mappings, configured).code is ErrorCode.PORT_VARIABLE_CONFLICT
        return
    check_port_variables(mappings, configured)
    names = [port_variable_name(m.service, m.target, m.protocol) for m in mappings]
    assert len(names) == len(set(names))
