"""The free-port test and the block search (data-model.md, Port allocation; FR-007 to FR-013)."""

import errno
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self

import pytest

import wtenv.ports as ports_module
from wtenv.errors import ErrorCode, WtenvError
from wtenv.ports import find_block, port_is_free
from wtenv.registry import PortBlock


def always_free(port: int) -> bool:
    return True


def block(start: int, size: int = 10) -> PortBlock:
    return PortBlock(start=start, size=size)


# --- the free-port test (FR-009) -------------------------------------------------------


@contextmanager
def listening(host: str, family: int = socket.AF_INET) -> Iterator[int]:
    """Listen on `host` at a port the system picks, and yield that port."""
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        listener.listen()
        yield int(listener.getsockname()[1])


def test_a_port_nothing_uses_is_free() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    assert port_is_free(port)


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_a_port_with_a_listener_on_ipv4_is_not_free(host: str) -> None:
    with listening(host) as port:
        assert not port_is_free(port)


def test_a_port_with_a_listener_on_ipv6_loopback_is_not_free() -> None:
    try:
        context = listening("::1", socket.AF_INET6)
        port = context.__enter__()
    except OSError:
        pytest.skip("this machine has no IPv6 loopback")
    try:
        assert not port_is_free(port)
    finally:
        context.__exit__(None, None, None)


class FakeSocket:
    """A stand-in for `socket.socket` that fails `bind` the way a test asks."""

    def __init__(self, failures: dict[tuple[int, str], int]) -> None:
        self.failures = failures
        self.binds: list[tuple[int, str, int]] = []
        self.family = 0

    def factory(self, family: int, kind: int = socket.SOCK_STREAM) -> "FakeSocket":
        sock = FakeSocket(self.failures)
        sock.binds = self.binds
        sock.family = family
        return sock

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def setsockopt(self, *args: object) -> None:
        raise AssertionError("the free-port test must not set SO_REUSEADDR")

    def bind(self, address: tuple[str, int]) -> None:
        host, port = address
        self.binds.append((self.family, host, port))
        code = self.failures.get((self.family, host))
        if code is not None:
            raise OSError(code, "fake failure")


def use_fake_sockets(
    monkeypatch: pytest.MonkeyPatch, failures: dict[tuple[int, str], int]
) -> FakeSocket:
    fake = FakeSocket(failures)
    monkeypatch.setattr(ports_module.socket, "socket", fake.factory)
    return fake


def test_the_test_binds_three_addresses_without_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_fake_sockets(monkeypatch, {})

    assert port_is_free(20123)

    assert fake.binds == [
        (socket.AF_INET, "127.0.0.1", 20123),
        (socket.AF_INET, "0.0.0.0", 20123),
        (socket.AF_INET6, "::1", 20123),
    ]


@pytest.mark.parametrize("code", [errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT])
def test_an_unavailable_address_family_is_skipped(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    use_fake_sockets(monkeypatch, {(socket.AF_INET6, "::1"): code})

    assert port_is_free(20123)


@pytest.mark.parametrize("code", [errno.EADDRINUSE, errno.EACCES])
def test_any_other_bind_failure_means_not_free(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    use_fake_sockets(monkeypatch, {(socket.AF_INET, "0.0.0.0"): code})

    assert not port_is_free(20123)


# --- the block search (FR-007, FR-008, FR-010, FR-012) ----------------------------------


def test_the_first_block_of_an_empty_registry_starts_the_range() -> None:
    assert find_block(10, [], always_free) == block(20000)


def test_a_block_of_another_size_starts_at_the_range_start_too() -> None:
    assert find_block(25, [], always_free) == block(20000, 25)


def test_a_block_in_the_registry_is_skipped() -> None:
    assert find_block(10, [block(20000)], always_free) == block(20010)


def test_the_first_free_gap_is_taken() -> None:
    taken = [block(20000), block(20020)]

    assert find_block(10, taken, always_free) == block(20010)


def test_a_block_of_a_different_size_that_overlaps_a_candidate_is_skipped() -> None:
    # 20000-20019 overlaps the candidates 20000 and 20010 of size 10.
    assert find_block(10, [block(20000, 20)], always_free) == block(20020)


def test_a_candidate_that_shares_only_one_port_with_a_block_is_skipped() -> None:
    # 20004-20005 shares 20004 with the candidate 20000-20004 and 20005 with 20005-20009.
    taken = [block(20004, 2)]

    assert find_block(5, taken, always_free) == block(20010, 5)


def test_a_candidate_with_a_busy_port_is_skipped() -> None:
    def busy_port_20003(port: int) -> bool:
        return port != 20003

    assert find_block(10, [], busy_port_20003) == block(20010)


def test_a_busy_port_in_the_last_place_of_a_candidate_is_found() -> None:
    def busy(port: int) -> bool:
        return port != 20019

    assert find_block(10, [], busy) == block(20000)
    assert find_block(10, [block(20000)], busy) == block(20020)


def test_the_search_takes_the_first_candidate_left() -> None:
    def free_from_20050(port: int) -> bool:
        return port >= 20050

    assert find_block(10, [], free_from_20050) == block(20050)


def test_the_same_inputs_give_the_same_block() -> None:
    taken = [block(20000), block(20030)]

    assert find_block(10, taken, always_free) == find_block(10, taken, always_free)


def test_the_last_candidate_ends_at_29999_at_the_latest() -> None:
    # Size 3: the last whole block is 29996-29998; 29999 cannot start one.
    def free_from_29996(port: int) -> bool:
        return port >= 29996

    assert find_block(3, [], free_from_29996) == block(29996, 3)


def test_a_block_of_size_10_can_end_at_29999() -> None:
    def free_from_29990(port: int) -> bool:
        return port >= 29990

    assert find_block(10, [], free_from_29990) == block(29990)


def test_no_candidate_that_ends_after_29999_is_tried() -> None:
    def free_from_29997(port: int) -> bool:
        return port >= 29997

    with pytest.raises(WtenvError) as caught:
        find_block(3, [], free_from_29997)

    assert caught.value.code is ErrorCode.NO_FREE_BLOCK


def test_no_free_block_reports_the_size_and_the_range() -> None:
    def nothing_free(port: int) -> bool:
        return False

    with pytest.raises(WtenvError) as caught:
        find_block(1000, [], nothing_free)

    assert caught.value.code is ErrorCode.NO_FREE_BLOCK
    assert caught.value.details == {"block_size": 1000, "range": "20000-29999"}
    assert caught.value.hint


def test_a_range_full_of_blocks_is_no_free_block() -> None:
    taken = [block(20000 + k * 1000, 1000) for k in range(10)]

    with pytest.raises(WtenvError) as caught:
        find_block(10, taken, always_free)

    assert caught.value.code is ErrorCode.NO_FREE_BLOCK
    assert caught.value.details["block_size"] == 10


def test_the_range_holds_a_thousand_blocks_of_size_10() -> None:
    taken = [block(20000 + k * 10) for k in range(999)]

    assert find_block(10, taken, always_free) == block(29990)


def test_the_free_port_test_is_asked_only_about_ports_of_the_candidate() -> None:
    asked: list[int] = []

    def probe(port: int) -> bool:
        asked.append(port)
        return True

    find_block(10, [block(20000)], probe)

    assert asked == list(range(20010, 20020))
