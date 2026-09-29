"""Argument handling of scripts/update-taxonomy.py. Every case here fails before
Arelle is called, so these are fast."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "update-taxonomy.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture
def package(tmp_path: Path) -> str:
    path = tmp_path / "taxonomy.zip"
    path.write_bytes(b"")
    return str(path)


@pytest.mark.parametrize(
    "mode", [[], ["--regenerate-builtin"], ["--list-entry-points"]]
)
@pytest.mark.parametrize("exists", [True, False])
def test_json_among_packages_points_at_output(
    tmp_path: Path, package: str, mode: list[str], exists: bool
) -> None:
    """The removed positional form, `update-taxonomy.py out.json *.zip`."""
    jsonPath = tmp_path / "out.json"
    if exists:
        jsonPath.write_text("{}", encoding="utf-8")

    result = run(*mode, str(jsonPath), package)

    assert result.returncode == 2, result.stderr
    assert str(jsonPath) in result.stderr
    assert "--output" in result.stderr


def test_no_mode_is_an_error(package: str) -> None:
    result = run(package)

    assert result.returncode == 2
    assert "--output" in result.stderr
    assert "--regenerate-builtin" in result.stderr


def test_modes_are_exclusive(tmp_path: Path, package: str) -> None:
    result = run(
        "--output", str(tmp_path / "out.json"), "--regenerate-builtin", package
    )

    assert result.returncode == 2
    assert "not allowed with" in result.stderr


def test_missing_package_is_named(tmp_path: Path, package: str) -> None:
    missing = str(tmp_path / "missing.zip")

    result = run("--regenerate-builtin", package, missing)

    assert result.returncode == 2
    assert missing in result.stderr
