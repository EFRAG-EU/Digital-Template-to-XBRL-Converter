"""Behavioral tests for XlsxProcessor.checkTemplate / checkReport /
checkMigrationStatus.

The webapp migration endpoint consumes the returned TemplateCheckResult, so the
version-comparison outcomes and migration-status probe are load-bearing API.
Version strings are derived from the running converter's version so nothing is
hardcoded.
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.version import OUR_VERSION_HOLDER, VersionHolder
from mireport.xlsx_template_reader.processor import XlsxProcessor

_SHEET = "S"
CONVERTER_VERSION = OUR_VERSION_HOLDER.strip_build_metadata


def _check(named_cells: dict[str, object]):
    """Run checkTemplate over a workbook holding the given named single cells."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = _SHEET
    for row, (name, value) in enumerate(named_cells.items(), start=1):
        ws.cell(row=row, column=1).value = value
        attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate(f'A{row}')}"
        wb.defined_names[name] = DefinedName(name, attr_text=attr)
    results = ConversionResultsBuilder(consoleOutput=False)
    check = XlsxProcessor(wb, results, VSME_DEFAULTS).checkTemplate()
    return check, results


class TestVersionComparison:
    def test_same_version(self):
        check, _ = _check(
            {"template_reporting_template_version": str(CONVERTER_VERSION)}
        )
        assert check.version_is_same is True
        assert check.version_major_minor_same is True
        assert check.reported_version == CONVERTER_VERSION

    def test_same_major_minor_different_patch(self):
        older = VersionHolder(
            CONVERTER_VERSION.major,
            CONVERTER_VERSION.minor,
            CONVERTER_VERSION.patch + 1,
            CONVERTER_VERSION.suffix,
        )
        check, _ = _check({"template_reporting_template_version": str(older)})
        assert check.version_is_same is False
        assert check.version_major_minor_same is True

    def test_different_major_warns(self):
        other = VersionHolder(CONVERTER_VERSION.major + 1, 0, 0, "")
        check, results = _check({"template_reporting_template_version": str(other)})
        assert check.version_is_same is False
        assert check.version_major_minor_same is False
        assert any(
            m.severity is Severity.WARNING and "migrate" in str(m.messageText)
            for m in results.messages
        )

    def test_invalid_version_string(self):
        check, _ = _check({"template_reporting_template_version": "not-a-version"})
        assert check.version_is_same is False
        assert check.version_major_minor_same is False
        assert check.reported_version == VersionHolder(0, 0, 0, "not-a-version")

    def test_missing_version_is_an_error(self):
        check, results = _check({})
        assert check.version_is_same is False
        assert check.version_major_minor_same is False
        assert any(m.severity is Severity.ERROR for m in results.messages)


class TestValidationStatus:
    def test_incomplete_when_status_matches_incomplete_label(self):
        check, _ = _check(
            {
                "template_overall_validation_status": "INCOMPLETE",
                "template_label_incomplete": "INCOMPLETE",
                "template_reporting_template_version": str(CONVERTER_VERSION),
            }
        )
        assert check.validation_is_incomplete is True

    def test_complete_when_status_differs(self):
        check, _ = _check(
            {
                "template_overall_validation_status": "COMPLETE",
                "template_label_incomplete": "INCOMPLETE",
                "template_reporting_template_version": str(CONVERTER_VERSION),
            }
        )
        assert check.validation_is_incomplete is False


class TestMigrationStatus:
    """template_migration_status is a formula cell: None value means the
    workbook was never opened/recalculated after migration."""

    def test_absent_named_range_is_none(self):
        check, _ = _check({})
        assert check.migration_status is None

    def test_populated_cell_is_true(self):
        check, _ = _check({"template_migration_status": "MIGRATED OK"})
        assert check.migration_status is True

    def test_empty_cell_is_false(self):
        check, _ = _check({"template_migration_status": None})
        assert check.migration_status is False


class TestCheckReport:
    def test_garbage_bytes_returns_none(self):
        assert XlsxProcessor.checkReport(BytesIO(b"this is not an xlsx")) is None
