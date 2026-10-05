"""`wtenv --version` imports only what it needs (NFR-001; research.md sections 7 and 8)."""

import json
import subprocess
import sys
from pathlib import Path

NOT_LOADED_BY_VERSION = ["pydantic", "filelock", "platformdirs", "psycopg", "click"]


def test_version_loads_none_of_the_heavy_modules() -> None:
    script = (
        "import json, sys\n"
        "import wtenv.cli\n"
        "status = wtenv.cli.main(['--version'])\n"
        f"loaded = [name for name in {NOT_LOADED_BY_VERSION!r} if name in sys.modules]\n"
        "print(json.dumps({'status': status, 'loaded': loaded}))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    version_line, report_line = result.stdout.splitlines()
    assert version_line.startswith("wtenv ")
    assert json.loads(report_line) == {"status": 0, "loaded": []}


def test_importing_the_command_line_loads_none_of_the_heavy_modules() -> None:
    script = (
        "import json, sys\n"
        "import wtenv.cli\n"
        f"print(json.dumps([n for n in {NOT_LOADED_BY_VERSION!r} if n in sys.modules]))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout) == []


def test_ls_json_does_not_load_psycopg_or_the_compose_module(tmp_path: Path) -> None:
    script = (
        "import json, sys\n"
        "import wtenv.cli\n"
        "status = wtenv.cli.main(['ls', '--json'])\n"
        "loaded = [name for name in ['psycopg', 'wtenv.compose'] if name in sys.modules]\n"
        "print(json.dumps({'status': status, 'loaded': loaded}))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )

    document_line, report_line = result.stdout.splitlines()
    assert json.loads(document_line)["command"] == "ls"
    assert json.loads(report_line) == {"status": 0, "loaded": []}
