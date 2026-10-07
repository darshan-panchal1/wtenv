"""What `uv build` puts in the sdist and the wheel (T225)."""

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = [
    ".claude/",
    ".specify/",
    "specs/",
    "tests/",
    ".github/",
    "docs/benchmarks.md",
    "CLAUDE.md",
]
REQUIRED = ["src/wtenv/", "pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md"]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[str], list[str]]:
    """Build a copy of the tracked files; return the sdist's and the wheel's member names."""
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    work = tmp_path_factory.mktemp("build")
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    for name in filter(None, listed):
        source = ROOT / name
        if source.is_file():
            target = work / "project" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    out = work / "dist"
    subprocess.run(
        ["uv", "build", "--out-dir", str(out)],
        cwd=work / "project",
        check=True,
        capture_output=True,
    )
    with tarfile.open(next(out.glob("*.tar.gz"))) as sdist:
        sdist_names = [n.split("/", 1)[1] for n in sdist.getnames() if "/" in n]
    with zipfile.ZipFile(next(out.glob("*.whl"))) as wheel:
        wheel_names = wheel.namelist()
    return sdist_names, wheel_names


def test_the_sdist_has_none_of_the_tooling_or_specs(built: tuple[list[str], list[str]]) -> None:
    sdist, _ = built
    found = [n for n in sdist for bad in EXCLUDED if n == bad.rstrip("/") or n.startswith(bad)]
    assert found == []


def test_the_sdist_has_the_source_and_the_package_files(built: tuple[list[str], list[str]]) -> None:
    sdist, _ = built
    missing = [r for r in REQUIRED if not any(n == r or n.startswith(r) for n in sdist)]
    assert missing == []


def test_the_wheel_has_only_the_package_and_its_metadata(
    built: tuple[list[str], list[str]],
) -> None:
    _, wheel = built
    stray = [n for n in wheel if not (n.startswith("wtenv/") or ".dist-info/" in n)]
    assert stray == []
    assert "wtenv/cli.py" in wheel
