"""Behavioral tests for XlsxProcessor.checkTemplate / checkReport /
checkMigrationStatus.

The webapp migration endpoint consumes the returned TemplateCheckResult, so the
version-comparison outcomes and migration-status probe are load-bearing API.
Version strings are derived from the running converter's version so nothing is
hardcoded.
"""

from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.template_versions import SupportedVersionSource
from mireport.version import OUR_VERSION_HOLDER, VersionHolder
from mireport.xlsx_template_reader.processor import VersionRelation, XlsxProcessor
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

_SHEET = "S"
CONVERTER_VERSION = OUR_VERSION_HOLDER.strip_build_metadata


@pytest.fixture(autouse=True)
def _no_migration_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Judge versions against the converter's own, whatever tool is installed."""
    monkeypatch.setattr(
        "mireport.template_versions.migrationTargetTemplateVersion", lambda: None
    )


def _workbook(named_cells: dict[str, object]) -> Workbook:
    """A workbook holding the given named single cells."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = _SHEET
    for row, (name, value) in enumerate(named_cells.items(), start=1):
        ws.cell(row=row, column=1).value = value
        attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate(f'A{row}')}"
        wb.defined_names[name] = DefinedName(name, attr_text=attr)
    return wb


def _check(named_cells: dict[str, object]):
    """Run checkTemplate over a workbook holding the given named single cells."""
    results = ConversionResultsBuilder(consoleOutput=False)
    check = XlsxProcessor(
        _workbook(named_cells), results, VSME_DEFAULTS
    ).checkTemplate()
    return check, results


class TestVersionComparison:
    def test_same_version(self):
        check, _ = _check(
            {"template_reporting_template_version": str(CONVERTER_VERSION)}
        )
        assert check.report_template_version_relation is VersionRelation.SAME
        assert check.report_template_version == CONVERTER_VERSION
        assert check.report_template_version.is_valid is True
        assert check.supported_template_version == CONVERTER_VERSION
        assert (
            check.supported_template_version_source is SupportedVersionSource.CONVERTER
        )

    def test_older_same_major_minor_is_supported(self):
        # An older version within the same major.minor: still supported, non-error path.
        if CONVERTER_VERSION.patch > 0:
            older = VersionHolder(
                CONVERTER_VERSION.major,
                CONVERTER_VERSION.minor,
                CONVERTER_VERSION.patch - 1,
                CONVERTER_VERSION.suffix,
            )
        else:
            # patch 0: a prerelease of the same core is older than the release (semver §11.3)
            older = VersionHolder(
                CONVERTER_VERSION.major,
                CONVERTER_VERSION.minor,
                CONVERTER_VERSION.patch,
                "-alpha",
            )
        if older >= CONVERTER_VERSION:
            pytest.skip("no strictly-older same-major.minor version to construct")
        check, results = _check({"template_reporting_template_version": str(older)})
        assert check.report_template_version_relation is VersionRelation.PATCH_BEHIND
        assert check.report_template_version.is_valid is True
        assert not any(m.severity is Severity.ERROR for m in results.messages)

    def test_older_different_major_warns_to_migrate(self):
        older = VersionHolder(max(CONVERTER_VERSION.major - 1, 0), 0, 0, "")
        assert older < CONVERTER_VERSION, (
            f"test needs a version below {CONVERTER_VERSION}"
        )
        check, results = _check({"template_reporting_template_version": str(older)})
        assert check.report_template_version_relation is VersionRelation.OLDER
        assert any(
            m.severity is Severity.WARNING and "migrate" in str(m.messageText)
            for m in results.messages
        )

    def test_newer_major_is_an_error(self):
        newer = VersionHolder(CONVERTER_VERSION.major + 1, 0, 0, "")
        check, results = _check({"template_reporting_template_version": str(newer)})
        assert check.report_template_version_relation is VersionRelation.NEWER
        assert any(
            m.severity is Severity.ERROR
            and "newer than the latest version" in str(m.messageText)
            for m in results.messages
        )

    def test_newer_patch_same_major_minor_is_an_error(self):
        newer = VersionHolder(
            CONVERTER_VERSION.major,
            CONVERTER_VERSION.minor,
            CONVERTER_VERSION.patch + 1,
            "",
        )
        check, results = _check({"template_reporting_template_version": str(newer)})
        assert check.report_template_version_relation is VersionRelation.NEWER
        assert any(m.severity is Severity.ERROR for m in results.messages)

    def test_newer_message_says_where_the_supported_version_came_from(self):
        newer = VersionHolder(CONVERTER_VERSION.major + 1, 0, 0, "")
        _, results = _check({"template_reporting_template_version": str(newer)})
        assert any(
            f"{CONVERTER_VERSION} (from this converter)" in str(m.messageText)
            for m in results.messages
        )

    def test_invalid_version_string(self):
        check, results = _check(
            {"template_reporting_template_version": "not-a-version"}
        )
        assert check.report_template_version_relation is VersionRelation.INVALID
        assert check.report_template_version.is_valid is False
        # the invalid sentinel carries the raw string but never == a real version
        assert check.report_template_version == VersionHolder.parse_or_invalid(
            "not-a-version"
        )
        assert str(check.report_template_version) == "not-a-version"
        assert any(m.severity is Severity.ERROR for m in results.messages)

    def test_blank_version_is_an_error_naming_the_cell(self):
        check, results = _check({"template_reporting_template_version": None})
        assert check.report_template_version_relation is VersionRelation.MISSING
        assert [
            m
            for m in results.messages
            if m.severity is Severity.ERROR
            and "no version recorded" in str(m.messageText)
            and "$A$1" in str(m.excelReference)
        ]

    def test_missing_version_range_is_an_error(self):
        check, results = _check({})
        assert check.report_template_version_relation is VersionRelation.MISSING
        assert check.report_template_version.is_valid is False
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


def _saved(named_cells: dict[str, object]) -> bytes:
    saved = BytesIO()
    _workbook(named_cells).save(saved)
    return saved.getvalue()


_CELLS = {
    "template_reporting_template_version": str(CONVERTER_VERSION),
    "template_migration_status": "MIGRATED OK",
}


class TestCheckWorkbook:
    """checkWorkbook checks a workbook already loaded, leaving it to be converted."""

    def test_same_result_as_check_report(self):
        data = _saved(_CELLS)
        wb = loadExcelFromPathOrFileLike(BytesIO(data))
        assert XlsxProcessor.checkWorkbook(wb) == XlsxProcessor.checkReport(
            BytesIO(data)
        )

    def test_leaves_the_workbook_open(self):
        """A read-only workbook reads its cells from the file until it is closed."""
        wb = loadExcelFromPathOrFileLike(BytesIO(_saved(_CELLS)), read_only=True)
        first = XlsxProcessor.checkWorkbook(wb)
        assert first is not None
        assert XlsxProcessor.checkWorkbook(wb) == first
        wb.close()

    def test_not_a_workbook_returns_none(self):
        assert XlsxProcessor.checkWorkbook(object()) is None  # type: ignore[arg-type]

    def test_checked_workbook_still_converts(self):
        wb = loadExcelFromPathOrFileLike(
            Path("digital-templates/VSME-Digital-Template-Sample-1.3.0.xlsx")
        )
        assert XlsxProcessor.checkWorkbook(wb) is not None
        report = XlsxProcessor(
            wb, ConversionResultsBuilder(consoleOutput=False), VSME_DEFAULTS
        ).createReport()
        assert report.hasFacts
