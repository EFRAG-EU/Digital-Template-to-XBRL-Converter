"""Behavioural tests for doMigrationChecks version classification.

A missing or unparseable template version must classify as INVALID_FORMAT
(rather than slipping through to a migration outcome). This is driven by
TemplateCheckResult.version.is_valid, so the whole read -> checkTemplate ->
doMigrationChecks path is exercised here with a real (in-memory) workbook.
"""

from io import BytesIO

import pytest
from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from digital_converter_webapp.migration import MigrationOutcome, doMigrationChecks

_SHEET = "S"


def _workbook_bytes(named_cells: dict[str, object]) -> bytes:
    """Serialise a workbook holding the given named single cells to xlsx bytes."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = _SHEET
    for row, (name, value) in enumerate(named_cells.items(), start=1):
        ws.cell(row=row, column=1).value = value
        attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate(f'A{row}')}"
        wb.defined_names[name] = DefinedName(name, attr_text=attr)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _conversion(named_cells: dict[str, object]) -> dict:
    return {"excel": (_workbook_bytes(named_cells), "report.xlsx")}


class TestDoMigrationChecksVersionClassification:
    @pytest.mark.parametrize(
        "raw_version,expected_display",
        [
            ("not-a-version", "not-a-version"),  # unparseable
            ("", ""),  # missing/blank
        ],
    )
    def test_invalid_version_is_invalid_format(self, raw_version, expected_display):
        conversion = _conversion(
            {"template_reporting_template_version": raw_version}
        )
        outcome, version = doMigrationChecks(conversion)
        assert outcome is MigrationOutcome.INVALID_FORMAT
        # display string is the raw input, not the misleading "0.0.0..."
        assert version == expected_display
