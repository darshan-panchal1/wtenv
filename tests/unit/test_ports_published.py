"""Assigning the ports compose services publish (data-model.md, Port allocation steps 2 to 5;
FR-014, FR-031, FR-032)."""

import pytest

from wtenv.compose import PortMapping
from wtenv.errors import ErrorCode, WtenvError
from wtenv.ports import assign_published_ports, check_block_size, count_untied_ports
from wtenv.registry import PortBlock, PublishedPort

BLOCK = PortBlock(start=20010, size=10)


def mapping(
    service: str,
    target: int,
    published: str | None,
    protocol: str = "tcp",
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


def published(
    service: str,
    target: int,
    port: int,
    variable: str | None = None,
    protocol: str = "tcp",
    host_ip: str | None = None,
) -> PublishedPort:
    return PublishedPort.model_validate(
        {
            "service": service,
            "target": target,
            "protocol": protocol,
            "host_ip": host_ip,
            "port": port,
            "variable": variable,
        }
    )


# --- tying a port to a variable (FR-031) -----------------------------------------------------


def test_a_port_equal_to_the_marker_of_variable_i_gets_that_variables_port() -> None:
    # Variable 1 (DB_PORT) has the marker 2; the compose file said "${DB_PORT:-5432}:5432".
    result = assign_published_ports([mapping("db", 5432, "2")], ["PORT", "DB_PORT"], BLOCK)

    assert result == [published("db", 5432, 20011, variable="DB_PORT")]


def test_the_first_variable_has_the_marker_one() -> None:
    result = assign_published_ports([mapping("web", 80, "1")], ["PORT", "DB_PORT"], BLOCK)

    assert result == [published("web", 80, 20010, variable="PORT")]


def test_a_marker_beyond_the_variables_is_an_ordinary_port() -> None:
    result = assign_published_ports([mapping("web", 80, "3")], ["PORT", "DB_PORT"], BLOCK)

    assert result == [published("web", 80, 20012)]


# --- the other ports --------------------------------------------------------------------------


def test_other_ports_follow_the_variables_in_service_port_protocol_host_ip_order() -> None:
    mappings = [
        mapping("web", 443, "8443"),
        mapping("db", 5432, "5432"),
        mapping("web", 80, "8080", host_ip="127.0.0.1"),
        mapping("web", 80, "8080"),
        mapping("web", 80, "8081", protocol="udp"),
    ]

    result = assign_published_ports(mappings, ["PORT", "API"], BLOCK)

    assert [(p.service, p.target, p.protocol, p.host_ip, p.port) for p in result] == [
        ("db", 5432, "tcp", None, 20012),
        ("web", 80, "tcp", None, 20013),
        ("web", 80, "tcp", "127.0.0.1", 20014),
        ("web", 80, "udp", None, 20015),
        ("web", 443, "tcp", None, 20016),
    ]
    assert all(p.variable is None for p in result)


def test_a_port_with_no_host_port_gets_a_block_port_too() -> None:
    result = assign_published_ports([mapping("web", 3000, None)], ["PORT"], BLOCK)

    assert result == [published("web", 3000, 20011)]


def test_tied_and_untied_ports_together() -> None:
    mappings = [
        mapping("cache", 6379, None),
        mapping("db", 5432, "2"),
    ]

    result = assign_published_ports(mappings, ["PORT", "DB_PORT"], BLOCK)

    assert result == [
        published("cache", 6379, 20012),
        published("db", 5432, 20011, variable="DB_PORT"),
    ]


def test_no_mappings_assign_nothing() -> None:
    assert assign_published_ports([], ["PORT"], BLOCK) == []


def test_the_same_input_gives_the_same_result_whatever_the_order_given() -> None:
    mappings = [mapping("b", 2, "9"), mapping("a", 1, "8"), mapping("a", 3, "1")]

    assert assign_published_ports(mappings, ["PORT"], BLOCK) == assign_published_ports(
        list(reversed(mappings)), ["PORT"], BLOCK
    )


# --- clashes -----------------------------------------------------------------------------------


def test_two_mappings_with_one_protocol_and_host_ip_on_one_port_clash() -> None:
    mappings = [mapping("web", 80, "1"), mapping("admin", 8080, "1")]

    with pytest.raises(WtenvError) as caught:
        assign_published_ports(mappings, ["PORT"], BLOCK)

    assert caught.value.code is ErrorCode.UNSUPPORTED
    assert caught.value.details["reason"] == "compose_port_clash"
    assert caught.value.details["service"] in ("web", "admin")


def test_the_same_port_with_another_protocol_is_no_clash() -> None:
    mappings = [mapping("dns", 53, "1"), mapping("dns", 53, "1", protocol="udp")]

    result = assign_published_ports(mappings, ["PORT"], BLOCK)

    assert [(p.protocol, p.port) for p in result] == [("tcp", 20010), ("udp", 20010)]


def test_the_same_port_with_another_host_ip_is_no_clash() -> None:
    mappings = [mapping("web", 80, "1"), mapping("web", 81, "1", host_ip="127.0.0.1")]

    result = assign_published_ports(mappings, ["PORT"], BLOCK)

    assert [p.port for p in result] == [20010, 20010]


# --- the block is too small (FR-014, FR-032) ---------------------------------------------------


def test_more_ports_than_the_block_holds_is_config_invalid_with_the_smallest_size() -> None:
    mappings = [mapping("a", 1, None), mapping("b", 2, None), mapping("c", 3, "1")]
    block = PortBlock(start=20000, size=3)

    with pytest.raises(WtenvError) as caught:
        assign_published_ports(mappings, ["PORT", "DB_PORT"], block)

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details["setting"] == "block_size"
    # Two variables plus two published ports that are not tied to one.
    assert caught.value.details["min_block_size"] == 4


def test_a_block_with_exactly_enough_room_is_enough() -> None:
    mappings = [mapping("a", 1, None), mapping("b", 2, "1")]
    block = PortBlock(start=20000, size=2)

    result = assign_published_ports(mappings, ["PORT"], block)

    assert sorted(p.port for p in result) == [20000, 20001]


def test_count_untied_ports_counts_what_needs_a_port_of_its_own() -> None:
    mappings = [
        mapping("a", 1, None),
        mapping("b", 2, "1"),
        mapping("c", 3, "2"),
        mapping("d", 4, "7000"),
    ]

    assert count_untied_ports(mappings, 2) == 2
    assert count_untied_ports(mappings, 1) == 3
    assert count_untied_ports([], 1) == 0


def test_check_block_size_counts_the_published_ports() -> None:
    check_block_size(2, 5, published=3)

    with pytest.raises(WtenvError) as caught:
        check_block_size(2, 4, published=3)

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details["min_block_size"] == 5
