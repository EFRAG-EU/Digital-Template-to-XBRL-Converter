"""The CLI's --json option, end to end through the script: the report written as xBRL-JSON by our
writer, beside the output, a document the xBRL-JSON reader takes whole and Arelle validates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.offline_arelle import offlineArelle

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.filesupport import FilelikeAndFileName
from mireport.taxonomy import listTaxonomies, loadBuiltInTaxonomyJSON
from mireport.xbrljson_reader import XbrlJsonProcessor
from mireport.xbrljson_reader.document import XbrlJsonDocument

SAMPLE_1_2_0 = Path("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx")
# The only sample with footnotes.
SAMPLE_1_3_0 = Path("digital-templates/VSME-Digital-Template-Sample-1.3.0.xlsx")

pytestmark = pytest.mark.slow


def _convert(sample: Path, out: Path) -> Path:
    """Run the script with --json (validation skipped); the xBRL-JSON it wrote."""
    result = subprocess.run(
        [
            sys.executable,
            "scripts/parse-and-ixbrl.py",
            "--skip-validation",
            "--json",
            str(sample.resolve()),
            str(out / "report.html"),
        ],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return out / "report.json"


def test_the_report_is_written_as_xbrl_json_the_reader_takes(tmp_path: Path) -> None:
    path = _convert(SAMPLE_1_2_0, tmp_path)
    document = XbrlJsonDocument.fromFile(path)
    assert document.facts and not document.invalidFacts
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    processor = XbrlJsonProcessor(
        document, ConversionResultsBuilder(consoleOutput=False), strict=True
    )
    processor.createReport()
    assert processor.factsAdded == len(document.facts)


def test_arelle_finds_nothing_wrong_with_it(tmp_path: Path) -> None:
    """1.3.0 has footnotes, so this covers the link types and note facts too."""
    path = _convert(SAMPLE_1_3_0, tmp_path)
    result = offlineArelle().validateXbrlJson(FilelikeAndFileName.fromPath(path))
    assert not result.has_exceptions, result.log_lines
    problems = [
        m.messageText
        for m in result.messages
        if m.severity in (Severity.ERROR, Severity.WARNING)
    ]
    assert not problems, problems
