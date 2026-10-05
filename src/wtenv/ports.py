"""Port allocation: the free-port test, the block search, and port assignment (data-model.md).

Every port wtenv hands out lies in `20000-29999`, in a block of consecutive ports. The search is
a pure function of the blocks already taken and of which ports are free, so the same registry
and the same free ports always give the same block (Principle III).
"""

import errno
import socket
from collections.abc import Callable, Iterable, Sequence

from wtenv.errors import ErrorCode, WtenvError
from wtenv.registry import PORT_RANGE_END, PORT_RANGE_START, PortBlock, VariablePort

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


def check_block_size(variable_count: int, block_size: int) -> None:
    """Raise `config_invalid` when a block of `block_size` ports cannot hold the variables.

    The error states the smallest block size that fits (FR-014).
    """
    if variable_count > block_size:
        raise WtenvError(
            ErrorCode.CONFIG_INVALID,
            f"block_size is {block_size}, but {variable_count} ports are needed",
            hint=f"Set block_size to {variable_count} or more, or list fewer ports.",
            details={"setting": "block_size", "min_block_size": variable_count},
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
