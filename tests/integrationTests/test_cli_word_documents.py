"""The CLI's --extra-data "replacementTextblockValues" key, end to end through the script.

Driven as a subprocess, as test_cli_pdf_attachments.py is.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.unitTests.word_documents import (
    EXTERNAL_CONCEPT,
    SAMPLE_1_3_0,
    externalValuesWorkbook,
    wordDocument,
)

pytestmark = pytest.mark.slow


def runCli(workbook: Path, directory: Path) -> subprocess.CompletedProcess:
    (directory / "policy.docx").write_bytes(
        wordDocument("Supplied in Word.", paragraphStyle="Fancy")
    )
    extra = directory / "extra.json"
    extra.write_text(
        json.dumps(
            {
                "replacementTextblockValues": [
                    {"concept": EXTERNAL_CONCEPT, "path": "policy.docx"}
                ]
            }
        ),
        encoding="utf-8",
    )
    argv = [
        sys.executable,
        "scripts/parse-and-ixbrl.py",
        "--skip-validation",
        str(workbook),
        str(directory / "out"),
        "--extra-data",
        str(extra),
    ]
    return subprocess.run(argv, capture_output=True, encoding="utf-8", check=False)


def reportText(directory: Path) -> str:
    (report,) = (directory / "out").glob("*.html")
    return report.read_text(encoding="utf-8")


def test_word_document_replaces_a_text_block_and_its_warnings_are_shown(
    tmp_path: Path,
) -> None:
    result = runCli(SAMPLE_1_3_0, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "policy.docx" in result.stdout
    assert "Fancy" in result.stdout
    assert "Supplied in Word." in reportText(tmp_path)


def test_word_document_fills_a_text_block_the_template_marks_as_external(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "external.xlsx"
    workbook.write_bytes(externalValuesWorkbook())
    result = runCli(workbook, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Supplied in Word." in reportText(tmp_path)
