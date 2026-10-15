"""Argument handling of scripts/update-taxonomy.py. Every case here fails before
Arelle is called, so these are fast."""

import json
import os
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


def test_entry_point_that_is_not_a_uri_is_a_usage_error(
    tmp_path: Path, package: str
) -> None:
    result = run(
        "--output", str(tmp_path / "out.json"), "--entry-point", "vsme-all.xsd", package
    )

    assert result.returncode == 2, result.stderr
    assert "not an absolute URI" in result.stderr
    assert "Traceback" not in result.stderr


def test_status_report_requires_output(tmp_path: Path, package: str) -> None:
    result = run(
        "--list-entry-points", "--status-report", str(tmp_path / "r.md"), package
    )

    assert result.returncode == 2
    assert "--status-report" in result.stderr
    assert "--output" in result.stderr


@pytest.mark.parametrize("name", ["report.txt", "report", "report.docx"])
def test_status_report_needs_a_known_extension(
    tmp_path: Path, package: str, name: str
) -> None:
    result = run(
        "--output",
        str(tmp_path / "out.json"),
        "--entry-point",
        "https://example.com/e.xsd",
        "--status-report",
        str(tmp_path / name),
        package,
    )

    assert result.returncode == 2
    assert "--status-report" in result.stderr
    assert ".md" in result.stderr
    assert ".html" in result.stderr


# --- the UTR mode ------------------------------------------------------------

SAMPLE_UTR = """<?xml version="1.0" encoding="UTF-8"?>
<utr lastUpdated="2025-03-18" version="1.0" xmlns="http://www.xbrl.org/2009/utr">
  <units>
    <unit id="u00001">
      <unitId>acre</unitId>
      <unitName>Acre</unitName>
      <nsUnit>http://www.xbrl.org/2009/utr</nsUnit>
      <itemType>areaItemType</itemType>
      <symbol>a</symbol>
      <definition>Acre</definition>
      <status>REC</status>
    </unit>
    <unit id="u00002">
      <unitId>rood</unitId>
      <unitName>Rood</unitName>
      <nsUnit>http://www.xbrl.org/2009/utr</nsUnit>
      <itemType>areaItemType</itemType>
      <definition>Rood</definition>
      <status>CR</status>
    </unit>
  </units>
</utr>
"""


def runWide(*args: str) -> subprocess.CompletedProcess[str]:
    """Like run(), with a console wide enough that rich doesn't wrap the output."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        env={**os.environ, "COLUMNS": "250"},
    )


@pytest.fixture
def sampleUtr(tmp_path: Path) -> Path:
    path = tmp_path / "sample-utr.xml"
    path.write_text(SAMPLE_UTR, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "other",
    [["--output", "out.json"], ["--regenerate-builtin"], ["--list-entry-points"]],
)
def test_utr_output_is_a_mode_of_its_own(tmp_path: Path, other: list[str]) -> None:
    result = run("--utr-output", str(tmp_path / "utr.json"), *other)

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


def test_utr_source_requires_utr_output(tmp_path: Path, package: str) -> None:
    result = run(
        "--output",
        str(tmp_path / "out.json"),
        "--utr-source",
        str(tmp_path / "utr.xml"),
        package,
    )

    assert result.returncode == 2
    assert "--utr-source" in result.stderr
    assert "--utr-output" in result.stderr


def test_utr_output_takes_no_taxonomy_packages(tmp_path: Path, package: str) -> None:
    result = run("--utr-output", str(tmp_path / "utr.json"), package)

    assert result.returncode == 2
    assert "taxonomy" in result.stderr


@pytest.mark.parametrize("mode", [["--regenerate-builtin"], ["--list-entry-points"]])
def test_the_taxonomy_modes_still_need_packages(mode: list[str]) -> None:
    result = run(*mode)

    assert result.returncode == 2
    assert "taxonomy" in result.stderr


def test_utr_output_from_a_local_source(tmp_path: Path, sampleUtr: Path) -> None:
    out = tmp_path / "utr.json"

    result = runWide("--utr-output", str(out), "--utr-source", str(sampleUtr))

    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["lastUpdated"] == "2025-03-18"
    assert [u["unitId"] for u in data["utr"]] == ["acre"]


@pytest.mark.parametrize(
    "content", [None, "<html/>", "<utr"], ids=["missing", "wrong-root", "malformed"]
)
def test_a_bad_utr_source_fails_and_leaves_the_existing_file_alone(
    tmp_path: Path, content: str | None
) -> None:
    source = tmp_path / "source.xml"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    out = tmp_path / "utr.json"
    out.write_text('{"old": true}', encoding="utf-8")

    result = runWide("--utr-output", str(out), "--utr-source", str(source))

    assert result.returncode == 1
    assert out.read_text(encoding="utf-8") == '{"old": true}'
    assert "source.xml" in result.stdout + result.stderr
    assert "UTR not written" in result.stdout + result.stderr
