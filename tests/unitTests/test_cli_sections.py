"""The CLI prints a conversion's messages under a header for each section."""

from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path
from types import ModuleType

import pytest

from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    MessageType,
    Severity,
    Stage,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "parse-and-ixbrl.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("parse_and_ixbrl_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def results() -> ConversionResults:
    builder = ConversionResultsBuilder()
    with builder.section(Stage.WORKBOOK, source="vsme.xlsx"):
        builder.addMessage("Old template.", Severity.WARNING, MessageType.ExcelParsing)
        builder.addMessage("Read 300 cells.", Severity.INFO, MessageType.DevInfo)
    with builder.section(Stage.TAXONOMY):
        builder.addMessage("Loaded 3.", Severity.INFO, MessageType.DevInfo)
    with builder.section(Stage.PRIOR, source="2025.json"):
        for n in range(2):
            builder.addMessage(
                f"Left out {n}.", Severity.WARNING, MessageType.Conversion
            )
    with builder.section(Stage.WORD):
        builder.addMessage(
            "Could not be read.",
            Severity.ERROR,
            MessageType.Conversion,
            source="policy.docx",
        )
    return builder.build()


def test_each_section_has_a_header_then_its_messages(script, capsys):
    script.outputMessages(Namespace(devinfo=False), results(), [])

    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    headers = [line for line in lines if not line.startswith(("Error", "Warning"))]
    assert headers == [
        "",
        "Information and issues encountered (4 messages):",
        "Digital Template (vsme.xlsx): 1 warning",
        "Last year's report (2025.json): 2 warnings",
        "Word documents: 1 error",
    ]
    assert lines[-1].endswith(": policy.docx: Could not be read.")


def test_developers_see_their_own_messages_and_sections_too(script, capsys):
    script.outputMessages(Namespace(devinfo=True), results(), [])

    out = capsys.readouterr().out
    assert "Digital Template (vsme.xlsx): 1 warning, 1 info" in out
    assert "Taxonomy: 1 info" in out
    assert "Read 300 cells." in out
