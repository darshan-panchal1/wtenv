"""Smoke test for the unit gate: pytest exits 5 when it collects no tests."""

import wtenv


def test_version_is_a_non_empty_string() -> None:
    assert isinstance(wtenv.__version__, str)
    assert wtenv.__version__
