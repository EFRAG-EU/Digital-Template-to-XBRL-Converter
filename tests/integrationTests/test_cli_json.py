"""The CLI's --xbrl-json option, end to end through the script: the report's facts written as
xBRL-JSON straight from the report, a document the xBRL-JSON reader takes whole."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.taxonomy import listTaxonomies, loadBuiltInTaxonomyJSON
from mireport.xbrljson_reader import XbrlJsonProcessor
from mireport.xbrljson_reader.document import XbrlJsonDocument

SAMPLE = Path("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx").resolve()

pytestmark = pytest.mark.slow


def test_the_facts_are_written_as_xbrl_json_the_reader_takes(tmp_path: Path) -> None:
    out = tmp_path / "json" / "facts.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/parse-and-ixbrl.py",
            "--skip-validation",
            "--xbrl-json",
            str(out),
            str(SAMPLE),
            str(tmp_path / "out"),
        ],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    document = XbrlJsonDocument.fromFile(out)
    assert document.facts and not document.invalidFacts
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    processor = XbrlJsonProcessor(
        document, ConversionResultsBuilder(consoleOutput=False), strict=True
    )
    processor.createReport()
    assert processor.factsAdded == len(document.facts)
