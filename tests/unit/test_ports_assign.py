"""Assigning the port variables within a block (data-model.md, Port allocation; FR-014)."""

import pytest

from wtenv.errors import ErrorCode, WtenvError
from wtenv.ports import assign_variable_ports, check_block_size
from wtenv.registry import PortBlock, VariablePort

BLOCK = PortBlock(start=20010, size=10)


def test_variable_i_gets_start_plus_i() -> None:
    assert assign_variable_ports(["PORT", "API_PORT", "VITE_PORT"], BLOCK) == [
        VariablePort(variable="PORT", port=20010),
        VariablePort(variable="API_PORT", port=20011),
        VariablePort(variable="VITE_PORT", port=20012),
    ]


def test_one_variable_gets_the_start_of_the_block() -> None:
    assert assign_variable_ports(["PORT"], BLOCK) == [VariablePort(variable="PORT", port=20010)]


def test_the_same_input_gives_the_same_ports() -> None:
    names = ["A", "B", "C"]

    assert assign_variable_ports(names, BLOCK) == assign_variable_ports(names, BLOCK)


def test_the_order_of_the_list_is_the_order_of_the_ports() -> None:
    first = assign_variable_ports(["A", "B"], BLOCK)
    swapped = assign_variable_ports(["B", "A"], BLOCK)

    assert [p.variable for p in first] == ["A", "B"]
    assert [p.port for p in swapped] == [20010, 20011]
    assert swapped[0].variable == "B"


def test_a_block_with_exactly_enough_room_is_enough() -> None:
    block = PortBlock(start=20000, size=3)

    ports = assign_variable_ports(["A", "B", "C"], block)

    assert [p.port for p in ports] == [20000, 20001, 20002]


def test_every_port_is_inside_the_block() -> None:
    names = [f"V{i}" for i in range(BLOCK.size)]

    ports = assign_variable_ports(names, BLOCK)

    assert all(BLOCK.start <= p.port < BLOCK.start + BLOCK.size for p in ports)
    assert len({p.port for p in ports}) == BLOCK.size


def test_more_variables_than_the_block_holds_is_config_invalid() -> None:
    block = PortBlock(start=20000, size=2)

    with pytest.raises(WtenvError) as caught:
        assign_variable_ports(["A", "B", "C"], block)

    error = caught.value
    assert error.code is ErrorCode.CONFIG_INVALID
    assert error.details["setting"] == "block_size"
    assert error.details["min_block_size"] == 3
    assert error.hint


@pytest.mark.parametrize(("count", "size"), [(1, 1), (5, 5), (5, 10), (1000, 1000)])
def test_check_block_size_accepts_a_block_that_holds_every_variable(count: int, size: int) -> None:
    check_block_size(count, size)


@pytest.mark.parametrize(("count", "size"), [(2, 1), (11, 10), (40, 3)])
def test_check_block_size_states_the_smallest_size_that_fits(count: int, size: int) -> None:
    with pytest.raises(WtenvError) as caught:
        check_block_size(count, size)

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.details == {"setting": "block_size", "min_block_size": count}
    assert str(count) in caught.value.message
