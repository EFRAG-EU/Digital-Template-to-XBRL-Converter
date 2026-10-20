"""--prior on both scripts, end to end: the 1.3.0 sample, which has baseline and target years, gets
comparatives from last year's report, which is its own xBRL-JSON a year earlier."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SAMPLE_1_3_0 = Path("digital-templates/VSME-Digital-Template-Sample-1.3.0.xlsx")
COMPARATIVES = "Comparative figures for 2025-01-01 – 2025-12-31"

pytestmark = pytest.mark.slow


def _run(*args: str | Path) -> str:
    result = subprocess.run(
        [sys.executable, *map(str, args)],
        capture_output=True,
        encoding="utf-8",
        check=False,
        # Messages in full, on one line each.
        env=os.environ | {"PYTHONIOENCODING": "utf-8", "COLUMNS": "1000"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _yearEarlier(current: Path, prior: Path) -> None:
    """``current`` with every period a year earlier, as last year's report."""
    document = json.loads(current.read_text(encoding="utf-8"))
    for fact in document["facts"].values():
        if period := fact["dimensions"].get("period"):
            fact["dimensions"]["period"] = re.sub(
                r"\d{4}", lambda year: str(int(year[0]) - 1), period
            )
    prior.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture(scope="module")
def reports(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """This year's xBRL-JSON, as the script writes it, and last year's."""
    out = tmp_path_factory.mktemp("current")
    _run(
        "scripts/parse-and-ixbrl.py",
        "--skip-validation",
        "--json",
        SAMPLE_1_3_0.resolve(),
        out / "report.html",
    )
    _yearEarlier(out / "report.json", out / "prior.json")
    return out / "report.json", out / "prior.json"


def _priorFacts(path: Path) -> int:
    facts = json.loads(path.read_text(encoding="utf-8"))["facts"].values()
    return sum(
        "2025-01-01T00:00:00/2026-01-01T00:00:00" == f["dimensions"].get("period")
        for f in facts
    )


def test_parse_and_ixbrl_takes_comparatives_from_last_years_report(
    reports: tuple[Path, Path], tmp_path: Path
) -> None:
    _, prior = reports
    stdout = _run(
        "scripts/parse-and-ixbrl.py",
        "--skip-validation",
        "--json",
        "--prior",
        prior,
        SAMPLE_1_3_0.resolve(),
        tmp_path / "report.html",
    )
    assert COMPARATIVES in stdout
    assert "2019-01-01 – 2019-12-31" in stdout  # last year's baseline year, left out
    assert _priorFacts(tmp_path / "report.json") > 0


def test_xbrljson_to_ixbrl_takes_comparatives_and_renders_them_all(
    reports: tuple[Path, Path], tmp_path: Path
) -> None:
    current, prior = reports
    stdout = _run(
        "scripts/xbrljson-to-ixbrl.py",
        "--strict",
        "--skip-validation",
        "--prior",
        prior,
        current,
        tmp_path,
    )
    assert COMPARATIVES in stdout
    assert "Error" not in stdout
    assert list(tmp_path.glob("*.html"))


def test_this_years_report_given_as_last_years_too_stops_the_conversion(
    reports: tuple[Path, Path], tmp_path: Path
) -> None:
    current, _ = reports
    stdout = _run(
        "scripts/xbrljson-to-ixbrl.py",
        "--skip-validation",
        "--prior",
        current,
        current,
        tmp_path,
    )
    assert "is it this year's report given twice?" in stdout
    assert not list(tmp_path.glob("*.html"))
