"""wtenv makes no network calls of its own (Principle I, FR-071; T136).

Every module under `src/wtenv/` is parsed with `ast`. None may import a networking module.
`urllib.parse` is allowed: it only splits strings. `socket` is allowed in `ports.py` and
`doctor.py` only, where it is used to `bind` a local port.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "wtenv"

FORBIDDEN = {
    "urllib",
    "urllib3",
    "http",
    "requests",
    "httpx",
    "aiohttp",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "xmlrpc",
    "socketserver",
    "ssl",
}
ALLOWED_SUBMODULES = {"urllib.parse"}
SOCKET_MODULES = {"ports.py", "doctor.py"}


def imported_names(source: str) -> list[str]:
    """Return the dotted module names that `source` imports, e.g. `urllib.parse`."""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # `from urllib import parse` names `urllib.parse`; `from ssl import X`, `ssl.X`.
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def forbidden_imports(source: str, file_name: str) -> list[str]:
    """Return the imports in `source` that the local-only rule does not allow."""
    found: list[str] = []
    for name in imported_names(source):
        top = name.split(".")[0]
        allowed = any(
            name == submodule or name.startswith(submodule + ".")
            for submodule in ALLOWED_SUBMODULES
        )
        bad_socket = top == "socket" and file_name not in SOCKET_MODULES
        if (top in FORBIDDEN and not allowed) or bad_socket:
            found.append(name)
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import requests",
        "import http.client",
        "from urllib.request import urlopen",
        "from urllib import request",
        "import urllib",
        "from ssl import create_default_context",
        "import socket",
    ],
)
def test_the_check_catches_a_network_import(source: str) -> None:
    assert forbidden_imports(source, "other.py")


@pytest.mark.parametrize(
    "source",
    ["from urllib.parse import urlsplit", "from urllib import parse", "import urllib.parse"],
)
def test_the_check_allows_url_parsing(source: str) -> None:
    assert forbidden_imports(source, "other.py") == []


def test_socket_is_allowed_only_where_a_port_is_bound() -> None:
    assert forbidden_imports("import socket", "ports.py") == []
    assert forbidden_imports("import socket", "doctor.py") == []
    assert forbidden_imports("import socket", "database.py") == ["socket"]


def test_no_module_imports_a_networking_library() -> None:
    modules = sorted(SRC.glob("*.py"))
    assert modules, "no modules found under src/wtenv"
    offenders = {
        path.name: forbidden_imports(path.read_text(encoding="utf-8"), path.name)
        for path in modules
    }
    assert {name: found for name, found in offenders.items() if found} == {}
