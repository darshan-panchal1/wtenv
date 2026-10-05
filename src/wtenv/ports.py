"""Port allocation: the free-port test, the block search, and port assignment (data-model.md).

Every port wtenv hands out lies in `20000-29999`, in a block of consecutive ports. The search is
a pure function of the blocks already taken and of which ports are free, so the same registry
and the same free ports always give the same block (Principle III).
"""

import errno
import socket
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Literal, cast

from wtenv.errors import ErrorCode, WtenvError
from wtenv.registry import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    PortBlock,
    PublishedPort,
    VariablePort,
)

if TYPE_CHECKING:
    from wtenv.compose import PortMapping

# A bind to these addresses must succeed for a port to count as free (FR-009).
_PROBE_ADDRESSES = (
    (socket.AF_INET, "127.0.0.1"),
    (socket.AF_INET, "0.0.0.0"),
    (socket.AF_INET6, "::1"),
)
# A machine without IPv6 cannot bind `::1`; that says nothing about the port.
_ADDRESS_UNAVAILABLE = (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT)


def port_is_free(port: int) -> bool:
    """Return whether `port` is free: a TCP `bind` succeeds on each local address.

    `SO_REUSEADDR` is not set, so a port that another process holds, even only to connect from,
    is not free. An address family the machine does not have is skipped.
    """
    for family, host in _PROBE_ADDRESSES:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((host, port))
        except OSError as error:
            if error.errno in _ADDRESS_UNAVAILABLE:
                continue
            return False
    return True


def find_block(
    size: int,
    taken: Iterable[PortBlock],
    is_free: Callable[[int], bool] = port_is_free,
) -> PortBlock:
    """Return the first block of `size` ports that is neither taken nor in use.

    Candidates start at `20000 + k × size` and must end by 29999. One that overlaps any block in
    `taken` (blocks of every repository, FR-008) or holds a port that `is_free` rejects (FR-009)
    is skipped. Raises `no_free_block` when none is left (FR-012). Call it inside the registry
    transaction that saves the result, so that two runs cannot choose the same block (FR-013).
    """
    taken_blocks = list(taken)
    start = PORT_RANGE_START
    while start + size - 1 <= PORT_RANGE_END:
        end = start + size - 1
        overlaps = any(
            start <= other.start + other.size - 1 and other.start <= end for other in taken_blocks
        )
        if not overlaps and all(is_free(port) for port in range(start, end + 1)):
            return PortBlock(start=start, size=size)
        start += size
    raise WtenvError(
        ErrorCode.NO_FREE_BLOCK,
        f"no free block of {size} ports in {PORT_RANGE_START}-{PORT_RANGE_END}",
        hint="Run `wtenv gc` to release blocks of removed worktrees, or `wtenv ls` to see what "
        "holds them.",
        details={"block_size": size, "range": f"{PORT_RANGE_START}-{PORT_RANGE_END}"},
    )


def check_block_size(variable_count: int, block_size: int, published: int = 0) -> None:
    """Raise `config_invalid` when a block of `block_size` ports cannot hold the ports needed.

    The ports needed are the variables plus `published`, the published ports not tied to a
    variable. The error states the smallest block size that fits (FR-014, FR-032).
    """
    needed = variable_count + published
    if needed > block_size:
        raise WtenvError(
            ErrorCode.CONFIG_INVALID,
            f"block_size is {block_size}, but {needed} ports are needed",
            hint=f"Set block_size to {needed} or more, or list fewer ports.",
            details={"setting": "block_size", "min_block_size": needed},
        )


def assign_variable_ports(variables: Sequence[str], block: PortBlock) -> list[VariablePort]:
    """Give variable `i` of `variables` the port `block.start + i` (FR-014, FR-017).

    The result depends only on the list and the block, so a repeat `up` assigns the same ports.
    Raises `config_invalid` when the block is too small; no port outside the block is used.
    """
    check_block_size(len(variables), block.size)
    return [
        VariablePort(variable=name, port=block.start + index)
        for index, name in enumerate(variables)
    ]


# --- published ports (data-model.md, Port allocation, steps 2 to 5) ----------------------------


def _is_tied(mapping: "PortMapping", variable_count: int) -> bool:
    """Return whether the host port is the marker of one of the variables (FR-031)."""
    return mapping.published in {str(index + 1) for index in range(variable_count)}


def count_untied_ports(mappings: Sequence["PortMapping"], variable_count: int) -> int:
    """Return how many mappings need a port of their own: those not tied to a variable.

    A mapping without a host port counts. Step 6 of `up` adds this to the number of variables
    to check the block size before a block is searched for (FR-032).
    """
    return sum(1 for mapping in mappings if not _is_tied(mapping, variable_count))


def published_order(mapping: "PortMapping") -> tuple[str, int, str, str]:
    """The order of published ports: service, container port, protocol, host IP.

    Untied ports are numbered in this order, and `assign_published_ports` returns its result in
    it, so sorting the mappings with this key lines them up with the result.
    """
    return (mapping.service, mapping.target, mapping.protocol, mapping.host_ip or "")


def assign_published_ports(
    mappings: Sequence["PortMapping"], variables: Sequence[str], block: PortBlock
) -> list[PublishedPort]:
    """Give every mapping a host port in `block` (FR-031, FR-032).

    A mapping whose host port is the marker `i + 1` is tied to variable `i` and gets that
    variable's port. Every other mapping gets the next port after the variables, in the order
    (service, container port, protocol, host IP). The result is in that order too and depends
    only on the mappings, the variables, and the block. Raises `unsupported`
    (`compose_port_clash`) when two mappings with one protocol and host IP end on one port, and
    `config_invalid` when the block is too small.
    """
    check_block_size(len(variables), block.size, count_untied_ports(mappings, len(variables)))
    next_free = block.start + len(variables)
    result: list[PublishedPort] = []
    taken: dict[tuple[int, str, str | None], str] = {}
    for mapping in sorted(mappings, key=published_order):
        tied = _is_tied(mapping, len(variables))
        if tied:
            index = int(cast(str, mapping.published)) - 1
            port, variable = block.start + index, variables[index]
        else:
            port, variable = next_free, None
            next_free += 1
        key = (port, mapping.protocol, mapping.host_ip)
        if key in taken:
            raise _port_clash(mapping.service, taken[key], port)
        taken[key] = mapping.service
        result.append(
            PublishedPort(
                service=mapping.service,
                target=mapping.target,
                protocol=cast(Literal["tcp", "udp"], mapping.protocol),
                host_ip=mapping.host_ip,
                port=port,
                variable=variable,
            )
        )
    return result


def _port_clash(service: str, other: str, port: int) -> WtenvError:
    return WtenvError(
        ErrorCode.UNSUPPORTED,
        f"services {other} and {service} publish port {port} with the same protocol and host IP",
        hint="Give each service its own variable, or its own host port in the compose file.",
        details={"reason": "compose_port_clash", "service": service, "port": port},
    )
